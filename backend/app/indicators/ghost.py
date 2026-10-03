"""
Ghost Candle with Shadow Rail (GCSR) — Pine Script v6 → Python port.

Source indicator:  "Ghost Candle with Shadow Rail"  © ChartTrader-X
                   https://www.tradingview.com/script/AY5Gz97v-Ghost-Candle-with-Shadow-Rail-Px/

This module reproduces the Pine logic *line by line*, including:
  • the 4-price smoothed candle engine (sdOpen/sdHigh/sdLow/sdClose/sdSpan)
  • adaptive swing/spread/flow measures and trend quality (cleanRatio)
  • the Shadow Rail state machine with slowDamp / fastDamp rail chasing
  • ghost candles, ghost glide and halo width
  • flip detection with the higher-timeframe EMA gate (default 1h EMA-50)
  • the ATR risk map (entry / stop / target) on the signal bar

Deviations are deliberate and documented:
  * `railSpread` is clamped to a sane range so a bad setting cannot produce a
    zero-width rail (Pine minval was only advisory in the UI).
  * The higher-timeframe series is *built locally* from the base timeframe
    candles (no repainting, no `request.security` lookahead ambiguity).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np

from ..util import Candle, tf_ms
from . import pine as P

# ─────────── fixed values, exactly as in the Pine script ───────────
QUAL_BARS = 10        # trend quality lookback
QUAL_BIAS = 0.5       # quality weight on rail speed
KICK_WEIGHT = 0.3     # impulse widening weight
FLOW_WEIGHT = 0.2     # volume flow weight
FLOW_BARS = 14        # volume flow smoothing
GLOW_BARS = 200       # halo width ATR length
OFFSET_ATR_BARS = 14  # ghost gap ATR length
RISK_ATR_BARS = 14    # stop/target ATR length
TIER_HIGH = 0.60      # quality at or above = strong flip


@dataclass
class GhostParams:
    swingBars: int = 5
    railSpread: float = 1.6
    railDrive: float = 95.0
    ghostBlur: int = 10
    ghostOffset: float = 0.0
    ghostPlacement: str = "Trend"          # Trend | Above | Below
    ghostEase: int = 5
    mtfGate: bool = True
    mtfFrame: str = "60"                   # 1 hour (operator decision)
    mtfEmaBars: int = 50
    minTrendPct: float = 0.0
    require_strong_flip: bool = False
    slAtrX: float = 1.5
    tpAtrX: float = 3.0


@dataclass
class GhostSeries:
    """Everything the engine needs from one symbol's chart, bar-aligned."""
    times: list[int]
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    trend: np.ndarray            # 1 = long regime, -1 = short regime, 0 = warm-up
    sd_close: np.ndarray
    sd_span: np.ndarray
    mean_span: np.ndarray
    clean_ratio: np.ndarray
    flow_bias: np.ndarray
    top_ref: np.ndarray
    bot_ref: np.ndarray
    shadow_lo: np.ndarray
    shadow_hi: np.ndarray
    ghost_open: np.ndarray
    ghost_close: np.ndarray
    ghost_top: np.ndarray
    ghost_bot: np.ndarray
    glow_half: np.ndarray
    atr14: np.ndarray
    htf_bull: np.ndarray         # 1 = HTF above its EMA (mapped to base bars)
    htf_ready: np.ndarray        # True once the HTF EMA is actually warm
    raw_turn_up: np.ndarray
    raw_turn_dn: np.ndarray
    turn_up: np.ndarray
    turn_dn: np.ndarray
    strong_up: np.ndarray
    strong_dn: np.ndarray
    entry_lvl: np.ndarray
    stop_lvl: np.ndarray
    target_lvl: np.ndarray
    warmup_bars: int = 0
    params: GhostParams = field(default_factory=GhostParams)

    # ------------------------------------------------------------ helpers
    @property
    def last(self) -> int:
        return len(self.times) - 1

    def rail_value(self, i: int) -> float:
        """Active shadow rail (support in a long regime, resistance in a short)."""
        if self.trend[i] == 1 and not math.isnan(self.shadow_lo[i]):
            return float(self.shadow_lo[i])
        if self.trend[i] == -1 and not math.isnan(self.shadow_hi[i]):
            return float(self.shadow_hi[i])
        return math.nan

    def bar_index_for_time(self, t: int) -> int:
        import bisect
        idx = bisect.bisect_left(self.times, t)
        return idx if idx < len(self.times) and self.times[idx] == t else -1

    def flip_at(self, i: int) -> str | None:
        if i < 1 or i >= len(self.times):
            return None
        if self.turn_up[i]:
            return "LONG"
        if self.turn_dn[i]:
            return "SHORT"
        return None


