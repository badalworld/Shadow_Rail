"""
FastAPI application — REST + realtime websocket for the Shadow Rail dashboard.

Everything the UI shows comes from here; the websocket multiplexes the event
bus so the dashboard is genuinely live (ticks, bot states, trades, logs, SOS).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import re
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .bots import PROMOTE_EVERY, build_registry, workflow_links
from .bus import BUS
from .config import STORE, WEB_DIR
from .db import DB
from .engine import TradingEngine, get_engine
from .exchange.base import ExchangeError
from .exchange.binance import BinanceFutures
from .indicators import ghost
from .journal import JOURNAL
from .ratelimit import GOVERNOR
from .risk import roi_points
from .util import now_ms

app = FastAPI(title="Shadow Rail API", version="1.0.0",
              description="Automatic Trading Engine for Binance USDT-M Futures")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],            # dashboard may be served from the Vite dev server
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ENGINE: TradingEngine | None = None
DEV = STORE.cfg.developer
# the autostart task must be referenced for the life of the process: asyncio
# keeps only a weak reference to a task, so an unreferenced one can be collected
# before the engine ever starts
_BOOT_TASK: asyncio.Task | None = None


@app.on_event("startup")
async def _startup() -> None:
    global ENGINE, _BOOT_TASK
    await DB.connect()
    ENGINE = get_engine()
    BUS.publish("system.boot", {"at": now_ms(), "version": app.version})
    if STORE.cfg.engine.autostart:
        _BOOT_TASK = asyncio.create_task(_autostart())


async def _autostart() -> None:
    await asyncio.sleep(1.0)
    with contextlib.suppress(Exception):
        await ENGINE.start()


@app.on_event("shutdown")
async def _shutdown() -> None:
    if ENGINE and ENGINE.running:
        with contextlib.suppress(Exception):
            await ENGINE.stop()
    await DB.close()


def eng() -> TradingEngine:
    if ENGINE is None:
        raise HTTPException(503, "engine not initialised")
    return ENGINE


# ═══════════════════════════════════════════════════════════════ status
@app.get("/api/status")
async def status() -> dict:
    e = eng()
    return {
        "status": e.status(),
        "equity": e.last_equity,
        "bots": e.registry.snapshot(GOVERNOR.snapshot()),
        "links": workflow_links(),
        "server_time": now_ms(),
    }


@app.get("/api/health")
async def health() -> dict:
    return {"engine": eng().health, "sos": eng().sos, "api": GOVERNOR.snapshot(),
            "transport": eng().hub.transport, "mode": eng().hub.mode}


@app.post("/api/health/probe")
async def probe_now() -> dict:
    """Force the Connector Bot to re-check the link immediately."""
    e = eng()
    e.registry.set_status("connector-bot", "working", "manual probe requested")
    try:
        h = await asyncio.wait_for(e.hub.health(deep=True), timeout=25)
    except Exception as exc:
        raise HTTPException(502, f"probe failed: {str(exc)[:200]}")
    e.health = h
    BUS.publish("connector.health", h)
    if not h.get("connected"):
        e._raise_sos("critical", h.get("problems") or ["probe failed"])
    else:
        e._clear_sos()
    e.log("info", "connector-bot", "Manual connection probe: " +
          ("healthy" if h.get("connected") else "; ".join(h.get("problems", []))[:160]))
    return h


# ══════════════════════════════════════════════════════════════ settings
@app.get("/api/config")
async def get_config() -> dict:
    return {"config": STORE.public_view(), "developer": DEV.model_dump()}


@app.put("/api/config")
async def put_config(patch: dict = Body(...)) -> dict:
    cfg = STORE.update(patch)
    e = eng()
    unknown = STORE.unknown_keys(patch)
    # hot-apply the pieces that can change at runtime
    e.risk = type(e.risk)(cfg.risk)
    # Rebuild the roster from the new config (seat counts can change), but
    # re-apply the office record afterwards: ranks, promotions, hires and the
    # per-bot stats are career data and must survive a settings save.
    saved_office = e.registry.state()
    e.registry = build_registry(cfg)
    with contextlib.suppress(Exception):
        e.registry.restore(saved_office)
    with contextlib.suppress(Exception):
        e._rebalance_workload()
    e.registry.publish_all(GOVERNOR.snapshot())
    GOVERNOR.limit_per_min = cfg.engine.api_weight_limit_per_min
    GOVERNOR.budget_pct = cfg.engine.api_budget_pct
    GOVERNOR.allow_critical_above_cap = cfg.engine.allow_critical_above_cap
    e.log("info", "ceo-bot", "Settings updated from the dashboard",
          {"sections": list(patch.keys())})
    BUS.publish("config.updated", {"sections": list(patch.keys()),
                                   "unknown": unknown})
    if unknown:
        e.log("warn", "ceo-bot",
              f"Settings payload contained unknown keys (ignored): {unknown}")
    return {"config": STORE.public_view(), "applied": True, "unknown": unknown}


@app.post("/api/config/test-connection")
async def test_connection(payload: dict = Body(default={})) -> dict:
    """
    Validate API credentials (or the pending ones) without saving them.
    Returns balance, permissions, latency and precise error messages.
    """
    key = (payload.get("api_key") or "").strip() or STORE.api_key()
    secret = (payload.get("api_secret") or "").strip() or STORE.api_secret()
    testnet = bool(payload.get("testnet", STORE.cfg.binance.testnet))
    if not key or not secret:
        return {"ok": False, "error": "API key and secret are required"}
    client = BinanceFutures(key, secret, testnet=testnet, bot_id="connector-bot")
    out: dict[str, Any] = {"ok": False, "testnet": testnet}
    try:
        await client.start()
        out["latency_ms"] = round(await client.ping(), 1)
        out["server_time"] = await client.server_time()
        acct = await client.account()
        out["ok"] = True
        out["equity"] = acct.total_margin_balance or acct.total_wallet_balance
        out["available"] = acct.available_balance
        out["unrealized"] = acct.total_unrealized_pnl
        out["open_positions"] = acct.open_count
        out["weight_header"] = client.last_weight_header
        out["message"] = "Connection verified — Connector Bot link is green"
    except ExchangeError as exc:
        out["error"] = str(exc)
        out["code"] = exc.code
        out["hint"] = _hint_for_error(exc)
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {str(exc)[:220]}"
        out["hint"] = ("This host cannot reach fapi.binance.com. Check the server's "
                       "outbound network, or run the engine where Binance is reachable.")
    finally:
        with contextlib.suppress(Exception):
            await client.close()
    return out


def _hint_for_error(exc: ExchangeError) -> str:
    code = exc.code
    hints = {
        -2015: "Invalid API key, IP restriction or missing Futures permission. "
               "Create the key with 'Enable Futures' and whitelist the server IP "
               "shown on this page.",
        -1022: "Signature error — the secret key is wrong or has trailing spaces.",
        -1021: "Timestamp outside recvWindow — check the server clock (NTP).",
        -1003: "API weight ceiling reached on Binance's side — slow the engine down.",
        -4046: "Margin type could not be changed (no need to change) — harmless.",
        -2019: "Margin is insufficient for this order.",
        -4164: "Order notional below the exchange minimum (5 USDT).",
    }
    return hints.get(code or 0, "")


@app.post("/api/config/verify-ip")
async def verify_ip(payload: dict = Body(default={})) -> dict:
    """
    Confirm the engine's public IP so the operator can whitelist it on Binance.
    Verifies the account is reachable *with* the whitelist in place.
    """
    out = {"ip": await public_ip(), "whitelisted_ok": False}
    # remember whatever IP string the operator says they whitelisted
    if str(payload.get("ip_whitelist") or "").strip():
        STORE.update({"binance": {"ip_whitelist": str(payload["ip_whitelist"]).strip()}})
    key = (payload.get("api_key") or "").strip() or STORE.api_key()
    secret = (payload.get("api_secret") or "").strip() or STORE.api_secret()
    if key and secret:
        client = BinanceFutures(key, secret, testnet=STORE.cfg.binance.testnet,
                                bot_id="connector-bot")
        try:
            await client.start()
            await client.account()
            out["whitelisted_ok"] = True
            STORE.update({"binance": {"ip_whitelist_confirmed": True,
                                      "ip_whitelist": out["ip"]}})
            out["message"] = "This IP is accepted by Binance — whitelist confirmed"
        except ExchangeError as exc:
            out["message"] = str(exc)
            out["hint"] = _hint_for_error(exc)
        finally:
            with contextlib.suppress(Exception):
                await client.close()
    return out


@app.get("/api/ip")
async def ip_info() -> dict:
    import socket
    ip = await public_ip()
    host = ""
    with contextlib.suppress(Exception):
        host = socket.gethostname()
    return {
        "public_ip": ip,
        "hostname": host,
        "whitelist_confirmed": STORE.cfg.binance.ip_whitelist_confirmed,
        "whitelist_entry": STORE.cfg.binance.ip_whitelist,
        "instructions": [
            "Binance → API Management → your key → Edit restrictions",
            "Tick 'Restrict access to trusted IPs only' and paste the IP above",
            f"Region must match the endpoint the engine uses "
            f"({'testnet' if STORE.cfg.binance.testnet else 'live'}: fapi.binance.com)",
            "Enable 'Futures' (USDⓈ-M) permission on the key",
        ],
    }


async def public_ip() -> str:
    import httpx
    for url in ("https://api.ipify.org", "https://ifconfig.me/ip", "https://ipinfo.io/ip"):
        try:
            async with httpx.AsyncClient(timeout=6.0) as c:
                r = await c.get(url)
                if r.status_code == 200 and r.text.strip():
                    return r.text.strip()[:64]
        except Exception:
            continue
    return "unknown (no outbound network on this host)"


# ════════════════════════════════════════════════════════════════ equity
@app.get("/api/equity")
async def equity() -> dict:
    e = eng()
    state = e.last_equity or {**JOURNAL.state.as_dict(),
                              "margin_budget": round(JOURNAL.margin_budget(), 2)}
    stats = await JOURNAL.refresh_stats()
    return {
        **state,
        "stats": stats,
        "daily": await JOURNAL.daily_anchor(state.get("equity", 0.0)),
        "max_trades": STORE.cfg.risk.max_concurrent_trades,
        "open_slots": max(0, STORE.cfg.risk.max_concurrent_trades
                          - int(state.get("open_positions", 0))),
        "risk": e.risk.effective_exits(),
    }


@app.get("/api/equity/curve")
async def equity_curve(limit: int = 1200) -> dict:
    return {"points": await JOURNAL.equity_series(limit),
            "cumulative": await JOURNAL.cumulative_pnl(400),
            "by_day": await JOURNAL.pnl_by_day(30)}


@app.get("/api/reconcile")
async def reconcile() -> dict:
    """Journal vs exchange cash ledger — the dashboard's honesty check."""
    return await eng().reconcile_exchange()


