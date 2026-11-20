import json
import secrets
from uuid import uuid4

from app.config import WorkshopConfig
from app.execution_models import ACTIVE_STATUSES
from app.executions import ExecutionError, ExecutionStore
from app.store import OFFLINE_AFTER_SECONDS, token_digest
from app.workshop_models import (
    ExecutionCreate,
    Principal,
    ProgramAst,
    ProgramCreate,
    ProgramUpdate,
    ReservationCreate,
    expand_program,
)


class WorkshopStore(ExecutionStore):
    def initialize(self, config: WorkshopConfig):
        super().initialize(config)
        self.reservation_ttl = config.reservation_ttl_seconds
        self.session_ttl = config.participant_session_ttl_seconds
        with self.connection() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS participant_sessions (
                id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE, label TEXT NOT NULL,
                created_at REAL NOT NULL, expires_at REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS programs (
                id TEXT PRIMARY KEY, request_id TEXT NOT NULL, session_id TEXT NOT NULL,
                name TEXT NOT NULL, blockly_workspace TEXT NOT NULL, ast TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                UNIQUE(session_id, request_id)
            )""")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS programs_by_owner ON programs(session_id, updated_at)"
            )
            conn.execute("""CREATE TABLE IF NOT EXISTS reservations (
                id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE, robot_id TEXT NOT NULL,
                session_id TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL,
                expires_at REAL NOT NULL, execution_id TEXT
            )""")
            conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS one_reservation_per_robot
                ON reservations(robot_id) WHERE status IN ('RESERVED', 'IN_USE')""")

    @staticmethod
    def _require_session(conn, session_id: str, now: float):
        row = conn.execute(
            "SELECT * FROM participant_sessions WHERE id=?", (session_id,)
        ).fetchone()
        if row is None or row["revoked"] or not row["created_at"] <= now < row["expires_at"]:
            raise ExecutionError("SESSION_EXPIRED", 401)
        return row

    def authenticate_session(self, token: str, now: float) -> str | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM participant_sessions WHERE token_hash=?", (token_digest(token),)
            ).fetchone()
            if row and not row["revoked"] and row["created_at"] <= now < row["expires_at"]:
                return row["id"]
        return None

    @staticmethod
    def _session_view(row) -> dict:
        return {
            "session_id": row["id"],
            "label": row["label"],
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "revoked": bool(row["revoked"]),
        }

    def issue_session(self, label: str, now: float) -> dict:
        identifier, token = str(uuid4()), secrets.token_urlsafe(32)
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO participant_sessions (id, token_hash, label, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                (identifier, token_digest(token), label, now, now + self.session_ttl),
            )
            row = conn.execute(
                "SELECT * FROM participant_sessions WHERE id=?", (identifier,)
            ).fetchone()
            return self._session_view(row) | {"session_token": token}

    def sessions(self, now: float) -> list[dict]:
        with self.connection() as conn:
            self._expire_reservations(conn, now)
            return [
                self._session_view(row)
                for row in conn.execute(
                    "SELECT * FROM participant_sessions ORDER BY created_at DESC LIMIT 100"
                )
            ]

    def revoke_session(self, identifier: str, now: float) -> dict:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM participant_sessions WHERE id=?", (identifier,)
            ).fetchone()
            if row is None:
                raise ExecutionError("SESSION_NOT_FOUND", 404)
            conn.execute("UPDATE participant_sessions SET revoked=1 WHERE id=?", (identifier,))
            self._expire_reservations(conn, now)
            for run in conn.execute(
                "SELECT * FROM executions WHERE participant_session_id=? AND status IN ('QUEUED','DISPATCHED','RUNNING','STOP_REQUESTED')",
                (identifier,),
            ).fetchall():
                status = "STOPPED" if run["status"] == "QUEUED" else "STOP_REQUESTED"
                self._transition(conn, run, status, now, error="SESSION_REVOKED")
            return self._session_view(dict(row) | {"revoked": 1})

    def _participant(self, conn, actor: Principal, now: float) -> str:
        if actor.role != "PARTICIPANT" or actor.session_id is None:
            raise ExecutionError("PARTICIPANT_ACCESS_REQUIRED", 403)
        self._require_session(conn, actor.session_id, now)
        return actor.session_id

    @staticmethod
    def _owned(row, actor: Principal, code: str):
        if row is None or (actor.role != "STATION" and row["session_id"] != actor.session_id):
            raise ExecutionError(code, 404)
        return row

    @staticmethod
    def _program_view(row) -> dict:
        ast = ProgramAst.model_validate_json(row["ast"])
        return {
            "program_id": row["id"],
            "session_id": row["session_id"],
            "name": row["name"],
            "blockly_workspace": json.loads(row["blockly_workspace"]),
            "ast": ast,
            "version": row["version"],
            "expanded_command_count": len(expand_program(ast)),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def create_program(self, actor: Principal, body: ProgramCreate, now: float) -> dict:
        workspace = json.dumps(body.blockly_workspace, ensure_ascii=False, sort_keys=True)
        ast = body.ast.model_dump_json(by_alias=True)
        with self.connection() as conn:
            owner = self._participant(conn, actor, now)
            old = conn.execute(
                "SELECT * FROM programs WHERE session_id=? AND request_id=?",
                (owner, str(body.request_id)),
            ).fetchone()
            if old:
                # 更新後のプログラムを古い作成要求で巻き戻さない。
                if old["version"] != 1 or (old["name"], old["blockly_workspace"], old["ast"]) != (
                    body.name,
                    workspace,
                    ast,
                ):
                    raise ExecutionError("IDEMPOTENCY_CONFLICT")
                return self._program_view(old)
            identifier = str(uuid4())
            conn.execute(
                "INSERT INTO programs (id, request_id, session_id, name, blockly_workspace, ast, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (identifier, str(body.request_id), owner, body.name, workspace, ast, now, now),
            )
            return self._program_view(
                conn.execute("SELECT * FROM programs WHERE id=?", (identifier,)).fetchone()
            )

    def update_program(
        self, identifier: str, actor: Principal, body: ProgramUpdate, now: float
    ) -> dict:
        with self.connection() as conn:
            self._participant(conn, actor, now)
            row = self._owned(
                conn.execute("SELECT * FROM programs WHERE id=?", (identifier,)).fetchone(),
                actor,
                "PROGRAM_NOT_FOUND",
            )
            if row["version"] != body.expected_version:
                raise ExecutionError("PROGRAM_VERSION_CONFLICT")
            conn.execute(
                "UPDATE programs SET name=?, blockly_workspace=?, ast=?, version=version+1, updated_at=? WHERE id=?",
                (
                    body.name,
                    json.dumps(body.blockly_workspace, ensure_ascii=False, sort_keys=True),
                    body.ast.model_dump_json(by_alias=True),
                    now,
                    identifier,
                ),
            )
            return self._program_view(
                conn.execute("SELECT * FROM programs WHERE id=?", (identifier,)).fetchone()
            )

    def get_program(self, identifier: str, actor: Principal) -> dict:
        with self.connection() as conn:
            row = self._owned(
                conn.execute("SELECT * FROM programs WHERE id=?", (identifier,)).fetchone(),
                actor,
                "PROGRAM_NOT_FOUND",
            )
            return self._program_view(row)

    def programs(self, actor: Principal, owner: str | None, limit: int, offset: int) -> list[dict]:
        if actor.role != "STATION":
            if owner is not None and owner != actor.session_id:
                raise ExecutionError("PROGRAM_ACCESS_DENIED", 403)
            owner = actor.session_id
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM programs WHERE (? IS NULL OR session_id=?) ORDER BY updated_at DESC, id LIMIT ? OFFSET ?",
                (owner, owner, limit, offset),
            ).fetchall()
            return [self._program_view(row) for row in rows]

    def _expire_reservations(self, conn, now: float):
        rows = conn.execute(
            """SELECT r.* FROM reservations r LEFT JOIN participant_sessions s ON r.session_id=s.id
            WHERE r.status='RESERVED' AND (r.expires_at<=? OR r.created_at>? OR s.id IS NULL OR s.revoked=1 OR s.expires_at<=? OR s.created_at>?)""",
            (now, now, now, now),
        ).fetchall()
        for row in rows:
            conn.execute("UPDATE reservations SET status='EXPIRED' WHERE id=?", (row["id"],))
            self._event(
                conn,
                row["robot_id"],
                None,
                "RESERVATION_EXPIRED",
                {"reservationId": row["id"]},
                now,
            )

    @staticmethod
    def _reservation_view(row) -> dict:
        value = dict(row)
        value["reservation_id"] = value.pop("id")
        return value

    def reserve(self, robot_id: str, body: ReservationCreate, now: float) -> dict:
        with self.connection() as conn:
            self._expire_reservations(conn, now)
            old = conn.execute(
                "SELECT * FROM reservations WHERE request_id=?", (str(body.request_id),)
            ).fetchone()
            if old:
                if old["robot_id"] != robot_id or old["session_id"] != str(body.session_id):
                    raise ExecutionError("IDEMPOTENCY_CONFLICT")
                return self._reservation_view(old)
            self._require_session(conn, str(body.session_id), now)
            self._ready_robot(conn, robot_id, now)
            if conn.execute(
                "SELECT 1 FROM reservations WHERE robot_id=? AND status IN ('RESERVED','IN_USE')",
                (robot_id,),
            ).fetchone():
                raise ExecutionError("ROBOT_RESERVED")
            identifier = str(uuid4())
            conn.execute(
                "INSERT INTO reservations (id, request_id, robot_id, session_id, status, created_at, expires_at) VALUES (?, ?, ?, ?, 'RESERVED', ?, ?)",
                (
                    identifier,
                    str(body.request_id),
                    robot_id,
                    str(body.session_id),
                    now,
                    now + self.reservation_ttl,
                ),
            )
            self._event(
                conn,
                robot_id,
                None,
                "RESERVATION_CREATED",
                {"reservationId": identifier, "sessionId": str(body.session_id)},
                now,
            )
            return self._reservation_view(
                conn.execute("SELECT * FROM reservations WHERE id=?", (identifier,)).fetchone()
            )

    def get_reservation(self, identifier: str, actor: Principal, now: float) -> dict:
        with self.connection() as conn:
            self._expire_reservations(conn, now)
            row = self._owned(
                conn.execute("SELECT * FROM reservations WHERE id=?", (identifier,)).fetchone(),
                actor,
                "RESERVATION_NOT_FOUND",
            )
            return self._reservation_view(row)

    def reservations(self, actor: Principal, now: float, limit: int, offset: int) -> list[dict]:
        owner = None if actor.role == "STATION" else actor.session_id
        with self.connection() as conn:
            self._expire_reservations(conn, now)
            return [
                self._reservation_view(r)
                for r in conn.execute(
                    "SELECT * FROM reservations WHERE (? IS NULL OR session_id=?) ORDER BY created_at DESC, id LIMIT ? OFFSET ?",
                    (owner, owner, limit, offset),
                )
            ]

    def renew_reservation(self, identifier: str, owner: str, now: float) -> dict:
        with self.connection() as conn:
            self._expire_reservations(conn, now)
            self._require_session(conn, owner, now)
            row = conn.execute("SELECT * FROM reservations WHERE id=?", (identifier,)).fetchone()
            if row is None or row["session_id"] != owner:
                raise ExecutionError("RESERVATION_NOT_FOUND", 404)
            if row["status"] != "RESERVED":
                raise ExecutionError("RESERVATION_NOT_RENEWABLE")
            conn.execute(
                "UPDATE reservations SET expires_at=? WHERE id=?",
                (now + self.reservation_ttl, identifier),
            )
            self._event(
                conn,
                row["robot_id"],
                None,
                "RESERVATION_RENEWED",
                {"reservationId": identifier},
                now,
            )
            return self._reservation_view(
                conn.execute("SELECT * FROM reservations WHERE id=?", (identifier,)).fetchone()
            )

    def release_reservation(self, identifier: str, actor: Principal, now: float) -> dict:
        with self.connection() as conn:
            self._expire_reservations(conn, now)
            row = self._owned(
                conn.execute("SELECT * FROM reservations WHERE id=?", (identifier,)).fetchone(),
                actor,
                "RESERVATION_NOT_FOUND",
            )
            if row["status"] == "IN_USE":
                raise ExecutionError("EXECUTION_ACTIVE")
            if row["status"] == "RESERVED":
                conn.execute("UPDATE reservations SET status='RELEASED' WHERE id=?", (identifier,))
                self._event(
                    conn,
                    row["robot_id"],
                    None,
                    "RESERVATION_RELEASED",
                    {"reservationId": identifier},
                    now,
                )
            return self._reservation_view(
                conn.execute("SELECT * FROM reservations WHERE id=?", (identifier,)).fetchone()
            )

    def _before_test(self, conn, robot_id: str, now: float):
        self._expire_reservations(conn, now)
        if conn.execute(
            "SELECT 1 FROM reservations WHERE robot_id=? AND status IN ('RESERVED','IN_USE')",
            (robot_id,),
        ).fetchone():
            raise ExecutionError("ROBOT_RESERVED")

    def create_execution(self, body: ExecutionCreate, now: float) -> dict:
        with self.connection() as conn:
            old = conn.execute(
                "SELECT * FROM executions WHERE request_id=?", (str(body.request_id),)
            ).fetchone()
            if old:
                if (
                    old["kind"],
                    old["program_id"],
                    old["program_version"],
                    old["reservation_id"],
                ) != (
                    "WORKSHOP",
                    str(body.program_id),
                    body.program_version,
                    str(body.reservation_id),
                ):
                    raise ExecutionError("IDEMPOTENCY_CONFLICT")
                return self._view(old)
            self._expire_reservations(conn, now)
            reservation = conn.execute(
                "SELECT * FROM reservations WHERE id=?", (str(body.reservation_id),)
            ).fetchone()
            if reservation is None:
                raise ExecutionError("RESERVATION_NOT_FOUND", 404)
            if reservation["status"] != "RESERVED":
                raise ExecutionError(
                    "RESERVATION_EXPIRED"
                    if reservation["status"] == "EXPIRED"
                    else "RESERVATION_NOT_AVAILABLE"
                )
            self._require_session(conn, reservation["session_id"], now)
            program = conn.execute(
                "SELECT * FROM programs WHERE id=?", (str(body.program_id),)
            ).fetchone()
            if program is None or program["session_id"] != reservation["session_id"]:
                raise ExecutionError("PROGRAM_RESERVATION_MISMATCH")
            if program["version"] != body.program_version:
                raise ExecutionError("PROGRAM_VERSION_CONFLICT")
            commands = expand_program(ProgramAst.model_validate_json(program["ast"]))
            robot = self._ready_robot(conn, reservation["robot_id"], now)
            run = self._insert_execution(
                conn,
                robot,
                str(body.request_id),
                commands,
                now,
                "WORKSHOP",
                reservation["session_id"],
                program["id"],
                program["version"],
                reservation["id"],
            )
            conn.execute(
                "UPDATE reservations SET status='IN_USE', execution_id=? WHERE id=?",
                (run["execution_id"], reservation["id"]),
            )
            self._event(
                conn,
                robot["id"],
                run["execution_id"],
                "RESERVATION_IN_USE",
                {"reservationId": reservation["id"]},
                now,
            )
            return run

    def _terminal(self, conn, row, now: float):
        if row["reservation_id"]:
            changed = conn.execute(
                "UPDATE reservations SET status='FINISHED' WHERE id=? AND status='IN_USE'",
                (row["reservation_id"],),
            ).rowcount
            if changed:
                self._event(
                    conn,
                    row["robot_id"],
                    row["id"],
                    "RESERVATION_FINISHED",
                    {"reservationId": row["reservation_id"]},
                    now,
                )

    @staticmethod
    def _execution_owned(row, actor: Principal):
        if row is None or (
            actor.role != "STATION" and row["participant_session_id"] != actor.session_id
        ):
            raise ExecutionError("EXECUTION_NOT_FOUND", 404)
        return row

    def get_execution(self, identifier: str, actor: Principal) -> dict:
        with self.connection() as conn:
            row = self._execution_owned(
                conn.execute("SELECT * FROM executions WHERE id=?", (identifier,)).fetchone(), actor
            )
            return self._view(row)

    def executions(
        self, actor: Principal, robot_id: str | None, limit: int, offset: int
    ) -> list[dict]:
        owner = None if actor.role == "STATION" else actor.session_id
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM executions WHERE (? IS NULL OR participant_session_id=?) AND (? IS NULL OR robot_id=?) ORDER BY execution_sequence DESC LIMIT ? OFFSET ?",
                (owner, owner, robot_id, robot_id, limit, offset),
            ).fetchall()
            return [self._view(row) for row in rows]

    def stop_execution(self, identifier: str, actor: Principal, now: float) -> dict:
        # 所有者の検査と停止を同じ接続で確定し、別参加者の実行を停止できないようにする。
        with self.connection() as conn:
            row = self._execution_owned(
                conn.execute("SELECT * FROM executions WHERE id=?", (identifier,)).fetchone(), actor
            )
            if row["status"] in ACTIVE_STATUSES:
                self._transition(
                    conn,
                    row,
                    "STOPPED" if row["status"] == "QUEUED" else "STOP_REQUESTED",
                    now,
                    error="STOP_REQUESTED",
                )
            return self._view(
                conn.execute("SELECT * FROM executions WHERE id=?", (identifier,)).fetchone()
            )

    def robot_events(
        self, robot_id: str | None, execution_id: str | None, after_id: int, limit: int
    ) -> list[dict]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM robot_events WHERE id>? AND (? IS NULL OR robot_id=?) AND (? IS NULL OR execution_id=?) ORDER BY id LIMIT ?",
                (after_id, robot_id, robot_id, execution_id, execution_id, limit),
            ).fetchall()
            return [
                {key: value for key, value in dict(r).items() if key != "id"}
                | {"event_id": r["id"], "payload": json.loads(r["payload"])}
                for r in rows
            ]

    def list_robots(self, now: float) -> list[dict]:
        rows = super().list_robots(now)
        with self.connection() as conn:
            self._expire_reservations(conn, now)
            for row in rows:
                robot = conn.execute("SELECT * FROM robots WHERE id=?", (row["id"],)).fetchone()
                reservation = conn.execute(
                    "SELECT * FROM reservations WHERE robot_id=? AND status IN ('RESERVED','IN_USE')",
                    (row["id"],),
                ).fetchone()
                status = row["execution_status"]
                if row["status"] == "OFFLINE":
                    availability = "OFFLINE"
                elif status:
                    availability = (
                        "STOPPING"
                        if status == "STOP_REQUESTED"
                        else "RUNNING"
                        if status == "RUNNING"
                        else "READY"
                    )
                elif reservation:
                    availability = "RESERVED"
                elif (
                    not robot["motion_enabled"]
                    or robot["agent_status"] != "IDLE"
                    or robot["last_control"] is None
                    or not 0 <= now - robot["last_control"] < OFFLINE_AFTER_SECONDS
                ):
                    availability = "UNAVAILABLE"
                else:
                    availability = "IDLE"
                row.update(
                    availability=availability,
                    available=availability == "IDLE",
                    reservation_id=reservation["id"] if reservation else None,
                )
        return rows
