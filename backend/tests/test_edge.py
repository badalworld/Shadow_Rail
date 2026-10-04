"""
The edge layer — the win-rate / profitability upgrade.

Every default in `EdgeSettings` was chosen by measurement (see WIN_RATE.md);
these tests pin the *behaviour* of each rule so a future change cannot quietly
reverse one of them.
"""
from __future__ import annotations

from app import edge
from app.config import EdgeSettings
from app.exchange.base import TAKER_FEE
from app.util import Candle


def feats(**over) -> dict[str, float]:
    base = {"clean_ratio": 0.70, "efficiency_ratio": 0.30, "atr_pct": 0.60,
            "rail_distance_pct": 1.0, "flow_bias": 0.40, "body_ratio": 0.40,
            "volume_ratio": 1.0, "bars_since_flip": 10, "htf_bull": True}
    base.update(over)
    return base


# ─────────────────────────────────────────── the two promoted gates ───────
def test_a_clean_setup_passes():
    v = edge.evaluate_entry(feats(), EdgeSettings(), direction="LONG",
                            tp_distance_pct=0.0)
    assert v.ok, v.reasons


def test_low_trend_quality_is_rejected_and_named():
    v = edge.evaluate_entry(feats(clean_ratio=0.10), EdgeSettings(),
                            direction="LONG")
    assert not v.ok
    assert any("trend quality" in r for r in v.reasons), v.reasons


def test_extreme_volatility_is_rejected_because_fees_eat_the_move():
    # ATR 2 % of price: the round trip costs 0.10 % of price, and the measured
    # edge does not scale with volatility — so this is rent, not an edge.
    v = edge.evaluate_entry(feats(atr_pct=2.0), EdgeSettings(), direction="LONG")
    assert not v.ok
    assert any("volatility extreme" in r for r in v.reasons), v.reasons


def test_the_promoted_defaults_are_the_measured_ones():
    s = EdgeSettings()
    assert s.entry_filters_enabled is True
    assert s.min_clean_ratio == 0.30
    assert s.max_atr_pct == 0.80
    assert s.breakeven_enabled is True and s.breakeven_arm_r == 1.5
    # these were tried and NOT promoted — they must stay off
    assert s.min_efficiency_ratio == 0.0
    assert s.require_bar_confirmation is False
    assert s.require_flow_alignment is False
    assert s.adaptive_exits_enabled is False
    assert s.time_stop_enabled is False


def test_gates_can_be_switched_off_as_one_block():
    v = edge.evaluate_entry(feats(clean_ratio=0.0, atr_pct=9.0), EdgeSettings(),
                            direction="LONG", enabled=False)
    assert v.ok and not v.reasons


# ───────────────────────────────────────────────────── the cost gate ──────
def test_round_trip_cost_is_leverage_independent():
    # leverage scales the risk, never the friction: two taker fills are 0.10 %
    # of price whether the position is 1x or 20x
    assert edge.round_trip_cost_pct(1) == 2 * TAKER_FEE * 100
    assert edge.round_trip_cost_pct(20) == edge.round_trip_cost_pct(1)


def test_cost_gate_rejects_a_target_that_is_mostly_fees():
    s = EdgeSettings(cost_gate_enabled=True, min_edge_to_cost=3.0,
                     entry_filters_enabled=False)
    cost = edge.round_trip_cost_pct()             # 0.10 % of price
    assert edge.evaluate_entry(feats(), s, direction="LONG",
                               tp_distance_pct=cost * 1.5).ok is False
    assert edge.evaluate_entry(feats(), s, direction="LONG",
                               tp_distance_pct=cost * 6.0).ok is True


def test_cost_gate_uses_the_atr_proxy_when_there_is_no_target():
    s = EdgeSettings(cost_gate_enabled=True, entry_filters_enabled=False,
                     min_edge_to_cost=3.0, expected_edge_atr=0.25)
    dead = feats(atr_pct=0.10)                    # 0.025 % of expected move
    assert not edge.evaluate_entry(dead, s, direction="LONG", tp_distance_pct=0.0).ok
    lively = feats(atr_pct=2.0)                   # 0.50 % of expected move
    assert edge.evaluate_entry(lively, s, direction="LONG", tp_distance_pct=0.0).ok


def test_the_master_switch_really_turns_the_quality_gates_off():
    """A/B comparisons are worthless if 'no filters' still applies thresholds."""
    ugly = feats(clean_ratio=0.0, atr_pct=9.0, rail_distance_pct=40.0,
                 flow_bias=-1.0, body_ratio=-1.0, efficiency_ratio=0.0)
    s = EdgeSettings(entry_filters_enabled=False)
    assert edge.evaluate_entry(ugly, s, direction="LONG").ok
    s_on = EdgeSettings(entry_filters_enabled=True)
    assert not edge.evaluate_entry(ugly, s_on, direction="LONG").ok


