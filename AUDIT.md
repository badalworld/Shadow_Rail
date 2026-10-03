# Shadow Rail — full code & engine audit (round 2)

Date: 2026-10-03 · branch `arena/01a1037b-shadow-rail` · scope: whole repository —
deprecation sweep (Python + Node toolchain), dependency security, and a live
speed test of every hot path (engine cycles, scanner sweeps, dashboard page
load, websocket boot frame, database), with everything slow fixed and
re-measured.

Round 1 (dead code, duplicated bodies, indicator-port bugs) is in the git
history of this file; its static findings remain at **0** — re-verified.

Re-run everything with:

```bash
/home/user/.venv/bin/python scripts/audit.py --strict      # static audit, CI-ready
/home/user/.venv/bin/python -m pyflakes backend/app backend/tests
/home/user/.venv/bin/python -m pytest backend/tests -q     # 114 tests, 0 warnings
cd frontend && npx tsc --noEmit && npm run build && npm run ui:check && npm run hq:audit
cd frontend && node scripts/ssr-smoke.mjs capture && node scripts/ssr-smoke.mjs
```

---

## 1. Deprecations found → fixed

| Where | Deprecation | Fix |
|---|---|---|
| `backend/app/api.py` | `@app.on_event("startup")` / `@app.on_event("shutdown")` — FastAPI has deprecated `on_event` in favour of lifespan handlers (emits `DeprecationWarning` on import; it had been silently hidden by the pytest filter below) | Replaced with an `@asynccontextmanager lifespan()` passed to `FastAPI(lifespan=…)`; the startup/shutdown bodies are unchanged |
| `backend/pytest.ini` | `filterwarnings = ignore::DeprecationWarning` — a blanket suppression that is exactly how the `on_event` deprecation above went unnoticed | Removed. The suite now runs with deprecation warnings **visible and at zero** |
| `backend/app/bus.py`, `backend/app/ratelimit.py` | `typing.Deque` — deprecated since Python 3.9 (PEP 585) | Annotations now use `collections.deque[…]` |
| `backend/requirements.txt` (test deps) | starlette 1.7's `TestClient` warns `Using httpx with starlette.testclient is deprecated; install httpx2 instead` on every test run | `httpx2>=2.0` added to the dev/test requirements; the warning is gone and the suite is warning-clean |
| `frontend/package.json` | vite 5.4.21 depends on esbuild ≤ 0.24.2 → **GHSA-67mh-4wv8-2f99** (dev-server request-forgery, moderate) flagged by `npm audit` | Upgraded to **vite 6.4.3** (patched, same major: no breaking changes for this build) — `npm audit` now reports **0 vulnerabilities** |

Nothing else in either stack uses a deprecated API: a repo-wide grep for
`utcnow / on_event / @validator / from_orm / parse_obj / typing.Deque /
pkg_resources / distutils / ReactDOM.render / findDOMNode / componentWill /
defaultProps` (plus three.js' removed `Geometry`/`outputEncoding`/`useLegacyLights`
family) comes back clean, and every backend module imports cleanly with
`-W error::DeprecationWarning`. The 3D component tree was reviewed for
per-frame allocation problems — it already memoises geometries, mutates via
refs and disposes GPU resources; nothing to fix there.

## 2. Speed test → what was slow, and the fixes

All "before" numbers measured on this machine (2 vCPU) against a live
simulation engine; "after" numbers re-measured the same way after the fixes.

### 2.1 Scanner sweeps / engine cycles — the engine's CPU bill

**Symptom:** each 5-minute cycle spent **3.4–5.5 s** sweeping the 150-symbol
universe (5 "parallel" scanner bots — but the indicator is pure-Python loops,
so the GIL serialises them; the sweep cost is single-thread speed × 150).

**Fix:** `indicators/ghost.py::compute()` and `indicators/pine.py` (the
`ema`/`rma`/`sma`/`rolling_sum`/`true_range` cores) now run their sequential
stages on plain Python floats instead of numpy scalar indexing (an order of
magnitude cheaper per iteration); vector-friendly stages stay vectorised.

**Correctness proof (this is the trading signal, so it is held to
bit-for-bit parity):** a harness compares the new implementation against the
previous one (reconstructed from git) over **96 cases** — 8 datasets
(trending / high-vol / gap / flat markets, 64–1500 bars, zero-volume bars) ×
12 parameter grids (all ghost placements, MTF gate on/off, strong-flip filter,
varied swing bars) — comparing **every output array bitwise, NaN payloads
included**: **0 mismatches**. The indicator self-test values (flips, rail
bars, clean ratio, ATR) are unchanged to the last digit, and all 114 tests
pass.

| Metric | Before | After |
|---|---|---|
| `ghost.compute()` (1000 bars) | 23.4 ms | **5.0 ms** (4.7×) |
| Scanner bot sweep (per bot) | 3.4–4.0 s | **0.8–1.1 s** |
| Full engine cycle | 3.9–5.5 s | **0.94–1.22 s** (avg 1.12 s) |

