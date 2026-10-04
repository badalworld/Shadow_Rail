# Shadow Rail — full-stack code review & data analysis

Date: 2026-10-04 · branch `arena/01a1049a-shadow-rail` · scope: the whole repository
(backend engine, exchange clients, indicator port, persistence, tests, dashboard) plus a
live end-to-end run of the engine against the simulator transport.

> Everything below was executed on this checkout.  Re-run it with the commands in
> [§7](#7-reproduce).

---

## 1. Verdict

**The engine and the dashboard work as specified.**  Every hard rule the README states
is enforced in code *and* observable on a live run: one TP/SL system per position, the
stop always inside liquidation, the ROI trail that only ever tightens, the 95 % API
ceiling, the 29-bot office, the equity identity and the journal ↔ venue ledger check.

The review found **ten issues** (1 medium, 4 low, 4 dead-code/robustness, 1 flaky test),
all fixed on this branch with regression tests where the behaviour was measurable.  None of them
changed strategy semantics — they are accounting, task-lifetime, hot-reload and display
correctness fixes.  The one thing that is *not* a code defect but is worth the operator's
attention is the strategy economics of the current simulator window
([§5](#5-data-analysis--the-live-journal)).

---

## 2. Verification evidence (all green on this branch)

| Check | Command | Result |
|---|---|---|
| Backend test-suite | `cd backend && python -m pytest` | **118 passed** (11 modules) |
| Repo audit (pyflakes + dead code + duplication + markers) | `python scripts/audit.py --strict` | **0 findings** |
| Python static check | `python -m pyflakes backend/app backend/tests scripts` | clean |
| TypeScript | `cd frontend && npx tsc --noEmit` | clean |
| Production bundle | `npm run build` | built → `backend/web/assets/index-BIYpptJH.js` |
| Dashboard the server serves | `npm run ui:check` | **all UI checks pass** (served bundle matches the spec) |
| 3D headquarters plan vs live roster | `npm run hq:audit` | all 29 agents seated, nothing clipped/overlapping |
| Every page rendered from the live API | `node scripts/ssr-smoke.mjs capture` then `node scripts/ssr-smoke.mjs` | 18 fixtures captured, all 9 pages render |
| Live invariants (running engine, HTTP) | `python /tmp/sr/verify.py` | **47–55 assertions (scales with open positions), 0 failures** |

The live harness (`/tmp/sr/verify.py`, stdlib-only) checks, against the running server:
engine running · 29 agents · 5 scanner buckets ≤ universe · concurrency ≤ 10 · per-trade
`liq < SL < entry` (mirrored for shorts, trail-aware) · live mark on every symbol ·
`uPnL = (mark − entry) × qty × side` · `ROI = uPnL ÷ margin` · the trail stop never on the
losing side and never armed before +25 % ROI · journal `net_pnl == released P&L` ·
`total = wins + losses + unreconciled` · equity bridge identity · venue ledger drift ≤
tolerance · office rules · 150 priced scan rows.

---

## 3. Rule-by-rule conformance (the written spec)

| Rule (README) | Where it lives | How it was verified |
|---|---|---|
| Signal 5 m, higher-TF filter 1 h EMA-50, no repaint | `indicators/ghost.py::_htf_bull_map` (HTF built locally from closed base bars) | indicator self-test on 900 bars: 7 raw flips → 2 confirmed, 890 rail bars, last trend −1 |
| Flip up → LONG, flip down → SHORT; opposite flip closes instantly | `engine._handle_reverse_exits` + `risk.reverse_exit_reason` | unit tests (`test_engine_flow`) + 34 live closes with `reverse_signal`/`sl`/`tp`/`trail` reasons |
| **Exactly one** TP/SL system, switchable | `config.RiskSettings.active_tp_sl()`; one `_conditional_market` path in `binance.py`/`sim.py` | `test_risk`, live order book: 1 `STOP_MARKET` + 1 `TAKE_PROFIT_MARKET` per open position, no orphans |
| Stop always between entry and liquidation (35 % buffer) | `risk.clamp_stop_to_liquidation` + executor re-check + `_stage_verify` | 4 live positions, all `liq < SL < entry` / mirrored; clamped cases logged as warnings |
| Max 10 concurrent, one position per symbol, 8 % margin @ 10× cross | `risk.plan` + `_open_trade_locked` re-checks under `_open_lock` | live: `open 8/10`, no duplicate symbols; `test_risk` |
| ROI trail: arms at +25 %, holds 15 pts behind the peak, starts +10 %, ratchets only, moves the one STOP order | `risk.trail_stop_price` + `engine._trail_tick` | `test_trail` (8 tests); live: `ACHUSDT` trailed to `0.04624`, exit == trail stop, **+$91.21 net, close_reason=trail** |
| 95 % API ceiling, nobody may exceed; protect/close always allowed | `ratelimit.WeightGovernor` + `_request` pre-charge + `X-MBX-USED-WEIGHT-1M` sync | `test_ratelimit` (8); live: 0 weight used, cap 2 280 armed, 0 blocked |
| 29 agents, 5×30 scan, 10 analysts, 2 execution, 4 monitors | `bots.build_registry` | `/api/bots` → 29, `/api/office` → 29 seats, buckets `[30,30,30,30,30]` |
| Promotion every 20 units, reliability gate, hire/fire | `bots.Bot/PROMOTE_EVERY` | `test_office` (16); live office avg level 1.31, 0 retired |
| Journal never invents money (`fills`/`estimated`/`unknown`) | `_finalize_close` + `db.stats_summary` | `test_journal_sim`, `test_identity_probe`; live: 34/34 `pnl_source=fills`, `unreconciled=0` |
| Notifications expire after 3 s; no banner on the deck | `store.tsx NOTIFY_MS = 3000` | `ui:check` asserts it against the source; SSR render asserts the deck carries no cards |
| Secrets encrypted at rest, never sent to the browser | `config.encrypt` (Fernet, 0600) + `public_view()` masking | `test_api_contract` round-trip: raw secret never appears in `/api/config` |

---

## 4. Findings & fixes

### 4.1 Live-trading correctness

| # | Severity | Finding | Fix |
|---|---|---|---|
| **F1** | **Medium** | **Non-trading income was booked as realised P&L.** `BinanceFutures.income_totals()` summed *every* unrecognised income type into `realized`. On a real account Binance's ledger also carries `TRANSFER`, `WELCOME_BONUS`, `INSURANCE_CLEAR`, `REFERRAL_KICKBACK`, `AUTO_EXCHANGE` … so a deposit would have appeared as trading profit and masked (or faked) journal↔venue drift. | Only `REALIZED_PNL`/`TRADE` counts as realized; cash movements go to a separate `other` bucket. `engine.reconcile_exchange()` now surfaces it as `exchange_other`. Regression tests: `tests/test_venue_ledger.py` (2). |
| **F2** | Low | **Background streams could be garbage-collected.** `asyncio` holds only *weak* references to tasks; `stream_klines()` (150-symbol websocket chunks) and the engine's fire-and-forget `DB.update_trade(...)` created tasks nobody referenced. A collected kline worker = a silently dead market feed. | `BinanceFutures` keeps `_stream_tasks` and cancels/awaits them in `close()`; `TradingEngine._spawn()` keeps a strong-reference set with a done-callback; `api._BOOT_TASK` holds the autostart task. Regression test: `test_kline_streams_keep_a_strong_task_reference`. |
| **F3** | Low | **A settings save wiped the office.** `PUT /api/config` rebuilt the roster from config, resetting every rank, promotion, hire and per-bot counter in memory (they only came back on the next restart). | The endpoint snapshots `registry.state()`, rebuilds, `restore()`s it and `_rebalance_workload()`s. Regression test: `test_settings_save_keeps_the_office_record`. |
| **F4** | Low | **Stale realised P&L after a close.** The equity sheet is refreshed by a 5 s loop, so for up to one tick the Account page could show the previous realised P&L next to a trade the history page already listed. | `engine._equity_wake` — the close path rings the bell and the equity loop re-reads the account immediately (event with a 5 s floor). |
| **F5** | Low | **Positions table used the journalled liquidation estimate** instead of the venue's live figure the API already ships as `liquidation_live`. | `frontend/src/pages/Positions.tsx` prefers `liquidation_live ?? liquidation_price` for both the colour warning and the cell. |

### 4.2 Dead code, clarity and test robustness (no production behaviour change)

| # | Finding | Fix |
|---|---|---|
| **F6** | `scripts/audit.py` — `duplicate_blocks()` assigned an `out` list that was never used (pyflakes finding in the audit tool itself). | removed |
| **F7** | `indicators/pine.py::_seeded_ma` carried a `seed` parameter and a "first sample" branch that no caller could reach (`ema`/`rma` both seed with the SMA). | parameter and branch removed; the Pine semantics are unchanged and every indicator test still passes |
| **F8** | `config.py::update()` had an unreachable "tolerate a key we do not model" branch (the key set was derived from the same dump it was checked against). | simplified; unknown fields are still reported by `unknown_keys()` and never written |
| **F9** | `pine.highest/stdev/crossover/linreg` and `util.true_range` remain as tested primitives the engine does not call. | kept deliberately (they are the Pine parity surface used by the indicator tests); noted here so a future audit does not flag them as orphans |
| **F10** | `test_websocket_hello_frame_and_live_relay` scanned a fixed 8 websocket frames for the `bot.promoted` event. The relay is a firehose — 150 symbols ticking at 3000× — so a loaded host queues more than 8 `market.tick` frames ahead of the event and the test flakes (~1 run in 4 observed under load, 0 in 3 clean runs). | the drain now runs to a 10 s wall-clock budget (capped at 2000 frames), so it is deterministic regardless of tick volume; verified by repeated runs |

### 4.3 Reviewed and found sound

* **Money maths** — `roi_points`, `roi_price_step`, `trail_stop_price`,
  `clamp_stop_to_liquidation`, `estimate_liquidation` (MMR 0.5 %) and the equity bridge
  `starting + released + unrealised − open entry fees = equity` reconcile exactly on the
  live run (bridge == equity to 4 dp).
* **Fee booking across restarts** — the entry leg is stored on the trade row at open and
  re-booked at close when the opening fill is no longer visible (`test_entry_fee_is_booked_even_when_the_close_happens_after_a_restart`).
* **Persistence** — WAL SQLite, idempotent migrations, single writer; the paper account is
  snapshotted on every equity tick and on shutdown, and restarted runs re-adopt open
  positions, protective orders and prices.
* **Bus** — bounded per-subscriber queues with drop-oldest: a slow websocket can never
  stall the trading loop.
* **Secrets** — Fernet at rest (`data/.secret.key`, 0600), masked in every API payload,
  `data/` git-ignored; `test_config_round_trip_never_leaks_the_secret` guards it.
* **Frontend plumbing** — same-origin typed fetch client, websocket with backoff and a 6 s
  REST fallback poll, `Cache-Control: no-store` shell + build-stamped asset URLs, an
  inlined boot snapshot for a zero-flash first paint, and a theme driven by SOS severity.
* **Indicator port** — the Pine `ta.*` primitives honour `na` propagation and TradingView
  seeding rules, the HTF gate is built from closed bars (no lookahead), and the flag
  before the EMA is warm refuses the symbol instead of biasing it one-sided.

---

## 5. Data analysis — the live journal

Snapshot from the running simulator (cycle ≈ 20, transport `sim`, accelerated clock: one
5 m candle ≈ 10 s real time).  The paper account is persisted and the simulator keeps
trading, so the count grows; every figure below is the **34-closed-trade** snapshot and
all of them are recomputable with `/tmp/sr/analysis.py`.

```
closed trades: 34     win rate 38.2 %  (13 W / 21 L)   net −$1,158.51   profit factor 0.617
equity $9,227.11 (start $10,000.00)   fees $269.00   funding $0.00   34/34 pnl_source = fills
```

| Close reason | n | win % | Σ R | net $ |
|---|---|---|---|---|
| `tp` (fixed target) | 10 | 100.0 | +19.43 | +1,580.94 |
| `trail` (ROI trail) | 3 | 100.0 | +2.83 | +399.75 |
| `sl` (protective stop) | 21 | 0.0 | −22.33 | −3,023.64 |

| Side | n | win % | Σ R | net $ |
|---|---|---|---|---|
| LONG | 21 | 42.9 | +1.28 | −502.84 |
| SHORT | 13 | 30.8 | −1.36 | −540.11 |

Dollars flatter and confuse; the R multiple is the honest unit — every trade journals it
(SL distance = 1 R).  **Confidence calibration in R:**

| score band | n | win % | Σ R | net $ |
|---|---|---|---|---|
| 60–69 | 13 | 23.1 | −6.73 | −1,131.24 |
| 70–79 | 11 | 45.5 | +2.61 | −42.17 |
| 80–89 | 10 | 50.0 | +4.03 | +130.46 |

R multiples: mean **−0.002**, median −1.04, best +2.53.  Hold time: median 71 s of the
simulated clock.  Ledger check: `journal_net − open_entry_fees == exchange_net`
(drift `0.0`, tolerance $1.18, balanced).  `/api/reconcile` exposes nothing unexplained
(`exchange_other 0.0`).

### What the numbers say

1. **The mechanics are clean.** Every close is priced from real exchange fills
   (**34/34 `fills`**, 0 `estimated`, 0 `unknown`), the ledger reconciles to the cent, and
   fees are booked where they are paid (0.05 % × notional × both legs ≈ $8 per trade,
   $269.00 / 34).
2. **The bracket does exactly what it is configured to do.** The stop never leaked
   (21/21 stops at −1.06 R), the target never leaked (10/10 TPs at +1.94 R, i.e. 3.0×ATR
   minus two legs of fees), and the three trail exits banked +0.94 R.  Mean expectancy is
   **−0.002 R over 34 trades** — a dead-flat coin flip with a 1 : 2.0 bracket at a 38 %
   hit rate.  Nothing in the engine is mispricing stops, targets or fills; the signal is
   simply carrying no edge in this window.
3. **The dollars are worse than the R — and that one is fixable, it is sizing.** Sizing is
   a fixed 8 % of equity × 10×, so *notional* is constant (~$7.6 k) while dollar risk is
   `notional × 1.5 × ATR%`.  ATR% differed systematically between outcomes: the median stop
   sat **1.03 %** below entry on winners but **1.57 %** on losers, so a −1 R loss cost
   ≈ $136 while a +1 R win paid ≈ $95 (1.4 : 1 against the account).  Counterfactual: the
   *same 34 trades* sized to a constant dollar risk would have finished ≈
   **−$390 (fees + slack) instead of −$1,159** — the bracket and the signal were not what
   lost the money, the volatility-blind position size was.  See recommendation 1.
4. **The confidence score carries real signal, and R shows it cleanly.** Σ R by band is
   monotone (−6.73 / +2.61 / +4.03) — the 60–69 band *alone* is the whole loss, across 13
   trades.  The ≥70 sample is +6.64 R over 21 trades.  Raising `min_confidence` removes
   the losing band without touching a single mechanic (recommendation 2).
5. **`shadow_3x` (no fixed target) is the structural alternative.** The `tp` rows prove
   the engine can bank 2 R moves; the `sl` rows show the stop taking 21 cuts for 10 runs.
   A trend-following exit (reverse signal / trail only) gives up the fixed 2 R target but
   stops paying for the ~60 % of flips that never run — worth a paper-account A/B before
   any live sizing (recommendation 3).
6. **Caveats.** The simulator's price model (regimes + AR(1) momentum) is deliberately
   trend-friendly but still synthetic, and one day of an accelerated clock is a tiny
   sample; every number above is evidence about the *engine*, not about the market.  Do not
   tune live size on it.

---

## 6. Recommendations

Ordered by evidence strength.  1 and 2 are backed by the 34-trade journal above; 1 is a
strategy-rule change, so it needs your decision before anyone codes it.

1. **Make position size risk-based, not margin-based (biggest measured lever).**
   Today every trade gets 8 % of equity × 10× of *notional*, so the dollars risked float
   with the symbol's ATR — in this window the losers' stops were 1.5× wider than the
   winners', which turned a break-even R curve into −$1,159.  Sizing to a constant risk
   budget (`qty = risk_budget ÷ stop_distance`, capped by the existing 8 % margin / 10×
   rule) would have cut the same window to ≈ −$390.  Suggested shape: a `sizing_mode`
   setting (`margin` = today's behaviour, `risk` = new) with `risk_per_trade_pct` ≈ 1 %,
   keeping every safety check in `risk.plan` untouched.  **Needs your approval — it is a
   documented rule in the README, so I did not change it unilaterally.**
2. **Raise `min_confidence` from 60 to 70** (Settings → Risk).  Σ R by band
   −6.73 / +2.61 / +4.03: dropping the 60–69 band removes the entire loss in this sample.
   Consider `require_strong_flip = true` at the same time; both are already wired into the
   model features and cost nothing to expose.
3. **A/B `shadow_3x` on the paper account** before touching live size: same signals,
   stop-only exit (3.0×ATR, no fixed TP), 20-trade `PROMOTE_EVERY` window on one bucket.
   The `tp`/`sl` split above predicts lower fee drag and fewer "gave it back" exits; the
   simulator can measure it for free.
4. **Before live keys**: run the same flow on Binance **testnet** (Settings → Endpoint)
   with a key restricted to this server's IP; the connector probe, order placement and the
   income-ledger reconciliation are then exercised on the real API.
5. Watch the **`exchange_other`** line in `/api/reconcile` once real keys are in use — a
   non-zero value means a deposit/transfer landed on the futures wallet and is correctly
   excluded from P&L.
6. Keep `max_concurrent_trades = 10` but watch `margin_used` vs equity once the account is
   live: 10 × 8 % at 10× is 80 % of equity in notional before fees.
7. Refresh the SSR fixtures (`node scripts/ssr-smoke.mjs capture`) whenever a payload shape
   changes — this branch deliberately left the generated fixture snapshot untouched so the
   code diff stays reviewable.

---

## 7. Reproduce

```bash
# backend
cd backend && /home/user/.venv/bin/python -m pytest -q          # 118 passed
/home/user/.venv/bin/python -m pyflakes backend/app backend/tests scripts
/home/user/.venv/bin/python scripts/audit.py --strict           # 0 findings

# frontend
cd frontend && npx tsc --noEmit && npm run build
npm run ui:check && npm run hq:audit
node scripts/ssr-smoke.mjs capture && node scripts/ssr-smoke.mjs

# live run (simulator transport, no keys needed)
cd backend && python -m app.main          # → http://localhost:8080
/home/user/.venv/bin/python /tmp/sr/verify.py      # live invariant harness
```

⚠️ Simulator figures are not a prediction.  Futures with leverage can lose money faster
than you can earn it — start on testnet or with a small account and conservative settings.
