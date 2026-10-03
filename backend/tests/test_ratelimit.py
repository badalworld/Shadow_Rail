"""API weight governor — the 95% ceiling must be absolute."""
from __future__ import annotations

import pytest

from app.ratelimit import GOVERNOR, RateLimitHalt, WeightGovernor


async def test_cap_is_95_percent_of_the_ip_budget():
    g = WeightGovernor(limit_per_min=2400, budget_pct=95.0)
    assert g.cap == 2280


async def test_blocks_the_moment_the_ceiling_is_reached():
    g = WeightGovernor(limit_per_min=100, budget_pct=95.0)
    spent = 0
    with pytest.raises(RateLimitHalt) as exc:
        for _ in range(200):
            await g.acquire("scanner-1", "account")      # weight 5 each
            spent += 5
    assert spent + 5 > g.cap
    assert "ceiling" in str(exc.value)
    assert g.budget("scanner-1").blocked_count == 1
    assert g.snapshot()["blocked_total"] == 1


async def test_nothing_is_sent_even_when_critical_once_capped():
    g = WeightGovernor(limit_per_min=100, budget_pct=95.0, allow_critical_above_cap=False)
    for _ in range(19):
        await g.acquire("execution-1", "account")        # 95 used of 95 cap
    assert g.available() == 0
    with pytest.raises(RateLimitHalt):
        await g.acquire("execution-1", "order", critical=True)


async def test_per_bot_attribution_is_tracked():
    g = WeightGovernor(limit_per_min=2400, budget_pct=95.0)
    await g.acquire("scanner-2", "klines")
    await g.acquire("scanner-2", "klines")
    await g.acquire("analyst-1", "ticker24hr_all")
    snap = g.snapshot()
    assert snap["per_bot"]["scanner-2"]["spent_window"] == 2
    assert snap["per_bot"]["analyst-1"]["spent_window"] == 40
    assert snap["used"] == 42


async def test_exchange_header_is_trusted_when_higher():
    g = WeightGovernor(limit_per_min=2400, budget_pct=95.0)
    await g.acquire("bot", "ping")
    g.sync_from_header(2000)
    assert g.used() >= 2000
    assert g.snapshot()["server_used"] == 2000


async def test_retry_after_reports_window_drain():
    g = WeightGovernor(limit_per_min=100, budget_pct=95.0, window_s=60)
    await g.acquire("bot", "account")
    assert 0 < g.retry_after() <= 60.0


async def test_real_binance_weight_table_used():
    g = WeightGovernor(limit_per_min=2400, budget_pct=95.0)
    assert await g.acquire("bot", "klines_big") == 5
    assert await g.acquire("bot", "openOrders") == 40
    assert await g.acquire("bot", "premiumIndex_all") == 1


def test_live_governor_is_configured_from_settings():
    from app.config import STORE
    snapshot = GOVERNOR.snapshot()
    assert snapshot["budget_pct"] == STORE.cfg.engine.api_budget_pct
    assert snapshot["cap"] <= snapshot["limit_per_min"] * 0.95 + 1e-6
