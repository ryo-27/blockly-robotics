#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/.." && pwd)"
json_include="$repo_root/firmware/.pio/libdeps/esp32dev/ArduinoJson/src"
if [[ ! -d "$json_include" ]]; then
  echo 'First run: apps/server/.venv/bin/pio run --project-dir firmware' >&2
  exit 1
fi
output="$(mktemp -d "${TMPDIR:-/tmp}/robot-firmware-test.XXXXXX")"
trap 'rm -rf "$output"' EXIT
c++ -std=c++17 -Wall -Wextra -Werror -fsanitize=address,undefined \
  -I"$repo_root/firmware/include" -I"$json_include" \
  "$repo_root/firmware/src/executor.cpp" "$repo_root/firmware/src/program_parser.cpp" \
  "$repo_root/firmware/src/control_message.cpp" \
  "$repo_root/firmware/test/executor_test.cpp" -o "$output/executor-test"
(
  cd "$repo_root/apps/server"
  .venv/bin/python - "$output" <<'PYTHON'
import json
import sys
from pathlib import Path
from uuid import uuid4
from fastapi.testclient import TestClient
from app.config import WorkshopConfig
from app.main import create_app

output = Path(sys.argv[1])
config = WorkshopConfig.model_validate({"stationToken": "contract-station-" * 3, "robots": [
    {"id": "robot-01", "displayName": "Contract test", "token": "contract-robot-" * 3, "motionEnabled": True}
]})
with TestClient(create_app(config, output / "contract.sqlite3")) as client:
    auth = {"Authorization": "Bearer " + "contract-robot-" * 3}
    boot = str(uuid4())
    registered = client.post("/api/robot-agent/register", headers=auth, json={
        "protocolVersion": 1, "robotId": "robot-01", "bootId": boot, "firmwareVersion": "contract"
    }).json()
    control = {"protocolVersion": 2, "bootId": boot, "sessionId": registered["sessionId"], "sequence": 1, "status": "IDLE"}
    assert client.post("/api/robot-agent/control", headers=auth, json=control).status_code == 200
    station = {"Authorization": "Bearer " + "contract-station-" * 3}
    participant = client.post("/api/sessions", headers=station, json={"label": "contract"})
    assert participant.status_code == 200
    participant = participant.json()
    program = client.post("/api/programs",
        headers={"Authorization": "Bearer " + participant["sessionToken"]}, json={
            "requestId": str(uuid4()), "ast": {"version": 1, "commands": [
                {"type": "REPEAT", "count": 1, "body": [
                    {"type": "MOVE", "direction": "FORWARD", "steps": 2},
                    {"type": "TURN", "direction": "RIGHT"}, {"type": "WAIT", "durationMs": 100}
                ]}
            ]}
        })
    assert program.status_code == 200
    reservation = client.post("/api/robots/robot-01/reservations", headers=station,
        json={"requestId": str(uuid4()), "sessionId": participant["sessionId"]})
    assert reservation.status_code == 200
    queued = client.post("/api/executions", headers=station, json={
        "requestId": str(uuid4()), "programId": program.json()["programId"],
        "programVersion": program.json()["version"],
        "reservationId": reservation.json()["reservationId"]
    })
    assert queued.status_code == 200
    control["sequence"] = 2
    start = client.post("/api/robot-agent/control", headers=auth, json=control).json()
    assert start["action"] == "START"
    (output / "start.json").write_text(json.dumps(start))
PYTHON
)
"$output/executor-test" "$output/start.json"

(
  cd "$repo_root/apps/server"
  .venv/bin/python - "$output" <<'PYTHON'
import json
import sys
from pathlib import Path
from app.execution_models import ControlRequest

reports = sorted(Path(sys.argv[1]).glob("report-*.json"))
assert len(reports) == 5
states = []
for report in reports:
    body = ControlRequest.model_validate(json.loads(report.read_text()))
    states.append(body.status)
assert states == ["IDLE", "RUNNING", "COMPLETED", "STOPPED", "ERROR"]
print("Firmware-to-server contract passed for all 5 report states")
PYTHON
)
