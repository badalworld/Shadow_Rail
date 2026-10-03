"""
Simulation exchange (offline / demo / rehearsal mode).

Implements the *same* interface as the live Binance client so every bot,
analyst and the executor run byte-for-byte identical code paths — only the
transport differs.  Used when:
  • the operator selects SIM mode in Settings, or
  • transport = 'auto' and Binance is unreachable from this host
    (the Connector Bot still raises SOS so nothing is hidden).

Market model: correlated geometric brownian motion with volatility regimes,
trend persistence and occasional volatility shocks — enough structure for the
Shadow Rail state machine to produce realistic flip sequences.
"""
from __future__ import annotations

import asyncio
import math
import random
from dataclasses import dataclass, field
from typing import Any, Callable

from ..bus import BUS
from ..util import Candle, now_ms, tf_ms
from .base import AccountSnapshot, ExchangeError, Fill, OrderResult, Position, SymbolFilter, Ticker

TAKER_FEE = 0.0005      # Binance USDT-M taker (0.05%)
MAKER_FEE = 0.0002
FUNDING_INTERVAL_MS = 8 * 3600 * 1000


@dataclass
class SimSymbol:
    symbol: str
    price: float
    vol: float                 # per-bar sigma (fraction)
    drift: float = 0.0
    regime: float = 1.0
    volume: float = 1.0e6


@dataclass
class SimPosition:
    symbol: str
    side: str
    qty: float
    entry: float
    leverage: int = 10
    margin_type: str = "CROSS"
    unrealized: float = 0.0
    funding_paid: float = 0.0
    fee_paid: float = 0.0


@dataclass
class SimOrder:
    order_id: str
    symbol: str
    side: str
    type: str
    qty: float
    stop_price: float = 0.0
    close_position: bool = True
    status: str = "NEW"
    reduce_only: bool = False