@app.get("/api/stats")
async def stats() -> dict:
    return {"stats": await JOURNAL.refresh_stats(force=True),
            "by_day": await JOURNAL.pnl_by_day(30),
            "symbols": await JOURNAL.symbol_stats(force=True),
            "equity": JOURNAL.state.as_dict()}


# ════════════════════════════════════════════════════════════════ trades
async def open_trades_payload(e: TradingEngine) -> dict:
    """Live open positions (shared by GET /api/trades/open and the boot frame)."""
    out = []
    positions = {}
    with contextlib.suppress(Exception):
        positions = {p.symbol: p for p in await e.hub.positions()}
    for tid, t in e.open_trades.items():
        pos = positions.get(t["symbol"])
        mark = pos.mark_price if pos else e.hub.price(t["symbol"])
        entry = float(t["entry_price"])
        qty = float(t["qty"])
        upnl = (mark - entry) * qty * (1 if t["side"] == "LONG" else -1)
        margin = float(t.get("margin") or 0.0)
        side = t["side"]
        roi = roi_points(entry, mark, qty, margin, side) if margin else 0.0
        peak = float(t.get("peak_price") or entry)
        sl = float(t.get("sl_price") or 0.0)
        trail_on, activation, distance, _step = STORE.cfg.risk.trail()
        out.append({**{k: v for k, v in t.items() if k != "notes"},
                    "mark": mark, "unrealized": round(upnl, 4),
                    "unrealized_pct": round((upnl / max(1e-9, margin)) * 100.0, 2),
                    "liquidation_live": pos.liquidation_price if pos else t.get("liquidation_price"),
                    "monitor_id": t.get("monitor_bot_id"),
                    # ── ROI trail (arm at +25 %, stop 15 ROI behind the peak)
                    "roi_pct": round(roi, 3),
                    "peak_roi_pct": round(roi_points(entry, peak, qty, margin, side), 3)
                    if margin else 0.0,
                    "stop_roi_pct": round(roi_points(entry, sl, qty, margin, side), 3)
                    if (margin and sl) else 0.0,
                    "trail_enabled": trail_on,
                    "trail_activation_roi_pct": activation,
                    "trail_distance_roi_pct": distance,
                    "trail_active": bool(t.get("trail_active")),
                    "trail_stop": float(t.get("trail_stop") or 0.0)})
    return {"trades": out, "count": len(out),
            "max": STORE.cfg.risk.max_concurrent_trades}


