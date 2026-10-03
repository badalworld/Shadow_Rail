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
RANK_SCORE = [0, 55, 68, 80, 90]         # score thresholds for each rank


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

    # -------------------------------------------------------------- helpers
    @property
    def rank(self) -> str:
        return RANKS[min(self.rank_index, len(RANKS) - 1)]

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
            "metrics": {
                "tasks_done": m.tasks_done, "tasks_failed": m.tasks_failed,
                "errors": m.errors, "wins": m.wins, "losses": m.losses,
                "avg_latency_ms": round(m.avg_latency_ms, 1),
                "api_spent_window": spent.get("spent_window", 0),
                "api_spent_total": spent.get("spent_total", m.api_spent),
                "api_blocked": spent.get("blocked", m.api_blocked),
                "score": round(m.score(), 1),
            },
            "assigned": self.assigned[:40],
            "last_active": self.last_active,
        }


class BotRegistry:
    def __init__(self) -> None:
        self.bots: dict[str, Bot] = {}
        self.order: list[str] = []

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

    # ----------------------------------------------------------- promotions
    def evaluate_promotions(self) -> list[dict]:
        out = []
        for bot in self.bots.values():
            score = bot.metrics.score()
            idx = bot.rank_index
            while idx < len(RANKS) - 1 and score >= RANK_SCORE[idx + 1]:
                idx += 1
            if idx > bot.rank_index:
                prev = bot.rank
                bot.rank_index = idx
                bot.promotions += 1
                bot.mood = "excited"
                bot.mood_until = time.time() + 45
                rec = {"bot_id": bot.bot_id, "name": bot.name, "from": prev,
                       "to": bot.rank, "score": round(score, 1), "at": now_ms()}
                out.append(rec)
                BUS.publish("bot.promoted", rec)
                self.publish(bot.bot_id)
            elif score + 12 < RANK_SCORE[max(0, bot.rank_index)] and bot.rank_index > 0:
                # sustained poor performance → demoted one field rank
                bot.rank_index -= 1
                rec = {"bot_id": bot.bot_id, "name": bot.name, "from": RANKS[bot.rank_index + 1],
                       "to": bot.rank, "score": round(score, 1), "at": now_ms(),
                       "demoted": True}
                out.append(rec)
                BUS.publish("bot.demoted", rec)
                self.publish(bot.bot_id)
        return out

    # -------------------------------------------------------------- publish
    def publish(self, bot_id: str, api: dict | None = None) -> None:
        bot = self.bots.get(bot_id)
        if bot:
            BUS.publish("bot.update", bot.as_dict(api))

    def publish_all(self, api: dict | None = None) -> None:
        BUS.publish("bots.snapshot", {"bots": [b.as_dict(api) for b in self.all()]})

    def snapshot(self, api: dict | None = None) -> list[dict]:
        return [b.as_dict(api) for b in self.all()]


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
