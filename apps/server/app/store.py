import hashlib
import hmac
import sqlite3
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.config import WorkshopConfig

OFFLINE_AFTER_SECONDS = 3.0


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class SessionConflict(Exception):
    pass


class Store:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connection(self):
        with closing(sqlite3.connect(self.path, timeout=5)) as conn, conn:
            conn.row_factory = sqlite3.Row
            # Serialize writes, including session validation and heartbeat updates.
            conn.execute("BEGIN IMMEDIATE")
            yield conn

    def initialize(self, config: WorkshopConfig):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS robots (
                    id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    token_hash TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    boot_id TEXT,
                    session_id TEXT,
                    sequence INTEGER NOT NULL DEFAULT 0,
                    last_seen REAL,
                    firmware_version TEXT
                )
            """)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(robots)")}
            for name, definition in {
                "agent_protocol": "INTEGER NOT NULL DEFAULT 1",
                "agent_status": "TEXT NOT NULL DEFAULT 'IDLE'",
                "last_control": "REAL",
                "motion_enabled": "INTEGER NOT NULL DEFAULT 0",
                "calibration": "TEXT NOT NULL DEFAULT '{}'",
            }.items():
                if name not in columns:
                    conn.execute(f"ALTER TABLE robots ADD COLUMN {name} {definition}")
            conn.execute(
                "UPDATE robots SET last_control=NULL, agent_protocol=1, agent_status='IDLE'"
            )
            # A restart never trusts a pre-restart connection or session.
            conn.execute("UPDATE robots SET enabled=0, session_id=NULL, last_seen=NULL")
            for robot in config.robots:
                conn.execute(
                    """
                    INSERT INTO robots (id, display_name, token_hash) VALUES (?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET display_name=excluded.display_name,
                        token_hash=excluded.token_hash, enabled=1
                """,
                    (robot.id, robot.display_name, token_digest(robot.token.get_secret_value())),
                )
                conn.execute(
                    "UPDATE robots SET motion_enabled=?, calibration=? WHERE id=?",
                    (
                        robot.motion_enabled,
                        robot.calibration.model_dump_json(by_alias=True),
                        robot.id,
                    ),
                )

    def authenticate(self, token: str) -> str | None:
        digest = token_digest(token)
        with self.connection() as conn:
            rows = conn.execute("SELECT id, token_hash FROM robots WHERE enabled=1").fetchall()
        for row in rows:
            if hmac.compare_digest(row["token_hash"], digest):
                return row["id"]
        return None

    def register(self, robot_id: str, boot_id: str, firmware: str, now: float) -> str:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM robots WHERE id=?", (robot_id,)).fetchone()
            session = row["session_id"] if row["boot_id"] == boot_id else None
            session = session or str(uuid4())
            sequence = row["sequence"] if session == row["session_id"] else 0
            conn.execute(
                """
                UPDATE robots SET boot_id=?, session_id=?, sequence=?,
                    last_seen=?, firmware_version=? WHERE id=?
            """,
                (boot_id, session, sequence, now, firmware, robot_id),
            )
            return session

    def heartbeat(self, robot_id: str, boot_id: str, session: str, sequence: int, now: float):
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM robots WHERE id=?", (robot_id,)).fetchone()
            if row["session_id"] != session or row["boot_id"] != boot_id:
                raise SessionConflict("REGISTER_REQUIRED")
            if sequence < row["sequence"]:
                raise SessionConflict("STALE_SEQUENCE")
            # A retried packet is acknowledged, but does not extend online presence.
            if sequence > row["sequence"]:
                conn.execute(
                    "UPDATE robots SET sequence=?, last_seen=? WHERE id=?",
                    (sequence, now, robot_id),
                )

    def list_robots(self, now: float) -> list[dict]:
        with self.connection() as conn:
            rows = conn.execute("SELECT * FROM robots WHERE enabled=1 ORDER BY id").fetchall()
        return [
            {
                "id": row["id"],
                "display_name": row["display_name"],
                "status": "IDLE"
                if row["last_seen"] is not None
                and 0 <= now - row["last_seen"] < OFFLINE_AFTER_SECONDS
                else "OFFLINE",
                "last_seen_at": datetime.fromtimestamp(row["last_seen"], UTC).isoformat()
                if row["last_seen"] is not None
                else None,
                "firmware_version": row["firmware_version"],
            }
            for row in rows
        ]