# ─────────────────────────────────────── the break-even ratchet ───────────
def test_break_even_covers_both_commissions_plus_a_buffer():
    s = EdgeSettings(breakeven_buffer_r=0.25)
    qty, entry, risk = 100.0, 100.0, 4.0
    fees = 3.0                                     # 1.5 in, 1.5 out
    be = edge.breakeven_price(entry=entry, side="LONG", qty=qty, entry_fee=1.5,
                              exit_fee_est=1.5, risk_distance=risk, s=s)
    assert be == 100.0 + fees / qty + 0.25 * risk
    # booked P&L at that stop: gross − both fees > 0
    gross = (be - entry) * qty
    assert gross - fees > 0


def test_break_even_is_mirrored_for_shorts_and_never_loses_side():
    s = EdgeSettings(breakeven_buffer_r=0.0)
    be = edge.breakeven_price(entry=100.0, side="SHORT", qty=100.0, entry_fee=1.0,
                              exit_fee_est=1.0, risk_distance=2.0, s=s)
    assert be < 100.0
    assert edge.breakeven_price(entry=100.0, side="LONG", qty=0.0, entry_fee=1.0,
                                exit_fee_est=1.0, risk_distance=2.0, s=s) is None
    # a zero buffer with no fees would sit exactly on entry — that is not a lock
    assert edge.breakeven_price(entry=100.0, side="LONG", qty=10.0, entry_fee=0.0,
                                exit_fee_est=0.0, risk_distance=0.0, s=s) is None


def test_break_even_arms_on_r_multiples_not_on_price():
    s = EdgeSettings(breakeven_arm_r=1.5)
    sl_roi = -10.0                                  # the stop is −10 ROI points
    assert edge.should_arm_breakeven(roi=14.0, sl_roi=sl_roi, s=s) is False
    assert edge.should_arm_breakeven(roi=15.0, sl_roi=sl_roi, s=s) is True
    assert edge.should_arm_breakeven(roi=50.0, sl_roi=0.0, s=s) is False


# ─────────────────────────────────────────── sizing & throttle ────────────
def test_conviction_sizing_is_clamped_to_the_configured_band():
    s = EdgeSettings(confidence_sizing_enabled=True, size_min_fraction=0.5,
                     size_max_fraction=1.5, size_confidence_low=60.0,
                     size_confidence_high=85.0)
    assert edge.size_fraction(40.0, s) == 0.5
    assert edge.size_fraction(99.0, s) == 1.5
    assert 0.5 < edge.size_fraction(72.5, s) < 1.5
    assert edge.size_fraction(72.5, s, enabled=False) == 1.0


def test_the_throttle_only_ever_stops_new_entries():
    s = EdgeSettings(drawdown_throttle_enabled=True, max_loss_streak=8,
                     throttle_drawdown_pct=15.0)
    assert edge.throttle_active(drawdown_pct=1.0, loss_streak=2, s=s) is None
    assert "loss streak" in (edge.throttle_active(drawdown_pct=0.0,
                                                  loss_streak=8, s=s) or "")
    assert "drawdown" in (edge.throttle_active(drawdown_pct=20.0,
                                               loss_streak=0, s=s) or "")
    assert edge.throttle_active(drawdown_pct=99.0, loss_streak=99, s=s,
                                enabled=False) is None


# ───────────────────────────────────────────────────── feature maths ──────
def test_efficiency_ratio_separates_trend_from_chop():
    trend = [100.0 + i for i in range(30)]
    chop = [100.0 + (1 if i % 2 else -1) for i in range(30)]
    assert edge.efficiency_ratio(trend, 29, 24) == 1.0
    assert edge.efficiency_ratio(chop, 29, 24) < 0.1


def test_bar_body_ratio_is_signed_and_bounded():
    up = Candle(0, 100.0, 104.0, 100.0, 104.0, 10.0)
    down = Candle(0, 104.0, 104.0, 100.0, 100.0, 10.0)
    flat = Candle(0, 100.0, 100.0, 100.0, 100.0, 10.0)
    assert edge.bar_body_ratio(up) == 1.0
    assert edge.bar_body_ratio(down) == -1.0
    assert edge.bar_body_ratio(flat) == 0.0


def test_adaptive_exits_widen_the_stop_in_quiet_markets():
    s = EdgeSettings(adaptive_exits_enabled=True, sl_atr_base=1.5,
                     vol_target_atr_pct=0.45, sl_scale_min=0.8, sl_scale_max=2.2)
    quiet = edge.exit_multipliers(0.20, 0.5, s, enabled=True)
    busy = edge.exit_multipliers(1.20, 0.5, s, enabled=True)
    assert quiet[0] > s.sl_atr_base > busy[0]
    assert quiet[0] <= s.sl_atr_base * s.sl_scale_max
    assert edge.exit_multipliers(0.2, 0.5, s, enabled=False) == (1.5, 3.0)


def test_time_stop_only_fires_on_a_flat_old_trade():
    s = EdgeSettings(time_stop_enabled=True, time_stop_bars=96,
                     time_stop_min_roi=0.0)
    assert edge.time_stop_due(200, 0.5, s) is False      # profitable → keep it
    assert edge.time_stop_due(50, -1.0, s) is False      # too young
    assert edge.time_stop_due(96, -0.1, s) is True
    assert edge.time_stop_due(200, -5.0, s, enabled=False) is False
