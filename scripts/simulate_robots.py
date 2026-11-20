"""GPIOを持たない検証専用エージェント。実機の設定では起動しない。"""

import argparse
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def simulate(robot, base, stopped):
    boot, session, sequence = str(uuid4()), None, 0
    state, execution, index, deadline = "IDLE", None, 0, 0.0
    durations, command_started, consumed = [], 0.0, 0

    def send(path, body):
        request = urllib.request.Request(
            base + path,
            json.dumps(body).encode(),
            {
                "Authorization": "Bearer " + robot["token"],
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=0.75) as response:
            return json.load(response)

    while not stopped.is_set():
        try:
            if session is None:
                session = send(
                    "/api/robot-agent/register",
                    {
                        "protocolVersion": 1,
                        "robotId": robot["id"],
                        "bootId": boot,
                        "firmwareVersion": "SIMULATION-no-motors",
                    },
                )["sessionId"]
                sequence = 0
            now = time.monotonic()
            if state == "RUNNING":
                if now >= deadline:
                    state = "STOPPED"
                else:
                    while (
                        index < len(durations)
                        and now >= command_started + durations[index]
                    ):
                        command_started += durations[index]
                        index += 1
                    if index == len(durations):
                        state = "COMPLETED"
            sequence += 1
            body = {
                "protocolVersion": 2,
                "bootId": boot,
                "sessionId": session,
                "sequence": sequence,
                "status": state,
                "currentCommandIndex": index,
            }
            if execution:
                body["executionId"] = execution
            if state == "ERROR":
                body["errorCode"] = "SIMULATOR_INVALID_START"
            started = time.monotonic()
            response = send("/api/robot-agent/control", body)
            action = response["action"]
            if action == "START":
                execution, index = response["executionId"], 0
                if response["executionSequence"] <= consumed:
                    state = "ERROR"
                    continue
                consumed = response["executionSequence"]
                calibration = response["calibration"]
                durations = []
                for command in response["commands"]:
                    if command["type"] == "MOVE":
                        key = (
                            "forwardOneCellMs"
                            if command["direction"] == "FORWARD"
                            else "backwardOneCellMs"
                        )
                        duration = command["steps"] * calibration[key]
                    elif command["type"] == "TURN":
                        duration = calibration[
                            "turnLeft90Ms"
                            if command["direction"] == "LEFT"
                            else "turnRight90Ms"
                        ]
                    else:
                        duration = command["durationMs"]
                    durations.append(duration / 1000)
                deadline, command_started = (
                    started + response["leaseMs"] / 1000,
                    time.monotonic(),
                )
                state = "RUNNING" if command_started < deadline else "STOPPED"
                print(
                    f"{robot['id']}: START {execution} ({len(durations)} commands)",
                    flush=True,
                )
            elif action == "CONTINUE" and state == "RUNNING":
                # 応答が遅れて期限を超えた場合、許可を復活させない。
                state = "RUNNING" if time.monotonic() < deadline else "STOPPED"
                if state == "RUNNING":
                    deadline = started + response["leaseMs"] / 1000
            elif action == "STOP":
                execution, state = response["executionId"], "STOPPED"
            elif action == "ACK":
                print(f"{robot['id']}: {state} {execution}", flush=True)
                execution, state, index = None, "IDLE", 0
        except urllib.error.HTTPError as error:
            if error.code in (401, 403, 409):
                session = None
            if state == "RUNNING":
                state = "STOPPED"
        except (OSError, ValueError, KeyError):
            if state == "RUNNING":
                state = "STOPPED"
        stopped.wait(0.2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=ROOT / "config/simulation.example.json"
    )
    parser.add_argument("--server", default="http://127.0.0.1:8003")
    parser.add_argument("--robot", help="1台だけ起動する場合のID")
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    if not all(
        r["id"].startswith("sim-") and r["token"].startswith("simulation-only-")
        for r in config["robots"]
    ):
        parser.error(
            "Simulation-only IDs and tokens required. Do not use config/local.json."
        )
    robots = [r for r in config["robots"] if not args.robot or r["id"] == args.robot]
    if not robots:
        parser.error("Unknown simulated robot")
    stopped = threading.Event()
    threads = [
        threading.Thread(
            target=simulate, args=(r, args.server.rstrip("/"), stopped), daemon=True
        )
        for r in robots
    ]
    print("SIMULATION ONLY — no motor control", flush=True)
    for thread in threads:
        thread.start()
    try:
        while not stopped.wait(1):
            pass
    except KeyboardInterrupt:
        stopped.set()
    for thread in threads:
        thread.join(timeout=2)


if __name__ == "__main__":
    main()
