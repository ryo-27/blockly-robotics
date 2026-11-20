import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_executions import Agent, auth, settings, submit

from app.main import create_app

MOVE = {"type": "MOVE", "direction": "FORWARD", "steps": 1}
AST = {"version": 1, "commands": [MOVE]}
REPEAT = {
    "version": 1,
    "commands": [
        {"type": "REPEAT", "count": 2, "body": [MOVE, {"type": "TURN", "direction": "LEFT"}]}
    ],
}


def issue(client, label="参加者"):
    r = client.post("/api/sessions", headers=auth(), json={"label": label})
    assert r.status_code == 200, r.text
    return r.json()


def owner_auth(session):
    return auth(session["sessionToken"])


def save(client, session, ast=None, request_id=None, workspace=None):
    return client.post(
        "/api/programs",
        headers=owner_auth(session),
        json={
            "requestId": request_id or str(uuid4()),
            "name": "まえにすすむ",
            "ast": ast or AST,
            "blocklyWorkspace": workspace,
        },
    )


def reserve(client, session, robot="robot-01", request_id=None):
    return client.post(
        f"/api/robots/{robot}/reservations",
        headers=auth(),
        json={
            "requestId": request_id or str(uuid4()),
            "sessionId": session["sessionId"],
        },
    )


def execute(client, program, reservation, request_id=None, **changes):
    body = {
        "requestId": request_id or str(uuid4()),
        "programId": program["programId"],
        "programVersion": program["version"],
        "reservationId": reservation["reservationId"],
    }
    body.update(changes)
    return client.post("/api/executions", headers=auth(), json=body)


def reservation_state(client, reservation):
    r = client.get(f"/api/reservations/{reservation['reservationId']}", headers=auth())
    assert r.status_code == 200, r.text
    return r.json()["status"]


@pytest.fixture
def workshop(tmp_path):
    now = [1000.0]
    path = tmp_path / "workshop.sqlite3"
    with TestClient(create_app(settings(), path, lambda: now[0])) as client:
        agent = Agent(client)
        agent.control()
        first, second = issue(client, "A"), issue(client, "B")
        yield client, agent, now, first, second, path


def test_session_tokens_are_private_and_expire(workshop):
    client, _, now, first, _, path = workshop
    assert len(first["sessionToken"]) >= 32
    assert first["sessionToken"].encode() not in path.read_bytes()
    listed = client.get("/api/sessions", headers=auth()).json()
    assert all("sessionToken" not in item for item in listed)
    assert client.get("/api/robots", headers=owner_auth(first)).status_code == 200
    now[0] = first["expiresAt"]
    assert client.get("/api/programs", headers=owner_auth(first)).status_code == 401


def test_program_roundtrip_edit_and_ownership(workshop):
    client, _, _, first, second, _ = workshop
    workspace = {
        "blocks": {
            "languageVersion": 0,
            "blocks": [{"type": "robot_move_forward", "x": 30, "y": 20}],
        }
    }
    r = save(client, first, REPEAT, workspace=workspace)
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["ast"] == REPEAT and p["blocklyWorkspace"] == workspace
    assert p["expandedCommandCount"] == 4 and p["sessionId"] == first["sessionId"]
    endpoint = f"/api/programs/{p['programId']}"
    assert client.get(endpoint, headers=owner_auth(second)).status_code == 404
    assert client.get(endpoint, headers=auth()).status_code == 200
    assert client.get("/api/programs", headers=owner_auth(second)).json() == []
    assert (
        client.get(
            f"/api/programs?session_id={first['sessionId']}", headers=owner_auth(second)
        ).status_code
        == 403
    )
    edit = {"expectedVersion": 1, "name": "修正", "ast": AST, "blocklyWorkspace": workspace}
    assert client.put(endpoint, headers=owner_auth(second), json=edit).status_code == 404
    updated = client.put(endpoint, headers=owner_auth(first), json=edit)
    assert updated.status_code == 200 and updated.json()["version"] == 2
    assert client.put(endpoint, headers=owner_auth(first), json=edit).status_code == 409


