"""参加者管理の廃止。実行の冪等性と機体の消費済み番号は引き継ぐ。"""

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

RETIRED_TABLES = ("participant_sessions", "programs", "reservations")


def retire_participant_storage(path: Path):
    if not path.exists():
        return
    with closing(sqlite3.connect(path)) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not tables.intersection(RETIRED_TABLES):
            return

        # 停止確認待ちも含め、移行時に実行中の機体を解放しない。
        def require_idle():
            if (
                "executions" in tables
                and conn.execute(
                    "SELECT 1 FROM executions WHERE status IN "
                    "('QUEUED','DISPATCHED','RUNNING','STOP_REQUESTED') LIMIT 1"
                ).fetchone()
            ):
                raise RuntimeError("Storage migration requires all executions to be terminal")

        require_idle()
        backups = path.parent / "backups"
        backups.mkdir(exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        backup = backups / f"{path.stem}-before-station-{stamp}-{uuid4().hex[:8]}.sqlite3"
        with closing(sqlite3.connect(backup)) as destination:
            conn.backup(destination)
        backup.chmod(0o600)
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            require_idle()
            for table in RETIRED_TABLES:
                conn.execute(f'DROP TABLE IF EXISTS "{table}"')
            if "executions" in tables:
                conn.execute(
                    "UPDATE executions SET participant_session_id=NULL, program_id=NULL, "
                    "program_version=NULL, reservation_id=NULL"
                )
            if "robot_events" in tables:
                conn.execute("DELETE FROM robot_events WHERE event_type GLOB 'RESERVATION_*'")