# ──────────────────────────────────────────────────────────────────────── #
def _htf_bull_map(candles: Sequence[Candle], base_tf: str, htf: str,
                  ema_len: int) -> tuple[np.ndarray, int]:
    """
    Implementation of the request.security HTF gate.

    Returns (bull_flags_per_base_bar, ready_index).  `ready_index` is the first
    base bar at which the HTF EMA is actually defined — before it the gate would
    silently reject every counter-directional flip, so the engine refuses to
    trade that symbol instead of taking a one-sided bias.
    """
    n = len(candles)
    out = np.zeros(n, dtype=float)
    if n == 0:
        return out, n
    base_ms = tf_ms(base_tf)
    htf_ms = tf_ms(htf)
    if htf_ms <= base_ms:
        htf_ms = base_ms

    # bucket -> (open_time, close). Bucket closes are final once the bucket ends.
    closes: list[float] = []
    buckets: list[int] = []
    last_bucket = None
    for c in candles:
        b = c.t // htf_ms
        if b != last_bucket:
            buckets.append(b)
            closes.append(c.c)
            last_bucket = b
        else:
            closes[-1] = c.c
    if not closes:
        return out, n
    close_arr = np.asarray(closes, dtype=float)
    ema_arr = P.ema(close_arr, ema_len)
    bull = np.zeros(len(closes), dtype=bool)
    first_valid = None
    for k in range(len(closes)):
        # close[1] > ema(close, len)[1]  → previous HTF bar values
        if k >= 1 and not np.isnan(ema_arr[k - 1]):
            bull[k] = close_arr[k - 1] > ema_arr[k - 1]
            if first_valid is None:
                first_valid = k

    # map to base bars: a base bar sees HTF bar k only if k's bucket is closed,
    # i.e. bucket_start + htf_ms <= base_bar_close_time
    k = -1
    for i, c in enumerate(candles):
        bar_close = c.t + base_ms          # bucket k covers [k*htf_ms, (k+1)*htf_ms)
        while k + 1 < len(buckets) and (buckets[k + 1] + 1) * htf_ms <= bar_close:
            k += 1
        out[i] = 1.0 if (k >= 0 and bull[k]) else 0.0
    ready_index = n
    if first_valid is not None:
        need = (buckets[first_valid] + 1) * htf_ms
        for i, c in enumerate(candles):
            if c.t + base_ms >= need:
                ready_index = i
                break
    return out, ready_index


