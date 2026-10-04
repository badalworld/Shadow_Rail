"""
Online SQLite backup for a running engine.

`sqlite3.Connection.backup()` takes a consistent snapshot of a live database
(the engine keeps writing during the copy — WAL mode makes that safe), so this
can run from cron without pausing trading:

    /home/user/.venv/bin/python scripts/backup.py             # → data/backups/
    SHADOW_RAIL_BACKUP_KEEP=30 python scripts/backup.py       # retention

Restore = stop the engine, copy the chosen file over
`$SHADOW_RAIL_DATA_DIR/shadow_rail.sqlite3` (delete any stale `-wal`/`-shm`
next to it), start the engine.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend"))

from app.config import DATA_DIR, DB_PATH       # noqa: E402  (path set above)


def backup(db_path: Path = DB_PATH, out_dir: Path | None = None,
           keep: int | None = None, stamp: str | None = None) -> Path:
    out_dir = out_dir or Path(os.environ.get("SHADOW_RAIL_BACKUP_DIR", DATA_DIR / "backups"))
    keep = keep if keep is not None else int(os.environ.get("SHADOW_RAIL_BACKUP_KEEP", "14"))
    out_dir.mkdir(parents=True, exist_ok=True)

    if not db_path.exists():
        raise SystemExit(f"no database at {db_path} — start the engine once first")

    name = f"shadow_rail-{stamp or time.strftime('%Y%m%d-%H%M%S')}.sqlite3"
    target = out_dir / name

    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)                      # atomic, consistent, live-safe
            dst.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            dst.close()
    finally:
        src.close()

    # integrity check on the copy, not the live file
    check = sqlite3.connect(target)
    try:
        ok = check.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        check.close()
    if ok != "ok":
        target.unlink(missing_ok=True)
        raise SystemExit(f"backup failed integrity check: {ok}")

    if keep > 0:
        snapshots = sorted(out_dir.glob("shadow_rail-*.sqlite3"),
                           key=lambda p: p.stat().st_mtime, reverse=True)
        for old in snapshots[keep:]:
            old.unlink(missing_ok=True)

    return target


def main() -> int:
    target = backup()
    size = target.stat().st_size / 1024
    print(f"backup ok  {target}  ({size:,.0f} KiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
