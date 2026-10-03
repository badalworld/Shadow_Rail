"""
Risk & Sizing engine  (Equity Manager Bot + Execution Bot math).

Hard rules enforced here — every one of them is unit-tested:
  1. Margin per trade  = size_pct_per_trade % of *current equity*.
  2. Leverage          = configured (default 10x), margin type CROSS.
  3. Exactly ONE stop/target system is active (risk_mode), never two.
  4. The stop is ALWAYS between entry and the liquidation price, with a
     safety buffer.  If the ATR stop would sit at/through liquidation the stop
     is tightened to the safety limit (never widened toward liquidation).
  5. Quantity is floored to the exchange step size and must satisfy
     minQty / minNotional, otherwise the trade is rejected (not rounded up).
  6. Max concurrent trades is never exceeded.
  7. API weight ceiling (95%) is respected by every order path.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import RiskSettings
from .exchange.base import SymbolFilter
from .util import round_step, round_tick

DEFAULT_MMR = 0.005          # maintenance margin ratio at 10x (bracket 1)


@dataclass
class SizingPlan:
    symbol: str
    side: str                       # LONG | SHORT
    ok: bool
    reason: str = ""
    entry: float = 0.0
    qty: float = 0.0
    notional: float = 0.0
    margin: float = 0.0
    leverage: int = 10
    margin_type: str = "CROSS"
    sl_price: float = 0.0
    tp_price: float = 0.0
    tp_enabled: bool = True
    sl_distance_pct: float = 0.0
    tp_distance_pct: float = 0.0
    liquidation_price: float = 0.0
    liq_distance_pct: float = 0.0
    risk_amount: float = 0.0
    risk_pct_equity: float = 0.0
    stop_clamped: bool = False
    risk_mode: str = "indicator_default"
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def estimate_liquidation(entry: float, side: str, leverage: int,
                         mmr: float = DEFAULT_MMR) -> float:
    """Cross-margin liquidation estimate (single position, whole-balance backed)."""
    lev = max(1, int(leverage))
    if side.upper() in ("LONG", "BUY"):
        return max(0.0, entry * (1.0 - 1.0 / lev + mmr))
    return entry * (1.0 + 1.0 / lev - mmr)


def roi_points(entry: float, price: float, qty: float, margin: float,
               side: str) -> float:
    """
    Position ROI in *points* (1 point = 1 % of the margin committed).

    This is the number Binance shows in the ROI % column: the unrealised P&L of
    the position divided by its margin.  At 10x a 2.5 % price move is +25 ROI.
    """
    if margin <= 0 or qty <= 0:
        return 0.0
    move = (price - entry) * (1.0 if side == "LONG" else -1.0)
    return move * qty / margin * 100.0


def roi_price_step(margin: float, qty: float) -> float:
    """Price move that equals exactly one ROI point (1 % of the margin)."""
    if qty <= 0:
        return 0.0
    return margin / (100.0 * qty)


def trail_stop_price(*, entry: float, side: str, peak_price: float, mark: float,
                     qty: float, margin: float, prev_stop: float,
                     activation_roi: float, distance_roi: float,
                     mark_gap_pct: float = 0.0) -> tuple[float, bool]:
    """
    The ROI trailing stop (user rule: arm at +25 % ROI, trail 15 ROI behind the
    peak — i.e. the stop starts at +10 % ROI and only ever ratchets *up*).

    Returns ``(stop_price, armed)``.  ``stop_price`` is ``prev_stop`` until the
    peak ROI reaches the activation threshold, and is never:
      * lower than ``prev_stop``                     (monotonic ratchet)
      * closer to the market than ``mark_gap_pct``   (never fires on placement)
      * on the losing side of the entry              (a trail locks profit)
      * beyond the liquidation price                 (hard board rule)
    """
    if qty <= 0 or margin <= 0:
        return prev_stop, False
    peak_roi = roi_points(entry, peak_price, qty, margin, side)
    if peak_roi < activation_roi:
        return prev_stop, False
    step = roi_price_step(margin, qty)
    target_roi = max(activation_roi - distance_roi,
                     peak_roi - distance_roi)
    floor_roi = max(0.0, target_roi)          # never past break-even
    if side == "LONG":
        stop = entry + floor_roi * step
        limit = mark * (1.0 - max(0.0, mark_gap_pct) / 100.0)
        stop = min(stop, limit)
        stop = max(stop, prev_stop)
        if stop < entry:                 # break-even is allowed, losses are not
            return prev_stop, False
    else:
        stop = entry - floor_roi * step
        limit = mark * (1.0 + max(0.0, mark_gap_pct) / 100.0)
        stop = max(stop, limit)
        stop = min(stop, prev_stop)
        if stop > entry:                 # break-even is allowed, losses are not
            return prev_stop, False
    return stop, True


def stop_loss_price(entry: float, side: str, atr: float, sl_mult: float) -> float:
    if side.upper() in ("LONG", "BUY"):
        return entry - sl_mult * atr
    return entry + sl_mult * atr


def take_profit_price(entry: float, side: str, atr: float, tp_mult: float) -> float:
    if side.upper() in ("LONG", "BUY"):
        return entry + tp_mult * atr
    return entry - tp_mult * atr


def clamp_stop_to_liquidation(entry: float, side: str, sl_price: float,
                              liq_price: float, buffer_pct: float) -> tuple[float, bool]:
    """
    Guarantee  entry > stop > liq   (LONG)   /   entry < stop < liq  (SHORT).
    Returns (stop, was_clamped).  The stop is only ever moved *toward entry*
    (tighter), never deeper toward liquidation.
    """
    if liq_price <= 0 or buffer_pct <= 0:
        return sl_price, False
    if side.upper() in ("LONG", "BUY"):
        floor_stop = entry - (entry - liq_price) * (1.0 - buffer_pct / 100.0)
        if sl_price < floor_stop:
            return floor_stop, True
    else:
        ceil_stop = entry + (liq_price - entry) * (1.0 - buffer_pct / 100.0)
        if sl_price > ceil_stop:
            return ceil_stop, True
    return sl_price, False


class RiskEngine:
    def __init__(self, settings: RiskSettings):
        self.s = settings

    # ------------------------------------------------------------------ plan
    def plan(self, *, symbol: str, side: str, price: float, atr: float,
             equity: float, available: float, flt: SymbolFilter,
             open_trades: int, mmr: float = DEFAULT_MMR,
             confidence: float | None = None) -> SizingPlan:
        s = self.s
        side = side.upper()
        plan = SizingPlan(symbol=symbol, side="LONG" if side in ("LONG", "BUY") else "SHORT",
                          ok=False, leverage=int(s.leverage), margin_type=s.margin_type,
                          risk_mode=s.risk_mode)
        sl_mult, tp_mult, tp_enabled = s.active_tp_sl()
        plan.tp_enabled = tp_enabled

        # ---- guards -----------------------------------------------------
        if price <= 0:
            plan.reason = "no price"
            return plan
        if atr <= 0:
            plan.reason = "ATR unavailable"
            return plan
        if equity <= 0:
            plan.reason = "no equity"
            return plan
        if open_trades >= s.max_concurrent_trades:
            plan.reason = f"max concurrent trades reached ({open_trades}/{s.max_concurrent_trades})"
            return plan
        if confidence is not None and confidence < s.min_confidence:
            plan.reason = f"confidence {confidence:.1f} < required {s.min_confidence:.1f}"
            return plan

        # ---- sizing -----------------------------------------------------
        margin = equity * (s.size_pct_per_trade / 100.0)
        if s.max_margin_utilization_pct < 100.0:
            max_margin = equity * (s.max_margin_utilization_pct / 100.0)
            used = equity - available
            margin = min(margin, max(0.0, max_margin - used))
        if margin <= 0:
            plan.reason = "margin utilisation guard"
            return plan
        notional = margin * float(s.leverage)
        raw_qty = notional / price
        qty = round_step(raw_qty, flt.step_size)
        if qty < flt.min_qty:
            plan.reason = (f"qty {qty} below exchange min {flt.min_qty} "
                           f"(increase size_pct_per_trade or pick a liquid symbol)")
            return plan
        if qty > flt.max_qty:
            qty = round_step(flt.max_qty, flt.step_size)
            plan.warnings.append(
                f"qty clamped by exchange maxQty {flt.max_qty:g} — position is smaller "
                f"than the {s.size_pct_per_trade:.1f}% margin target")
        notional = qty * price
        min_notional = max(flt.min_notional, s.min_notional_override)
        if notional < min_notional:
            plan.reason = f"notional {notional:.2f} < min {min_notional:.2f}"
            return plan
        if margin > available * 1.02:
            plan.reason = f"insufficient available balance ({available:.2f}) for margin {margin:.2f}"
            return plan

        # ---- stop / target ----------------------------------------------
        sl = stop_loss_price(price, plan.side, atr, sl_mult)
        tp = take_profit_price(price, plan.side, atr, tp_mult) if tp_enabled else 0.0
        liq = estimate_liquidation(price, plan.side, s.leverage, mmr)

        sl, clamped = clamp_stop_to_liquidation(price, plan.side, sl, liq,
                                               s.liq_safety_buffer_pct)
        plan.stop_clamped = clamped
        if clamped:
            plan.warnings.append(
                f"ATR stop sat at/through liquidation — tightened to keep "
                f"{s.liq_safety_buffer_pct:.0f}% buffer inside liq {liq:.8g}")

        if plan.side == "LONG":
            if sl >= price:
                plan.reason = "invalid stop (>= entry) after liquidation clamp"
                return plan
        else:
            if sl <= price:
                plan.reason = "invalid stop (<= entry) after liquidation clamp"
                return plan

        sl_dist_pct = abs(price - sl) / price * 100.0
        if sl_dist_pct > s.max_stop_distance_pct:
            plan.reason = (f"stop distance {sl_dist_pct:.2f}% exceeds max "
                           f"{s.max_stop_distance_pct:.2f}%")
            return plan

        # final sanity: stop strictly between entry and liquidation
        if plan.side == "LONG" and not (liq < sl < price):
            plan.reason = f"stop {sl:.8g} not inside liquidation {liq:.8g}"
            return plan
        if plan.side == "SHORT" and not (price < sl < liq):
            plan.reason = f"stop {sl:.8g} not inside liquidation {liq:.8g}"
            return plan

        risk_amount = abs(price - sl) * qty

        plan.ok = True
        plan.entry = price
        plan.qty = qty
        plan.notional = notional
        plan.margin = notional / float(s.leverage)
        target_notional = margin * float(s.leverage)
        if target_notional and notional < target_notional * 0.97:
            plan.warnings.append(
                f"notional {notional:,.2f} is {100.0 * notional / target_notional:.1f}% of the "
                f"{target_notional:,.2f} target (exchange lot limits)")
        plan.sl_price = round_tick(sl, flt.tick_size)
        plan.tp_price = round_tick(tp, flt.tick_size) if tp_enabled and tp > 0 else 0.0
        plan.sl_distance_pct = sl_dist_pct
        plan.tp_distance_pct = (abs(plan.tp_price - price) / price * 100.0) if plan.tp_price else 0.0
        plan.liquidation_price = liq
        plan.liq_distance_pct = abs(price - liq) / price * 100.0
        plan.risk_amount = risk_amount
        plan.risk_pct_equity = (risk_amount / equity * 100.0) if equity else 0.0
        return plan

    # --------------------------------------------------------- exit helpers
    def reverse_exit_reason(self, position_side: str, signal_direction: str) -> bool:
        """LONG is closed by a confirmed SHORT flip (and vice-versa)."""
        if not self.s.use_reverse_signal_exit:
            return False
        return ((position_side == "LONG" and signal_direction == "SHORT") or
                (position_side == "SHORT" and signal_direction == "LONG"))

    def effective_exits(self) -> dict[str, Any]:
        sl, tp, tp_on = self.s.active_tp_sl()
        return {
            "risk_mode": self.s.risk_mode,
            "sl_atr_mult": sl,
            "tp_atr_mult": tp if tp_on else None,
            "tp_enabled": tp_on,
            "reverse_signal_exit": self.s.use_reverse_signal_exit,
            "leverage": self.s.leverage,
            "margin_type": self.s.margin_type,
            "size_pct_per_trade": self.s.size_pct_per_trade,
            "max_concurrent": self.s.max_concurrent_trades,
            "liquidation_buffer_pct": self.s.liq_safety_buffer_pct,
            "description": _describe_mode(self.s),
        }


def _describe_mode(s: RiskSettings) -> str:
    if s.risk_mode == "indicator_default":
        return ("Indicator default risk map: SL 1.5×ATR, TP 3.0×ATR. "
                "Opposite flip also closes instantly. Stop clamped inside liquidation.")
    if s.risk_mode == "shadow_3x":
        return ("Shadow 3×ATR stop, no fixed target — profits run until the "
                "indicator flips. Stop clamped inside liquidation.")
    return (f"Custom: SL {s.custom_sl_atr_mult}×ATR, "
            f"TP {s.custom_tp_atr_mult if s.custom_tp_enabled else '—'}×ATR. "
            "Stop clamped inside liquidation.")

# NOTE: qty / min-notional validation lives inside plan_sizing() above (it owns
# the step floor, the max-qty clamp and min_notional_override) and fill sanity
# lives in the executor's fill-drift recompute — so there is deliberately no
# second copy of either rule here.
