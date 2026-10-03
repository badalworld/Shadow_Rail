# Shadow Rail — full code & engine audit

Date: 2026-10-04 · branch `arena/01a10287-shadow-rail` (PR #1) · scope: whole repository
(backend engine, exchange clients, indicator port, tests, dashboard) plus a live
end-to-end run of the engine.

Re-run everything with:

```bash
/home/user/.venv/bin/python scripts/audit.py --strict      # static audit, CI-ready
/home/user/.venv/bin/python -m pyflakes backend/app backend/tests
/home/user/.venv/bin/python -m pytest backend/tests -q
cd frontend && npx tsc --noEmit && npm run build && npm run ui:check && npm run hq:audit
```

---

## 1. Result

| Section | Before | After |
|---|---|---|
| Dead imports / unused locals (pyflakes) | 40 | **0** |
| Module-level names nothing uses | 29 | **0** |
| Orphan modules / files | 0 | 0 |
| Unused frontend exports | 3 | **0** |
| Duplicate function bodies | 2 | **0** |
| Duplicated 14-line blocks | 4 groups (2 false) | **0** |
| Leftover markers (`TODO`/`FIXME`/`HACK`/`XXX`) | 0 | 0 |
| **Total** | **75** | **0** |

Everything below was found by the audit, fixed, and re-verified; no finding was
suppressed by an allowlist except framework-called HTTP/websocket handlers
(FastAPI calls them, the source never does).

## 2. Real bugs fixed

| Where | Problem | Fix |
|---|---|---|
| `exchange/sim.py:199` | `_advance(self, s: "SymbolState", …)` referenced a name that does not exist — any tool that resolves annotations (`typing.get_type_hints`, model rebuilds) would raise `NameError`. | Annotation is `SimSymbol`. |
| `engine.py:728` | `f"sweep complete"` — f-string with no placeholders. | Plain string. |
| `engine.py:880` | `stats = await self.journal.refresh_stats()` bound a value nobody read. | The call stays (it keeps the dashboard stats hot), the dead binding is gone. |
| `engine.py:1286` | `sl_roi` computed on every monitor tick and thrown away — the payload already carries `stop_roi_pct`. | Dead computation removed. |
| `indicators/ghost.py:299` | `slo = shadow_lo[i - 1]` read in the Shadow Rail state machine but never used (the loop already carries its state exactly like Pine's `var`). | Dead read removed; the ported maths and all indicator tests are unchanged. |
| `api.py:548` | `e = eng()` in the websocket handler never used (`boot_payload()` builds its own engine handle). | Dead line removed. |

## 3. Dead code removed (with reasons)

* **`exchange/base.py`** — `MarketData` and `Broker` Protocols: no implementation
  names them and nothing type-checks against them. The concrete clients are the
  contract.
* **`exchange/sim.py`** — `SimBroker = SimExchange` compatibility alias (no
  references) and `MAKER_FEE` (the simulator always fills as a taker, so only
  `TAKER_FEE` applies).
* **`risk.py`** — `validate_notional()` and `apply_slippage_guard()`: both were
  *superseded* copies of live logic — min qty / min notional are enforced in
  `plan_sizing()` (which also owns the step floor, max-qty clamp and the
  `min_notional_override`), and fill sanity is handled by the executor's
  fill-drift recompute. Removed and replaced with an in-code note so a second,
  divergent copy cannot creep back in.
* **`indicators/pine.py`** — `na_mask`, `lowest`, `crossunder`: primitives the
  Ghost Candle port never calls.
* **`util.py`** — `iso`, `last_closed_open_ms`, `safe_div`, `aggregate` (+ its
  private `_merge`), `fmt_money`, `chunked`, `now_iso`, `percentile`: none had a
  caller anywhere (backend, tests, or frontend).
* **`frontend`** — unused exports `Sparkline`, `MiniMeter` (`Charts.tsx`) and
  `Ring` (`Glass.tsx`); the dashboard still builds and renders identically.
* Unused imports / locals across `api.py`, `engine.py`, `db.py`, `journal.py`,
  `risk.py`, `exchange/hub.py`, `exchange/sim.py`, `indicators/ghost.py` and the
  five test modules.

## 4. Duplicate code removed

`binance.py` and `sim.py` each carried byte-identical `stop_market()` and
`take_profit_market()` bodies that differed only in the order type literal. Each
client now has **one** `_conditional_market(type, …)` path, so the two
protective order payloads can never drift apart. This matters for a hard project
rule: exactly one TP/SL system is active per position, never doubled.

## 5. Audit tool

`scripts/audit.py` is part of the repo (`--strict` exits non-zero on any
finding). Three deliberate hardening passes were needed before its output could
be trusted:

1. **Framework-called functions are not dead code.** A decorated top-level def
   (FastAPI route, websocket handler, middleware) is registered with the
   framework and called by it — the first run flagged 14 live endpoints.
2. **A shared file preamble is not copy-paste.** Module docstrings and import
   blocks are stripped before clone comparison, so `binance.py`/`sim.py` header
   similarity no longer reads as duplication.
3. **A copy-pasted function usually only renames itself.** Declaration lines are
   dropped from block fingerprints, so a duplicated body is still detected after
   the rename. The detector was validated by injecting a synthetic renamed clone
   (reported) and then removing it.

## 6. Engine check (live, simulator transport, 150 symbols)

```
connected: True · transport: sim · universe: 150 · API weight 0.0 % of 2 400/min (cap 2 280)
sos: {active: false, level: "notice", reasons: ["no Binance API keys stored — simulator active"]}
```

* **Scan stage** — 5 scanner bots × 30 assets; sweeps complete in 3.3–4.5 s.
  The sweep is now bounded by `SCAN_TIMEOUT_S` (120 s) via `asyncio.wait_for`:
  a hung exchange call logs and continues with the opportunities already found
  instead of stalling the 5-minute cycle. No timeouts across 24+ logged cycles.
* **Positions & protection** — 5/10 open, every invariant holds:

  | # | symbol | side | entry | stop | target | liquidation | mode |
  |---|---|---|---|---|---|---|---|
  | 47 | LTCUSDT | LONG | 96.7461 | 96.054 | 98.099 | 87.5457 | indicator_default |
  | 53 | MANTAUSDT | LONG | 2.13876 | 2.104 | 2.207 | 1.93541 | indicator_default |
  | 55 | OMNIUSDT | SHORT | 8.47608 | 8.620 | 8.190 | 9.28185 | indicator_default |
  | 58 | WIFUSDT | SHORT | 1.36382 | 1.379 | 1.333 | 1.49348 | indicator_default |
  | 59 | BANANAUSDT | SHORT | 45.1723 | 45.950 | 43.627 | 49.4672 | indicator_default |

  The stop is always between entry and liquidation (`liq < SL < entry` for
  longs, mirrored for shorts) — the "SL never beyond liquidation" rule holds on
  every live position.
* **Order book** — exactly one `STOP_MARKET` **and** one `TAKE_PROFIT_MARKET`
  per open position (one risk system, its two legs), each price matching the
  journal to the last decimal; zero orphans, zero extra orders, no duplicates.
* **ROI trail** — 5 trades closed by the trail, every one of them profitable,
  with `trail_stop == exit price` (+77.97 LRCUSDT, +193.63 HOTUSDT, +121.32
  AEVOUSDT, +187.05 PIXELUSDT). The trail moves the single existing stop; it
  never adds an order and never loosens.
* **Accounting** — 54 closed trades, 24 W / 30 L (win rate 44.4 %), profit
  factor 1.072, **net +$282.10 after $426.31 fees and +$7.73 net funding**,
  0 unreconciled trades. Close reasons: 30 stop, 19 target, 5 trail.
* **Equity manager** — starting balance locked at 10 000 (never moves after the
  first connect), equity 10 584.40, balance 10 263.33, available 6 388.70,
  margin used 4 195.70, released P&L +282.10.
* **Office** — 29 agents over 7 departments, promotion rule every 20 completed
  trades, average level 1.72, 0 retired, 0 hires yet.
* **Indicator port** — self-test ok on 900 bars: 890 rail bars, 2 confirmed
  flips out of 7 raw flips (higher-timeframe gate + quality filter), last trend
  −1, clean ratio 0.517, ATR 0.627, parameters exactly the TradingView defaults
  (swingBars 5, railSpread 1.6, railDrive 95, ghostBlur 10, slAtrX 1.5, tpAtrX 3.0).
* **API governor** — 0.0 % of the 2 400/min budget used, hard cap at 95 %
  (2 280) armed, no blocked requests, no halt.

Live dashboard re-check after the frontend cleanup: headquarters renders with
minimal labels (department names and agent names only), sidebar symbols only,
no win/loss banner, no ambient amber warning — captured to
`/tmp/shot/post_audit_deck.png`.