@app.get("/api/trades/open")
async def open_trades() -> dict:
    return await open_trades_payload(eng())


@app.get("/api/trades/closed")
async def closed_trades(limit: int = Query(100, le=1000), offset: int = 0,
                        symbol: str | None = None, result: str | None = None,
                        reason: str | None = None) -> dict:
    rows = await DB.closed_trades(limit=limit, offset=offset, symbol=symbol,
                                  result=result, reason=reason)
    return {"trades": rows, "count": len(rows),
            "total": await DB.count_closed(),
            "stats": await JOURNAL.refresh_stats()}


@app.get("/api/trades/{trade_id}")
async def trade_detail(trade_id: int) -> dict:
    trade = await DB.get_trade(trade_id)
    if not trade:
        raise HTTPException(404, "trade not found")
    return {"trade": trade, "events": await DB.trade_events(trade_id)}


# ═════════════════════════════════════════════════════════════════ scan
async def scan_payload(e: TradingEngine) -> dict:
    """The scanner view (shared by GET /api/scan and the boot frame)."""
    snap = e.scan_snapshot
    return {
        "cycle": snap.get("cycle", 0),
        "updated_at": snap.get("updated_at", 0),
        "universe": e.hub.universe,
        "by_bot": snap.get("by_bot", {}),
        "opportunities": e.recent_analyst_rows[:40],
        "seconds_to_close": round(e.hub.seconds_to_close(), 1),
        "timeframe": STORE.cfg.engine.monitored_timeframe,
    }


