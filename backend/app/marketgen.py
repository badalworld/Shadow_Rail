"""
Synthetic market model — one definition, used by both the live simulator and
the offline backtest lab.

The model is deliberately *not* a pure random walk: a pure random walk punishes
every trend-following system equally and would make the engine (and any
backtest of it) meaningless.  Real crypto has three properties the Shadow Rail
state machine is built to exploit, and this generator reproduces all three:

  * **volatility clustering** — a regime multiplier that switches rarely and
    persists (GARCH-like),
  * **momentum persistence** — an AR(1) drift term with a ~25-bar half life,
  * **shocks** — rare impulse jumps so fat tails exist.

`sim.py` steps this model bar-by-bar for the live dashboard; `backtest.py` uses
it to build a deterministic candle panel that can be replayed thousands of
times.  Both therefore measure the *same* market.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from .util import Candle, tf_ms

# ── model constants (kept verbatim from the simulator so behaviour is identical)
REGIME_CHOICES = (0.7, 1.0, 1.0, 1.35, 1.8, 2.4)
REGIME_SWITCH_P = 0.0015          # ~1 switch per 670 bars
DRIFT_DECAY = 0.97                # AR(1) coefficient (≈25-bar half life)
DRIFT_INNOVATION = 0.55
DRIFT_SHOCK_P = 0.002
DRIFT_SHOCK_RANGE = (0.6, 2.2)
DRIFT_CLAMP = 2.2
DRIFT_SCALE = 0.55                # drift → per-bar return, as a fraction of sigma
WICK_SCALE = 0.8
VOLUME_BASE = 0.4


@dataclass
class MarketState:
    """Per-symbol state the bar generator advances."""

    vol: float                    # per-bar sigma, as a fraction of price
    drift: float = 0.0            # AR(1) momentum term, in sigma units
    regime: float = 1.0           # volatility multiplier
    volume: float = 1.0           # base units traded per bar
    momentum: float = 1.0         # 0 = pure random walk (research control)


def init_drift(rng: random.Random) -> float:
    """Starting momentum for a freshly seeded symbol."""
    return rng.gauss(0, 1) * 0.6


def advance(rng: random.Random, st: MarketState, prev_close: float) -> Candle:
    """
    One bar.  Mutates `st.drift` / `st.regime` (they carry across bars) and
    returns a candle with ``open_time = 0``; callers stamp the real timestamp.
    """
    if rng.random() < REGIME_SWITCH_P:
        st.regime = rng.choice(REGIME_CHOICES)

    st.drift = DRIFT_DECAY * st.drift + 0.03 * rng.gauss(0, 1) * DRIFT_INNOVATION
    if rng.random() < DRIFT_SHOCK_P:
        st.drift += rng.choice([-1, 1]) * rng.uniform(*DRIFT_SHOCK_RANGE)
    st.drift = max(-DRIFT_CLAMP, min(DRIFT_CLAMP, st.drift))

    step = st.vol * st.regime * rng.gauss(0, 1)
    drift = st.drift * st.vol * DRIFT_SCALE * st.momentum
    o = prev_close
    c = max(prev_close * 1e-6, prev_close * (1.0 + drift + step))

    spread = st.vol * st.regime * WICK_SCALE
    wick_hi = abs(rng.gauss(0, 1)) * spread * prev_close
    wick_lo = abs(rng.gauss(0, 1)) * spread * prev_close
    hi = max(o, c) + wick_hi
    lo = max(1e-12, min(o, c) - wick_lo)
    vol = st.volume * (VOLUME_BASE + abs(rng.gauss(0, 1)))
    return Candle(0, o, hi, lo, c, vol)


@dataclass
class Panel:
    """A deterministic multi-symbol candle panel the backtester can replay."""

    candles: dict[str, list[Candle]] = field(default_factory=dict)
    interval: str = "5m"
    seed: int = 0
    start_ms: int = 0

    @property
    def symbols(self) -> list[str]:
        return list(self.candles)

    @property
    def bars(self) -> int:
        return max((len(v) for v in self.candles.values()), default=0)


def generate_panel(rows: list[tuple[str, float, float]], bars: int, *,
                   seed: int = 20261003, interval: str = "5m",
                   start_ms: int = 1_700_000_000_000,
                   warmup_drift: bool = True, momentum: float = 1.0) -> Panel:
    """
    Build ``bars`` closed candles for every ``(symbol, start_price, sigma)`` row.

    Deterministic: the same ``seed`` + ``rows`` always yields the same panel, so
    every configuration in a sweep is scored on identical market data.

    ``momentum`` scales the AR(1) trend term only.  ``1.0`` is the market the
    simulator ships; ``0.0`` turns that term off and produces a pure
    volatility-clustered random walk — the control the lab uses to prove a
    measured edge is not an artefact of the generator.
    """
    rng = random.Random(seed)
    step = tf_ms(interval)
    out: dict[str, list[Candle]] = {}
    for symbol, price, sigma in rows:
        st = MarketState(vol=sigma, drift=init_drift(rng) if warmup_drift else 0.0,
                         volume=1.0e6 / max(price, 1e-12), momentum=momentum)
        series: list[Candle] = []
        last = price
        for i in range(bars):
            c = advance(rng, st, last)
            series.append(Candle(start_ms + i * step, c.o, c.h, c.l, c.c, c.v))
            last = c.c
        out[symbol] = series
    return Panel(candles=out, interval=interval, seed=seed, start_ms=start_ms)
