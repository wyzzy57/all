#!/usr/bin/env bash
set -euo pipefail

DATA_PATH="${1:?}"
EDGE_USER="visiox-edge"
EDGE_HOME="/home/${EDGE_USER}"
SSH_DIR="${EDGE_HOME}/.ssh"
AUTHORIZED_KEYS="${SSH_DIR}/authorized_keys"

OPERATION="$(python3 - "${DATA_PATH}" <<'PY'
import json
from pathlib import Path
import sys

data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
operation = data.get("operation")
if operation not in {"bootstrap_add", "rotate_add", "rotate_commit", "remove_key"}:
    raise SystemExit(2)
print(operation)
PY
)"

PRIVILEGE=()
if [ "$(id -u)" -ne 0 ]; then
    PRIVILEGE=(sudo -n)
fi

if [ "${OPERATION}" = "bootstrap_add" ]; then
    if ! id -u visiox-edge >/dev/null 2>&1; then
        "${PRIVILEGE[@]}" useradd --create-home --shell /bin/bash visiox-edge
    fi
    "${PRIVILEGE[@]}" groupadd -f docker
    "${PRIVILEGE[@]}" usermod -aG docker visiox-edge
elif [ "$(id -un)" != "${EDGE_USER}" ]; then
    exit 2
fi

if [ "$(id -un)" = "${EDGE_USER}" ]; then
    install -d -m 0700 "${SSH_DIR}"
    TEMP_KEYS="$(mktemp "${SSH_DIR}/authorized_keys.XXXXXX")"
else
    "${PRIVILEGE[@]}" install -d -m 0700 -o visiox-edge -g visiox-edge "${SSH_DIR}"
    TEMP_KEYS="$(mktemp "${DATA_PATH%/*}/authorized_keys.XXXXXX")"
fi

python3 - "${DATA_PATH}" "${AUTHORIZED_KEYS}" "${TEMP_KEYS}" <<'PY'
import json
from pathlib import Path
import re
import sys

data_path, authorized_path, output_path = map(Path, sys.argv[1:])
data = json.loads(data_path.read_text(encoding="utf-8"))
operation = data.get("operation")
node_id = data.get("node_id")
key_version = data.get("key_version")
if (
    operation not in {"bootstrap_add", "rotate_add", "rotate_commit", "remove_key"}
    or not isinstance(node_id, str)
    or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,63}", node_id) is None
    or not isinstance(key_version, int)
    or isinstance(key_version, bool)
    or not 1 <= key_version <= 1_000_000_000
):
    raise SystemExit(2)

marker = f"visiox-edge:{node_id}:{key_version}"
managed_pattern = re.compile(rf"(?:^|\s)visiox-edge:{re.escape(node_id)}:(\d+)\s*$")
if authorized_path.exists():
    lines = authorized_path.read_text(
        encoding="utf-8", errors="surrogateescape"
    ).splitlines()
else:
    lines = []

if operation == "remove_key":
    lines = [line for line in lines if not line.rstrip().endswith(f" {marker}")]
elif operation == "rotate_commit":
    if not any(line.rstrip().endswith(f" {marker}") for line in lines):
        raise SystemExit(2)
    lines = [
        line
        for line in lines
        if managed_pattern.search(line) is None or line.rstrip().endswith(f" {marker}")
    ]
else:
    public_key = data.get("public_key")
    if (
        not isinstance(public_key, str)
        or not public_key.startswith("ssh-ed25519 ")
        or "\n" in public_key
        or "\r" in public_key
    ):
        raise SystemExit(2)
    managed_line = f"{public_key} {marker}"
    if managed_line not in lines:
        lines.append(managed_line)

output_path.write_text(
    "\n".join(lines) + ("\n" if lines else ""),
    encoding="utf-8",
    errors="surrogateescape",
)
PY

chmod 0600 "${TEMP_KEYS}"
if [ "$(id -un)" = "${EDGE_USER}" ]; then
    mv -f "${TEMP_KEYS}" "${AUTHORIZED_KEYS}"
else
    "${PRIVILEGE[@]}" install -m 0600 -o visiox-edge -g visiox-edge "${TEMP_KEYS}" "${AUTHORIZED_KEYS}"
    rm -f "${TEMP_KEYS}"
fi
