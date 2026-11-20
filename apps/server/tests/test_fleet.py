from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from fastapi.testclient import TestClient
from test_executions import Agent, auth

from app.config import WorkshopConfig
from app.main import create_app


def fleet_config():
    return WorkshopConfig.model_validate(
        {
            "stationToken": "s" * 40,
            "robots": [
                {
                    "id": f"robot-{i:02d}",
                    "displayName": f"Robot {i}",
                    "token": str(i) * 40,
                    "motionEnabled": True,
                }
                for i in range(1, 4)
            ],
        }
    )


def prepare(client):
    agents = [Agent(client, str(i) * 40, f"robot-{i:02d}") for i in range(1, 4)]
    for agent in agents:
        assert agent.control().status_code == 200
    return agents


def launch(client):
    def send(i):
        return client.post(
            f"/api/robots/robot-{i:02d}/executions",
            headers=auth(),
            json={
                "requestId": str(uuid4()),
                "ast": {"version": 1, "commands": [{"type": "WAIT", "durationMs": i * 1000}]},
            },
        )

    with ThreadPoolExecutor(max_workers=3) as pool:
        runs = list(pool.map(send, range(1, 4)))
    assert all(r.status_code == 200 for r in runs)
    return [r.json() for r in runs]


def test_three_robots_execute_distinct_programs_and_stop_independently(tmp_path):
    now = [1000.0]
    with TestClient(create_app(fleet_config(), tmp_path / "fleet.db", lambda: now[0])) as client:
        agents = prepare(client)
        runs = launch(client)
        assert len({r["executionSequence"] for r in runs}) == 3
        assert [r["estimatedDurationMs"] for r in runs] == [1000, 2000, 3000]
        for agent, run in zip(agents, runs):
            start = agent.control().json()
            assert start["action"] == "START" and start["executionId"] == run["executionId"]
            assert agent.control("RUNNING", run["executionId"]).json()["action"] == "CONTINUE"
        client.post(f"/api/executions/{runs[0]['executionId']}/stop", headers=auth())
        assert agents[0].control("RUNNING", runs[0]["executionId"]).json()["action"] == "STOP"
        for i in (1, 2):
            assert (
                agents[i].control("RUNNING", runs[i]["executionId"]).json()["action"] == "CONTINUE"
            )
        assert agents[0].control("STOPPED", runs[0]["executionId"]).json()["action"] == "ACK"
        for i in (1, 2):
            assert (
                agents[i].control("COMPLETED", runs[i]["executionId"], 1).json()["action"] == "ACK"
            )
        states = client.get("/api/executions", headers=auth()).json()
        assert len(states) == 3 and {r["status"] for r in states} == {"STOPPED", "COMPLETED"}


def test_one_offline_robot_holds_occupancy_without_blocking_other_two(tmp_path):
    now = [1000.0]
    with TestClient(create_app(fleet_config(), tmp_path / "fleet.db", lambda: now[0])) as client:
        agents = prepare(client)
        runs = launch(client)
        for agent, run in zip(agents, runs):
            agent.control()
            agent.control("RUNNING", run["executionId"])
        for elapsed in (0.9, 0.9, 0.9, 0.4):
            now[0] += elapsed
            for i in (1, 2):
                assert (
                    agents[i].control("RUNNING", runs[i]["executionId"]).json()["action"]
                    == "CONTINUE"
                )
        robots = client.get("/api/robots", headers=auth()).json()
        assert robots[0]["availability"] == "OFFLINE" and robots[0]["executionId"]
        assert (
            client.post(
                "/api/robots/robot-01/executions",
                headers=auth(),
                json={
                    "requestId": str(uuid4()),
                    "ast": {"version": 1, "commands": [{"type": "WAIT", "durationMs": 1000}]},
                },
            ).status_code
            == 409
        )
        assert all(r["availability"] == "RUNNING" for r in robots[1:])
        assert agents[0].control("RUNNING", runs[0]["executionId"]).json()["action"] == "STOP"
        assert (
            client.get(f"/api/executions/{runs[0]['executionId']}", headers=auth()).json()["status"]
            == "STOP_REQUESTED"
        )


def test_restart_retains_three_occupancies_until_individual_stop_reports(tmp_path):
    now = [1000.0]
    path = tmp_path / "fleet.db"
    with TestClient(create_app(fleet_config(), path, lambda: now[0])) as client:
        agents = prepare(client)
        runs = launch(client)
        for agent, run in zip(agents, runs):
            agent.control()
            agent.control("RUNNING", run["executionId"])
    with TestClient(create_app(fleet_config(), path, lambda: now[0])) as client:
        states = client.get("/api/executions", headers=auth()).json()
        assert len(states) == 3 and all(r["status"] == "STOP_REQUESTED" for r in states)
        for i, (agent, run) in enumerate(zip(agents, runs)):
            agent.client = client
            agent.register()
            assert agent.control("RUNNING", run["executionId"]).json()["action"] == "STOP"
            assert agent.control("STOPPED", run["executionId"]).json()["action"] == "ACK"
            executions = client.get("/api/executions", headers=auth()).json()
            assert sum(r["status"] == "STOP_REQUESTED" for r in executions) == 2 - i
