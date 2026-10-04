"""
Strategy policy layer — the *win-rate pack*.

Everything in here is a pure function of (signal snapshot, settings), so it is
unit-testable in isolation and reusable by the backtester (scripts/backtest.py)
exactly the way the live engine uses it.  The indicator (indicators/ghost.py)
stays a faithful Pine port; the *decisions* about which flips deserve money
live here:

  entry_gate()          — confluence filters a confirmed flip must pass
  btc_regime_allows()   — alts only follow BTC's regime when it clearly disagrees
  sizing_multiplier()   — conviction-weighted margin (anti-martingale by score)
  stall_exit_due()      — a trade that never worked dies on its own schedule
  loss_streak_cooldown()— a symbol that keeps hurting us waits longer per retry
  blacklist_entry()     — repeat offenders are skipped until the streak is proven
                          different (journal-driven)

Win-rate maximisation is about *subtracting* trades: one extra filter that
removes mediocre setups beats one new setup type.  Every gate below defaults to
on and each can be turned off in Settings → Risk / TP-SL.
"""
from __future__ import annotations

import math
from typing import Any

# ───────────────────────────── entry gates ────────────────────────────── #
def _flip_sign(direction: str) -> float:
    return 1.0 if direction == "LONG" else -1.0


def entry_gate(sig: dict[str, Any], r: Any, *, mtf_gate_enabled: bool = True,
               btc_aligned: bool | None = None) -> tuple[bool, str]:
    """
    Decide whether a confirmed GCSR flip deserves an entry.

    ``sig`` is the dict produced by ``ghost.latest_signal``; ``r`` is the
    active ``RiskSettings``.  Returns ``(allowed, reason)`` — the reason is
    surfaced verbatim in the workflow log so a rejected flip is never a
    mystery.  A gate whose input is unavailable *passes* (fail-open on data,
    fail-closed on policy): the HTF gate for example needs the 1h history to
    be warm, and before that the engine already refuses the symbol wholesale.
    """
    d = _flip_sign(sig.get("direction", "LONG"))

    # 1 · participation — the flip bar must come with real volume behind it
    if getattr(r, "require_volume_confirm", False):
        vs = sig.get("vol_surge")
        if vs is not None and not (isinstance(vs, float) and math.isnan(vs)):
            if float(vs) < float(getattr(r, "vol_confirm_ratio", 0.9)):
                return False, (f"volume confirm: flip bar only {float(vs):.2f}× "
                               f"the 20-bar average (need ≥ {r.vol_confirm_ratio:.2f}×)")

    # 2 · commitment — the flip bar must close near its extreme (not indecision)
    if getattr(r, "require_body_confirm", False):
        body = sig.get("body_strength")
        if body is not None and not (isinstance(body, float) and math.isnan(body)):
            signed = float(body) * d
            if signed < float(getattr(r, "body_min", 0.30)):
                return False, (f"body confirm: close is only {signed:+.2f} of the bar "
                               f"range in trade direction (need ≥ {r.body_min:.2f})")

    # 3 · the 1h tide must not be flat against us (EMA-50 slope confluence)
    if getattr(r, "require_htf_slope", False) and mtf_gate_enabled:
        up = sig.get("htf_ema_up")
        if up is not None and sig.get("htf_ready", True):
            want_up = d > 0
            if bool(up) != want_up:
                return False, ("HTF slope: 1h EMA-50 points the other way — "
                               "flip is likely an exhaustion whipsaw")

    # 4 · volatility band — dead chop and event chaos both lose; only trade
    #     the middle where ATR-sized stops actually pay
    if getattr(r, "require_volatility_band", False):
        atr_pct = float(sig.get("atr_pct") or 0.0)
        if atr_pct and (atr_pct < float(r.atr_floor_pct) or atr_pct > float(r.atr_cap_pct)):
            return False, (f"volatility band: ATR {atr_pct:.2f}% outside "
                           f"[{r.atr_floor_pct:.2f}, {r.atr_cap_pct:.2f}]% of price")

    # 5 · never chase — an entry far from the rail is late, and late is how
    #     trend systems die (mean reversion eats the extended entries)
    if getattr(r, "enforce_rail_extension", False):
        ext = abs(float(sig.get("rail_distance_pct") or 0.0))
        cap = float(r.max_rail_extension_pct)
        if cap > 0 and ext > cap:
            return False, (f"rail extension: entry sits {ext:.2f}% from the shadow rail "
                           f"(max {cap:.2f}%) — too late to be a fresh flip")

    # 6 · market regime — in crypto perps an alt long against a clearly bearish
    #     BTC is a low-win-rate trade no matter how pretty the flip looks
    if getattr(r, "require_btc_alignment", False) and btc_aligned is False:
        return False, ("BTC regime: leader is firmly against this direction — "
                       "alt flip skipped")

    return True, ""


