import sqlite3
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import WorkshopConfig
from app.main import create_app

STATION = "s" * 40
TOKEN = "a" * 40
OTHER = "b" * 40
MOVE = {"type": "MOVE", "direction": "FORWARD", "steps": 1}


def auth(token=STATION):
    return {"Authorization": f"Bearer {token}"}


def settings(enabled=True):
    return WorkshopConfig.model_validate(
        {
            "stationToken": STATION,
            "robots": [
                {"id": "robot-01", "displayName": "A", "token": TOKEN, "motionEnabled": enabled},
                {"id": "robot-02", "displayName": "B", "token": OTHER, "motionEnabled": enabled},
            ],
        }
    )


class Agent:
    def __init__(self, client, token=TOKEN, robot="robot-01"):
        self.client, self.token, self.robot = client, token, robot
        self.boot = str(uuid4())
        self.sequence = 0
        self.register()

    def register(self):
        response = self.client.post(
            "/api/robot-agent/register",
            headers=auth(self.token),
            json={
                "protocolVersion": 1,
                "robotId": self.robot,
                "bootId": self.boot,
                "firmwareVersion": "0.2.0-phase2",
            },
        )
        assert response.status_code == 200
        self.session = response.json()["sessionId"]
        self.sequence = 0

    def control(self, status="IDLE", execution=None, index=0, error=None, **changes):
        self.sequence += 1
        body = {
            "protocolVersion": 2,
            "sessionId": self.session,
            "bootId": self.boot,
            "sequence": self.sequence,
            "status": status,
            "currentCommandIndex": index,
        }
        if execution:
            body["executionId"] = execution
        if error:
            body["errorCode"] = error
        body.update(changes)
        return self.client.post("/api/robot-agent/control", headers=auth(self.token), json=body)


def submit(client, commands=None, request_id=None, robot="robot-01"):
    return client.post(
        f"/api/robots/{robot}/test-executions",
        headers=auth(),
        json={
            "requestId": request_id or str(uuid4()),
            "commands": [MOVE] if commands is None else commands,
        },
    )


def get(client, execution):
    return client.get(f"/api/test-executions/{execution}", headers=auth()).json()


@pytest.fixture
def rig(tmp_path):
    now = [1000.0]
    app = create_app(settings(), tmp_path / "db.sqlite3", lambda: now[0])
    with TestClient(app) as client:
        agent = Agent(client)
        assert agent.control().json()["action"] == "IDLE"
        yield client, agent, now


def test_execution_lifecycle(rig):
    client, agent, now = rig
    commands = [MOVE, {"type": "TURN", "direction": "RIGHT"}, {"type": "WAIT", "durationMs": 100}]
    response = submit(client, commands)
    assert response.status_code == 200
    run = response.json()
    identifier = run["executionId"]
    assert run["status"] == "QUEUED" and run["estimatedDurationMs"] == 930
    start = agent.control().json()
    assert start["action"] == "START" and start["leaseMs"] == 1500
    assert start["commands"] == commands
    assert start["calibration"]["invertRight"] is True
    assert get(client, identifier)["status"] == "DISPATCHED"
    assert agent.control("RUNNING", identifier, 1).json()["action"] == "CONTINUE"
    now[0] += 1
    assert agent.control("COMPLETED", identifier, 3).json()["action"] == "ACK"
    assert get(client, identifier)["status"] == "COMPLETED"
    assert agent.control("COMPLETED", identifier, 3).json()["action"] == "ACK"
    assert agent.control().json()["action"] == "IDLE"
    assert submit(client).status_code == 200


def test_request_retry_never_creates_second_execution(rig):
    client, agent, _ = rig
    request = str(uuid4())
    first = submit(client, request_id=request).json()
    assert submit(client, request_id=request).json()["executionId"] == first["executionId"]
    assert submit(client, [{"type": "TURN", "direction": "LEFT"}], request).status_code == 409
    identifier = first["executionId"]
    agent.control()
    agent.control("COMPLETED", identifier, 1)
    assert submit(client, request_id=request).json()["status"] == "COMPLETED"


