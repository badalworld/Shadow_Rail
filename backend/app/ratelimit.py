"""
API Weight Governor  —  "API Guard Bot" backend.

Binance USDT-M Futures allows 2400 request-weight per minute per IP.
Operator rule: the engine must NEVER exceed 95% of that budget, and when the
95% ceiling is reached *nobody* may call the API until the sliding window
drains (prevents a 418/429 IP ban).

Every REST call inside the engine goes through `WeightGovernor.acquire()`,
which is attributed to the calling bot so the dashboard can show per-bot spend.
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field

# Real Binance USDT-M weight costs used by this engine.
WEIGHTS: dict[str, int] = {
    "ping": 1,
    "time": 1,
    "exchangeInfo": 1,          # cached, refreshed rarely
    "ticker24hr_all": 40,
    "premiumIndex_all": 1,
    "markPrice_all": 1,
    "klines": 1,                # limit <= 100
    "klines_mid": 2,            # 100 < limit <= 500
    "klines_big": 5,            # 500 < limit <= 1000
    "depth": 5,
    "leverageBracket": 1,
    "positionRisk": 5,
    "account": 5,
    "balance": 5,
    "openOrders": 40,
    "userTrades": 5,
    "income": 30,
    "order": 1,
    "batchOrder": 5,
    "cancelOrder": 1,
    "allOpenOrders": 1,
    "leverage": 1,
    "marginType": 1,
    "listenKey": 1,
}


class RateLimitHalt(RuntimeError):
    """Raised when the 95% ceiling is reached and the call must not be sent."""

    def __init__(self, used_pct: float, retry_after: float, bot: str):
        self.used_pct = used_pct
        self.retry_after = retry_after
        self.bot = bot
        super().__init__(
            f"API budget ceiling reached ({used_pct:.1f}%) — '{bot}' blocked, "
            f"retry in {retry_after:.1f}s"
        )


@dataclass
class BotBudget:
    """Optional per-bot soft allocation so one bot cannot starve the others."""
    bot_id: str
    share_pct: float = 0.0        # 0 = unlimited (only used for fairness stats)
    spent_window: int = 0
    spent_total: int = 0
    blocked_count: int = 0


@dataclass
class WeightGovernor:
    limit_per_min: int = 2400
    budget_pct: float = 95.0
    allow_critical_above_cap: bool = False
    window_s: float = 60.0

    _events: deque[tuple[float, int]] = field(default_factory=deque)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _budgets: dict[str, BotBudget] = field(default_factory=dict)
    _peak_pct: float = 0.0
    _blocked_total: int = 0
    _halt_since: float | None = None
    _server_used: int = 0

    # ------------------------------------------------------------------ api
    @property
    def cap(self) -> int:
        return int(self.limit_per_min * self.budget_pct / 100.0)

    def budget(self, bot_id: str) -> BotBudget:
        if bot_id not in self._budgets:
            self._budgets[bot_id] = BotBudget(bot_id=bot_id)
        return self._budgets[bot_id]

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_s
        while self._events and self._events[0][0] < cutoff:
            self._events.popleft()

    def used(self) -> int:
        self._prune(time.monotonic())
        return sum(w for _, w in self._events)

    def used_pct(self) -> float:
        return 100.0 * self.used() / max(1, self.limit_per_min)

    def available(self) -> int:
        return max(0, self.cap - self.used())

    def can_send(self, weight: int) -> bool:
        return self.used() + weight <= self.cap

    def retry_after(self) -> float:
        """Seconds until the oldest event leaves the sliding window."""
        self._prune(time.monotonic())
        if not self._events:
            return 0.0
        return max(0.0, self._events[0][0] + self.window_s - time.monotonic())

    # --------------------------------------------------------------- acquire
    async def acquire(self, bot_id: str, op: str, critical: bool = False,
                      weight: int | None = None) -> int:
        """
        Reserve weight *before* an HTTP request is sent.
        Raises RateLimitHalt when the 95% ceiling would be crossed.
        """
        w = WEIGHTS.get(op, 1) if weight is None else weight
        async with self._lock:
            now = time.monotonic()
            self._prune(now)
            used = sum(x for _, x in self._events)
            over_cap = used + w > self.cap
            if over_cap:
                b = self.budget(bot_id)
                b.blocked_count += 1
                self._blocked_total += 1
                if self._halt_since is None:
                    self._halt_since = now
                # Strict mode (as specified): no exceptions, even for critical ops.
                if not (critical and self.allow_critical_above_cap):
                    raise RateLimitHalt(100.0 * used / max(1, self.limit_per_min),
                                        max(0.0, self._events[0][0] + self.window_s - now)
                                        if self._events else 1.0, bot_id)
            self._events.append((now, w))
            b = self.budget(bot_id)
            b.spent_window += w
            b.spent_total += w
            pct = 100.0 * (used + w) / max(1, self.limit_per_min)
            self._peak_pct = max(self._peak_pct, pct)
            if used + w <= self.cap:
                self._halt_since = None
            return w

    def note_window_rollover(self) -> None:
        """Reset per-window counters (called by the API Guard Bot's beat)."""
        now = time.monotonic()
        self._prune(now)
        for b in self._budgets.values():
            b.spent_window = 0

    # ------------------------------------------------- exchange truth sync
    def sync_from_header(self, used_weight_1m: int, limit: int | None = None) -> None:
        """
        Binance reports real IP usage in X-MBX-USED-WEIGHT-1M.  Trust the
        exchange over our own estimate whenever it is higher, so the 95% rule
        can never be fooled by a request we did not account for.
        """
        if limit:
            self.limit_per_min = int(limit)
        self._server_used = int(used_weight_1m)
        local = self.used()
        if self._server_used > local:
            now = time.monotonic()
            self._events.append((now, self._server_used - local))
            self._peak_pct = max(self._peak_pct, 100.0 * self._server_used / max(1, self.limit_per_min))

    # ----------------------------------------------------------------- stats
    def snapshot(self) -> dict:
        self._prune(time.monotonic())
        used = sum(w for _, w in self._events)
        pct = 100.0 * used / max(1, self.limit_per_min)
        cap = self.cap
        return {
            "limit_per_min": self.limit_per_min,
            "cap": cap,
            "budget_pct": self.budget_pct,
            "used": used,
            "used_pct": round(pct, 2),
            "cap_used_pct": round(100.0 * used / max(1, cap), 2),
            "available": max(0, cap - used),
            "headroom_total": max(0, self.limit_per_min - used),
            "peak_pct": round(self._peak_pct, 2),
            "server_used": self._server_used,
            "blocked_total": self._blocked_total,
            # "halted" = there is no room left under the enforced cap, so every
            # further request would be refused.  This is the operator's 95% rule.
            "halted": used >= cap,
            "halt_seconds": (time.monotonic() - self._halt_since) if self._halt_since else 0.0,
            "retry_after": round(self.retry_after(), 1),
            "per_bot": {
                b.bot_id: {
                    "spent_window": b.spent_window,
                    "spent_total": b.spent_total,
                    "blocked": b.blocked_count,
                }
                for b in self._budgets.values()
            },
        }


GOVERNOR = WeightGovernor()
