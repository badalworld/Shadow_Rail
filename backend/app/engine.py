"""
TradingEngine — the CEO workflow, implemented exactly as specified.

    Connector Bot ──green──▶ Engine CEO ──order──▶ 5× Scanner Bot (30 assets each,
                                                        split by volatility)
         scanners ──opportunities + CEO confirmation──▶ 10× Market Analyst
         analysts ──high-confidence proposals──▶ Execution team (2 bots)
         execution ──order + TP/SL (inside liquidation)──▶ Info Bot verification
         Info Bot ──verified──▶ CEO ──order──▶ 4× Trade Monitor (live watch)
         close ⟶ Trade Manager (journal, P&L, win-rate, fees, funding)
              ⟶ Equity Manager (8% margin/ trade, 10× cross, 10 concurrent)

Everything is event-driven and concurrent; every step is logged, streamed to
the dashboard and attributed to the bot that performed it.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import math
import time
from dataclasses import dataclass, field
from typing import Any

from .bots import REGISTRY, BotRegistry, build_registry, workflow_links
from .bus import BUS
from .confidence import MODEL
from .config import ConfigStore, STORE
from .db import DB
from .exchange.base import ExchangeError, Position
from .exchange.hub import MarketHub
from .indicators import ghost
from .journal import JOURNAL, Journal
from .ratelimit import GOVERNOR, RateLimitHalt
from .risk import RiskEngine, estimate_liquidation
from .util import Candle, fnum, now_iso, now_ms, percentile, seconds_to_next_candle, tf_ms

SCAN_TIMEOUT_S = 120
MAX_OPPORTUNITIES_PER_CYCLE = 40
SYMBOL_COOLDOWN_S = 300


@dataclass
class Opportunity:
    symbol: str
    direction: str
    entry: float
    stop: float
    target: float
    atr: float
    atr_pct: float
    trend_quality: float
    tier: str
    rail: float | None
    rail_distance_pct: float
    htf_bull: bool
    flow_bias: float
    volume: float
    bar_time: int
    scanner_id: str
    features: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {**self.__dict__}

    def to_signal(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol, "direction": self.direction, "entry": self.entry,
            "stop": self.stop, "target": self.target, "atr": self.atr,
            "atr_pct": self.atr_pct, "trend_quality": self.trend_quality,
            "tier": self.tier, "rail": self.rail,
            "rail_distance_pct": self.rail_distance_pct, "htf_bull": self.htf_bull,
            "flow_bias": self.flow_bias, "bar_time": self.bar_time,
            "scanner_id": self.scanner_id,
        }


@dataclass
class Proposal:
    opportunity: Opportunity
    analyst_id: str
    confidence: float
    approved: bool
    factors: dict[str, float]
    notes: list[str]
    model: str
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.opportunity.symbol,
            "direction": self.opportunity.direction,
            "confidence": round(self.confidence, 2),
            "approved": self.approved,
            "analyst_id": self.analyst_id,
            "factors": self.factors,
            "notes": self.notes,
            "model": self.model,
            "reason": self.reason,
            "entry": self.opportunity.entry,
            "stop": self.opportunity.stop,
            "target": self.opportunity.target,
            "atr": self.opportunity.atr,
            "atr_pct": self.opportunity.atr_pct,
            "trend_quality": self.opportunity.trend_quality,
            "tier": self.opportunity.tier,
            "bar_time": self.opportunity.bar_time,
            "feature_row": self.opportunity.features,
        }


class TradingEngine:
    def __init__(self, store: ConfigStore = STORE, hub: MarketHub | None = None,
                 journal: Journal = JOURNAL, registry: BotRegistry | None = None):
        self.store = store
        self.hub = hub or MarketHub(store)
        self.journal = journal
        self.registry = registry or build_registry(store.cfg)
        self.risk = RiskEngine(store.cfg.risk)

        self.running = False
        self.tasks: list[asyncio.Task] = []
        self._log_queue: asyncio.Queue = asyncio.Queue(maxsize=5000)
        self._log_task: asyncio.Task | None = None

        self.open_trades: dict[int, dict] = {}
        self.symbol_to_trade: dict[str, int] = {}
        self.cooldowns: dict[str, float] = {}
        self.series_cache: dict[str, tuple[int, Any]] = {}
        self.scanner_buckets: list[list[str]] = []
        self.symbol_monitor: dict[str, str] = {}
        self.cycle = 0
        self.signals_seen = 0
        self.last_scan_at = 0
        self.started_at = 0

        self.sos: dict[str, Any] = {"active": False, "level": "none", "reasons": [],
                                    "since": 0, "checks": 0, "last_ok": 0}
        self.health: dict[str, Any] = {"connected": False, "problems": ["not started"]}
        self.workflow: dict[str, Any] = {"cycle": 0, "stage": "idle", "stages": {},
                                         "active_links": [], "updated_at": now_ms()}
        self.paused = False
        self.emergency_stop = False
        self._equity_task_ts = 0
        self.last_equity: dict[str, Any] = {}
        self.recent_analyst_rows: list[dict] = []
        self.pending_close: set[str] = set()
        self.consumed_fills: set[str] = set()
        self._open_lock = asyncio.Lock()
        self.scan_snapshot: dict[str, Any] = {"by_bot": {}, "updated_at": 0, "cycle": 0}

    # ================================================================ startup
    async def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.started_at = now_ms()
        self._log_task = asyncio.create_task(self._log_writer())
        await self.hub.start()
        self.scanner_buckets = self.hub.scanner_allocation(
            self.store.cfg.engine.scanner_bots, self.store.cfg.engine.assets_per_bot)
        self._assign_scanner_symbols()
        await self._load_open_trades()
        self.log("info", "ceo-bot", f"Engine starting — transport={self.hub.transport} "
                                   f"mode={self.hub.mode} universe={len(self.hub.universe)}")
        self.tasks = [
            asyncio.create_task(self._connector_loop()),
            asyncio.create_task(self._equity_loop()),
            asyncio.create_task(self._scan_loop()),
            asyncio.create_task(self._monitor_loop()),
            asyncio.create_task(self._maintenance_loop()),
        ]
        self.registry.publish_all(GOVERNOR.snapshot())

    async def stop(self) -> None:
        self.running = False
        for t in self.tasks:
            t.cancel()
        for t in self.tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        self.tasks.clear()
        await self.hub.stop()
        if self._log_task:
            self._log_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._log_task
            self._log_task = None
        self.log("warn", "ceo-bot", "Engine stopped")

    # ================================================================== log
    def log(self, level: str, bot_id: str, message: str, payload: dict | None = None,
            topic: str | None = None) -> None:
        rec = {"ts": now_ms(), "level": level, "bot_id": bot_id, "message": message,
               "payload": payload, "topic": topic or bot_id}
        try:
            self._log_queue.put_nowait(rec)
        except asyncio.QueueFull:
            pass
        BUS.publish("log.append", rec)

    async def _log_writer(self) -> None:
        while True:
            try:
                batch: list[dict] = []
                rec = await self._log_queue.get()
                batch.append(rec)
                while len(batch) < 40:
                    try:
                        batch.append(self._log_queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break
                for r in batch:
                    with contextlib.suppress(Exception):
                        await DB.add_log(r["level"], r["bot_id"], r["topic"],
                                         r["message"], r["payload"])
                if self._log_queue.qsize() == 0:
                    await asyncio.sleep(0.35)
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(0.5)

    # ============================================================== workflow
    def _wf(self, stage: str, status: str, detail: str = "", **extra: Any) -> None:
        stages = self.workflow.setdefault("stages", {})
        stages[stage] = {"status": status, "detail": detail, "at": now_ms(), **extra}
        self.workflow["stage"] = stage
        self.workflow["updated_at"] = now_ms()
        BUS.publish("workflow.update", self.workflow)
        if status in ("start",):
            self._pulse_links(stage)

    def _pulse_links(self, stage: str) -> None:
        mapping = {
            "connector": [("connector-bot", "ceo-bot")],
            "scan": [("ceo-bot", f"scanner-{i+1}") for i in range(self.store.cfg.engine.scanner_bots)],
            "analyze": [("scanner-team", "analyst-team")],
            "execute": [("analyst-team", "execution-team"), ("risk-bot", "execution-team")],
            "verify": [("execution-team", "info-verifier-bot")],
            "monitor": [("ceo-bot", "monitor-team")],
            "close": [("monitor-team", "trade-manager-bot"),
                      ("trade-manager-bot", "equity-manager-bot")],
        }
        for src, dst in mapping.get(stage, []):
            BUS.publish("link.pulse", {"from": src, "to": dst, "stage": stage,
                                       "ts": now_ms()})

    def _assign_scanner_symbols(self) -> None:
        for i, bucket in enumerate(self.scanner_buckets):
            bot = self.registry.get(f"scanner-{i+1}")
            if bot:
                bot.assigned = list(bucket)
        self.log("info", "ceo-bot",
                 f"Scanner allocation ready — {len(self.scanner_buckets)} bots × "
                 f"{len(self.scanner_buckets[0]) if self.scanner_buckets else 0} assets "
                 f"(ranked by volatility)")

    # ============================================================== loops
    async def _connector_loop(self) -> None:
        """Connector Bot: verify the link now, then every 5 minutes."""
        first = True
        while self.running:
            if not first:
                wait = float(self.store.cfg.engine.connector_health_interval_s)
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(asyncio.sleep(wait), timeout=wait + 1)
            first = False
            if not self.running:
                return
            self.registry.set_status("connector-bot", "working", "probing Binance link",
                                     progress=0.4)
            self._wf("connector", "start", "Connector Bot probing the exchange link")
            try:
                health = await asyncio.wait_for(self.hub.health(deep=True), timeout=25)
            except Exception as exc:
                health = {"connected": False, "problems": [str(exc)[:200]],
                          "transport": self.hub.transport, "checked_at": now_ms()}
            self.health = health
            self.sos["checks"] += 1
            problems = health.get("problems") or []
            critical = (not health.get("connected")) and (
                self.hub.transport == "binance" or not self.store.cfg.engine.simulate_when_offline)
            # even in sim mode, a Binance failure in LIVE mode is an SOS for the operator
            if self.hub.transport != "binance" and self.store.cfg.binance.mode == "live" \
                    and self.hub.last_error:
                problems = list(problems) + [self.hub.last_error]
                critical = True

            was_active = self.sos["active"]
            if critical:
                self._raise_sos("critical", problems or ["connection lost"])
                self.registry.set_status("connector-bot", "error", "SOS RAISED",
                                         message="; ".join(problems)[:200])
            elif problems:
                self._raise_sos("warning", problems)
                self.registry.set_status("connector-bot", "success", "link degraded",
                                         message="; ".join(problems)[:200])
            else:
                self._clear_sos()
                self.registry.set_status(
                    "connector-bot", "success", "link verified",
                    message=f"{health.get('transport')} OK · {health.get('latency_ms', 0):.0f} ms · "
                            f"{health.get('universe', 0)} symbols")
                if not was_active:
                    self._wf("connector", "done", "green signal sent to Engine CEO")
                    self.log("success", "connector-bot",
                             f"Connection confirmed ({health.get('transport')}, "
                             f"{health.get('latency_ms', 0):.0f}ms) → green signal to CEO",
                             {"health": {k: v for k, v in health.items() if k != "api_weight"}})
                    self.registry.set_status("ceo-bot", "working", "awaiting scan cycle",
                                             message="green signal received from ORACLE")
            BUS.publish("connector.health", health)
            self._wf("connector", "done" if not critical else "error",
                     "; ".join(problems)[:180] if problems else "healthy")

    def _raise_sos(self, level: str, reasons: list[str]) -> None:
        if level == "warning" and self.sos["active"] and self.sos["level"] == "critical":
            return
        if not self.sos["active"] or self.sos["level"] != level or \
                set(reasons) != set(self.sos["reasons"]):
            self.sos.update({"active": True, "level": level, "reasons": reasons[:6],
                             "since": self.sos["since"] or now_ms()})
            BUS.publish("sos.on", {**self.sos, "at": now_ms()})
            self.log("sos", "connector-bot",
                     f"☠️ SOS {level.upper()} — " + "; ".join(reasons)[:220],
                     {"reasons": reasons})
            for bot in self.registry.all():
                if bot.bot_id not in ("connector-bot",):
                    self.registry.set_mood(bot.bot_id, "sad", 40)
            # Live transport failure = stop trading.  Simulation fallback keeps the
            # workflow visible (labelled SIMULATION) so the operator can watch it.
            simulate = (self.hub.transport == "sim"
                        and self.store.cfg.engine.simulate_when_offline)
            self.paused = not simulate
            self.registry.publish_all(GOVERNOR.snapshot())

    def _clear_sos(self) -> None:
        if self.sos["active"]:
            self.sos.update({"active": False, "level": "none", "reasons": [], "since": 0})
            BUS.publish("sos.off", {"at": now_ms()})
            self.log("success", "connector-bot", "SOS cleared — connection healthy again")
            self.paused = False
            self.registry.publish_all(GOVERNOR.snapshot())

    async def _equity_loop(self) -> None:
        """Equity Manager: keep balance, equity, margin budget live."""
        while self.running:
            try:
                await asyncio.sleep(5)
                if not self.running:
                    return
                account = await self.hub.account()
                state = await self.journal.update(account)
                stats = await self.journal.refresh_stats()
                payload = {**state.as_dict(), "stats": stats,
                           "mode": self.hub.mode, "transport": self.hub.transport,
                           "open_slots": max(0, self.store.cfg.risk.max_concurrent_trades
                                             - state.open_positions),
                           "margin_budget": round(self.journal.margin_budget(), 2)}
                self.last_equity = payload
                BUS.publish("equity.update", payload)
                self.registry.set_status(
                    "equity-manager-bot", "working", "balance sync",
                    message=f"equity ${state.equity:,.2f} · "
                            f"{state.open_positions}/{self.store.cfg.risk.max_concurrent_trades} slots · "
                            f"budget ${self.journal.margin_budget():,.2f}/trade", publish=False)
                self.registry.set_status(
                    "trade-manager-bot", "working", "journal sync",
                    message=f"{stats.get('total_trades', 0)} closed · "
                            f"WR {stats.get('win_rate', 0):.1f}% · net ${stats.get('net_pnl', 0):,.2f}",
                    publish=False)
            except asyncio.CancelledError:
                raise
            except RateLimitHalt as exc:
                self._note_blocked("equity-manager-bot", str(exc))
            except Exception as exc:
                with contextlib.suppress(Exception):
                    self.log("warn", "equity-manager-bot", f"equity sync failed: {str(exc)[:180]}")

    async def _scan_loop(self) -> None:
        """Aligned to the 5-minute candle close (+2s), one workflow cycle each bar."""
        while self.running:
            try:
                wait = self.hub.seconds_to_close() + 2.0
                if self.hub.transport == "sim":
                    wait = min(wait, self.hub.exchange.tick_seconds + 1.5)
                await asyncio.sleep(max(1.0, wait))
                if not self.running:
                    return
                await self.run_cycle()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                with contextlib.suppress(Exception):
                    self.log("error", "ceo-bot", f"cycle failed: {str(exc)[:200]}")
                await asyncio.sleep(5)

    async def run_cycle(self, force: bool = False) -> dict[str, Any]:
        """One full pass of the CEO workflow."""
        if self.paused and not force:
            self._wf("idle", "skipped", "paused — waiting for connector green")
            return {"skipped": "paused"}
        if not self.hub.universe:
            await self.hub.build_universe()
        marker = self._current_bar_marker()
        if marker and marker == getattr(self, "_last_cycle_bar", None) and not force:
            return {"skipped": "same bar"}
        self._last_cycle_bar = marker
        self.cycle += 1
        self.workflow["cycle"] = self.cycle
        self.last_scan_at = now_ms()
        t0 = time.time()
        self.registry.set_status("ceo-bot", "working", f"cycle #{self.cycle}",
                                 message="orchestrating the swarm", progress=0.05)

        # ── 1. scanners (5 bots in parallel) ──────────────────────────────
        self._wf("scan", "start", "5 scanner bots sweeping the volatility-ranked universe")
        opportunities = await self._stage_scan()
        self._wf("scan", "done", f"{len(opportunities)} opportunities",
                 count=len(opportunities))

        # ── 1b. instant reverse-signal exits (indicator flip closes position) ──
        await self._handle_reverse_exits(opportunities)

        if not opportunities:
            self.registry.set_status("ceo-bot", "success", f"cycle #{self.cycle}",
                                     message="no confirmed flips this bar")
            self._wf("idle", "done", f"cycle {self.cycle} complete — no signals")
            return {"opportunities": 0, "opened": 0, "elapsed": round(time.time() - t0, 2)}

        # ── 2. analysts (up to 10 in parallel) ────────────────────────────
        self._wf("analyze", "start", f"analyst team scoring {len(opportunities)} opportunities")
        proposals = await self._stage_analyze(opportunities)
        approved = [p for p in proposals if p.approved]
        self._wf("analyze", "done",
                 f"{len(approved)}/{len(proposals)} approved above confidence threshold",
                 approved=len(approved), total=len(proposals))

        # ── 3. execution ──────────────────────────────────────────────────
        opened: list[dict] = []
        if approved:
            self._wf("execute", "start", f"executing {len(approved)} approved setups")
            opened = await self._stage_execute(approved)
            self._wf("execute", "done", f"{len(opened)} positions opened", count=len(opened))

        # ── 4. verification ───────────────────────────────────────────────
        if opened:
            self._wf("verify", "start", "Info Bot verifying fills and protection")
            verified = await self._stage_verify(opened)
            self._wf("verify", "done" if verified else "error",
                     f"{len(verified)} positions verified on the exchange",
                     count=len(verified))

        # ── 5. monitoring handover ────────────────────────────────────────
        if self.symbol_to_trade:
            self._wf("monitor", "start", "Trade Monitor team watching live positions")
            await self._assign_monitors()

        self.registry.set_status("ceo-bot", "success", f"cycle #{self.cycle}",
                                 message=f"{len(opened)} opened / {len(approved)} approved / "
                                         f"{len(opportunities)} scanned")
        self.log("info", "ceo-bot",
                 f"Cycle #{self.cycle} complete in {time.time() - t0:.2f}s — "
                 f"{len(opportunities)} opportunities, {len(approved)} approved, "
                 f"{len(opened)} executed")
        return {"opportunities": len(opportunities), "approved": len(approved),
                "opened": len(opened), "elapsed": round(time.time() - t0, 2)}

    def _current_bar_marker(self) -> int | None:
        for sym in self.hub.universe[:1] or list(self.hub.symbols.keys())[:1]:
            candles = self.hub.candles(sym, 1)
            if candles:
                return candles[-1].t
        return None

    # ============================================================ stage 1: scan
    async def _compute_series(self, symbol: str, force: bool = False):
        """Ghost/Shadow-Rail series for a symbol, cached by last closed bar."""
        candles = self.hub.candles(symbol)
        if len(candles) < 220:
            return None
        last_t = candles[-1].t
        cached = self.series_cache.get(symbol)
        if cached and cached[0] == last_t and not force:
            return cached[1]
        params = ghost.GhostParams(**{
            k: getattr(self.store.cfg.indicator, k)
            for k in ("swingBars", "railSpread", "railDrive", "ghostBlur", "ghostOffset",
                      "ghostPlacement", "ghostEase", "mtfGate", "mtfFrame", "mtfEmaBars",
                      "minTrendPct", "require_strong_flip")
        }, slAtrX=self.risk.s.active_tp_sl()[0], tpAtrX=max(0.1, self.risk.s.active_tp_sl()[1] or 3.0))
        series = await asyncio.to_thread(ghost.compute, candles, params,
                                        self.store.cfg.engine.monitored_timeframe)
        self.series_cache[symbol] = (last_t, series)
        return series

    async def _stage_scan(self) -> list[Opportunity]:
        buckets = self.scanner_buckets or [self.hub.universe]
        results: list[Opportunity] = []
        lock = asyncio.Lock()

        async def scan_bot(index: int, symbols: list[str]) -> None:
            bot_id = f"scanner-{index+1}"
            bot = self.registry.get(bot_id)
            t_bot = time.perf_counter()
            self.registry.set_status(bot_id, "working", f"sweeping {len(symbols)} assets",
                                     progress=0.0)
            found = 0
            rows: list[dict] = []
            for i, sym in enumerate(symbols):
                try:
                    series = await self._compute_series(sym)
                except Exception as exc:
                    self.registry.note_task(bot_id, ok=False, error=True)
                    self.log("warn", bot_id, f"scan error {sym}: {str(exc)[:140]}")
                    continue
                if series is None:
                    continue
                rows.append(self._scan_row(sym, series))
                sig = ghost.latest_signal(series)
                if not sig:
                    continue
                found += 1
                opp = await self._to_opportunity(sym, sig, series, bot_id)
                if opp:
                    async with lock:
                        results.append(opp)
                self.registry.set_status(
                    bot_id, "working", f"sweeping {len(symbols)} assets",
                    message=f"{sym} flip → {sig['direction']} (quality {sig['trend_quality']:.2f})",
                    progress=(i + 1) / max(1, len(symbols)), publish=False)
            latency = (time.perf_counter() - t_bot) * 1000.0
            async with lock:
                self.scan_snapshot["by_bot"][bot_id] = rows
                self.scan_snapshot["updated_at"] = now_ms()
                self.scan_snapshot["cycle"] = self.cycle
                self.registry.note_task(bot_id, ok=True, latency_ms=latency)
            self.registry.set_status(bot_id, "success", f"sweep complete",
                                     message=f"{found} flip(s) · {len(symbols)} assets · "
                                             f"{latency/1000:.1f}s")
            self.registry.publish(bot_id, GOVERNOR.snapshot())
            self.log("info", bot_id,
                     f"Sweep done: {found} flip(s) across {len(symbols)} assets "
                     f"({latency/1000:.1f}s) → reporting to CEO", topic="scan")
            if bot:
                bot.mood = "happy" if found else bot.mood

        await asyncio.gather(*(scan_bot(i, bucket) for i, bucket in enumerate(buckets)),
                             return_exceptions=True)
        results.sort(key=lambda o: (o.tier == "strong", o.trend_quality), reverse=True)
        return results[:MAX_OPPORTUNITIES_PER_CYCLE]

    def _warn_htf_once(self, symbol: str) -> None:
        if getattr(self, "_htf_warned", False):
            return
        self._htf_warned = True
        self.log("warn", "analyst-1",
                 f"HTF gate not warm yet (needs >= {int(self.store.cfg.indicator.mtfEmaBars)+1} "
                 f"closed {self.store.cfg.indicator.mtfFrame} bars) — symbols are skipped "
                 f"until history is deep enough (increase kline_warmup if this persists)")

    def _scan_row(self, symbol: str, series) -> dict[str, Any]:
        """Compact per-symbol market state for the scanner page."""
        i = series.last
        trend = int(series.trend[i])
        cr = series.clean_ratio[i]
        rail = series.rail_value(i)
        price = float(series.close[i])
        atr = series.atr14[i]
        return {
            "symbol": symbol,
            "price": price,
            "trend": "LONG" if trend == 1 else ("SHORT" if trend == -1 else "—"),
            "trend_side": trend,
            "quality": round(float(cr), 3) if not math.isnan(cr) else None,
            "rail": round(float(rail), 8) if not math.isnan(rail) else None,
            "rail_distance_pct": round((price - rail) / price * 100.0, 3)
            if rail and not math.isnan(rail) else None,
            "atr_pct": round(float(atr) / price * 100.0, 3) if not math.isnan(atr) and price else None,
            "htf_bull": bool(series.htf_bull[i] == 1.0),
            "ghost_close": round(float(series.ghost_close[i]), 8)
            if not math.isnan(series.ghost_close[i]) else None,
            "flip": ("LONG" if series.turn_up[i] else ("SHORT" if series.turn_dn[i] else None)),
            "tier": "strong" if (series.strong_up[i] or series.strong_dn[i]) else None,
            "has_position": symbol in self.symbol_to_trade,
            "bar_time": int(series.times[i]),
        }

    async def _to_opportunity(self, symbol: str, sig: dict, series, scanner_id: str
                              ) -> Opportunity | None:
        bar = self.hub.candles(symbol, 2)
        if not bar:
            return None
        if sig["bar_time"] != bar[-1].t:
            return None                                  # stale (not the latest bar)
        if symbol in self.symbol_to_trade:
            return None                                  # one position per symbol
        if self.store.cfg.indicator.mtfGate and not sig.get("htf_ready", True):
            self._warn_htf_once(symbol)
            return None                                  # gate not warm yet → skip
        if self.cooldowns.get(symbol, 0) > time.time():
            return None
        i = series.last
        flow_smooth = 0.0
        with contextlib.suppress(Exception):
            flow_smooth = float(ghost.P.nz(series.volume[i], 0.0))
        return Opportunity(
            symbol=symbol, direction=sig["direction"], entry=sig["entry"],
            stop=sig["stop"], target=sig["target"], atr=sig["atr"],
            atr_pct=sig["atr_pct"], trend_quality=sig["trend_quality"], tier=sig["tier"],
            rail=sig.get("rail"), rail_distance_pct=sig.get("rail_distance_pct", 0.0),
            htf_bull=bool(sig.get("htf_bull")), flow_bias=float(sig.get("flow_bias", 0.0)),
            volume=sig.get("volume", 0.0), bar_time=sig["bar_time"], scanner_id=scanner_id,
            features={
                "trend_quality": sig["trend_quality"], "tier": sig["tier"],
                "htf_bull": bool(sig.get("htf_bull")), "atr_pct": sig["atr_pct"],
                "rail_distance_pct": sig.get("rail_distance_pct", 0.0),
                "flow_bias": float(sig.get("flow_bias", 0.0)),
                "symbol": symbol, "direction": sig["direction"],
            })

    # =========================================================== stage 2: analysts
    async def _stage_analyze(self, opportunities: list[Opportunity]) -> list[Proposal]:
        analysts = self.registry.by_group("analyst")
        if not analysts:
            return []
        sym_stats = await self.journal.symbol_stats()
        queue: asyncio.Queue = asyncio.Queue()
        for i, opp in enumerate(opportunities):
            queue.put_nowait((i, opp))
        proposals: list[Proposal] = []
        lock = asyncio.Lock()

        async def analyst_worker(bot) -> None:
            while True:
                try:
                    idx, opp = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                t0 = time.perf_counter()
                self.registry.set_status(bot.bot_id, "working",
                                         f"analysing {opp.symbol} {opp.direction}",
                                         progress=0.4)
                # small deterministic per-analyst variance (independent opinions)
                variance = ((hash(bot.bot_id + opp.symbol) % 7) - 3) * 0.9
                res = MODEL.score(opp.features, sym_stats.get(opp.symbol),
                                  analyst_variance=variance)
                threshold = self.store.cfg.risk.min_confidence
                approved = res.score >= threshold
                reason = "" if approved else \
                    f"confidence {res.score:.1f} < threshold {threshold:.1f}"
                prop = Proposal(opportunity=opp, analyst_id=bot.bot_id,
                                confidence=res.score, approved=approved,
                                factors=res.factors, notes=res.notes, model=res.model,
                                reason=reason)
                latency = (time.perf_counter() - t0) * 1000.0
                async with lock:
                    proposals.append(prop)
                    self.recent_analyst_rows.insert(0, prop.as_dict())
                    del self.recent_analyst_rows[60:]
                    self.registry.note_task(bot.bot_id, ok=True, latency_ms=latency)
                self.registry.set_status(
                    bot.bot_id, "success" if approved else "idle",
                    f"{opp.symbol} {'APPROVED' if approved else 'rejected'}",
                    message=f"confidence {res.score:.1f} · {opp.direction} · "
                            f"quality {opp.trend_quality:.2f}",
                    progress=1.0)
                self.registry.publish(bot.bot_id, GOVERNOR.snapshot())
                BUS.publish("analysis.result", prop.as_dict())
                self.log("success" if approved else "info", bot.bot_id,
                         f"{opp.symbol} {opp.direction} — confidence {res.score:.1f} "
                         f"({'approved for execution' if approved else 'rejected'})",
                         prop.as_dict(), topic="analysis")
                bot.mood = "excited" if approved and res.score >= 80 else bot.mood

        await asyncio.gather(*(analyst_worker(b) for b in analysts), return_exceptions=True)
        proposals.sort(key=lambda p: p.confidence, reverse=True)
        if proposals:
            self.log("info", "ceo-bot",
                     f"Analyst team reported {len([p for p in proposals if p.approved])} "
                     f"approved setups of {len(proposals)}", topic="analysis")
        return proposals

    # ========================================================== stage 3: execute
    async def _stage_execute(self, proposals: list[Proposal]) -> list[dict]:
        execs = self.registry.by_group("execution") or [self.registry.get("execution-1")]
        execs = [b for b in execs if b]
        if not execs:
            return []
        cfg = self.store.cfg
        self.risk = RiskEngine(cfg.risk)
        account = await self.hub.account()
        state = await self.journal.update(account)
        stats = await self.journal.refresh_stats()
        open_count = len(self.open_trades)

        # hard guards before spending any API weight
        if self.journal.daily_drawdown_breached():
            self.log("warn", "risk-bot",
                     f"Daily drawdown guard tripped ({state.daily_pnl:.2f}) — no new trades")
            self.registry.set_status("risk-bot", "error", "daily drawdown guard",
                                     message="new entries blocked for today")
            return []
        if open_count >= cfg.risk.max_concurrent_trades:
            self.log("info", "risk-bot",
                     f"Max concurrent trades reached ({open_count}/{cfg.risk.max_concurrent_trades})")
            return []

        opened: list[dict] = []
        queue: asyncio.Queue = asyncio.Queue()
        for p in proposals:
            queue.put_nowait(p)
        lock = asyncio.Lock()

        async def executor(bot) -> None:
            while True:
                async with lock:
                    if len(self.open_trades) >= cfg.risk.max_concurrent_trades:
                        return
                try:
                    proposal = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                opp = proposal.opportunity
                if opp.symbol in self.symbol_to_trade:
                    continue
                t0 = time.perf_counter()
                self.registry.set_status(bot.bot_id, "working",
                                         f"executing {opp.symbol} {opp.direction}",
                                         progress=0.3)
                trade = await self._open_trade(bot.bot_id, proposal, {
                    "equity": state.equity, "available": state.available,
                    "open_count": len(self.open_trades)})
                latency = (time.perf_counter() - t0) * 1000.0
                async with lock:
                    self.registry.note_task(bot.bot_id, ok=bool(trade), latency_ms=latency)
                    if trade:
                        opened.append(trade)
                self.registry.publish(bot.bot_id, GOVERNOR.snapshot())

        await asyncio.gather(*(executor(b) for b in execs), return_exceptions=True)
        if opened:
            self.log("success", "ceo-bot",
                     f"Execution team opened {len(opened)} position(s): "
                     + ", ".join(t["symbol"] for t in opened), topic="execution")
        return opened

    async def _open_trade(self, exec_bot_id: str, proposal: Proposal,
                          ctx: dict[str, Any]) -> dict | None:
        """
        Open one position.  The whole step runs under `self._open_lock` so the
        concurrency cap, the margin budget and the API ceiling are re-checked
        against *live* state immediately before the order is sent.
        """
        async with self._open_lock:
            return await self._open_trade_locked(exec_bot_id, proposal, ctx)

    async def _open_trade_locked(self, exec_bot_id: str, proposal: Proposal,
                                 ctx: dict[str, Any]) -> dict | None:
        cfg = self.store.cfg
        opp = proposal.opportunity
        symbol = opp.symbol
        side = "LONG" if opp.direction == "LONG" else "SHORT"
        flt = self.hub.filters.get(symbol)
        if not flt:
            self.log("warn", exec_bot_id, f"{symbol}: no exchange filters — skipped")
            return None

        # ── hard caps re-verified against live state ─────────────────────
        if len(self.open_trades) >= cfg.risk.max_concurrent_trades:
            self.log("info", "risk-bot",
                     f"{symbol}: skipped — {len(self.open_trades)}/"
                     f"{cfg.risk.max_concurrent_trades} slots already in use")
            return None
        if symbol in self.symbol_to_trade:
            return None
        api = GOVERNOR.snapshot()
        if api["halted"]:
            self.log("warn", "api-guard-bot",
                     f"{symbol}: entry blocked — API weight at {api['used_pct']:.1f}% "
                     f"(ceiling {api['budget_pct']:.0f}%). Close/protect paths stay allowed.")
            self.registry.set_status(exec_bot_id, "blocked", "API ceiling",
                                     message=f"{api['used_pct']:.1f}% of the IP budget used")
            return None

        # fresh equity so the 8% sizing reflects the current balance
        with contextlib.suppress(Exception):
            account = await self.hub.account()
            state = await self.journal.update(account, persist=False)
            ctx = {**ctx, "equity": state.equity, "available": state.available,
                   "open_count": len(self.open_trades)}
        price = self.hub.price(symbol) or opp.entry
        plan = self.risk.plan(symbol=symbol, side=side, price=price, atr=opp.atr,
                              equity=ctx["equity"], available=ctx["available"], flt=flt,
                              open_trades=len(self.open_trades),
                              confidence=proposal.confidence)
        if not plan.ok:
            self.log("info", exec_bot_id,
                     f"{symbol}: sizing rejected — {plan.reason}", {"plan": plan.as_dict()},
                     topic="execution")
            self.registry.set_status(exec_bot_id, "idle", f"{symbol} rejected",
                                     message=plan.reason[:140])
            return None

        # ── risk manager sign-off ────────────────────────────────────────
        self.registry.set_status("risk-bot", "working", f"validating {symbol}",
                                 message=f"stop {plan.sl_price:.6g} vs liq {plan.liquidation_price:.6g}")
        if not (plan.liquidation_price < plan.sl_price < price if side == "LONG"
                else price < plan.sl_price < plan.liquidation_price):
            self.log("error", "risk-bot",
                     f"BLOCKED {symbol}: stop {plan.sl_price} not inside liquidation "
                     f"{plan.liquidation_price}")
            return None
        if plan.stop_clamped:
            self.log("warn", "risk-bot",
                     f"{symbol}: ATR stop tightened to stay inside liquidation "
                     f"(stop {plan.sl_price:.6g} · liq {plan.liquidation_price:.6g})")

        trade_row: dict[str, Any] | None = None
        try:
            await self.hub.set_leverage(symbol, cfg.risk.leverage)
            await self.hub.set_margin_type(symbol, cfg.risk.margin_type)
        except RateLimitHalt as exc:
            self._note_blocked(exec_bot_id, str(exc))
            return None
        except ExchangeError as exc:
            self.log("warn", exec_bot_id,
                     f"{symbol}: leverage/margin setup warning — {str(exc)[:140]}")

        try:
            order = await self.hub.market_order(
                symbol, "BUY" if side == "LONG" else "SELL", plan.qty,
                client_id=f"SR{int(time.time())}{symbol[:6]}")
        except RateLimitHalt as exc:
            self._note_blocked(exec_bot_id, str(exc))
            return None
        except ExchangeError as exc:
            self.registry.note_task(exec_bot_id, ok=False, error=True)
            self.log("error", exec_bot_id, f"{symbol}: entry order failed — {str(exc)[:160]}")
            return None

        fill_price = order.avg_price or price
        entry = fill_price if fill_price > 0 else price

        # stop/target recomputed from the *actual* fill so risk maths stay true
        sl_mult, tp_mult, tp_on = cfg.risk.active_tp_sl()
        sl_price = plan.sl_price
        tp_price = plan.tp_price if plan.tp_enabled else 0.0
        drift = abs(entry - price) / price if price else 0.0
        if drift > 0.0002:
            recompute = self.risk.plan(symbol=symbol, side=side, price=entry, atr=opp.atr,
                                       equity=ctx["equity"], available=ctx["available"],
                                       flt=flt, open_trades=ctx["open_count"])
            if recompute.ok:
                sl_price, tp_price = recompute.sl_price, recompute.tp_price
                plan.sl_price, plan.tp_price = sl_price, tp_price

        # ── protection: ONE system only, never double ────────────────────
        protect_ok = True
        sl_order_id = ""
        tp_order_id = ""
        try:
            sl_order = await self.hub.stop_market(
                symbol, "SELL" if side == "LONG" else "BUY", sl_price,
                close_position=True, client_id=f"SRsl{int(time.time())}")
            sl_order_id = sl_order.order_id
        except RateLimitHalt as exc:
            self._note_blocked(exec_bot_id, str(exc))
            protect_ok = False
        except Exception as exc:            # any failure ⇒ flatten immediately below
            self.log("error", exec_bot_id,
                     f"{symbol}: STOP order failed ({str(exc)[:140]}) — closing immediately",
                     topic="execution")
            protect_ok = False

        if protect_ok and tp_on and tp_price > 0:
            try:
                tp_order = await self.hub.take_profit_market(
                    symbol, "SELL" if side == "LONG" else "BUY", tp_price,
                    close_position=True, client_id=f"SRtp{int(time.time())}")
                tp_order_id = tp_order.order_id
            except RateLimitHalt as exc:
                self._note_blocked(exec_bot_id, str(exc))
            except Exception as exc:        # TP is optional; the reverse signal covers us
                self.log("warn", exec_bot_id,
                         f"{symbol}: TP order failed — position runs to the reverse "
                         f"signal / stop ({str(exc)[:140]})")

        if not protect_ok:
            # fail-safe: never hold an unprotected position
            with contextlib.suppress(Exception):
                await self.hub.market_order(symbol, "SELL" if side == "LONG" else "BUY",
                                            plan.qty, reduce_only=True)
            self.log("sos", "risk-bot",
                     f"{symbol}: protection failed — position closed immediately (fail-safe)")
            return None

        trade_row = {
            "symbol": symbol, "side": side, "status": "open", "qty": plan.qty,
            "entry_price": entry, "leverage": cfg.risk.leverage, "margin": plan.margin,
            "notional": plan.notional, "sl_price": sl_price, "tp_price": tp_price,
            "liquidation_price": plan.liquidation_price,
            "sl_atr_mult": sl_mult if cfg.risk.risk_mode != "indicator_default" else 1.5,
            "tp_atr_mult": (tp_mult if tp_on else 0.0),
            "risk_mode": cfg.risk.risk_mode,
            "opened_at": now_ms(), "close_reason": None,
            "gross_pnl": 0.0, "fee_paid": 0.0, "funding_paid": 0.0, "net_pnl": 0.0,
            "signal_confidence": proposal.confidence, "signal_tier": opp.tier,
            "analyst_id": proposal.analyst_id, "scanner_id": opp.scanner_id,
            "exec_bot_id": exec_bot_id, "monitor_bot_id": "",
            "entry_order_id": order.order_id, "exit_order_id": "",
            "mode": self.hub.mode,
            "notes": json.dumps({
                "features": opp.features, "factors": proposal.factors,
                "notes": proposal.notes, "model": proposal.model,
                "stop_clamped": plan.stop_clamped, "warnings": plan.warnings,
                "sl_order_id": sl_order_id, "tp_order_id": tp_order_id,
                "planned_sl_atr": sl_mult, "planned_tp_atr": tp_mult if tp_on else 0.0,
                "atr": opp.atr, "bar_time": opp.bar_time,
                "liquidation_estimate": plan.liquidation_price,
            }),
        }
        trade_id = await DB.insert_trade(trade_row)
        trade_row["id"] = trade_id
        await DB.add_trade_event(trade_id, "opened",
                                 {"entry": entry, "qty": plan.qty, "sl": sl_price,
                                  "tp": tp_price, "leverage": cfg.risk.leverage})
        self.open_trades[trade_id] = trade_row
        self.symbol_to_trade[symbol] = trade_id
        self._assign_monitor_for(symbol)

        # ── bookkeeping for the bots that made it happen ──────────────────
        for bot in self.registry.all():
            if bot.bot_id in (opp.scanner_id, proposal.analyst_id, exec_bot_id):
                bot.mood = "excited"
        self.registry.set_status(exec_bot_id, "success", f"{symbol} opened",
                                 message=f"{side} {plan.qty:.6g} @ {entry:.6g} · "
                                         f"SL {sl_price:.6g} · "
                                         f"{'TP ' + format(tp_price, '.6g') if tp_price else 'no fixed TP'}",
                                 progress=1.0)
        self.registry.set_status("risk-bot", "success", f"{symbol} cleared",
                                 message=f"stop inside liquidation · risk ${plan.risk_amount:,.2f} "
                                         f"({plan.risk_pct_equity:.2f}% equity)")
        self.log("success", exec_bot_id,
                 f"{symbol} {side} filled {plan.qty:.6g} @ {entry:.6g} (lev {cfg.risk.leverage}x "
                 f"{cfg.risk.margin_type}) · SL {sl_price:.6g} · "
                 f"TP {format(tp_price, '.6g') if tp_price else 'reverse-signal exit'} · "
                 f"liq {plan.liquidation_price:.6g}",
                 {"trade": {k: v for k, v in trade_row.items() if k != "notes"}},
                 topic="execution")
        BUS.publish("trade.opened", {"trade": {k: v for k, v in trade_row.items()
                                               if k != "notes"},
                                     "proposal": proposal.as_dict()})
        return trade_row

    # =========================================================== stage 4: verify
    async def _stage_verify(self, trades: list[dict]) -> list[dict]:
        bot_id = "info-verifier-bot"
        self.registry.set_status(bot_id, "working", f"verifying {len(trades)} position(s)",
                                 progress=0.2)
        positions = {p.symbol: p for p in await self.hub.positions()}
        verified: list[dict] = []
        for trade in trades:
            sym = trade["symbol"]
            pos = positions.get(sym)
            problems: list[str] = []
            if not pos:
                problems.append("position not visible on the exchange")
            else:
                if abs(pos.qty - trade["qty"]) / max(trade["qty"], 1e-9) > 0.02:
                    problems.append(f"qty mismatch {pos.qty} vs {trade['qty']}")
                if (trade["side"] == "LONG" and pos.side != "LONG") or \
                        (trade["side"] == "SHORT" and pos.side != "SHORT"):
                    problems.append(f"side mismatch {pos.side} vs {trade['side']}")
                if pos.liquidation_price > 0:
                    # LONG:  liq < stop < entry     SHORT: entry < stop < liq
                    if trade["side"] == "LONG" and trade["sl_price"] <= pos.liquidation_price:
                        problems.append(f"stop {trade['sl_price']:.6g} at/through liquidation "
                                        f"{pos.liquidation_price:.6g}")
                    if trade["side"] == "SHORT" and trade["sl_price"] >= pos.liquidation_price:
                        problems.append(f"stop {trade['sl_price']:.6g} at/through liquidation "
                                        f"{pos.liquidation_price:.6g}")
            orders = []
            with contextlib.suppress(Exception):
                orders = await self.hub.open_orders(sym)
            has_stop = any(str(o.get("type")) in ("STOP_MARKET", "STOP") for o in orders)
            has_tp = any(str(o.get("type")) in ("TAKE_PROFIT_MARKET", "TAKE_PROFIT") for o in orders)
            if not has_stop:
                problems.append("no exchange-side stop order found")
            if trade.get("tp_price") and not has_tp:
                problems.append("no take-profit order found")

            if problems:
                self.log("error", bot_id, f"{sym}: verification FAILED — {'; '.join(problems)}",
                         {"trade_id": trade["id"], "problems": problems}, topic="verify")
                await DB.add_trade_event(int(trade["id"]), "verify_failed", problems)
                self.registry.set_status(bot_id, "error", f"{sym} verify failed",
                                         message="; ".join(problems)[:160])
                self.registry.note_task(bot_id, ok=False, error=True)
                # a missing stop on a live position is an emergency
                if any("stop" in p for p in problems):
                    await self._close_trade(int(trade["id"]), "emergency",
                                            reason_detail="; ".join(problems))
            else:
                verified.append(trade)
                await DB.add_trade_event(int(trade["id"]), "verified",
                                         {"orders": len(orders)})
                self.log("success", bot_id,
                         f"{sym}: execution verified — position, quantity, stop and target all "
                         f"match the order plan", {"trade_id": trade["id"]}, topic="verify")
        self.registry.note_task(bot_id, ok=True)
        self.registry.set_status(bot_id, "success", f"{len(verified)}/{len(trades)} verified",
                                 message="report sent to Engine CEO", progress=1.0)
        self.registry.publish(bot_id, GOVERNOR.snapshot())
        return verified

    # ========================================================== stage 5: monitor
    def _assign_monitor_for(self, symbol: str) -> str:
        monitors = self.registry.by_group("monitor")
        if not monitors:
            return ""
        idx = len(self.symbol_monitor) % len(monitors)
        bot = monitors[idx]
        self.symbol_monitor[symbol] = bot.bot_id
        trade_id = self.symbol_to_trade.get(symbol)
        if trade_id and trade_id in self.open_trades:
            self.open_trades[trade_id]["monitor_bot_id"] = bot.bot_id
            with contextlib.suppress(Exception):
                asyncio.create_task(DB.update_trade(trade_id, {"monitor_bot_id": bot.bot_id}))
        bot.assigned = [s for s, b in self.symbol_monitor.items() if b == bot.bot_id]
        return bot.bot_id

    async def _assign_monitors(self) -> None:
        monitors = self.registry.by_group("monitor")
        for i, symbol in enumerate(list(self.symbol_to_trade.keys())):
            if symbol not in self.symbol_monitor:
                self._assign_monitor_for(symbol)
        for bot in monitors:
            watched = [s for s, b in self.symbol_monitor.items() if b == bot.bot_id]
            if watched:
                self.registry.set_status(bot.bot_id, "working", f"watching {len(watched)} position(s)",
                                         message=", ".join(watched[:4]), publish=False)
            else:
                self.registry.set_status(bot.bot_id, "idle", "standing by", publish=False)

    async def _monitor_loop(self) -> None:
        """Trade Monitor team + close detection (5s cadence)."""
        tick = max(2, int(self.store.cfg.engine.monitor_tick_s))
        while self.running:
            try:
                await asyncio.sleep(tick)
                if not self.running:
                    return
                if not self.open_trades:
                    await self._assign_monitors()
                    continue
                positions = {p.symbol: p for p in await self.hub.positions()}
                for trade_id, trade in list(self.open_trades.items()):
                    sym = trade["symbol"]
                    pos = positions.get(sym)
                    if pos is None and sym not in self.pending_close:
                        await self._finalize_close(trade_id, fallback_price=self.hub.price(sym))
                    elif pos is not None:
                        await self._monitor_tick(trade, pos)
                self._pulse_links("monitor")
            except asyncio.CancelledError:
                raise
            except RateLimitHalt as exc:
                self._note_blocked("monitor-team", str(exc))
            except Exception as exc:
                with contextlib.suppress(Exception):
                    self.log("warn", "monitor-1", f"monitor cycle error: {str(exc)[:160]}")

    async def _monitor_tick(self, trade: dict, pos: Position) -> None:
        bot_id = trade.get("monitor_bot_id") or self.symbol_monitor.get(trade["symbol"], "monitor-1")
        entry = float(trade["entry_price"])
        mark = pos.mark_price or self.hub.price(trade["symbol"])
        side = trade["side"]
        pnl = (mark - entry) * float(trade["qty"]) * (1 if side == "LONG" else -1)
        tp = float(trade.get("tp_price") or 0)
        sl = float(trade.get("sl_price") or 0)
        liq = pos.liquidation_price or float(trade.get("liquidation_price") or 0)
        to_tp = ((tp - mark) / mark * 100.0 * (1 if side == "LONG" else -1)) if tp else 0.0
        to_sl = ((mark - sl) / mark * 100.0 * (1 if side == "LONG" else -1))
        warn = ""
        if liq > 0:
            dist_liq = abs(mark - liq) / mark * 100.0
            if dist_liq < 2.0:
                warn = f"⚠ {dist_liq:.2f}% from liquidation"
        state = {
            "trade_id": trade["id"], "symbol": trade["symbol"], "side": side,
            "qty": float(trade["qty"]), "entry": entry, "mark": mark,
            "unrealized": round(pnl, 4), "tp": tp, "sl": sl, "liq": liq,
            "to_tp_pct": round(to_tp, 3), "to_sl_pct": round(to_sl, 3),
            "leverage": trade["leverage"], "monitor_id": bot_id,
            "opened_at": trade["opened_at"], "confidence": trade.get("signal_confidence"),
            "warning": warn,
        }
        BUS.publish("trade.tick", state)
        self.registry.set_status(bot_id, "working", f"watching {trade['symbol']}",
                                 message=f"{side} uPnL ${pnl:,.2f} · {to_tp:+.2f}% to TP · "
                                         f"{to_sl:+.2f}% to SL {warn}",
                                 progress=min(1.0, max(0.0, 0.5 + to_tp / 4)), publish=False)
        if warn:
            self.registry.set_mood(bot_id, "worried", 15)
        self.registry.publish(bot_id, GOVERNOR.snapshot())

    async def _handle_reverse_exits(self, opportunities: list[Opportunity]) -> None:
        """Indicator flip in the opposite direction closes the position instantly."""
        if not self.open_trades:
            return
        flips = {o.symbol: o.direction for o in opportunities}
        # also catch flips of symbols that were filtered out of the final list
        for symbol, trade_id in list(self.symbol_to_trade.items()):
            if symbol in flips:
                continue
            series = await self._compute_series(symbol)
            if series is None:
                continue
            sig = ghost.latest_signal(series)
            if sig:
                flips[symbol] = sig["direction"]
        for symbol, direction in flips.items():
            trade_id = self.symbol_to_trade.get(symbol)
            if not trade_id or trade_id not in self.open_trades:
                continue
            trade = self.open_trades[trade_id]
            if self.risk.reverse_exit_reason(trade["side"], direction):
                open_time = trade["opened_at"]
                if now_ms() - open_time < 5_000:
                    continue                     # ignore same-bar churn
                self.log("info", "ceo-bot",
                         f"{symbol}: opposite {direction} flip — closing {trade['side']} instantly",
                         topic="monitor")
                await self._close_trade(trade_id, "reverse_signal",
                                        reason_detail=f"indicator flipped to {direction}")

    @staticmethod
    def _fill_key(f) -> str:
        return f"{f.symbol}|{f.order_id}|{f.trade_id}|{f.ts}|{round(f.realized_pnl, 8)}"

    # ============================================================ close & journal
    async def _close_trade(self, trade_id: int, reason: str, price: float | None = None,
                           reason_detail: str = "") -> dict | None:
        trade = self.open_trades.get(trade_id)
        if not trade:
            return None
        symbol = trade["symbol"]
        if symbol in self.pending_close:
            return None
        self.pending_close.add(symbol)
        side = trade["side"]
        try:
            exit_price = price or self.hub.price(symbol)
            orders_cancelled = False
            with contextlib.suppress(Exception):
                await self.hub.cancel_all(symbol)
                orders_cancelled = True
            # if the exchange already closed us (TP/SL filled), there is no position
            position = None
            with contextlib.suppress(Exception):
                position = next((p for p in await self.hub.positions() if p.symbol == symbol), None)
            exit_order_id = ""
            if position is not None:
                try:
                    order = await self.hub.market_order(
                        symbol, "SELL" if side == "LONG" else "BUY", float(trade["qty"]),
                        reduce_only=True,
                        client_id=f"SRx{int(time.time())}{symbol[:6]}")
                    exit_price = order.avg_price or exit_price
                    exit_order_id = str(order.order_id)
                except Exception as exc:
                    self.log("error", "risk-bot",
                             f"{symbol}: close order failed — {str(exc)[:160]} (will retry)")
                    self.pending_close.discard(symbol)
                    return None
            return await self._finalize_close(trade_id, exit_price, reason, reason_detail,
                                              orders_cancelled,
                                              exit_order_id=exit_order_id)
        finally:
            self.pending_close.discard(symbol)

    async def _finalize_close(self, trade_id: int, fallback_price: float,
                              reason: str | None = None,
                              reason_detail: str = "", orders_cancelled: bool = False,
                              exit_order_id: str = "", allow_estimate: bool = True
                              ) -> dict | None:
        trade = self.open_trades.get(trade_id)
        if not trade:
            return None
        symbol = trade["symbol"]
        side = trade["side"]
        entry = float(trade["entry_price"])
        qty = float(trade["qty"])
        opened_at = int(trade["opened_at"])

        gross = fee = 0.0
        exit_price = fallback_price or self.hub.price(symbol)
        closed_at = now_ms()
        entry_oid = str(trade.get("entry_order_id") or "")
        exit_oid = str(exit_order_id or "")
        try:
            fills = await self.hub.fills([symbol], since_ms=opened_at - 2000)
            window = [f for f in fills
                      if opened_at - 2000 <= f.ts <= closed_at + 3000
                      and f.kind in ("TRADE", "MARKET", "STOP_MARKET",
                                     "TAKE_PROFIT_MARKET", "LIQUIDATION")]
            fresh = [f for f in window if self._fill_key(f) not in self.consumed_fills]
            # Every fresh fill on this symbol inside the trade window belongs to
            # this trade (one position per symbol + close cooldown).  Order-id
            # matching is used only to double-check, never to drop fills — an
            # exchange-side stop carries an order id the engine never saw.
            exact = [f for f in fresh if f.order_id and f.order_id in (entry_oid, exit_oid)]
            used = fresh
            if exact and len(exact) < len(fresh):
                self.log("debug", "trade-manager-bot",
                         f"{symbol}: {len(fresh) - len(exact)} fill(s) matched by window "
                         f"only (exchange-side protection) — all counted")
            if used:
                gross = sum(f.realized_pnl for f in used)
                fee = sum(f.commission for f in used
                          if f.commission_asset in ("USDT", ""))
                closing = [f for f in used if f.realized_pnl]
                if closing:
                    exit_price = closing[-1].price or exit_price
                for f in used:
                    self.consumed_fills.add(self._fill_key(f))
                if len(self.consumed_fills) > 4000:
                    self.consumed_fills = set(list(self.consumed_fills)[-2000:])
            elif allow_estimate:
                gross = (exit_price - entry) * qty * (1 if side == "LONG" else -1)
                fee = (entry + exit_price) * qty * 0.0005
        except Exception:
            if allow_estimate:
                gross = (exit_price - entry) * qty * (1 if side == "LONG" else -1)
                fee = (entry + exit_price) * qty * 0.0005

        # funding for the trade window: positive cash flow = received
        funding_cash = 0.0
        if self.hub.transport == "sim":
            with contextlib.suppress(Exception):
                funding_cash = sum(f.realized_pnl for f in self.hub.exchange.fills
                                   if f.kind == "FUNDING_FEE" and f.symbol == symbol
                                   and opened_at <= f.ts <= closed_at + 3000)
        else:
            with contextlib.suppress(Exception):
                rows = await self.hub.funding_income(since_ms=opened_at)
                funding_cash = sum(r.realized_pnl for r in rows
                                   if r.symbol == symbol and r.ts <= closed_at + 3000)

        funding = -funding_cash          # stored positive = paid, negative = received
        net = gross - fee + funding_cash
        sl_distance = abs(entry - float(trade.get("sl_price") or entry))
        r_multiple = (net / (sl_distance * qty)) if sl_distance > 0 and qty else 0.0

        if reason is None:
            reason = self._infer_close_reason(trade, exit_price, orders_cancelled)

        patch = {
            "status": "closed", "exit_price": exit_price, "closed_at": now_ms(),
            "close_reason": reason, "gross_pnl": round(gross, 6),
            "fee_paid": round(fee, 6), "funding_paid": round(funding, 6),
            "net_pnl": round(net, 6), "r_multiple": round(r_multiple, 3),
        }
        await DB.update_trade(trade_id, patch)
        trade.update(patch)
        self.open_trades.pop(trade_id, None)
        self.symbol_to_trade.pop(symbol, None)
        self.cooldowns[symbol] = time.time() + SYMBOL_COOLDOWN_S
        self.symbol_monitor.pop(symbol, None)
        self.series_cache.pop(symbol, None)

        stats = await self.journal.record_close(trade)
        win = net > 0
        # ── bot reactions: celebrate on a win, sad on a loss ───────────────
        for bot in self.registry.all():
            self.registry.set_mood(bot.bot_id, "happy" if win else "sad", 30)
        for bot in self.registry.all():
            if bot.bot_id in (trade.get("scanner_id"), trade.get("analyst_id"),
                              trade.get("exec_bot_id"), trade.get("monitor_bot_id")):
                if win:
                    bot.metrics.wins += 1
                else:
                    bot.metrics.losses += 1
        self.registry.evaluate_promotions()
        BUS.publish("celebration", {
            "win": win, "symbol": symbol, "net": round(net, 2),
            "reason": reason, "r_multiple": round(r_multiple, 2),
            "bots": [b.bot_id for b in self.registry.all()],
            "message": (f"🎉 {symbol} closed in profit +${net:,.2f}" if win else
                        f"💧 {symbol} closed at a loss ${net:,.2f}"),
        })
        self.log("success" if win else "warn", "trade-manager-bot",
                 f"{symbol} {side} closed ({reason}) → net {'+' if net >= 0 else ''}"
                 f"${net:,.2f} · gross ${gross:,.2f} · fees ${fee:,.2f} · "
                 f"funding ${funding:,.2f} · R {r_multiple:+.2f} · "
                 f"win-rate {stats.get('win_rate', 0):.1f}%",
                 {"trade": {k: v for k, v in trade.items() if k != "notes"},
                  "stats": stats}, topic="journal")
        self.registry.set_status("trade-manager-bot",
                                 "celebrating" if win else "sad", f"{symbol} closed",
                                 message=f"net ${net:,.2f} · reason {reason}")
        self.registry.set_status("equity-manager-bot", "success", "equity released",
                                 message=f"released PnL ${stats.get('net_pnl', 0):,.2f}")
        self._wf("close", "done", f"{symbol} {reason} · net ${net:,.2f}")
        BUS.publish("trade.closed", {"trade": {k: v for k, v in trade.items() if k != "notes"},
                                     "stats": stats})
        if len(self.open_trades) < self.store.cfg.risk.max_concurrent_trades:
            self.registry.set_status("monitor-team", "idle", "slot free",
                                     message=f"{len(self.open_trades)}/"
                                             f"{self.store.cfg.risk.max_concurrent_trades} open")
        return trade

    def _infer_close_reason(self, trade: dict, exit_price: float,
                            orders_cancelled: bool) -> str:
        side = trade["side"]
        sl = float(trade.get("sl_price") or 0)
        tp = float(trade.get("tp_price") or 0)
        if tp and exit_price > 0:
            if side == "LONG" and exit_price >= tp * 0.999:
                return "tp"
            if side == "SHORT" and exit_price <= tp * 1.001:
                return "tp"
        if sl and exit_price > 0:
            if side == "LONG" and exit_price <= sl * 1.001:
                return "sl"
            if side == "SHORT" and exit_price >= sl * 0.999:
                return "sl"
        return "manual" if not orders_cancelled else "sl_or_tp"

    # ============================================================ maintenance
    async def _maintenance_loop(self) -> None:
        while self.running:
            try:
                await asyncio.sleep(30)
                if not self.running:
                    return
                api = GOVERNOR.snapshot()
                self.registry.decay_moods()
                self.registry.publish_all(api)
                for bot in self.registry.all():
                    await DB.upsert_bot_stats(bot.bot_id, {
                        "name": bot.name, "rank": bot.rank_index, "score": bot.metrics.score(),
                        "wins": bot.metrics.wins, "losses": bot.metrics.losses,
                        "tasks_done": bot.metrics.tasks_done,
                        "tasks_failed": bot.metrics.tasks_failed,
                        "errors": bot.metrics.errors,
                        "api_spent": bot.metrics.api_spent,
                        "avg_latency_ms": bot.metrics.avg_latency_ms,
                        "promotions": bot.promotions,
                        "last_task_at": int(bot.last_active * 1000),
                    })
                GOVERNOR.note_window_rollover()
                BUS.publish("api.weight", api)
                self.registry.set_status("api-guard-bot",
                                         "halted" if api["halted"] else "working",
                                         "watching API budget",
                                         message=f"{api['used']}/{api['cap']} weight "
                                                 f"({api['used_pct']:.1f}%) · "
                                                 f"{api['blocked_total']} blocked",
                                         publish=False)
                self.registry.publish("api-guard-bot", api)
                self.registry.set_status("maintenance-bot", "working", "hygiene pass",
                                         message="pruning logs, refreshing universe", publish=False)
                with contextlib.suppress(Exception):
                    await DB.prune_logs(20000)
                if self.cycle % 6 == 0:
                    await self.hub.refresh_tickers()
                    await self.hub.build_universe()
                    self.scanner_buckets = self.hub.scanner_allocation(
                        self.store.cfg.engine.scanner_bots,
                        self.store.cfg.engine.assets_per_bot)
                    self._assign_scanner_symbols()
                self.registry.set_status("maintenance-bot", "idle", "data normal",
                                         publish=False)
                # model refresh from our own journal
                if self.cycle and self.cycle % 20 == 0:
                    rows = await self.journal.feature_rows()
                    res = MODEL.train(rows)
                    if res.get("trained"):
                        self.log("info", "analyst-1",
                                 f"confidence model retrained on {res['samples']} trades "
                                 f"(accuracy {res.get('train_accuracy', 0):.3f})")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                with contextlib.suppress(Exception):
                    self.log("warn", "maintenance-bot", f"maintenance error: {str(exc)[:160]}")

    def _note_blocked(self, bot_id: str, message: str) -> None:
        bot = self.registry.get(bot_id)
        if bot:
            bot.metrics.api_blocked += 1
        self.registry.set_status(bot_id, "blocked", "API budget ceiling",
                                 message=message[:160])
        self.log("warn", "api-guard-bot",
                 f"Request from {bot_id} blocked — {message[:160]}", topic="api")

    # ================================================================ helpers
    async def _load_open_trades(self) -> None:
        rows = await DB.open_trades()
        # reconcile with the exchange: drop journal rows with no live position
        try:
            positions = {p.symbol for p in await self.hub.positions()}
        except Exception:
            positions = {r["symbol"] for r in rows}
        for row in rows:
            if row["symbol"] in positions:
                self.open_trades[int(row["id"])] = row
                self.symbol_to_trade[row["symbol"]] = int(row["id"])
            else:
                # the exchange has no such position → the trade closed while we were down
                self.open_trades[int(row["id"])] = row
                with contextlib.suppress(Exception):
                    await self._finalize_close(
                        int(row["id"]), self.hub.price(row["symbol"]) or row["entry_price"],
                        reason="reconciled_missing", allow_estimate=False,
                        reason_detail="position absent on exchange at startup")
                if int(row["id"]) in self.open_trades:      # fallback if finalise bailed out
                    await DB.update_trade(int(row["id"]), {
                        "status": "closed", "close_reason": "reconciled_missing",
                        "closed_at": now_ms()})
                    self.open_trades.pop(int(row["id"]), None)
                    self.symbol_to_trade.pop(row["symbol"], None)
                self.log("warn", "info-verifier-bot",
                         f"{row['symbol']}: journal had an open trade with no exchange "
                         f"position — reconciled and closed at startup")
        if self.open_trades:
            self.log("info", "ceo-bot",
                     f"Adopted {len(self.open_trades)} open position(s) from the journal")

    async def emergency_close_all(self, reason: str = "manual") -> dict:
        """Operator panic button — flattens every engine position."""
        closed = []
        for trade_id in list(self.open_trades.keys()):
            t = await self._close_trade(trade_id, "emergency", reason_detail=reason)
            if t:
                closed.append(t["symbol"])
        self.log("sos", "ceo-bot", f"EMERGENCY FLATTEN executed by operator: {closed or 'nothing open'}")
        return {"closed": closed, "remaining": len(self.open_trades)}

    def status(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "paused": self.paused,
            "emergency_stop": self.emergency_stop,
            "cycle": self.cycle,
            "mode": self.hub.mode,
            "transport": self.hub.transport,
            "universe": len(self.hub.universe),
            "open_trades": len(self.open_trades),
            "max_trades": self.store.cfg.risk.max_concurrent_trades,
            "sos": self.sos,
            "health": {k: v for k, v in self.health.items() if k != "api_weight"},
            "api": GOVERNOR.snapshot(),
            "workflow": self.workflow,
            "last_scan_at": self.last_scan_at,
            "signals_seen": self.signals_seen,
            "started_at": self.started_at,
            "risk": self.risk.effective_exits(),
            "scanner_buckets": [len(b) for b in self.scanner_buckets],
            "links": workflow_links(),
        }


ENGINE: TradingEngine | None = None


def get_engine() -> TradingEngine:
    global ENGINE
    if ENGINE is None:
        ENGINE = TradingEngine()
    return ENGINE