def test_stop_before_and_during_motion(rig):
    client, agent, _ = rig
    first = submit(client).json()["executionId"]
    stop = client.post(f"/api/test-executions/{first}/stop", headers=auth())
    assert stop.json()["status"] == "STOPPED"
    assert agent.control().json()["action"] == "IDLE"
    second = submit(client).json()["executionId"]
    agent.control()
    client.post(f"/api/test-executions/{second}/stop", headers=auth())
    assert agent.control("RUNNING", second).json()["action"] == "STOP"
    assert submit(client).status_code == 409
    assert agent.control("STOPPED", second, error="STOP_REQUESTED").json()["action"] == "ACK"
    assert agent.control().json()["action"] == "IDLE"
    assert get(client, second)["status"] == "STOPPED"


def test_start_response_is_never_redispatched(rig):
    client, agent, _ = rig
    run = submit(client).json()["executionId"]
    assert agent.control().json()["action"] == "START"
    assert agent.control().json()["action"] == "STOP"
    assert submit(client).status_code == 409
    assert agent.control("STOPPED", run).json()["action"] == "ACK"


def test_timeout_cannot_resume_or_release_occupied_robot(rig):
    client, agent, now = rig
    run = submit(client).json()["executionId"]
    agent.control()
    agent.control("RUNNING", run)
    now[0] += 3.1
    assert submit(client).status_code == 409
    result = agent.control("RUNNING", run).json()
    assert result["action"] == "STOP" and result["leaseMs"] == 0
    assert submit(client).status_code == 409
    agent.control("STOPPED", run, error="LEASE_EXPIRED")
    assert get(client, run)["errorCode"] == "LEASE_EXPIRED"


def test_expired_queue_is_stopped_not_started(rig):
    client, agent, now = rig
    submit(client)
    now[0] += 3
    assert agent.control().json()["action"] == "STOP"


def test_sequence_replay_does_not_grant_a_lease(rig):
    client, agent, _ = rig
    identifier = submit(client).json()["executionId"]
    agent.control()
    assert agent.control("RUNNING", identifier).json()["action"] == "CONTINUE"
    assert agent.control("RUNNING", identifier, sequence=agent.sequence).status_code == 409


def test_wrong_robot_cannot_report_another_execution(rig):
    client, agent, _ = rig
    other = Agent(client, OTHER, "robot-02")
    run = submit(client).json()["executionId"]
    assert other.control("COMPLETED", run, 1).status_code == 409
    assert get(client, run)["status"] == "QUEUED"
    assert agent.control().json()["action"] == "START"


def test_restart_revokes_permits_and_preserves_execution_numbers(tmp_path):
    db = tmp_path / "db.sqlite3"
    with TestClient(create_app(settings(), db)) as client:
        agent = Agent(client)
        agent.control()
        first = submit(client).json()
        agent.control()
        agent.control("RUNNING", first["executionId"])
    with TestClient(create_app(settings(), db)) as client:
        agent.client = client
        assert agent.control("RUNNING", first["executionId"]).status_code == 409
        agent.register()
        assert agent.control("RUNNING", first["executionId"]).json()["action"] == "STOP"
        agent.control("STOPPED", first["executionId"], error="LINK_LOST")
        agent.control()
        second = submit(client).json()
        assert second["executionSequence"] > first["executionSequence"]


def test_robot_reboot_requires_stop_confirmation(rig):
    client, agent, _ = rig
    run = submit(client).json()["executionId"]
    agent.control()
    rebooted = Agent(client)
    assert rebooted.control().json()["action"] == "STOP"
    assert submit(client).status_code == 409
    rebooted.control("STOPPED", run)
    rebooted.control()
    assert submit(client).status_code == 200


