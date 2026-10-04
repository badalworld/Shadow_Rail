# 🛰 Shadow Rail — Automatic Trading Engine

> Trend in friend. Trend filter Trading Ghost.

A production-shaped automatic trading engine for **Binance USDT-M Futures** driven by the
TradingView indicator **Ghost Candle with Shadow Rail (GCSR)** — ported line-by-line from
Pine Script v6 to Python and executed by a swarm of 29 cooperating bots.

---

## What it does

| Stage | Bot(s) | Responsibility |
|---|---|---|
| 1. Link | **ORACLE** (Connector) | Confirms the Binance link, re-checks **every 5 minutes**, raises **☠️ SOS** (whole dashboard turns red) if anything is wrong |
| 2. CEO | **PRIME** | Receives the green signal, orders the swarm, confirms every hand-off |
| 3. Scan | **VEGA · NOVA · ORION · LYRA · ATLAS** (5) | Sweep **30 volatility-ranked assets each** on the 5-minute candle |
| 4. Analyse | **EINSTEIN … BOHR** (10) | Score each flip 0–100 (7-factor model + logistic learning); only confident setups advance |
| 5. Execute | **BOLT · TITAN** (2) | Size (8% equity @ 10× cross), place the market order, attach **one** TP/SL system inside liquidation |
| 6. Verify | **ECHO** | Confirms position, quantity, stop and target really exist on the exchange |
| 7. Monitor | **SENTINEL · WARDEN · WATCHMAN · GUARDIAN** | Watch every open position live, flag liquidation proximity |
| 8. Journal | **LEDGER** / **BANKER** / **AEGIS** | History, P&L, win-rate, fees, funding, equity, stops |

Wins make the whole swarm celebrate 🎉; losses make it mourn 💧; sustained good work earns
**field promotions** (Recruit → Operative → Specialist → Elite → Legend).
A **VAULT** (API Guard) bot keeps every request inside **95 % of the IP weight budget** and
attributes each call to the bot that made it.

### Strategy rules (as specified)

* Signal timeframe **5m**, higher-timeframe filter **1h EMA-50** (no repaint).
* Flip up → **LONG**, flip down → **SHORT**; an opposite flip closes the position instantly.
* Exactly **one** take-profit/stop-loss system is ever active, switchable in Settings:
  * **Indicator default** — SL **1.5×ATR**, TP **3.0×ATR** (recommended, smallest risk)
  * **Shadow 3×** — SL 3.0×ATR, no fixed target (runs to the reverse signal)
  * **Custom** — your own multiples
* The stop is **always clamped between entry and liquidation** (35 % safety buffer by default).
* **Max 10 concurrent trades**, one position per symbol, 8 % margin each at 10× cross.

### ROI trailing stop (never give a winner back)

Once a position's **ROI reaches +25 %** (ROI = unrealised P&L ÷ margin, leverage-adjusted, exactly
like Binance's ROI column) the trail arms and holds the stop **15 ROI-points behind the best price
the position has seen** — so it starts at **+10 % ROI** and only ever ratchets *up*. It:

* **moves the one STOP_MARKET order** the trade already has (cancel + replace, never a second order);
* never loosens (a falling market can never pull the stop back down);
* never sits on the losing side of the entry (break-even is the floor) and never past liquidation;
* is kept a hair behind the mark (`0.05 %`) so it can never fire the instant it is placed;
* throttles itself (≥ 1 ROI-point improvement, one request per monitor tick) to stay inside the API budget.

Activation, distance and step are editable in **Settings → Risk / TP-SL**. A trailed exit is
journalled with `close_reason = trail`, so the history shows exactly how much was protected.

---

## Quick start

```bash
# 1 — backend (Python 3.11+)
python3 -m venv .venv && . .venv/bin/activate
pip install -r backend/requirements-dev.txt      # runtime + test tools
# exact verified versions: pip install -r backend/requirements.lock.txt

# 2 — dashboard (Node 18+)
cd frontend && npm install && npm run build && cd ..

# 3 — run (serves API + dashboard on one port)
cd backend && python -m app.main          # → http://localhost:8080
```

