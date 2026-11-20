import json

from app.config import WorkshopConfig
from app.execution_models import ACTIVE_STATUSES
from app.executions import ExecutionError, ExecutionStore
from app.quests import get_quest, resolve_program
from app.storage_migration import retire_participant_storage
from app.store import OFFLINE_AFTER_SECONDS
from app.workshop_models import StationExecutionCreate


class WorkshopStore(ExecutionStore):
    def initialize(self, config: WorkshopConfig):
        # 旧データは接続状態を変更する前に検査・退避する。
        retire_participant_storage(self.path)
        super().initialize(config)

    @staticmethod
    def _view(row):
        value = ExecutionStore._view(row)
        value["quest_cleared"] = (
            bool(row["quest_cleared"]) if row["quest_id"] and row["status"] == "COMPLETED" else None
        )
        value["command_block_indices"] = (
            json.loads(row["command_block_indices"]) if row["command_block_indices"] else None
        )
        return value

    def create_station_execution(
        self, robot_id: str, body: StationExecutionCreate, now: float
    ) -> dict:
        """参加者・保存プログラムなしで、機体の排他確認と実行受付を一括確定する。"""
        ast = body.ast.model_dump_json(by_alias=True)
        with self.connection() as conn:
            old = conn.execute(
                "SELECT * FROM executions WHERE request_id=?", (str(body.request_id),)
            ).fetchone()
            if old is not None:
                if (
                    old["kind"] != "STATION"
                    or old["robot_id"] != robot_id
                    or old["station_ast"] != ast
                    or old["quest_id"] != body.quest_id
                    or old["quest_version"] != body.quest_version
                ):
                    raise ExecutionError("IDEMPOTENCY_CONFLICT")
                return self._view(old)
            quest = get_quest(body.quest_id, body.quest_version)
            commands, indices, cleared = resolve_program(body.ast, quest)
            robot = self._ready_robot(conn, robot_id, now)
            run = self._insert_execution(
                conn, robot, str(body.request_id), commands, now, "STATION"
            )
            conn.execute(
                "UPDATE executions SET station_ast=?,quest_id=?,quest_version=?,quest_snapshot=?,quest_cleared=?,command_block_indices=? WHERE id=?",
                (
                    ast,
                    body.quest_id,
                    body.quest_version,
                    quest.model_dump_json(by_alias=True) if quest else None,
                    cleared,
                    json.dumps(indices),
                    run["execution_id"],
                ),
            )
            return self._view(
                conn.execute(
                    "SELECT * FROM executions WHERE id=?", (run["execution_id"],)
                ).fetchone()
            )

    @staticmethod
    def _require_execution(row):
        if row is None:
            raise ExecutionError("EXECUTION_NOT_FOUND", 404)
        return row

    def get_execution(self, identifier: str) -> dict:
        with self.connection() as conn:
            row = self._require_execution(
                conn.execute("SELECT * FROM executions WHERE id=?", (identifier,)).fetchone()
            )
            return self._view(row)

    def executions(self, robot_id: str | None, limit: int, offset: int) -> list[dict]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM executions WHERE (? IS NULL OR robot_id=?) ORDER BY execution_sequence DESC LIMIT ? OFFSET ?",
                (robot_id, robot_id, limit, offset),
            ).fetchall()
            return [self._view(row) for row in rows]

    def stop_execution(self, identifier: str, now: float) -> dict:
        with self.connection() as conn:
            row = self._require_execution(
                conn.execute("SELECT * FROM executions WHERE id=?", (identifier,)).fetchone()
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
            for row in rows:
                robot = conn.execute("SELECT * FROM robots WHERE id=?", (row["id"],)).fetchone()
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
                    reservation_id=None,
                )
        return rows