def test_motion_disabled_by_default(tmp_path):
    with TestClient(create_app(settings(False), tmp_path / "db")) as client:
        agent = Agent(client)
        agent.control()
        assert submit(client).status_code == 403


def test_phase1_heartbeat_cannot_make_robot_ready(tmp_path):
    with TestClient(create_app(settings(), tmp_path / "db")) as client:
        Agent(client)
        assert submit(client).status_code == 409


@pytest.mark.parametrize(
    "commands",
    [
        [],
        [MOVE] * 65,
        [{"type": "MOVE", "direction": "FORWARD", "steps": True}],
        [{"type": "MOVE", "direction": "FORWARD", "steps": 1.1}],
        [{"type": "MOVE", "direction": "FORWARD", "steps": -1}],
        [{"type": "REPEAT", "count": 2, "body": [MOVE]}],
        [{"type": "TURN", "direction": "UP"}],
        [{"type": "WAIT", "durationMs": 5001}],
        [{"type": "WAIT", "durationMs": 5000}] * 7,
        [dict(MOVE, extra="invalid")],
    ],
)
def test_program_limits_and_types_are_enforced(rig, commands):
    client, _, _ = rig
    assert submit(client, commands).status_code == 422


def test_calibration_is_validated_and_frozen(tmp_path):
    config = settings()
    config.robots[0].calibration.forward_one_cell_ms = 1200
    with TestClient(create_app(config, tmp_path / "db")) as client:
        agent = Agent(client)
        agent.control()
        assert submit(client, [dict(MOVE, steps=5)]).status_code == 422
        run = submit(client).json()
        assert run["estimatedDurationMs"] == 1200
        start = agent.control().json()
        assert start["calibration"]["forwardOneCellMs"] == 1200


def test_competing_requests_accept_only_one_execution(rig):
    client, _, _ = rig
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: submit(client).status_code, range(2)))
    assert sorted(results) == [200, 409]


def test_legacy_database_migration(tmp_path):
    path = tmp_path / "db"
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE robots (id TEXT PRIMARY KEY, display_name TEXT NOT NULL,
            token_hash TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, boot_id TEXT,
            session_id TEXT, sequence INTEGER NOT NULL DEFAULT 0, last_seen REAL, firmware_version TEXT)""")
    with TestClient(create_app(settings(), path)) as client:
        agent = Agent(client)
        agent.control()
        assert submit(client).status_code == 200


def test_station_auth_is_required_for_execution_endpoints(rig):
    client, _, _ = rig
    assert client.post("/api/robots/robot-01/test-executions", json={}).status_code == 401
    assert client.get(f"/api/test-executions/{uuid4()}", headers=auth(TOKEN)).status_code == 403
    assert (
        client.post(f"/api/test-executions/{uuid4()}/stop", headers=auth(TOKEN)).status_code == 403
    )


@pytest.mark.parametrize("dispatch,index", [(False, 1), (True, 0)])
def test_invalid_completion_does_not_release_robot(rig, dispatch, index):
    client, agent, _ = rig
    identifier = submit(client).json()["executionId"]
    if dispatch:
        agent.control()
    assert agent.control("COMPLETED", identifier, index).status_code == 409
    assert submit(client).status_code == 409


def test_running_command_index_cannot_regress(rig):
    client, agent, _ = rig
    identifier = submit(client, [MOVE, MOVE]).json()["executionId"]
    agent.control()
    assert agent.control("RUNNING", identifier, 1).status_code == 200
    assert agent.control("RUNNING", identifier, 0).status_code == 409


def test_invalid_control_report_logs_field_without_input_value(rig, caplog):
    _, agent, _ = rig
    response = agent.control("ERROR", "invalid-private-value", error="MOTION_DISABLED")
    assert response.status_code == 422
    assert "body.executionId" in caplog.text
    assert "uuid_parsing" in caplog.text
    assert "invalid-private-value" not in caplog.text
