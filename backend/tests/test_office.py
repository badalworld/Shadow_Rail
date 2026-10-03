"""The trading office: 20-unit promotions, the reliability gate, and the
hire/fire cycle that keeps every desk staffed."""
from __future__ import annotations

import pytest

from app.bots import (
    CANDIDATES, FIRE_FAIL_RATIO, FIRE_MIN_FAILURES, FIRE_MIN_TASKS,
    HOLD_FAIL_RATIO, PROMOTE_EVERY, RANKS, TRADE_GROUPS, BotRegistry,
    build_registry,
)

def seat(reg: BotRegistry, group: str = "scanner", slot: int = 0):
    return next(b for b in reg.all() if b.group == group and b.slot == slot)


# ───────────────────────────── the ladder ──────────────────────────────────

def test_a_trade_seat_levels_up_every_twenty_completed_trades(store):
    reg = build_registry(store.cfg)
    bot = reg.get("scanner-1")
    assert bot.rank == "Recruit"

    for _ in range(PROMOTE_EVERY - 1):
        reg.note_unit(bot.bot_id, "trade")
    assert reg.office_pass()["promoted"] == []
    assert bot.rank == "Recruit"
    assert bot.next_level_in == 1

    reg.note_unit(bot.bot_id, "trade")          # unit #20
    res = reg.office_pass()
    assert [p["bot_id"] for p in res["promoted"]] == [bot.bot_id]
    assert bot.rank == RANKS[1]
    assert bot.promotions == 1
    assert bot.next_level_in == PROMOTE_EVERY

    for _ in range(PROMOTE_EVERY * 2):          # 40 more → two more levels
        reg.note_unit(bot.bot_id, "trade")
    reg.office_pass()
    assert bot.rank == RANKS[3]


def test_support_seats_level_on_served_cycles(store):
    reg = build_registry(store.cfg)
    ceo = reg.get("ceo-bot")
    assert ceo.unit == "cycle"
    for _ in range(PROMOTE_EVERY):
        reg.note_unit(ceo.bot_id, "cycle")
    reg.office_pass()
    assert ceo.rank == RANKS[1]


def test_levels_never_pass_legend(store):
    reg = build_registry(store.cfg)
    bot = reg.get("analyst-1")
    for _ in range(PROMOTE_EVERY * 12):
        reg.note_unit(bot.bot_id, "trade")
    reg.office_pass()
    assert bot.rank == "Legend"
    assert bot.rank_index == len(RANKS) - 1
    assert reg.office_pass()["promoted"] == []      # nothing left to earn


def test_promotion_raises_the_load_the_bot_carries(store):
    reg = build_registry(store.cfg)
    bot = reg.get("scanner-1")
    base = bot.capacity
    for _ in range(PROMOTE_EVERY * 3):
        reg.note_unit(bot.bot_id, "trade")
    reg.office_pass()
    assert bot.rank_index == 3
    assert bot.capacity > base
    assert bot.workload_weight == pytest.approx(1 + 0.2 * 3)


def test_a_failing_bot_is_held_at_its_rank(store):
    reg = build_registry(store.cfg)
    bot = reg.get("execution-1")
    bot.metrics.tasks_done = 6
    bot.metrics.tasks_failed = 6              # 50 % failure, above the gate
    assert bot.metrics.fail_ratio > HOLD_FAIL_RATIO
    assert not bot.quality_ok
    for _ in range(PROMOTE_EVERY):
        reg.note_unit(bot.bot_id, "trade")
    res = reg.office_pass()
    assert res["promoted"] == [] and res["held"]
    assert bot.rank == "Recruit"

    bot.metrics.tasks_done = 30               # recovers its record
    reg.office_pass()
    assert bot.rank == RANKS[1]               # the earned level is honoured


# ──────────────────────────── hire and fire ────────────────────────────────

