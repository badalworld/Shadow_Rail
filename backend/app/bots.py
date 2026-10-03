"""
Bot roster — every agent in the Shadow Rail swarm.

Each bot has a unique call-sign the operator can recognise on the dashboard,
a role, a live status, its own API-weight ledger and a performance score that
drives field promotions (Recruit → Operative → Specialist → Elite → Legend).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .bus import BUS
from .util import now_ms

RANKS = ["Recruit", "Operative", "Specialist", "Elite", "Legend"]

# ── the office contract ────────────────────────────────────────────────────
# A bot earns one field rank for every PROMOTE_EVERY completed work units.  A
# unit is a *closed trade it carried* for the trade seats (scanner / analyst /
# execution / monitor) and a *finished workflow cycle it served* for the support
# seats (CEO, connector, API guard, verifier, finance) — same 20-unit rule for
# everybody, so nobody sits at Recruit forever while others level up.
PROMOTE_EVERY = 20
HOLD_FAIL_RATIO = 0.30        # worse than this and the level-up is held
HOLD_MIN_TASKS = 10
FIRE_MIN_TASKS = 20           # an agent is only replaced after real work
FIRE_MIN_FAILURES = 12
FIRE_FAIL_RATIO = 0.55        # sustained failure → the seat is re-filled
TRADE_GROUPS = {"scanner", "analyst", "execution", "monitor"}
CAPACITY_PER_RANK = 0.20      # each rank is trusted with 20 % more work
GROUP_CAPACITY = {"core": 2, "scanner": 30, "analyst": 6, "execution": 4,
                  "verify": 3, "monitor": 3, "finance": 2}
OFFICE_LOG_MAX = 80
RETIRED_MAX = 60


@dataclass
class BotMetrics:
    tasks_done: int = 0
    tasks_failed: int = 0
    errors: int = 0
    wins: int = 0
    losses: int = 0
    api_spent: int = 0
    api_blocked: int = 0
    latency_sum_ms: float = 0.0
    latency_n: int = 0
    completed_trades: int = 0      # closed trades this agent carried
    completed_cycles: int = 0      # finished workflow cycles it served (support seats)

    @property
    def tasks_total(self) -> int:
        return self.tasks_done + self.tasks_failed

    @property
    def fail_ratio(self) -> float:
        total = self.tasks_total
        return (self.tasks_failed / total) if total else 0.0

    @property
    def avg_latency_ms(self) -> float:
        return (self.latency_sum_ms / self.latency_n) if self.latency_n else 0.0

    def score(self) -> float:
        total = self.tasks_done + self.tasks_failed
        reliability = (self.tasks_done / total * 100.0) if total else 60.0
        trade_ratio = self.wins / max(1, self.wins + self.losses)
        error_penalty = min(30.0, self.errors * 1.5)
        blocked_penalty = min(15.0, self.api_blocked * 0.8)
        latency_penalty = min(10.0, self.avg_latency_ms / 250.0)
        experience = min(6.0, total * 0.05)
        s = (reliability * 0.55 + trade_ratio * 100.0 * 0.30 + experience) \
            - error_penalty - blocked_penalty - latency_penalty
        return max(0.0, min(100.0, s))


@dataclass
class Bot:
    bot_id: str
    name: str
    role: str
    group: str
    sigil: str = "◆"
    color: str = "#00e5a8"
    slot: int = 0
    status: str = "offline"            # offline|idle|working|success|error|blocked|sad|celebrating|halted
    task: str = ""
    message: str = ""
    progress: float = 0.0
    mood: str = "neutral"
    mood_until: float = 0.0
    rank_index: int = 0
    promotions: int = 0
    started_at: float = 0.0
    last_active: float = field(default_factory=time.time)
    metrics: BotMetrics = field(default_factory=BotMetrics)
    assigned: list[str] = field(default_factory=list)     # symbols / trades owned
    # office bookkeeping — a *seat* (bot_id) keeps its id for life, the agent
    # sitting in it can be replaced when the office fires it
    founder: bool = True
    generation: int = 1              # 1 = founder, 2 = first replacement, …
    bonus_ranks: int = 0             # operator merit promotions (survive the rule)
    hired_at: float = 0.0
    fired_at: float = 0.0
    fired_reason: str = ""

    # -------------------------------------------------------------- helpers
    @property
    def rank(self) -> str:
        return RANKS[min(self.rank_index, len(RANKS) - 1)]

    # ── the office ladder ─────────────────────────────────────────────────
    @property
    def unit(self) -> str:
        """The work unit this seat is promoted on: closed trades or cycles."""
        return "trade" if self.group in TRADE_GROUPS else "cycle"

    @property
    def completed_units(self) -> int:
        m = self.metrics
        return m.completed_trades if self.unit == "trade" else m.completed_cycles

    @property
    def target_rank_index(self) -> int:
        """What the 20-unit rule has earned, before the quality gate."""
        earned = self.completed_units // PROMOTE_EVERY + self.bonus_ranks
        return min(earned, len(RANKS) - 1)

    @property
    def next_level_in(self) -> int:
        return PROMOTE_EVERY - (self.completed_units % PROMOTE_EVERY)

    @property
    def quality_ok(self) -> bool:
        """Reliability gate: a bot that keeps failing does not get promoted."""
        m = self.metrics
        if m.tasks_total < HOLD_MIN_TASKS:
            return True
        return m.fail_ratio <= HOLD_FAIL_RATIO

    @property
    def workload_weight(self) -> float:
        """How much of the floor this rank is trusted with, vs a Recruit."""
        return 1.0 + CAPACITY_PER_RANK * self.rank_index

    @property
    def capacity(self) -> int:
        """Work items this agent should carry per cycle at its level."""
        base = GROUP_CAPACITY.get(self.group, 2)
        return max(1, int(round(base * self.workload_weight)))

    @property
    def should_retire(self) -> bool:
        m = self.metrics
        return (m.tasks_total >= FIRE_MIN_TASKS
                and m.tasks_failed >= FIRE_MIN_FAILURES
                and m.fail_ratio > FIRE_FAIL_RATIO)

    def as_dict(self, api: dict | None = None) -> dict[str, Any]:
        m = self.metrics
        spent = {}
        if api:
            spent = (api.get("per_bot") or {}).get(self.bot_id, {})
        return {
            "bot_id": self.bot_id, "name": self.name, "role": self.role,
            "group": self.group, "sigil": self.sigil, "color": self.color,
            "slot": self.slot, "status": self.status, "task": self.task,
            "message": self.message, "progress": round(self.progress, 3),
            "mood": self.mood, "rank": self.rank, "rank_index": self.rank_index,
            "promotions": self.promotions,
            "unit": self.unit, "completed_units": self.completed_units,
            "completed_trades": m.completed_trades,
            "completed_cycles": m.completed_cycles,
            "promote_every": PROMOTE_EVERY, "next_level_in": self.next_level_in,
            "capacity": self.capacity,
            "workload_weight": round(self.workload_weight, 2),
            "quality_ok": self.quality_ok,
            "founder": self.founder, "generation": self.generation,
            "hired_at": self.hired_at,
            "metrics": {
                "tasks_done": m.tasks_done, "tasks_failed": m.tasks_failed,
                "errors": m.errors, "wins": m.wins, "losses": m.losses,
                "avg_latency_ms": round(m.avg_latency_ms, 1),
                "api_spent_window": spent.get("spent_window", 0),
                "api_spent_total": spent.get("spent_total", m.api_spent),
                "api_blocked": spent.get("blocked", m.api_blocked),
                "score": round(m.score(), 1),
                "fail_ratio": round(m.fail_ratio, 3),
            },
            "assigned": self.assigned[:40],
            "last_active": self.last_active,
        }


class BotRegistry:
    def __init__(self) -> None:
        self.bots: dict[str, Bot] = {}
        self.order: list[str] = []
        self.retired: list[dict] = []       # agents the office replaced
        self.office_log: list[dict] = []    # promotions / holds / hires / fires

    # ------------------------------------------------------------- creation
    def register(self, bot_id: str, name: str, role: str, group: str,
                 sigil: str = "◆", color: str = "#00e5a8", slot: int = 0) -> Bot:
        bot = Bot(bot_id=bot_id, name=name, role=role, group=group, sigil=sigil,
                  color=color, slot=slot)
        self.bots[bot_id] = bot
        self.order.append(bot_id)
        return bot

    def get(self, bot_id: str) -> Bot | None:
        return self.bots.get(bot_id)

    def by_group(self, group: str) -> list[Bot]:
        return [b for b in self.bots.values() if b.group == group]

    def all(self) -> list[Bot]:
        return [self.bots[i] for i in self.order if i in self.bots]

    # --------------------------------------------------------------- status
    def set_status(self, bot_id: str, status: str, task: str | None = None,
                   message: str | None = None, progress: float | None = None,
                   publish: bool = True) -> None:
        bot = self.bots.get(bot_id)
        if not bot:
            return
        bot.status = status
        if task is not None:
            bot.task = task
        if message is not None:
            bot.message = message
        if progress is not None:
            bot.progress = max(0.0, min(1.0, progress))
        if status in ("working",):
            bot.started_at = time.time()
        bot.last_active = time.time()
        if status in ("success", "error"):
            bot.progress = 1.0 if status == "success" else bot.progress
            if status == "success":
                bot.metrics.tasks_done += 1
            else:
                bot.metrics.tasks_failed += 1
        if status == "idle":
            bot.progress = 0.0
        if publish:
            self.publish(bot_id)

    def note_task(self, bot_id: str, ok: bool = True, latency_ms: float = 0.0,
                  error: bool = False, api_spent: int = 0, api_blocked: int = 0) -> None:
        bot = self.bots.get(bot_id)
        if not bot:
            return
        if ok:
            bot.metrics.tasks_done += 1
        else:
            bot.metrics.tasks_failed += 1
        if error:
            bot.metrics.errors += 1
        if latency_ms:
            bot.metrics.latency_sum_ms += latency_ms
            bot.metrics.latency_n += 1
        bot.metrics.api_spent += api_spent
        bot.metrics.api_blocked += api_blocked

    def set_mood(self, bot_id: str, mood: str, seconds: float = 25.0) -> None:
        bot = self.bots.get(bot_id)
        if not bot:
            return
        bot.mood = mood
        bot.mood_until = time.time() + seconds

    def decay_moods(self) -> bool:
        changed = False
        now = time.time()
        for bot in self.bots.values():
            if bot.mood != "neutral" and bot.mood_until and now > bot.mood_until:
                bot.mood = "neutral"
                changed = True
                self.publish(bot.bot_id)
        return changed

    # ═══════════════════════════════════════════════════════════ the office
    #  Every 20 completed work units a seat earns its next field rank; an agent
    #  that keeps failing is replaced, and the office hires a fresh one into the
    #  same seat so the floor is never short-staffed.
    def note_unit(self, bot_id: str, kind: str = "trade", n: int = 1) -> None:
        """Count completed work for the ladder: a closed trade or a served cycle."""
        bot = self.bots.get(bot_id)
        if not bot:
            return
        if kind == "trade":
            bot.metrics.completed_trades += n
        else:
            bot.metrics.completed_cycles += n

    def _log_office(self, rec: dict) -> dict:
        self.office_log.insert(0, rec)
        del self.office_log[OFFICE_LOG_MAX:]
        return rec

    def _promote(self, bot: Bot, reason: str, manual: bool = False) -> dict:
        prev = bot.rank
        bot.rank_index = min(bot.target_rank_index, len(RANKS) - 1)
        bot.promotions += 1
        bot.mood, bot.mood_until = "excited", time.time() + 45
        rec = {"kind": "promote", "bot_id": bot.bot_id, "name": bot.name,
               "from": prev, "to": bot.rank, "level": bot.rank_index + 1,
               "unit": bot.unit, "completed_units": bot.completed_units,
               "capacity": bot.capacity, "manual": manual, "reason": reason,
               "score": round(bot.metrics.score(), 1), "at": now_ms()}
        self._log_office(rec)
        BUS.publish("bot.promoted", rec)
        self.publish(bot.bot_id)
        return rec

    def _hire(self, seat: Bot, reason: str) -> Bot:
        """Fill a seat with a new agent: same desk, new call-sign, Recruit rank."""
        used = {b.name for b in self.bots.values()} | {r.get("name") for r in self.retired}
        pool = CANDIDATES.get(seat.group, [])
        name = next((n for n in pool if n not in used), "")
        if not name:
            n = 2
            while f"{seat.name}-{n}" in used:
                n += 1
            name = f"{seat.name}-{n}"
        seat.name = name
        seat.metrics = BotMetrics()
        seat.rank_index = 0
        seat.bonus_ranks = 0
        seat.promotions = 0
        seat.status = "working"
        seat.task = "onboarding — learning the desk"
        seat.message = reason
        seat.progress = 0.15
        seat.mood, seat.mood_until = "excited", time.time() + 40
        seat.assigned = []
        seat.founder = False
        seat.generation += 1
        seat.hired_at = time.time()
        seat.fired_at = 0.0
        seat.fired_reason = ""
        seat.last_active = time.time()
        rec = {"kind": "hire", "bot_id": seat.bot_id, "name": seat.name,
               "seat": seat.role, "rank": seat.rank, "level": 1,
               "unit": seat.unit, "generation": seat.generation,
               "reason": reason, "at": now_ms()}
        self._log_office(rec)
        BUS.publish("bot.hired", rec)
        self.publish(seat.bot_id)
        return seat

    def _fire(self, bot: Bot, reason: str) -> dict:
        m = bot.metrics
        rec = {"kind": "fire", "bot_id": bot.bot_id, "name": bot.name,
               "seat": bot.role, "rank": bot.rank, "level": bot.rank_index + 1,
               "unit": bot.unit, "completed_units": bot.completed_units,
               "tasks_done": m.tasks_done, "tasks_failed": m.tasks_failed,
               "fail_ratio": round(m.fail_ratio, 3), "reason": reason,
               "at": now_ms()}
        bot.fired_at = time.time()
        bot.fired_reason = reason
        bot.status = "fired"
        bot.mood = "sad"
        bot.mood_until = time.time() + 20
        self.retired.insert(0, {**rec, "score": round(m.score(), 1),
                                "wins": m.wins, "losses": m.losses,
                                "hired_at": bot.hired_at or None,
                                "generation": bot.generation})
        del self.retired[RETIRED_MAX:]
        self._log_office(rec)
        BUS.publish("bot.fired", rec)
        self.publish(bot.bot_id)
        return rec

    def office_pass(self) -> dict:
        """Promotions (20 units per level), holds, firings and replacements."""
        out: dict[str, list] = {"promoted": [], "held": [], "fired": [], "hired": []}
        for bot in self.all():
            target = bot.target_rank_index
            if target > bot.rank_index:
                if bot.quality_ok:
                    out["promoted"].append(self._promote(
                        bot, f"{bot.completed_units} completed {bot.unit}s"))
                else:
                    # the units are there but the record is not — hold the level
                    hold = {"kind": "hold", "bot_id": bot.bot_id, "name": bot.name,
                            "rank": bot.rank, "unit": bot.unit,
                            "completed_units": bot.completed_units,
                            "fail_ratio": round(bot.metrics.fail_ratio, 3),
                            "reason": "reliability below the promotion gate",
                            "at": now_ms()}
                    if not self.office_log or self.office_log[0].get("kind") != "hold" \
                            or self.office_log[0].get("bot_id") != bot.bot_id:
                        self._log_office(hold)
                        BUS.publish("bot.held", hold)
                    out["held"].append(hold)
            if bot.should_retire:
                fired = self._fire(bot, "sustained failures — replaced by the office")
                out["fired"].append(fired)
                hired = self._hire(bot, f"replacing {fired['name']} — {bot.bot_id}")
                out["hired"].append({"kind": "hire", "bot_id": hired.bot_id,
                                     "name": hired.name, "at": now_ms()})
        if out["promoted"] or out["fired"]:
            BUS.publish("office.update", {"summary": self.office_summary(),
                                          **{k: v for k, v in out.items()}})
        return out

    def evaluate_promotions(self) -> list[dict]:
        """Back-compat shim: the office pass handles the ladder now."""
        return self.office_pass()["promoted"]

    def merit_promote(self, bot_id: str, reason: str = "operator merit") -> dict | None:
        """Operator override: a bonus rank that the 20-unit rule will not undo."""
        bot = self.bots.get(bot_id)
        if not bot or bot.rank_index >= len(RANKS) - 1:
            return None
        bot.bonus_ranks += 1
        return self._promote(bot, reason, manual=True)

    # ── office reporting + persistence ────────────────────────────────────
    def office_summary(self) -> dict:
        seats = self.all()
        counts: dict[str, int] = {}
        levels: dict[str, list] = {}
        for b in seats:
            counts[b.group] = counts.get(b.group, 0) + 1
            levels.setdefault(b.group, []).append(b.rank_index + 1)
        return {
            "agents": len(seats), "promote_every": PROMOTE_EVERY,
            "units": {"trade": sum(1 for b in seats if b.unit == "trade"),
                      "cycle": sum(1 for b in seats if b.unit == "cycle")},
            "by_group": counts,
            "avg_level": round(sum(b.rank_index + 1 for b in seats) / max(1, len(seats)), 2),
            "levels": {k: {"avg": round(sum(v) / len(v), 2), "max": max(v),
                           "min": min(v)} for k, v in levels.items()},
            "next_level_in": {b.bot_id: b.next_level_in for b in seats},
            "retired": len(self.retired), "hires": sum(b.generation - 1 for b in seats),
            "recent": self.office_log[:12],
        }

    def state(self) -> dict:
        def seat(b: Bot) -> dict:
            m = b.metrics
            return {"bot_id": b.bot_id, "name": b.name, "role": b.role,
                    "group": b.group, "sigil": b.sigil, "color": b.color,
                    "slot": b.slot, "rank_index": b.rank_index,
                    "bonus_ranks": b.bonus_ranks, "promotions": b.promotions,
                    "founder": b.founder, "generation": b.generation,
                    "hired_at": b.hired_at, "status": b.status,
                    "metrics": {"tasks_done": m.tasks_done, "tasks_failed": m.tasks_failed,
                                "errors": m.errors, "wins": m.wins, "losses": m.losses,
                                "api_spent": m.api_spent, "api_blocked": m.api_blocked,
                                "latency_sum_ms": m.latency_sum_ms, "latency_n": m.latency_n,
                                "completed_trades": m.completed_trades,
                                "completed_cycles": m.completed_cycles}}
        return {"seats": [seat(b) for b in self.all()],
                "retired": self.retired[:RETIRED_MAX],
                "office_log": self.office_log[:OFFICE_LOG_MAX],
                "at": now_ms()}

    def restore(self, state: dict | None) -> int:
        """Re-apply a saved roster onto the seats built from config."""
        if not isinstance(state, dict):
            return 0
        saved = {r.get("bot_id"): r for r in (state.get("seats") or [])
                 if isinstance(r, dict) and r.get("bot_id")}
        n = 0
        for bot in self.all():
            r = saved.get(bot.bot_id)
            if not r:
                continue
            for key in ("name", "role", "sigil", "color"):
                if r.get(key):
                    setattr(bot, key, str(r[key]))
            bot.rank_index = max(0, min(int(r.get("rank_index", 0)), len(RANKS) - 1))
            bot.bonus_ranks = max(0, int(r.get("bonus_ranks", 0)))
            bot.promotions = max(0, int(r.get("promotions", 0)))
            bot.founder = bool(r.get("founder", True))
            bot.generation = max(1, int(r.get("generation", 1)))
            bot.hired_at = float(r.get("hired_at") or 0)
            m = r.get("metrics") or {}
            for key in ("tasks_done", "tasks_failed", "errors", "wins", "losses",
                        "api_spent", "api_blocked", "completed_trades",
                        "completed_cycles"):
                if key in m:
                    setattr(bot.metrics, key, max(0, int(m[key])))
            bot.metrics.latency_sum_ms = float(m.get("latency_sum_ms") or 0)
            bot.metrics.latency_n = max(0, int(m.get("latency_n") or 0))
            n += 1
        self.retired = [r for r in (state.get("retired") or []) if isinstance(r, dict)][:RETIRED_MAX]
        self.office_log = [r for r in (state.get("office_log") or []) if isinstance(r, dict)][:OFFICE_LOG_MAX]
        return n

    # -------------------------------------------------------------- publish
    def publish(self, bot_id: str, api: dict | None = None) -> None:
        bot = self.bots.get(bot_id)
        if bot:
            BUS.publish("bot.update", bot.as_dict(api))

    def publish_all(self, api: dict | None = None) -> None:
        BUS.publish("bots.snapshot", {"bots": [b.as_dict(api) for b in self.all()]})

    def snapshot(self, api: dict | None = None) -> list[dict]:
        return [b.as_dict(api) for b in self.all()]


# ── hiring pool ───────────────────────────────────────────────────────────
# When the office replaces a failed agent the seat keeps its id (every link,
# seat and layout in the engine and the 3D floor is keyed by it) but gets a new
# call-sign from here, so a fresh agent shows up in the office.
CANDIDATES: dict[str, list[str]] = {
    "core": ["SIGMA", "HELIOS", "MIRAGE", "NEXUS"],
    "scanner": ["COMET", "PHOTON", "QUASAR", "METEOR", "ZENITH", "GALAXY",
                "CORONA", "NEBULA", "CYGNUS", "SIRIUS"],
    "analyst": ["RAMANUJAN", "GALILEO", "PASTEUR", "VOLTA", "MENDELEYEV",
                "ERATOSTHENES", "HYPATIA", "LAGRANGE", "AMPERE", "PLANCK",
                "SCHRÖDINGER", "BABBAGE"],
    "execution": ["HAMMER", "VIPER", "ANVIL", "FALCON", "SABRE"],
    "verify": ["VERITAS", "PROBE", "AUDIT", "WITNESS"],
    "monitor": ["OVERWATCH", "VIGIL", "SENTRY", "PATROL", "LOOKOUT"],
    "finance": ["TALLY", "LEDGER-2", "AUDITOR", "EXCHEQUER", "MINT"],
}


# ======================================================================= #
# The roster.  Each call-sign is unique so the operator can follow one bot
# through the workflow, the log and the 3D map.
# ======================================================================= #
def build_registry(cfg) -> BotRegistry:
    reg = BotRegistry()
    e = cfg.engine

    reg.register("connector-bot", "ORACLE", "Connector Bot — Binance link & SOS",
                 "core", "🛰", "#22d3ee", 0)
    reg.register("ceo-bot", "PRIME", "Engine CEO — workflow authority",
                 "core", "👑", "#fbbf24", 1)
    reg.register("api-guard-bot", "VAULT", "API Guard — 95% weight ceiling",
                 "core", "🛡", "#a78bfa", 2)

    scan_names = ["VEGA", "NOVA", "ORION", "LYRA", "ATLAS", "HELIOS", "PULSAR"]
    for i in range(max(1, e.scanner_bots)):
        reg.register(f"scanner-{i+1}", scan_names[i % len(scan_names)],
                     f"Scanner Bot {i+1:02d} — volatility sweep",
                     "scanner", "📡", "#00e5a8", i)

    analyst_names = ["EINSTEIN", "CURIE", "TESLA", "HOPPER", "TURING",
                     "LOVELACE", "FEYNMAN", "DARWIN", "KEPLER", "BOHR",
                     "HAWKING", "NOETHER"]
    for i in range(max(1, e.analyst_slots)):
        reg.register(f"analyst-{i+1}", analyst_names[i % len(analyst_names)],
                     f"Market Analyst {i+1:02d} — confidence scoring",
                     "analyst", "🧠", "#38bdf8", i)

    exec_names = ["BOLT", "TITAN", "RAZOR"]
    for i in range(max(1, e.execution_bots)):
        reg.register(f"execution-{i+1}", exec_names[i % len(exec_names)],
                     f"Trade Execution {i+1:02d} — order & protection",
                     "execution", "⚡", "#f97316", i)

    reg.register("info-verifier-bot", "ECHO", "Info Bot — execution verification",
                 "verify", "🔍", "#e879f9", 0)

    mon_names = ["SENTINEL", "WARDEN", "WATCHMAN", "GUARDIAN", "OVERSEER"]
    for i in range(max(1, e.monitor_bots)):
        reg.register(f"monitor-{i+1}", mon_names[i % len(mon_names)],
                     f"Trade Monitor {i+1:02d} — live position watch",
                     "monitor", "👁", "#60a5fa", i)

    reg.register("trade-manager-bot", "LEDGER", "Trade Manager — journal & P&L",
                 "finance", "📒", "#34d399", 0)
    reg.register("equity-manager-bot", "BANKER", "Equity Manager — balance & sizing",
                 "finance", "🏦", "#facc15", 1)
    reg.register("risk-bot", "AEGIS", "Risk Manager — stops & liquidation guard",
                 "finance", "⚖️", "#fb7185", 2)
    reg.register("maintenance-bot", "SWEEPER", "Maintenance — data hygiene",
                 "core", "🧹", "#94a3b8", 3)
    return reg


# ------------------------------------------------------------------ links --
def workflow_links() -> list[dict[str, Any]]:
    """Directed graph edges rendered in 3D on the main page."""
    return [
        {"from": "connector-bot", "to": "ceo-bot", "label": "link green"},
        {"from": "api-guard-bot", "to": "ceo-bot", "label": "budget ok"},
        {"from": "ceo-bot", "to": "scanner-1", "label": "scan order"},
        {"from": "ceo-bot", "to": "scanner-2", "label": "scan order"},
        {"from": "ceo-bot", "to": "scanner-3", "label": "scan order"},
        {"from": "ceo-bot", "to": "scanner-4", "label": "scan order"},
        {"from": "ceo-bot", "to": "scanner-5", "label": "scan order"},
        {"from": "scanner-1", "to": "analyst-team", "label": "opportunities"},
        {"from": "scanner-2", "to": "analyst-team", "label": "opportunities"},
        {"from": "scanner-3", "to": "analyst-team", "label": "opportunities"},
        {"from": "scanner-4", "to": "analyst-team", "label": "opportunities"},
        {"from": "scanner-5", "to": "analyst-team", "label": "opportunities"},
        {"from": "analyst-team", "to": "execution-team", "label": "high confidence"},
        {"from": "execution-team", "to": "info-verifier-bot", "label": "verify"},
        {"from": "info-verifier-bot", "to": "ceo-bot", "label": "confirmed"},
        {"from": "ceo-bot", "to": "monitor-team", "label": "watch"},
        {"from": "monitor-team", "to": "trade-manager-bot", "label": "closed"},
        {"from": "trade-manager-bot", "to": "equity-manager-bot", "label": "equity"},
        {"from": "risk-bot", "to": "execution-team", "label": "stop guard"},
    ]


REGISTRY = BotRegistry()
