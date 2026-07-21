# syntax=docker/dockerfile:1.7
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get -o Acquire::Retries=5 update \
    && apt-get -o Acquire::Retries=5 install --yes --no-install-recommends openssh-server python3 \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /run/sshd /etc/ssh/host-keys /var/lib/fake-docker \
    && echo 'root:visiox-smoke' | chpasswd

RUN <<'PY'
cat > /usr/local/bin/fake-docker.py <<'EOF'
#!/usr/bin/env python3
import json
from pathlib import Path
import sys

STATE = Path("/var/lib/fake-docker/state.json")


def load():
    return json.loads(STATE.read_text(encoding="utf-8"))


def save(state):
    temporary = STATE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    temporary.replace(STATE)


def selected(state, args, *, include_stopped):
    labels = []
    for index, value in enumerate(args):
        if value == "--filter" and index + 1 < len(args) and args[index + 1].startswith("label="):
            labels.append(args[index + 1][6:])
    records = []
    for record in state["containers"]:
        if not include_stopped and record["status"] != "running":
            continue
        if all(
            record["labels"].get(item.split("=", 1)[0]) == item.split("=", 1)[1]
            for item in labels
        ):
            records.append(record)
    return records


def main():
    args = sys.argv[1:]
    if not args:
        return 2
    if args[0] == "version":
        print("27.0.0-fixture")
        return 0
    if args[0] == "info":
        template = args[-1]
        if "Runtimes" in template:
            print(json.dumps({"runc": {}, "nvidia": {}}))
        elif "DefaultRuntime" in template:
            print("nvidia")
        else:
            print("fixture")
        return 0

    state = load()
    if args[0] == "ps":
        include_stopped = "-a" in args or "-aq" in args
        for record in selected(state, args, include_stopped=include_stopped):
            print(record["id"])
        return 0
    if args[0] == "inspect":
        if "--format" in args:
            container_id = args[-1]
            record = next(item for item in state["containers"] if item["id"] == container_id)
            template = args[args.index("--format") + 1]
            if "Config.Labels" in template:
                print(json.dumps(record["labels"], sort_keys=True))
            else:
                print(json.dumps(as_inspect(record), sort_keys=True))
            return 0
        ids = [item for item in args[1:] if not item.startswith("-")]
        print(json.dumps([as_inspect(record) for record in state["containers"] if record["id"] in ids]))
        return 0
    if args[0] in {"start", "stop"}:
        container_id = args[-1]
        record = next(item for item in state["containers"] if item["id"] == container_id)
        record["status"] = "running" if args[0] == "start" else "exited"
        save(state)
        print(container_id)
        return 0
    if args[0] == "port":
        print("127.0.0.1:18080")
        return 0
    if args[0] == "rm":
        container_id = args[-1]
        state["containers"] = [item for item in state["containers"] if item["id"] != container_id]
        save(state)
        print(container_id)
        return 0
    if args[0] == "pull":
        print(args[-1])
        return 0
    print("unsupported fake docker command", file=sys.stderr)
    return 64


def as_inspect(record):
    return {
        "Id": record["id"],
        "State": {
            "Status": record["status"],
            "ExitCode": 0,
            "OOMKilled": False,
            "Health": {"Status": "healthy"} if record["status"] == "running" else None,
        },
        "Config": {"Labels": record["labels"]},
    }


if __name__ == "__main__":
    raise SystemExit(main())
EOF
chmod 0755 /usr/local/bin/fake-docker.py
ln -s /usr/local/bin/fake-docker.py /usr/bin/docker
PY

RUN <<'PY'
cat > /usr/bin/nvidia-smi <<'EOF'
#!/bin/sh
if echo "$*" | grep -q -- '--query-gpu'; then
    echo 'Fixture NVIDIA GPU, GPU-fixture-0, 8192, 8.9, 555.42'
else
    echo 'NVIDIA-SMI 555.42 Driver Version: 555.42 CUDA Version: 12.5'
fi
EOF
chmod 0755 /usr/bin/nvidia-smi

cat > /usr/local/bin/fake-health.py <<'EOF'
#!/usr/bin/env python3
from http.server import BaseHTTPRequestHandler, HTTPServer
import json

class Handler(BaseHTTPRequestHandler):
    def _write(self, payload):
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._write({"status": "ok", "task": "detect"})

    def do_POST(self):
        self._write({"task": "detect", "predictions": []})

    def log_message(self, *_args):
        return

HTTPServer(("127.0.0.1", 18080), Handler).serve_forever()
EOF
chmod 0755 /usr/local/bin/fake-health.py
PY

RUN <<'SH'
cat > /usr/local/bin/start-edge-ssh-fixture <<'EOF'
#!/bin/bash
set -euo pipefail

key_root=/etc/ssh/host-keys
groupadd -f docker
install -d -m 0775 -o root -g docker /var/lib/fake-docker
if [ ! -s "${key_root}/ssh_host_ed25519_key" ]; then
    ssh-keygen -q -t ed25519 -N '' -f "${key_root}/ssh_host_ed25519_key"
fi
cat > /etc/ssh/sshd_config <<CONFIG
Port 22
ListenAddress 0.0.0.0
HostKey ${key_root}/ssh_host_ed25519_key
PermitRootLogin yes
PasswordAuthentication yes
PubkeyAuthentication yes
ChallengeResponseAuthentication no
UsePAM no
AllowUsers root visiox-edge
Subsystem sftp internal-sftp
PidFile /run/sshd.pid
CONFIG

if [ ! -s /var/lib/fake-docker/state.json ]; then
    python3 - <<'PY'
import json
from pathlib import Path

common = {
    "com.visiox.managed": "true",
    "com.visiox.remote-execution-id": "exec-smoke",
    "com.visiox.node-id": "smoke-node-a",
    "com.visiox.resource-type": "deployment",
    "com.visiox.resource-id": "instance-smoke",
    "com.visiox.deployment-instance-id": "instance-smoke",
    "com.visiox.image-digest": "registry.local/visiox@sha256:" + "c" * 64,
    "com.visiox.model-checksum": "d" * 64,
    "com.visiox.engine": "engine",
    "com.visiox.engine-digest": "e" * 64,
    "com.visiox.port": "18080",
}
state = {
    "containers": [
        {"id": "a" * 64, "status": "running", "labels": dict(common)},
        {"id": "b" * 64, "status": "exited", "labels": dict(common)},
    ]
}
Path("/var/lib/fake-docker/state.json").write_text(json.dumps(state), encoding="utf-8")
PY
fi

/usr/local/bin/fake-health.py &
exec /usr/sbin/sshd -D -e
EOF
chmod 0755 /usr/local/bin/start-edge-ssh-fixture
SH

EXPOSE 22
ENTRYPOINT ["/usr/local/bin/start-edge-ssh-fixture"]
