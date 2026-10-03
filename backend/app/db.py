"""
SQLite persistence layer (aiosqlite).  Single writer, WAL mode.

Tables
  trades        — every position the engine opened (open + closed)
  trade_events  — per-trade lifecycle audit (opened, tp/sl adjusted, closed…)
  equity_curve  — sampled equity snapshots for the P&L chart
  bot_stats     — per-bot performance counters + promotion rank
  logs          — full workflow log (mirrored live over the websocket)
  kv            — misc durable state (starting balance lock, daily anchors…)
"""
from __future__ import annotations

import json
from typing import Any

import aiosqlite

from .config import DB_PATH
from .util import now_ms

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS trades (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol            TEXT NOT NULL,
    side              TEXT NOT NULL,            -- LONG | SHORT
    status            TEXT NOT NULL,            -- open | closed | cancelled
    qty               REAL NOT NULL,
    entry_price       REAL NOT NULL,
    exit_price        REAL,
    leverage          INTEGER NOT NULL,
    margin            REAL NOT NULL,
    notional          REAL NOT NULL,
    sl_price          REAL,
    tp_price          REAL,
    liquidation_price REAL,
    sl_atr_mult       REAL,
    tp_atr_mult       REAL,
    risk_mode         TEXT,
    opened_at         INTEGER NOT NULL,
    closed_at         INTEGER,
    close_reason      TEXT,                     -- tp | sl | reverse_signal | manual | liquidation | emergency
    gross_pnl         REAL DEFAULT 0,
    fee_paid          REAL DEFAULT 0,
    entry_fee         REAL DEFAULT 0,           -- entry leg, booked at open
    funding_paid      REAL DEFAULT 0,           -- positive = paid, negative = received
    net_pnl           REAL DEFAULT 0,
    r_multiple        REAL,
    signal_confidence REAL,
    signal_tier       TEXT,                     -- strong | normal
    analyst_id        TEXT,
    scanner_id        TEXT,
    exec_bot_id       TEXT,
    monitor_bot_id    TEXT,
    entry_order_id    TEXT,
    exit_order_id     TEXT,
    sl_order_id       TEXT,                     -- the LIVE protective stop order
    peak_price        REAL DEFAULT 0,           -- best mark seen (trail anchor)
    trail_active      INTEGER DEFAULT 0,        -- ROI trail armed
    trail_stop        REAL DEFAULT 0,           -- last trail price we placed
    mode              TEXT DEFAULT 'live',
    pnl_source        TEXT DEFAULT 'fills',      -- fills | estimated | unknown
    notes             TEXT,
    created_at        INTEGER NOT NULL,
    updated_at        INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_trades_closed ON trades(closed_at);

CREATE TABLE IF NOT EXISTS trade_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id   INTEGER NOT NULL,
    kind       TEXT NOT NULL,
    detail     TEXT,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trade_events_trade ON trade_events(trade_id);

CREATE TABLE IF NOT EXISTS equity_curve (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             INTEGER NOT NULL,
    equity         REAL NOT NULL,
    balance        REAL NOT NULL,
    unrealized     REAL DEFAULT 0,
    margin_used    REAL DEFAULT 0,
    open_positions INTEGER DEFAULT 0,
    source         TEXT
);
CREATE INDEX IF NOT EXISTS idx_equity_ts ON equity_curve(ts);

CREATE TABLE IF NOT EXISTS bot_stats (
    bot_id        TEXT PRIMARY KEY,
    name          TEXT,
    rank          INTEGER DEFAULT 1,
    score         REAL DEFAULT 0,
    wins          INTEGER DEFAULT 0,
    losses        INTEGER DEFAULT 0,
    tasks_done    INTEGER DEFAULT 0,
    tasks_failed  INTEGER DEFAULT 0,
    errors        INTEGER DEFAULT 0,
    api_spent     INTEGER DEFAULT 0,
    avg_latency_ms REAL DEFAULT 0,
    promotions    INTEGER DEFAULT 0,
    last_task_at  INTEGER,
    updated_at    INTEGER
);

CREATE TABLE IF NOT EXISTS logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         INTEGER NOT NULL,
    level      TEXT NOT NULL,       -- debug | info | success | warn | error | sos
    bot_id     TEXT,
    topic      TEXT,
    message    TEXT,
    payload    TEXT
);
CREATE INDEX IF NOT EXISTS idx_logs_ts ON logs(ts);
CREATE INDEX IF NOT EXISTS idx_logs_bot ON logs(bot_id);

