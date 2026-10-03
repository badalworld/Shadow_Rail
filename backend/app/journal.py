"""
Trade Manager + Equity Manager.

• Trade Manager keeps the journal: history, P&L, win-rate, executed count,
  fees paid, funding received/paid and the final net profit after both.
• Equity Manager owns the two numbers the operator sees on the main page:
  the STARTING BALANCE (locked at first successful Binance connect, never
  changes) and the CURRENT EQUITY (live), plus the per-trade margin budget.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any

from .bus import BUS
from .config import ConfigStore, STORE
from .db import DB
from .util import now_ms, pct

STARTING_BALANCE_KEY = "starting_balance"
STARTING_BALANCE_META = "starting_balance_meta"
DAY_ANCHOR_KEY = "day_anchor"


@dataclass
class EquityState:
    starting_balance: float = 0.0
    starting_locked: bool = False
    starting_source: str = ""
    starting_at: int = 0
    equity: float = 0.0
    balance: float = 0.0
    available: float = 0.0
    unrealized: float = 0.0
    margin_used: float = 0.0
    open_positions: int = 0
    released_pnl: float = 0.0          # realised net PnL (already after fees & funding)
    fees_paid: float = 0.0             # closed trades (entry + exit legs)
    open_entry_fees: float = 0.0       # entry legs of positions still open
    taker_fee_rate: float = 0.0005     # Binance USDT-M taker (0.05%)
    funding_paid: float = 0.0          # positive = net paid, negative = net received
    daily_pnl: float = 0.0
    day_start_equity: float = 0.0
    peak_equity: float = 0.0
    drawdown_pct: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        growth = self.equity - self.starting_balance
        return {
            "starting_balance": round(self.starting_balance, 4),
            "starting_locked": self.starting_locked,
            "starting_source": self.starting_source,
            "starting_at": self.starting_at,
            "equity": round(self.equity, 4),
            "balance": round(self.balance, 4),
            "available": round(self.available, 4),
            "unrealized": round(self.unrealized, 4),
            "margin_used": round(self.margin_used, 4),
            "open_positions": self.open_positions,
            "released_pnl": round(self.released_pnl, 4),
            "fees_paid": round(self.fees_paid, 4),
            "fees_paid_total": round(self.fees_paid + self.open_entry_fees, 4),
            "open_entry_fees": round(self.open_entry_fees, 4),
            "funding_paid": round(self.funding_paid, 4),
            "funding_net": round(-self.funding_paid, 4),
            "daily_pnl": round(self.daily_pnl, 4),
            "day_start_equity": round(self.day_start_equity, 4),
            "peak_equity": round(self.peak_equity, 4),
            "drawdown_pct": round(self.drawdown_pct, 2),
            "growth_pct": round(pct(self.equity, self.starting_balance), 2),
            "growth_abs": round(growth, 4),
            "net_after_costs": round(self.released_pnl - self.open_entry_fees, 4),
            "equity_bridge": round(self.starting_balance + self.released_pnl
                                   + self.unrealized - self.open_entry_fees, 4),
        }


class Journal:
    def __init__(self, store: ConfigStore = STORE):
        self.store = store
        self.state = EquityState()
        self._last_point_ms = 0
        self._symbol_stats: dict[str, dict] = {}
        self._stats_cache: dict[str, Any] = {}
        self._stats_cache_ms = 0

    # =================================================== equity manager
    async def ensure_starting_balance(self, equity: float, source: str) -> bool:
        """
        Lock the starting balance at the FIRST connect that returns a real
        balance.  Never overwritten afterwards (Main Page rule).
        """
        if equity <= 0:
            return False
        existing = await DB.kv_get(STARTING_BALANCE_KEY)
        if existing:
            self.state.starting_balance = float(existing)
            self.state.starting_locked = True
            meta = await DB.kv_get(STARTING_BALANCE_META, {}) or {}
            self.state.starting_source = meta.get("source", "")
            self.state.starting_at = int(meta.get("at", 0))
            return False
        await DB.kv_set(STARTING_BALANCE_KEY, float(equity))
        meta = {"source": source, "at": now_ms(), "equity": float(equity)}
        await DB.kv_set(STARTING_BALANCE_META, meta)
        self.state.starting_balance = float(equity)
        self.state.starting_locked = True
        self.state.starting_source = source
        self.state.starting_at = int(meta["at"])
        BUS.publish("equity.starting_locked", {"amount": float(equity), "source": source,
                                               "at": meta["at"]})
        return True

    async def load_locked(self) -> bool:
        """
        Re-read the locked starting balance from storage.

        Called at boot so the Main Page shows the locked figure immediately --
        before the first account fetch (which may take a moment, or fail).
        """
        existing = await DB.kv_get(STARTING_BALANCE_KEY)
        if not existing:
            return False
        self.state.starting_balance = float(existing)
        self.state.starting_locked = True
        meta = await DB.kv_get(STARTING_BALANCE_META, {}) or {}
        self.state.starting_source = meta.get("source", "")
        self.state.starting_at = int(meta.get("at", 0))
        return True

    async def reset_starting_balance(self) -> None:
        """Operator-initiated only (Settings → danger zone)."""
        await DB.kv_set(STARTING_BALANCE_KEY, None)
        self.state.starting_locked = False
        BUS.publish("equity.starting_reset", {"at": now_ms()})

    async def daily_anchor(self, equity: float) -> float:
        anchor = await DB.kv_get(DAY_ANCHOR_KEY, {}) or {}
        day = time.strftime("%Y-%m-%d", time.gmtime())
        if anchor.get("day") != day:
            anchor = {"day": day, "equity": float(equity)}
            await DB.kv_set(DAY_ANCHOR_KEY, anchor)
            self.state.day_start_equity = float(equity)
        else:
            self.state.day_start_equity = float(anchor.get("equity", equity))
        return self.state.day_start_equity

    async def update(self, account, persist: bool = True) -> EquityState:
        s = self.state
        s.equity = account.total_margin_balance or account.total_wallet_balance
        s.balance = account.total_wallet_balance
        s.available = account.available_balance
        s.unrealized = account.total_unrealized_pnl
        s.margin_used = account.total_initial_margin
        s.open_positions = account.open_count
        # the entry leg of an open position is already paid — count it so the
        # "fees paid" tile and the equity bridge always reconcile
        s.open_entry_fees = sum(abs(p.qty) * p.entry_price * s.taker_fee_rate
                                for p in account.positions if abs(p.qty) > 0)
        s.peak_equity = max(s.peak_equity or s.equity, s.equity)
        s.drawdown_pct = ((s.peak_equity - s.equity) / s.peak_equity * 100.0) if s.peak_equity else 0.0
        await self.ensure_starting_balance(s.equity, account.source)
        await self.daily_anchor(s.equity)
        s.daily_pnl = s.equity - s.day_start_equity
        if persist and (now_ms() - self._last_point_ms) > 20_000:
            self._last_point_ms = now_ms()
            await DB.add_equity_point(s.equity, s.balance, s.unrealized, s.margin_used,
                                      s.open_positions, "sync")
        return s

    def margin_budget(self) -> float:
        """USD margin the Equity Manager releases for the next trade."""
        return self.state.equity * (self.store.cfg.risk.size_pct_per_trade / 100.0)

    def can_open_more(self, open_count: int) -> bool:
        return open_count < self.store.cfg.risk.max_concurrent_trades

    def daily_drawdown_breached(self) -> bool:
        limit = self.store.cfg.risk.daily_drawdown_stop_pct
        if limit <= 0 or not self.state.day_start_equity:
            return False
        return self.state.daily_pnl <= -(self.state.day_start_equity * limit / 100.0)

    # ==================================================== trade manager
    async def refresh_stats(self, force: bool = False) -> dict[str, Any]:
        if not force and (now_ms() - self._stats_cache_ms) < 5_000 and self._stats_cache:
            return self._stats_cache
        stats = await DB.stats_summary()
        self._stats_cache = stats
        self._stats_cache_ms = now_ms()
        self.state.released_pnl = float(stats.get("net_pnl", 0.0))
        self.state.fees_paid = float(stats.get("fees_paid", 0.0))
        self.state.funding_paid = float(stats.get("funding_paid", 0.0))
        return stats

    async def symbol_stats(self, force: bool = False) -> dict[str, dict]:
        if not force and self._symbol_stats:
            return self._symbol_stats
        rows = await DB.all_closed_for_stats()
        agg: dict[str, dict] = {}
        for r in rows:
            sym = r.get("symbol") or "?"
            a = agg.setdefault(sym, {"trades": 0, "wins": 0, "net": 0.0})
            a["trades"] += 1
            net = float(r.get("net_pnl") or 0.0)
            a["net"] += net
            if net > 0:
                a["wins"] += 1
        for sym, a in agg.items():
            a["win_rate"] = (a["wins"] / a["trades"] * 100.0) if a["trades"] else 0.0
            a["avg_net"] = a["net"] / a["trades"] if a["trades"] else 0.0
        self._symbol_stats = agg
        return agg

    async def record_close(self, trade: dict) -> dict[str, Any]:
        """Called by the Trade Manager when a position closes (win or loss)."""
        await DB.add_trade_event(int(trade["id"]), "closed", {
            "reason": trade.get("close_reason"), "net": trade.get("net_pnl")})
        self._symbol_stats = {}
        stats = await self.refresh_stats(force=True)
        net = float(trade.get("net_pnl") or 0.0)
        win = net > 0
        payload = {
            "trade": {k: trade.get(k) for k in (
                "id", "symbol", "side", "net_pnl", "gross_pnl", "fee_paid", "funding_paid",
                "close_reason", "entry_price", "exit_price", "qty", "leverage", "r_multiple",
                "opened_at", "closed_at", "analyst_id", "scanner_id", "exec_bot_id")},
            "win": win,
            "stats": stats,
        }
        BUS.publish("trade.win" if win else "trade.loss", payload)
        return stats

    async def feature_rows(self) -> list[dict]:
        """Extract (features → outcome) rows from the journal for model training."""
        import json
        cur = await DB.conn.execute(
            "SELECT notes, net_pnl, signal_confidence FROM trades "
            "WHERE status='closed' AND notes IS NOT NULL")
        rows = await cur.fetchall()
        await cur.close()
        out: list[dict] = []
        for r in rows:
            try:
                notes = json.loads(r["notes"]) if isinstance(r["notes"], str) else (r["notes"] or {})
            except (TypeError, ValueError):
                continue
            feats = notes.get("features") if isinstance(notes, dict) else None
            if feats:
                out.append({"features": feats, "win": 1 if (r["net_pnl"] or 0) > 0 else 0})
        return out

    # ------------------------------------------------------------ snapshots
    async def equity_series(self, limit: int = 1200) -> list[dict]:
        return await DB.equity_series(limit=limit)

    async def pnl_by_day(self, days: int = 30) -> list[dict]:
        start = now_ms() - days * 86_400_000
        rows = await DB.closed_between(start)
        buckets: dict[str, dict] = {}
        for r in rows:
            day = time.strftime("%Y-%m-%d", time.gmtime((r.get("closed_at") or 0) / 1000))
            b = buckets.setdefault(day, {"day": day, "net": 0.0, "trades": 0, "wins": 0,
                                         "fees": 0.0, "funding": 0.0})
            net = float(r.get("net_pnl") or 0.0)
            b["net"] += net
            b["trades"] += 1
            b["wins"] += 1 if net > 0 else 0
            b["fees"] += abs(float(r.get("fee_paid") or 0.0))
            b["funding"] += float(r.get("funding_paid") or 0.0)
        return [buckets[k] for k in sorted(buckets)]

    async def cumulative_pnl(self, limit: int = 500) -> list[dict]:
        rows = await DB.closed_trades(limit=limit)
        rows = sorted(rows, key=lambda r: r.get("closed_at") or 0)
        out, run = [], 0.0
        for r in rows:
            run += float(r.get("net_pnl") or 0.0)
            out.append({"ts": r.get("closed_at"), "cum": run,
                        "net": float(r.get("net_pnl") or 0.0), "symbol": r.get("symbol")})
        return out


JOURNAL = Journal()
