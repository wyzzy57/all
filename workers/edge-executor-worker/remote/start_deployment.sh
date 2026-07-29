#!/usr/bin/env bash
set -euo pipefail

exec python3 - "$@" <<'PY'
import json
import re
import subprocess
import sys
import time
import urllib.request


INSTANCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}\Z")
CONTAINER_ID = re.compile(r"[a-f0-9]{12,64}\Z")
LABEL_KEY = "com.visiox.deployment-instance-id"


def _validate_request(value):
    if not isinstance(value, dict) or set(value) != {
        "action",
        "container_id",
        "labels",
        "port",
    }:
        raise ValueError("invalid start request")
    if value["action"] not in {"start", "restart"}:
        raise ValueError("invalid start action")
    if not isinstance(value["container_id"], str) or not CONTAINER_ID.fullmatch(
        value["container_id"]
    ):
        raise ValueError("invalid container identity")
    if (
        not isinstance(value["labels"], dict)
        or set(value["labels"]) != {LABEL_KEY}
        or not isinstance(value["labels"][LABEL_KEY], str)
        or not INSTANCE_ID.fullmatch(value["labels"][LABEL_KEY])
    ):
        raise ValueError("invalid deployment label")
    if not isinstance(value["port"], int) or not 1024 <= value["port"] <= 65535:
        raise ValueError("invalid service port")
    return value


def _run(args, timeout=30):
    return subprocess.run(
        args,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _inspect(request):
    result = _run(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            f"label={LABEL_KEY}={request['labels'][LABEL_KEY]}",
            "--format",
            "{{.ID}}",
        ]
    )
    identities = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    expected = request["container_id"]
    if len(identities) != 1 or not CONTAINER_ID.fullmatch(identities[0]):
        raise ValueError("deployment container is unavailable")
    if not expected.startswith(identities[0]) and not identities[0].startswith(expected):
        raise ValueError("deployment container identity mismatch")
    running = _run(["docker", "inspect", "--format", "{{.State.Running}}", expected])
    state = running.stdout.strip().lower()
    if state not in {"true", "false"}:
        raise ValueError("invalid container state")
    return state == "true"


def _wait_healthy(port):
    url = f"http://127.0.0.1:{port}/health"
    for _ in range(30):
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if 200 <= response.status < 300:
                    return
        except Exception:
            time.sleep(1)
    raise RuntimeError("deployment health check failed")


def main():
    if len(sys.argv) != 2:
        return 2
    try:
        with open(sys.argv[1], "r", encoding="utf-8") as request_file:
            request = _validate_request(json.load(request_file))
        already_running = _inspect(request)
        if request["action"] == "restart":
            _run(["docker", "restart", "--time", "10", request["container_id"]], 60)
        elif not already_running:
            _run(["docker", "start", request["container_id"]], 60)
        _wait_healthy(request["port"])
        print(
            json.dumps(
                {
                    "already_running": already_running,
                    "container_id": request["container_id"],
                    "health_status": "healthy",
                    "port": request["port"],
                },
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return 0
    except Exception:
        print("start operation failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
PY
