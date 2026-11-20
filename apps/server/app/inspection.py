"""運営者向けの、書き込みを行わないSQLite閲覧。"""

import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path

from app.executions import ExecutionError

TABLES = {
    "robots": "機体",
    "executions": "実行",
    "robot_events": "実行イベント",
    "sqlite_sequence": "採番",
}
REDACTED_COLUMNS = {"token_hash"}
CELL_TEXT_LIMIT = 4000


@contextmanager
def readonly_database(path: Path):
    if not path.is_file():
        raise ExecutionError("DATABASE_NOT_FOUND", 404)
    try:
        with closing(
            sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.2)
        ) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            # 制御通信を長時間妨げないよう、閲覧処理にも実行時間の上限を設ける。
            deadline = time.monotonic() + 0.25
            conn.set_progress_handler(lambda: time.monotonic() > deadline, 1000)
            yield conn
    except sqlite3.OperationalError as error:
        raise ExecutionError("DATABASE_BUSY", 503) from error


def available_tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def database_overview(path: Path) -> dict:
    if not path.is_file():
        return {"available": False, "fileName": path.name, "readOnly": True, "tables": []}
    with readonly_database(path) as conn:
        existing = available_tables(conn)
        tables = [
            {
                "name": name,
                "label": label,
                "rowCount": conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0],
            }
            for name, label in TABLES.items()
            if name in existing
        ]
    return {"available": True, "fileName": path.name, "readOnly": True, "tables": tables}


def inspect_table(path: Path, table: str, limit: int, offset: int) -> dict:
    if table not in TABLES:
        raise ExecutionError("TABLE_NOT_FOUND", 404)
    with readonly_database(path) as conn:
        if table not in available_tables(conn):
            raise ExecutionError("TABLE_NOT_FOUND", 404)
        columns = [
            {
                "name": r["name"],
                "type": r["type"],
                "primaryKey": bool(r["pk"]),
                "redacted": r["name"] in REDACTED_COLUMNS,
            }
            for r in conn.execute(f'PRAGMA table_info("{table}")')
        ]
        count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        rows = []
        for row in conn.execute(
            f'SELECT * FROM "{table}" ORDER BY rowid DESC LIMIT ? OFFSET ?', (limit, offset)
        ):
            value = {}
            for key, cell in dict(row).items():
                if key in REDACTED_COLUMNS:
                    cell = "（非表示）"
                elif isinstance(cell, str) and len(cell) > CELL_TEXT_LIMIT:
                    cell = cell[:CELL_TEXT_LIMIT] + "…（先頭4000文字）"
                value[key] = cell
            rows.append(value)
    return {
        "table": table,
        "label": TABLES[table],
        "rowCount": count,
        "columns": columns,
        "rows": rows,
        "offset": offset,
        "limit": limit,
        "textLimit": CELL_TEXT_LIMIT,
        "readOnly": True,
    }