def btc_regime_allows(btc_trend: int, btc_htf_bull: bool, direction: str) -> bool | None:
    """
    ``None`` = no opinion (pass), ``True/False`` = allow/reject.

    Reject only on a *clear* disagreement: both the GCSR regime and the 1h EMA
    gate of BTCUSDT point against us.  A split BTC tape passes — we trade our
    own chart, the leader only filters the obvious head-winds.
    """
    if btc_trend not in (1, -1):
        return None
    against = (btc_trend == -1 and not btc_htf_bull) if direction == "LONG" \
        else (btc_trend == 1 and btc_htf_bull)
    return not against


# ───────────────────────────── sizing by conviction ────────────────────── #
SIZING_TIERS = (            # (confidence floor, margin multiplier)
    (90.0, 1.15),           # emphatic A+ book → slightly bigger
    (78.0, 1.00),           # the normal base size
    (0.0, 0.80),            # barely-passing setups → deliberately smaller
)


def sizing_multiplier(confidence: float | None) -> float:
    """
    Bet bigger only when the analyst team is emphatic — never more than +15 %
    over the configured base size, and shrink to 0.8× for borderline setups.
    This tilts the equity curve toward the A-grade book without raising gross
    risk beyond what the operator sized for.
    """
    if confidence is None:
        return 1.0
    for floor, mult in SIZING_TIERS:
        if float(confidence) >= floor:
            return mult
    return 1.0


# ───────────────────────────── trade management ────────────────────────── #
def stall_exit_due(*, opened_at_ms: int, now_ms: int, entry: float, peak_price: float,
                   qty: float, margin: float, side: str, minutes: float,
                   min_peak_roi: float, roi_points_fn) -> bool:
    """
    A momentum flip that has not *done anything* after ``minutes`` is a missed
    thesis, not a trade to marry: close it.  Only positions that never even
    touched ``min_peak_roi`` are cut — anything that showed promise keeps its
    stop / trail / reverse-signal exits.
    """
    if minutes <= 0 or qty <= 0 or margin <= 0:
        return False
    age_ms = int(now_ms) - int(opened_at_ms)
    if age_ms < int(minutes) * 60_000:
        return False
    peak_roi = roi_points_fn(entry, peak_price, qty, margin, side)
    return peak_roi < float(min_peak_roi)


def loss_streak_cooldown_s(streak: int, base_s: float) -> float:
    """2 losses in a row → double wait, 3 → triple, capped at 4×. Tilt-proof."""
    if streak <= 1:
        return base_s
    return base_s * min(4.0, float(streak))


def blacklist_entry(*, trades: int, win_rate: float, net: float,
                    min_trades: int, max_winrate: float) -> bool:
    """
    Journal-driven anti-tilt: a symbol we have repeatedly traded and repeatedly
    lost on is *not* our edge — it is our leak.  Skip its flips until enough
    fresh evidence (handled by the expiry in the engine) says otherwise.
    """
    if min_trades <= 0 or trades < min_trades:
        return False
    return win_rate < float(max_winrate) and net < 0
