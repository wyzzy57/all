#!/usr/bin/env bash
set -euo pipefail

exec python3 - "$@" <<'PY'
import json
import re
import subprocess
import sys
from urllib.request import urlopen


INSTANCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}\Z")
CONTAINER_ID = re.compile(r"[a-f0-9]{12,64}\Z")
LABEL_KEY = "com.visiox.deployment-instance-id"
OBSERVED_LABELS = (
    LABEL_KEY,
    "com.visiox.image-digest",
    "com.visiox.model-checksum",
    "com.visiox.engine",
    "com.visiox.engine-digest",
    "com.visiox.port",
)


def _validate_request(value):
    if (
        not isinstance(value, dict)
        or set(value) != {"labels", "expected_container_id", "port"}
        or not isinstance(value["labels"], dict)
        or set(value["labels"]) != {LABEL_KEY}
        or not isinstance(value["labels"][LABEL_KEY], str)
        or not INSTANCE_ID.fullmatch(value["labels"][LABEL_KEY])
        or not isinstance(value["expected_container_id"], str)
        or not CONTAINER_ID.fullmatch(value["expected_container_id"])
        or not isinstance(value["port"], int)
        or isinstance(value["port"], bool)
        or not 1024 <= value["port"] <= 65535
    ):
        raise ValueError("invalid deployment inspection request")
    return value


def _list_command(request):
    return [
        "docker",
        "ps",
        "-a",
        "--filter",
        f"label={LABEL_KEY}={request['labels'][LABEL_KEY]}",
        "--format",
        "{{.ID}}",
    ]


def _endpoint_reachable(port):
    try:
        with urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as response:
            payload = json.load(response)
        return (
            response.status == 200
            and payload.get("status") == "ok"
            and payload.get("task") == "detect"
        )
    except Exception:
        return False


def _inspect(container_id, request):
    result = subprocess.run(
        ["docker", "inspect", "--format", "{{json .}}", container_id],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    raw = json.loads(result.stdout)
    state = raw.get("State") if isinstance(raw, dict) else None
    config = raw.get("Config") if isinstance(raw, dict) else None
    labels = config.get("Labels") if isinstance(config, dict) else None
    health = state.get("Health") if isinstance(state, dict) else None
    observed_id = raw.get("Id") if isinstance(raw, dict) else None
    status = state.get("Status") if isinstance(state, dict) else None
    health_status = health.get("Status") if isinstance(health, dict) else None
    if (
        not isinstance(observed_id, str)
        or not CONTAINER_ID.fullmatch(observed_id)
        or status not in {"created", "restarting", "running", "removing", "paused", "exited", "dead"}
        or health_status not in {None, "starting", "healthy", "unhealthy"}
        or not isinstance(labels, dict)
    ):
        raise ValueError("invalid Docker inspection response")
    observed_labels = {
        key: labels.get(key)
        for key in OBSERVED_LABELS
        if isinstance(labels.get(key), str)
    }
    return {
        "id": observed_id,
        "status": status,
        "health": health_status,
        "labels": observed_labels,
        "endpoint_reachable": (
            observed_id == request["expected_container_id"]
            and status == "running"
            and _endpoint_reachable(request["port"])
        ),
    }


def main():
    if len(sys.argv) != 2:
        return 2
    try:
        with open(sys.argv[1], "r", encoding="utf-8") as request_file:
            request = _validate_request(json.load(request_file))
        listed = subprocess.run(
            _list_command(request),
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        container_ids = [item.strip() for item in listed.stdout.splitlines() if item.strip()]
        if any(not CONTAINER_ID.fullmatch(item) for item in container_ids):
            raise ValueError("invalid Docker response")
        containers = [_inspect(container_id, request) for container_id in container_ids]
        print(
            json.dumps(
                {"containers": containers},
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return 0
    except Exception:
        print("deployment inspection failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
PY