@app.get("/api/scan")
async def scan() -> dict:
    return await scan_payload(eng())


@app.post("/api/scan/run")
async def run_cycle(force: bool = True) -> dict:
    return await eng().run_cycle(force=force)


# ═════════════════════════════════════════════════════════════════ bots
@app.get("/api/bots")
async def bots() -> dict:
    e = eng()
    return {"bots": e.registry.snapshot(GOVERNOR.snapshot()),
            "links": workflow_links(),
            "office": e.registry.office_summary(),
            "ranks": ["Recruit", "Operative", "Specialist", "Elite", "Legend"]}


@app.get("/api/office")
async def office() -> dict:
    """The trading office: the ladder, the record and the hiring rules."""
    reg = eng().registry
    return {
        "summary": reg.office_summary(),
        "rules": {
            "promote_every": PROMOTE_EVERY,
            "unit": "a closed trade for the trade seats, a served cycle for support seats",
            "hold": "a bot whose failure ratio is above 30% is held at its rank",
            "fire": "20+ tasks, 12+ failures and a failure ratio above 55% costs the seat",
            "hire": "the seat is re-filled with a fresh agent at Recruit, same desk",
        },
        "retired": reg.retired[:24],
        "log": reg.office_log[:40],
        "seats": [{"bot_id": b.bot_id, "name": b.name, "group": b.group,
                   "role": b.role, "rank": b.rank, "level": b.rank_index + 1,
                   "unit": b.unit, "completed_units": b.completed_units,
                   "next_level_in": b.next_level_in, "capacity": b.capacity,
                   "generation": b.generation, "founder": b.founder,
                   "fail_ratio": round(b.metrics.fail_ratio, 3),
                   "assigned": len(b.assigned)} for b in reg.all()],
    }