Open **http://localhost:8080**, go to **Settings → Binance API & IP**, paste your API key +
secret, press **Test connection**, then add the displayed IP to your Binance key's whitelist.

> The engine boots in **simulation** whenever Binance is unreachable so you can watch the full
> workflow offline; the SOS banner stays red until the real link is verified — nothing is hidden.

### Requirements for the API key
* Enable **Futures** (USDⓈ-M), permissions **Read + Trade** (never enable withdrawals)
* Restrict access to your server IP (the engine displays it on the Settings page)
* Works with **testnet** keys too (`Settings → Endpoint`)

---

## Useful commands

```bash
# tests (indicator port, risk maths, API governor, journal, full engine flow)
cd backend && ../.venv/bin/python -m pytest -q

# render every dashboard page against the live API (catches payload drift)
cd frontend && node scripts/ssr-smoke.mjs capture && node scripts/ssr-smoke.mjs

# indicator self-check against the Pine logic
cd backend && ../.venv/bin/python -m app.indicators.ghost

# dev dashboard with hot reload (proxies /api + /ws to :8080)
cd frontend && npm run dev

# prove the dashboard the server is serving matches the agreed UI
cd frontend && npm run ui:check

# check the 3D headquarters plan against the live roster (seats, furniture, walls)
cd frontend && npm run hq:audit

# rebuild the dashboard bundle FastAPI serves
cd frontend && npm run build

# static audit (dead code, duplication, leftovers) — CI runs this with --strict
python scripts/audit.py --strict

# consistent backup of the live journal (safe while the engine trades)
python scripts/backup.py
```

Environment overrides:

| Variable | Default | What it does |
|---|---|---|
| `SHADOW_RAIL_PORT` / `SHADOW_RAIL_HOST` | `8080` / `0.0.0.0` | where uvicorn listens |
| `SHADOW_RAIL_DATA_DIR` | `<repo>/data` | SQLite journal, encrypted config, Fernet key |
| `SHADOW_RAIL_API_TOKEN` | *unset* | **production switch**: every `/api` route, the websocket, `/docs` and `/openapi.json` then require this token; `/api/health` stays open for supervisors. Unset = the local simulator dashboard (open, as before) |
| `SHADOW_RAIL_CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | comma-separated allowlist; same-origin production needs none |
| `SHADOW_RAIL_LOG_LEVEL` / `SHADOW_RAIL_LOG_FILE` | `info` / *unset* | process log on stdout (one line per engine event, same records as the Workflow Log) and an optional file mirror |
| `SHADOW_RAIL_BACKUP_DIR` / `SHADOW_RAIL_BACKUP_KEEP` | `<data>/backups` / `14` | used by `scripts/backup.py` |
| `SHADOW_RAIL_RELOAD` | `0` | uvicorn reload (development only) |

---

## Layout

```
backend/
  app/
    api.py            FastAPI REST + /ws realtime stream, serves the dashboard
    engine.py         the CEO workflow state machine (scan→analyse→execute→verify→monitor)
    bots.py           the 29-bot roster, ranks, promotions, moods, workflow graph
    risk.py           sizing, liquidation-safe stops, the one-system TP/SL rule
    confidence.py     analyst scoring (7 factors + logistic model trained on your journal)
    journal.py        Trade Manager + Equity Manager (locked starting balance!)
    ratelimit.py      API Weight Governor — the 95 % ceiling
    db.py             SQLite persistence (trades, events, equity curve, logs, kv)
    indicators/
      pine.py         TradingView ta.* primitives with exact Pine semantics
      ghost.py        Ghost Candle with Shadow Rail — Pine v6 → Python port
    exchange/
      binance.py      USDT-M REST + websockets (orders, protection, user stream)
      sim.py          simulation exchange (offline demo & rehearsal)
      hub.py          MarketHub — candles, universe ranking, scanner allocation, health
  tests/              120 tests covering every safety-critical rule
frontend/             React + TypeScript + Tailwind + three.js dashboard
  src/components/hq/  the 3D headquarters: layout (floor plan + cast), furniture,
                      Human (the rigged body), HQ (room, camera, live boards),
                      HQPanel (agent card), DetailToggle, RenderpeopleCredits
  public/models/people/  drop-in slot for licensed Renderpeople scans (empty by default)
  scripts/hq-audit.mjs   asserts the floor plan against the live roster
