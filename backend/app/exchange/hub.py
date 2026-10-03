"""
MarketHub — the single gateway every bot talks to.

Responsibilities
  • pick the transport (Binance live/testnet | simulation) and own its life-cycle
  • keep a warm, gap-free candle store per symbol (WS primary, REST reconciler)
  • build and rank the trading universe by volatility (scanner allocation)
  • expose account / position / order operations to the Execution team
  • expose Connector-Bot health (latency, stream state, API weight, errors)
"""
from __future__ import annotations

import asyncio
import contextlib
import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from ..bus import BUS
from ..config import ConfigStore, STORE
from ..ratelimit import GOVERNOR, RateLimitHalt
from ..util import Candle, fnum, now_ms, percentile, seconds_to_next_candle, tf_ms
from .base import AccountSnapshot, ExchangeError, Fill, OrderResult, Position, SymbolFilter, Ticker
from .binance import BinanceFutures
from .sim import SimExchange

STALE_BAR_TOLERANCE = 2          # reconcile when a series is this many bars behind


@dataclass
class SymbolState:
    symbol: str
    candles: list[Candle] = field(default_factory=list)
    forming: dict | None = None
    last_ws_ms: int = 0
    last_rest_ms: int = 0
    vol_score: float = 0.0
    atr_pct: float = 0.0
    quote_volume: float = 0.0
    spread_bps: float = 0.0
    updated_at: int = 0

    def last_closed(self) -> Candle | None:
        return self.candles[-1] if self.candles else None


