from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import WorkshopConfig
from app.main import create_app

STATION_TOKEN = "s" * 40
ROBOT_TOKEN = "a" * 40
OTHER_TOKEN = "b" * 40


@pytest.fixture
def config():
    return WorkshopConfig.model_validate(
        {
            "stationToken": STATION_TOKEN,
            "robots": [
                {"id": "robot-01", "displayName": "ロボットA", "token": ROBOT_TOKEN},
                {"id": "robot-02", "displayName": "ロボットB", "token": OTHER_TOKEN},
            ],
        }
    )


def auth(token=ROBOT_TOKEN):
    return {"Authorization": f"Bearer {token}"}


def register(client, boot=None, robot="robot-01", token=ROBOT_TOKEN):
    boot = boot or str(uuid4())
    response = client.post(
        "/api/robot-agent/register",
        headers=auth(token),
        json={
            "protocolVersion": 1,
            "robotId": robot,
            "bootId": boot,
            "firmwareVersion": "test-1",
        },
    )
    return boot, response


def heartbeat(client, boot, session, sequence=1, **changes):
    body = {
        "protocolVersion": 1,
        "bootId": boot,
        "sessionId": session,
        "sequence": sequence,
        "status": "IDLE",
    }
    body.update(changes)
    return client.post("/api/robot-agent/heartbeat", headers=auth(), json=body)


def robots(client):
    response = client.get("/api/robots", headers=auth(STATION_TOKEN))
    assert response.status_code == 200
    return response.json()


@pytest.fixture
def environment(tmp_path, config):
    now = [1000.0]
    app = create_app(config, tmp_path / "robots.sqlite3", lambda: now[0])
    with TestClient(app) as client:
        yield client, now


def test_registration_and_timeout(environment):
    client, now = environment
    assert [r["status"] for r in robots(client)] == ["OFFLINE", "OFFLINE"]
    boot, response = register(client)
    assert response.status_code == 200
    session = response.json()["sessionId"]
    assert response.json()["motorsEnabled"] is False
    assert robots(client)[0]["status"] == "IDLE"
    assert robots(client)[1]["status"] == "OFFLINE"
    now[0] += 3
    assert robots(client)[0]["status"] == "OFFLINE"
    assert heartbeat(client, boot, session).status_code == 200
    assert robots(client)[0]["status"] == "IDLE"


def test_authentication_boundaries(environment):
    client, _ = environment
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/robots").status_code == 401
    assert client.get("/api/robots", headers=auth()).status_code == 403
    assert register(client, token=STATION_TOKEN)[1].status_code == 401
    assert register(client, token="unknown")[1].status_code == 401
    assert register(client, robot="robot-02")[1].status_code == 403
    assert register(client, robot="robot-99")[1].status_code == 403
    assert "token" not in str(robots(client)).lower()


def test_register_retry_is_idempotent(environment):
    client, now = environment
    boot, first = register(client)
    session = first.json()["sessionId"]
    assert heartbeat(client, boot, session, 10).status_code == 200
    now[0] += 1
    assert register(client, boot)[1].json()["sessionId"] == session
    assert heartbeat(client, boot, session, 9).status_code == 409


def test_replay_does_not_extend_online_presence(environment):
    client, now = environment
    boot, response = register(client)
    session = response.json()["sessionId"]
    assert heartbeat(client, boot, session, 5).status_code == 200
    now[0] += 3
    assert heartbeat(client, boot, session, 5).status_code == 200
    assert robots(client)[0]["status"] == "OFFLINE"
    assert heartbeat(client, boot, session, 4).status_code == 409
    assert heartbeat(client, boot, session, 6).status_code == 200
    assert robots(client)[0]["status"] == "IDLE"


def test_reboot_invalidates_old_session(environment):
    client, _ = environment
    old_boot, first = register(client)
    boot, second = register(client)
    assert first.json()["sessionId"] != second.json()["sessionId"]
    assert heartbeat(client, old_boot, first.json()["sessionId"]).status_code == 409
    assert heartbeat(client, boot, first.json()["sessionId"]).status_code == 409
    assert heartbeat(client, boot, second.json()["sessionId"]).status_code == 200


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "RUNNING"},
        {"protocolVersion": 2},
        {"bootId": "invalid"},
        {"sequence": 0},
        {"sequence": True},
        {"sequence": 1.2},
        {"sequence": 2147483648},
        {"executionId": "not-supported"},
    ],
)
def test_invalid_heartbeat_rejected_without_presence_refresh(environment, changes):
    client, now = environment
    boot, response = register(client)
    now[0] += 4
    assert heartbeat(client, boot, response.json()["sessionId"], **changes).status_code == 422
    assert robots(client)[0]["status"] == "OFFLINE"


def test_restart_requires_registration_and_preserves_metadata(tmp_path, config):
    db = tmp_path / "robots.sqlite3"
    with TestClient(create_app(config, db)) as client:
        boot, response = register(client)
        session = response.json()["sessionId"]
    with TestClient(create_app(config, db)) as client:
        row = robots(client)[0]
        assert row["status"] == "OFFLINE"
        assert row["firmwareVersion"] == "test-1"
        assert heartbeat(client, boot, session).status_code == 409
        _, response = register(client, boot)
        assert response.json()["sessionId"] != session
    assert ROBOT_TOKEN.encode() not in db.read_bytes()


def test_concurrent_heartbeats_do_not_regress_sequence(environment):
    client, _ = environment
    boot, response = register(client)
    session = response.json()["sessionId"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(lambda i: heartbeat(client, boot, session, i).status_code, range(1, 21))
        )
    assert set(results) <= {200, 409}
    assert heartbeat(client, boot, session, 19).status_code == 409
    assert heartbeat(client, boot, session, 21).status_code == 200


def test_config_removal_and_token_rotation(tmp_path, config):
    db = tmp_path / "robots.sqlite3"
    with TestClient(create_app(config, db)) as client:
        register(client)
    updated = config.model_dump(by_alias=True)
    updated["robots"] = [{"id": "robot-01", "displayName": "新しい名前", "token": "c" * 40}]
    new_config = WorkshopConfig.model_validate(updated)
    with TestClient(create_app(new_config, db)) as client:
        assert len(robots(client)) == 1
        assert robots(client)[0]["displayName"] == "新しい名前"
        assert register(client)[1].status_code == 401
        assert register(client, token=OTHER_TOKEN, robot="robot-02")[1].status_code == 401
        assert register(client, token="c" * 40)[1].status_code == 200


def test_duplicate_credentials_rejected(config):
    raw = config.model_dump(by_alias=True)
    raw["robots"][0]["token"] = STATION_TOKEN
    with pytest.raises(ValueError):
        WorkshopConfig.model_validate(raw)
