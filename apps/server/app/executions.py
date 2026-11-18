import json
import sqlite3
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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS test_executions (
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
            conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_test_per_robot
                ON test_executions(robot_id)
                WHERE status IN ('QUEUED', 'DISPATCHED', 'RUNNING', 'STOP_REQUESTED')
            """)
            conn.execute("""UPDATE test_executions SET status='STOP_REQUESTED',
                         error_code='SERVER_RESTARTED' WHERE status IN
                         ('QUEUED', 'DISPATCHED', 'RUNNING')""")

    def register(self, robot_id: str, boot_id: str, firmware: str, now: float) -> str:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM robots WHERE id=?", (robot_id,)).fetchone()
            old_session = row["session_id"]
            session = old_session if row["boot_id"] == boot_id else None
            session = session or str(uuid4())
            same = session == old_session
            if not same:
                conn.execute(
                    """UPDATE test_executions SET status='STOP_REQUESTED',
                    error_code='SESSION_CHANGED' WHERE robot_id=? AND status IN
                    ('QUEUED', 'DISPATCHED', 'RUNNING')""",
                    (robot_id,),
                )
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
            """SELECT * FROM test_executions WHERE robot_id=? AND status IN
                ('QUEUED', 'DISPATCHED', 'RUNNING', 'STOP_REQUESTED')""",
            (robot_id,),
        ).fetchone()

    @staticmethod
    def _view(row: sqlite3.Row) -> dict:
        return dict(row) | {"execution_id": row["id"]}

    def create_test(self, robot_id: str, request: TestExecutionRequest, now: float) -> dict:
        commands = json.dumps(
            [c.model_dump(by_alias=True) for c in request.commands], sort_keys=True
        )
        with self.connection() as conn:
            old = conn.execute(
                "SELECT * FROM test_executions WHERE request_id=?", (str(request.request_id),)
            ).fetchone()
            if old is not None:
                if old["robot_id"] != robot_id or old["commands"] != commands:
                    raise ExecutionError("IDEMPOTENCY_CONFLICT")
                return self._view(old)
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
            calibration = Calibration.model_validate_json(robot["calibration"])
            try:
                duration = execution_duration(request.commands, calibration)
            except ValueError as error:
                raise ExecutionError(str(error), 422) from error
            identifier = str(uuid4())
            conn.execute(
                """INSERT INTO test_executions
                (id, request_id, robot_id, session_id, commands, calibration, status,
                 estimated_duration_ms, created_at) VALUES (?, ?, ?, ?, ?, ?, 'QUEUED', ?, ?)""",
                (
                    identifier,
                    str(request.request_id),
                    robot_id,
                    robot["session_id"],
                    commands,
                    calibration.model_dump_json(by_alias=True),
                    duration,
                    now,
                ),
            )
            row = conn.execute("SELECT * FROM test_executions WHERE id=?", (identifier,)).fetchone()
            if row["execution_sequence"] > 2147483647:
                raise ExecutionError("EXECUTION_SEQUENCE_EXHAUSTED")
            return self._view(row)

    def get_test(self, identifier: str) -> dict:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM test_executions WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise ExecutionError("EXECUTION_NOT_FOUND", 404)
            return self._view(row)

    def stop_test(self, identifier: str, now: float) -> dict:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM test_executions WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise ExecutionError("EXECUTION_NOT_FOUND", 404)
            if row["status"] == "QUEUED":
                conn.execute(
                    "UPDATE test_executions SET status='STOPPED', completed_at=? WHERE id=?",
                    (now, identifier),
                )
            elif row["status"] in ACTIVE_STATUSES:
                conn.execute(
                    "UPDATE test_executions SET status='STOP_REQUESTED' WHERE id=?", (identifier,)
                )
            return self._view(
                conn.execute("SELECT * FROM test_executions WHERE id=?", (identifier,)).fetchone()
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
                    "SELECT * FROM test_executions WHERE id=? AND robot_id=?",
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
                    conn.execute(
                        """UPDATE test_executions SET status=?, current_command_index=?,
                        error_code=?, completed_at=? WHERE id=?""",
                        (
                            body.status,
                            body.current_command_index,
                            body.error_code,
                            now,
                            reported["id"],
                        ),
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
                conn.execute(
                    "UPDATE test_executions SET status='STOP_REQUESTED' WHERE id=?", (run["id"],)
                )
            common = response | {
                "execution_id": run["id"],
                "execution_sequence": run["execution_sequence"],
            }
            if body.status == "IDLE":
                if run["status"] == "QUEUED":
                    conn.execute(
                        "UPDATE test_executions SET status='DISPATCHED' WHERE id=?", (run["id"],)
                    )
                    return ControlResponse(
                        **common,
                        action="START",
                        lease_ms=LEASE_MS,
                        commands=json.loads(run["commands"]),
                        calibration=json.loads(run["calibration"]),
                    )
                # STARTの再配信はしない。未確認の実行は停止確認まで占有する。
                conn.execute(
                    "UPDATE test_executions SET status='STOP_REQUESTED' WHERE id=?", (run["id"],)
                )
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
            conn.execute(
                "UPDATE test_executions SET status='RUNNING', current_command_index=? WHERE id=?",
                (body.current_command_index, run["id"]),
            )
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
