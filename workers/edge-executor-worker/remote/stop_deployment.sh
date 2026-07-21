#!/usr/bin/env bash
set -euo pipefail

exec python3 - "$@" <<'PY'
import json
import re
import subprocess
import sys


INSTANCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}\Z")
CONTAINER_ID = re.compile(r"[a-f0-9]{12,64}\Z")
LABEL_KEY = "com.visiox.deployment-instance-id"


def _validate_request(value):
    if (
        not isinstance(value, dict)
        or set(value) != {"labels"}
        or not isinstance(value["labels"], dict)
        or set(value["labels"]) != {LABEL_KEY}
        or not isinstance(value["labels"][LABEL_KEY], str)
        or not INSTANCE_ID.fullmatch(value["labels"][LABEL_KEY])
    ):
        raise ValueError("invalid stop request")
    return value


def _list_command(request):
    return [
        "docker",
        "ps",
        "--filter",
        f"label={LABEL_KEY}={request['labels'][LABEL_KEY]}",
        "--format",
        "{{.ID}}",
    ]


def main():
    if len(sys.argv) != 2:
        return 2
    try:
        with open(sys.argv[1], "r", encoding="utf-8") as request_file:
            request = _validate_request(json.load(request_file))
        result = subprocess.run(
            _list_command(request),
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        container_ids = [item.strip() for item in result.stdout.splitlines() if item.strip()]
        if any(not CONTAINER_ID.fullmatch(item) for item in container_ids):
            raise ValueError("invalid Docker response")
        for container_id in container_ids:
            subprocess.run(
                ["docker", "stop", "--time", "10", container_id],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
        print(
            json.dumps(
                {"stopped_container_ids": container_ids},
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return 0
    except Exception:
        print("stop operation failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
PY
