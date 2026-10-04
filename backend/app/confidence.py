"""
Market Analyst confidence model (v2 — win-rate pack).

The 10 analyst bots score every scanner opportunity 0–100 using a transparent,
auditable blend of structural factors — with an optional online logistic model
that learns from the engine's own closed-trade journal (enabled automatically
once there is enough history, or forced on/off in Settings).

Factors (weights sum to 1.0):
   trend_quality   0.21  — Shadow Rail cleanRatio (dirMove / wanderMove)
   htf_alignment   0.13  — 1h EMA-50 regime agreement with the flip
   htf_momentum    0.09  — 1h EMA-50 *slope* agrees too (not just position)
   volume_flow     0.09  — normalised volume flow bias in the trade direction
   candle_confirm  0.09  — flip-bar participation (volume surge) + commitment
   volatility_fit  0.09  — ATR% inside the profitable band (not dead, not insane)
   rail_position   0.08  — distance from the shadow rail (not chasing an extended move)
   market_regime   0.08  — BTCUSDT regime agreement for alt flips
   signal_tier     0.06  — "strong" flips (cleanRatio ≥ 0.60)
   symbol_history  0.08  — realised win-rate of this symbol in our own journal

Every factor is returned with the score so the dashboard can show *why*.
Confluence factors fall back to a neutral 60 when their measurement is
unavailable (warm-up / simulator), so missing data can neither fake quality
nor veto an otherwise A-grade setup.

Learning loop: the logistic model is trained on the *scored factor rows* the
journal stores with every trade (`notes.factors`) — the same 0–100 vectors the
scorer produces — so what is learned is exactly what is scored.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from typing import Any

import numpy as np

WEIGHTS = {
    "trend_quality": 0.21,
    "htf_alignment": 0.13,
    "htf_momentum": 0.09,
    "volume_flow": 0.09,
    "candle_confirm": 0.09,
    "volatility_fit": 0.09,
    "rail_position": 0.08,
    "market_regime": 0.08,
    "signal_tier": 0.06,
    "symbol_history": 0.08,
}
MODEL_FORMAT = 2      # bump when the factor set changes; stale models are dropped

NEUTRAL = 60.0        # score for a factor whose input is missing

# ATR% band that historically favours a 5m flip system
VOL_SWEET_LO = 0.30     # % of price
VOL_SWEET_HI = 1.80


@dataclass
class ConfidenceResult:
    score: float
    factors: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    model: str = "heuristic"

    def as_dict(self) -> dict[str, Any]:
        return {"score": round(self.score, 2),
                "factors": {k: round(v, 2) for k, v in self.factors.items()},
                "notes": self.notes, "model": self.model}


def _band_score(value: float, lo: float, hi: float, softness: float = 0.35) -> float:
    """1.0 inside [lo,hi], decaying outside — smooth, no cliffs."""
    if lo <= value <= hi:
        return 1.0
    if value < lo:
        span = max(1e-9, lo * softness)
        return max(0.0, 1.0 - (lo - value) / span)
    span = max(1e-9, hi * softness)
    return max(0.0, 1.0 - (value - hi) / span)


class ConfidenceModel:
    """Heuristic scorer + optional logistic refinement trained on the journal."""

    def __init__(self, model_path: str | None = None):
        self.model_path = model_path or os.path.join(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))), "data", "confidence_model.json")
        self.coef: np.ndarray | None = None
        self.intercept: float = 0.0
        self.samples: int = 0
        self.enabled: bool = True
        self._load()

    # ------------------------------------------------------------ training
    def _load(self) -> None:
        try:
            with open(self.model_path, "r") as fh:
                data = json.load(fh)
            # a model trained on a different factor set must never be reused —
            # its coefficients would silently score the wrong features
            if int(data.get("format", 1)) != MODEL_FORMAT \
                    or list(data.get("keys", [])) != list(WEIGHTS.keys()):
                self.coef, self.intercept, self.samples = None, 0.0, 0
                return
            self.coef = np.asarray(data.get("coef", []), dtype=float)
            self.intercept = float(data.get("intercept", 0.0))
            self.samples = int(data.get("samples", 0))
        except (OSError, ValueError, TypeError):
            self.coef = None
            self.intercept = 0.0

    def train(self, rows: list[dict]) -> dict[str, Any]:
        """
        rows: [{'factors': {name: 0..100}, 'win': 0/1}, …] from the closed-trade
        journal ('features' accepted for legacy rows).  Plain gradient-descent
        logistic regression — no external deps, and the coefficients are saved
        so the dashboard can display them.
        """
        def row_vector(r: dict) -> list[float]:
            src = r.get("factors") or r.get("features") or {}
            return [min(1.0, max(0.0, float(src.get(k, NEUTRAL)) / 100.0))
                    for k in WEIGHTS]

        usable = [r for r in rows if (r.get("factors") or r.get("features"))]
        self.samples = len(usable)
        if len(usable) < 60:
            self.coef = None
            return {"trained": False, "samples": self.samples,
                    "reason": "need at least 60 closed trades"}
        keys = list(WEIGHTS.keys())
        X = np.asarray([row_vector(r) for r in usable])
        y = np.asarray([1.0 if r.get("win") else 0.0 for r in usable])
        w = np.zeros(len(keys))
        b = 0.0
        lr = 0.35
        l2 = 1e-3
        for _ in range(600):
            z = X @ w + b
            p = 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))
            grad = X.T @ (p - y) / len(y) + l2 * w
            grad_b = float(np.mean(p - y))
            w -= lr * grad
            b -= lr * grad_b
        self.coef, self.intercept = w, b
        try:
            os.makedirs(os.path.dirname(self.model_path), exist_ok=True)
            with open(self.model_path, "w") as fh:
                json.dump({"coef": w.tolist(), "intercept": b, "samples": self.samples,
                           "keys": keys, "format": MODEL_FORMAT}, fh, indent=2)
        except OSError:
            pass
        acc = float(np.mean((p > 0.5) == (y > 0.5)))
        return {"trained": True, "samples": self.samples,
                "train_accuracy": round(acc, 4),
                "coef": {k: round(float(v), 3) for k, v in zip(keys, w)},
                "intercept": round(b, 3)}

    # ------------------------------------------------------------- scoring
    def score(self, feature: dict[str, Any], symbol_stats: dict[str, Any] | None = None,
              analyst_variance: float = 0.0) -> ConfidenceResult:
        f: dict[str, float] = {}
        notes: list[str] = []

        cr = float(feature.get("trend_quality") or 0.0)          # 0..1
        f["trend_quality"] = min(100.0, max(0.0, cr * 118.0))    # 0.85 → 100

        htf_bull = feature.get("htf_bull")
        direction = feature.get("direction", "LONG")
        aligned = (direction == "LONG" and htf_bull) or (direction == "SHORT" and not htf_bull)
        f["htf_alignment"] = 100.0 if aligned else 25.0
        if not aligned:
            notes.append("counter-trend vs 1h EMA-50 (HTF gate should have filtered)")

        # HTF slope confluence: the 1h tide should be *moving* our way
        up = feature.get("htf_ema_up")
        if up is None or not feature.get("htf_ready", True):
            f["htf_momentum"] = NEUTRAL
        else:
            want_up = direction == "LONG"
            f["htf_momentum"] = 100.0 if bool(up) == want_up else 15.0
            if bool(up) != want_up:
                notes.append("1h EMA-50 slope points against the flip")

        bias = float(feature.get("flow_bias") or 0.0)            # -1..1
        flow_dir = bias if direction == "LONG" else -bias
        f["volume_flow"] = min(100.0, max(0.0, 50.0 + flow_dir * 50.0))

        # flip-bar participation + commitment (the two cheapest fake-out filters)
        parts: list[float] = []
        vs = feature.get("vol_surge")
        if vs is not None:
            parts.append(min(100.0, max(0.0, 40.0 + (float(vs) - 0.8) * 75.0)))
        body = feature.get("body_strength")
        if body is not None:
            signed = float(body) * (1.0 if direction == "LONG" else -1.0)
            parts.append(min(100.0, max(0.0, signed / 0.6 * 100.0)))
        f["candle_confirm"] = sum(parts) / len(parts) if parts else NEUTRAL

        atr_pct = float(feature.get("atr_pct") or 0.0)
        f["volatility_fit"] = 100.0 * _band_score(atr_pct, VOL_SWEET_LO, VOL_SWEET_HI)
        if atr_pct > VOL_SWEET_HI * 1.6:
            notes.append(f"volatility very high (ATR {atr_pct:.2f}%)")

        rail_dist = abs(float(feature.get("rail_distance_pct") or 0.0))
        # 0–1.5% from the rail is ideal; >3% means chasing
        f["rail_position"] = 100.0 * (1.0 if rail_dist <= 1.5 else
                                      max(0.0, 1.0 - (rail_dist - 1.5) / 2.5))

        f["signal_tier"] = 100.0 if feature.get("tier") == "strong" else 60.0

        # market regime (leader coin) — None means "no opinion"
        btc = feature.get("btc_aligned")
        f["market_regime"] = NEUTRAL if btc is None else (100.0 if btc else 25.0)
        if btc is False:
            notes.append("BTCUSDT regime runs against this alt flip")

        hist = 50.0
        if symbol_stats and symbol_stats.get("trades", 0) >= 3:
            wr = float(symbol_stats.get("win_rate", 0.0))
            hist = min(100.0, max(0.0, wr))
            if wr < 35.0:
                notes.append(f"symbol win-rate {wr:.0f}% over {symbol_stats['trades']} trades")
        f["symbol_history"] = hist

        base = sum(WEIGHTS[k] * f[k] for k in WEIGHTS)
        model_kind = "heuristic"
        if self.enabled and self.coef is not None and self.samples >= 60:
            x = np.asarray([f[k] / 100.0 for k in WEIGHTS])
            z = float(x @ self.coef + self.intercept)
            pmodel = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))
            base = 0.55 * base + 0.45 * (pmodel * 100.0)
            model_kind = "heuristic+logistic"
            notes.append(f"logistic model ({self.samples} trades) adjusted the score")

        if analyst_variance:
            base = min(100.0, base + analyst_variance)
        return ConfidenceResult(score=min(100.0, max(0.0, base)), factors=f,
                                notes=notes, model=model_kind)


MODEL = ConfidenceModel()
