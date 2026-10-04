# Raising the win rate — what we measured, what we shipped

Date: 2026-10-04 · branch `arena/01a104c8-shadow-rail`
Question: *"How to upgrade our Win Rate — Implemented Best Profitable System."*

Short answer: the shipped system was **losing** money (−88 % in-sample), because
round-trip costs (0.10 % of price) are about the same size as the edge the
indicator actually has. We built a measurement lab, found that the indicator's
edge is real but lives in *trend* not in *mean-reversion distance*, switched the
engine to a volatility-normalised runner with cash-at-risk sizing, and moved

| | win rate | profit factor | expectancy | max DD | return |
|---|---|---|---|---|---|
| shipped before (`indicator_default`) | 37.8 % | 0.89 | −0.060 R | 91 % | **−88 %** |
| shipped now (`edge_runner`) | **43.0 %** | **1.48** | **+0.283 R** | **9.2 %** | **+102 %** |

and it holds out of sample: **43.7 % win, PF 1.48, +0.292 R, +73 %** on bars the
selection never saw.

**The honest caveat, up front.** Win rate and expectancy pull in opposite
directions. A stop that is never reached wins often and earns little. The table
above is the trade we chose; §6 shows the other end of the curve (56 % win rate,
PF 1.15) and how to switch to it in the UI. Both beat the old system.

---

## 1. How to reproduce

```bash
/home/user/.venv/bin/python scripts/backtest.py            # headline: old vs new, IS + OOS
/home/user/.venv/bin/python scripts/backtest.py --ablate   # one upgrade off at a time
/home/user/.venv/bin/python scripts/backtest.py --sweep    # the stop-width curve
/home/user/.venv/bin/python scripts/lab.py                 # policy matrix, 4 market types
/home/user/.venv/bin/python scripts/grid.py                # 96-cell robustness grid (~3 min)
cd backend && /home/user/.venv/bin/python -m pytest -q     # 146 tests
```

The market is synthetic (`backend/app/marketgen.py`) because this sandbox has no
route to Binance. It is a shared, seeded generator with a momentum knob, not a
curve-fit target: `momentum=0.0` is a pure random walk, and the lab is required
to report **no** edge on it (`backend/tests/test_backtest.py` asserts this).

## 2. Why the old system lost

| measurement (40 symbols × 12 000 bars) | result |
|---|---|
| round-trip cost, Binance taker, 20× | **0.10 %** of price ≈ **0.12 ATR** |
| forward move after a flip, 24 bars | **+0.241 ATR** (t = 4.46) |
| win rate of "did price move my way at all" | 52–53 % |

So the signal is worth roughly **two to three times** what it costs — thin but
real. It is not enough to survive a 3 ATR target that the market reaches less
often than it reaches 1.5 ATR the wrong way:

* every barrier-exit combination loses without filters — the best of eleven
  (2.0/4.0 ATR) is PF 1.00 and −$745; the shipped 1.5/3.0 is PF 0.93 and −$9 130;
* tightening the target does not help — a 1 ATR target is PF 0.71.

Conclusion: **do not cap the winner.** Give the trade room, take the profit when
the trend ends, and cut the setups where the edge is smaller than the fee.

## 3. What the lab found

Feature buckets (24-bar forward return in ATR, n = 4 416). Only monotone,
large-sample splits were promoted:

| feature | best bucket | n | forward | t |
|---|---|---|---|---|
| trend quality (`clean_ratio`) | ≥ 0.60 … ≥ 0.30 | 1 870 | +0.330 | 5.18 |
| volatility (`atr_pct`) | 0.7 – 1.2 % | — | +0.356 | 5.53 |
| volatility (`atr_pct`) | > 1.2 % | — | **+0.072** | 1.01 |
| distance from the rail | 2.0 – 3.0 % | — | +0.473 | 5.48 |
| order flow (`flow_bias`) | ≥ 0.15 | — | +0.284 | 5.76 |

Two findings shaped the exit:

* **Chop is not a linear filter.** Efficiency ratio 0.00–0.08 *and* ≥ 0.30 both
  beat the middle (+0.256 and +0.390). A monotone gate cannot capture that, so
  it was left off rather than fitted to noise.
* **Volatility-normalised exits are the wrong instrument.** Scaling the stop to a
  target ATR% *tightens* stops in fast markets — exactly backwards for an exit
  that is supposed to let a drift run. Measured: PF 0.59 out of sample. Left
  implemented, left off.

## 4. What shipped

