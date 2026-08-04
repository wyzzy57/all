#!/usr/bin/env bash
set -euo pipefail

exec python3 - "$@" <<'PY'
import json
import re
import subprocess
import sys


IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}\Z")
CONTAINER_ID = re.compile(r"[a-f0-9]{12,64}\Z")


def validate(request):
    if not isinstance(request, dict) or set(request) != {"schema_version", "run_id", "attempt"}:
        raise ValueError("invalid distributed stop request")
    if request["schema_version"] != "1.0":
        raise ValueError("invalid distributed stop request")
    if not isinstance(request["run_id"], str) or not IDENTIFIER.fullmatch(request["run_id"]):
        raise ValueError("invalid distributed stop request")
    if not isinstance(request["attempt"], int) or isinstance(request["attempt"], bool) or request["attempt"] < 1:
        raise ValueError("invalid distributed stop request")
    return request


def main():
    if len(sys.argv) != 2:
        return 2
    try:
        with open(sys.argv[1], "r", encoding="utf-8") as source:
            request = validate(json.load(source))
        filters = [
            "--filter", f"label=com.visiox.training-run-id={request['run_id']}",
            "--filter", f"label=com.visiox.training-attempt={request['attempt']}",
        ]
        result = subprocess.run(["docker", "ps", "-a", *filters, "--format", "{{.ID}}"], check=True, capture_output=True, text=True, timeout=30)
        container_ids = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if any(not CONTAINER_ID.fullmatch(item) for item in container_ids):
            raise ValueError("invalid Docker response")
        for container_id in container_ids:
            subprocess.run(["docker", "stop", "--time", "30", container_id], check=False, capture_output=True, text=True, timeout=45)
        print(json.dumps({"stopped_container_ids": container_ids}, ensure_ascii=True, separators=(",", ":"), sort_keys=True))
        return 0
    except Exception:
        print("distributed training stop failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
PY
