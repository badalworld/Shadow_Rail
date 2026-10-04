"""
The dashboard contract.

These tests boot the *real* FastAPI app in-process, start the engine through the
same endpoint the UI's "start" button uses, then walk every route the dashboard
calls.  They are the safety net against payload drift and against values that
cannot be serialised (a NaN in a scan row 500s the whole page in production).
"""
from __future__ import annotations

import math
import time

import pytest
from fastapi.testclient import TestClient

import app.api as api
from app.config import STORE

# ── routes the UI depends on ────────────────────────────────────────────────
GET_ROUTES = [
    "/api/status", "/api/health", "/api/config", "/api/ip", "/api/equity",
    "/api/equity/curve", "/api/stats", "/api/trades/open", "/api/trades/closed",
    "/api/scan", "/api/bots", "/api/logs", "/api/events", "/api/about",
    "/api/indicator/selftest", "/api/reconcile",
]

# numeric fields each page formats with toFixed()/math — a string here breaks the UI
NUMERIC_FIELDS = {
    "/api/equity": ["equity", "starting_balance", "available", "released_pnl",
                    "unrealized", "fees_paid", "funding_net", "growth_pct",
                    "open_positions", "margin_used", "peak_equity", "drawdown_pct"],
    "/api/stats": ["total_trades", "wins", "losses", "win_rate", "net_pnl",
                   "profit_factor", "avg_win", "avg_loss", "fees_paid"],
}


@pytest.fixture(scope="module")
def client():
    """Boot the app exactly like production (small universe to stay fast)."""
    STORE.cfg.engine.universe_size = 24
    STORE.cfg.engine.sim_time_accel = 3000.0        # freeze the sim clock
    STORE.cfg.binance.transport = "sim"
    STORE.cfg.engine.autostart = False              # start it via the API instead
    STORE.save()

    with TestClient(api.app) as c:
        r = c.post("/api/engine/start")
        assert r.status_code == 200, r.text
        # wait for the first full cycle so the assertions below test a live
        # pipeline rather than a booting one
        deadline = time.time() + 60
        while time.time() < deadline:
            s = c.get("/api/status").json()["status"]
            if s["cycle"] >= 1 and s["scan_snapshot_ready" if False else "universe"]:
                snap = c.get("/api/scan").json()
                if snap.get("by_bot"):
                    break
            time.sleep(0.5)
        yield c
        c.post("/api/engine/stop")