CREATE TABLE IF NOT EXISTS kv (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at INTEGER
);
"""


class Database:
    def __init__(self, path=DB_PATH):
        self.path = str(path)
        self.conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.executescript(SCHEMA)
        # lightweight migrations for databases created by earlier versions
        cur = await self.conn.execute("PRAGMA table_info(trades)")
        cols = {row[1] for row in await cur.fetchall()}
        if "pnl_source" not in cols:
            await self.conn.execute(
                "ALTER TABLE trades ADD COLUMN pnl_source TEXT DEFAULT 'fills'")
        for column, ddl in (
            ("sl_order_id", "TEXT"),
            ("peak_price", "REAL DEFAULT 0"),
            ("trail_active", "INTEGER DEFAULT 0"),
            ("trail_stop", "REAL DEFAULT 0"),
        ):
            if column not in cols:
                await self.conn.execute(f"ALTER TABLE trades ADD COLUMN {column} {ddl}")
        if "entry_fee" not in cols:
            # the entry commission is paid at open; a close that happens in a
            # later run (restart) must still be able to book it, otherwise the
            # released P&L misses that leg and the equity bridge breaks
            await self.conn.execute(
                "ALTER TABLE trades ADD COLUMN entry_fee REAL DEFAULT 0")
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn:
            await self.conn.commit()
            await self.conn.close()
            self.conn = None

    # ------------------------------------------------------------------ kv
    async def kv_get(self, key: str, default: Any = None) -> Any:
        cur = await self.conn.execute("SELECT value FROM kv WHERE key=?", (key,))
        row = await cur.fetchone()
        await cur.close()
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except (TypeError, json.JSONDecodeError):
            return row["value"]

    async def kv_set(self, key: str, value: Any) -> None:
        await self.conn.execute(
            "INSERT INTO kv(key,value,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, json.dumps(value), now_ms()),
        )
        await self.conn.commit()

    async def kv_del(self, key: str) -> None:
        await self.conn.execute("DELETE FROM kv WHERE key = ?", (key,))
        await self.conn.commit()

    # ---------------------------------------------------------------- logs
    async def add_log(self, level: str, bot_id: str | None, topic: str | None,
                      message: str, payload: dict | None = None) -> int:
        cur = await self.conn.execute(
            "INSERT INTO logs(ts,level,bot_id,topic,message,payload) VALUES(?,?,?,?,?,?)",
            (now_ms(), level, bot_id, topic, message,
             json.dumps(payload) if payload else None),
        )
        await self.conn.commit()
        return cur.lastrowid or 0

    async def add_logs(self, records: list[dict]) -> None:
        """Batch insert (one transaction) — the engine's log writer flushes in
        batches, so it must not pay a commit per row."""
        if not records:
            return
        await self.conn.executemany(
            "INSERT INTO logs(ts,level,bot_id,topic,message,payload) VALUES(?,?,?,?,?,?)",
            [(r["ts"], r["level"], r["bot_id"], r["topic"], r["message"],
              json.dumps(r["payload"]) if r.get("payload") else None)
             for r in records])
        await self.conn.commit()

    async def query_logs(self, limit: int = 200, offset: int = 0, level: str | None = None,
                         bot_id: str | None = None, topic: str | None = None,
                         search: str | None = None) -> list[dict]:
        sql = "SELECT * FROM logs WHERE 1=1"
        args: list[Any] = []
        if level:
            sql += " AND level=?"
            args.append(level)
        if bot_id:
            sql += " AND bot_id=?"
            args.append(bot_id)
        if topic:
            sql += " AND topic LIKE ?"
            args.append(f"{topic}%")
        if search:
            sql += " AND message LIKE ?"
            args.append(f"%{search}%")
        sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
        args += [limit, offset]
        cur = await self.conn.execute(sql, args)
        rows = await cur.fetchall()
        await cur.close()
        return [dict(r) for r in rows]

    async def prune_logs(self, keep: int = 20000) -> None:
        await self.conn.execute(
            "DELETE FROM logs WHERE id < (SELECT MAX(id) FROM logs) - ?", (keep,))
        await self.conn.commit()

    # -------------------------------------------------------------- trades
    async def insert_trade(self, data: dict) -> int:
        ts = now_ms()
        data = {**data, "created_at": ts, "updated_at": ts}
        cols = ", ".join(data.keys())
        marks = ", ".join("?" for _ in data)
        cur = await self.conn.execute(
            f"INSERT INTO trades({cols}) VALUES({marks})", list(data.values()))
        await self.conn.commit()
        return cur.lastrowid or 0

    async def update_trade(self, trade_id: int, patch: dict) -> None:
        patch = {**patch, "updated_at": now_ms()}
        sets = ", ".join(f"{k}=?" for k in patch)
        await self.conn.execute(f"UPDATE trades SET {sets} WHERE id=?",
                                [*patch.values(), trade_id])
        await self.conn.commit()

    async def get_trade(self, trade_id: int) -> dict | None:
        cur = await self.conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,))
        row = await cur.fetchone()
        await cur.close()
        return dict(row) if row else None

    async def open_trades(self) -> list[dict]:
        cur = await self.conn.execute(
            "SELECT * FROM trades WHERE status='open' ORDER BY opened_at ASC")
        rows = await cur.fetchall()
        await cur.close()
        return [dict(r) for r in rows]

    async def closed_trades(self, limit: int = 200, offset: int = 0,
                            symbol: str | None = None, result: str | None = None,
                            reason: str | None = None) -> list[dict]:
        sql = "SELECT * FROM trades WHERE status='closed'"
        args: list[Any] = []
        if symbol:
            sql += " AND symbol=?"
            args.append(symbol)
        if result == "win":
            sql += " AND net_pnl > 0"
        elif result == "loss":
            sql += " AND net_pnl <= 0"
        if reason:
            sql += " AND close_reason=?"
            args.append(reason)
        sql += " ORDER BY closed_at DESC LIMIT ? OFFSET ?"
        args += [limit, offset]
        cur = await self.conn.execute(sql, args)
        rows = await cur.fetchall()
        await cur.close()
        return [dict(r) for r in rows]

    async def count_closed(self) -> int:
        cur = await self.conn.execute("SELECT COUNT(*) c FROM trades WHERE status='closed'")
        row = await cur.fetchone()
        await cur.close()
        return int(row["c"]) if row else 0

    async def closed_between(self, start_ms: int, end_ms: int | None = None) -> list[dict]:
        end_ms = end_ms or now_ms()
        cur = await self.conn.execute(
            "SELECT * FROM trades WHERE status='closed' AND closed_at BETWEEN ? AND ? "
            "ORDER BY closed_at ASC", (start_ms, end_ms))
        rows = await cur.fetchall()
        await cur.close()
        return [dict(r) for r in rows]

    async def closed_totals(self) -> dict:
        """Net P&L / fees / funding over all closed trades (journal reconciliation).

        A SQL aggregate over the closed book — the per-row Python sum this
        replaces was O(journal size) on a path the maintenance loop runs
        every 30 seconds.
        """
        cur = await self.conn.execute(
            "SELECT COALESCE(SUM(COALESCE(net_pnl,0)),0)   AS net_pnl, "
            "       COALESCE(SUM(ABS(COALESCE(fee_paid,0))),0) AS fees, "
            "       COALESCE(SUM(COALESCE(funding_paid,0)),0)  AS funding "
            "FROM trades WHERE status='closed'")
        row = await cur.fetchone()
        await cur.close()
        return {"net_pnl": float(row["net_pnl"]), "fees": float(row["fees"]),
                "funding": float(row["funding"])}

    async def symbol_stats_rows(self) -> list[dict]:
        """Per-symbol closed-trade aggregates (scanner/analyst confidence model)."""
        cur = await self.conn.execute(
            "SELECT symbol, COUNT(*) AS trades, "
            "       SUM(CASE WHEN COALESCE(net_pnl,0)>0 THEN 1 ELSE 0 END) AS wins, "
            "       COALESCE(SUM(COALESCE(net_pnl,0)),0) AS net "
            "FROM trades WHERE status='closed' GROUP BY symbol")
        rows = await cur.fetchall()
        await cur.close()
        return [dict(r) for r in rows]

    async def add_trade_event(self, trade_id: int, kind: str, detail: str | dict = "") -> None:
        if isinstance(detail, (dict, list, tuple)):
            detail = json.dumps(detail)
        await self.conn.execute(
            "INSERT INTO trade_events(trade_id,kind,detail,created_at) VALUES(?,?,?,?)",
            (trade_id, kind, detail, now_ms()))
        await self.conn.commit()

    async def trade_events(self, trade_id: int) -> list[dict]:
        cur = await self.conn.execute(
            "SELECT * FROM trade_events WHERE trade_id=? ORDER BY id ASC", (trade_id,))
        rows = await cur.fetchall()
        await cur.close()
        return [dict(r) for r in rows]

    # --------------------------------------------------------- equity curve
    async def add_equity_point(self, equity: float, balance: float, unrealized: float,
                               margin_used: float, open_positions: int, source: str = "tick") -> None:
        await self.conn.execute(
            "INSERT INTO equity_curve(ts,equity,balance,unrealized,margin_used,open_positions,source) "
            "VALUES(?,?,?,?,?,?,?)",
            (now_ms(), equity, balance, unrealized, margin_used, open_positions, source))
        await self.conn.commit()

    async def equity_series(self, limit: int = 2000, since_ms: int | None = None) -> list[dict]:
        if since_ms:
            cur = await self.conn.execute(
                "SELECT * FROM equity_curve WHERE ts>=? ORDER BY ts ASC LIMIT ?",
                (since_ms, limit))
        else:
            cur = await self.conn.execute(
                "SELECT * FROM (SELECT * FROM equity_curve ORDER BY ts DESC LIMIT ?) ORDER BY ts ASC",
                (limit,))
        rows = await cur.fetchall()
        await cur.close()
        return [dict(r) for r in rows]

    async def equity_curve_since(self, start_ms: int) -> list[dict]:
        cur = await self.conn.execute(
            "SELECT ts, equity, balance FROM equity_curve WHERE ts>=? ORDER BY ts ASC",
            (start_ms,))
        rows = await cur.fetchall()
        await cur.close()
        return [dict(r) for r in rows]

    async def first_equity_point(self) -> dict | None:
        cur = await self.conn.execute(
            "SELECT * FROM equity_curve ORDER BY ts ASC LIMIT 1")
        row = await cur.fetchone()
        await cur.close()
        return dict(row) if row else None

    # ------------------------------------------------------------ bot stats
    async def upsert_bot_stats(self, bot_id: str, patch: dict) -> None:
        data = {"bot_id": bot_id, "updated_at": now_ms(), **patch}
        cols = ", ".join(data.keys())
        updates = ", ".join(f"{k}=excluded.{k}" for k in patch)
        marks = ", ".join("?" for _ in data)
        await self.conn.execute(
            f"INSERT INTO bot_stats({cols}) VALUES({marks}) "
            f"ON CONFLICT(bot_id) DO UPDATE SET {updates}, updated_at=excluded.updated_at",
            list(data.values()))
        await self.conn.commit()

    async def bot_stats(self) -> list[dict]:
        cur = await self.conn.execute("SELECT * FROM bot_stats")
        rows = await cur.fetchall()
        await cur.close()
        return [dict(r) for r in rows]

    async def stats_summary(self) -> dict:
        """Aggregate P&L statistics straight from the journal (source of truth).

        Computed as a single SQL aggregate: loading every closed trade into
        Python scaled linearly with the journal (tens of milliseconds per call
        on a busy quarter, on a path hit every few seconds).
        """
        sql = """
        SELECT
            COUNT(*)                                                   AS total_trades,
            SUM(CASE WHEN COALESCE(pnl_source,'fills')='unknown' THEN 1 ELSE 0 END) AS unknown_n,
            SUM(CASE WHEN COALESCE(pnl_source,'fills')!='unknown' THEN 1 ELSE 0 END) AS rated_n,
            SUM(CASE WHEN COALESCE(pnl_source,'fills')!='unknown' AND COALESCE(net_pnl,0)>0 THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN COALESCE(pnl_source,'fills')!='unknown' AND COALESCE(net_pnl,0)<=0 THEN 1 ELSE 0 END) AS losses,
            SUM(CASE WHEN COALESCE(pnl_source,'fills')!='unknown' THEN COALESCE(net_pnl,0) ELSE 0 END) AS net_pnl,
            SUM(CASE WHEN COALESCE(pnl_source,'fills')!='unknown' AND COALESCE(net_pnl,0)>0 THEN COALESCE(net_pnl,0) ELSE 0 END) AS gross_profit,
            SUM(CASE WHEN COALESCE(pnl_source,'fills')!='unknown' AND COALESCE(net_pnl,0)<=0 THEN COALESCE(net_pnl,0) ELSE 0 END) AS gross_loss,
            SUM(CASE WHEN COALESCE(pnl_source,'fills')!='unknown' THEN ABS(COALESCE(fee_paid,0)) ELSE 0 END) AS fees_paid,
            SUM(CASE WHEN COALESCE(pnl_source,'fills')!='unknown' THEN COALESCE(funding_paid,0) ELSE 0 END) AS funding_paid
        FROM trades WHERE status='closed'"""
        cur = await self.conn.execute(sql)
        row = await cur.fetchone()
        await cur.close()
        r = dict(row) if row else {}
        # best / worst among *rated* trades only
        cur = await self.conn.execute(
            "SELECT net_pnl, symbol FROM trades WHERE status='closed' "
            "AND COALESCE(pnl_source,'fills')!='unknown' "
            "ORDER BY COALESCE(net_pnl,0) DESC LIMIT 1")
        best = await cur.fetchone()
        await cur.close()
        cur = await self.conn.execute(
            "SELECT net_pnl, symbol FROM trades WHERE status='closed' "
            "AND COALESCE(pnl_source,'fills')!='unknown' "
            "ORDER BY COALESCE(net_pnl,0) ASC LIMIT 1")
        worst = await cur.fetchone()
        await cur.close()

        total = int(r.get("total_trades") or 0)
        unknown = int(r.get("unknown_n") or 0)
        rated = int(r.get("rated_n") or 0)
        wins = int(r.get("wins") or 0)
        losses = int(r.get("losses") or 0)
        gross = float(r.get("net_pnl") or 0.0)
        win_sum = float(r.get("gross_profit") or 0.0)
        loss_sum = float(r.get("gross_loss") or 0.0)
        fees = float(r.get("fees_paid") or 0.0)
        funding_paid = float(r.get("funding_paid") or 0.0)
        return {
            "total_trades": total,
            "rated_trades": rated,
            "unreconciled": unknown,
            "wins": wins,
            "losses": losses,
            "win_rate": (wins / rated * 100.0) if rated else 0.0,
            "net_pnl": gross,
            "gross_profit": win_sum,
            "gross_loss": loss_sum,
            "profit_factor": (win_sum / abs(loss_sum)) if loss_sum else (999.0 if win_sum else 0.0),
            "avg_win": (win_sum / wins) if wins else 0.0,
            "avg_loss": (loss_sum / losses) if losses else 0.0,
            "fees_paid": fees,
            "funding_paid": funding_paid,        # positive = net paid, negative = received
            "funding_net": -funding_paid,        # positive = net received
            "best_trade": float(best["net_pnl"] or 0.0) if best else 0.0,
            "best_symbol": best["symbol"] if best else "",
            "worst_trade": float(worst["net_pnl"] or 0.0) if worst else 0.0,
            "worst_symbol": worst["symbol"] if worst else "",
        }


DB = Database()