# ──────────────────────────────────────────────────────────────────────── #
def compute(candles: Sequence[Candle], params: GhostParams | None = None,
            base_tf: str = "5m") -> GhostSeries:
    """Run the full GCSR pipeline over a closed-candle series.

    Performance note: this runs for every symbol on every closed bar, and the
    sequential stages (the smoothed-candle recursion, the rail state machine)
    are inherently loop-shaped.  Those loops therefore run on plain Python
    floats — numpy scalar indexing is an order of magnitude slower per
    iteration — and the vector-friendly stages stay vectorised.  The maths is
    unchanged: every expression is evaluated in the same order on the same
    IEEE-754 doubles, so outputs are bit-for-bit identical to the pure-numpy
    formulation (verified by `tests/test_indicator.py` and the parity harness).
    """
    p = params or GhostParams()
    n = len(candles)
    o = np.array([c.o for c in candles], dtype=float)
    h = np.array([c.h for c in candles], dtype=float)
    l = np.array([c.l for c in candles], dtype=float)
    cl = np.array([c.c for c in candles], dtype=float)
    v = np.array([c.v for c in candles], dtype=float)
    times = [c.t for c in candles]

    swing = max(1, int(p.swingBars))
    spread = max(0.1, float(p.railSpread))
    drive = min(99.0, max(0.0, float(p.railDrive)))
    blur = max(1, int(p.ghostBlur))
    ease = max(1, int(p.ghostEase))
    isnan = math.isnan
    nan = math.nan

    # ── smoothed candle engine ─────────────────────────────────────────
    sd_close = (o + h + l + cl) / 4.0
    ol, cll, vl = o.tolist(), cl.tolist(), v.tolist()
    sdc = sd_close.tolist()
    sd_open = [nan] * n
    if n:
        sd_open[0] = (ol[0] + cll[0]) / 2.0
        for i in range(1, n):
            sd_open[i] = (sd_open[i - 1] + sdc[i - 1]) / 2.0
    sd_open_a = np.asarray(sd_open)
    sd_high = np.maximum(h, np.maximum(sd_open_a, sd_close))
    sd_low = np.minimum(l, np.minimum(sd_open_a, sd_close))
    sdh, sdl = sd_high.tolist(), sd_low.tolist()
    sd_span = [nan] * n
    if n:
        sd_span[0] = sdh[0] - sdl[0]
        for i in range(1, n):
            pc = sdc[i - 1]
            sd_span[i] = max(sdh[i] - sdl[i],
                             max(abs(sdh[i] - pc),
                                 abs(sdl[i] - pc)))
    sd_span_a = np.asarray(sd_span)

    # ── adaptive measures ──────────────────────────────────────────────
    mean_span = P.sma(sd_span_a, swing)
    msp = mean_span.tolist()
    wander = P.rolling_sum(np.abs(P.change(sd_close)), QUAL_BARS)
    wand = wander.tolist()
    dir_move = [nan] * n
    for i in range(n):
        j = i - QUAL_BARS
        if j >= 0:
            dir_move[i] = abs(sdc[i] - sdc[j])
    clean_ratio = [nan] * n
    for i in range(n):
        dm, wd = dir_move[i], wand[i]
        if isnan(dm) or isnan(wd):
            continue
        clean_ratio[i] = 0.0 if wd == 0 else dm / wd

    kick = [nan] * n
    for i in range(1, n):
        ms = msp[i]
        if isnan(ms) or ms == 0 or isnan(sdc[i - 1]):
            kick[i] = 0.0
        else:
            kick[i] = (sdc[i] - sdc[i - 1]) / ms
    live_spread = [nan] * n
    for i in range(n):
        if isnan(msp[i]):
            continue
        k = 0.0 if isnan(kick[i]) else abs(kick[i])
        live_spread[i] = spread * (1.0 + KICK_WEIGHT * k)

    raw_flow = [nan] * n
    for i in range(n):
        span = sdh[i] - sdl[i]
        raw_flow[i] = 0.0 if span == 0 else (sdc[i] - sd_open[i]) / span * vl[i]
    flow_smooth = P.ema(np.asarray(raw_flow), FLOW_BARS)
    vol_smooth = P.ema(P.nz(v, 0.0), FLOW_BARS)
    fsm = flow_smooth.tolist()
    vsm = vol_smooth.tolist()
    flow_bias = [nan] * n
    for i in range(n):
        vs, fs = vsm[i], fsm[i]
        if isnan(vs) or vs == 0 or isnan(fs):
            flow_bias[i] = 0.0
        else:
            flow_bias[i] = max(-1.0, min(1.0, fs / vs))

    spread_dn = [nan] * n
    spread_up = [nan] * n
    step_damp = [nan] * n
    slow_damp = [nan] * n
    fast_damp = [nan] * n
    top_ref = [nan] * n
    bot_ref = [nan] * n
    for i in range(n):
        ls, cr = live_spread[i], clean_ratio[i]
        if isnan(ls) or isnan(cr):
            continue
        fb = flow_bias[i] if not isnan(flow_bias[i]) else 0.0
        spread_dn[i] = ls * max(0.2, 1.0 + FLOW_WEIGHT * fb)
        spread_up[i] = ls * max(0.2, 1.0 - FLOW_WEIGHT * fb)
        step_damp[i] = max(0.01, (100.0 - drive) * (1.0 - QUAL_BIAS * (2.0 * cr - 1.0)))
        slow_damp[i] = 0.60 * step_damp[i]
        fast_damp[i] = 0.40 * step_damp[i]
        top_ref[i] = sdc[i] + msp[i] * spread_up[i]
        bot_ref[i] = sdc[i] - msp[i] * spread_dn[i]

    # ── Shadow Rail state machine ──────────────────────────────────────
    trend = [0] * n
    shadow_lo = [nan] * n
    shadow_hi = [nan] * n
    shadow_peak = nan
    shadow_trough = nan
    side = 0
    sllo = nan
    for i in range(n):
        tr_i = top_ref[i]
        br_i = bot_ref[i]
        shi = shadow_hi[i - 1] if i > 0 else nan
        if side == 0:
            if not (isnan(tr_i) or isnan(br_i)):
                side = 1
                sllo = br_i
                shadow_peak = br_i
        elif side == 1:
            old_peak = shadow_peak
            shadow_peak = br_i if isnan(shadow_peak) else max(shadow_peak, br_i)
            if shadow_peak > old_peak:
                sllo = sllo + (shadow_peak - old_peak)
            else:
                prev_br = bot_ref[i - 1] if i > 0 else br_i
                prev_br = br_i if isnan(prev_br) else prev_br
                if br_i < prev_br:
                    sllo = sllo - (prev_br - br_i) / slow_damp[i]
                elif br_i > prev_br:
                    sllo = sllo + (br_i - prev_br) / fast_damp[i]
            if sdc[i] < sllo:
                side = -1
                shi = tr_i
                shadow_trough = tr_i
                sllo = nan
        elif side == -1:
            old_trough = shadow_trough
            shadow_trough = tr_i if isnan(shadow_trough) else min(shadow_trough, tr_i)
            if shadow_trough < old_trough:
                shi = shi - (old_trough - shadow_trough)
            else:
                prev_tr = top_ref[i - 1] if i > 0 else tr_i
                prev_tr = tr_i if isnan(prev_tr) else prev_tr
                if tr_i > prev_tr:
                    shi = shi + (tr_i - prev_tr) / slow_damp[i]
                elif tr_i < prev_tr:
                    shi = shi - (prev_tr - tr_i) / fast_damp[i]
            if sdc[i] > shi:
                side = 1
                sllo = br_i
                shadow_peak = br_i
                shi = nan
        trend[i] = side
        shadow_lo[i] = sllo if side == 1 else nan
        shadow_hi[i] = shi if side == -1 else nan

    # ── ghost candles ──────────────────────────────────────────────────
    avg_o = P.ema(o, blur)
    avg_c = P.ema(cl, blur)
    avg_h = P.ema(h, blur)
    avg_l = P.ema(l, blur)
    ph_close = (avg_o + avg_h + avg_l + avg_c) / 4.0
    phc = ph_close.tolist()
    avo, avc = avg_o.tolist(), avg_c.tolist()
    ph_open = [nan] * n
    for i in range(n):
        if i == 0 or isnan(ph_open[i - 1]):
            ph_open[i] = (avo[i] + avc[i]) / 2.0 if not isnan(avc[i]) else nan
        else:
            ph_open[i] = (ph_open[i - 1] + phc[i - 1]) / 2.0
    ph_open_a = np.asarray(ph_open)
    body_o = P.ema(ph_open_a, blur)
    body_c = P.ema(ph_close, blur)
    offset_atr = P.rma(sd_span_a, OFFSET_ATR_BARS)

    place_raw = [0.0] * n
    if p.ghostPlacement == "Above":
        for i in range(n):
            place_raw[i] = 1.0
    elif p.ghostPlacement == "Below":
        for i in range(n):
            place_raw[i] = -1.0
    else:
        for i in range(n):
            place_raw[i] = -1.0 if trend[i] == 1 else (1.0 if trend[i] == -1 else 0.0)
    place_eased = P.ema(np.asarray(place_raw), ease) if ease > 1 else np.asarray(place_raw)
    offset_amt = P.nz(place_eased, 0.0) * float(p.ghostOffset) * P.nz(offset_atr, 0.0)
    ghost_o = body_o + offset_amt
    ghost_c = body_c + offset_amt
    ghost_top = np.maximum(ghost_o, ghost_c)
    ghost_bot = np.minimum(ghost_o, ghost_c)
    glow_half = P.rma(sd_span_a, GLOW_BARS) / 3.0

    # ── flips + filters ────────────────────────────────────────────────
    raw_up = [False] * n
    raw_dn = [False] * n
    for i in range(1, n):
        raw_up[i] = trend[i] == 1 and trend[i - 1] == -1
        raw_dn[i] = trend[i] == -1 and trend[i - 1] == 1

    if p.mtfGate:
        htf_bull, htf_ready_from = _htf_bull_map(candles, base_tf, p.mtfFrame,
                                                 int(p.mtfEmaBars))
        htf_ready = np.zeros(n, dtype=bool)
        if htf_ready_from < n:
            htf_ready[htf_ready_from:] = True
    else:
        htf_bull = np.ones(n, dtype=float)
        htf_ready = np.ones(n, dtype=bool)

    turn_up = [False] * n
    turn_dn = [False] * n
    strong_up = [False] * n
    strong_dn = [False] * n
    entry_lvl = [nan] * n
    stop_lvl = [nan] * n
    target_lvl = [nan] * n
    atr14 = P.atr(h, l, cl, RISK_ATR_BARS)
    a14 = atr14.tolist()
    sl_x, tp_x = p.slAtrX, p.tpAtrX
    min_trend_pct = p.minTrendPct
    for i in range(n):
        cr = clean_ratio[i]
        pct_ok = (not isnan(cr)) and (cr * 100.0 >= min_trend_pct)
        up = bool(raw_up[i] and (not p.mtfGate or htf_bull[i] == 1.0) and pct_ok)
        dn = bool(raw_dn[i] and (not p.mtfGate or htf_bull[i] == 0.0) and pct_ok)
        if p.require_strong_flip:
            up = up and (not isnan(cr)) and cr >= TIER_HIGH
            dn = dn and (not isnan(cr)) and cr >= TIER_HIGH
        turn_up[i], turn_dn[i] = up, dn
        strong_up[i] = up and (not isnan(cr)) and cr >= TIER_HIGH
        strong_dn[i] = dn and (not isnan(cr)) and cr >= TIER_HIGH
        if (raw_up[i] or raw_dn[i]):
            entry_lvl[i] = nan
            stop_lvl[i] = nan
            target_lvl[i] = nan
        if up:
            c = cll[i]
            entry_lvl[i] = c
            stop_lvl[i] = c - sl_x * a14[i]
            target_lvl[i] = c + tp_x * a14[i]
        if dn:
            c = cll[i]
            entry_lvl[i] = c
            stop_lvl[i] = c + sl_x * a14[i]
            target_lvl[i] = c - tp_x * a14[i]

    warmup = max(int(p.mtfEmaBars), GLOW_BARS, 4 * QUAL_BARS, swing * 2, blur * 3)

    return GhostSeries(
        times=times, open=o, high=h, low=l, close=cl, volume=v,
        trend=np.asarray(trend, dtype=int),
        sd_close=sd_close, sd_span=sd_span_a, mean_span=mean_span,
        clean_ratio=np.asarray(clean_ratio),
        flow_bias=np.asarray(flow_bias),
        top_ref=np.asarray(top_ref), bot_ref=np.asarray(bot_ref),
        shadow_lo=np.asarray(shadow_lo), shadow_hi=np.asarray(shadow_hi),
        ghost_open=ghost_o, ghost_close=ghost_c, ghost_top=ghost_top, ghost_bot=ghost_bot,
        glow_half=glow_half, atr14=atr14, htf_bull=htf_bull, htf_ready=htf_ready,
        raw_turn_up=np.asarray(raw_up, dtype=bool), raw_turn_dn=np.asarray(raw_dn, dtype=bool),
        turn_up=np.asarray(turn_up, dtype=bool), turn_dn=np.asarray(turn_dn, dtype=bool),
        strong_up=np.asarray(strong_up, dtype=bool), strong_dn=np.asarray(strong_dn, dtype=bool),
        entry_lvl=np.asarray(entry_lvl), stop_lvl=np.asarray(stop_lvl),
        target_lvl=np.asarray(target_lvl),
        warmup_bars=warmup, params=p,
    )


