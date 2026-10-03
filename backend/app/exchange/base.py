"""Exchange-agnostic data contracts shared by the Binance client and the simulator."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


class ExchangeError(RuntimeError):
    """Raised for any exchange REST/WS failure (message is UI-safe)."""

    def __init__(self, message: str, code: int | None = None, status: int | None = None,
                 retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.status = status
        self.retryable = retryable


@dataclass
class SymbolFilter:
    symbol: str
    tick_size: float = 0.01
    step_size: float = 0.001
    min_qty: float = 0.001
    max_qty: float = 1_000_000.0
    min_notional: float = 5.0
    max_leverage: int = 125
    quote: str = "USDT"
    contract_type: str = "PERPETUAL"
    status: str = "TRADING"
    price_precision: int = 2
    qty_precision: int = 3


@dataclass
class Ticker:
    symbol: str
    last: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    quote_volume: float = 0.0
    price_change_pct: float = 0.0
    high: float = 0.0
    low: float = 0.0
    mark: float = 0.0
    funding_rate: float = 0.0
    updated_at: int = 0

    @property
    def spread_bps(self) -> float:
        mid = (self.bid + self.ask) / 2 or self.last
        if not mid or not self.bid or not self.ask:
            return 0.0
        return (self.ask - self.bid) / mid * 10_000.0


@dataclass
class Position:
    symbol: str
    side: Literal["LONG", "SHORT"]
    qty: float
    entry_price: float
    mark_price: float = 0.0
    liquidation_price: float = 0.0
    leverage: int = 10
    margin_type: str = "CROSS"
    unrealized_pnl: float = 0.0
    isolated_margin: float = 0.0
    notional: float = 0.0
    position_side: str = "BOTH"

    @property
    def signed_qty(self) -> float:
        return self.qty if self.side == "LONG" else -self.qty


@dataclass
class AccountSnapshot:
    total_wallet_balance: float = 0.0     # margin balance (equity incl. unrealized)
    total_margin_balance: float = 0.0
    available_balance: float = 0.0
    total_unrealized_pnl: float = 0.0
    total_initial_margin: float = 0.0
    total_maint_margin: float = 0.0
    positions: list[Position] = field(default_factory=list)
    source: str = "binance"
    ts: int = 0

    @property
    def open_count(self) -> int:
        return len([p for p in self.positions if abs(p.qty) > 0])

    @property
    def margin_used(self) -> float:
        return self.total_initial_margin


@dataclass
class OrderResult:
    order_id: str
    symbol: str
    side: str
    type: str
    status: str
    qty: float
    avg_price: float = 0.0
    stop_price: float = 0.0
    client_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Fill:
    symbol: str
    side: str
    qty: float
    price: float
    commission: float = 0.0
    commission_asset: str = "USDT"
    realized_pnl: float = 0.0
    ts: int = 0
    order_id: str = ""
    trade_id: str = ""
    is_maker: bool = False
    kind: str = "TRADE"          # TRADE | FUNDING_FEE


class MarketData(Protocol):
    async def symbol_filters(self, symbol: str) -> SymbolFilter: ...
    async def tickers(self) -> dict[str, Ticker]: ...
    async def klines(self, symbol: str, interval: str, limit: int = 500) -> list: ...
    def last_price(self, symbol: str) -> float: ...


class Broker(Protocol):
    async def account(self) -> AccountSnapshot: ...
    async def positions(self) -> list[Position]: ...
    async def set_leverage(self, symbol: str, leverage: int) -> None: ...
    async def set_margin_type(self, symbol: str, margin_type: str) -> None: ...
    async def market_order(self, symbol: str, side: str, qty: float,
                           reduce_only: bool = False) -> OrderResult: ...
    async def place_tp_sl(self, **kw) -> list[OrderResult]: ...
    async def cancel_all(self, symbol: str) -> None: ...
    async def fills(self, start_ms: int | None = None) -> list[Fill]: ...