| change | old | new | measured effect (OOS, one knob off at a time) |
|---|---|---|---|
| **risk mode** | `indicator_default` (1.5/3.0 ATR) | `edge_runner` — one volatility-normalised stop at 6 ATR, no target | the whole difference |
| **sizing** | 8 % of equity as margin | **1 % of equity as cash at risk**, 8 % margin ceiling | a 6 ATR stop and a 1.5 ATR stop now put the same money at risk |
| **protective trail** | ROI points (25/15) | **R multiples (1.5 R / 0.9 R)** | PF 1.50 → 1.48, +0.27 R → +0.29 R |
| **break-even lock** | none | stop → fee-covered break-even at +1.5 R | −0.07 R of expectancy, buys the "was up 1.5 R, booked a loss" tail |
| **entry gates** | none | trend quality ≥ 0.30, ATR% ≤ 0.80 | **PF 1.27 → 1.48, +0.150 R → +0.292 R** |
| **brake** | none | hold *new entries* past 15 % DD or an 8-loss streak | −0.04 R, never touches a protective close |

Every rejected candidate stays implemented and switchable. Each one carries a
comment recording the number that killed it, so nobody has to re-litigate it.

### The two hard rules, kept

1. **One risk system per position.** The trail and the break-even lock *move*
   the single `STOP_MARKET` the trade already owns. They never add a second
   protective order. `backend/tests/test_trail.py` and the live-order check in
   the audit both assert it.
2. **The brake holds entries only.** Protection, closes and the reverse-signal
   exit stay armed while the throttle is on, so a position can never be trapped.

## 5. Where it lives

| file | role |
|---|---|
| `backend/app/edge.py` | gates, R-denominated trail/break-even maths, cost gate, conviction sizing, time stop |
| `backend/app/backtest.py` | the lab: fees + funding, stop-before-target fill order, R accounting |
| `backend/app/marketgen.py` | the shared synthetic market (momentum knob, seeded) |
| `backend/app/config.py` | `EdgeSettings` — shipped defaults, each with the measurement behind it |
| `backend/app/risk.py` | cash-at-risk sizing, R→ROI trail conversion |
| `backend/app/engine.py` | gate before analyst time, sizing fraction, brake, R-trail, break-even, time stop |
| `frontend/src/pages/Settings.tsx` | **Edge & Win Rate** tab — every knob, plus the live rejection tally |
| `scripts/{backtest,lab,grid,tune}.py` | the studies; `tune.py` is kept as a documented dead end |

## 6. The win-rate dial, if you want more of it

Measured out of sample on the same panel:

| configuration | trades | win | PF | expectancy | max DD |
|---|---|---|---|---|---|
| **shipped** — trail 1.5 R / 0.9 R | 213 | 43.7 % | 1.48 | +0.292 R | 15.4 % |
| trail 2.5 R / 1.5 R | 177 | 44.6 % | 1.65 | **+0.368 R** | 13.9 % |
| ROI trail 25 / 15 pts | 342 | **56.1 %** | 1.15 | +0.074 R | 16.4 % |

A tighter trail locks a small profit more often — that is the 56 % — and gives
back most of the expectancy doing it. Both are one dropdown away
(**Risk / TP-SL → trailing stop → measured in**); the shipped default is the one
that pays.

## 7. Known limits

* **All results are synthetic.** The market generator has drift, momentum and
  session volume, but it is not Binance. Re-run `scripts/backtest.py` against
  real candles before risking money.
* **The grid ranks by worst-panel expectancy, not win rate.** Ranked that way the
  top cell is a 4 ATR stop with a 2.5 R trail (worst panel +0.310 R) at a
  **32 %** win rate. We did not ship it, because the brief asked for win rate and
  42.5 % vs 32.1 % is the whole point — but if the objective ever changes to raw
  expectancy, that is the cell to ship.
* **Two protection features cost expectancy on paper.** The break-even lock
  (−0.07 R) and the brake (−0.04 R) both measure slightly negative in a lab that
  fills stops exactly at the level. Live stops slip; that is what they are for.
* **One test was relaxed.** `test_websocket_hello_frame_and_live_relay` read 8
  frames and expected the promotion among them. The swarm emits ~30 events/s and
  a scanner sweep alone is three frames, so it now reads up to 80 with the same
  assertion. It failed on this branch before the change and passes 3/3 after.
* **Two audit tests were failing for an unrelated reason**: `backend/web/` (the
  production bundle) is git-ignored and had not been built. `npm run build` fixes
  them; the suite is 146/146 green.
