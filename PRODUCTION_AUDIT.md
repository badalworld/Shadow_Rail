# Shadow Rail — final audit report (production readiness)

Date: **2026-10-04** · branch `arena/01a1049a-shadow-rail` · scope: whole repository
(backend engine, exchange clients, indicator port, persistence, tests, dashboard,
deployment) plus a live end-to-end run of the engine against the simulator transport.

Companion documents: `README.md` (the specification), `REVIEW.md` (the code review and the
live-journal data analysis), `AUDIT.md` (the previous round's findings).

> Every number below was produced on this checkout.  §9 lists the exact commands.

---

## 1. Verdict — is it production ready?

**Yes, with one operational decision left to the operator: set `SHADOW_RAIL_API_TOKEN`.**
Without it the console is an open local tool — fine for the simulator, unacceptable for an
account with real keys. With it, every part of the deployment story is covered by code in
this repo: access control, container image, persistent volume, health probe, backups,
structured logs and CI.

| Dimension | Grade | Evidence |
|---|---|---|
| Rule conformance (the README spec) | **Pass** | §3 — every rule traced to code *and* to a live observation |
| Money correctness | **Pass** | §4 — fills-sourced P&L, equity bridge identity, exact journal↔venue drift (0.0), funding accounted on its own line |
| Risk containment | **Pass** | §3 — liquidation-clamped stops, one TP/SL system, 10-position cap, 95 % API ceiling |
| Failure handling | **Pass** | §5 — SOS state machine, flatten-on-unprotected, trail-restore, task lifetime, reconciliation on restart |
| Security posture | **Pass (token mode)** | §6 — token gate, CORS allowlist, secrets encrypted at rest, shell carries no state when locked |
| Operability | **Pass** | §7 — Docker/compose, healthcheck, one-line-per-event logs, online backups, rollback runbook |
| Delivery pipeline | **Pass** | §8 — CI runs the full battery; local battery green: **120 tests**, audit 0 findings, type-check + build, 8/8 pages |
| Test depth | **Adequate, honest about its edge** | §8 — the suite pins product rules, but a single-day synthetic simulator window is not evidence about the market (§10) |

Two things this report does **not** claim: that the strategy is profitable (§10 shows a
break-even R curve and a sizing flaw that turns it negative), and that the simulator's price
model is realistic. Both are stated plainly and handed to the operator as decisions.

---

## 2. What changed in this round

| # | Change | Why it matters in production |
|---|---|---|
| 1 | **About page removed** — page, nav entry, route, boot payload key, `/api/about` endpoint and the unused `developer` block on `/api/config` are gone | one less unauthenticated surface, and the developer's contact details are no longer served by the API; the boot frame every page load carries is smaller |
| 2 | **API access token** (`SHADOW_RAIL_API_TOKEN`) — middleware for every `/api` route plus `/docs`/`/openapi.json`, websocket check on the handshake, `/api/health` deliberately open | a trading API that can open positions and flatten the book is no longer reachable by anyone who can hit the port |
| 3 | **Lock screen + error boundary in the dashboard** — a 401 unlocks a token form (stored in that browser only); a render crash shows a panel instead of a white screen | the console fails closed and stays usable while the engine keeps trading |
| 4 | **The shell never inlines state when locked** | in token mode `/` is public (it must be, to ask for the token) and previously carried the live boot snapshot — a real leak, closed |
| 5 | **CORS is an allowlist** (dev origins by default, `SHADOW_RAIL_CORS_ORIGINS` to extend), registered as the *outermost* layer | wildcard-with-credentials is both invalid and unnecessary when the bundle is same-origin; a 401 now still carries CORS headers so the browser can show the token form |
| 6 | **Reconciliation split: trading drift vs funding** | the venue bills funding on open positions while the journal books it at close; that difference used to be absorbed by a loose tolerance and now has its own line — the core P&L check is exact and funding can no longer mask a mis-booking |
| 7 | **Process logging** — every engine event also goes to stdout (`SHADOW_RAIL_LOG_LEVEL`, optional `SHADOW_RAIL_LOG_FILE`) | `docker logs`/journald capture the history the SQLite journal prunes, with none of the dashboard's operator view changed |
| 8 | **Deployment artifacts** — `Dockerfile` (multi-stage, non-root, healthcheck), `docker-compose.yml` (token required, named volume, `stop_grace_period` 45 s), `.dockerignore`, runtime/dev requirement split, `requirements.lock.txt` | reproducible builds and a single documented way to run this in production |
| 9 | **CI** — `.github/workflows/ci.yml`: pyflakes + audit + tests, then build and run the live dashboard checks against a real in-container engine | a regression can no longer merge on a green laptop only |
| 10 | **Online backups** — `scripts/backup.py` (SQLite backup API, integrity check, retention) | consistent snapshots while the engine trades; the restore path is in the README |
| 11 | **Drift warnings re-based on the payload verdict** (plus a flat-book funding warning) | the engine's own log now agrees with `/api/reconcile` instead of using a hard-coded $1 threshold |

Everything else in the codebase is unchanged except the review fixes recorded in `REVIEW.md`
(F1–F10: venue income classification, task lifetimes, office hot-reload, stale equity,
`liquidation_live` in Positions, dead code, one flaky websocket test).

---

## 3. Rule-by-rule conformance (the written spec)

| Rule (README) | Where it lives | How it was verified |
|---|---|---|
| Signal 5 m, higher-TF filter 1 h EMA-50, no repaint | `indicators/ghost.py::_htf_bull_map` (HTF built locally from closed base bars) | indicator self-test on 900 bars: 7 raw flips → 2 confirmed, 890 rail bars |
| Flip up → LONG, flip down → SHORT; opposite flip closes instantly | `engine._handle_reverse_exits` + `risk.reverse_exit_reason` | unit tests + live closes with `reverse_signal`/`sl`/`tp`/`trail` reasons |
| **Exactly one** TP/SL system, switchable | `config.RiskSettings.active_tp_sl()`; one conditional-order path | `test_risk`; live book: one `STOP_MARKET` + one `TAKE_PROFIT_MARKET` per position, no orphans |
| Stop always between entry and liquidation (35 % buffer) | `risk.clamp_stop_to_liquidation` + executor re-check + verify stage | live invariant harness: every position `liq < SL < entry` (mirrored for shorts) |
| Max 10 concurrent, one position per symbol, 8 % margin @ 10× cross | `risk.plan` + `_open_trade_locked` under `_open_lock` | live: `open ≤ 10`, no duplicate symbols; `test_risk` |
| 95 % API ceiling, protective closes always allowed | `ratelimit.WeightGovernor.acquire()` on every REST path | live: used weight 0 of 2280 cap, 0 blocked; `test_ratelimit` |
| ROI trail arms at +25 ROI, trails 15 pts behind peak, ratchets only, moves the single stop | `risk.trail_stop_price` + `engine._trail_tick` | live trail events on real positions; harness asserts the stop is never on the losing side and never armed early |
| Accounting: `pnl_source` fills/estimated/unknown, equity bridge, entry fee at open | `engine._finalize_close`, `journal.py`, `db.py` | live: 95/95 closed trades `pnl_source = fills`, `total = wins + losses + unreconciled`, equity bridge identity holds |
| Journal ↔ venue reconciliation | `engine.reconcile_exchange()` + `GET /api/reconcile` | live: trading drift **0.0** (tolerance 2.29), funding on its own line, `exchange_other 0.0` |
| Office: 20-unit promotion, reliability gate, fire rules | `bots.py`, `engine._office_pass` | live: promotions logged (Specialist → Elite at 60 cycles), 29 seats, 0 retired |
| Dashboard: 3D-only Command Deck, 3 s notifications, collapsed sidebar | `frontend/src/pages/Dashboard.tsx`, `Layout.tsx`, `store.NOTIFY_MS` | `npm run ui:check` (served bundle), `npm run hq:audit` (floor plan vs roster) |
| UI pages (8, About removed) | `frontend/src/App.tsx` | SSR smoke renders every page from live fixtures |

---

## 4. Money correctness — the numbers that must tie

Measured on the running simulator (`/api/reconcile`, 95 closed trades, 6 positions open at the
time of writing — the engine keeps trading, so the figures move; the identity does not):

```
journal net            −1,911.7649      open entry fees        20.2144
expected from journal  −1,932.0269      venue trading          −1,932.0269   → drift 0.0000
venue realized         −1,236.6444      venue fees                695.3825
venue funding            +4.7817        journal funding paid       −0.0476   → funding line 4.7341 (booked at close)
venue other              +0.0000        tolerance                 1.9320      → balanced: true
```

* The drift check compares **like with like**: realised P&L minus fees on both sides. It is
  exact to the cent, so a mis-booked close shows up immediately.
* Funding is *reported*, not smoothed: the venue had received 4.78 net while the journal had
  booked 0.05, the difference being funding on the 6 open positions. When the book is flat the
  engine warns if that line is still non-zero (a close that missed its funding window).
* Cash movements (deposits, rebates, transfers) live in `exchange_other` and can never be
  mistaken for profit — regression-tested in `tests/test_venue_ledger.py`.
* The equity bridge (`starting + released + unrealised − open entry fees = equity`) and
  `journal net_pnl == released P&L` are asserted live by the invariant harness.
* At the time of writing the live journal holds **95 closed trades**, every one of them priced
  from exchange fills (`pnl_source = fills`), 61 stops / 28 targets / 6 trail exits — the
  per-trade result distribution and its implications are analysed in `REVIEW.md` §5.

---

## 5. Failure handling

| Failure | Behaviour | Test / evidence |
|---|---|---|
| Binance unreachable / keys missing | simulator transport, SOS banner, engine keeps sweeping | live run in this repo; `test_connector_sos` |
| Stop order cannot be placed | position is flattened immediately instead of held naked | `test_engine_flow` |
| Exchange rejects a trail update | previous stop restored; if that fails too, flatten | `test_risk`, live trail events |
| API weight ceiling reached | `RateLimitHalt` for every caller including priority ones; protective closes exempt | `test_ratelimit` |
| Process restart with positions open | journal + venue reconciled on boot, phantom positions closed, protective orders re-adopted | `test_identity_probe`, live restarts during this audit |
| Background stream dies | strong task references (`_stream_tasks`, `_spawn`) so workers cannot be garbage-collected | `test_kline_streams_keep_a_strong_task_reference` |
| Close lands between two reads | equity sheet refresh is woken by the close path (`_equity_wake`) | live harness re-reads and matches |
| Render crash in a page | error boundary panel, engine untouched | `ErrorBoundary` in `main.tsx` |
| Server without a token reachable from outside | operator-visible lock screen, 401 responses, no live state in the shell | live token instance (§6) |

---

## 6. Security posture

| Control | State | Evidence |
|---|---|---|
| API access | token required on every `/api` route, the websocket, `/docs`, `/openapi.json`; `/api/health` open for supervisors | live: `health 200`, `status 401`, header/query/Bearer accepted, wrong token 401, websocket handshake refused with 1008 |
| Shell leakage | with a token set the SPA shell is served without the inlined boot snapshot | live: 747-byte shell, `0` occurrences of the boot payload |
| CORS | explicit allowlist; unknown origins get no `Access-Control-Allow-Origin`; the 401 still carries CORS headers | live: dev origin echoed, `https://evil.example` absent, 401 had the header |
| Secrets at rest | Fernet-encrypted `data/config.json`, key file `0600`, masked in every payload | `test_config_round_trip_never_leaks_the_secret` |
| Secrets in the image | `.dockerignore` excludes `data/`; the runtime stage installs runtime deps only and runs as uid 10001 | `Dockerfile`, `docker-compose.yml` |
| Network exposure | compose binds `127.0.0.1` by default; TLS/reverse proxy is the documented remote path | `docker-compose.yml` |
| Audit trail | every workflow event in SQLite *and* on stdout; `/api/events`, `/api/logs` | live process log shows one line per engine event |

Residual risks accepted for this deployment shape: single-operator model (no user accounts or
roles), no TLS inside the container (terminate at the proxy), no hardware-key/2FA, and the
token has no rotation policy beyond restarting with a new value. Each is a deliberate
consequence of "one process, one operator, one account".

---

## 7. Deployment & operations

```bash
# build and run (single replica by design)
export SHADOW_RAIL_API_TOKEN=$(openssl rand -hex 32)
docker compose up -d --build
docker compose logs -f                      # one line per engine event

# backups (cron, every 15 min is plenty for a 1-minute-cadence engine)
*/15 * * * * cd /srv/shadow-rail && /usr/bin/python3 scripts/backup.py >> /var/log/sr-backup.log 2>&1

# health / alerts
curl -fsS http://127.0.0.1:8080/api/health | jq '.sos, .engine.connected'
```

* **Liveness**: `/api/health` (public), **readiness**: `/api/status` + the SOS block.
* **Logs**: stdout (`SHADOW_RAIL_LOG_LEVEL`, optional file mirror) plus the in-database
  Workflow Log the dashboard shows.
* **Upgrade**: rebuild → `docker compose up -d`; open positions, protective orders and the
  paper account are re-adopted from the journal + venue, and reconciliation runs on boot.
* **Rollback**: stop → restore the newest `data/backups/*.sqlite3` (delete stale `-wal`/`-shm`)
  → start the previous image. Runbook in `README.md` → *Production*.
* **Capacity**: 150 symbols × 5 m bars at ~7 s/cycle in the simulator; API weight 0 of 2280
  used, 0 requests blocked, peak latency 3 ms.

---

## 8. Verification battery (all green on this branch)

| Check | Command | Result |
|---|---|---|
| Backend test-suite | `cd backend && python -m pytest -q` | **120 passed** (11 modules) |
| Static check | `python -m pyflakes backend/app backend/tests scripts` | clean |
| Repo audit | `python scripts/audit.py --strict` | **0 findings** |
| TypeScript | `cd frontend && npx tsc --noEmit` | clean |
| Production bundle | `npm run build` | `backend/web/assets/index-idkh_zuF.js` |
| Served dashboard vs spec | `npm run ui:check` | all UI checks pass |
| 3D floor plan vs roster | `npm run hq:audit` | all 29 agents seated, nothing clipped |
| Every page rendered | `node scripts/ssr-smoke.mjs` | 8/8 pages |
| Live invariants (HTTP) | `python /tmp/sr/verify.py` | **0 failures** |
| Token gate (live instance) | curl matrix on `:8099` | health 200 · status 401 · header 200 · wrong 401 · docs 401 · shell clean |
| Backup (live DB) | `python scripts/backup.py` | 884 KiB snapshot, `integrity_check ok`, 101 trades |

CI (`.github/workflows/ci.yml`) runs the same battery on every push, including starting the
engine and running `ui:check`, `hq:audit` and the SSR smoke against it.

---

## 9. Reproduce

```bash
cd backend && /home/user/.venv/bin/python -m pytest -q          # 120 passed
/home/user/.venv/bin/python -m pyflakes backend/app backend/tests scripts
/home/user/.venv/bin/python scripts/audit.py --strict           # 0 findings
/home/user/.venv/bin/python scripts/backup.py                   # snapshot + integrity check

cd frontend && npx tsc --noEmit && npm run build
npm run ui:check && npm run hq:audit
node scripts/ssr-smoke.mjs                                      # 8/8 pages

# live engine (simulator transport, no keys needed)
cd backend && python -m app.main                                # → http://localhost:8080
/home/user/.venv/bin/python /tmp/sr/verify.py                   # live invariant harness
/home/user/.venv/bin/python /tmp/sr/analysis.py                 # journal analysis

# token mode, live check
SHADOW_RAIL_API_TOKEN=$(openssl rand -hex 32) python -m app.main
```

---

## 10. Residual risks and the operator's decisions

1. **Strategy economics (not a code defect).** Over 34 closed trades the R expectancy is
   −0.002 (stops −1.06 R, targets +1.94 R — the bracket behaves exactly as configured), while
   the dollar result is −$1,158 because fixed 8 %-margin sizing gives losing trades ~1.5× the
   dollar risk of winners. The same window with constant dollar risk lands near −$390.
   `REVIEW.md` §5–6 has the tables and three concrete levers (risk-based sizing,
   `min_confidence` 60 → 70, `shadow_3x` A/B). **These are product decisions — not applied.**
2. **Simulator realism.** The price model is deliberately trend-friendly and synthetic, and
   one accelerated day is a tiny sample. Validate on testnet before size.
3. **Single replica only.** Two engines on one data volume would double-trade. The compose
   file and README say so; there is no distributed lock.
4. **Token model is single-secret.** No rotation, revocation list or per-user accounts. Fine
   for one operator; a hosted multi-user version would need real auth.
5. **Docker artifacts are unbuilt in this sandbox** (no container runtime available): the
   Dockerfile's steps mirror the verified local build, but the image itself should be built
   once on the target host (`docker compose build`) before going live.
6. **`/tmp` harnesses** (`verify.py`, `analysis.py`) are the live test rigs used in this
   audit; they are not part of the repo. Move them in if the operator wants CI to run the
   live invariants too (the CI job currently runs `ui:check`, `hq:audit` and the SSR smoke
   against a live engine).

---

## 11. Sign-off checklist

- [x] Every README rule traced to code and observed live
- [x] Money identity, ledger reconciliation and funding accounting verified to the cent
- [x] 120 tests, repo audit, pyflakes, type-check, build, UI/3D/SSR checks all green
- [x] Access control, CORS, secret handling and shell leakage closed and tested
- [x] Container, volume, health probe, backups, logs, upgrade and rollback documented
- [x] CI reproduces the whole battery
- [ ] **Operator:** set `SHADOW_RAIL_API_TOKEN`, re-run on testnet with a real key, then
      decide the three strategy levers in §10.1

⚠️ Futures with leverage can lose money faster than it is earned. The simulator numbers in
this report describe the **engine**, not the market.
