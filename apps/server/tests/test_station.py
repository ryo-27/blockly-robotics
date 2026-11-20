import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_executions import Agent, auth, settings, submit
from test_fleet import fleet_config

from app.main import create_app

AST = {"version": 1, "commands": [{"type": "MOVE", "direction": "FORWARD", "steps": 1}]}


def send(client, robot="robot-01", ast=None, request_id=None, headers=None):
    return client.post(
        f"/api/robots/{robot}/executions",
        headers=headers or auth(),
        json={"requestId": request_id or str(uuid4()), "ast": ast or AST},
    )


@pytest.fixture
def station(tmp_path):
    clock = [1000.0]
    path = tmp_path / "station.db"
    with TestClient(create_app(settings(), path, lambda: clock[0])) as client:
        agent = Agent(client)
        agent.control()
        yield client, agent, clock, path


def test_direct_dispatch_has_no_participant_program_or_reservation(station):
    client, agent, _, path = station
    ast = {
        "version": 1,
        "commands": [
            {
                "type": "REPEAT",
                "count": 2,
                "body": [
                    {"type": "WAIT", "durationMs": 100},
                    {"type": "TURN", "direction": "LEFT"},
                ],
            }
        ],
    }
    response = send(client, ast=ast)
    assert response.status_code == 200
    run = response.json()
    assert run["kind"] == "STATION"
    assert all(
        run[key] is None for key in ("sessionId", "programId", "programVersion", "reservationId")
    )
    frame = agent.control().json()
    assert frame["action"] == "START"
    assert frame["commands"] == ast["commands"][0]["body"] * 2
    assert "calibration" in frame
    with sqlite3.connect(path) as conn:
        for table in ("participant_sessions", "programs", "reservations"):
            assert (
                conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone()
                is None
            )
        assert json.loads(conn.execute("SELECT station_ast FROM executions").fetchone()[0]) == ast


def test_station_auth_is_required(station):
    client, _, _, _ = station
    assert client.get("/api/station/status", headers=auth()).json() == {"role": "STATION"}
    assert (
        client.get("/api/station/status", headers=auth("invalid-participant-token")).status_code
        == 403
    )
    assert send(client, headers=auth("invalid-participant-token")).status_code == 403
    assert send(client, headers=auth("a" * 40)).status_code == 403
    assert (
        client.post(
            "/api/robots/robot-01/executions", json={"requestId": str(uuid4()), "ast": AST}
        ).status_code
        == 401
    )


def test_retry_after_completion_does_not_start_again(station):
    client, agent, _, _ = station
    request = str(uuid4())
    run = send(client, request_id=request).json()
    agent.control()
    agent.control("RUNNING", run["executionId"])
    agent.control("COMPLETED", run["executionId"], 1)
    agent.control()
    retry = send(client, request_id=request).json()
    assert retry["executionId"] == run["executionId"] and retry["status"] == "COMPLETED"
    assert agent.control().json()["action"] == "IDLE"
    assert send(client, robot="robot-02", request_id=request).status_code == 409
    assert (
        send(
            client,
            ast={"version": 1, "commands": [{"type": "TURN", "direction": "LEFT"}]},
            request_id=request,
        ).status_code
        == 409
    )


def test_simultaneous_requests_only_dispatch_once(station):
    client, _, _, _ = station
    request = str(uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: send(client, request_id=request), range(2)))
    assert all(r.status_code == 200 for r in responses)
    assert len({r.json()["executionId"] for r in responses}) == 1
    assert send(client).status_code == 409
    assert submit(client).status_code == 409


def test_retired_routes_are_unavailable(station):
    client, _, _, _ = station
    for path in (
        "/api/sessions",
        "/api/programs",
        "/api/reservations",
        "/api/robots/robot-01/reservations",
    ):
        assert client.post(path, headers=auth(), json={}).status_code in (404, 405)
    assert client.post("/api/executions", headers=auth(), json={}).status_code == 405


def test_station_stop_holds_occupancy_until_robot_acknowledges(station):
    client, agent, _, _ = station
    run = send(client).json()
    agent.control()
    agent.control("RUNNING", run["executionId"])
    assert (
        client.post(f"/api/executions/{run['executionId']}/stop", headers=auth()).json()["status"]
        == "STOP_REQUESTED"
    )
    assert send(client).status_code == 409
    assert agent.control("RUNNING", run["executionId"]).json()["action"] == "STOP"
    assert agent.control("STOPPED", run["executionId"]).json()["action"] == "ACK"
    agent.control()
    assert send(client).status_code == 200


def test_invalid_or_too_long_instructions_do_not_create_execution(station):
    client, _, _, path = station
    assert (
        send(
            client,
            ast={
                "version": 1,
                "commands": [
                    {"type": "REPEAT", "count": 7, "body": [{"type": "WAIT", "durationMs": 5000}]}
                ],
            },
        ).status_code
        == 422
    )
    assert (
        send(
            client,
            ast={
                "version": 1,
                "commands": [{"type": "REPEAT", "count": 20, "body": [AST["commands"][0]] * 4}],
            },
        ).status_code
        == 422
    )
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 0


def test_three_direct_dispatches_are_independent(tmp_path):
    with TestClient(create_app(fleet_config(), tmp_path / "fleet.db", lambda: 1000.0)) as client:
        agents = [Agent(client, str(i) * 40, f"robot-{i:02d}") for i in range(1, 4)]
        for agent in agents:
            agent.control()
        with ThreadPoolExecutor(max_workers=3) as pool:
            responses = list(
                pool.map(
                    lambda i: send(
                        client,
                        robot=f"robot-{i:02d}",
                        ast={"version": 1, "commands": [{"type": "WAIT", "durationMs": i * 1000}]},
                    ),
                    range(1, 4),
                )
            )
        assert all(r.status_code == 200 for r in responses)
        runs = [r.json() for r in responses]
        assert [r["estimatedDurationMs"] for r in runs] == [1000, 2000, 3000]
        assert len({r["executionSequence"] for r in runs}) == 3
        for i, (agent, run) in enumerate(zip(agents, runs), 1):
            start = agent.control().json()
            assert start["executionId"] == run["executionId"]
            assert start["commands"] == [{"type": "WAIT", "durationMs": i * 1000}]
            assert agent.control("RUNNING", run["executionId"]).json()["action"] == "CONTINUE"
        client.post(f"/api/executions/{runs[0]['executionId']}/stop", headers=auth())
        assert agents[0].control("RUNNING", runs[0]["executionId"]).json()["action"] == "STOP"
        for agent, run in zip(agents[1:], runs[1:]):
            assert agent.control("RUNNING", run["executionId"]).json()["action"] == "CONTINUE"


def test_restart_keeps_direct_execution_stopped_and_retry_idempotent(tmp_path):
    path = tmp_path / "restart.db"
    request = str(uuid4())
    with TestClient(create_app(settings(), path, lambda: 1000.0)) as client:
        agent = Agent(client)
        agent.control()
        run = send(client, request_id=request).json()
        agent.control()
        agent.control("RUNNING", run["executionId"])
    with TestClient(create_app(settings(), path, lambda: 1001.0)) as client:
        retry = send(client, request_id=request).json()
        assert retry["executionId"] == run["executionId"] and retry["status"] == "STOP_REQUESTED"
        agent.client = client
        agent.register()
        assert agent.control("RUNNING", run["executionId"]).json()["action"] == "STOP"
        assert agent.control("STOPPED", run["executionId"]).json()["action"] == "ACK"