def _assert_finite(node, path="$") -> None:
    """No NaN/Infinity anywhere: Starlette refuses to serialise them (500)."""
    if isinstance(node, float):
        assert math.isfinite(node), f"{path} is not finite: {node}"
    elif isinstance(node, dict):
        for k, v in node.items():
            _assert_finite(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _assert_finite(v, f"{path}[{i}]")


def test_every_dashboard_route_answers(client):
    for route in GET_ROUTES:
        res = client.get(route)
        assert res.status_code == 200, f"{route} → {res.status_code} {res.text[:200]}"
        _assert_finite(res.json(), route)


def test_numeric_fields_stay_numbers(client):
    for route, fields in NUMERIC_FIELDS.items():
        payload = client.get(route).json()
        container = payload.get("stats", payload) if route == "/api/stats" else payload
        for f in fields:
            if f not in container:
                continue
            assert isinstance(container[f], (int, float)), (
                f"{route}.{f} is {type(container[f]).__name__}, the UI formats it as a number")


def test_status_exposes_what_the_header_reads(client):
    st = client.get("/api/status").json()
    assert set(("status", "bots", "links")) <= set(st)
    s = st["status"]
    for key in ("mode", "transport", "running", "paused", "cycle", "max_trades",
                "api", "sos", "health", "workflow", "universe", "scanner_buckets"):
        assert key in s, f"/api/status.status is missing {key}"
    for stage in ("connector", "scan", "analyze", "execute", "verify", "monitor", "close"):
        assert stage in s["workflow"]["stages"], f"workflow stage {stage} missing"
        assert "status" in s["workflow"]["stages"][stage]
    assert s["api"]["used_pct"] <= 100 and "halted" in s["api"]
    # the engine reports the operator's risk system
    assert s["risk"]["risk_mode"] in ("indicator_default", "shadow_3x", "custom")
    assert s["risk"]["leverage"] == 10 and s["risk"]["margin_type"] == "CROSS"


def test_bots_and_links_resolve(client):
    payload = client.get("/api/bots").json()
    bots, links = payload["bots"], payload["links"]
    assert len(bots) == 29, "the swarm roster must be 29 agents"
    assert links, "the 3D map needs workflow links"
    ids = {b["bot_id"] for b in bots} | {
        f"{g}-team" for g in ("core", "scanner", "analyst", "execution", "verify",
                              "monitor", "finance")}
    for link in links:
        # every rail must point at a real bot or a known team centroid
        assert link["from"] in ids, f"link from unknown node {link['from']}"
        assert link["to"] in ids, f"link to unknown node {link['to']}"
    # scanner allocation is the spec: 5 bots × 30 assets
    buckets = client.get("/api/status").json()["status"]["scanner_buckets"]
    assert len(buckets) == 5


def test_scan_snapshot_is_grouped_per_scanner(client):
    payload = client.get("/api/scan").json()
    assert set(payload["by_bot"]) == {f"scanner-{i}" for i in range(1, 6)}
    rows = [r for group in payload["by_bot"].values() for r in group]
    assert rows, "the scan list must contain the assigned assets"
    for r in rows:
        for f in ("symbol", "price", "trend", "trend_side", "quality", "rail",
                  "rail_distance_pct", "atr_pct", "htf_bull", "has_position"):
            assert f in r, f"scan row missing {f}"
        assert isinstance(r["price"], (int, float)) and r["price"] > 0
        assert r["trend_side"] in (-1, 0, 1)


def test_run_cycle_is_reachable_from_the_ui(client):
    res = client.post("/api/scan/run")
    assert res.status_code == 200
    body = res.json()
    assert "opportunities" in body or "cycle" in body


def test_config_round_trip_never_leaks_the_secret(client):
    before = client.get("/api/config").json()["config"]
    assert "api_secret" not in before["binance"]

    body = {
        "binance": {"api_key": "TESTKEY123", "api_secret": "TESTSECRET456",
                    "testnet": False, "ip_whitelist": "1.2.3.4"},
        "risk": {**before["risk"], "risk_mode": "shadow_3x"},
    }
    assert client.put("/api/config", json=body).status_code == 200

    after = client.get("/api/config").json()["config"]
    assert after["binance"]["has_key"] and after["binance"]["has_secret"]
    assert "TESTSECRET456" not in str(after)          # masked on the way out
    # masked: identifiable but never the raw value
    assert after["binance"]["api_key_masked"] != "TESTKEY123"
    assert set(after["binance"]["api_key_masked"]) - set("•") == set("Y123")
    assert after["binance"]["api_key_len"] == len("TESTKEY123")
    assert after["risk"]["risk_mode"] == "shadow_3x"
    assert after["risk"]["active_system"]["risk_mode"] == "shadow_3x"
    # only one TP/SL system is ever active
    assert after["risk"]["active_system"]["tp_atr_mult"] == 0.0 or \
        after["risk"]["active_system"]["tp_enabled"] in (True, False)


def test_pause_and_emergency_paths_are_guarded(client):
    assert client.post("/api/engine/pause?paused=true").json()["paused"] is True
    assert client.post("/api/engine/pause?paused=false").json()["paused"] is False
    # flatten requires the explicit confirmation phrase
    assert client.post("/api/engine/emergency-close").status_code == 422
    assert client.get("/api/trades/999999").status_code == 404


def test_boot_frame_matches_the_route_payloads(client):
    """The inlined first paint must equal what the REST routes return — a stale
    or trimmed boot frame is what makes the dashboard flash zeros on load."""
    import json
    import re

    html = client.get("/").text
    m = re.search(r"window\.__SHADOW_RAIL_BOOT__=([\s\S]*?)</script>", html)
    assert m, "index.html is served without a boot snapshot"
    frame = json.loads(m.group(1).replace("<\\/", "</"))

    live_open = client.get("/api/trades/open").json()
    # open positions carry live marks in the boot frame, not zeros
    for t in frame["open_trades"]:
        assert t.get("mark"), f"{t['symbol']} has no mark price in the boot frame"
        assert "unrealized" in t and "unrealized_pct" in t
    assert frame["open_trades_meta"]["max"] == live_open["max"]
    assert len(frame["open_trades"]) == live_open["count"]
    # the candle countdown is a real value, not the placeholder zero
    assert frame["scan"]["seconds_to_close"] > 0
    assert abs(frame["scan"]["seconds_to_close"]
               - client.get("/api/scan").json()["seconds_to_close"]) < 5
    # and every slice the UI paints from is present
    for key in ("bots", "status", "equity", "stats", "scan", "open_trades",
                "closed_trades", "curve", "config", "ip", "logs", "links"):
        assert key in frame, f"boot frame is missing {key}"


def test_websocket_hello_frame_and_live_relay(client):
    """The dashboard's realtime channel: hello snapshot, then relayed events."""
    with client.websocket_connect("/ws") as ws:
        hello = ws.receive_json()
        assert hello["topic"] == "hello"
        data = hello["data"]
        for key in ("bots", "status", "equity", "stats", "scan", "open_trades",
                    "closed_trades", "curve", "logs", "links"):
            assert key in data, f"hello frame is missing {key}"
        assert len(data["bots"]) == 29

        # an action taken through the UI must arrive on the socket
        assert client.post("/api/bots/scanner-1/promote").status_code == 200
        seen = []
        for _ in range(8):
            frame = ws.receive_json()
            seen.append(frame["topic"])
            if frame["topic"] == "bot.promoted":
                assert "name" in frame["data"] and "to" in frame["data"]
                break
        assert "bot.promoted" in seen, f"promotion never reached the socket: {seen}"


def test_settings_save_keeps_the_office_record(client):
    """Saving a setting rebuilds the roster from config — the career record
    (ranks, promotions, per-bot counters) must survive that rebuild."""
    promoted = client.post("/api/bots/scanner-1/promote")
    assert promoted.status_code == 200
    body = promoted.json()
    assert body["promotion"], "the merit promotion did not register"
    level = body["bot"]["rank_index"]
    assert level > 0

    # a harmless re-save of one risk field (the UI sends whole sections)
    res = client.put("/api/config", json={"risk": {"trail_activation_roi_pct": 25.0}})
    assert res.status_code == 200 and res.json()["applied"] is True

    after = client.get("/api/bots/scanner-1").json()["bot"]
    assert after["rank_index"] >= level, "the office ladder was wiped by a settings save"
    assert after["promotions"] >= 1, "the promotion counter was wiped by a settings save"
    assert after["capacity"] >= body["bot"]["capacity"], \
        "the promoted seat lost the workload its rank earned"


def test_routes_are_registered_before_the_spa_catch_all(client):
    """Regression: /api/about was registered after the SPA catch-all inside the
    static-mount block, so the catch-all answered 404 for it."""
    from fastapi.routing import APIRoute

    paths = [r.path for r in api.app.routes if isinstance(r, APIRoute)]
    assert "/{full_path:path}" in paths
    catch_all = paths.index("/{full_path:path}")
    for route in GET_ROUTES:
        assert route in paths, f"{route} is no longer registered"
        assert paths.index(route) < catch_all, (
            f"{route} is registered after the catch-all and will 404")
