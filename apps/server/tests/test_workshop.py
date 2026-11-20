import json
import sqlite3
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_executions import Agent, settings, submit
from test_station import send, station  # noqa: F401

from app.main import create_app

MOVE = {"type": "MOVE", "direction": "FORWARD", "steps": 1}


def test_phase2_migration_preserves_deleted_sequence_high_water(tmp_path):
    path = tmp_path / "db"
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE test_executions (
            execution_sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
            request_id TEXT NOT NULL UNIQUE, robot_id TEXT NOT NULL, session_id TEXT NOT NULL,
            commands TEXT NOT NULL, calibration TEXT NOT NULL, status TEXT NOT NULL,
            current_command_index INTEGER NOT NULL DEFAULT 0, estimated_duration_ms INTEGER NOT NULL,
            error_code TEXT, created_at REAL NOT NULL, completed_at REAL)""")
        for sequence in (7, 99):
            conn.execute(
                "INSERT INTO test_executions VALUES (?, ?, ?, 'robot-01', ?, ?, ?, 'COMPLETED', 1, 500, NULL, 1000, 1001)",
                (sequence, str(uuid4()), str(uuid4()), str(uuid4()), json.dumps([MOVE]), "{}"),
            )
        conn.execute("DELETE FROM test_executions WHERE execution_sequence=99")
    with TestClient(create_app(settings(), path)) as client:
        agent = Agent(client)
        agent.control()
        assert submit(client).json()["executionSequence"] > 99
    with sqlite3.connect(path) as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM executions WHERE execution_sequence=7").fetchone()[0]
            == 1
        )
        assert (
            conn.execute("SELECT name FROM sqlite_master WHERE name='test_executions'").fetchone()
            is None
        )


@pytest.mark.parametrize(
    "ast",
    [
        {"version": 2, "commands": [MOVE]},
        {"version": 1, "commands": []},
        {"version": 1, "commands": [{"type": "REPEAT", "count": True, "body": [MOVE]}]},
        {"version": 1, "commands": [{"type": "REPEAT", "count": 21, "body": [MOVE]}]},
        {"version": 1, "commands": [{"type": "REPEAT", "count": 1, "body": []}]},
        {
            "version": 1,
            "commands": [{"type": "REPEAT", "count": 20, "body": [MOVE, MOVE, MOVE, MOVE]}],
        },
        {
            "version": 1,
            "commands": [
                {
                    "type": "REPEAT",
                    "count": 2,
                    "body": [{"type": "REPEAT", "count": 20, "body": [MOVE, MOVE]}],
                }
            ],
        },
        {
            "version": 1,
            "commands": [
                {
                    "type": "REPEAT",
                    "count": 1,
                    "body": [
                        {
                            "type": "REPEAT",
                            "count": 1,
                            "body": [
                                {
                                    "type": "REPEAT",
                                    "count": 1,
                                    "body": [{"type": "REPEAT", "count": 1, "body": [MOVE]}],
                                }
                            ],
                        }
                    ],
                }
            ],
        },
        {"version": 1, "commands": [{"type": "MOVE", "direction": "FORWARD", "steps": 1.5}]},
        {"version": 1, "commands": [dict(MOVE, extra="invalid")]},
    ],
)
def test_invalid_ast_is_rejected_before_dispatch(station, ast):  # noqa: F811
    client, _, _, _ = station
    assert send(client, ast=ast).status_code == 422