@app.get("/api/bots/{bot_id}")
async def bot_detail(bot_id: str) -> dict:
    bot = eng().registry.get(bot_id)
    if not bot:
        raise HTTPException(404, "unknown bot")
    rows = await DB.query_logs(limit=60, bot_id=bot_id)
    return {"bot": bot.as_dict(GOVERNOR.snapshot()), "logs": rows,
            "stats": next((s for s in await DB.bot_stats() if s["bot_id"] == bot_id), {})}


@app.post("/api/bots/{bot_id}/promote")
async def promote_bot(bot_id: str) -> dict:
    """Operator merit promotion — a bonus rank the 20-unit rule will not undo."""
    e = eng()
    bot = e.registry.get(bot_id)
    if not bot:
        raise HTTPException(404, "unknown bot")
    rec = e.registry.merit_promote(bot_id, "operator merit promotion")
    if rec:
        e._rebalance_workload()
    e.registry.publish(bot_id, GOVERNOR.snapshot())
    return {"bot": bot.as_dict(GOVERNOR.snapshot()), "promotion": rec}


# ═════════════════════════════════════════════════════════════════ logs
@app.get("/api/logs")
async def logs(limit: int = Query(200, le=2000), offset: int = 0,
               level: str | None = None, bot_id: str | None = None,
               topic: str | None = None, search: str | None = None) -> dict:
    rows = await DB.query_logs(limit=limit, offset=offset, level=level, bot_id=bot_id,
                               topic=topic, search=search)
    return {"logs": rows, "count": len(rows)}


@app.get("/api/events")
async def events(limit: int = 100, topic: str | None = None) -> dict:
    return {"events": BUS.recent(limit=limit, topic_prefix=topic)}


# ══════════════════════════════════════════════════════════════ engine ctl
@app.post("/api/engine/start")
async def engine_start() -> dict:
    e = eng()
    if not e.running:
        await e.start()
    return {"running": e.running, "transport": e.hub.transport, "mode": e.hub.mode}


@app.post("/api/engine/stop")
async def engine_stop() -> dict:
    e = eng()
    if e.running:
        await e.stop()
    return {"running": e.running}


@app.post("/api/engine/pause")
async def engine_pause(paused: bool = True) -> dict:
    e = eng()
    e.paused = paused
    e.log("warn" if paused else "info", "ceo-bot",
          f"Trading {'paused' if paused else 'resumed'} by operator")
    e.registry.set_status("ceo-bot", "idle" if paused else "working",
                          "paused by operator" if paused else "resumed")
    return {"paused": e.paused}


@app.post("/api/engine/emergency-close")
async def emergency_close(confirm: str = Query(...)) -> dict:
    if confirm != "FLATTEN":
        raise HTTPException(400, "confirm=FLATTEN required")
    return await eng().emergency_close_all("operator panic button")


@app.post("/api/engine/reload-risk")
async def reload_risk() -> dict:
    e = eng()
    e.risk = type(e.risk)(STORE.cfg.risk)
    return {"risk": e.risk.effective_exits()}


# ══════════════════════════════════════════════════════════ indicator test
@app.get("/api/indicator/selftest")
async def indicator_selftest() -> dict:
    res = await asyncio.to_thread(ghost.selftest)
    res["params"] = STORE.cfg.indicator.model_dump()
    return res


async def about_payload() -> dict:
    """Developer + project attribution (shared with the boot frame)."""
    return {
        "developer": DEV.model_dump(),
        "project": {
            "name": "Shadow Rail",
            "engine": "Automatic Trading Engine — Binance USDT-M Futures",
            "indicator": "Ghost Candle with Shadow Rail (GCSR) by ChartTrader-X",
            "indicator_url": "https://www.tradingview.com/script/AY5Gz97v-Ghost-Candle-with-Shadow-Rail-Px/",
            "timeframe": STORE.cfg.engine.monitored_timeframe,
            "htf_filter": f"{STORE.cfg.indicator.mtfFrame} EMA-{STORE.cfg.indicator.mtfEmaBars}",
            "mode": eng().hub.mode,
            "transport": eng().hub.transport,
            "api_budget_pct": STORE.cfg.engine.api_budget_pct,
            "bots": len(eng().registry.all()),
            "strategy": eng().risk.effective_exits(),
        },
    }


@app.get("/api/about")
async def about() -> dict:
    return await about_payload()


