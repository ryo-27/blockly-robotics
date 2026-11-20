import json
import sqlite3
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_executions import Agent, auth, settings

from app.main import create_app


def move(steps=1, direction="FORWARD"):
    return {"type": "MOVE", "direction": direction, "steps": steps}


RIGHT = {"type": "TURN", "direction": "RIGHT"}
WAIT = {"type": "WAIT", "durationMs": 1}


def branch(condition=None, body=None, other=None):
    return {
        "type": "IF",
        "condition": condition or {"type": "CAN_MOVE_FORWARD"},
        "body": body or [move()],
        "elseBody": other or [],
    }


@pytest.fixture
def quest_app(tmp_path):
    path = tmp_path / "quests.db"
    with TestClient(create_app(settings(), path, lambda: 1000.0)) as client:
        agent = Agent(client)
        agent.control()
        yield client, agent, path


def send(client, commands, quest="Q01", version=1, request=None):
    return client.post(
        "/api/robots/robot-01/executions",
        headers=auth(),
        json={
            "requestId": request or str(uuid4()),
            "ast": {"version": 1, "commands": commands},
            **({"questId": quest, "questVersion": version} if quest else {}),
        },
    )


def test_if_is_resolved_and_clear_is_only_visible_after_completion(quest_app):
    client, agent, path = quest_app
    commands = [
        branch(body=[move(2)], other=[move(1, "BACKWARD")]),
        RIGHT,
        move(),
        branch({"type": "ON_CELL", "cellType": "GOAL"}, [RIGHT], [WAIT]),
    ]
    response = send(client, commands)
    assert response.status_code == 200
    run = response.json()
    assert run["questCleared"] is None
    assert run["commandBlockIndices"] == [0, 2, 3, 4]
    frame = agent.control().json()
    assert frame["commands"] == [move(2), RIGHT, move(), RIGHT]
    assert all(c["type"] in ("MOVE", "TURN", "WAIT") for c in frame["commands"])
    assert (
        client.get(f"/api/executions/{run['executionId']}", headers=auth()).json()["questCleared"]
        is None
    )
    agent.control("COMPLETED", run["executionId"], 4)
    result = client.get(f"/api/executions/{run['executionId']}", headers=auth()).json()
    # B3到達・最後は南向き。ゴールの向きは判定しない。
    assert result["questCleared"] is True
    with sqlite3.connect(path) as conn:
        snapshot = json.loads(conn.execute("SELECT quest_snapshot FROM executions").fetchone()[0])
        assert snapshot["id"] == "Q01" and snapshot["version"] == 1


def test_repeat_rechecks_condition_after_each_move_and_turn(quest_app):
    client, agent, _ = quest_app
    run = send(client, [{"type": "REPEAT", "count": 6, "body": [branch(other=[RIGHT])]}]).json()
    assert agent.control().json()["commands"] == [move()] * 4 + [RIGHT, move()]
    assert run["commandBlockIndices"] == [0, 0, 0, 0, 1, 0]
    agent.control("COMPLETED", run["executionId"], 6)
    assert (
        client.get(f"/api/executions/{run['executionId']}", headers=auth()).json()["questCleared"]
        is False
    )


@pytest.mark.parametrize("commands", [[move(1, "BACKWARD")], [move(5)], [move(), RIGHT, move(4)]])
def test_obstacles_and_intermediate_cells_abort_without_queuing(quest_app, commands):
    client, agent, path = quest_app
    response = send(client, commands)
    assert response.status_code == 422 and response.json()["detail"] == "QUEST_PATH_BLOCKED"
    assert agent.control().json()["action"] == "IDLE"
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 0


@pytest.mark.parametrize(
    "quest,commands",
    [
        ("Q01", [branch({"type": "ON_CELL", "cellType": "START"}, [WAIT], [RIGHT])]),
        ("Q01", [move(), branch({"type": "ON_CELL", "cellType": "PATH"}, [WAIT], [RIGHT])]),
        (
            "Q03",
            [
                move(3),
                RIGHT,
                move(),
                branch({"type": "ON_CELL", "cellType": "CHECKPOINT"}, [WAIT], [RIGHT]),
            ],
        ),
    ],
)
def test_each_cell_condition(quest_app, quest, commands):
    client, agent, _ = quest_app
    assert send(client, commands, quest=quest).status_code == 200
    assert agent.control().json()["commands"][-1] == WAIT