def test_program_create_retry_is_scoped_to_owner(workshop):
    client, _, _, first, second, _ = workshop
    request = str(uuid4())
    a = save(client, first, request_id=request).json()
    assert save(client, first, request_id=request).json()["programId"] == a["programId"]
    assert save(client, first, REPEAT, request).status_code == 409
    assert save(client, second, request_id=request).json()["programId"] != a["programId"]


def test_station_role_is_required_for_allocation(workshop):
    client, _, _, first, _, _ = workshop
    assert client.post("/api/sessions", headers=owner_auth(first), json={}).status_code == 403
    assert client.get("/api/sessions", headers=owner_auth(first)).status_code == 403
    assert save(client, {"sessionToken": "s" * 40}).status_code == 403
    assert (
        client.post(
            "/api/robots/robot-01/reservations",
            headers=owner_auth(first),
            json={"requestId": str(uuid4()), "sessionId": first["sessionId"]},
        ).status_code
        == 403
    )
    assert client.post("/api/executions", headers=owner_auth(first), json={}).status_code == 403
    assert client.get("/api/robot-events", headers=owner_auth(first)).status_code == 403


def test_reservation_retry_release_and_competition(workshop):
    client, _, _, first, second, _ = workshop
    request = str(uuid4())
    r = reserve(client, first, request_id=request)
    assert r.status_code == 200, r.text
    reservation = r.json()
    assert reserve(client, first, request_id=request).json() == reservation
    assert reserve(client, second, request_id=request).status_code == 409
    assert reserve(client, second).status_code == 409
    assert submit(client).status_code == 409
    endpoint = f"/api/reservations/{reservation['reservationId']}"
    assert client.get(endpoint, headers=owner_auth(second)).status_code == 404
    assert client.delete(endpoint, headers=owner_auth(second)).status_code == 404
    assert client.delete(endpoint, headers=owner_auth(first)).json()["status"] == "RELEASED"
    assert reserve(client, second).status_code == 200


def test_simultaneous_reservations_have_one_winner(workshop):
    client, _, _, first, second, _ = workshop
    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(pool.map(lambda owner: reserve(client, owner).status_code, [first, second]))
    assert sorted(codes) == [200, 409]


def test_unused_reservation_expires_and_renewal_is_explicit(workshop):
    client, agent, now, first, second, _ = workshop
    reservation = reserve(client, first).json()
    now[0] += 30
    endpoint = f"/api/reservations/{reservation['reservationId']}/renew"
    renewed = client.post(endpoint, headers=auth(), json={"sessionId": first["sessionId"]})
    assert renewed.status_code == 200 and renewed.json()["expiresAt"] == now[0] + 60
    now[0] += 60
    assert reservation_state(client, reservation) == "EXPIRED"
    assert (
        client.post(endpoint, headers=auth(), json={"sessionId": first["sessionId"]}).status_code
        == 409
    )
    agent.control()
    assert reserve(client, second).status_code == 200


def test_execution_snapshot_repeat_and_terminal_release(workshop):
    client, agent, _, first, _, _ = workshop
    p = save(client, first, REPEAT).json()
    reservation = reserve(client, first).json()
    r = execute(client, p, reservation)
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["sessionId"] == first["sessionId"]
    assert run["programId"] == p["programId"] and run["kind"] == "WORKSHOP"
    assert reservation_state(client, reservation) == "IN_USE"
    edit = {"expectedVersion": 1, "name": "実行後に編集", "ast": AST}
    assert (
        client.put(
            f"/api/programs/{p['programId']}", headers=owner_auth(first), json=edit
        ).status_code
        == 200
    )
    start = agent.control().json()
    assert start["action"] == "START" and len(start["commands"]) == 4
    assert start["commands"] == REPEAT["commands"][0]["body"] * 2
    assert agent.control("RUNNING", run["executionId"], 1).json()["action"] == "CONTINUE"
    assert agent.control("COMPLETED", run["executionId"], 4).json()["action"] == "ACK"
    assert reservation_state(client, reservation) == "FINISHED"
    agent.control()
    assert client.get("/api/robots", headers=auth()).json()[0]["available"] is True
    assert (
        client.get(f"/api/executions/{run['executionId']}", headers=owner_auth(first)).json()[
            "programVersion"
        ]
        == 1
    )