### 2.2 Dashboard first paint — external HTTP on the critical path

**Symptom:** `public_ip()` fired up to **three external HTTP calls (6 s
timeout each)** on **every page load and every websocket hello** (the boot
frame inlines `ip_info()`). On a firewalled host that is up to 18 s added to
first paint, and even on a healthy host it is a third-party round-trip in
front of the UI.

**Fix:** the probe result is now cached — success for 10 minutes, failure for
2 (negative cache, so a firewalled host also stops paying), single-flight
under a lock, and the per-URL timeout dropped to 2.5 s. The rendered
`index.html` is likewise cached by (mtime, size) instead of re-read from disk
per request.

| Metric | Before | After |
|---|---|---|
| `GET /` (warm) | 71–90 ms + up to 18 s worst-case external stall | **10–14 ms**, never stalls (63 ms cold) |
| `GET /api/ip` (repeat) | fresh probe every call | **~1 ms** cached |
| websocket hello (full boot frame) | + external IP probe | **33 ms** |

### 2.3 Database hot paths — Python-side aggregation over the whole journal

**Symptom (measured on a seeded 5,000-trade journal + 60 k log rows):**
`stats_summary()` loaded **every closed trade into Python** to compute sums —
22.9 ms per call, on a path hit every 5 s by the equity loop and by
`/api/stats`; `reconcile_exchange()` did the same every 30 s; the maintenance
loop paid a SELECT-then-UPDATE round trip per bot (29 bots) and the log
writer committed once per row. All of these grow linearly with the journal.

**Fix:** aggregates moved into SQL (`SUM/CASE`, `GROUP BY symbol`,
best/worst via `ORDER BY … LIMIT 1` — NULL-safe with `COALESCE` so legacy
rows behave exactly like the Python code they replace), `upsert_bot_stats`
is a single `INSERT … ON CONFLICT DO UPDATE`, and the engine's log writer now
batches with one transaction (`DB.add_logs`). The Python fallback helper
`fnum_net` and the now-unused `all_closed_for_stats` row dump were removed
(the audit's no-dead-code rule), replaced by `closed_totals()` and
`symbol_stats_rows()`.

| Path (5 k closed trades) | Before | After |
|---|---|---|
| `stats_summary` (every 5 s + `/api/stats`) | 22.9 ms | **5.7 ms** |
| reconcile totals (every 30 s) | 14.2 ms | **1.1 ms** |
| symbol stats (confidence model) | — (same 14.2 ms full scan) | **3.0 ms** |
| 40-row log batch (log writer) | 9.0 ms | **0.2 ms** |
| 29 × `upsert_bot_stats` (maintenance) | 14.2 ms | **1.5 ms** |

Crucially the SQL aggregates stay flat as the journal grows to 50 k+ trades,
where the old Python scans would have passed 200 ms.

### 2.4 Frontend bundle — one 1.46 MB chunk

**Symptom:** the whole dashboard (React + three.js + drei + framer-motion +
app code) shipped as a single 1,460 kB JS file — one cache entry, one
serial download, invalidated on every app change.

**Fix:** vendor chunk splitting in `vite.config.ts` (three / react / motion /
vendor / app). Same total bytes, but downloaded in parallel and cached
independently — an app-code deploy no longer re-downloads three.js.

```
three   750.5 kB (gzip 198.8)   vendor 260.3 kB (gzip  78.8)
react   147.3 kB (gzip  47.5)   motion 114.4 kB (gzip  37.8)
app     189.9 kB (gzip  51.7)
```

### 2.5 Verified fast (no action needed)

Every REST endpoint answers in ≤ 10 ms on the live engine (`/api/status`
1.7 ms, `/api/equity` 1.3 ms, `/api/scan` 1.5 ms, `/api/logs` 2.3 ms,
`/api/reconcile` 1.8 ms, the indicator self-test 10.6 ms); the websocket
fan-out, event bus and rate governor are bounded queues/deques with no
per-tick allocations; the 3D scene memoises and disposes its GPU resources.

## 3. Verification

* `scripts/audit.py --strict` → **0 findings** (dead code / orphans / duplication / markers)
* `pyflakes backend/app backend/tests` → clean
* `pytest backend/tests -q` → **114 passed, 0 warnings** (no warning filters left)
* indicator parity harness → **96/96 bitwise-identical** vs the pre-change implementation
* `npx tsc --noEmit` → clean; `npm run build` → 5 chunks, no warnings
* `npm audit` → **0 vulnerabilities**
* `npm run ui:check` → all UI rules pass against the rendered Command Deck
* `npm run hq:audit` → floor plan matches the live roster
* `node scripts/ssr-smoke.mjs` → all 9 pages render against captured live fixtures
* live engine: cycles 0.94–1.22 s, warm page load 10–14 ms, ws hello 33 ms
