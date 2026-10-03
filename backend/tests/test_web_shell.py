"""
The served dashboard shell.

What the browser loads must never be a build from several revisions ago — that
bug already bit once — and the 3D headquarters' drop-in scan art must be
cacheable without ever making a *missing* scan return the SPA shell (the room has
to be able to fall back to its procedural cast).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.api as api

WEB = Path(__file__).resolve().parents[1] / "web"
STAMPED = re.compile(r"/assets/index-[A-Za-z0-9_.-]+\.(?:js|css)\?v=\d+")


@pytest.fixture
def built_client():
    if not (WEB / "index.html").exists():
        pytest.skip("dashboard bundle not built")
    with TestClient(api.app) as c:
        yield c


def test_shell_is_never_cached_and_carries_a_stamped_bundle(built_client):
    res = built_client.get("/")
    assert res.status_code == 200
    assert res.headers["cache-control"] == "no-store"
    # the boot snapshot inlined into the shell saves the first paint from zeros
    assert "__SHADOW_RAIL_BOOT__" in res.text
    assert STAMPED.search(res.text), "asset URLs must carry the build stamp"


def test_every_spa_route_serves_the_same_uncached_shell(built_client):
    for route in ("/", "/trades", "/settings", "/bots/ceo-bot"):
        res = built_client.get(route)
        assert res.status_code == 200, route
        assert res.headers["cache-control"] == "no-store", route
        assert "<div id=\"root\"" in res.text or "index-" in res.text


def test_hashed_assets_are_immutable(built_client):
    shell = built_client.get("/").text
    asset = STAMPED.search(shell)
    assert asset, "shell references no bundle"
    res = built_client.get(asset.group(0))
    assert res.status_code == 200
    assert "immutable" in res.headers["cache-control"]


def test_scan_art_is_mounted_only_when_it_exists(built_client):
    """A missing scan must 404 (so the 3D room falls back), never the shell."""
    if not (WEB / "models").exists():
        pytest.skip("no drop-in model folder in this build")
    res = built_client.get("/models/people/does-not-exist.glb")
    assert res.status_code == 404
    readme = built_client.get("/models/people/README.md")
    assert readme.status_code == 200
    assert "immutable" in readme.headers["cache-control"]


def test_bundle_keeps_the_hq_drop_in_slot_off_by_default(built_client):
    """The dashboard must never fetch Renderpeople geometry at runtime."""
    shell = built_client.get("/").text
    asset = STAMPED.search(shell)
    if not asset:
        pytest.skip("no bundle")
    src = built_client.get(asset.group(0)).text
    assert "/models/people" in src, "the licensed-scan drop-in slot disappeared"
    assert "cdn.renderpeople" not in src