def test_a_persistent_failure_loses_the_seat_and_a_new_agent_is_hired(store):
    reg = build_registry(store.cfg)
    bot = reg.get("scanner-3")
    old_name = bot.name
    bot.metrics.tasks_done = 6
    bot.metrics.tasks_failed = FIRE_MIN_FAILURES + 2
    assert bot.metrics.tasks_total >= FIRE_MIN_TASKS
    assert bot.metrics.fail_ratio > FIRE_FAIL_RATIO
    assert bot.should_retire

    res = reg.office_pass()
    assert [r["bot_id"] for r in res["fired"]] == [bot.bot_id]
    assert res["hired"], "the office must re-fill the seat it just cleared"

    seat = reg.get(bot.bot_id)
    assert seat is not None, "the seat itself stays on the floor"
    assert seat.name != old_name, "a different agent now works that desk"
    assert seat.name in CANDIDATES["scanner"] or "-" in seat.name
    assert seat.rank == "Recruit" and seat.metrics.completed_trades == 0
    assert seat.generation == 2 and not seat.founder
    # the retired agent is kept on the record, not deleted
    assert reg.retired and reg.retired[0]["name"] == old_name
    assert reg.retired[0]["reason"]


def test_firing_never_shrinks_the_floor(store):
    reg = build_registry(store.cfg)
    before = {g: len(reg.by_group(g)) for g in
              ("core", "scanner", "analyst", "execution", "verify", "monitor", "finance")}
    for bot in reg.all():
        bot.metrics.tasks_done = 6
        bot.metrics.tasks_failed = FIRE_MIN_FAILURES + 4
    res = reg.office_pass()
    assert len(res["fired"]) == len(reg.all())
    assert len(res["hired"]) == len(res["fired"])
    after = {g: len(reg.by_group(g)) for g in before}
    assert after == before, "every desk stays staffed after the office acts"


def test_hires_never_reuse_a_call_sign(store):
    reg = build_registry(store.cfg)
    bot = reg.get("monitor-1")
    seen = {bot.name}
    for _ in range(6):
        bot.metrics.tasks_done = 6
        bot.metrics.tasks_failed = FIRE_MIN_FAILURES + 5
        reg.office_pass()
        assert bot.name not in seen
        seen.add(bot.name)
    assert len(seen) == 7


def test_a_healthy_bot_is_never_fired(store):
    reg = build_registry(store.cfg)
    bot = reg.get("analyst-1")
    bot.metrics.tasks_done = 400
    bot.metrics.tasks_failed = 3
    assert not bot.should_retire
    assert reg.office_pass()["fired"] == []


def test_an_idle_bot_is_never_fired(store):
    reg = build_registry(store.cfg)
    assert reg.office_pass()["fired"] == []


# ────────────────────────── workload + persistence ─────────────────────────

def test_the_office_record_survives_a_restart(store):
    reg = build_registry(store.cfg)
    bot = reg.get("scanner-2")
    for _ in range(PROMOTE_EVERY * 2):
        reg.note_unit(bot.bot_id, "trade")
    reg.office_pass()
    reg.get("monitor-2").metrics.tasks_failed = FIRE_MIN_FAILURES + 3
    reg.get("monitor-2").metrics.tasks_done = 8
    reg.office_pass()
    saved = reg.state()
    hired_name = reg.get("monitor-2").name

    fresh = build_registry(store.cfg)
    assert fresh.restore(saved) == len(saved["seats"])
    assert fresh.get("scanner-2").rank == RANKS[2]
    assert fresh.get("scanner-2").completed_units == PROMOTE_EVERY * 2
    assert fresh.get("monitor-2").name == hired_name
    assert fresh.get("monitor-2").generation == 2
    assert len(fresh.retired) == 1


def test_office_summary_reports_the_floor(store):
    reg = build_registry(store.cfg)
    for _ in range(PROMOTE_EVERY):
        reg.note_unit("scanner-1", "trade")
    reg.office_pass()
    s = reg.office_summary()
    assert s["agents"] == len(reg.all())
    assert s["promote_every"] == PROMOTE_EVERY
    assert s["by_group"]["scanner"] == 5
    assert s["units"]["cycle"] > 0 and s["units"]["trade"] > 0
    assert s["next_level_in"]["scanner-1"] == PROMOTE_EVERY
    assert s["avg_level"] > 1


