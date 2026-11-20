import sqlite3

import pytest
from fastapi.testclient import TestClient
from test_executions import Agent, settings
from test_station import send

from app.main import create_app


def seed_legacy(path):
    with TestClient(create_app(settings(), path, lambda: 1000.0)) as client:
        agent = Agent(client)
        agent.control()
        run = send(client).json()
        agent.control()
        agent.control("COMPLETED", run["executionId"], 1)
    with sqlite3.connect(path) as conn:
        for table in ("participant_sessions", "programs", "reservations"):
            conn.execute(f"CREATE TABLE {table} (id TEXT)")
            conn.execute(f"INSERT INTO {table} VALUES ('legacy')")
        conn.execute(
            "UPDATE executions SET participant_session_id='legacy', program_id='legacy', "
            "program_version=1, reservation_id='legacy'"
        )
        conn.execute("UPDATE sqlite_sequence SET seq=99 WHERE name='executions'")
        conn.execute(
            "INSERT INTO robot_events (robot_id,event_type,payload,created_at) "
            "VALUES ('robot-01','RESERVATION_FINISHED','{}',1001)"
        )
    return run


def test_retirement_backs_up_removes_legacy_data_and_preserves_idempotency(tmp_path):
    path = tmp_path / "workshop.db"
    run = seed_legacy(path)
    with TestClient(create_app(settings(), path, lambda: 1001.0)) as client:
        retry = send(client, request_id=run["requestId"]).json()
        assert retry["executionId"] == run["executionId"]
        assert retry["status"] == "COMPLETED"
        agent = Agent(client)
        agent.control()
        assert send(client).json()["executionSequence"] == 100
    backups = list((tmp_path / "backups").glob("*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM programs").fetchone()[0] == 1
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    with sqlite3.connect(path) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert tables == {"robots", "executions", "robot_events", "sqlite_sequence"}
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM robot_events WHERE event_type GLOB 'RESERVATION_*'"
            ).fetchone()[0]
            == 0
        )
        assert conn.execute(
            "SELECT participant_session_id,program_id,program_version,reservation_id FROM executions LIMIT 1"
        ).fetchone() == (None, None, None, None)
    # 再起動で旧テーブルを作り直さず、移行バックアップも増やさない。
    with TestClient(create_app(settings(), path, lambda: 1002.0)):
        pass
    assert list((tmp_path / "backups").glob("*.sqlite3")) == backups


def test_retirement_refuses_active_execution_without_mutating_database(tmp_path):
    path = tmp_path / "workshop.db"
    seed_legacy(path)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE executions SET status='STOP_REQUESTED'")
        conn.execute("UPDATE robots SET last_seen=1234")
    with (
        pytest.raises(RuntimeError, match="requires all executions to be terminal"),
        TestClient(create_app(settings(), path)),
    ):
        pass
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM programs").fetchone()[0] == 1
        assert conn.execute("SELECT last_seen FROM robots LIMIT 1").fetchone()[0] == 1234
    assert not (tmp_path / "backups").exists()