class MarketHub:
    async def income_totals(self) -> dict | None:
        """Exchange-side cash ledger totals (None when the venue can't report)."""
        ex = self.exchange
        if ex is None or not hasattr(ex, "income_totals"):
            return None
        return await ex.income_totals()

    # ------------------------------------------------------- sim persistence
    SIM_STATE_KEY = "sim_state_v1"

    async def _load_sim_state(self) -> dict | None:
        """Read the stored paper-account snapshot (used when booting the sim)."""
        with contextlib.suppress(Exception):
            from ..db import DB
            return await DB.kv_get(self.SIM_STATE_KEY)
        return None

    async def persist_sim_state(self) -> bool:
        """
        Keep the paper account across restarts so the journal never lies.

        Returns True when the snapshot was written.  Failures are reported on
        the bus rather than swallowed — a silently frozen snapshot is worse
        than no snapshot, because the journal then looks authoritative.
        """
        if self.transport != "sim" or not isinstance(self.exchange, SimExchange):
            return False
        try:
            from ..db import DB
            await DB.kv_set(self.SIM_STATE_KEY, self.exchange.export_state())
            self.sim_persist_failures = 0
            return True
        except Exception as exc:
            self.sim_persist_failures = getattr(self, "sim_persist_failures", 0) + 1
            BUS.publish("sim.persist_failed", {"error": str(exc)[:200],
                                               "failures": self.sim_persist_failures})
            return False

    async def reset_sim_state(self) -> None:
        with contextlib.suppress(Exception):
            from ..db import DB
            await DB.kv_del(self.SIM_STATE_KEY)

    def __init__(self, store: ConfigStore = STORE, exchange=None):
        self.store = store
        self.exchange: BinanceFutures | SimExchange | None = exchange
        self.transport: str = "sim"          # resolved at start(): binance | sim
        self.mode: str = "live"              # live | paper | sim   (order routing)
        self.symbols: dict[str, SymbolState] = {}
        self.tickers: dict[str, Ticker] = {}
        self.filters: dict[str, SymbolFilter] = {}
        self.universe: list[str] = []
        self.started = False
        self.last_error = ""
        self.last_rest_ok_ms = 0
        self.last_ws_ms = 0
        self.latency_ms = 0.0
        self.stream_tasks: list[asyncio.Task] = []
        self._reconcile_task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._universe_lock = asyncio.Lock()
        self.global_liq: dict[str, float] = {}

    # ================================================================ startup
    async def start(self) -> None:
        cfg = self.store.cfg
        self.mode = cfg.binance.mode
        transport = cfg.binance.transport
        self.started = True
        self._stop.clear()

        if transport in ("binance", "auto"):
            key = self.store.api_key()
            secret = self.store.api_secret()
            if key and secret:
                client = BinanceFutures(key, secret, testnet=cfg.binance.testnet,
                                        recv_window=cfg.binance.recv_window_ms)
                try:
                    await client.start()
                    self.latency_ms = await client.ping()
                    self.transport = "binance"
                    self.exchange = client
                    self.last_rest_ok_ms = now_ms()
                    self.last_error = ""
                    BUS.publish("transport.ready", {"transport": "binance",
                                                    "testnet": cfg.binance.testnet,
                                                    "latency_ms": round(self.latency_ms, 1)})
                except Exception as exc:
                    self.last_error = f"Binance unreachable: {str(exc)[:180]}"
                    await client.close()
                    if transport == "binance":
                        BUS.publish("transport.failed", {"error": self.last_error})
                    else:
                        BUS.publish("transport.fallback",
                                    {"error": self.last_error, "to": "sim"})
            else:
                self.last_error = "No Binance API credentials configured"
                BUS.publish("transport.failed", {"error": self.last_error})

        if self.exchange is None:
            # simulation fallback (offline demo / rehearsal) — resume the paper
            # account from the last snapshot so a restart continues the run
            restored = await self._load_sim_state()
            sim = SimExchange(balance=10_000.0,
                              universe=self.store.cfg.engine.universe_size,
                              time_accel=self.store.cfg.engine.sim_time_accel,
                              interval=self.store.cfg.engine.monitored_timeframe,
                              restore=restored)
            self.exchange = sim
            self.transport = "sim"
            if self.mode == "live":
                self.mode = "sim"       # never pretend to trade live without a feed
            BUS.publish("transport.ready", {"transport": "sim", "reason": self.last_error,
                                            "resumed": bool(restored),
                                            "open_positions": len(sim.positions)})

        await self.refresh_filters()
        await self.refresh_tickers()
        await self.build_universe()
        await self.warmup(self.universe)

        # streams
        if isinstance(self.exchange, SimExchange):
            await self.exchange.start_stream(self._on_kline, self._on_kline_close)
        else:
            await self.exchange.stream_klines(
                self.universe[: self.store.cfg.engine.universe_size],
                self.store.cfg.engine.monitored_timeframe,
                self._on_kline, self._on_kline_close)
            self.stream_tasks.append(asyncio.create_task(self.exchange.stream_user(self._on_user_event)))
        self._reconcile_task = asyncio.create_task(self._reconcile_loop())
        BUS.publish("hub.started", {"transport": self.transport, "mode": self.mode,
                                    "symbols": len(self.symbols)})

    async def stop(self) -> None:
        self._stop.set()
        self.started = False
        for t in self.stream_tasks:
            t.cancel()
        self.stream_tasks.clear()
        if self._reconcile_task:
            self._reconcile_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reconcile_task
            self._reconcile_task = None
        if isinstance(self.exchange, SimExchange):
            await self.exchange.stop_stream()
        elif isinstance(self.exchange, BinanceFutures):
            await self.exchange.close()

    # ============================================================ market data
    async def refresh_filters(self) -> None:
        if isinstance(self.exchange, (BinanceFutures, SimExchange)):
            if isinstance(self.exchange, BinanceFutures):
                self.filters = await self.exchange.filters()
            else:
                self.filters = await self.exchange.all_filters()
            if self.filters and "BTCUSDT" not in self.filters:
                BUS.publish("hub.warn", {"message": "filters look malformed"})

    async def refresh_tickers(self) -> None:
        if isinstance(self.exchange, BinanceFutures):
            self.tickers = await self.exchange.tickers()
            with contextlib.suppress(Exception):
                prem = await self.exchange.premium_index()
                for sym, info in prem.items():
                    t = self.tickers.get(sym)
                    if t:
                        t.mark = info["mark"]
                        t.funding_rate = info["funding"]
        elif isinstance(self.exchange, SimExchange):
            self.tickers = await self.exchange.tickers_all()

    async def build_universe(self) -> list[str]:
        """
        Rank USDT perpetuals by a volatility score (ATR% × log quote volume)
        so the 5 scanner bots are split across the *most tradeable* 150 assets.
        """
        async with self._universe_lock:
            cfg = self.store.cfg.engine
            rows: list[tuple[str, float, float, float]] = []
            for sym, tick in self.tickers.items():
                flt = self.filters.get(sym)
                if not flt or flt.status != "TRADING":
                    continue
                if flt.quote != "USDT" or tick.last <= 0:
                    continue
                if tick.quote_volume < 2_000_000:            # ignore dust books
                    continue
                state = self.symbols.setdefault(sym, SymbolState(symbol=sym))
                atr_pct = state.atr_pct
                if not atr_pct:
                    atr_pct = min(6.0, abs(tick.price_change_pct) / 100.0 * 0.35 + 0.4)
                vol = math.log10(max(1.0, tick.quote_volume))
                score = (atr_pct * 2.0) + (vol * 0.6) - (tick.spread_bps * 0.01)
                rows.append((sym, score, atr_pct, tick.quote_volume))
            rows.sort(key=lambda r: r[1], reverse=True)
            keep = rows[: max(20, cfg.universe_size)]
            for sym, score, atr_pct, qv in keep:
                st = self.symbols.setdefault(sym, SymbolState(symbol=sym))
                st.vol_score = score
                st.atr_pct = atr_pct
                st.quote_volume = qv
                st.updated_at = now_ms()
            self.universe = [r[0] for r in keep]
            BUS.publish("hub.universe", {"count": len(self.universe),
                                         "symbols": self.universe[:400]})
            return self.universe

    def scanner_allocation(self, bots: int, per_bot: int) -> list[list[str]]:
        """
        Split the ranked universe across scanner bots *by volatility* — the
        highest-volatility names are dealt round-robin so every bot gets a
        balanced mix of calm and wild assets (fair workload, fair risk).
        """
        buckets: list[list[str]] = [[] for _ in range(max(1, bots))]
        rank = 0
        for sym in self.universe:
            if all(len(b) >= per_bot for b in buckets):
                break
            idx = rank % len(buckets)
            for offset in range(len(buckets)):          # skip full buckets
                j = (idx + offset) % len(buckets)
                if len(buckets[j]) < per_bot:
                    buckets[j].append(sym)
                    break
            rank += 1
        return buckets

    # -------------------------------------------------------------- candles
    async def warmup(self, symbols: Iterable[str], bars: int | None = None) -> None:
        """Fetch pre-history so indicators are warm from the first scan."""
        cfg = self.store.cfg
        bars = bars or cfg.indicator.kline_warmup
        tf = cfg.engine.monitored_timeframe
        syms = [s for s in symbols if self.filters.get(s)]
        if not syms:
            return
        t0 = time.time()
        sem = asyncio.Semaphore(6 if self.transport == "binance" else 32)
        done = 0

        async def fetch(sym: str):
            nonlocal done
            async with sem:
                try:
                    data = await self.exchange.klines(sym, tf, limit=min(1500, bars))
                except Exception as exc:
                    BUS.publish("hub.warn", {"message": f"warmup {sym}: {str(exc)[:120]}"})
                    return
                st = self.symbols.setdefault(sym, SymbolState(symbol=sym))
                st.candles = [c for c in data if c.t + tf_ms(tf) <= now_ms()] or list(data)
                st.last_rest_ms = now_ms()
                done += 1
                if self.transport == "binance" and done % 25 == 0:
                    await asyncio.sleep(0.4)             # be gentle with weight
        await asyncio.gather(*(fetch(s) for s in syms))
        self._refresh_atr_scores()
        BUS.publish("hub.warmup", {"symbols": done, "bars": bars,
                                   "seconds": round(time.time() - t0, 2)})

    def _refresh_atr_scores(self) -> None:
        for st in self.symbols.values():
            if len(st.candles) < 30:
                continue
            recent = st.candles[-60:]
            trs = []
            for i in range(1, len(recent)):
                prev = recent[i - 1].c
                trs.append(max(recent[i].h - recent[i].l,
                               abs(recent[i].h - prev), abs(recent[i].l - prev)))
            atr = sum(trs) / max(1, len(trs))
            close = recent[-1].c or 1.0
            st.atr_pct = atr / close * 100.0
            st.updated_at = now_ms()

    def candles(self, symbol: str, limit: int | None = None) -> list[Candle]:
        st = self.symbols.get(symbol)
        if not st:
            return []
        return st.candles[-limit:] if limit else st.candles

    def last_close(self, symbol: str) -> float:
        st = self.symbols.get(symbol)
        if st and st.forming:
            return fnum(st.forming.get("close"))
        if st and st.candles:
            return st.candles[-1].c
        t = self.tickers.get(symbol)
        return t.last if t else 0.0

    def price(self, symbol: str) -> float:
        if self.exchange:
            p = self.exchange.last_price(symbol)
            if p:
                return p
        return self.last_close(symbol)

    def seconds_to_close(self) -> float:
        return seconds_to_next_candle(now_ms(), self.store.cfg.engine.monitored_timeframe)

    # ---------------------------------------------------------------- events
    def _ingest_ws_candle(self, payload: dict) -> None:
        sym = payload["symbol"]
        st = self.symbols.setdefault(sym, SymbolState(symbol=sym))
        st.forming = payload
        st.last_ws_ms = now_ms()
        self.last_ws_ms = st.last_ws_ms
        t = self.tickers.get(sym)
        if t:
            t.last = payload["close"]
            t.updated_at = now_ms()

    def _commit_closed_candle(self, payload: dict) -> None:
        sym = payload["symbol"]
        st = self.symbols.setdefault(sym, SymbolState(symbol=sym))
        candle = Candle(payload["open_time"], payload["open"], payload["high"],
                        payload["low"], payload["close"], payload["volume"])
        if st.candles and st.candles[-1].t >= candle.t:
            if st.candles[-1].t == candle.t:          # replace (idempotent)
                st.candles[-1] = candle
        else:
            st.candles.append(candle)
        if len(st.candles) > 2000:
            del st.candles[: len(st.candles) - 2000]
        st.updated_at = now_ms()
        self._refresh_atr_scores_one(st)

    def _refresh_atr_scores_one(self, st: SymbolState) -> None:
        if len(st.candles) < 30:
            return
        recent = st.candles[-60:]
        trs = []
        for i in range(1, len(recent)):
            prev = recent[i - 1].c
            trs.append(max(recent[i].h - recent[i].l,
                           abs(recent[i].h - prev), abs(recent[i].l - prev)))
        atr = sum(trs) / max(1, len(trs))
        close = recent[-1].c or 1.0
        st.atr_pct = atr / close * 100.0

    def _on_user_event(self, msg: dict) -> None:
        """Binance user-data stream → instant fill / position notifications."""
        etype = msg.get("e")
        if etype == "ORDER_TRADE_UPDATE":
            o = msg.get("o", {})
            BUS.publish("user.order", {
                "symbol": o.get("s"), "side": o.get("S"), "status": o.get("X"),
                "type": o.get("o"), "qty": fnum(o.get("q")),
                "avg_price": fnum(o.get("ap")), "last_price": fnum(o.get("L")),
                "realized": fnum(o.get("rp")), "commission": fnum(o.get("n")),
                "order_id": str(o.get("i")), "client_id": o.get("c"),
                "reduce_only": bool(o.get("R")), "ts": msg.get("E"),
            })
        elif etype == "ACCOUNT_UPDATE":
            BUS.publish("user.account", {"ts": msg.get("E"),
                                         "reason": msg.get("a", {}).get("m")})
        elif etype == "MARGIN_CALL":
            BUS.publish("user.margin_call", {"ts": msg.get("E")})

    # ------------------------------------------------------------ reconciler
    async def _reconcile_loop(self) -> None:
        """Keep candle series gap-free and refresh tickers/health in the background."""
        tf = self.store.cfg.engine.monitored_timeframe
        step = tf_ms(tf)
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=45.0)
                return
            except asyncio.TimeoutError:
                pass
            try:
                now = now_ms()
                stale = [s for s in self.universe
                         if not self.symbols.get(s)
                         or not self.symbols[s].candles
                         or self.symbols[s].candles[-1].t < now - step * STALE_BAR_TOLERANCE]
                if stale:
                    sem = asyncio.Semaphore(5)

                    async def fix(sym: str):
                        async with sem:
                            try:
                                data = await self.exchange.klines(sym, tf, limit=5)
                            except Exception as exc:
                                BUS.publish("hub.warn", {"message": f"reconcile {sym}: {str(exc)[:120]}"})
                                return
                            st = self.symbols.setdefault(sym, SymbolState(symbol=sym))
                            for c in data:
                                if c.t + step <= now_ms():
                                    if not st.candles or c.t > st.candles[-1].t:
                                        st.candles.append(c)
                                    elif c.t == st.candles[-1].t:
                                        st.candles[-1] = c
                            st.last_rest_ms = now_ms()
                    await asyncio.gather(*(fix(s) for s in stale[:200]))
                    BUS.publish("hub.reconciled", {"symbols": len(stale[:200])})
                if self.transport == "binance":
                    await self.refresh_tickers()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                BUS.publish("hub.warn", {"message": f"reconcile error: {str(exc)[:160]}"})

    # ------------------------------------------------------------- callback
    async def _on_kline(self, payload: dict) -> None:
        try:
            self._ingest_ws_candle(payload)
            BUS.publish("market.tick", {"symbol": payload["symbol"],
                                        "price": payload["close"],
                                        "ts": payload.get("close_time")})
        except Exception as exc:
            BUS.publish("hub.warn", {"message": f"tick error {str(exc)[:120]}"})

    async def _on_kline_close(self, payload: dict) -> None:
        try:
            self._commit_closed_candle(payload)
            BUS.publish("market.bar_closed", {"symbol": payload["symbol"],
                                              "open_time": payload["open_time"],
                                              "close": payload["close"],
                                              "high": payload["high"],
                                              "low": payload["low"],
                                              "volume": payload["volume"]})
        except Exception as exc:
            BUS.publish("hub.warn", {"message": f"bar close error {str(exc)[:120]}"})

    # ---------------------------------------------------------------- broker
    async def account(self) -> AccountSnapshot:
        return await self.exchange.account()

    async def positions(self) -> list[Position]:
        if isinstance(self.exchange, BinanceFutures):
            return await self.exchange.positions()
        return await self.exchange.positions_all()

    async def set_leverage(self, symbol: str, leverage: int) -> None:
        await self.exchange.set_leverage(symbol, leverage)

    async def set_margin_type(self, symbol: str, margin_type: str) -> None:
        await self.exchange.set_margin_type(symbol, margin_type)

    async def market_order(self, symbol: str, side: str, qty: float,
                           reduce_only: bool = False, client_id: str = "") -> OrderResult:
        if isinstance(self.exchange, BinanceFutures) and self.mode != "live":
            raise ExchangeError("order routing blocked: engine is not in LIVE mode")
        return await self.exchange.market_order(symbol, side, qty, reduce_only, client_id)

    async def stop_market(self, symbol: str, side: str, stop_price: float,
                          close_position: bool = True, qty: float | None = None,
                          client_id: str = "") -> OrderResult:
        if isinstance(self.exchange, BinanceFutures) and self.mode != "live":
            raise ExchangeError("order routing blocked: engine is not in LIVE mode")
        return await self.exchange.stop_market(symbol, side, stop_price, close_position,
                                               qty, client_id=client_id)

    async def take_profit_market(self, symbol: str, side: str, stop_price: float,
                                 close_position: bool = True, qty: float | None = None,
                                 client_id: str = "") -> OrderResult:
        if isinstance(self.exchange, BinanceFutures) and self.mode != "live":
            raise ExchangeError("order routing blocked: engine is not in LIVE mode")
        return await self.exchange.take_profit_market(symbol, side, stop_price,
                                                      close_position, qty, client_id=client_id)

    async def cancel_all(self, symbol: str) -> None:
        await self.exchange.cancel_all(symbol)

    async def open_orders(self, symbol: str | None = None) -> list[dict]:
        return await self.exchange.open_orders(symbol)

    async def fills(self, symbols: Iterable[str] | None = None,
                    since_ms: int | None = None) -> list[Fill]:
        """Fills for the symbols we actually hold / care about (weight-aware)."""
        if isinstance(self.exchange, SimExchange):
            want_sim = {s for s in (symbols or []) if s}
            return [f for f in self.exchange.fills
                    if (not since_ms or f.ts >= since_ms)
                    and (not want_sim or f.symbol in want_sim)]
        want = {s for s in (symbols or []) if s}
        if not want:
            want = {p.symbol for p in await self.positions()}
        out: list[Fill] = []
        for sym in sorted(want):
            with contextlib.suppress(Exception):
                out.extend(await self.exchange.user_trades(sym, start_ms=since_ms, limit=200))
        return out

    async def funding_income(self, since_ms: int | None = None) -> list[Fill]:
        if isinstance(self.exchange, SimExchange):
            return []
        out: list[Fill] = []
        with contextlib.suppress(Exception):
            out = await self.exchange.income(start_ms=since_ms, income_type="FUNDING_FEE")
        return out

    # ---------------------------------------------------------------- health
    async def health(self, deep: bool = False) -> dict[str, Any]:
        """
        Connector-Bot probe.  Performs a cheap REST ping (1 weight) plus state
        checks.  Returns a full diagnosis used for the SOS state machine.
        """
        t0 = time.perf_counter()
        problems: list[str] = []
        connected = False
        latency = self.latency_ms
        server_time_skew_ms = 0
        try:
            if isinstance(self.exchange, BinanceFutures):
                latency = await self.exchange.ping()
                self.last_rest_ok_ms = now_ms()
                if deep:
                    server_ms = await self.exchange.server_time()
                    server_time_skew_ms = server_ms - now_ms()
                    if abs(server_time_skew_ms) > 1000:
                        problems.append(f"clock skew {server_time_skew_ms}ms — signatures may fail")
                connected = True
            else:
                await asyncio.sleep(0)
                connected = bool(self.symbols)
                latency = 3.0
        except RateLimitHalt as exc:                     # transparent reporting
            problems.append(str(exc))
        except Exception as exc:
            self.last_error = str(exc)[:200]
            problems.append(f"REST ping failed: {str(exc)[:160]}")

        ws_age = (now_ms() - self.last_ws_ms) / 1000.0 if self.last_ws_ms else None
        if self.transport == "binance" and (ws_age is None or ws_age > 300):
            problems.append(f"market stream silent for {int(ws_age or 0)}s")
        if self.transport == "binance":
            key_ok = bool(self.store.api_key() and self.store.api_secret())
            if not key_ok:
                problems.append("API credentials missing")
        weight = GOVERNOR.snapshot()
        if weight["used_pct"] >= self.store.cfg.engine.api_budget_pct:
            problems.append(f"API weight at {weight['used_pct']:.1f}% — requests halted")
        if not self.universe:
            problems.append("no tradeable universe")

        return {
            "connected": connected and not problems,
            "transport": self.transport,
            "mode": self.mode,
            "testnet": self.store.cfg.binance.testnet,
            "latency_ms": round(latency, 1),
            "probe_ms": round((time.perf_counter() - t0) * 1000, 1),
            "server_time_skew_ms": server_time_skew_ms,
            "ws_age_s": round(ws_age, 1) if ws_age is not None else None,
            "universe": len(self.universe),
            "symbols": len(self.symbols),
            "problems": problems,
            "last_error": self.last_error,
            "api_weight": weight,
            "checked_at": now_ms(),
        }