def test_saved_program_owner_and_version_must_match_reservation(workshop):
    client, _, _, first, second, _ = workshop
    p = save(client, first).json()
    reservation = reserve(client, second).json()
    assert execute(client, p, reservation).status_code == 409
    client.delete(f"/api/reservations/{reservation['reservationId']}", headers=auth())
    reservation = reserve(client, first).json()
    assert execute(client, p, reservation, programVersion=2).status_code == 409
    assert reservation_state(client, reservation) == "RESERVED"


def test_expired_reservation_cannot_start_execution(workshop):
    client, agent, now, first, _, _ = workshop
    p = save(client, first).json()
    reservation = reserve(client, first).json()
    now[0] += 60
    agent.control()
    assert execute(client, p, reservation).status_code == 409
    assert agent.control().json()["action"] == "IDLE"


def test_reservation_expiry_does_not_release_an_execution(workshop):
    client, agent, now, first, second, _ = workshop
    p = save(client, first).json()
    reservation = reserve(client, first).json()
    now[0] += 59
    agent.control()
    run = execute(client, p, reservation).json()
    agent.control()
    for _ in range(4):
        now[0] += 0.5
        assert agent.control("RUNNING", run["executionId"]).json()["action"] == "CONTINUE"
    assert now[0] > reservation["expiresAt"]
    assert reservation_state(client, reservation) == "IN_USE"
    assert reserve(client, second).status_code == 409
    assert (
        client.delete(
            f"/api/reservations/{reservation['reservationId']}", headers=auth()
        ).status_code
        == 409
    )
    agent.control("COMPLETED", run["executionId"], 1)
    assert reservation_state(client, reservation) == "FINISHED"


def test_execution_idempotency_precedes_program_edits_and_reservation_expiry(workshop):
    client, agent, now, first, _, _ = workshop
    p = save(client, first).json()
    reservation = reserve(client, first).json()
    request = str(uuid4())
    run = execute(client, p, reservation, request).json()
    agent.control()
    agent.control("COMPLETED", run["executionId"], 1)
    now[0] += 61
    retry = execute(client, p, reservation, request)
    assert retry.status_code == 200 and retry.json()["status"] == "COMPLETED"
    assert execute(client, p, reservation, request, programVersion=2).status_code == 409
    assert submit(client, request_id=request).status_code == 409


def test_concurrent_execution_requests_cannot_double_consume_reservation(workshop):
    client, _, _, first, _, _ = workshop
    p, reservation = save(client, first).json(), reserve(client, first).json()
    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(pool.map(lambda _: execute(client, p, reservation).status_code, range(2)))
    assert sorted(codes) == [200, 409]


def test_participant_cannot_read_or_stop_another_execution(workshop):
    client, agent, _, first, second, _ = workshop
    p, reservation = save(client, first).json(), reserve(client, first).json()
    run = execute(client, p, reservation).json()
    endpoint = f"/api/executions/{run['executionId']}"
    assert client.get(endpoint, headers=owner_auth(second)).status_code == 404
    assert client.get("/api/executions", headers=owner_auth(second)).json() == []
    assert client.post(endpoint + "/stop", headers=owner_auth(second)).status_code == 404
    agent.control()
    assert (
        client.post(endpoint + "/stop", headers=owner_auth(first)).json()["status"]
        == "STOP_REQUESTED"
    )
    assert reservation_state(client, reservation) == "IN_USE"
    agent.control("STOPPED", run["executionId"], error="STOP_REQUESTED")
    assert reservation_state(client, reservation) == "FINISHED"


