import json
import sqlite3
import time
from uuid import uuid4

from app.config import WorkshopConfig
from app.execution_models import (
    ACTIVE_STATUSES,
    LEASE_MS,
    TERMINAL_STATUSES,
    Calibration,
    ControlRequest,
    ControlResponse,
    TestExecutionRequest,
    execution_duration,
)
from app.store import OFFLINE_AFTER_SECONDS, SessionConflict, Store


class ExecutionError(Exception):
    def __init__(self, code: str, status_code: int = 409):
        self.code = code
        self.status_code = status_code


class ExecutionStore(Store):
    def initialize(self, config: WorkshopConfig):
        super().initialize(config)
        with self.connection() as conn:
            tables = {
                row["name"]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if "test_executions" in tables and "executions" in tables:
                raise RuntimeError(
                    "Both legacy and current execution tables exist; migration requires inspection"
                )
            if "test_executions" in tables:
                # 表の改名はAUTOINCREMENTの採番位置も引き継ぎ、消費済み番号を再利用しない。
                conn.execute("ALTER TABLE test_executions RENAME TO executions")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS executions (
                    execution_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT NOT NULL UNIQUE,
                    request_id TEXT NOT NULL UNIQUE,
                    robot_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    commands TEXT NOT NULL,
                    calibration TEXT NOT NULL,
                    status TEXT NOT NULL,
                    current_command_index INTEGER NOT NULL DEFAULT 0,
                    estimated_duration_ms INTEGER NOT NULL,
                    error_code TEXT,
                    created_at REAL NOT NULL,
                    completed_at REAL
                )
            """)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(executions)")}
            for name, definition in {
                "kind": "TEXT NOT NULL DEFAULT 'TEST'",
                "participant_session_id": "TEXT",
                "program_id": "TEXT",
                "program_version": "INTEGER",
                "reservation_id": "TEXT",
                "started_at": "REAL",
                "station_ast": "TEXT",
                "quest_id": "TEXT",
                "quest_version": "INTEGER",
                "quest_snapshot": "TEXT",
                "quest_cleared": "INTEGER",
                "command_block_indices": "TEXT",
            }.items():
                if name not in columns:
                    conn.execute(f"ALTER TABLE executions ADD COLUMN {name} {definition}")
            conn.execute("DROP INDEX IF EXISTS one_active_test_per_robot")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS robot_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    robot_id TEXT NOT NULL,
                    execution_id TEXT,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS events_by_robot ON robot_events(robot_id, id)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS events_by_execution ON robot_events(execution_id, id)"
            )
            conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_execution_per_robot
                ON executions(robot_id)
                WHERE status IN ('QUEUED', 'DISPATCHED', 'RUNNING', 'STOP_REQUESTED')
            """)
            for run in conn.execute(
                "SELECT * FROM executions WHERE status IN ('QUEUED','DISPATCHED','RUNNING')"
            ).fetchall():
                self._transition(conn, run, "STOP_REQUESTED", None, error="SERVER_RESTARTED")

    def register(self, robot_id: str, boot_id: str, firmware: str, now: float) -> str:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM robots WHERE id=?", (robot_id,)).fetchone()
            old_session = row["session_id"]
            session = old_session if row["boot_id"] == boot_id else None
            session = session or str(uuid4())
            same = session == old_session
            if not same:
                for run in conn.execute(
                    "SELECT * FROM executions WHERE robot_id=? AND status IN ('QUEUED','DISPATCHED','RUNNING')",
                    (robot_id,),
                ).fetchall():
                    self._transition(conn, run, "STOP_REQUESTED", now, error="SESSION_CHANGED")
            conn.execute(
                """UPDATE robots SET boot_id=?, session_id=?, sequence=?,
                last_seen=?, firmware_version=?, agent_protocol=?, agent_status=?, last_control=?
                WHERE id=?""",
                (
                    boot_id,
                    session,
                    row["sequence"] if same else 0,
                    now,
                    firmware,
                    row["agent_protocol"] if same else 1,
                    row["agent_status"] if same else "IDLE",
                    row["last_control"] if same else None,
                    robot_id,
                ),
            )
            return session

    @staticmethod
    def _active(conn: sqlite3.Connection, robot_id: str):
        return conn.execute(
            """SELECT * FROM executions WHERE robot_id=? AND status IN
                ('QUEUED', 'DISPATCHED', 'RUNNING', 'STOP_REQUESTED')""",
            (robot_id,),
        ).fetchone()

    @staticmethod
    def _view(row: sqlite3.Row) -> dict:
        return dict(row) | {"execution_id": row["id"]}

    @staticmethod
    def _event(
        conn, robot_id: str, execution_id: str | None, event_type: str, payload: dict, now: float
    ):
        conn.execute(
            "INSERT INTO robot_events (robot_id, execution_id, event_type, payload, created_at) VALUES (?, ?, ?, ?, ?)",
            (robot_id, execution_id, event_type, json.dumps(payload, sort_keys=True), now),
        )

    def _terminal(self, conn, row, now: float):
        """予約のある実行では、終端保存と予約解放を同じトランザクションで行う。"""

    def _transition(
        self,
        conn,
        row,
        status: str,
        now: float | None,
        index: int | None = None,
        error: str | None = None,
    ):
        at = time.time() if now is None else now
        command_index = row["current_command_index"] if index is None else index
        changed = (
            status != row["status"]
            or command_index != row["current_command_index"]
            or error != row["error_code"]
        )
        started = row["started_at"]
        if status == "RUNNING" and started is None:
            started = at
        completed = at if status in TERMINAL_STATUSES else row["completed_at"]
        conn.execute(
            "UPDATE executions SET status=?, current_command_index=?, error_code=?, started_at=?, completed_at=? WHERE id=?",
            (status, command_index, error, started, completed, row["id"]),
        )
        if changed:
            self._event(
                conn,
                row["robot_id"],
                row["id"],
                "EXECUTION_STATUS",
                {
                    "from": row["status"],
                    "to": status,
                    "currentCommandIndex": command_index,
                    "errorCode": error,
                },
                at,
            )
        if status in TERMINAL_STATUSES:
            self._terminal(conn, row, at)

    def _ready_robot(self, conn, robot_id: str, now: float):
        robot = conn.execute(
            "SELECT * FROM robots WHERE id=? AND enabled=1", (robot_id,)
        ).fetchone()
        if robot is None:
            raise ExecutionError("ROBOT_NOT_FOUND", 404)
        if not robot["motion_enabled"]:
            raise ExecutionError("MOTION_DISABLED", 403)
        if (
            robot["last_control"] is None
            or not 0 <= now - robot["last_control"] < OFFLINE_AFTER_SECONDS
        ):
            raise ExecutionError("ROBOT_NOT_READY")
        if robot["agent_status"] != "IDLE" or self._active(conn, robot_id) is not None:
            raise ExecutionError("ROBOT_BUSY")
        return robot

    def _before_test(self, conn, robot_id: str, now: float):
        """参加者用の予約を導入した保存層で、試験APIの競合も検査する。"""

    def _insert_execution(
        self,
        conn,
        robot,
        request_id: str,
        commands,
        now: float,
        kind: str = "TEST",
        participant_session_id=None,
        program_id=None,
        program_version=None,
        reservation_id=None,
    ):
        calibration = Calibration.model_validate_json(robot["calibration"])
        try:
            duration = execution_duration(commands, calibration)
        except ValueError as error:
            raise ExecutionError(str(error), 422) from error
        identifier = str(uuid4())
        encoded = json.dumps([c.model_dump(by_alias=True) for c in commands], sort_keys=True)
        conn.execute(
            """INSERT INTO executions (id, request_id, robot_id, session_id, commands,
            calibration, status, estimated_duration_ms, created_at, kind, participant_session_id,
            program_id, program_version, reservation_id) VALUES (?, ?, ?, ?, ?, ?, 'QUEUED', ?, ?, ?, ?, ?, ?, ?)""",
            (
                identifier,
                request_id,
                robot["id"],
                robot["session_id"],
                encoded,
                calibration.model_dump_json(by_alias=True),
                duration,
                now,
                kind,
                participant_session_id,
                program_id,
                program_version,
                reservation_id,
            ),
        )
        row = conn.execute("SELECT * FROM executions WHERE id=?", (identifier,)).fetchone()
        if row["execution_sequence"] > 2147483647:
            raise ExecutionError("EXECUTION_SEQUENCE_EXHAUSTED")
        self._event(
            conn,
            robot["id"],
            identifier,
            "EXECUTION_CREATED",
            {"kind": kind, "status": "QUEUED"},
            now,
        )
        return self._view(row)

    def create_test(self, robot_id: str, request: TestExecutionRequest, now: float) -> dict:
        encoded = json.dumps(
            [c.model_dump(by_alias=True) for c in request.commands], sort_keys=True
        )
        with self.connection() as conn:
            old = conn.execute(
                "SELECT * FROM executions WHERE request_id=?", (str(request.request_id),)
            ).fetchone()
            if old is not None:
                if (
                    old["kind"] != "TEST"
                    or old["robot_id"] != robot_id
                    or old["commands"] != encoded
                ):
                    raise ExecutionError("IDEMPOTENCY_CONFLICT")
                return self._view(old)
            self._before_test(conn, robot_id, now)
            robot = self._ready_robot(conn, robot_id, now)
            return self._insert_execution(
                conn, robot, str(request.request_id), request.commands, now
            )

    def get_test(self, identifier: str) -> dict:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM executions WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise ExecutionError("EXECUTION_NOT_FOUND", 404)
            return self._view(row)

    def stop_test(self, identifier: str, now: float) -> dict:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM executions WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise ExecutionError("EXECUTION_NOT_FOUND", 404)
            if row["status"] == "QUEUED":
                self._transition(conn, row, "STOPPED", now, error="STOP_REQUESTED")
            elif row["status"] in ACTIVE_STATUSES:
                self._transition(conn, row, "STOP_REQUESTED", now, error="STOP_REQUESTED")
            return self._view(
                conn.execute("SELECT * FROM executions WHERE id=?", (identifier,)).fetchone()
            )

    def control(self, robot_id: str, body: ControlRequest, now: float) -> ControlResponse:
        with self.connection() as conn:
            robot = conn.execute("SELECT * FROM robots WHERE id=?", (robot_id,)).fetchone()
            if robot["session_id"] != str(body.session_id) or robot["boot_id"] != str(body.boot_id):
                raise SessionConflict("REGISTER_REQUIRED")
            if body.sequence <= robot["sequence"]:
                raise SessionConflict("STALE_SEQUENCE")
            conn.execute(
                """UPDATE robots SET sequence=?, last_seen=?, last_control=?,
                agent_protocol=2, agent_status=? WHERE id=?""",
                (body.sequence, now, now, body.status, robot_id),
            )
            response = {"session_id": body.session_id, "sequence": body.sequence}
            active = self._active(conn, robot_id)
            reported = (
                None
                if body.execution_id is None
                else conn.execute(
                    "SELECT * FROM executions WHERE id=? AND robot_id=?",
                    (str(body.execution_id), robot_id),
                ).fetchone()
            )
            if body.status in TERMINAL_STATUSES:
                if reported is None:
                    raise SessionConflict("UNKNOWN_EXECUTION")
                command_count = len(json.loads(reported["commands"]))
                if body.current_command_index > command_count:
                    raise SessionConflict("INVALID_COMMAND_INDEX")
                if body.status == "COMPLETED" and (
                    reported["status"] == "QUEUED" or body.current_command_index != command_count
                ):
                    raise SessionConflict("INVALID_COMPLETION")
                if reported["status"] in ACTIVE_STATUSES:
                    self._transition(
                        conn,
                        reported,
                        body.status,
                        now,
                        index=body.current_command_index,
                        error=body.error_code,
                    )
                return ControlResponse(**response, action="ACK", execution_id=body.execution_id)
            if active is None:
                if body.status == "RUNNING":
                    # サーバーが許可していない走行は、期限を延ばさず停止させる。
                    return ControlResponse(
                        **response,
                        action="STOP",
                        execution_id=body.execution_id,
                        execution_sequence=reported["execution_sequence"] if reported else 0,
                    )
                return ControlResponse(**response, action="IDLE")
            run = dict(active)
            lost_contact = (
                robot["last_control"] is None
                or not 0 <= now - robot["last_control"] < OFFLINE_AFTER_SECONDS
            )
            expired_queue = (
                run["status"] == "QUEUED" and now - run["created_at"] >= OFFLINE_AFTER_SECONDS
            )
            if (
                run["session_id"] != str(body.session_id)
                or lost_contact
                or expired_queue
                or not robot["motion_enabled"]
            ):
                run["status"] = "STOP_REQUESTED"
                self._transition(conn, active, "STOP_REQUESTED", now, error="STOP_REQUESTED")
                active = dict(active) | {"status": "STOP_REQUESTED", "error_code": "STOP_REQUESTED"}
            common = response | {
                "execution_id": run["id"],
                "execution_sequence": run["execution_sequence"],
            }
            if body.status == "IDLE":
                if run["status"] == "QUEUED":
                    self._transition(conn, active, "DISPATCHED", now)
                    return ControlResponse(
                        **common,
                        action="START",
                        lease_ms=LEASE_MS,
                        commands=json.loads(run["commands"]),
                        calibration=json.loads(run["calibration"]),
                    )
                # STARTの再配信はしない。未確認の実行は停止確認まで占有する。
                self._transition(conn, active, "STOP_REQUESTED", now, error="STOP_REQUESTED")
                return ControlResponse(**common, action="STOP")
            if str(body.execution_id) != run["id"] or run["status"] not in (
                "DISPATCHED",
                "RUNNING",
            ):
                return ControlResponse(**common, action="STOP")
            if (
                body.current_command_index >= len(json.loads(run["commands"]))
                or body.current_command_index < run["current_command_index"]
            ):
                raise SessionConflict("INVALID_COMMAND_INDEX")
            self._transition(conn, active, "RUNNING", now, index=body.current_command_index)
            return ControlResponse(**common, action="CONTINUE", lease_ms=LEASE_MS)

    def list_robots(self, now: float) -> list[dict]:
        rows = super().list_robots(now)
        with self.connection() as conn:
            for row in rows:
                robot = conn.execute("SELECT * FROM robots WHERE id=?", (row["id"],)).fetchone()
                run = self._active(conn, row["id"])
                row.update(
                    agent_protocol=robot["agent_protocol"],
                    motion_enabled=bool(robot["motion_enabled"]),
                    execution_id=run["id"] if run else None,
                    execution_status=run["status"] if run else None,
                )
        return rows
