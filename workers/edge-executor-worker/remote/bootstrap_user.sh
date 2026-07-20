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
if operation not in {"bootstrap", "rotate_add", "rotate_commit"}:
    raise SystemExit(2)
print(operation)
PY
)"

if [ "${OPERATION}" = "bootstrap" ]; then
    if ! id -u visiox-edge >/dev/null 2>&1; then
        sudo useradd --create-home --shell /bin/bash visiox-edge
    fi
    sudo groupadd -f docker
    sudo usermod -aG docker visiox-edge
elif [ "$(id -un)" != "${EDGE_USER}" ]; then
    exit 2
fi

if [ "$(id -un)" = "${EDGE_USER}" ]; then
    install -d -m 0700 "${SSH_DIR}"
    TEMP_KEYS="$(mktemp "${SSH_DIR}/authorized_keys.XXXXXX")"
else
    sudo install -d -m 0700 -o visiox-edge -g visiox-edge "${SSH_DIR}"
    TEMP_KEYS="$(mktemp)"
fi

python3 - "${DATA_PATH}" "${TEMP_KEYS}" <<'PY'
import json
from pathlib import Path
import sys

data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
operation = data.get("operation")
if operation in {"bootstrap", "rotate_commit"}:
    keys = [data["public_key"]]
elif operation == "rotate_add":
    keys = [data["old_public_key"], data["public_key"]]
else:
    raise SystemExit(2)

if any(not isinstance(key, str) or not key.startswith("ssh-ed25519 ") for key in keys):
    raise SystemExit(2)
Path(sys.argv[2]).write_text("\n".join(dict.fromkeys(keys)) + "\n", encoding="ascii")
PY

chmod 0600 "${TEMP_KEYS}"
if [ "$(id -un)" = "${EDGE_USER}" ]; then
    mv -f "${TEMP_KEYS}" "${AUTHORIZED_KEYS}"
else
    sudo install -m 0600 -o visiox-edge -g visiox-edge "${TEMP_KEYS}" "${AUTHORIZED_KEYS}"
    rm -f "${TEMP_KEYS}"
fi