# ════════════════════════════════════════════════════════════════ websocket
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    queue = BUS.subscribe()
    try:
        await ws.send_text(json.dumps({
            "topic": "hello", "ts": now_ms(), "data": await boot_payload()},
            default=str))
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=12.0)
                await ws.send_text(json.dumps(event.to_dict(), default=str))
            except asyncio.TimeoutError:
                await ws.send_text(json.dumps({"topic": "ping", "ts": now_ms(),
                                               "data": {"api": GOVERNOR.snapshot()}}))
    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception:
        pass
    finally:
        BUS.unsubscribe(queue)


# ═════════════════════════════════════════════════════════ static dashboard
if WEB_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(WEB_DIR / "assets")), name="assets")
    # Licensed Renderpeople scans (and any other drop-in art) live here: the
    # route only exists when the folder does, so nothing 404s on a clean build.
    if (WEB_DIR / "models").exists():
        app.mount("/models", StaticFiles(directory=str(WEB_DIR / "models")), name="models")

    @app.middleware("http")
    async def _asset_cache_headers(request: Request, call_next):     # noqa: ANN001
        """Hashed bundles and static art are immutable; the shell is never cached."""
        response = await call_next(request)
        if request.url.path.startswith(("/assets/", "/models/")):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

    # every rebuild gets new asset filenames, and we add the build stamp on top
    # so no browser/proxy cache can ever serve yesterday's dashboard
    _ASSET_RE = re.compile(r'(/assets/[A-Za-z0-9_.\-]+\.(?:js|css|woff2?|png|jpg|svg))')

    async def render_index() -> Any:
        """Serve the SPA shell with a boot snapshot inlined so the first paint
        already shows live numbers (no flash of zeros before the socket opens)."""
        index = WEB_DIR / "index.html"
        if not index.exists():
            raise HTTPException(404, "dashboard bundle not built")
        html = index.read_text(encoding="utf-8")
        stamp = str(int(index.stat().st_mtime))
        html = _ASSET_RE.sub(lambda m: f"{m.group(1)}?v={stamp}", html)
        if "__SHADOW_RAIL_BOOT__" in html:
            return HTMLResponse(html, headers={"Cache-Control": "no-store"})
        try:
            boot = await boot_payload()
            payload = json.dumps(boot, default=str).replace("</", "<\\/")
            tag = f'<script>window.__SHADOW_RAIL_BOOT__={payload}</script>'
            html = html.replace("</head>", tag + "</head>", 1) if "</head>" in html \
                else tag + html
        except Exception:
            pass                       # a broken snapshot must never block the UI
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    @app.get("/{full_path:path}")
    async def spa(full_path: str, request: Request):
        if full_path.startswith("api/") or full_path == "ws":
            raise HTTPException(404, "not found")
        candidate = WEB_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate, headers={"Cache-Control": "no-store"})
        return await render_index()


async def boot_payload() -> dict[str, Any]:
    """The same frame the websocket hello sends — shared by `/` and `/ws`."""
    e = eng()
    scan = await scan_payload(e)
    open_trades = await open_trades_payload(e)
    return {"bots": e.registry.snapshot(GOVERNOR.snapshot()),
            "status": e.status(),
            "equity": e.last_equity or JOURNAL.state.as_dict(),
            "stats": await JOURNAL.refresh_stats(),
            "scan": scan,
            "open_trades": open_trades["trades"],
            "open_trades_meta": {"count": open_trades["count"], "max": open_trades["max"]},
            "closed_trades": {"trades": await DB.closed_trades(limit=100),
                              "total": await DB.count_closed()},
            "config": STORE.public_view(),
            "about": await about_payload(),
            "ip": await ip_info(),
            "curve": await JOURNAL.cumulative_pnl(limit=400),
            "logs": await DB.query_logs(limit=80),
            "links": workflow_links(),
            "office": {"summary": e.registry.office_summary(),
                       "retired": e.registry.retired[:24],
                       "log": e.registry.office_log[:40],
                       "rules": {"promote_every": PROMOTE_EVERY}}}


@app.get("/")
async def root() -> Any:
    index = WEB_DIR / "index.html"
    if index.exists():
        return await render_index()
    return JSONResponse({
        "service": "Shadow Rail API",
        "docs": "/docs",
        "hint": "Frontend bundle not built yet — run `npm run build` in /frontend",
    })