scripts/
  audit.py            dead code / duplication audit (--strict for CI)
  backup.py           online SQLite snapshots for a running engine
Dockerfile            multi-stage production image (Node build → Python runtime)
docker-compose.yml    single-replica stack: token, volume, healthcheck
.github/workflows/ci.yml   pyflakes + audit + tests + build + live dashboard checks
data/                 runtime state (git-ignored): config.json, journal, encrypted keys
```

## How the money is accounted

The dashboard never guesses. Every closed trade carries a `pnl_source`:

| source | meaning |
|---|---|
| `fills` | the exchange reported the real fills — gross, fees and funding come straight from it |
| `estimated` | no fills were visible, so P&L is inferred from prices (flagged amber in the UI) |
| `unknown` | a position vanished with no fill history — recorded as 0 and **excluded from the win rate** |

The Main Page always satisfies

```
exchange equity  =  starting balance + released P&L + unrealised − entry fees of open positions
```

A test (`test_equity_bridge_reconciles_with_the_exchange`) asserts that identity, and a
regression test (`test_reconciliation_never_invents_pnl`) proves the journal can never
fabricate a number when it restarts and finds a position gone.

The journal is also checked against the **venue's own ledger**. `GET /api/reconcile`
compares the net P&L booked from closed trades (minus the entry fees of positions that
are still open) with the exchange income ledger — `REALIZED_PNL − COMMISSION +
FUNDING_FEE` on Binance, the fill history on the simulator. The Main Page shows this as
the *Ledger check* strip; the maintenance loop logs a warning the moment the two drift
apart beyond rounding, so a mis-booked trade cannot hide.

The paper account is snapshotted on every equity tick *and* on shutdown, and a restart
re-adopts open positions, protective orders, cumulative realized P&L and prices, so
cancelling a demo run never loses a trade
(`test_restart_loses_nothing_from_the_paper_account`).

Fees are booked where they are paid: the **entry commission is charged to the trade
when it opens** (`trades.entry_fee`), the exit leg when it closes. A position that is
opened in one run and closed in the next therefore still reports its full cost — before
this the released P&L silently missed the entry leg of every restarted trade
(`test_entry_fee_is_booked_even_when_the_close_happens_after_a_restart`).

## Production

The engine is a **single process on purpose** (in-process state, one SQLite writer): run exactly
one replica per data volume, and keep `stop_grace_period` long enough for the shutdown snapshot.

```bash
# container (recommended)
export SHADOW_RAIL_API_TOKEN=$(openssl rand -hex 32)     # openssl rand -hex 32
docker compose up -d --build                             # binds 127.0.0.1:8080
docker compose logs -f                                   # one line per engine event