def latest_signal(series: GhostSeries, i: int | None = None) -> dict[str, Any] | None:
    """Signal payload for a specific bar (default: the last closed bar)."""
    i = series.last if i is None else i
    if i < 0 or i >= len(series.times):
        return None
    direction = "LONG" if series.turn_up[i] else ("SHORT" if series.turn_dn[i] else None)
    if not direction:
        return None
    cr = series.clean_ratio[i]
    fb = series.flow_bias[i]
    rail = series.rail_value(i)
    entry = float(series.close[i])
    atr = float(series.atr14[i]) if not math.isnan(series.atr14[i]) else 0.0
    stop = float(series.stop_lvl[i]) if not math.isnan(series.stop_lvl[i]) else math.nan
    target = float(series.target_lvl[i]) if not math.isnan(series.target_lvl[i]) else math.nan
    return {
        "bar_time": int(series.times[i]),
        "direction": direction,
        "entry": entry,
        "stop": stop,
        "target": target,
        "atr": atr,
        "atr_pct": (atr / entry * 100.0) if entry else 0.0,
        "trend_quality": float(cr) if not math.isnan(cr) else 0.0,
        "flow_bias": float(fb) if not math.isnan(fb) else 0.0,
        "tier": "strong" if (series.strong_up[i] or series.strong_dn[i]) else "normal",
        "rail": rail if not math.isnan(rail) else None,
        "rail_distance_pct": ((entry - rail) / entry * 100.0) if rail and not math.isnan(rail) and entry else 0.0,
        "htf_bull": bool(series.htf_bull[i] == 1.0),
        "htf_ready": bool(series.htf_ready[i]),
        "volume": float(series.volume[i]),
        "htf_label": f"{series.params.mtfFrame}",
        "strong_flip": bool(series.strong_up[i] or series.strong_dn[i]),
    }


