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

---

## Quick start

```bash
# 1 — backend (Python 3.11+)
python3 -m venv .venv && . .venv/bin/activate
pip install -r backend/requirements.txt

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

# rebuild the dashboard bundle FastAPI serves
cd frontend && npm run build
```

Environment overrides: `SHADOW_RAIL_PORT` (default 8080), `SHADOW_RAIL_HOST`,
`SHADOW_RAIL_DATA_DIR` (default `<repo>/data`).

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
  tests/              79 tests covering every safety-critical rule
frontend/             React + TypeScript + Tailwind + three.js dashboard
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
re-adopts open positions, protective orders and prices, so cancelling a demo run never
loses a trade (`test_restart_loses_nothing_from_the_paper_account`).

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
4. Journal ↔ exchange reconciliation on every restart: phantom positions are marked closed.
5. Emergency **FLATTEN ALL** button in the sidebar; `pause` stops new entries without disturbing
   the monitors.

## Credits

* Indicator: **Ghost Candle with Shadow Rail** © ChartTrader-X (MPL-2.0) —
  <https://www.tradingview.com/script/AY5Gz97v-Ghost-Candle-with-Shadow-Rail-Px/>
* Engine & dashboard: **badalworld**

⚠️ **Trading futures with leverage can lose money faster than you can earn it.** Start on
testnet or with a small account, keep the risk settings conservative, and remember that past
performance never guarantees future results.