def test_if_else_handles_blocked_front(quest_app):
    client, agent, _ = quest_app
    assert send(client, [move(), RIGHT, move(), branch(other=[RIGHT])]).status_code == 200
    # B2の東はC2の岩。
    assert agent.control().json()["commands"] == [move(), RIGHT, move(), RIGHT]


def test_quest_optional_but_required_for_if_and_version_is_fixed(quest_app):
    client, _, _ = quest_app
    assert send(client, [branch()], quest=None).status_code == 422
    assert send(client, [WAIT], quest="UNKNOWN").status_code == 404
    assert send(client, [WAIT], version=2).status_code == 409
    assert send(client, [WAIT], version=True).status_code == 422
    assert send(client, [WAIT], quest=None).status_code == 200


def test_no_commands_and_runtime_limit_are_rejected(quest_app):
    client, _, _ = quest_app
    assert (
        send(client, [branch({"type": "ON_CELL", "cellType": "GOAL"})]).json()["detail"]
        == "NO_COMMANDS"
    )
    response = send(client, [{"type": "REPEAT", "count": 20, "body": [branch(body=[WAIT] * 4)]}])
    assert response.status_code == 422 and response.json()["detail"] == "EXPANDED_PROGRAM_TOO_LARGE"
    assert (
        send(
            client, [{"type": "REPEAT", "count": 7, "body": [{"type": "WAIT", "durationMs": 5000}]}]
        ).json()["detail"]
        == "EXECUTION_TOO_LONG"
    )


def test_retry_keeps_quest_and_does_not_dispatch_twice(quest_app):
    client, agent, _ = quest_app
    request = str(uuid4())
    run = send(client, [branch(body=[WAIT])], request=request).json()
    agent.control()
    agent.control("COMPLETED", run["executionId"], 1)
    agent.control()
    assert (
        send(client, [branch(body=[WAIT])], request=request).json()["executionId"]
        == run["executionId"]
    )
    assert send(client, [branch(body=[WAIT])], quest="Q02", request=request).status_code == 409
    assert agent.control().json()["action"] == "IDLE"


def test_catalog_auth_and_no_solution_preview(quest_app):
    client, _, _ = quest_app
    assert client.get("/api/quests").status_code == 401
    assert client.get("/api/quests", headers=auth("a" * 40)).status_code == 403
    catalog = client.get("/api/quests", headers=auth()).json()
    assert len(catalog) == 8 and set(catalog[0]) == {"id", "title", "version"}


@pytest.mark.parametrize("status,error", [("STOPPED", None), ("ERROR", "TEST_ERROR")])
def test_incomplete_execution_never_reports_quest_success(quest_app, status, error):
    client, agent, _ = quest_app
    run = send(client, [WAIT], quest="Q06").json()
    agent.control()
    agent.control(status, run["executionId"], error=error)
    result = client.get(f"/api/executions/{run['executionId']}", headers=auth()).json()
    assert result["status"] == status and result["questCleared"] is None


def test_checkpoints_are_not_clear_requirements(quest_app):
    client, agent, _ = quest_app
    response = send(
        client, [RIGHT, move(4), {"type": "TURN", "direction": "LEFT"}, move()], quest="Q03"
    )
    assert response.status_code == 200
    run = response.json()
    agent.control()
    agent.control("COMPLETED", run["executionId"], 4)
    # E2に到達すれば、B4の調査ポイントを経由していなくてもクリア。
    assert (
        client.get(f"/api/executions/{run['executionId']}", headers=auth()).json()["questCleared"]
        is True
    )


def test_retry_uses_frozen_map_even_after_catalog_update(quest_app, monkeypatch):
    from app import quests

    client, _, _ = quest_app
    request = str(uuid4())
    run = send(client, [WAIT], request=request).json()
    monkeypatch.setattr(quests, "load_quests", dict)
    assert send(client, [WAIT], request=request).json()["executionId"] == run["executionId"]
    assert send(client, [WAIT]).status_code == 404


def test_invalid_condition_and_deep_hidden_branch_are_rejected(quest_app):
    client, _, _ = quest_app
    assert send(client, [branch({"type": "ON_CELL", "cellType": "UNKNOWN"})]).status_code == 422
    nested = WAIT
    for _ in range(4):
        nested = branch(body=[WAIT], other=[nested])
    assert send(client, [nested]).status_code == 422
