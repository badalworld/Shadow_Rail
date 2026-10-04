"""
TradingEngine — the CEO workflow, implemented exactly as specified.

    Connector Bot ──green──▶ Engine CEO ──order──▶ 5× Scanner Bot (30 assets each,
                                                        split by volatility)
         scanners ──opportunities + CEO confirmation──▶ 10× Market Analyst
         analysts ──high-confidence proposals──▶ Execution team (2 bots)
         execution ──order + TP/SL (inside liquidation)──▶ Info Bot verification
         Info Bot ──verified──▶ CEO ──order──▶ 4× Trade Monitor (live watch)
         close ⟶ Trade Manager (journal, P&L, win-rate, fees, funding)
              ⟶ Equity Manager (8% margin/ trade, 10× cross, 10 concurrent)

Everything is event-driven and concurrent; every step is logged, streamed to
the dashboard and attributed to the bot that performed it.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import math
import time
from dataclasses import dataclass, field
from typing import Any

from .bots import BotRegistry, build_registry, workflow_links
from .bus import BUS
from .confidence import MODEL
from .config import ConfigStore, STORE
from .db import DB
from .exchange.base import TAKER_FEE, ExchangeError, Position
from .exchange.hub import MarketHub
from .indicators import ghost
from .journal import JOURNAL, Journal
from .ratelimit import GOVERNOR, RateLimitHalt
from .risk import RiskEngine, roi_points, roi_price_step, trail_stop_price
from .strategy import (breakeven_stop, direction_crowded, enrich, r_progress,
                       symbol_vetoed, time_stop_due)
from .util import now_ms, round_tick, tf_ms

SCAN_TIMEOUT_S = 120
MAX_OPPORTUNITIES_PER_CYCLE = 40
SYMBOL_COOLDOWN_S = 300


@dataclass
class Opportunity:
    symbol: str
    direction: str
    entry: float
    stop: float
    target: float
    atr: float
    atr_pct: float
    trend_quality: float
    tier: str
    rail: float | None
    rail_distance_pct: float
    htf_bull: bool
    flow_bias: float
    volume: float
    bar_time: int
    scanner_id: str
    features: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {**self.__dict__}

    def to_signal(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol, "direction": self.direction, "entry": self.entry,
            "stop": self.stop, "target": self.target, "atr": self.atr,
            "atr_pct": self.atr_pct, "trend_quality": self.trend_quality,
            "tier": self.tier, "rail": self.rail,
            "rail_distance_pct": self.rail_distance_pct, "htf_bull": self.htf_bull,
            "flow_bias": self.flow_bias, "bar_time": self.bar_time,
            "scanner_id": self.scanner_id,
        }


@dataclass
class Proposal:
    opportunity: Opportunity
    analyst_id: str
    confidence: float
    approved: bool
    factors: dict[str, float]
    notes: list[str]
    model: str
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.opportunity.symbol,
            "direction": self.opportunity.direction,
            "confidence": round(self.confidence, 2),
            "approved": self.approved,
            "analyst_id": self.analyst_id,
            "factors": self.factors,
            "notes": self.notes,
            "model": self.model,
            "reason": self.reason,
            "entry": self.opportunity.entry,
            "stop": self.opportunity.stop,
            "target": self.opportunity.target,
            "atr": self.opportunity.atr,
            "atr_pct": self.opportunity.atr_pct,
            "trend_quality": self.opportunity.trend_quality,
            "tier": self.opportunity.tier,
            "bar_time": self.opportunity.bar_time,
            "feature_row": self.opportunity.features,
        }


# the seven stages the dashboard renders, in order — every one is always present
WORKFLOW_STAGES = ("connector", "scan", "analyze", "execute", "verify",
                   "monitor", "close")


class TradingEngine:
    def __init__(self, store: ConfigStore = STORE, hub: MarketHub | None = None,
                 journal: Journal = JOURNAL, registry: BotRegistry | None = None):
        self.store = store
        self.hub = hub or MarketHub(store)
        self.journal = journal
        self.registry = registry or build_registry(store.cfg)
        self.risk = RiskEngine(store.cfg.risk)

        self.running = False
        self.tasks: list[asyncio.Task] = []
        self._log_queue: asyncio.Queue = asyncio.Queue(maxsize=5000)
        self._log_task: asyncio.Task | None = None

        self.open_trades: dict[int, dict] = {}
        self.symbol_to_trade: dict[str, int] = {}
        self.cooldowns: dict[str, float] = {}
        self.series_cache: dict[str, tuple[int, Any]] = {}
        self.scanner_buckets: list[list[str]] = []
        self.symbol_monitor: dict[str, str] = {}
        self.cycle = 0
        self.signals_seen = 0
        self.last_scan_at = 0
        self.started_at = 0

        self.sos: dict[str, Any] = {"active": False, "level": "none", "reasons": [],
                                    "since": 0, "checks": 0, "last_ok": 0}
        self.health: dict[str, Any] = {"connected": False, "problems": ["not started"]}
        self._last_connector_level: str | None = None   # green-light hand-off tracker
        self.workflow: dict[str, Any] = {"cycle": 0, "stage": "idle",
                                         "stages": {k: {"status": "idle", "detail": "",
                                                        "at": 0}
                                                    for k in WORKFLOW_STAGES},
                                         "active_links": [], "updated_at": now_ms()}
        self.paused = False
        self.emergency_stop = False
        self._equity_task_ts = 0
        self.last_equity: dict[str, Any] = {}
        self.recent_analyst_rows: list[dict] = []
        self.gate_rejections: dict[str, int] = {}     # Strategy-v2 gate rejections
        self.pending_close: set[str] = set()
        self.consumed_fills: set[str] = set()
        self._open_lock = asyncio.Lock()
        self.scan_snapshot: dict[str, Any] = {"by_bot": {}, "updated_at": 0, "cycle": 0}

    # ================================================================ startup
    async def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.started_at = now_ms()
        self._log_task = asyncio.create_task(self._log_writer())
        await self.hub.start()
        # the office record (ranks, hires, firings) survives a restart
        with contextlib.suppress(Exception):
            saved = await DB.kv_get("office.roster")
            if saved:
                n = self.registry.restore(saved)
                if n:
                    self.log("info", "ceo-bot",
                             f"office restored — {n} seats, "
                             f"{len(self.registry.retired)} agents on the retired list")
        self.scanner_buckets = self._split_universe_by_rank() or \
            self.hub.scanner_allocation(self.store.cfg.engine.scanner_bots,
                                        self.store.cfg.engine.assets_per_bot)
        self._assign_scanner_symbols()
        await self._load_open_trades()
        # show the locked starting balance straight away
        with contextlib.suppress(Exception):
            await self.journal.load_locked()
        if self.journal.state.starting_locked and not self.last_equity:
            with contextlib.suppress(Exception):
                self.last_equity = {
                    **self.journal.state.as_dict(),
                    "mode": self.hub.mode, "transport": self.hub.transport,
                    "open_slots": self.store.cfg.risk.max_concurrent_trades,
                }
        self.log("info", "ceo-bot", f"Engine starting — transport={self.hub.transport} "
                                   f"mode={self.hub.mode} universe={len(self.hub.universe)}")
        self.tasks = [
            asyncio.create_task(self._connector_loop()),
            asyncio.create_task(self._equity_loop()),
            asyncio.create_task(self._scan_loop()),
            asyncio.create_task(self._monitor_loop()),
            asyncio.create_task(self._maintenance_loop()),
        ]
        self.registry.publish_all(GOVERNOR.snapshot())

    async def stop(self) -> None:
        # flush the paper account so nothing is lost between the last tick and now
        with contextlib.suppress(Exception):
            await self.hub.persist_sim_state()
        await self.persist_office()
        self.running = False
        for t in self.tasks:
            t.cancel()
        for t in self.tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        self.tasks.clear()
        await self.hub.stop()
        if self._log_task:
            self._log_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._log_task
            self._log_task = None
        self.log("warn", "ceo-bot", "Engine stopped")

    # ================================================================== log
    async def persist_office(self) -> None:
        """Write the office record (ranks, hires, firings) so a restart keeps it."""
        with contextlib.suppress(Exception):
            await DB.kv_set("office.roster", self.registry.state())

    def log(self, level: str, bot_id: str, message: str, payload: dict | None = None,
            topic: str | None = None) -> None:
        rec = {"ts": now_ms(), "level": level, "bot_id": bot_id, "message": message,
               "payload": payload, "topic": topic or bot_id}
        try:
            self._log_queue.put_nowait(rec)
        except asyncio.QueueFull:
            pass
        BUS.publish("log.append", rec)

    async def _log_writer(self) -> None:
        while True:
            try:
                batch: list[dict] = []
                rec = await self._log_queue.get()
                batch.append(rec)
                while len(batch) < 40:
                    try:
                        batch.append(self._log_queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break
                for r in batch:
                    with contextlib.suppress(Exception):
                        await DB.add_log(r["level"], r["bot_id"], r["topic"],
                                         r["message"], r["payload"])
                if self._log_queue.qsize() == 0:
                    await asyncio.sleep(0.35)
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(0.5)

    # ============================================================== workflow
    def _wf(self, stage: str, status: str, detail: str = "", **extra: Any) -> None:
        stages = self.workflow.setdefault("stages", {})
        stages[stage] = {"status": status, "detail": detail, "at": now_ms(), **extra}
        self.workflow["stage"] = stage
        self.workflow["updated_at"] = now_ms()
        BUS.publish("workflow.update", self.workflow)
        if status in ("start",):
            self._pulse_links(stage)

    def _pulse_links(self, stage: str) -> None:
        mapping = {
            "connector": [("connector-bot", "ceo-bot")],
            "scan": [("ceo-bot", f"scanner-{i+1}") for i in range(self.store.cfg.engine.scanner_bots)],
            "analyze": [("scanner-team", "analyst-team")],
            "execute": [("analyst-team", "execution-team"), ("risk-bot", "execution-team")],
            "verify": [("execution-team", "info-verifier-bot")],
            "monitor": [("ceo-bot", "monitor-team")],
            "close": [("monitor-team", "trade-manager-bot"),
                      ("trade-manager-bot", "equity-manager-bot")],
        }
        for src, dst in mapping.get(stage, []):
            BUS.publish("link.pulse", {"from": src, "to": dst, "stage": stage,
                                       "ts": now_ms()})

    def _assign_scanner_symbols(self) -> None:
        for i, bucket in enumerate(self.scanner_buckets):
            bot = self.registry.get(f"scanner-{i+1}")
            if bot:
                bot.assigned = list(bucket)
        sizes = [len(b) for b in self.scanner_buckets]
        spread = f"{sizes[0]} assets" if len(set(sizes)) <= 1 else \
            f"{min(sizes)}–{max(sizes)} assets by level"
        self.log("info", "ceo-bot",
                 f"Scanner allocation ready — {len(self.scanner_buckets)} bots × "
                 f"{spread} (ranked by volatility, weighted by field rank)")

    def _split_universe_by_rank(self) -> list[list[str]]:
        """Deal the volatility-ranked universe across the scanner desks in
        proportion to each agent's level: a promoted scanner sweeps more of the
        market, a fresh hire sweeps less.  With an all-Recruit floor this is
        exactly the configured `assets_per_bot` split."""
        scanners = [b for b in self.registry.by_group("scanner")
                    if b.status != "fired"]
        universe = list(self.hub.universe)
        if not scanners or not universe:
            return []
        cfg = self.store.cfg.engine
        total = min(len(universe), int(cfg.assets_per_bot) * len(scanners))
        weights = [b.workload_weight for b in scanners]
        shares = [total * w / sum(weights) for w in weights]
        counts = [max(1, int(v)) for v in shares]
        while sum(counts) > total:                    # never over-allocate
            i = max(range(len(counts)), key=lambda j: (counts[j], -weights[j]))
            counts[i] -= 1
        rest = total - sum(counts)
        order = sorted(range(len(scanners)),
                       key=lambda i: (-scanners[i].rank_index, scanners[i].slot))
        for k in range(max(0, rest)):
            counts[order[k % len(order)]] += 1
        # deal round-robin by volatility so every desk keeps a fair mix
        buckets: list[list[str]] = [[] for _ in scanners]
        rank = 0
        for sym in universe:
            if all(len(b) >= c for b, c in zip(buckets, counts)):
                break
            for offset in range(len(buckets)):
                j = (rank + offset) % len(buckets)
                if len(buckets[j]) < counts[j]:
                    buckets[j].append(sym)
                    break
            rank += 1
        return buckets

    def _rebalance_workload(self) -> None:
        """Hand the floor to the ranks: scanners get a level-weighted slice of
        the universe, monitor desks get positions in proportion to capacity."""
        buckets = self._split_universe_by_rank()
        if buckets:
            self.scanner_buckets = buckets
            self._assign_scanner_symbols()
        for symbol in list(self.symbol_monitor.keys()):
            self.symbol_monitor.pop(symbol, None)
        for symbol in self.symbol_to_trade:
            self._assign_monitor_for(symbol)

    def _office_pass(self, trigger: str = "") -> dict:
        """Run the office: promotions every 20 units, replacements for failures."""
        res = self.registry.office_pass()
        if not (res["promoted"] or res["fired"]):
            return res
        for rec in res["promoted"]:
            self.log("success", rec["bot_id"],
                     f"promoted {rec['from']} → {rec['to']} "
                     f"({rec['completed_units']} completed {rec['unit']}s) — "
                     f"workload now {rec['capacity']} items/cycle",
                     {"office": rec}, topic="office")
        for rec in res["fired"]:
            self.log("warn", rec["bot_id"],
                     f"{rec['name']} relieved of duty ({rec['reason']}) — "
                     f"{rec['tasks_failed']} failed of {rec['tasks_done'] + rec['tasks_failed']} tasks",
                     {"office": rec}, topic="office")
        for rec in res["hired"]:
            self.log("info", rec["bot_id"],
                     f"hired {rec['name']} into the seat ({trigger or 'office'})",
                     {"office": rec}, topic="office")
        self._rebalance_workload()
        self.registry.publish_all(GOVERNOR.snapshot())
        return res

    # ============================================================== loops
    async def _connector_loop(self) -> None:
        """Connector Bot: verify the link now, then every 5 minutes."""
        first = True
        while self.running:
            if not first:
                wait = float(self.store.cfg.engine.connector_health_interval_s)
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(asyncio.sleep(wait), timeout=wait + 1)
            first = False
            if not self.running:
                return
            self.registry.set_status("connector-bot", "working", "probing Binance link",
                                     progress=0.4)
            self._wf("connector", "start", "Connector Bot probing the exchange link")
            try:
                health = await asyncio.wait_for(self.hub.health(deep=True), timeout=25)
            except Exception as exc:
                health = {"connected": False, "problems": [str(exc)[:200]],
                          "transport": self.hub.transport, "checked_at": now_ms()}
            self.health = health
            self.sos["checks"] += 1
            level, problems = self._connector_verdict(health)
            waiting_for_keys = self.hub.transport != "binance" \
                and not bool(self.store.api_key() and self.store.api_secret())

            if level == "critical":
                self._raise_sos("critical", problems or ["connection lost"])
                self.registry.set_status("connector-bot", "error", "SOS RAISED",
                                         message="; ".join(problems)[:200])
            elif level == "notice":
                # nothing to raise: the simulator is doing its job while the
                # operator has not stored keys yet
                self._note_connector_notice(problems)
                self.registry.set_status(
                    "connector-bot", "success", "simulator active",
                    message="add the Binance API key + secret in Settings to go live")
            elif level == "warning":
                self._raise_sos("warning", problems)
                if waiting_for_keys:
                    self.registry.set_status(
                        "connector-bot", "success", "simulator active",
                        message="add the Binance API key + secret in Settings to go live")
                else:
                    self.registry.set_status("connector-bot", "success", "link degraded",
                                             message="; ".join(problems)[:200])
            else:
                self._clear_sos()
                self.registry.set_status(
                    "connector-bot", "success", "link verified",
                    message=f"{health.get('transport')} OK · {health.get('latency_ms', 0):.0f} ms · "
                            f"{health.get('universe', 0)} symbols")

            # inform the CEO on every *change* of link state (first probe counts,
            # recovering from the siren counts) — but never while the SOS is up
            # and never on a repeated 5-minute probe, so the log stays readable
            prev_level = self._last_connector_level
            self._last_connector_level = level
            if level != "critical" and (prev_level is None or prev_level == "critical"
                                        or (prev_level == "none" and level == "warning")):
                self._wf("connector", "done", f"link {level} → Engine CEO informed")
                if level == "notice" or (level == "warning" and waiting_for_keys):
                    self.log("info", "connector-bot",
                             "Simulation broker active — no Binance keys yet; add them in "
                             "Settings to go live. Engine CEO informed.",
                             {"health": {k: v for k, v in health.items() if k != "api_weight"}})
                else:
                    self.log("success", "connector-bot",
                             f"Connection confirmed ({health.get('transport')}, "
                             f"{health.get('latency_ms', 0):.0f}ms) → green signal to CEO",
                             {"health": {k: v for k, v in health.items() if k != "api_weight"}})
                self.registry.set_status("ceo-bot", "working", "awaiting scan cycle",
                                         message="link report received from ORACLE")
            BUS.publish("connector.health", health)
            self._wf("connector", "error" if level == "critical" else "done",
                     "; ".join(problems)[:180] if problems else "healthy")

    def _connector_verdict(self, health: dict[str, Any]) -> tuple[str, list[str]]:
        """
        Classify a Connector-Bot probe: ``none`` (green), ``notice`` (the
        simulator is running because no keys are stored yet), ``warning`` (a
        configured link is degraded) or ``critical`` (the ☠️ SOS siren).

        Only a link that is *supposed* to be up can raise anything over the
        dashboard.  A brand-new install with no keys gets a neutral ``notice``:
        nothing is raised, nothing is paused, the floor simply states that the
        simulator is working, and the whole swarm keeps trading it.
        """
        problems = list(health.get("problems") or [])
        creds = bool(self.store.api_key() and self.store.api_secret())
        # no keys → there is nothing to connect to: never a problem, only a notice
        if not creds and self.hub.transport != "binance":
            return ("notice", problems or ["no Binance API keys stored — simulator active"])
        # live mode with keys but the transport fell back to the simulator
        if self.hub.transport != "binance" and self.store.cfg.binance.mode == "live" \
                and self.hub.last_error:
            problems = problems + [self.hub.last_error]
            return ("critical" if creds else "warning"), problems
        if not health.get("connected"):
            hard = self.hub.transport == "binance" \
                or not self.store.cfg.engine.simulate_when_offline
            if not hard and not creds:
                return "notice", (problems or ["simulator active — no keys stored"])
            return ("critical" if hard else "warning"), (problems or ["connection lost"])
        return ("warning", problems) if problems else ("none", problems)

    def _note_connector_notice(self, reasons: list[str]) -> None:
        """Keyless simulator: report the state, raise nothing over the UI."""
        if self.sos["active"] or self.sos.get("level") != "notice" \
                or set(reasons) != set(self.sos["reasons"]):
            self.sos.update({"active": False, "level": "notice",
                             "reasons": reasons[:6], "since": 0})
            BUS.publish("sos.notice", {"level": "notice", "reasons": reasons[:6],
                                       "at": now_ms()})

    def _raise_sos(self, level: str, reasons: list[str]) -> None:
        if level == "warning" and self.sos["active"] and self.sos["level"] == "critical":
            return
        if not self.sos["active"] or self.sos["level"] != level or \
                set(reasons) != set(self.sos["reasons"]):
            self.sos.update({"active": True, "level": level, "reasons": reasons[:6],
                             "since": self.sos["since"] or now_ms()})
            BUS.publish("sos.on", {**self.sos, "at": now_ms()})
            icon = "☠️" if level == "critical" else "⚠️"
            self.log("sos", "connector-bot",
                     f"{icon} SOS {level.upper()} — " + "; ".join(reasons)[:220],
                     {"reasons": reasons})
            if level == "critical":
                # a real outage is felt by the whole crew; an amber prompt
                # (e.g. "no keys yet") must not sadden 28 innocent bots
                for bot in self.registry.all():
                    if bot.bot_id not in ("connector-bot",):
                        self.registry.set_mood(bot.bot_id, "sad", 40)
            # Live transport failure = stop trading.  Simulation fallback keeps the
            # workflow visible (labelled SIMULATION) so the operator can watch it.
            simulate = (self.hub.transport == "sim"
                        and self.store.cfg.engine.simulate_when_offline)
            self.paused = not simulate
            self.registry.publish_all(GOVERNOR.snapshot())

    def _clear_sos(self) -> None:
        was_active = bool(self.sos["active"])
        if was_active or self.sos.get("level") not in ("none", None):
            self.sos.update({"active": False, "level": "none", "reasons": [], "since": 0})
            BUS.publish("sos.off", {"at": now_ms()})
            if was_active:
                self.log("success", "connector-bot", "SOS cleared — connection healthy again")
            self.paused = False
            self.registry.publish_all(GOVERNOR.snapshot())

    async def _equity_loop(self) -> None:
        """Equity Manager: keep balance, equity, margin budget live."""
        first = True
        while self.running:
            try:
                if not first:
                    await asyncio.sleep(5)
                first = False
                if not self.running:
                    return
                account = await self.hub.account()
                state = await self.journal.update(account)
                stats = await self.journal.refresh_stats()
                payload = {**state.as_dict(), "stats": stats,
                           "mode": self.hub.mode, "transport": self.hub.transport,
                           "open_slots": max(0, self.store.cfg.risk.max_concurrent_trades
                                             - state.open_positions),
                           "margin_budget": round(self.journal.margin_budget(), 2)}
                self.last_equity = payload
                BUS.publish("equity.update", payload)
                # keep the paper account durable across restarts (sim only):
                # every tick, so a restart can never lose a closed trade
                await self.hub.persist_sim_state()
                self.registry.set_status(
                    "equity-manager-bot", "working", "balance sync",
                    message=f"equity ${state.equity:,.2f} · "
                            f"{state.open_positions}/{self.store.cfg.risk.max_concurrent_trades} slots · "
                            f"budget ${self.journal.margin_budget():,.2f}/trade", publish=False)
                self.registry.set_status(
                    "trade-manager-bot", "working", "journal sync",
                    message=f"{stats.get('total_trades', 0)} closed · "
                            f"WR {stats.get('win_rate', 0):.1f}% · net ${stats.get('net_pnl', 0):,.2f}",
                    publish=False)
            except asyncio.CancelledError:
                raise
            except RateLimitHalt as exc:
                self._note_blocked("equity-manager-bot", str(exc))
            except Exception as exc:
                with contextlib.suppress(Exception):
                    self.log("warn", "equity-manager-bot", f"equity sync failed: {str(exc)[:180]}")

    async def _scan_loop(self) -> None:
        """Aligned to the 5-minute candle close (+2s), one workflow cycle each bar."""
        while self.running:
            try:
                wait = self.hub.seconds_to_close() + 2.0
                if self.hub.transport == "sim":
                    wait = min(wait, self.hub.exchange.tick_seconds + 1.5)
                await asyncio.sleep(max(1.0, wait))
                if not self.running:
                    return
                await self.run_cycle()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                with contextlib.suppress(Exception):
                    self.log("error", "ceo-bot", f"cycle failed: {str(exc)[:200]}")
                await asyncio.sleep(5)

    async def run_cycle(self, force: bool = False) -> dict[str, Any]:
        """One full pass of the CEO workflow."""
        if self.paused and not force:
            self._wf("idle", "skipped", "paused — waiting for connector green")
            return {"skipped": "paused"}
        if not self.hub.universe:
            await self.hub.build_universe()
        marker = self._current_bar_marker()
        if marker and marker == getattr(self, "_last_cycle_bar", None) and not force:
            return {"skipped": "same bar"}
        self._last_cycle_bar = marker
        self.cycle += 1
        self.workflow["cycle"] = self.cycle
        self.last_scan_at = now_ms()
        t0 = time.time()
        self.registry.set_status("ceo-bot", "working", f"cycle #{self.cycle}",
                                 message="orchestrating the swarm", progress=0.05)

        # ── 1. scanners (5 bots in parallel) ──────────────────────────────
        self._wf("scan", "start", "5 scanner bots sweeping the volatility-ranked universe")
        opportunities = await self._stage_scan()
        self.scan_snapshot["gate_rejections"] = dict(self.gate_rejections)
        self._wf("scan", "done", f"{len(opportunities)} opportunities",
                 count=len(opportunities),
                 gates_filtered=sum(self.gate_rejections.values()))
        if self.gate_rejections:
            parts = ", ".join(f"{k} {v}" for k, v in
                              sorted(self.gate_rejections.items(),
                                     key=lambda kv: -kv[1]))
            self.log("info", "ceo-bot",
                     f"Strategy gates filtered {sum(self.gate_rejections.values())} "
                     f"flips this bar ({parts})", topic="scan")

        # ── 1b. instant reverse-signal exits (indicator flip closes position) ──
        await self._handle_reverse_exits(opportunities)

        if not opportunities:
            self.registry.set_status("ceo-bot", "success", f"cycle #{self.cycle}",
                                     message="no confirmed flips this bar")
            self._wf("idle", "done", f"cycle {self.cycle} complete — no signals")
            return {"opportunities": 0, "opened": 0, "elapsed": round(time.time() - t0, 2)}

        # ── 2. analysts (up to 10 in parallel) ────────────────────────────
        self._wf("analyze", "start", f"analyst team scoring {len(opportunities)} opportunities")
        proposals = await self._stage_analyze(opportunities)
        approved = [p for p in proposals if p.approved]
        self._wf("analyze", "done",
                 f"{len(approved)}/{len(proposals)} approved above confidence threshold",
                 approved=len(approved), total=len(proposals))

        # ── 3. execution ──────────────────────────────────────────────────
        opened: list[dict] = []
        if approved:
            self._wf("execute", "start", f"executing {len(approved)} approved setups")
            opened = await self._stage_execute(approved)
            self._wf("execute", "done", f"{len(opened)} positions opened", count=len(opened))

        # ── 4. verification ───────────────────────────────────────────────
        if opened:
            self._wf("verify", "start", "Info Bot verifying fills and protection")
            verified = await self._stage_verify(opened)
            self._wf("verify", "done" if verified else "error",
                     f"{len(verified)} positions verified on the exchange",
                     count=len(verified))

        # ── 5. monitoring handover ────────────────────────────────────────
        if self.symbol_to_trade:
            self._wf("monitor", "start", "Trade Monitor team watching live positions")
            await self._assign_monitors()

        self.registry.set_status("ceo-bot", "success", f"cycle #{self.cycle}",
                                 message=f"{len(opened)} opened / {len(approved)} approved / "
                                         f"{len(opportunities)} scanned")
        for bot in self.registry.by_group("core") + self.registry.by_group("verify") \
                + self.registry.by_group("finance"):
            if bot.status not in ("fired", "offline"):
                self.registry.note_unit(bot.bot_id, "cycle")
        self.log("info", "ceo-bot",
                 f"Cycle #{self.cycle} complete in {time.time() - t0:.2f}s — "
                 f"{len(opportunities)} opportunities, {len(approved)} approved, "
                 f"{len(opened)} executed")
        return {"opportunities": len(opportunities), "approved": len(approved),
                "opened": len(opened), "elapsed": round(time.time() - t0, 2)}

    def _current_bar_marker(self) -> int | None:
        for sym in self.hub.universe[:1] or list(self.hub.symbols.keys())[:1]:
            candles = self.hub.candles(sym, 1)
            if candles:
                return candles[-1].t
        return None

    # ============================================================ stage 1: scan
    async def _compute_series(self, symbol: str, force: bool = False):
        """Ghost/Shadow-Rail series for a symbol, cached by last closed bar."""
        candles = self.hub.candles(symbol)
        if len(candles) < 220:
            return None
        last_t = candles[-1].t
        cached = self.series_cache.get(symbol)
        if cached and cached[0] == last_t and not force:
            return cached[1]
        params = ghost.GhostParams(**{
            k: getattr(self.store.cfg.indicator, k)
            for k in ("swingBars", "railSpread", "railDrive", "ghostBlur", "ghostOffset",
                      "ghostPlacement", "ghostEase", "mtfGate", "mtfFrame", "mtfEmaBars",
                      "minTrendPct", "require_strong_flip")
        }, slAtrX=self.risk.s.active_tp_sl()[0], tpAtrX=max(0.1, self.risk.s.active_tp_sl()[1] or 3.0))
        strat_cfg = self.store.cfg.strategy
        tf = self.store.cfg.engine.monitored_timeframe

        def _run() -> ghost.GhostSeries:
            series = ghost.compute(candles, params, tf)
            if strat_cfg.v2_enabled:
                series.strategy = enrich(series, strat_cfg)
            return series

        series = await asyncio.to_thread(_run)
        self.series_cache[symbol] = (last_t, series)
        return series

    async def _stage_scan(self) -> list[Opportunity]:
        buckets = self.scanner_buckets or [self.hub.universe]
        results: list[Opportunity] = []
        lock = asyncio.Lock()
        self.gate_rejections = {}

        async def scan_bot(index: int, symbols: list[str]) -> None:
            bot_id = f"scanner-{index+1}"
            bot = self.registry.get(bot_id)
            t_bot = time.perf_counter()
            self.registry.set_status(bot_id, "working", f"sweeping {len(symbols)} assets",
                                     progress=0.0)
            found = 0
            rows: list[dict] = []
            for i, sym in enumerate(symbols):
                try:
                    series = await self._compute_series(sym)
                except Exception as exc:
                    self.registry.note_task(bot_id, ok=False, error=True)
                    self.log("warn", bot_id, f"scan error {sym}: {str(exc)[:140]}")
                    continue
                if series is None:
                    continue
                rows.append(self._scan_row(sym, series))
                sig = ghost.latest_signal(series)
                if not sig:
                    continue
                found += 1
                opp = await self._to_opportunity(sym, sig, series, bot_id)
                if opp:
                    async with lock:
                        results.append(opp)
                self.registry.set_status(
                    bot_id, "working", f"sweeping {len(symbols)} assets",
                    message=f"{sym} flip → {sig['direction']} (quality {sig['trend_quality']:.2f})",
                    progress=(i + 1) / max(1, len(symbols)), publish=False)
            latency = (time.perf_counter() - t_bot) * 1000.0
            async with lock:
                self.scan_snapshot["by_bot"][bot_id] = rows
                self.scan_snapshot["updated_at"] = now_ms()
                self.scan_snapshot["cycle"] = self.cycle
                self.registry.note_task(bot_id, ok=True, latency_ms=latency)
            self.registry.set_status(bot_id, "success", "sweep complete",
                                     message=f"{found} flip(s) · {len(symbols)} assets · "
                                             f"{latency/1000:.1f}s")
            self.registry.publish(bot_id, GOVERNOR.snapshot())
            self.log("info", bot_id,
                     f"Sweep done: {found} flip(s) across {len(symbols)} assets "
                     f"({latency/1000:.1f}s) → reporting to CEO", topic="scan")
            if bot:
                bot.mood = "happy" if found else bot.mood

        # A hung exchange call must never stall the whole 5-minute cycle: the
        # sweep is bounded and whatever the scanners already found is kept.
        try:
            await asyncio.wait_for(
                asyncio.gather(*(scan_bot(i, bucket) for i, bucket in enumerate(buckets)),
                               return_exceptions=True),
                timeout=SCAN_TIMEOUT_S)
        except asyncio.TimeoutError:
            self.log("warn", "ceo-bot",
                     f"scan sweep exceeded {SCAN_TIMEOUT_S}s — continuing with the "
                     f"{len(results)} opportunit{'y' if len(results) == 1 else 'ies'} found so far")
        results.sort(key=lambda o: (o.tier == "strong", o.trend_quality), reverse=True)
        return results[:MAX_OPPORTUNITIES_PER_CYCLE]

    def _warn_htf_once(self, symbol: str) -> None:
        if getattr(self, "_htf_warned", False):
            return
        self._htf_warned = True
        self.log("warn", "analyst-1",
                 f"HTF gate not warm yet (needs >= {int(self.store.cfg.indicator.mtfEmaBars)+1} "
                 f"closed {self.store.cfg.indicator.mtfFrame} bars) — symbols are skipped "
                 f"until history is deep enough (increase kline_warmup if this persists)")

    def _scan_row(self, symbol: str, series) -> dict[str, Any]:
        """Compact per-symbol market state for the scanner page."""
        i = series.last
        trend = int(series.trend[i])
        cr = series.clean_ratio[i]
        rail = series.rail_value(i)
        price = float(series.close[i])
        atr = series.atr14[i]
        return {
            "symbol": symbol,
            "price": price,
            "trend": "LONG" if trend == 1 else ("SHORT" if trend == -1 else "—"),
            "trend_side": trend,
            "quality": round(float(cr), 3) if not math.isnan(cr) else None,
            "rail": round(float(rail), 8) if not math.isnan(rail) else None,
            "rail_distance_pct": round((price - rail) / price * 100.0, 3)
            if rail and not math.isnan(rail) else None,
            "atr_pct": round(float(atr) / price * 100.0, 3) if not math.isnan(atr) and price else None,
            "htf_bull": bool(series.htf_bull[i] == 1.0),
            "ghost_close": round(float(series.ghost_close[i]), 8)
            if not math.isnan(series.ghost_close[i]) else None,
            "flip": ("LONG" if series.turn_up[i] else ("SHORT" if series.turn_dn[i] else None)),
            "tier": "strong" if (series.strong_up[i] or series.strong_dn[i]) else None,
            "has_position": symbol in self.symbol_to_trade,
            "bar_time": int(series.times[i]),
        }

    async def _to_opportunity(self, symbol: str, sig: dict, series, scanner_id: str
                              ) -> Opportunity | None:
        bar = self.hub.candles(symbol, 2)
        if not bar:
            return None
        if sig["bar_time"] != bar[-1].t:
            return None                                  # stale (not the latest bar)
        if symbol in self.symbol_to_trade:
            return None                                  # one position per symbol
        if self.store.cfg.indicator.mtfGate and not sig.get("htf_ready", True):
            self._warn_htf_once(symbol)
            return None                                  # gate not warm yet → skip
        if self.cooldowns.get(symbol, 0) > time.time():
            return None
        # ── Strategy v2 entry gates (trend strength / vol regime / momentum) ──
        strat = getattr(series, "strategy", None)
        if strat is not None:
            ok, reasons = strat.gate(series.last, sig["direction"],
                                     self.store.cfg.strategy)
            if not ok:
                for r in reasons:
                    self.gate_rejections[r.split(" ")[0]] = \
                        self.gate_rejections.get(r.split(" ")[0], 0) + 1
                self.log("debug", scanner_id,
                         f"{symbol} {sig['direction']} flip vetoed by strategy gate: "
                         f"{'; '.join(reasons)}", topic="scan")
                return None
        return Opportunity(
            symbol=symbol, direction=sig["direction"], entry=sig["entry"],
            stop=sig["stop"], target=sig["target"], atr=sig["atr"],
            atr_pct=sig["atr_pct"], trend_quality=sig["trend_quality"], tier=sig["tier"],
            rail=sig.get("rail"), rail_distance_pct=sig.get("rail_distance_pct", 0.0),
            htf_bull=bool(sig.get("htf_bull")), flow_bias=float(sig.get("flow_bias", 0.0)),
            volume=sig.get("volume", 0.0), bar_time=sig["bar_time"], scanner_id=scanner_id,
            features={
                "trend_quality": sig["trend_quality"], "tier": sig["tier"],
                "htf_bull": bool(sig.get("htf_bull")), "atr_pct": sig["atr_pct"],
                "rail_distance_pct": sig.get("rail_distance_pct", 0.0),
                "flow_bias": float(sig.get("flow_bias", 0.0)),
                "symbol": symbol, "direction": sig["direction"],
                **(strat.features(series.last) if strat is not None else {}),
            })

    # =========================================================== stage 2: analysts
    async def _stage_analyze(self, opportunities: list[Opportunity]) -> list[Proposal]:
        analysts = self.registry.by_group("analyst")
        if not analysts:
            return []
        sym_stats = await self.journal.symbol_stats()
        queue: asyncio.Queue = asyncio.Queue()
        for i, opp in enumerate(opportunities):
            queue.put_nowait((i, opp))
        proposals: list[Proposal] = []
        lock = asyncio.Lock()

        async def analyst_worker(bot) -> None:
            while True:
                try:
                    idx, opp = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                t0 = time.perf_counter()
                self.registry.set_status(bot.bot_id, "working",
                                         f"analysing {opp.symbol} {opp.direction}",
                                         progress=0.4)
                # small deterministic per-analyst variance (independent opinions)
                variance = ((hash(bot.bot_id + opp.symbol) % 7) - 3) * 0.9
                res = MODEL.score(opp.features, sym_stats.get(opp.symbol),
                                  analyst_variance=variance)
                threshold = self.store.cfg.risk.min_confidence
                # Strategy v2: hard veto on structurally losing symbols — a
                # soft score penalty is not enough to keep us re-buying a name
                # that keeps losing.
                veto = symbol_vetoed(sym_stats.get(opp.symbol),
                                     self.store.cfg.strategy)
                if veto:
                    approved = False
                    reason = veto
                else:
                    approved = res.score >= threshold
                    reason = "" if approved else \
                        f"confidence {res.score:.1f} < threshold {threshold:.1f}"
                prop = Proposal(opportunity=opp, analyst_id=bot.bot_id,
                                confidence=res.score, approved=approved,
                                factors=res.factors, notes=res.notes, model=res.model,
                                reason=reason)
                latency = (time.perf_counter() - t0) * 1000.0
                async with lock:
                    proposals.append(prop)
                    self.recent_analyst_rows.insert(0, prop.as_dict())
                    del self.recent_analyst_rows[60:]
                    self.registry.note_task(bot.bot_id, ok=True, latency_ms=latency)
                self.registry.set_status(
                    bot.bot_id, "success" if approved else "idle",
                    f"{opp.symbol} {'APPROVED' if approved else 'rejected'}",
                    message=f"confidence {res.score:.1f} · {opp.direction} · "
                            f"quality {opp.trend_quality:.2f}",
                    progress=1.0)
                self.registry.publish(bot.bot_id, GOVERNOR.snapshot())
                BUS.publish("analysis.result", prop.as_dict())
                self.log("success" if approved else "info", bot.bot_id,
                         f"{opp.symbol} {opp.direction} — confidence {res.score:.1f} "
                         f"({'approved for execution' if approved else 'rejected'})",
                         prop.as_dict(), topic="analysis")
                bot.mood = "excited" if approved and res.score >= 80 else bot.mood

        await asyncio.gather(*(analyst_worker(b) for b in analysts), return_exceptions=True)
        proposals.sort(key=lambda p: p.confidence, reverse=True)
        if proposals:
            self.log("info", "ceo-bot",
                     f"Analyst team reported {len([p for p in proposals if p.approved])} "
                     f"approved setups of {len(proposals)}", topic="analysis")
        return proposals

    # ========================================================== stage 3: execute
    async def _stage_execute(self, proposals: list[Proposal]) -> list[dict]:
        execs = self.registry.by_group("execution") or [self.registry.get("execution-1")]
        execs = [b for b in execs if b]
        if not execs:
            return []
        cfg = self.store.cfg
        self.risk = RiskEngine(cfg.risk)
        account = await self.hub.account()
        state = await self.journal.update(account)
        await self.journal.refresh_stats()          # keeps the dashboard stats hot
        open_count = len(self.open_trades)

        # hard guards before spending any API weight
        if self.journal.daily_drawdown_breached():
            self.log("warn", "risk-bot",
                     f"Daily drawdown guard tripped ({state.daily_pnl:.2f}) — no new trades")
            self.registry.set_status("risk-bot", "error", "daily drawdown guard",
                                     message="new entries blocked for today")
            return []
        if open_count >= cfg.risk.max_concurrent_trades:
            self.log("info", "risk-bot",
                     f"Max concurrent trades reached ({open_count}/{cfg.risk.max_concurrent_trades})")
            return []

        opened: list[dict] = []
        queue: asyncio.Queue = asyncio.Queue()
        for p in proposals:
            queue.put_nowait(p)
        lock = asyncio.Lock()

        async def executor(bot) -> None:
            while True:
                async with lock:
                    if len(self.open_trades) >= cfg.risk.max_concurrent_trades:
                        return
                try:
                    proposal = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                opp = proposal.opportunity
                if opp.symbol in self.symbol_to_trade:
                    continue
                # Strategy v2: direction crowding — ten correlated alts all in
                # the same direction is one trade, not ten.
                if cfg.strategy.v2_enabled:
                    side = "LONG" if opp.direction == "LONG" else "SHORT"
                    async with lock:
                        open_sides = [t["side"] for t in self.open_trades.values()]
                    if direction_crowded(side, open_sides, cfg.strategy):
                        same = sum(1 for s in open_sides if s == side)
                        self.log("info", "risk-bot",
                                 f"{opp.symbol}: skipped — {side} crowding cap "
                                 f"{same}/{cfg.strategy.max_same_direction}",
                                 topic="execution")
                        continue
                t0 = time.perf_counter()
                self.registry.set_status(bot.bot_id, "working",
                                         f"executing {opp.symbol} {opp.direction}",
                                         progress=0.3)
                trade = await self._open_trade(bot.bot_id, proposal, {
                    "equity": state.equity, "available": state.available,
                    "open_count": len(self.open_trades)})
                latency = (time.perf_counter() - t0) * 1000.0
                async with lock:
                    self.registry.note_task(bot.bot_id, ok=bool(trade), latency_ms=latency)
                    if trade:
                        opened.append(trade)
                self.registry.publish(bot.bot_id, GOVERNOR.snapshot())

        await asyncio.gather(*(executor(b) for b in execs), return_exceptions=True)
        if opened:
            self.log("success", "ceo-bot",
                     f"Execution team opened {len(opened)} position(s): "
                     + ", ".join(t["symbol"] for t in opened), topic="execution")
        return opened

    async def _open_trade(self, exec_bot_id: str, proposal: Proposal,
                          ctx: dict[str, Any]) -> dict | None:
        """
        Open one position.  The whole step runs under `self._open_lock` so the
        concurrency cap, the margin budget and the API ceiling are re-checked
        against *live* state immediately before the order is sent.
        """
        async with self._open_lock:
            return await self._open_trade_locked(exec_bot_id, proposal, ctx)

    async def _open_trade_locked(self, exec_bot_id: str, proposal: Proposal,
                                 ctx: dict[str, Any]) -> dict | None:
        cfg = self.store.cfg
        opp = proposal.opportunity
        symbol = opp.symbol
        side = "LONG" if opp.direction == "LONG" else "SHORT"
        flt = self.hub.filters.get(symbol)
        if not flt:
            self.log("warn", exec_bot_id, f"{symbol}: no exchange filters — skipped")
            return None

        # ── hard caps re-verified against live state ─────────────────────
        if len(self.open_trades) >= cfg.risk.max_concurrent_trades:
            self.log("info", "risk-bot",
                     f"{symbol}: skipped — {len(self.open_trades)}/"
                     f"{cfg.risk.max_concurrent_trades} slots already in use")
            return None
        if symbol in self.symbol_to_trade:
            return None
        api = GOVERNOR.snapshot()
        if api["halted"]:
            self.log("warn", "api-guard-bot",
                     f"{symbol}: entry blocked — API weight at {api['used_pct']:.1f}% "
                     f"(ceiling {api['budget_pct']:.0f}%). Close/protect paths stay allowed.")
            self.registry.set_status(exec_bot_id, "blocked", "API ceiling",
                                     message=f"{api['used_pct']:.1f}% of the IP budget used")
            return None

        # fresh equity so the 8% sizing reflects the current balance
        with contextlib.suppress(Exception):
            account = await self.hub.account()
            state = await self.journal.update(account, persist=False)
            ctx = {**ctx, "equity": state.equity, "available": state.available,
                   "open_count": len(self.open_trades)}
        price = self.hub.price(symbol) or opp.entry
        plan = self.risk.plan(symbol=symbol, side=side, price=price, atr=opp.atr,
                              equity=ctx["equity"], available=ctx["available"], flt=flt,
                              open_trades=len(self.open_trades),
                              confidence=proposal.confidence)
        if not plan.ok:
            self.log("info", exec_bot_id,
                     f"{symbol}: sizing rejected — {plan.reason}", {"plan": plan.as_dict()},
                     topic="execution")
            self.registry.set_status(exec_bot_id, "idle", f"{symbol} rejected",
                                     message=plan.reason[:140])
            return None

        # ── risk manager sign-off ────────────────────────────────────────
        self.registry.set_status("risk-bot", "working", f"validating {symbol}",
                                 message=f"stop {plan.sl_price:.6g} vs liq {plan.liquidation_price:.6g}")
        if not (plan.liquidation_price < plan.sl_price < price if side == "LONG"
                else price < plan.sl_price < plan.liquidation_price):
            self.log("error", "risk-bot",
                     f"BLOCKED {symbol}: stop {plan.sl_price} not inside liquidation "
                     f"{plan.liquidation_price}")
            return None
        if plan.stop_clamped:
            self.log("warn", "risk-bot",
                     f"{symbol}: ATR stop tightened to stay inside liquidation "
                     f"(stop {plan.sl_price:.6g} · liq {plan.liquidation_price:.6g})")

        trade_row: dict[str, Any] | None = None
        try:
            await self.hub.set_leverage(symbol, cfg.risk.leverage)
            await self.hub.set_margin_type(symbol, cfg.risk.margin_type)
        except RateLimitHalt as exc:
            self._note_blocked(exec_bot_id, str(exc))
            return None
        except ExchangeError as exc:
            self.log("warn", exec_bot_id,
                     f"{symbol}: leverage/margin setup warning — {str(exc)[:140]}")

        try:
            order = await self.hub.market_order(
                symbol, "BUY" if side == "LONG" else "SELL", plan.qty,
                client_id=f"SR{int(time.time())}{symbol[:6]}")
        except RateLimitHalt as exc:
            self._note_blocked(exec_bot_id, str(exc))
            return None
        except ExchangeError as exc:
            self.registry.note_task(exec_bot_id, ok=False, error=True)
            self.log("error", exec_bot_id, f"{symbol}: entry order failed — {str(exc)[:160]}")
            return None

        fill_price = order.avg_price or price
        entry = fill_price if fill_price > 0 else price

        # stop/target recomputed from the *actual* fill so risk maths stay true
        sl_mult, tp_mult, tp_on = cfg.risk.active_tp_sl()
        sl_price = plan.sl_price
        tp_price = plan.tp_price if plan.tp_enabled else 0.0
        drift = abs(entry - price) / price if price else 0.0
        if drift > 0.0002:
            recompute = self.risk.plan(symbol=symbol, side=side, price=entry, atr=opp.atr,
                                       equity=ctx["equity"], available=ctx["available"],
                                       flt=flt, open_trades=ctx["open_count"])
            if recompute.ok:
                sl_price, tp_price = recompute.sl_price, recompute.tp_price
                plan.sl_price, plan.tp_price = sl_price, tp_price

        # ── protection: ONE system only, never double ────────────────────
        protect_ok = True
        sl_order_id = ""
        tp_order_id = ""
        try:
            sl_order = await self.hub.stop_market(
                symbol, "SELL" if side == "LONG" else "BUY", sl_price,
                close_position=True, client_id=f"SRsl{int(time.time())}")
            sl_order_id = sl_order.order_id
        except RateLimitHalt as exc:
            self._note_blocked(exec_bot_id, str(exc))
            protect_ok = False
        except Exception as exc:            # any failure ⇒ flatten immediately below
            self.log("error", exec_bot_id,
                     f"{symbol}: STOP order failed ({str(exc)[:140]}) — closing immediately",
                     topic="execution")
            protect_ok = False

        if protect_ok and tp_on and tp_price > 0:
            try:
                tp_order = await self.hub.take_profit_market(
                    symbol, "SELL" if side == "LONG" else "BUY", tp_price,
                    close_position=True, client_id=f"SRtp{int(time.time())}")
                tp_order_id = tp_order.order_id
            except RateLimitHalt as exc:
                self._note_blocked(exec_bot_id, str(exc))
            except Exception as exc:        # TP is optional; the reverse signal covers us
                self.log("warn", exec_bot_id,
                         f"{symbol}: TP order failed — position runs to the reverse "
                         f"signal / stop ({str(exc)[:140]})")

        if not protect_ok:
            # fail-safe: never hold an unprotected position
            with contextlib.suppress(Exception):
                await self.hub.market_order(symbol, "SELL" if side == "LONG" else "BUY",
                                            plan.qty, reduce_only=True)
            self.log("sos", "risk-bot",
                     f"{symbol}: protection failed — position closed immediately (fail-safe)")
            return None

        # the entry commission is *paid now*: remember it on the row so a close
        # that happens in a later run (after a restart) still books this leg
        entry_fee = abs(float(plan.qty)) * float(entry) * TAKER_FEE
        trade_row = {
            "symbol": symbol, "side": side, "status": "open", "qty": plan.qty,
            "entry_price": entry, "leverage": cfg.risk.leverage, "margin": plan.margin,
            "notional": plan.notional, "sl_price": sl_price, "tp_price": tp_price,
            "liquidation_price": plan.liquidation_price,
            "sl_atr_mult": sl_mult if cfg.risk.risk_mode != "indicator_default" else 1.5,
            "tp_atr_mult": (tp_mult if tp_on else 0.0),
            "risk_mode": cfg.risk.risk_mode,
            "opened_at": now_ms(), "close_reason": None,
            "gross_pnl": 0.0, "fee_paid": 0.0, "entry_fee": entry_fee,
            "funding_paid": 0.0, "net_pnl": 0.0,
            "signal_confidence": proposal.confidence, "signal_tier": opp.tier,
            "analyst_id": proposal.analyst_id, "scanner_id": opp.scanner_id,
            "exec_bot_id": exec_bot_id, "monitor_bot_id": "",
            "entry_order_id": order.order_id, "exit_order_id": "",
            "sl_order_id": sl_order_id, "peak_price": entry,
            "trail_active": 0, "trail_stop": 0.0,
            "mode": self.hub.mode,
            "notes": json.dumps({
                "features": opp.features, "factors": proposal.factors,
                "notes": proposal.notes, "model": proposal.model,
                "stop_clamped": plan.stop_clamped, "warnings": plan.warnings,
                "sl_order_id": sl_order_id, "tp_order_id": tp_order_id,
                "planned_sl_atr": sl_mult, "planned_tp_atr": tp_mult if tp_on else 0.0,
                "atr": opp.atr, "bar_time": opp.bar_time,
                "sl_distance0": abs(entry - sl_price),   # 1 R in price units
                "liquidation_estimate": plan.liquidation_price,
            }),
        }
        trade_id = await DB.insert_trade(trade_row)
        trade_row["id"] = trade_id
        await DB.add_trade_event(trade_id, "opened",
                                 {"entry": entry, "qty": plan.qty, "sl": sl_price,
                                  "tp": tp_price, "leverage": cfg.risk.leverage})
        self.open_trades[trade_id] = trade_row
        self.symbol_to_trade[symbol] = trade_id
        self._assign_monitor_for(symbol)

        # ── bookkeeping for the bots that made it happen ──────────────────
        for bot in self.registry.all():
            if bot.bot_id in (opp.scanner_id, proposal.analyst_id, exec_bot_id):
                bot.mood = "excited"
        self.registry.set_status(exec_bot_id, "success", f"{symbol} opened",
                                 message=f"{side} {plan.qty:.6g} @ {entry:.6g} · "
                                         f"SL {sl_price:.6g} · "
                                         f"{'TP ' + format(tp_price, '.6g') if tp_price else 'no fixed TP'}",
                                 progress=1.0)
        self.registry.set_status("risk-bot", "success", f"{symbol} cleared",
                                 message=f"stop inside liquidation · risk ${plan.risk_amount:,.2f} "
                                         f"({plan.risk_pct_equity:.2f}% equity)")
        self.log("success", exec_bot_id,
                 f"{symbol} {side} filled {plan.qty:.6g} @ {entry:.6g} (lev {cfg.risk.leverage}x "
                 f"{cfg.risk.margin_type}) · SL {sl_price:.6g} · "
                 f"TP {format(tp_price, '.6g') if tp_price else 'reverse-signal exit'} · "
                 f"liq {plan.liquidation_price:.6g}",
                 {"trade": {k: v for k, v in trade_row.items() if k != "notes"}},
                 topic="execution")
        BUS.publish("trade.opened", {"trade": {k: v for k, v in trade_row.items()
                                               if k != "notes"},
                                     "proposal": proposal.as_dict()})
        return trade_row

    # =========================================================== stage 4: verify
    async def _stage_verify(self, trades: list[dict]) -> list[dict]:
        bot_id = "info-verifier-bot"
        self.registry.set_status(bot_id, "working", f"verifying {len(trades)} position(s)",
                                 progress=0.2)
        positions = {p.symbol: p for p in await self.hub.positions()}
        verified: list[dict] = []
        for trade in trades:
            sym = trade["symbol"]
            pos = positions.get(sym)
            problems: list[str] = []
            if not pos:
                problems.append("position not visible on the exchange")
            else:
                if abs(pos.qty - trade["qty"]) / max(trade["qty"], 1e-9) > 0.02:
                    problems.append(f"qty mismatch {pos.qty} vs {trade['qty']}")
                if (trade["side"] == "LONG" and pos.side != "LONG") or \
                        (trade["side"] == "SHORT" and pos.side != "SHORT"):
                    problems.append(f"side mismatch {pos.side} vs {trade['side']}")
                if pos.liquidation_price > 0:
                    # LONG:  liq < stop < entry     SHORT: entry < stop < liq
                    if trade["side"] == "LONG" and trade["sl_price"] <= pos.liquidation_price:
                        problems.append(f"stop {trade['sl_price']:.6g} at/through liquidation "
                                        f"{pos.liquidation_price:.6g}")
                    if trade["side"] == "SHORT" and trade["sl_price"] >= pos.liquidation_price:
                        problems.append(f"stop {trade['sl_price']:.6g} at/through liquidation "
                                        f"{pos.liquidation_price:.6g}")
            orders = []
            with contextlib.suppress(Exception):
                orders = await self.hub.open_orders(sym)
            has_stop = any(str(o.get("type")) in ("STOP_MARKET", "STOP") for o in orders)
            has_tp = any(str(o.get("type")) in ("TAKE_PROFIT_MARKET", "TAKE_PROFIT") for o in orders)
            if not has_stop:
                problems.append("no exchange-side stop order found")
            if trade.get("tp_price") and not has_tp:
                problems.append("no take-profit order found")

            if problems:
                self.log("error", bot_id, f"{sym}: verification FAILED — {'; '.join(problems)}",
                         {"trade_id": trade["id"], "problems": problems}, topic="verify")
                await DB.add_trade_event(int(trade["id"]), "verify_failed", problems)
                self.registry.set_status(bot_id, "error", f"{sym} verify failed",
                                         message="; ".join(problems)[:160])
                self.registry.note_task(bot_id, ok=False, error=True)
                # a missing stop on a live position is an emergency
                if any("stop" in p for p in problems):
                    await self._close_trade(int(trade["id"]), "emergency",
                                            reason_detail="; ".join(problems))
            else:
                verified.append(trade)
                await DB.add_trade_event(int(trade["id"]), "verified",
                                         {"orders": len(orders)})
                self.log("success", bot_id,
                         f"{sym}: execution verified — position, quantity, stop and target all "
                         f"match the order plan", {"trade_id": trade["id"]}, topic="verify")
        self.registry.note_task(bot_id, ok=True)
        self.registry.set_status(bot_id, "success", f"{len(verified)}/{len(trades)} verified",
                                 message="report sent to Engine CEO", progress=1.0)
        self.registry.publish(bot_id, GOVERNOR.snapshot())
        return verified

    # ========================================================== stage 5: monitor
    def _assign_monitor_for(self, symbol: str) -> str:
        """Seat the position with the least-loaded desk for its level, so a
        promoted monitor carries more of the book and a rookie carries less."""
        monitors = [b for b in self.registry.by_group("monitor")
                    if b.status != "fired"]
        if not monitors:
            return ""
        load = {b.bot_id: sum(1 for v in self.symbol_monitor.values()
                              if v == b.bot_id) for b in monitors}
        # weighted fair share: seat the symbol where the *next* position costs
        # the least fraction of that desk's capacity, so the Legend carries the
        # most and a fresh Recruit carries the least
        bot = min(monitors, key=lambda b: ((load[b.bot_id] + 1) / max(1, b.capacity),
                                           load[b.bot_id], b.slot))
        self.symbol_monitor[symbol] = bot.bot_id
        trade_id = self.symbol_to_trade.get(symbol)
        if trade_id and trade_id in self.open_trades:
            self.open_trades[trade_id]["monitor_bot_id"] = bot.bot_id
            with contextlib.suppress(Exception):
                asyncio.create_task(DB.update_trade(trade_id, {"monitor_bot_id": bot.bot_id}))
        bot.assigned = [s for s, b in self.symbol_monitor.items() if b == bot.bot_id]
        return bot.bot_id

    async def _assign_monitors(self) -> None:
        monitors = self.registry.by_group("monitor")
        for i, symbol in enumerate(list(self.symbol_to_trade.keys())):
            if symbol not in self.symbol_monitor:
                self._assign_monitor_for(symbol)
        for bot in monitors:
            watched = [s for s, b in self.symbol_monitor.items() if b == bot.bot_id]
            if watched:
                self.registry.set_status(bot.bot_id, "working", f"watching {len(watched)} position(s)",
                                         message=", ".join(watched[:4]), publish=False)
            else:
                self.registry.set_status(bot.bot_id, "idle", "standing by", publish=False)

    async def _monitor_loop(self) -> None:
        """Trade Monitor team + close detection (5s cadence)."""
        tick = max(2, int(self.store.cfg.engine.monitor_tick_s))
        while self.running:
            try:
                await asyncio.sleep(tick)
                if not self.running:
                    return
                if not self.open_trades:
                    await self._assign_monitors()
                    continue
                positions = {p.symbol: p for p in await self.hub.positions()}
                for trade_id, trade in list(self.open_trades.items()):
                    sym = trade["symbol"]
                    pos = positions.get(sym)
                    if pos is None and sym not in self.pending_close:
                        await self._finalize_close(trade_id, fallback_price=self.hub.price(sym))
                    elif pos is not None:
                        await self._monitor_tick(trade, pos)
                self._pulse_links("monitor")
            except asyncio.CancelledError:
                raise
            except RateLimitHalt as exc:
                self._note_blocked("monitor-team", str(exc))
            except Exception as exc:
                with contextlib.suppress(Exception):
                    self.log("warn", "monitor-1", f"monitor cycle error: {str(exc)[:160]}")

    async def _monitor_tick(self, trade: dict, pos: Position) -> None:
        bot_id = trade.get("monitor_bot_id") or self.symbol_monitor.get(trade["symbol"], "monitor-1")
        entry = float(trade["entry_price"])
        mark = pos.mark_price or self.hub.price(trade["symbol"])
        side = trade["side"]
        qty = float(trade["qty"])
        margin = float(trade.get("margin") or 0.0)
        pnl = (mark - entry) * qty * (1 if side == "LONG" else -1)
        roi = roi_points(entry, mark, qty, margin, side)
        # Strategy v2 exit layers, then the ROI trail on top — every one of
        # them only ever tightens the single protective stop:
        await self._breakeven_tick(trade, pos, mark, bot_id)
        if trade["id"] not in self.open_trades:
            return                              # protection-lost flatten above
        await self._trail_tick(trade, pos, mark, bot_id)
        if trade["id"] not in self.open_trades:
            return
        await self._time_stop_tick(trade, mark, bot_id)
        tp = float(trade.get("tp_price") or 0)
        sl = float(trade.get("sl_price") or 0)
        liq = pos.liquidation_price or float(trade.get("liquidation_price") or 0)
        to_tp = ((tp - mark) / mark * 100.0 * (1 if side == "LONG" else -1)) if tp else 0.0
        to_sl = ((mark - sl) / mark * 100.0 * (1 if side == "LONG" else -1))
        warn = ""
        if liq > 0:
            dist_liq = abs(mark - liq) / mark * 100.0
            if dist_liq < 2.0:
                warn = f"⚠ {dist_liq:.2f}% from liquidation"
        peak = float(trade.get("peak_price") or entry)
        peak_roi = roi_points(entry, peak, qty, margin, side)
        state = {
            "trade_id": trade["id"], "symbol": trade["symbol"], "side": side,
            "qty": qty, "entry": entry, "mark": mark, "margin": margin,
            "unrealized": round(pnl, 4), "tp": tp, "sl": sl, "liq": liq,
            "to_tp_pct": round(to_tp, 3), "to_sl_pct": round(to_sl, 3),
            "leverage": trade["leverage"], "monitor_id": bot_id,
            "opened_at": trade["opened_at"], "confidence": trade.get("signal_confidence"),
            "warning": warn,
            "roi_pct": round(roi, 3), "peak_roi_pct": round(peak_roi, 3),
            "stop_roi_pct": round(roi_points(entry, sl, qty, margin, side), 3) if sl else 0.0,
            "trail_active": bool(trade.get("trail_active")),
            "trail_stop": float(trade.get("trail_stop") or 0.0),
        }
        BUS.publish("trade.tick", state)
        self.registry.set_status(bot_id, "working", f"watching {trade['symbol']}",
                                 message=f"{side} uPnL ${pnl:,.2f} · {to_tp:+.2f}% to TP · "
                                         f"{to_sl:+.2f}% to SL {warn}",
                                 progress=min(1.0, max(0.0, 0.5 + to_tp / 4)), publish=False)
        if warn:
            self.registry.set_mood(bot_id, "worried", 15)
        self.registry.publish(bot_id, GOVERNOR.snapshot())

    async def _trail_tick(self, trade: dict, pos: Position, mark: float,
                          bot_id: str = "") -> None:
        """
        ROI trailing stop (one order, always the same one).

        The user's rule: once the position's ROI (P&L ÷ margin) has reached
        +25 %, keep the stop 15 ROI-points behind the best price seen — so the
        stop starts at +10 % ROI and ratchets up with the market.  The trail
        *moves* the protective STOP_MARKET order that already exists; it never
        adds a second one, never loosens, never crosses the liquidation price
        and never sits on the losing side of the entry.
        """
        cfg = self.store.cfg.risk
        enabled, activation, distance, min_step = cfg.trail()
        if not enabled:
            return
        symbol = trade["symbol"]
        side = trade["side"]
        entry = float(trade["entry_price"])
        qty = float(trade["qty"])
        margin = float(trade.get("margin") or 0.0)
        if qty <= 0 or margin <= 0 or symbol in self.pending_close:
            return

        # ── anchor the peak to the best mark we have seen ────────────────
        peak = float(trade.get("peak_price") or entry)
        new_peak = max(peak, mark) if side == "LONG" else min(peak, mark)
        if abs(new_peak - peak) > 0:
            trade["peak_price"] = new_peak
            with contextlib.suppress(Exception):
                await DB.update_trade(int(trade["id"]), {"peak_price": new_peak})
            peak = new_peak

        prev_stop = float(trade.get("sl_price") or 0.0)
        stop, armed = trail_stop_price(
            entry=entry, side=side, peak_price=peak, mark=mark, qty=qty, margin=margin,
            prev_stop=prev_stop, activation_roi=activation, distance_roi=distance,
            mark_gap_pct=cfg.trail_mark_gap_pct)
        if not armed:
            return

        liq = float(pos.liquidation_price or trade.get("liquidation_price") or 0.0)
        if liq > 0:                                  # belt & braces: never past liq
            if side == "LONG":
                stop = max(stop, liq * 1.001)
            else:
                stop = min(stop, liq * 0.999)
        if (side == "LONG" and stop <= prev_stop) or (side == "SHORT" and stop >= prev_stop):
            return                                   # nothing better to do
        step = roi_price_step(margin, qty)
        gain_roi = abs(stop - prev_stop) / step if step else 0.0
        if trade.get("trail_active") and gain_roi < min_step:
            return                                   # throttle the order churn

        flt = self.hub.filters.get(symbol)
        if flt:
            stop = round_tick(stop, flt.tick_size)
        peak_roi = roi_points(entry, peak, qty, margin, side)
        locked_roi = roi_points(entry, stop, qty, margin, side)
        armed_now = not bool(trade.get("trail_active"))
        moved = await self._apply_stop_move(
            trade, pos, stop, tag="tr", prev_stop=prev_stop,
            event_kind="trail",
            event_meta={"stop": stop, "peak": peak, "peak_roi": round(peak_roi, 3),
                        "locked_roi": round(locked_roi, 3), "armed": armed_now},
            log_msg=(f"{symbol}: {'🔒' if armed_now else '⤴'} ROI trail "
                     f"{'armed' if armed_now else 'raised'} — peak "
                     f"+{peak_roi:.1f}% ROI, stop {stop:.8g} locks "
                     f"+{locked_roi:.1f}% ROI "
                     f"(rule: arm {activation:.0f}%, trail {distance:.0f} pts)"),
            log_payload={"trade_id": trade["id"], "stop": stop, "peak": peak,
                         "peak_roi": round(peak_roi, 3),
                         "locked_roi": round(locked_roi, 3)},
            status_msg=f"stop locks +{locked_roi:.1f}% ROI (peak +{peak_roi:.1f}%)",
            patch_extra={"trail_stop": stop, "trail_active": 1},
            bot_id=bot_id)
        if moved:
            self.registry.set_mood("guardian-bot", "happy", 20)
            self.registry.set_status(bot_id or "monitor-team", "success",
                                     f"trail on {symbol}",
                                     message=f"stop locks +{locked_roi:.1f}% ROI "
                                             f"(peak +{peak_roi:.1f}%)")

    async def _apply_stop_move(self, trade: dict, pos: Position, new_stop: float,
                               *, tag: str, prev_stop: float,
                               event_kind: str, event_meta: dict[str, Any],
                               log_msg: str, log_payload: dict[str, Any] | None = None,
                               status_msg: str = "",
                               patch_extra: dict[str, Any] | None = None,
                               bot_id: str = "") -> bool:
        """
        Move the ONE protective STOP_MARKET order to `new_stop`
        (cancel + replace — never a second order).

        Shared by the ROI trail and the break-even ratchet: both must honour
        the hard rule that exactly one stop order exists per position, and
        both must restore the previous stop (or flatten the position) if the
        exchange refuses the move, so a position is never left unprotected.
        `new_stop` must already be strictly tighter than `prev_stop` and
        tick-rounded — the callers verify that.
        """
        symbol = trade["symbol"]
        side = trade["side"]
        bot = bot_id or "monitor-team"
        close_side = "SELL" if side == "LONG" else "BUY"
        old_order = str(trade.get("sl_order_id") or "")
        if not old_order:                      # rows written before the column
            with contextlib.suppress(Exception):
                old_order = str(json.loads(trade.get("notes") or "{}").get("sl_order_id") or "")
        if not old_order:                      # last resort: ask the venue
            with contextlib.suppress(Exception):
                for o in await self.hub.open_orders(symbol):
                    if str(o.get("type")) == "STOP_MARKET":
                        old_order = str(o.get("orderId") or "")
                        break
        order = None
        try:
            if old_order:
                await self.hub.cancel_order(symbol, old_order)
            order = await self.hub.stop_market(symbol, close_side, new_stop,
                                               close_position=True,
                                               client_id=f"SR{tag}{int(time.time())}")
        except RateLimitHalt as exc:
            self._note_blocked(bot, str(exc))
            with contextlib.suppress(Exception):     # put the old stop back
                if old_order and order is None:
                    back = await self.hub.stop_market(symbol, close_side, prev_stop,
                                                      close_position=True,
                                                      client_id=f"SR{tag}{int(time.time())}b")
                    trade["sl_order_id"] = back.order_id
                    await DB.update_trade(int(trade["id"]), {"sl_order_id": back.order_id})
            return False
        except Exception as exc:
            self.log("error", bot,
                     f"{symbol}: stop move ({tag}) failed ({str(exc)[:140]}) — restoring "
                     f"the previous stop", topic="monitor")
            restored = False
            with contextlib.suppress(Exception):
                if old_order:
                    back = await self.hub.stop_market(symbol, close_side, prev_stop,
                                                      close_position=True,
                                                      client_id=f"SR{tag}{int(time.time())}b")
                    trade["sl_order_id"] = back.order_id
                    await DB.update_trade(int(trade["id"]), {"sl_order_id": back.order_id})
                    restored = True
            if not restored:
                # never hold an unprotected position: if we cannot put a stop
                # back, flatten now rather than trade naked
                self.log("sos", bot,
                         f"{symbol}: no stop could be placed after the {tag} stop move "
                         f"failed — flattening to stay protected")
                await self._close_trade(int(trade["id"]), "protection_lost")
            return False

        patch = {"sl_price": new_stop,
                 "sl_order_id": order.order_id if order else old_order}
        if patch_extra:
            patch.update(patch_extra)
        trade.update(patch)
        trade["notes"] = self._notes_with(trade, sl_order_id=patch["sl_order_id"])
        with contextlib.suppress(Exception):
            await DB.update_trade(int(trade["id"]), {**patch, "notes": trade["notes"]})
        with contextlib.suppress(Exception):
            await DB.add_trade_event(int(trade["id"]), event_kind, event_meta)
        self.log("success", bot, log_msg, log_payload, topic="monitor")
        if status_msg:
            self.registry.set_status(bot, "success", f"{event_kind} on {symbol}",
                                     message=status_msg)
        BUS.publish(f"trade.{event_kind}",
                    {"trade_id": trade["id"], "symbol": symbol, **event_meta})
        return True

    async def _breakeven_tick(self, trade: dict, pos: Position, mark: float,
                              bot_id: str = "") -> None:
        """
        Strategy v2 — break-even ratchet.

        Once the trade is up `breakeven_r` R, move the single protective stop
        to entry ± `breakeven_offset_atr` × ATR, so a trade that ran a full R
        and then round-trips closes at ~break-even instead of paying the whole
        stop.  The candidate only ever tightens the existing stop (it never
        loosens it) and the ROI trail keeps ratcheting on top of it, so the
        "one stop order, tighten only" invariant holds for both layers.
        """
        cfg = self.store.cfg.strategy
        if not cfg.v2_enabled or not cfg.breakeven_enabled:
            return
        symbol = trade["symbol"]
        side = trade["side"]
        entry = float(trade["entry_price"])
        if float(trade["qty"]) <= 0 or symbol in self.pending_close:
            return
        try:
            notes = json.loads(trade.get("notes") or "{}")
        except (TypeError, ValueError):
            notes = {}
        if notes.get("be_done"):
            return                              # the ratchet only fires once
        sl0 = float(notes.get("sl_distance0") or 0.0)
        atr = float(notes.get("atr") or 0.0)
        if sl0 <= 0 or atr <= 0:
            return
        prev_stop = float(trade.get("sl_price") or 0.0)
        cand = breakeven_stop(side=side, entry=entry, mark=mark, atr=atr,
                              sl_distance=sl0, current_stop=prev_stop, cfg=cfg)
        if cand is None:
            return
        flt = self.hub.filters.get(symbol)
        if flt:
            cand = round_tick(cand, flt.tick_size)
            if (side == "LONG" and cand <= prev_stop) or \
                    (side == "SHORT" and cand >= prev_stop):
                return                          # tick rounding ate the improvement
        r_now = r_progress(side, entry, mark, sl0)
        moved = await self._apply_stop_move(
            trade, pos, cand, tag="be", prev_stop=prev_stop,
            event_kind="breakeven",
            event_meta={"stop": cand, "r": round(r_now, 3)},
            log_msg=(f"{symbol}: 🛡 break-even ratchet — up {r_now:+.2f} R, stop moved "
                     f"{prev_stop:.8g} → {cand:.8g} "
                     f"(entry locked, {cfg.breakeven_offset_atr:g}×ATR in)"),
            log_payload={"trade_id": trade["id"], "stop": cand, "r": round(r_now, 3)},
            status_msg=f"break-even armed at {cand:.8g} (R {r_now:+.2f})",
            bot_id=bot_id)
        if moved:
            trade["notes"] = self._notes_with(trade, be_done=1)
            with contextlib.suppress(Exception):
                await DB.update_trade(int(trade["id"]), {"notes": trade["notes"]})
            self.registry.set_mood("warden-bot", "happy", 15)

    async def _time_stop_tick(self, trade: dict, mark: float, bot_id: str = "") -> None:
        """
        Strategy v2 — time stop.

        A position that has held `time_stop_bars` bars without
        `time_stop_min_r` R of progress is closed at market: dead trades are
        usually just late losers, and cutting them frees the slot, the margin
        budget and the second fee leg.
        """
        cfg = self.store.cfg.strategy
        if not cfg.v2_enabled or not cfg.time_stop_enabled:
            return
        symbol = trade["symbol"]
        if symbol in self.pending_close:
            return
        side = trade["side"]
        entry = float(trade["entry_price"])
        try:
            notes = json.loads(trade.get("notes") or "{}")
        except (TypeError, ValueError):
            notes = {}
        sl0 = float(notes.get("sl_distance0") or 0.0)
        if sl0 <= 0:
            return
        bar_ms = tf_ms(self.store.cfg.engine.monitored_timeframe)
        bars_elapsed = (now_ms() - int(trade.get("opened_at") or 0)) / bar_ms
        r_now = r_progress(side, entry, mark, sl0)
        if not time_stop_due(bars_elapsed=bars_elapsed, r_now=r_now, cfg=cfg):
            return
        self.log("info", bot_id or "monitor-team",
                 f"{symbol}: time stop — {bars_elapsed:.0f} bars with {r_now:+.2f} R "
                 f"(< {cfg.time_stop_min_r:g} R) — closing at market", topic="monitor")
        await self._close_trade(int(trade["id"]), "time_stop",
                                reason_detail=(f"no progress after {bars_elapsed:.0f} bars "
                                               f"(R {r_now:+.2f} < {cfg.time_stop_min_r:g})"))

    @staticmethod
    def _notes_with(trade: dict, **values: Any) -> str:
        """Update a few keys inside the trade's JSON notes blob."""
        try:
            data = json.loads(trade.get("notes") or "{}")
            if not isinstance(data, dict):
                data = {}
        except Exception:
            data = {}
        data.update(values)
        return json.dumps(data)

    async def _handle_reverse_exits(self, opportunities: list[Opportunity]) -> None:
        """Indicator flip in the opposite direction closes the position instantly."""
        if not self.open_trades:
            return
        flips = {o.symbol: o.direction for o in opportunities}
        # also catch flips of symbols that were filtered out of the final list
        for symbol, trade_id in list(self.symbol_to_trade.items()):
            if symbol in flips:
                continue
            series = await self._compute_series(symbol)
            if series is None:
                continue
            sig = ghost.latest_signal(series)
            if sig:
                flips[symbol] = sig["direction"]
        for symbol, direction in flips.items():
            trade_id = self.symbol_to_trade.get(symbol)
            if not trade_id or trade_id not in self.open_trades:
                continue
            trade = self.open_trades[trade_id]
            if self.risk.reverse_exit_reason(trade["side"], direction):
                open_time = trade["opened_at"]
                if now_ms() - open_time < 5_000:
                    continue                     # ignore same-bar churn
                self.log("info", "ceo-bot",
                         f"{symbol}: opposite {direction} flip — closing {trade['side']} instantly",
                         topic="monitor")
                await self._close_trade(trade_id, "reverse_signal",
                                        reason_detail=f"indicator flipped to {direction}")

    @staticmethod
    def _fill_key(f) -> str:
        return f"{f.symbol}|{f.order_id}|{f.trade_id}|{f.ts}|{round(f.realized_pnl, 8)}"

    # ============================================================ close & journal
    async def _close_trade(self, trade_id: int, reason: str, price: float | None = None,
                           reason_detail: str = "") -> dict | None:
        trade = self.open_trades.get(trade_id)
        if not trade:
            return None
        symbol = trade["symbol"]
        if symbol in self.pending_close:
            return None
        self.pending_close.add(symbol)
        side = trade["side"]
        try:
            exit_price = price or self.hub.price(symbol)
            orders_cancelled = False
            with contextlib.suppress(Exception):
                await self.hub.cancel_all(symbol)
                orders_cancelled = True
            # if the exchange already closed us (TP/SL filled), there is no position
            position = None
            with contextlib.suppress(Exception):
                position = next((p for p in await self.hub.positions() if p.symbol == symbol), None)
            exit_order_id = ""
            if position is not None:
                try:
                    order = await self.hub.market_order(
                        symbol, "SELL" if side == "LONG" else "BUY", float(trade["qty"]),
                        reduce_only=True,
                        client_id=f"SRx{int(time.time())}{symbol[:6]}")
                    exit_price = order.avg_price or exit_price
                    exit_order_id = str(order.order_id)
                except Exception as exc:
                    self.log("error", "risk-bot",
                             f"{symbol}: close order failed — {str(exc)[:160]} (will retry)")
                    self.pending_close.discard(symbol)
                    return None
            return await self._finalize_close(trade_id, exit_price, reason, reason_detail,
                                              orders_cancelled,
                                              exit_order_id=exit_order_id)
        finally:
            self.pending_close.discard(symbol)

    async def _finalize_close(self, trade_id: int, fallback_price: float,
                              reason: str | None = None,
                              reason_detail: str = "", orders_cancelled: bool = False,
                              exit_order_id: str = "", allow_estimate: bool = True
                              ) -> dict | None:
        # pnl_source: 'fills' when the exchange reported the real fills,
        #             'estimated' when we had to infer from a price,
        #             'unknown'  when neither was available (never invented)
        trade = self.open_trades.get(trade_id)
        if not trade:
            return None
        symbol = trade["symbol"]
        side = trade["side"]
        entry = float(trade["entry_price"])
        qty = float(trade["qty"])
        opened_at = int(trade["opened_at"])

        gross = fee = 0.0
        pnl_source = "unknown" if fallback_price is None else "estimated"
        exit_price = fallback_price or self.hub.price(symbol)
        closed_at = now_ms()
        entry_oid = str(trade.get("entry_order_id") or "")
        exit_oid = str(exit_order_id or "")
        try:
            fills = await self.hub.fills([symbol], since_ms=opened_at - 2000)
            window = [f for f in fills
                      if opened_at - 2000 <= f.ts <= closed_at + 3000
                      and f.kind in ("TRADE", "MARKET", "STOP_MARKET",
                                     "TAKE_PROFIT_MARKET", "LIQUIDATION")]
            fresh = [f for f in window if self._fill_key(f) not in self.consumed_fills]
            # Every fresh fill on this symbol inside the trade window belongs to
            # this trade (one position per symbol + close cooldown).  Order-id
            # matching is used only to double-check, never to drop fills — an
            # exchange-side stop carries an order id the engine never saw.
            exact = [f for f in fresh if f.order_id and f.order_id in (entry_oid, exit_oid)]
            used = fresh
            if used:
                pnl_source = "fills"
            if exact and len(exact) < len(fresh):
                self.log("debug", "trade-manager-bot",
                         f"{symbol}: {len(fresh) - len(exact)} fill(s) matched by window "
                         f"only (exchange-side protection) — all counted")
            if used:
                gross = sum(f.realized_pnl for f in used)
                fee = sum(f.commission for f in used
                          if f.commission_asset in ("USDT", ""))
                # the opening fill carries no realized P&L — if no such fill is
                # in the window (it was filled by a previous run, before the
                # restart) the entry commission is still owed to the P&L
                opening = any(f.realized_pnl == 0 or (entry_oid and f.order_id == entry_oid)
                              for f in used)
                if not opening:
                    fee += float(trade.get("entry_fee") or 0.0) \
                        or abs(qty) * entry * TAKER_FEE
                closing = [f for f in used if f.realized_pnl]
                if closing:
                    exit_price = closing[-1].price or exit_price
                for f in used:
                    self.consumed_fills.add(self._fill_key(f))
                if len(self.consumed_fills) > 4000:
                    self.consumed_fills = set(list(self.consumed_fills)[-2000:])
            elif allow_estimate:
                gross = (exit_price - entry) * qty * (1 if side == "LONG" else -1)
                fee = (entry + exit_price) * qty * 0.0005
                pnl_source = "estimated"
        except Exception:
            if allow_estimate:
                gross = (exit_price - entry) * qty * (1 if side == "LONG" else -1)
                fee = (entry + exit_price) * qty * 0.0005
                pnl_source = "estimated"

        # funding for the trade window: positive cash flow = received
        funding_cash = 0.0
        if self.hub.transport == "sim":
            with contextlib.suppress(Exception):
                funding_cash = sum(f.realized_pnl for f in self.hub.exchange.fills
                                   if f.kind == "FUNDING_FEE" and f.symbol == symbol
                                   and opened_at <= f.ts <= closed_at + 3000)
        else:
            with contextlib.suppress(Exception):
                rows = await self.hub.funding_income(since_ms=opened_at)
                funding_cash = sum(r.realized_pnl for r in rows
                                   if r.symbol == symbol and r.ts <= closed_at + 3000)

        funding = -funding_cash          # stored positive = paid, negative = received
        net = gross - fee + funding_cash
        sl_distance = abs(entry - float(trade.get("sl_price") or entry))
        r_multiple = (net / (sl_distance * qty)) if sl_distance > 0 and qty else 0.0

        if reason is None:
            reason = self._infer_close_reason(trade, exit_price, orders_cancelled)

        patch = {
            "status": "closed", "exit_price": exit_price, "closed_at": now_ms(),
            "close_reason": reason, "gross_pnl": round(gross, 6),
            "fee_paid": round(fee, 6), "funding_paid": round(funding, 6),
            "net_pnl": round(net, 6), "r_multiple": round(r_multiple, 3),
            "pnl_source": pnl_source,
        }
        await DB.update_trade(trade_id, patch)
        trade.update(patch)
        self.open_trades.pop(trade_id, None)
        self.symbol_to_trade.pop(symbol, None)
        self.cooldowns[symbol] = time.time() + SYMBOL_COOLDOWN_S
        self.symbol_monitor.pop(symbol, None)
        self.series_cache.pop(symbol, None)

        stats = await self.journal.record_close(trade)
        win = net > 0
        # ── bot reactions: celebrate on a win, sad on a loss ───────────────
        for bot in self.registry.all():
            self.registry.set_mood(bot.bot_id, "happy" if win else "sad", 30)
        carriers = [bid for bid in (trade.get("scanner_id"), trade.get("analyst_id"),
                                    trade.get("exec_bot_id"), trade.get("monitor_bot_id"))
                    if bid]
        for bot in self.registry.all():
            if bot.bot_id in carriers:
                if win:
                    bot.metrics.wins += 1
                else:
                    bot.metrics.losses += 1
        # ── the office: a closed trade is a completed unit for every seat that
        #    carried it; every 20 units is a field promotion
        for bid in carriers:
            self.registry.note_unit(bid, "trade")
        self._office_pass(f"trade {symbol} closed")
        BUS.publish("celebration", {
            "win": win, "symbol": symbol, "net": round(net, 2),
            "reason": reason, "r_multiple": round(r_multiple, 2),
            "bots": [b.bot_id for b in self.registry.all()],
            "message": (f"🎉 {symbol} closed in profit +${net:,.2f}" if win else
                        f"💧 {symbol} closed at a loss ${net:,.2f}"),
        })
        self.log("success" if win else "warn", "trade-manager-bot",
                 f"{symbol} {side} closed ({reason}) → net {'+' if net >= 0 else ''}"
                 f"${net:,.2f} · gross ${gross:,.2f} · fees ${fee:,.2f} · "
                 f"funding ${funding:,.2f} · R {r_multiple:+.2f} · "
                 f"win-rate {stats.get('win_rate', 0):.1f}%",
                 {"trade": {k: v for k, v in trade.items() if k != "notes"},
                  "stats": stats}, topic="journal")
        self.registry.set_status("trade-manager-bot",
                                 "celebrating" if win else "sad", f"{symbol} closed",
                                 message=f"net ${net:,.2f} · reason {reason}")
        self.registry.set_status("equity-manager-bot", "success", "equity released",
                                 message=f"released PnL ${stats.get('net_pnl', 0):,.2f}")
        self._wf("close", "done", f"{symbol} {reason} · net ${net:,.2f}")
        BUS.publish("trade.closed", {"trade": {k: v for k, v in trade.items() if k != "notes"},
                                     "stats": stats})
        if len(self.open_trades) < self.store.cfg.risk.max_concurrent_trades:
            self.registry.set_status("monitor-team", "idle", "slot free",
                                     message=f"{len(self.open_trades)}/"
                                             f"{self.store.cfg.risk.max_concurrent_trades} open")
        return trade

    def _infer_close_reason(self, trade: dict, exit_price: float,
                            orders_cancelled: bool) -> str:
        side = trade["side"]
        sl = float(trade.get("sl_price") or 0)
        tp = float(trade.get("tp_price") or 0)
        if tp and exit_price > 0:
            if side == "LONG" and exit_price >= tp * 0.999:
                return "tp"
            if side == "SHORT" and exit_price <= tp * 1.001:
                return "tp"
        if sl and exit_price > 0:
            hit_stop = (side == "LONG" and exit_price <= sl * 1.001) or \
                (side == "SHORT" and exit_price >= sl * 0.999)
            if hit_stop:
                if trade.get("trail_active"):
                    return "trail"
                try:
                    notes = json.loads(trade.get("notes") or "{}")
                except (TypeError, ValueError):
                    notes = {}
                if notes.get("be_done"):
                    return "breakeven"
                return "sl"
        return "manual" if not orders_cancelled else "sl_or_tp"

    # ============================================================ maintenance
    async def _maintenance_loop(self) -> None:
        while self.running:
            try:
                await asyncio.sleep(30)
                if not self.running:
                    return
                api = GOVERNOR.snapshot()
                self.registry.decay_moods()
                self.registry.publish_all(api)
                for bot in self.registry.all():
                    await DB.upsert_bot_stats(bot.bot_id, {
                        "name": bot.name, "rank": bot.rank_index, "score": bot.metrics.score(),
                        "wins": bot.metrics.wins, "losses": bot.metrics.losses,
                        "tasks_done": bot.metrics.tasks_done,
                        "tasks_failed": bot.metrics.tasks_failed,
                        "errors": bot.metrics.errors,
                        "api_spent": bot.metrics.api_spent,
                        "avg_latency_ms": bot.metrics.avg_latency_ms,
                        "promotions": bot.promotions,
                        "last_task_at": int(bot.last_active * 1000),
                    })
                GOVERNOR.note_window_rollover()
                BUS.publish("api.weight", api)
                self.registry.set_status("api-guard-bot",
                                         "halted" if api["halted"] else "working",
                                         "watching API budget",
                                         message=f"{api['used']}/{api['cap']} weight "
                                                 f"({api['used_pct']:.1f}%) · "
                                                 f"{api['blocked_total']} blocked",
                                         publish=False)
                self.registry.publish("api-guard-bot", api)
                self.registry.set_status("maintenance-bot", "working", "hygiene pass",
                                         message="pruning logs, refreshing universe", publish=False)
                with contextlib.suppress(Exception):
                    await DB.prune_logs(20000)
                if self.cycle % 6 == 0:
                    await self.hub.refresh_tickers()
                    await self.hub.build_universe()
                    self.scanner_buckets = self._split_universe_by_rank() or \
                        self.hub.scanner_allocation(
                            self.store.cfg.engine.scanner_bots,
                            self.store.cfg.engine.assets_per_bot)
                    self._assign_scanner_symbols()
                # office round: promotions, replacements, roster persistence
                with contextlib.suppress(Exception):
                    self._office_pass("maintenance round")
                with contextlib.suppress(Exception):
                    await DB.kv_set("office.roster", self.registry.state())
                self.registry.set_status("maintenance-bot", "idle", "data normal",
                                         publish=False)
                # model refresh from our own journal
                drift = await self.reconcile_exchange()
                if abs(drift.get("net_drift", 0.0)) > 1.0:
                    self.log("warn", "trade-manager-bot",
                             f"Journal vs exchange drift ${drift['net_drift']:+,.2f} "
                             f"(journal {drift['journal_net']:,.2f} vs exchange "
                             f"{drift['exchange_net']:,.2f}) — check recent fills",
                             {"reconcile": drift})
                if self.cycle and self.cycle % 20 == 0:
                    rows = await self.journal.feature_rows()
                    res = MODEL.train(rows)
                    if res.get("trained"):
                        self.log("info", "analyst-1",
                                 f"confidence model retrained on {res['samples']} trades "
                                 f"(accuracy {res.get('train_accuracy', 0):.3f})")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                with contextlib.suppress(Exception):
                    self.log("warn", "maintenance-bot", f"maintenance error: {str(exc)[:160]}")

    async def reconcile_exchange(self) -> dict[str, Any]:
        """
        Compare the journal against the exchange's own cash movements.

        The journal is a *view* of the exchange; if the two disagree by more
        than rounding, something was billed that we never booked (or vice
        versa).  Live Binance accounts expose this through the income ledger.
        """
        closed = await DB.all_closed_for_stats()
        journal_net = sum(float(r.get("net_pnl") or 0) for r in closed)
        journal_fees = sum(abs(float(r.get("fee_paid") or 0)) for r in closed)
        journal_funding = sum(float(r.get("funding_paid") or 0) for r in closed)
        open_fees = sum(
            float(t.get("entry_fee") or 0.0)
            or abs(float(t.get("qty") or 0)) * float(t.get("entry_price") or 0) * TAKER_FEE
            for t in self.open_trades.values())
        out: dict[str, Any] = {
            "journal_net": round(journal_net, 4),
            "journal_fees": round(journal_fees, 4),
            "journal_funding_paid": round(journal_funding, 4),
            "open_entry_fees": round(open_fees, 4),
            "exchange_net": None,
            "net_drift": 0.0,
            "transport": self.hub.transport,
        }
        try:
            totals = await self.hub.income_totals()
        except Exception as exc:                       # pragma: no cover - transport gap
            out["error"] = str(exc)[:160]
            return out
        if totals is None:
            return out
        exchange_net = float(totals.get("realized", 0.0)) - float(totals.get("fees", 0.0)) \
            + float(totals.get("funding", 0.0))
        out["exchange_net"] = round(exchange_net, 4)
        out["exchange_fees"] = round(float(totals.get("fees", 0.0)), 4)
        out["exchange_funding"] = round(float(totals.get("funding", 0.0)), 4)
        # the journal books closed trades; the exchange books everything, so
        # subtract what is still open (its entry fees are already paid)
        expected = journal_net - open_fees
        out["expected_from_journal"] = round(expected, 4)
        out["net_drift"] = round(exchange_net - expected, 4)
        # funding on *open* positions is billed by the venue before we close
        # the trade, so allow a small proportional slack on top of the cent-level
        # rounding tolerance.
        tol = max(1.0, abs(out["exchange_net"]) * 0.001)
        out["tolerance"] = round(tol, 4)
        out["balanced"] = abs(out["net_drift"]) <= tol
        return out

    def _note_blocked(self, bot_id: str, message: str) -> None:
        bot = self.registry.get(bot_id)
        if bot:
            bot.metrics.api_blocked += 1
        self.registry.set_status(bot_id, "blocked", "API budget ceiling",
                                 message=message[:160])
        self.log("warn", "api-guard-bot",
                 f"Request from {bot_id} blocked — {message[:160]}", topic="api")

    # ================================================================ helpers
    async def _load_open_trades(self) -> None:
        rows = await DB.open_trades()
        # reconcile with the exchange: drop journal rows with no live position
        try:
            positions = {p.symbol for p in await self.hub.positions()}
        except Exception:
            positions = {r["symbol"] for r in rows}
        for row in rows:
            if row["symbol"] in positions:
                if not row.get("sl_order_id"):
                    with contextlib.suppress(Exception):
                        row["sl_order_id"] = str(json.loads(row.get("notes") or "{}")
                                                 .get("sl_order_id") or "")
                self.open_trades[int(row["id"])] = row
                self.symbol_to_trade[row["symbol"]] = int(row["id"])
            else:
                # The exchange has no such position → the trade closed while we
                # were down.  Book ONLY what the exchange reported: real fills if
                # they are still visible, otherwise an explicit 'unknown' with a
                # zero P&L.  A journal must never invent money.
                self.open_trades[int(row["id"])] = row
                with contextlib.suppress(Exception):
                    closed = await self._finalize_close(
                        int(row["id"]), None, reason="reconciled_missing",
                        allow_estimate=False,
                        reason_detail="position absent on exchange at startup — "
                                      "booked from the exchange record only")
                    if closed and closed.get("pnl_source") == "unknown":
                        self.log("warn", "trade-manager-bot",
                                 f"{row['symbol']}: closed while the engine was offline and "
                                 f"the fill history is gone — P&L recorded as unknown "
                                 f"(excluded from win/loss until reviewed)")
                if int(row["id"]) in self.open_trades:      # fallback if finalise bailed out
                    await DB.update_trade(int(row["id"]), {
                        "status": "closed", "close_reason": "reconciled_missing",
                        "pnl_source": "unknown", "closed_at": now_ms()})
                    self.open_trades.pop(int(row["id"]), None)
                    self.symbol_to_trade.pop(row["symbol"], None)
                self.log("warn", "info-verifier-bot",
                         f"{row['symbol']}: journal had an open trade with no exchange "
                         f"position — reconciled and closed at startup")
        if self.open_trades:
            self.log("info", "ceo-bot",
                     f"Adopted {len(self.open_trades)} open position(s) from the journal")

    async def emergency_close_all(self, reason: str = "manual") -> dict:
        """Operator panic button — flattens every engine position."""
        closed = []
        for trade_id in list(self.open_trades.keys()):
            t = await self._close_trade(trade_id, "emergency", reason_detail=reason)
            if t:
                closed.append(t["symbol"])
        self.log("sos", "ceo-bot", f"EMERGENCY FLATTEN executed by operator: {closed or 'nothing open'}")
        return {"closed": closed, "remaining": len(self.open_trades)}

    def status(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "paused": self.paused,
            "emergency_stop": self.emergency_stop,
            "cycle": self.cycle,
            "mode": self.hub.mode,
            "transport": self.hub.transport,
            "universe": len(self.hub.universe),
            "open_trades": len(self.open_trades),
            "max_trades": self.store.cfg.risk.max_concurrent_trades,
            "sos": self.sos,
            "health": {k: v for k, v in self.health.items() if k != "api_weight"},
            "api": GOVERNOR.snapshot(),
            "workflow": self.workflow,
            "last_scan_at": self.last_scan_at,
            "signals_seen": self.signals_seen,
            "started_at": self.started_at,
            "risk": self.risk.effective_exits(),
            "strategy": {
                "v2_enabled": self.store.cfg.strategy.v2_enabled,
                "adx_min": self.store.cfg.strategy.adx_min,
                "vol_expansion_min": self.store.cfg.strategy.vol_expansion_min,
                "breakeven_enabled": self.store.cfg.strategy.breakeven_enabled,
                "time_stop_enabled": self.store.cfg.strategy.time_stop_enabled,
                "max_same_direction": self.store.cfg.strategy.max_same_direction,
                "gate_rejections": dict(self.gate_rejections),
            },
            "scanner_buckets": [len(b) for b in self.scanner_buckets],
            "links": workflow_links(),
            "office": self.registry.office_summary(),
        }


ENGINE: TradingEngine | None = None


def get_engine() -> TradingEngine:
    global ENGINE
    if ENGINE is None:
        ENGINE = TradingEngine()
    return ENGINE