DEFAULT_UNIVERSE: list[tuple[str, float, float]] = [
    # (symbol, start price, per-5m sigma)
    ("BTCUSDT", 68000, 0.0015), ("ETHUSDT", 3400, 0.0019), ("BNBUSDT", 580, 0.0021),
    ("SOLUSDT", 165, 0.0030), ("XRPUSDT", 0.62, 0.0026), ("DOGEUSDT", 0.145, 0.0034),
    ("ADAUSDT", 0.44, 0.0027), ("AVAXUSDT", 34, 0.0032), ("TRXUSDT", 0.118, 0.0018),
    ("LINKUSDT", 16.4, 0.0030), ("DOTUSDT", 6.8, 0.0028), ("MATICUSDT", 0.58, 0.0031),
    ("LTCUSDT", 82, 0.0023), ("BCHUSDT", 420, 0.0031), ("NEARUSDT", 5.4, 0.0036),
    ("APTUSDT", 8.6, 0.0035), ("ARBUSDT", 0.92, 0.0037), ("OPUSDT", 1.9, 0.0036),
    ("SUIUSDT", 1.32, 0.0040), ("INJUSDT", 22, 0.0041), ("SEIUSDT", 0.42, 0.0042),
    ("TIAUSDT", 7.1, 0.0043), ("FILUSDT", 4.6, 0.0033), ("ATOMUSDT", 8.2, 0.0030),
    ("ETCUSDT", 26, 0.0029), ("UNIUSDT", 8.1, 0.0032), ("AAVEUSDT", 92, 0.0033),
    ("MKRUSDT", 2400, 0.0034), ("CRVUSDT", 0.42, 0.0040), ("LDOUSDT", 2.1, 0.0039),
    ("RUNEUSDT", 5.2, 0.0041), ("GALAUSDT", 0.032, 0.0044), ("SANDUSDT", 0.34, 0.0037),
    ("MANAUSDT", 0.38, 0.0037), ("AXSUSDT", 6.4, 0.0038), ("GMTUSDT", 0.21, 0.0042),
    ("APEUSDT", 1.2, 0.0040), ("CHZUSDT", 0.086, 0.0039), ("ENJUSDT", 0.28, 0.0038),
    ("ZILUSDT", 0.022, 0.0039), ("ONEUSDT", 0.016, 0.0041), ("IOTAUSDT", 0.21, 0.0035),
    ("ALGOUSDT", 0.17, 0.0034), ("VETUSDT", 0.031, 0.0036), ("THETAUSDT", 1.6, 0.0039),
    ("EGLDUSDT", 34, 0.0038), ("FLOWUSDT", 0.78, 0.0039), ("XTZUSDT", 0.94, 0.0033),
    ("EOSUSDT", 0.68, 0.0032), ("NEOUSDT", 14.5, 0.0034), ("DASHUSDT", 28, 0.0033),
    ("ZECUSDT", 24, 0.0034), ("COMPUSDT", 52, 0.0037), ("SNXUSDT", 2.4, 0.0039),
    ("1INCHUSDT", 0.34, 0.0038), ("SUSHIUSDT", 0.82, 0.0040), ("YFIUSDT", 6200, 0.0040),
    ("BALUSDT", 3.1, 0.0039), ("KNCUSDT", 0.52, 0.0039), ("ZRXUSDT", 0.33, 0.0038),
    ("BATUSDT", 0.19, 0.0037), ("ANKRUSDT", 0.03, 0.0041), ("CVCUSDT", 0.11, 0.0040),
    ("STORJUSDT", 0.42, 0.0042), ("AUDIOUSDT", 0.16, 0.0043), ("CELOUSDT", 0.72, 0.0040),
    ("MTLUSDT", 1.2, 0.0041), ("OGNUSDT", 0.12, 0.0042), ("RSRUSDT", 0.006, 0.0044),
    ("CTSIUSDT", 0.19, 0.0043), ("BELUSDT", 0.6, 0.0044), ("WAVESUSDT", 1.6, 0.0045),
    ("KAVAUSDT", 0.62, 0.0040), ("BANDUSDT", 1.3, 0.0041), ("OCEANUSDT", 0.62, 0.0042),
    ("ARUSDT", 24, 0.0043), ("RNDRUSDT", 7.4, 0.0044), ("WOOUSDT", 0.21, 0.0043),
    ("MASKUSDT", 2.6, 0.0044), ("LPTUSDT", 14, 0.0045), ("IMXUSDT", 1.6, 0.0042),
    ("GMXUSDT", 32, 0.0043), ("DYDXUSDT", 1.7, 0.0044), ("MAGICUSDT", 0.62, 0.0045),
    ("HOOKUSDT", 0.72, 0.0045), ("IDUSDT", 0.42, 0.0046), ("ARBUSDT", 0.92, 0.0037),
    ("PEPEUSDT", 0.0000092, 0.0052), ("SHIBUSDT", 0.0000195, 0.0046),
    ("FLOKIUSDT", 0.00016, 0.0050), ("BONKUSDT", 0.000024, 0.0051),
    ("WIFUSDT", 2.4, 0.0050), ("ORDIUSDT", 38, 0.0051), ("JUPUSDT", 1.1, 0.0048),
    ("PYTHUSDT", 0.55, 0.0047), ("STRKUSDT", 1.1, 0.0048), ("WLDUSDT", 4.9, 0.0049),
    ("ENSUSDT", 22, 0.0040), ("GRTUSDT", 0.22, 0.0041), ("CAKEUSDT", 2.6, 0.0040),
    ("ROSEUSDT", 0.09, 0.0042), ("JASMYUSDT", 0.023, 0.0046), ("ACHUSDT", 0.026, 0.0044),
    ("IOTXUSDT", 0.048, 0.0045), ("CKBUSDT", 0.012, 0.0045), ("DENTUSDT", 0.0013, 0.0046),
    ("HOTUSDT", 0.0022, 0.0045), ("STMXUSDT", 0.0082, 0.0047), ("LRCUSDT", 0.28, 0.0041),
    ("DUSKUSDT", 0.26, 0.0046), ("ARPAUSDT", 0.062, 0.0047), ("PERPUSDT", 0.92, 0.0048),
    ("TRBUSDT", 62, 0.0046), ("BLZUSDT", 0.24, 0.0047), ("CHRUSDT", 0.28, 0.0046),
    ("API3USDT", 2.6, 0.0045), ("PUNDIXUSDT", 0.52, 0.0044), ("TLMUSDT", 0.016, 0.0046),
    ("ALICEUSDT", 1.4, 0.0047), ("SUPERUSDT", 0.72, 0.0048), ("VOXELUSDT", 0.18, 0.0049),
    ("HIGHUSDT", 1.9, 0.0048), ("PHBUSDT", 1.3, 0.0049), ("RDNTUSDT", 0.22, 0.0050),
    ("MBOXUSDT", 0.28, 0.0049), ("MOVRUSDT", 12, 0.0048), ("DENTUSDT", 0.0013, 0.0046),
    ("ACEUSDT", 2.4, 0.0050), ("MANTAUSDT", 1.5, 0.0051), ("ALTUSDT", 0.32, 0.0051),
    ("DYMUSDT", 3.1, 0.0050), ("PIXELUSDT", 0.32, 0.0051), ("PORTALUSDT", 0.62, 0.0052),
    ("AXLUSDT", 0.92, 0.0050), ("METISUSDT", 42, 0.0049), ("AEVOUSDT", 1.1, 0.0052),
    ("ETHFIUSDT", 3.4, 0.0051), ("BOMEUSDT", 0.0086, 0.0054), ("SAGAUSDT", 1.3, 0.0053),
    ("OMNIUSDT", 12, 0.0051), ("REZUSDT", 0.12, 0.0053), ("BBUSDT", 0.42, 0.0052),
    ("NOTUSDT", 0.014, 0.0055), ("IOUSDT", 3.6, 0.0054), ("ZKUSDT", 0.16, 0.0055),
    ("LISTAUSDT", 0.52, 0.0054), ("ZROUSDT", 3.4, 0.0055), ("GUSDT", 0.032, 0.0053),
    ("BANANAUSDT", 42, 0.0056), ("RENDERUSDT", 6.4, 0.0044), ("TONUSDT", 6.7, 0.0038),
    ("TAOUSDT", 420, 0.0048), ("FTMUSDT", 0.62, 0.0039), ("KASUSDT", 0.16, 0.0046),
]


