#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
    exit 2
fi

request_path="$1"

/usr/bin/python3 - "$request_path" <<'PY'
import json
from pathlib import Path
import re
import subprocess
import sys


ALLOWED_LABELS = {
    "com.visiox.managed",
    "com.visiox.remote-execution-id",
    "com.visiox.node-id",
    "com.visiox.resource-type",
    "com.visiox.resource-id",
}
SAFE_VALUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}\Z")

request_path = Path(sys.argv[1])
raw = request_path.read_bytes()
if len(raw) > 16_384:
    raise SystemExit(3)
request = json.loads(raw)
if not isinstance(request, dict) or set(request) != {"labels"}:
    raise SystemExit(3)
labels = request["labels"]
if (
    not isinstance(labels, dict)
    or not {"com.visiox.managed", "com.visiox.remote-execution-id", "com.visiox.node-id"}.issubset(labels)
    or not set(labels).issubset(ALLOWED_LABELS)
    or labels.get("com.visiox.managed") != "true"
    or any(not isinstance(value, str) or not SAFE_VALUE.fullmatch(value) for value in labels.values())
):
    raise SystemExit(3)

command = ["/usr/bin/docker", "ps", "-aq"]
for key, value in sorted(labels.items()):
    command.extend(["--filter", f"label={key}={value}"])
listed = subprocess.run(command, check=True, capture_output=True, text=True, timeout=20)
container_ids = [item for item in listed.stdout.splitlines() if item]
if len(container_ids) > 2 or any(not re.fullmatch(r"[0-9a-f]{12,64}", item) for item in container_ids):
    raise SystemExit(4)

containers = []
if container_ids:
    inspected = subprocess.run(
        ["/usr/bin/docker", "inspect", *container_ids],
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    records = json.loads(inspected.stdout)
    if not isinstance(records, list) or len(records) != len(container_ids):
        raise SystemExit(4)
    for record in records:
        state = record.get("State", {})
        health = state.get("Health")
        containers.append(
            {
                "id": record.get("Id"),
                "status": state.get("Status"),
                "exit_code": state.get("ExitCode"),
                "oom_killed": state.get("OOMKilled"),
                "health": health.get("Status") if isinstance(health, dict) else None,
            }
        )

print(json.dumps({"containers": containers}, ensure_ascii=True, separators=(",", ":"), sort_keys=True))
PY