def test_trade_seats_and_support_seats_use_the_right_unit(store):
    reg = build_registry(store.cfg)
    for bot in reg.all():
        expected = "trade" if bot.group in TRADE_GROUPS else "cycle"
        assert bot.unit == expected, bot.bot_id


def test_merit_promotion_is_a_bonus_rank_that_survives_the_rule(store):
    reg = build_registry(store.cfg)
    bot = reg.get("verify-1") or reg.get("info-verifier-bot")
    assert reg.merit_promote(bot.bot_id, "operator")["to"] == RANKS[1]
    reg.office_pass()
    assert bot.rank == RANKS[1], "the 20-unit rule must not undo a merit rank"
    for _ in range(PROMOTE_EVERY):
        reg.note_unit(bot.bot_id, "cycle")
    reg.office_pass()
    assert bot.rank == RANKS[2]


# ───────────────────────── engine-side workload ────────────────────────────

@pytest.mark.asyncio
async def test_a_promotion_moves_assets_to_the_veteran_scanner(store):
    """The promoted desk must visibly carry more of the universe."""
    from app.exchange.hub import MarketHub
    from app.engine import TradingEngine
    from app.exchange.sim import SimExchange
    from app.journal import Journal

    hub = MarketHub(store=store)
    sim = SimExchange(balance=10_000.0, universe=40, history_bars=120,
                      interval="5m", time_accel=3000.0)
    hub.exchange = sim
    hub.transport = "sim"
    hub.mode = "sim"
    hub.filters = await sim.all_filters()
    hub.tickers = await sim.tickers_all()
    await hub.warmup(list(sim.syms.keys()))
    hub.universe = list(sim.syms.keys())
    hub.started = True

    reg = build_registry(store.cfg)
    eng = TradingEngine(store=store, hub=hub, journal=Journal(store=store), registry=reg)
    eng.running = True
    eng.scanner_buckets = eng._split_universe_by_rank()
    eng._assign_scanner_symbols()
    equal = [len(b) for b in eng.scanner_buckets]
    assert len(set(equal)) == 1, f"an all-Recruit floor splits evenly: {equal}"

    for _ in range(PROMOTE_EVERY * 2):
        reg.note_unit("scanner-1", "trade")
    eng._office_pass("test promotion")
    sizes = [len(b) for b in eng.scanner_buckets]
    assert reg.get("scanner-1").rank_index == 2
    assert sizes[0] > max(equal), f"the veteran should sweep more: {sizes}"
    assert sum(sizes) == sum(equal), "no assets may be lost in the re-shuffle"
    assert all(s >= 1 for s in sizes)
    # …and the desks are told about it
    assert len(reg.get("scanner-1").assigned) == sizes[0]


@pytest.mark.asyncio
async def test_monitor_desks_take_positions_by_capacity(store):
    from app.exchange.hub import MarketHub
    from app.engine import TradingEngine
    from app.exchange.sim import SimExchange
    from app.journal import Journal

    hub = MarketHub(store=store)
    sim = SimExchange(balance=10_000.0, universe=12, history_bars=120,
                      interval="5m", time_accel=3000.0)
    hub.exchange = sim
    hub.transport = "sim"
    hub.mode = "sim"
    hub.filters = await sim.all_filters()
    hub.tickers = await sim.tickers_all()
    await hub.warmup(list(sim.syms.keys()))
    hub.universe = list(sim.syms.keys())
    hub.started = True

    reg = build_registry(store.cfg)
    for _ in range(PROMOTE_EVERY * 4):                 # make monitor-1 a Legend
        reg.note_unit("monitor-1", "trade")
    reg.office_pass()
    eng = TradingEngine(store=store, hub=hub, journal=Journal(store=store), registry=reg)
    eng.running = True
    for i in range(8):
        symbol = f"TST{i}USDT"
        eng.symbol_to_trade[symbol] = 1000 + i
        eng.open_trades[1000 + i] = {"id": 1000 + i, "symbol": symbol}
        eng._assign_monitor_for(symbol)
    counts: dict[str, int] = {}
    for bot_id in eng.symbol_monitor.values():
        counts[bot_id] = counts.get(bot_id, 0) + 1
    assert counts.get("monitor-1", 0) >= 3, f"the Legend carries the book: {counts}"