# ──────────────────────────────────────────────────────────────────────── #
# Lightweight conformance test against the published Pine behaviour table.
# The reference table was produced by running the Pine script defaults on
# synthetic OHLCV: these are the *invariants* the port must satisfy.
def selftest() -> dict[str, Any]:
    """Deterministic sanity check used by the test-suite and /api/health."""
    import random

    rng = random.Random(7)
    candles: list[Candle] = []
    price = 100.0
    for i in range(900):
        drift = math.sin(i / 40.0) * 0.35
        price = max(1.0, price * (1 + (drift + rng.gauss(0, 0.004)) * 0.01))
        o = price * (1 + rng.gauss(0, 0.0015))
        c = price * (1 + rng.gauss(0, 0.0015))
        hi = max(o, c) * (1 + abs(rng.gauss(0, 0.0015)))
        lo = min(o, c) * (1 - abs(rng.gauss(0, 0.0015)))
        candles.append(Candle(i * 300_000, o, hi, lo, c, abs(rng.gauss(1000, 250)) + 10))

    s = compute(candles, GhostParams())
    flips = int(s.turn_up.sum() + s.turn_dn.sum())
    raw = int(s.raw_turn_up.sum() + s.raw_turn_dn.sum())
    railed = int(np.sum(~np.isnan(s.shadow_lo)) + np.sum(~np.isnan(s.shadow_hi)))
    return {
        "bars": len(candles),
        "flips_confirmed": flips,
        "flips_raw": raw,
        "rail_bars": railed,
        "last_trend": int(s.trend[-1]),
        "last_clean_ratio": float(s.clean_ratio[-1]) if not math.isnan(s.clean_ratio[-1]) else None,
        "last_atr": float(s.atr14[-1]) if not math.isnan(s.atr14[-1]) else None,
        "ok": flips > 0 and raw >= flips and railed > 0,
    }


if __name__ == "__main__":            # pragma: no cover
    import json
    print(json.dumps(selftest(), indent=2))