def test_revoke_session_releases_unused_reservation(workshop):
    client, _, _, first, _, _ = workshop
    reservation = reserve(client, first).json()
    assert (
        client.delete(f"/api/sessions/{first['sessionId']}", headers=auth()).json()["revoked"]
        is True
    )
    assert reservation_state(client, reservation) == "EXPIRED"
    assert save(client, first).status_code == 401


def test_revoke_session_stops_active_execution_without_reassignment(workshop):
    client, agent, _, first, second, _ = workshop
    p, reservation = save(client, first).json(), reserve(client, first).json()
    run = execute(client, p, reservation).json()
    agent.control()
    client.delete(f"/api/sessions/{first['sessionId']}", headers=auth())
    assert agent.control("RUNNING", run["executionId"]).json()["action"] == "STOP"
    assert reserve(client, second).status_code == 409
    assert reservation_state(client, reservation) == "IN_USE"
    agent.control("STOPPED", run["executionId"])
    assert reservation_state(client, reservation) == "FINISHED"


def test_event_history_is_ordered_and_terminal_retry_does_not_duplicate(workshop):
    client, agent, _, first, _, _ = workshop
    p, reservation = save(client, first).json(), reserve(client, first).json()
    run = execute(client, p, reservation).json()
    agent.control()
    agent.control("RUNNING", run["executionId"])
    agent.control("COMPLETED", run["executionId"], 1)
    response = client.get("/api/robot-events", headers=auth())
    assert response.status_code == 200, response.text
    events = response.json()
    assert [e["eventId"] for e in events] == sorted(e["eventId"] for e in events)
    assert {e["eventType"] for e in events} >= {
        "RESERVATION_CREATED",
        "EXECUTION_CREATED",
        "EXECUTION_STATUS",
        "RESERVATION_FINISHED",
    }
    agent.control("COMPLETED", run["executionId"], 1)
    assert client.get("/api/robot-events", headers=auth()).json() == events
    assert (
        client.get(f"/api/robot-events?after_id={events[-1]['eventId']}", headers=auth()).json()
        == []
    )


def test_restart_keeps_in_use_reservation_until_stop_ack(tmp_path):
    path, now = tmp_path / "db", [1000.0]
    with TestClient(create_app(settings(), path, lambda: now[0])) as client:
        first = issue(client)
        agent = Agent(client)
        agent.control()
        p, reservation = save(client, first).json(), reserve(client, first).json()
        run = execute(client, p, reservation).json()
        agent.control()
    now[0] += 100
    with TestClient(create_app(settings(), path, lambda: now[0])) as client:
        assert reservation_state(client, reservation) == "IN_USE"
        assert (
            client.get(f"/api/programs/{p['programId']}", headers=owner_auth(first)).status_code
            == 200
        )
        rebooted = Agent(client)
        assert rebooted.control().json()["action"] == "STOP"
        assert submit(client).status_code == 409
        rebooted.control("STOPPED", run["executionId"])
        assert reservation_state(client, reservation) == "FINISHED"
        rebooted.control()
        assert reserve(client, first).status_code == 200


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
def test_invalid_ast_is_rejected_before_saving(workshop, ast):
    client, _, _, first, _, _ = workshop
    assert save(client, first, ast).status_code == 422


def test_workspace_size_is_bounded(workshop):
    client, _, _, first, _, _ = workshop
    assert save(client, first, workspace={"note": "あ" * 70000}).status_code == 422


def test_trial_and_reservation_acquisition_are_atomic(workshop):
    client, _, _, first, _, _ = workshop
    with ThreadPoolExecutor(max_workers=2) as pool:
        trial = pool.submit(submit, client)
        reservation = pool.submit(reserve, client, first)
        assert sorted([trial.result().status_code, reservation.result().status_code]) == [200, 409]