def _dedupe_universe() -> list[tuple[str, float, float]]:
    seen: set[str] = set()
    out: list[tuple[str, float, float]] = []
    for row in DEFAULT_UNIVERSE:
        if row[0] in seen:
            continue
        seen.add(row[0])
        out.append(row)
    while len(out) < 150:                      # pad deterministically to >= 150
        i = len(out)
        out.append((f"ALT{i}USDT", 0.5 + (i % 40) * 0.37, 0.0035 + (i % 9) * 0.0002))
    return out


class SimExchange:
    """Synthetic USDT-M futures exchange (market data + paper broker)."""

    def __init__(self, balance: float = 10_000.0, universe: int = 150,
                 time_accel: float = 30.0, interval: str = "5m",
                 history_bars: int = 1200, warm_candles: int = 240,
                 seed: int = 20261003, restore: dict | None = None):
        self.rng = random.Random(seed)
        self.balance = balance
        self.start_balance = balance
        self.time_accel = max(1.0, time_accel)
        self.interval = interval
        self.bar_ms = tf_ms(interval)
        self.history_bars = history_bars
        self.warm_candles = warm_candles
        self._universe_rows = _dedupe_universe()[:max(20, universe)]
        self.syms: dict[str, SimSymbol] = {}
        for sym, price, vol in self._universe_rows:
            # volume is expressed in *base* units so that quoteVolume (USD) is realistic
            quote_turnover = self.rng.uniform(6e6, 5e8) * (1.6 if sym in ("BTCUSDT", "ETHUSDT") else 1.0)
            self.syms[sym] = SimSymbol(symbol=sym, price=price, vol=vol,
                                       volume=quote_turnover / max(price, 1e-12))
        self.candles: dict[str, list[Candle]] = {}
        self.tickers: dict[str, Ticker] = {}
        self.positions: dict[str, SimPosition] = {}
        self.orders: dict[str, SimOrder] = {}
        self.fills: list[Fill] = []
        self.realized_today: float = 0.0
        self.fees_paid: float = 0.0
        self.funding_net: float = 0.0
        self._order_seq = 1000
        self._virtual_now = (now_ms() // self.bar_ms) * self.bar_ms
        self._last_funding_ms = self._virtual_now
        # continuity across restarts (see export_state/import_state)
        self._restore: dict | None = restore
        self.filters: dict[str, SymbolFilter] = {}
        self._on_candle: Callable | None = None
        self._on_close: Callable | None = None
        self._task: asyncio.Task | None = None
        self._callbacks: list[Callable] = []
        self.running = False
        self._build_filters()
        self._seed_history()
        if restore:
            self.import_state(restore)

    # ------------------------------------------------------------- universe
    def _build_filters(self) -> None:
        for sym, price, _vol in self._universe_rows:
            tick = 0.01 if price >= 100 else (0.001 if price >= 1 else 0.00001)
            step = 0.001 if price >= 100 else (0.1 if price >= 1 else 1.0)
            # Binance's LOT_SIZE maxQty is generous — size it so an 8%-margin order
            # at 10x always fits, otherwise cheap coins get silently shrunk.
            max_qty = max(1_000_000.0, (30_000.0 / max(price, 1e-12)))
            self.filters[sym] = SymbolFilter(
                symbol=sym, tick_size=tick, step_size=step,
                min_qty=step, min_notional=5.0, max_qty=max_qty, max_leverage=125,
                quote="USDT", contract_type="PERPETUAL", status="TRADING")

    def _advance(self, s: "SymbolState", prev_close: float) -> Candle:
        """
        One realistic bar: volatility regimes + an AR(1) momentum term.

        A pure random walk punishes any trend-following strategy, so the
        simulation would misrepresent the engine.  Real crypto has momentum
        persistence and volatility clustering, which is what this models.
        """
        if self.rng.random() < 0.0015:                       # regime switch (~1/670 bars)
            s.regime = self.rng.choice([0.7, 1.0, 1.0, 1.35, 1.8, 2.4])
        # momentum: AR(1) with ~25-bar half life, plus rare shock impulses
        s.drift = 0.97 * s.drift + 0.03 * self.rng.gauss(0, 1) * 0.55
        if self.rng.random() < 0.002:
            s.drift += self.rng.choice([-1, 1]) * self.rng.uniform(0.6, 2.2)
        s.drift = max(-2.2, min(2.2, s.drift))
        step = s.vol * s.regime * self.rng.gauss(0, 1)
        drift = s.drift * s.vol * 0.55
        o = prev_close
        c = max(prev_close * 1e-6, prev_close * (1.0 + drift + step))
        wick = abs(self.rng.gauss(0, 1)) * s.vol * s.regime * 0.8 * prev_close
        hi = max(o, c) + wick
        lo = max(1e-12, min(o, c) - abs(self.rng.gauss(0, 1)) * s.vol * s.regime * 0.8 * prev_close)
        vol = s.volume * (0.4 + abs(self.rng.gauss(0, 1)))
        return Candle(0, o, hi, lo, c, vol)

    def _seed_history(self) -> None:
        """Deterministic pre-history so indicators are warm on first scan."""
        for sym, s in self.syms.items():
            candles: list[Candle] = []
            price = s.price
            t = self._virtual_now - self.bar_ms * self.history_bars
            s.drift = self.rng.gauss(0, 1) * 0.6
            for i in range(self.history_bars):
                candle = self._advance(s, price)
                candles.append(Candle(t + i * self.bar_ms, candle.o, candle.h,
                                      candle.l, candle.c, candle.v))
                price = candle.c
            anchor = (self._restore or {}).get("end_prices", {}).get(sym)
            if anchor and price > 0:
                candles, price = self._anchor(candles, float(anchor))
            s.price = price
            self.candles[sym] = candles
            self.tickers[sym] = Ticker(
                symbol=sym, last=price, bid=price * 0.99995, ask=price * 1.00005,
                quote_volume=s.volume * price, price_change_pct=0.0,
                high=max(c.h for c in candles[-288:]), low=min(c.l for c in candles[-288:]),
                mark=price, funding_rate=0.0001, updated_at=now_ms())

    async def income_totals(self) -> dict:
        """Everything this account has ever paid or earned (the cash ledger)."""
        realized = sum(f.realized_pnl for f in self.fills
                       if f.kind not in ("FUNDING_FEE",))
        funding = sum(f.realized_pnl for f in self.fills if f.kind == "FUNDING_FEE")
        return {"realized": realized, "fees": self.fees_paid, "funding": funding,
                "fills": len(self.fills)}

    # ------------------------------------------------------- state continuity
    @staticmethod
    def _pick(obj: Any, *names: str) -> dict:
        """Copy only the attributes the dataclass actually has."""
        return {n: getattr(obj, n) for n in names if hasattr(obj, n)}

    def export_state(self) -> dict:
        """Snapshot the paper account so a restart does not wipe the demo."""
        return {
            "balance": self.balance,
            "start_balance": self.start_balance,
            "virtual_now": self._virtual_now,
            "order_seq": self._order_seq,
            "realized_today": self.realized_today,
            "fees_paid": self.fees_paid,
            "funding_net": self.funding_net,
            "end_prices": {sym: s.price for sym, s in self.syms.items()},
            "positions": [
                {"symbol": p.symbol, "side": p.side, "qty": p.qty, "entry": p.entry,
                 "leverage": p.leverage, "margin_type": p.margin_type}
                for p in self.positions.values()
            ],
            "orders": [
                {"order_id": o.order_id, "symbol": o.symbol, "type": o.type,
                 "side": o.side, "qty": o.qty, "stop_price": o.stop_price,
                 "close_position": o.close_position,
                 "reduce_only": o.reduce_only, "status": o.status}
                for o in self.orders.values() if o.status == "NEW"
            ],
        }

    @staticmethod
    def _anchor(candles: list[Candle], anchor: float) -> tuple[list[Candle], float]:
        """Rescale a candle series so its last close equals `anchor`."""
        last = candles[-1].c if candles else 0.0
        if not candles or last <= 0 or anchor <= 0:
            return candles, anchor or last
        k = anchor / last
        return ([Candle(c.t, c.o * k, c.h * k, c.l * k, c.c * k, c.v) for c in candles],
                anchor)

    def import_state(self, state: dict) -> None:
        """Restore a previous snapshot (at construction or shortly after)."""
        if not state:
            return
        # keep the chart continuous: the last seeded candle must close at the
        # price the previous run stopped on
        for sym, price in (state.get("end_prices") or {}).items():
            if sym in self.candles and price:
                self.candles[sym], self.syms[sym].price = self._anchor(
                    self.candles[sym], float(price))
                t = self.tickers.get(sym)
                if t:
                    t.last = t.bid = t.ask = t.mark = self.syms[sym].price
        self.balance = float(state.get("balance", self.balance))
        self.start_balance = float(state.get("start_balance", self.start_balance))
        self._order_seq = int(state.get("order_seq", self._order_seq))
        self.realized_today = float(state.get("realized_today", 0.0))
        self.fees_paid = float(state.get("fees_paid", 0.0))
        self.funding_net = float(state.get("funding_net", 0.0))
        for row in state.get("positions", []):
            self.positions[row["symbol"]] = SimPosition(
                symbol=row["symbol"], side=row["side"], qty=float(row["qty"]),
                entry=float(row["entry"]), leverage=int(row.get("leverage", 10)),
                margin_type=row.get("margin_type", "CROSS"))
        for row in state.get("orders", []):
            self.orders[row["order_id"]] = SimOrder(
                order_id=row["order_id"], symbol=row["symbol"], type=row["type"],
                side=row["side"], qty=float(row["qty"]),
                stop_price=float(row.get("stop_price") or 0),
                close_position=bool(row.get("close_position", True)),
                reduce_only=bool(row.get("reduce_only")), status=row.get("status", "NEW"))

    # ---------------------------------------------------------- market data
    async def symbol_filters(self, symbol: str) -> SymbolFilter:
        return self.filters.get(symbol, SymbolFilter(symbol=symbol))

    async def all_filters(self) -> dict[str, SymbolFilter]:
        return self.filters

    async def tickers_all(self) -> dict[str, Ticker]:
        return self.tickers

    async def klines(self, symbol: str, interval: str = "5m", limit: int = 500,
                     start_ms: int | None = None, end_ms: int | None = None) -> list[Candle]:
        data = self.candles.get(symbol, [])
        if end_ms or start_ms:
            data = [c for c in data
                    if (not start_ms or c.t >= start_ms) and (not end_ms or c.t <= end_ms)]
        return data[-limit:]

    def last_price(self, symbol: str) -> float:
        t = self.tickers.get(symbol)
        return t.last if t else 0.0

    async def ping(self) -> float:
        await asyncio.sleep(0)
        return 3.0

    # --------------------------------------------------------------- broker
    async def account(self) -> AccountSnapshot:
        unreal = 0.0
        positions: list[Position] = []
        for p in self.positions.values():
            mark = self.last_price(p.symbol) or p.entry
            if p.side == "LONG":
                pnl = (mark - p.entry) * p.qty
            else:
                pnl = (p.entry - mark) * p.qty
            p.unrealized = pnl
            unreal += pnl
            notional = mark * p.qty
            positions.append(Position(
                symbol=p.symbol, side=p.side, qty=p.qty, entry_price=p.entry,
                mark_price=mark,
                liquidation_price=self.liquidation_price(p.symbol, p.side, p.entry, p.qty, p.leverage),
                leverage=p.leverage, margin_type=p.margin_type,
                unrealized_pnl=pnl, notional=notional,
                isolated_margin=p.entry * p.qty / max(1, p.leverage)))
        used = sum(p.entry * p.qty / max(1, p.leverage) for p in self.positions.values())
        equity = self.balance + unreal
        return AccountSnapshot(
            total_wallet_balance=equity, total_margin_balance=equity,
            available_balance=max(0.0, equity - used),
            total_unrealized_pnl=unreal, total_initial_margin=used,
            total_maint_margin=used * 0.005, positions=positions,
            source="sim", ts=now_ms())

    async def positions_all(self) -> list[Position]:
        snap = await self.account()
        return snap.positions

    async def set_leverage(self, symbol: str, leverage: int) -> None:
        self.filters.setdefault(symbol, SymbolFilter(symbol=symbol))

    async def set_margin_type(self, symbol: str, margin_type: str) -> None:
        return None

    async def leverage_brackets(self) -> list[dict]:
        return []

    def liquidation_price(self, symbol: str, side: str, entry: float, qty: float,
                          leverage: int) -> float:
        """Approximate cross-margin liquidation (MMR 0.5%)."""
        mmr = 0.005
        if side == "LONG":
            return max(0.0, entry * (1.0 - 1.0 / leverage + mmr))
        return entry * (1.0 + 1.0 / leverage - mmr)

    async def market_order(self, symbol: str, side: str, qty: float,
                           reduce_only: bool = False, client_id: str = "") -> OrderResult:
        price = self.last_price(symbol)
        if not price:
            raise ExchangeError(f"unknown symbol {symbol}")
        slip = price * (0.00005 + abs(self.rng.gauss(0, 0.00004)))
        fill_price = price + slip if side.upper() == "BUY" else price - slip
        return self._apply_fill(symbol, side.upper(), qty, fill_price, reduce_only, client_id)

    async def stop_market(self, symbol: str, side: str, stop_price: float,
                          close_position: bool = True, qty: float | None = None,
                          working_type: str = "MARK_PRICE", client_id: str = "") -> OrderResult:
        self._order_seq += 1
        oid = str(self._order_seq)
        order = SimOrder(order_id=oid, symbol=symbol, side=side.upper(),
                         type="STOP_MARKET", qty=qty or 0.0, stop_price=stop_price,
                         close_position=close_position)
        self.orders[oid] = order
        return OrderResult(order_id=oid, symbol=symbol, side=side.upper(),
                           type="STOP_MARKET", status="NEW", qty=qty or 0.0,
                           stop_price=stop_price, client_id=client_id)

    async def take_profit_market(self, symbol: str, side: str, stop_price: float,
                                 close_position: bool = True, qty: float | None = None,
                                 working_type: str = "MARK_PRICE", client_id: str = "") -> OrderResult:
        self._order_seq += 1
        oid = str(self._order_seq)
        order = SimOrder(order_id=oid, symbol=symbol, side=side.upper(),
                         type="TAKE_PROFIT_MARKET", qty=qty or 0.0, stop_price=stop_price,
                         close_position=close_position)
        self.orders[oid] = order
        return OrderResult(order_id=oid, symbol=symbol, side=side.upper(),
                           type="TAKE_PROFIT_MARKET", status="NEW", qty=qty or 0.0,
                           stop_price=stop_price, client_id=client_id)

    async def cancel_all(self, symbol: str) -> None:
        for oid, o in list(self.orders.items()):
            if o.symbol == symbol and o.status == "NEW":
                o.status = "CANCELED"
                self.orders.pop(oid, None)

    async def open_orders(self, symbol: str | None = None) -> list[dict]:
        return [{"orderId": o.order_id, "symbol": o.symbol, "type": o.type,
                 "stopPrice": o.stop_price, "status": o.status}
                for o in self.orders.values()
                if o.status == "NEW" and (not symbol or o.symbol == symbol)]

    async def query_order(self, symbol: str, order_id: str) -> dict:
        o = self.orders.get(order_id)
        return {"orderId": order_id, "status": o.status if o else "UNKNOWN"}

    async def user_trades(self, symbol: str, start_ms: int | None = None,
                          limit: int = 500) -> list[Fill]:
        out = [f for f in self.fills if f.symbol == symbol]
        if start_ms:
            out = [f for f in out if f.ts >= start_ms]
        return out[-limit:]

    # ------------------------------------------------------------ mechanics
    def _apply_fill(self, symbol: str, side: str, qty: float, price: float,
                    reduce_only: bool, client_id: str = "", kind: str = "TRADE") -> OrderResult:
        self._order_seq += 1
        oid = str(self._order_seq)
        pos = self.positions.get(symbol)
        fee = abs(qty * price) * TAKER_FEE
        realized = 0.0
        if pos is None:
            if reduce_only:
                return OrderResult(order_id=oid, symbol=symbol, side=side, type="MARKET",
                                   status="REJECTED", qty=0.0)
            self.positions[symbol] = SimPosition(symbol=symbol, side="LONG" if side == "BUY" else "SHORT",
                                                 qty=qty, entry=price)
        else:
            same_dir = (pos.side == "LONG" and side == "BUY") or (pos.side == "SHORT" and side == "SELL")
            if same_dir:
                new_qty = pos.qty + qty
                pos.entry = (pos.entry * pos.qty + price * qty) / new_qty
                pos.qty = new_qty
            else:
                closing = min(qty, pos.qty)
                if pos.side == "LONG":
                    realized = (price - pos.entry) * closing
                else:
                    realized = (pos.entry - price) * closing
                pos.qty -= closing
                if pos.qty <= 1e-12:
                    self.positions.pop(symbol, None)
        self.balance += realized - fee
        self.fees_paid += fee
        fill = Fill(symbol=symbol, side=side, qty=qty, price=price, commission=fee,
                    realized_pnl=realized, ts=now_ms(), order_id=oid,
                    trade_id=f"sim-{oid}", kind=kind)
        self.fills.append(fill)
        if len(self.fills) > 5000:
            self.fills = self.fills[-3000:]
        BUS.publish("sim.fill", {"symbol": symbol, "side": side, "qty": qty,
                                 "price": price, "realized": realized, "fee": fee,
                                 "order_id": oid, "client_id": client_id})
        return OrderResult(order_id=oid, symbol=symbol, side=side, type="MARKET",
                           status="FILLED", qty=qty, avg_price=price, raw={"sim": True})

    async def close_position_market(self, symbol: str, qty: float | None = None) -> OrderResult:
        pos = self.positions.get(symbol)
        if not pos:
            raise ExchangeError(f"no sim position on {symbol}")
        side = "SELL" if pos.side == "LONG" else "BUY"
        return self._apply_fill(symbol, side, qty or pos.qty, self.last_price(symbol), True)

    # ------------------------------------------------------- virtual clock
    async def start_stream(self, on_candle: Callable, on_close: Callable | None = None) -> None:
        """Drive the accelerated virtual market (one 5m bar per accel-scaled tick)."""
        self._on_candle = on_candle
        self._on_close = on_close
        self.running = True
        self._task = asyncio.create_task(self._clock_loop())

    async def stop_stream(self) -> None:
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    @property
    def tick_seconds(self) -> float:
        return max(2.0, (self.bar_ms / 1000.0) / self.time_accel)

    async def _clock_loop(self) -> None:
        while self.running:
            await asyncio.sleep(self.tick_seconds)
            try:
                await self.step_bar()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                BUS.publish("sim.error", {"error": str(exc)[:200]})

    async def step_bar(self) -> None:
        """Advance the virtual market by one candle for every symbol."""
        self._virtual_now += self.bar_ms
        for sym, s in self.syms.items():
            candles = self.candles[sym]
            prev_close = candles[-1].c if candles else s.price
            gen = self._advance(s, prev_close)
            o, hi, lo, c, vol = gen.o, gen.h, gen.l, gen.c, gen.v
            candle = Candle(self._virtual_now, o, hi, lo, c, vol)
            candles.append(candle)
            if len(candles) > self.history_bars + 2000:
                del candles[: self.history_bars]
            s.price = c
            # forming candle → UI, then closed candle → engine
            if self._on_candle:
                res = self._on_candle({"symbol": sym, "open_time": candle.t,
                                       "close_time": candle.t + self.bar_ms - 1,
                                       "open": o, "high": hi, "low": lo, "close": c,
                                       "volume": vol, "closed": False})
                if asyncio.iscoroutine(res):
                    await res
            t = self.tickers.get(sym)
            if t:
                t.last, t.mark = c, c
                t.bid, t.ask = c * 0.99995, c * 1.00005
                t.updated_at = now_ms()
                t.quote_volume = vol * c
        await self._check_protective_orders()
        await self._maybe_funding()
        if self._on_close:
            for sym in self.syms:
                c = self.candles[sym][-1]
                res = self._on_close({"symbol": sym, "open_time": c.t,
                                      "close_time": c.t + self.bar_ms - 1,
                                      "open": c.o, "high": c.h, "low": c.l,
                                      "close": c.c, "volume": c.v, "closed": True})
                if asyncio.iscoroutine(res):
                    await res
        BUS.publish("sim.bar", {"virtual_time": self._virtual_now,
                                "symbols": len(self.syms)})

    async def _check_protective_orders(self) -> None:
        """
        Fill STOP_MARKET / TAKE_PROFIT_MARKET orders like Binance does: the mark
        price *crossing* the trigger fires the order, even if the bar gapped
        straight through it.  A gapped open fills at the (worse) open price so
        the simulation never under-states a stop-out.
        """
        for oid, o in list(self.orders.items()):
            if o.status != "NEW":
                continue
            pos = self.positions.get(o.symbol)
            if not pos:
                o.status = "CANCELED"
                self.orders.pop(oid, None)
                continue
            candle = self.candles[o.symbol][-1]
            # Where the order sits relative to the position decides how it fires:
            # below entry on a long = stop (sell on the way down), above = target.
            sits_above = o.stop_price >= pos.entry
            if pos.side == "LONG":
                upward = sits_above          # take-profit
            else:
                upward = sits_above          # stop-loss for a short
            if upward:
                crossed = candle.h >= o.stop_price or candle.o >= o.stop_price
                gap = candle.o >= o.stop_price
                fill_price = max(o.stop_price, candle.o) if gap else o.stop_price
            else:
                crossed = candle.l <= o.stop_price or candle.o <= o.stop_price
                gap = candle.o <= o.stop_price
                fill_price = min(o.stop_price, candle.o) if gap else o.stop_price
            if not crossed:
                continue
            side = "SELL" if pos.side == "LONG" else "BUY"
            self._apply_fill(o.symbol, side, pos.qty, fill_price, True, kind=o.type)
            o.status = "FILLED"
            self.orders.pop(oid, None)
            await self.cancel_all(o.symbol)
            BUS.publish("sim.protective", {"symbol": o.symbol, "type": o.type,
                                           "price": fill_price, "stop": o.stop_price,
                                           "gap": gap})

    async def _maybe_funding(self) -> None:
        if self._virtual_now - self._last_funding_ms < FUNDING_INTERVAL_MS:
            return
        self._last_funding_ms = self._virtual_now
        for sym, pos in list(self.positions.items()):
            rate = self.rng.gauss(0.0001, 0.0002)
            notional = pos.entry * pos.qty
            payment = rate * notional * (-1 if pos.side == "LONG" else 1)
            self.balance += payment
            self.funding_net += payment
            pos.funding_paid += payment
            self.fills.append(Fill(symbol=sym, side="FUNDING_FEE", qty=0.0, price=0.0,
                                   realized_pnl=payment, ts=now_ms(), kind="FUNDING_FEE",
                                   trade_id=f"funding-{self._virtual_now}"))
        BUS.publish("sim.funding", {"at": self._virtual_now})


# Backwards-friendly alias used by the hub
SimBroker = SimExchange
