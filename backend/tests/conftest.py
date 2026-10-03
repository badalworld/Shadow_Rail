"""Isolate every test run in its own data directory (never touches live state)."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

TMP_DATA = Path(tempfile.mkdtemp(prefix="shadowrail-test-"))
os.environ["SHADOW_RAIL_DATA_DIR"] = str(TMP_DATA)

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

import pytest  # noqa: E402


@pytest.fixture
async def db():
    from app.db import DB

    await DB.connect()
    for table in ("trades", "trade_events", "equity_curve", "logs", "bot_stats", "kv"):
        await DB.conn.execute(f"DELETE FROM {table}")
    await DB.conn.commit()
    yield DB
    await DB.close()


@pytest.fixture
def store():
    from app.config import AppConfig, ConfigStore

    path = TMP_DATA / "test_config.json"
    s = ConfigStore(path=path)
    s._cfg = AppConfig()
    s.save()
    return s