def test_program_concurrent_edits_have_one_winner(workshop):
    client, _, _, first, _, _ = workshop
    p = save(client, first).json()
    edit = {"expectedVersion": 1, "name": "変更", "ast": AST}
    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(
            pool.map(
                lambda _: (
                    client.put(
                        f"/api/programs/{p['programId']}", headers=owner_auth(first), json=edit
                    ).status_code
                ),
                range(2),
            )
        )
    assert sorted(codes) == [200, 409]


def test_two_robots_execute_independently(workshop):
    client, agent, _, first, second, _ = workshop
    other = Agent(client, "b" * 40, "robot-02")
    other.control()
    one = execute(client, save(client, first).json(), reserve(client, first).json()).json()
    two = execute(
        client, save(client, second).json(), reserve(client, second, "robot-02").json()
    ).json()
    assert one["executionSequence"] != two["executionSequence"]
    assert agent.control().json()["executionId"] == one["executionId"]
    assert other.control().json()["executionId"] == two["executionId"]
    agent.control("COMPLETED", one["executionId"], 1)
    assert other.control("RUNNING", two["executionId"]).json()["action"] == "CONTINUE"


def test_expanded_execution_time_is_validated_with_robot_calibration(workshop):
    client, _, _, first, _, path = workshop
    p = save(
        client,
        first,
        {
            "version": 1,
            "commands": [
                {"type": "REPEAT", "count": 7, "body": [{"type": "WAIT", "durationMs": 5000}]}
            ],
        },
    ).json()
    reservation = reserve(client, first).json()
    assert execute(client, p, reservation).status_code == 422
    assert reservation_state(client, reservation) == "RESERVED"
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 0


def test_revoke_session_cancels_queued_execution_without_dispatch(workshop):
    client, agent, _, first, _, _ = workshop
    p, reservation = save(client, first).json(), reserve(client, first).json()
    run = execute(client, p, reservation).json()
    client.delete(f"/api/sessions/{first['sessionId']}", headers=auth())
    assert reservation_state(client, reservation) == "FINISHED"
    assert (
        client.get(f"/api/executions/{run['executionId']}", headers=auth()).json()["status"]
        == "STOPPED"
    )
    assert agent.control().json()["action"] == "IDLE"


@pytest.mark.parametrize("version", [True, "1", 1.0])
def test_ast_version_type_is_strict(workshop, version):
    client, _, _, first, _, _ = workshop
    assert save(client, first, {"version": version, "commands": [MOVE]}).status_code == 422


def test_readiness_lists_reserved_running_and_offline_without_freeing_owner(workshop):
    client, agent, now, first, _, _ = workshop
    p, reservation = save(client, first).json(), reserve(client, first).json()
    assert client.get("/api/robots", headers=auth()).json()[0]["availability"] == "RESERVED"
    run = execute(client, p, reservation).json()
    assert client.get("/api/robots", headers=auth()).json()[0]["availability"] == "READY"
    agent.control()
    agent.control("RUNNING", run["executionId"])
    assert client.get("/api/robots", headers=auth()).json()[0]["availability"] == "RUNNING"
    now[0] += 4
    robot = client.get("/api/robots", headers=auth()).json()[0]
    assert robot["availability"] == "OFFLINE" and robot["available"] is False
    assert robot["reservationId"] == reservation["reservationId"]
    assert reservation_state(client, reservation) == "IN_USE"


def test_expired_dispatch_records_one_stop_transition(workshop):
    client, agent, now, first, _, _ = workshop
    program = save(client, first).json()
    reservation = reserve(client, first).json()
    run = execute(client, program, reservation).json()
    now[0] += 3
    response = agent.control()
    assert response.status_code == 200
    assert response.json()["action"] == "STOP"
    events = client.get(
        f"/api/robot-events?execution_id={run['executionId']}", headers=auth()
    ).json()
    stops = [e for e in events if e["payload"].get("to") == "STOP_REQUESTED"]
    assert len(stops) == 1
    assert reservation_state(client, reservation) == "IN_USE"