# or a bare host
pip install -r backend/requirements.txt
cd backend && SHADOW_RAIL_API_TOKEN=$TOKEN python -m app.main
```

Checklist — every item is implemented in this repo, `PRODUCTION_AUDIT.md` is the evidence:

| Area | What to do |
|---|---|
| Access | set `SHADOW_RAIL_API_TOKEN`; keep the port on localhost/behind TLS + a reverse proxy; the token is pasted once into the dashboard lock screen and kept in that browser |
| Keys | Binance key with **Read + Trade** only, IP-restricted to the server, never withdrawal permission; test on **testnet** first (Settings → Endpoint) |
| Data | `data/` (or the `shadow-rail-data` volume) holds the journal, the encrypted keys and the Fernet key — back it up and never commit it |
| Backups | `python scripts/backup.py` from cron — online, consistent snapshots under `data/backups/`, `SHADOW_RAIL_BACKUP_KEEP` prunes (see *Restore* inside the script) |
| Monitoring | `GET /api/health` for liveness; the engine's own SOS state drives the dashboard colour; process logs on stdout (journald/`docker logs`) |
| Upgrades | rebuild, then `docker compose up -d` (or restart): the engine re-adopts open positions and protective orders from the journal + venue |
| Rollback | stop the container, restore the newest `data/backups/*.sqlite3` over `shadow_rail.sqlite3` (delete stale `-wal`/`-shm`), start the previous image |
| CI | `.github/workflows/ci.yml` runs pyflakes + the repo audit + 120 tests + type-check/build + the live dashboard checks on every push |

### Restore a backup

```bash
docker compose stop shadow-rail
docker run --rm -v shadow-rail-data:/data -v $PWD/data/backups:/backups alpine \
  sh -c 'cp /backups/shadow_rail-YYYYMMDD-HHMMSS.sqlite3 /data/shadow_rail.sqlite3 && rm -f /data/shadow_rail.sqlite3-*'
docker compose start shadow-rail
```

## Running offline

Without Binance keys the engine boots the **simulation broker**: 150 assets, real
indicator maths, real risk/verification pipeline, accelerated clock (one 5-minute candle
every ~10 s) and a momentum/volatility-regime price model. The paper account is
persisted, so restarts continue where they left off instead of resetting the demo.

## Safety model

1. `data/config.json` + `data/.secret.key` — API secrets are encrypted at rest (Fernet, 0600).
2. Every REST call is pre-charged against a sliding 60-second window; at the ceiling **nobody**
   may send anything, and new entries are refused. Protective closes are always allowed so a
   position can never be trapped by a rate limit.
3. If the stop cannot be placed on the exchange, the engine **flattens the position immediately**
   rather than holding it unprotected.
4. Journal ↔ exchange reconciliation on every restart: phantom positions are marked closed, and
   `GET /api/reconcile` compares the journal with the venue's own income ledger continuously.
5. Fees are booked where they are paid (entry commission at open, exit commission at close), so a
   trade that spans a restart still reports its full cost.
6. The ROI trail can only ever **tighten** a stop; if the exchange rejects a trail update the engine
   restores the previous stop, and if that fails too it flattens the position rather than leave it naked.
7. Emergency **FLATTEN ALL** button in the sidebar; `pause` stops new entries without disturbing
   the monitors.
8. **Access control** — set `SHADOW_RAIL_API_TOKEN` for any deployment that holds real keys. Without
   it any host that can reach the port can trade; with it the console unlocks from that token and
   the shell never inlines account state (see *Production* below).
9. **Drift, split honestly** — `/api/reconcile` compares trading P&L (realised minus fees) between
   journal and venue, exactly, and reports funding on its own line because the venue bills it on
   open positions while the journal books it at close. Cash movements (deposits, rebates) are a
   third bucket and can never look like profit.

## The Command Deck

The main page is deliberately one thing: the **3D AI Trading Bot Headquarters** — a trading floor
you can fly around, where all 29 agents have a body, a desk and a job.  Nothing else is on it: no
cards, no charts, no panels.  The six numbers (and the rest of the money) live on the **Account**
page, and the app header carries only system state — the API-weight bar and the connection chip.
Transient notifications (the top win/loss banner, the toasts) expire after **3 seconds**; the SOS
banner stays up for as long as the connection is broken.  Everything lives in the menu:

| Page | What is there |
|---|---|
| Command Deck | the 3D headquarters only — short department labels, head-only name badges, no ticker/counters, click agents, fly the floor |
| Account | starting balance · current equity · opened positions · realised P&L · fees paid · win rate, the equity sheet, growth, costs and the ledger check |
| Open Positions | live trades, per-position ROI / peak ROI, the ROI trail lock, ledger check |
| Market Scan | scanner bots × assets |
| Closed Trades | history, P&L charts, the celebration tape of the newest closes |
| Bot Roster | every agent + the pipeline-stage rail |
| Workflow Log | the full audit trail |
| Settings | keys + IP, risk/TP-SL (incl. the ROI trail), indicator, engine, advanced, access token |

The sidebar starts **collapsed** — logo plus short labels — and the **⋯ button at the top** expands
the full menu with the engine controls.

### The headquarters (the 3D work zone)

A 48 × 30 m floor with a 10 m ceiling.  Each bay of the floor is a station of the engine's
workflow, and the agents in it are the bots that own that stage:

| Bay | Who is there | What it is |
|---|---|---|
| Command Deck (dais) | CEO · Connector · API Guard | the CEO's podium and the two bridge consoles; the maintenance bot walks a patrol loop |
| Scanner Bay (west wall) | VEGA … ATLAS | five desks, one per volatility group, each with a live board |
| Analyst Wing | the ten Market Analysts | two rows of desks facing the big board |
| Execution Pods | BOLT · TITAN | curved consoles under the hanging execution board |
| Verification Gate | ECHO | the arch every execution walks through before it counts |
| Monitor Wall (east wall) | the four trade monitors | a four-panel video wall, one desk each |
| Vault & Ledger | LEDGER · BANKER · AEGIS · RISK | a vault door with the equity hologram floating over it |

* **Click anything** — an agent, a screen, a floor ring, a station chip — to fly the camera there.
  Clicking an agent opens its card (live task and progress, mood, score, per-bot stats, its own log
  tail, *locate* and *promote* buttons) straight from `/api/bots/{id}`.
* **Rail pulses**: when the engine hands work from one team to the next, the rail between those two
  bays lights up and a pulse travels it, and the two agents it touched react.
* **Mood is the bot status** — the floor celebrates a win (sparkles over the winning agent) and
  slumps after a stop, exactly like the roster says.
* **The big board** and the four monitor panels cycle *overview · workflow rail · risk governor ·
  equity manager*; they are drawn from the same numbers as the six cards, so nothing on the wall can
  disagree with the ledger.  Click a board to change its view.
* **Rendering quality** — *cinema / balanced / speed*, remembered per browser under
  `shadow-rail.hq.detail`; *speed* drops reflections, shadows and geometry detail for older
  laptops.  The node-graph view from earlier builds is still there as the **swarm map** toggle.
* **The floor is lit to be read** — *every* station view is kept visible on purpose, so no part of
  the room is a black silhouette: exposure (`toneMappingExposure`) sits at `1.16` (lowered to
  `1.02` when the connector is in SOS), ambient + hemisphere + two directionals fill the hall,
  five of the nine ceiling bars carry real point lights, each station has one accent-tinted light
  above it, and the shell uses `#1d2635` walls, a `#232c3c`/`#374660` furniture palette, `#28344a`
  suits and a `#2d3a4d` floor grid, with a ledge and truss so the room has edges instead of an
  empty sky.  Station cameras were re-aimed to ~20 m so a zone fits the frame whole.  The floor grid
  is architectural only — `ui:check` asserts it is never fed trade or equity data.
* **Nothing is raised over the floor** — there is no top notification banner, and no ticker or
  counter row above the 3D view either (the deck is the work zone, full height).  A win or a loss is
  stated on the floor itself (the swarm's mood, the sparkles over the winning agent, the board),
  and the toasts that remain expire in exactly `NOTIFY_MS = 3000` ms (see
  `frontend/src/state/store.tsx`).  `npm run ui:check` asserts both rules against the source,
  because minification would hide the timer in the bundle.
* **The floor names departments, not dossiers** — each of the seven bays carries only its short
  department label (`COMMAND · SCAN · ANALYST · EXECUTE · VERIFY · MONITOR · FINANCE`, from
  `STATIONS[].short`) and only the *head* of each department wears a name badge
  (`headsFor()` in `hq/layout.ts` picks the highest rank, then the best score).  Any other agent
  still responds to a click and opens its card; the wall boards carry three headline rows each, so
  the 3D view reads as an office rather than a spreadsheet.

**Connector severity** — `_connector_verdict()` classifies a probe into four levels, and only the
last two can raise anything over the dashboard: `none` (green, verified link), **`notice`** (no API
keys stored yet, so the simulator is doing the work — the header shows a small `◈ simulator` chip,
nothing is raised, nothing is paused), `warning` (a *configured* link is degraded — amber banner)
and `critical` (a configured/real link is down — the ☠️ SOS siren, the dashboard turns red and
trading halts).  A keyless install therefore never looks broken, and a real outage still cannot hide.

The menu follows the same rule: collapsed, the rail is **symbols only**; the `⋯` button at the top
expands it into the full labels + hints.  No auto-promotion popup, no banner, no stray text on the
floor.

### The trading office (promotion, hiring, firing)

The floor is staffed like an office, and the roster is a career, not a label:

* **Promotion every 20 completed units** — Recruit → Operative → Specialist → Elite → Legend.  The
  unit is a *closed trade the seat carried* (scanner / analyst / execution / monitor) or a *served
  workflow cycle* (CEO, connector, API guard, verifier, finance).  `PROMOTE_EVERY = 20` lives in
  `backend/app/bots.py`; the API exposes it on every bot (`unit`, `completed_units`,
  `next_level_in`, `capacity`).
* **A level-up changes what the bot is trusted with** — `capacity = base(group) × (1 + 0.20 × level)`.
  After every promotion the engine reallocates work: `_split_universe_by_rank()` deals the
  volatility-ranked universe across the scanner desks *in proportion to rank* (a Specialist sweeps
  ~40 % more than a Recruit, and the total is unchanged), and `_assign_monitor_for()` seats each
  position with the desk whose capacity is least loaded.
* **The reliability gate** — a bot whose failure ratio is above 30 % (after 10 tasks) is *held* at
  its rank until it recovers, however many units it has banked.
* **Hiring and firing** — an agent with 20+ tasks, 12+ failures and a failure ratio above 55 % is
  relieved of duty.  The **seat** keeps its id — every link, desk and layout is keyed by it — and
  the office hires a fresh agent into the same desk at Recruit, with a new call-sign from
  `CANDIDATES`.  Fired agents stay on the record (`retired`), and the floor is never short-staffed.
* **Persistence** — the roster (ranks, hires, firings, counters) is written to the `kv` store
  (`office.roster`) every maintenance round and on shutdown, and restored on boot.
* **API** — `GET /api/office` returns the ladder, the rules, the retired list and the office log;
  `GET /api/bots` carries the same summary; the boot frame inlines it so the roster paints at once.
  `POST /api/bots/{id}/promote` is an operator *merit* rank — a bonus level the 20-unit rule will
  not undo.
* **On screen** — *Bot Roster → Trading office* shows the per-department levels, the promotion rule
  and the office record (promotions, holds, hires, firings); every agent card carries its level,
  its progress to the next rank and the load it is trusted with.

### Renderpeople integration

The bodies are modelled on the [Renderpeople](https://renderpeople.com/3d-people) office-people
catalogue — sitting, typing, standing and walking poses, business and smart-casual clothing, and the
full range of builds, ages and ethnicities.  Those scans are **paid, licensed assets**, so none of
their geometry is redistributed here: `buildLook()` derives a deterministic body from the agent id
instead, which is why the same agent always looks the same (and why all 29 of them differ).

If you own a licence, drop the scans in and the room uses them instead:

```bash
mkdir -p frontend/public/models/people
cp ceo-bot.glb frontend/public/models/people/          # one file per bot_id
VITE_HQ_SCANS=on npm run build
```

Each GLB is cloned per agent, normalised to that agent's height, and falls back to the procedural
body if the file is missing or broken (one error boundary per agent, so a single bad scan can never
take the floor down).  FastAPI serves `/models/*` with `Cache-Control: immutable`
(`_asset_cache_headers` in `backend/app/api.py`) and only mounts the route when the folder exists —
a missing scan 404s instead of silently returning the SPA shell.  Nothing is ever fetched from
renderpeople.com at runtime; the dashboard has no external network dependency.

### Never see a stale dashboard again

The shell is served `Cache-Control: no-store` and every asset URL is stamped with the build time, so
a browser or proxy cannot hand you yesterday's bundle.  `npm run ui:check` follows the exact URL a
browser would load and asserts all of the above against the **rendered** Command Deck — run it after
any rebuild.

## Credits

* Indicator: **Ghost Candle with Shadow Rail** © ChartTrader-X (MPL-2.0) —
  <https://www.tradingview.com/script/AY5Gz97v-Ghost-Candle-with-Shadow-Rail-Px/>
* Human reference for the headquarters cast: **Renderpeople** office-people catalogue —
  <https://renderpeople.com/3d-people> (taxonomy only; no scans redistributed)
* Engine & dashboard: **badalworld**

⚠️ **Trading futures with leverage can lose money faster than you can earn it.** Start on
testnet or with a small account, keep the risk settings conservative, and remember that past
performance never guarantees future results.
