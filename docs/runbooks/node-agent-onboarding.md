# Node Agent Onboarding Runbook

Use this runbook to provision an M1 Visiox Node Agent on a supported Linux
edge host. M1 enrolls a real node, inventories it, maintains an authenticated
outbound Gateway connection, and records heartbeats. It does not install a
container runtime or deploy models.

## Security Boundaries

- The control plane owns the Agent CA. `ca.key` is a platform private key and
  must remain on the control-plane host; never copy it to an edge device.
- An enrollment token is a one-time bearer secret. It expires after the
  configured token lifetime (15 minutes by default), is consumed by a
  successful enrollment, and must not be put in source control, logs, tickets,
  or shell history.
- The Agent generates its Ed25519 device private key locally. Enrollment sends
  a CSR and returns a device certificate plus the CA certificate, never a
  private key. The private key, certificate, and durable event spool remain in
  `/var/lib/visiox-agent/agent.db` with mode `0600`.
- Restrict the enrollment-token endpoint to the trusted management network.
  Anyone who can create a token can authorize a new device during its lifetime.

## 1. Configure the Production Control Plane

M1 production requires a durable, self-signed Ed25519 Agent CA. Create it once
on the control-plane host, retain an offline backup under the platform's key
management policy, and mount the directory read-only into `api-service` at
`/var/lib/visiox/pki`.

```bash
sudo install -d -o root -g root -m 0700 /srv/visiox/pki
sudo openssl genpkey -algorithm ED25519 -out /srv/visiox/pki/ca.key
sudo openssl req -x509 -new -key /srv/visiox/pki/ca.key -out /srv/visiox/pki/ca.crt -days 3650 -subj "/CN=Visiox Agent CA" -addext "basicConstraints=critical,CA:TRUE,pathlen:0" -addext "keyUsage=critical,keyCertSign,cRLSign"
sudo chmod 0600 /srv/visiox/pki/ca.key
sudo chmod 0644 /srv/visiox/pki/ca.crt
sudo openssl x509 -in /srv/visiox/pki/ca.crt -noout -subject -issuer -dates -text
```

The production deployment must set these values for `api-service` and bind
mount `/srv/visiox/pki:/var/lib/visiox/pki:ro`:

```dotenv
VISIOX_ENV=production
VISIOX_AGENT_GATEWAY_ENABLED=true
VISIOX_AGENT_AUTO_GENERATE_CA=false
VISIOX_AGENT_CA_CERT_PATH=/var/lib/visiox/pki/ca.crt
VISIOX_AGENT_CA_KEY_PATH=/var/lib/visiox/pki/ca.key
VISIOX_AGENT_PUBLIC_WS_URL=wss://visiox-control.example.internal/agent/v1/connect
```

`VISIOX_AGENT_PUBLIC_WS_URL` is a control-plane setting, not an Agent setting.
It must be a LAN-reachable `wss://` URL at `/agent/v1/connect`, served with a
certificate trusted by every edge device. Do not deploy
`infra/compose/docker-compose.yml` unchanged to production: its
`host.docker.internal` WebSocket address and automatic CA generation are local
Docker Desktop defaults. `host.docker.internal` is only the Docker Desktop
smoke-test default; it is not an edge-device address.

Before enrolling any node, verify the public HTTPS endpoint and its WSS
reverse-proxy route from the edge network. Do not set
`VISIOX_AGENT_ALLOW_INSECURE_LOCAL=true` outside explicit local development.

## 2. Build and Transfer the Correct Binary

On the build host, produce both supported architectures:

```powershell
.\scripts\build-node-agent.ps1
Get-Item dist\visiox-node-agent-linux-amd64, dist\visiox-node-agent-linux-arm64
```

Transfer exactly one binary and the packaged unit file to the target. For
example, use the `arm64` binary for a Jetson and the `amd64` binary for an x86
NVIDIA host:

```bash
scp dist/visiox-node-agent-linux-arm64 ops@edge-jetson-01:/tmp/visiox-node-agent-linux-arm64
scp apps/node-agent/packaging/systemd/visiox-node-agent.service ops@edge-jetson-01:/tmp/visiox-node-agent.service
```

## 3. Create a One-Time Enrollment Token

Run this on a trusted control-plane operator host. It prints only the token's
ID, name, and expiry; transfer the value held in `ENROLLMENT_TOKEN` to the node
through an approved secret channel, then clear it from the operator shell.

```bash
export VISIOX_API_URL='https://visiox-control.example.internal'
TOKEN_RESPONSE="$(curl --fail --silent --show-error --request POST "$VISIOX_API_URL/agent/v1/enrollment-tokens" --header 'Content-Type: application/json' --data '{"name":"edge-jetson-01"}')"
ENROLLMENT_TOKEN="$(printf '%s' "$TOKEN_RESPONSE" | jq -er '.token')"
printf '%s\n' "$TOKEN_RESPONSE" | jq '{id, name, expires_at}'
unset TOKEN_RESPONSE
```

Do not retry enrollment with the same token after success or failure. A token
is one-time and a successfully enrolled token will be rejected on reuse. If it
expires or is unavailable, create a new token instead.

## 4. Install and Start the Agent

On the target node, place the transferred files in the current directory and
run the following commands. The explicit group creation precedes the required
service account command.

```bash
sudo groupadd --system visiox-agent
sudo useradd --system --home-dir /var/lib/visiox-agent --shell /usr/sbin/nologin visiox-agent
sudo install -d -o root -g visiox-agent -m 0750 /etc/visiox-agent
sudo install -d -o visiox-agent -g visiox-agent -m 0750 /var/lib/visiox-agent
sudo install -m 0755 visiox-node-agent-linux-arm64 /usr/local/bin/visiox-node-agent
sudo install -m 0644 visiox-node-agent.service /etc/systemd/system/visiox-node-agent.service
```

If the system account was already installed, confirm it instead of rerunning
the creation commands:

```bash
getent group visiox-agent
id visiox-agent
```

Enter the one-time secret without echoing it, then create the service
environment file. Substitute the real HTTPS platform URL and a unique node
name; do not place a `ws://` URL or `host.docker.internal` in this file.

```bash
read -rsp 'Enrollment token: ' VISIOX_AGENT_ENROLLMENT_TOKEN; echo
sudo install -o root -g visiox-agent -m 0640 /dev/stdin /etc/visiox-agent/agent.env <<EOF
VISIOX_AGENT_PLATFORM_URL=https://visiox-control.example.internal
VISIOX_AGENT_NODE_NAME=edge-jetson-01
VISIOX_AGENT_ENROLLMENT_TOKEN=$VISIOX_AGENT_ENROLLMENT_TOKEN
VISIOX_AGENT_STATE_DIR=/var/lib/visiox-agent
VISIOX_AGENT_VERSION=0.1.0
EOF
unset VISIOX_AGENT_ENROLLMENT_TOKEN
sudo systemctl daemon-reload
sudo systemctl enable --now visiox-node-agent
sudo systemctl status visiox-node-agent --no-pager
sudo journalctl -u visiox-node-agent -n 200 --no-pager
```

The service runs as `visiox-agent`, has a read-only system filesystem, and may
write only below `/var/lib/visiox-agent`. Do not change ownership of
`agent.db`, copy it off the node, or inspect it with a tool that can modify
BoltDB.

## 5. Verify Enrollment and Remove the Bootstrap Secret

After the Agent reports online, find the dynamically created node. A real node
appears in `GET /nodes`; no seed data is required.

```bash
curl --fail --silent --show-error "$VISIOX_API_URL/nodes" | jq -er '.items[] | select(.name == "edge-jetson-01") | {id, name, status, architecture, platform_kind, last_seen_at}'
```

Once this reports `"status": "online"`, remove the consumed bootstrap token
from the node's environment file and restart. The persisted identity is used
on subsequent starts, so a valid enrolled Agent does not need the token again.

```bash
sudo sed -i '/^VISIOX_AGENT_ENROLLMENT_TOKEN=/d' /etc/visiox-agent/agent.env
sudo systemctl restart visiox-node-agent
sudo systemctl status visiox-node-agent --no-pager
sudo journalctl -u visiox-node-agent -n 200 --no-pager
```

## 6. Certificate Rotation and Routine Diagnostics

M1 rotates device certificates through the authenticated Gateway when a
certificate enters its 30-day renewal window. The Agent generates a new CSR
for the same locally retained private key; the control plane replaces the
certificate and rejects the old certificate on reconnect.

M1 rotates device certificates under the same CA but does not provide
zero-downtime CA rotation. Replacing the CA requires a maintenance window and
node re-enrollment. Plan CA replacement before changing CA files: keep the old
CA available through the maintenance work, arrange new one-time tokens, and
coordinate the control-plane node records before deleting any device state.

For routine diagnostics, collect both service status and logs before changing
configuration:

```bash
sudo systemctl status visiox-node-agent --no-pager
sudo journalctl -u visiox-node-agent -n 200 --no-pager
curl --fail --silent --show-error "$VISIOX_API_URL/health"
curl --fail --silent --show-error "$VISIOX_API_URL/nodes" | jq '.items[] | {id, name, status, last_seen_at, certificate_expires_at}'
```

## 7. Drain Before Maintenance or Removal

Draining is a control-plane state change. It preserves the node's device
identity and local state; putting a node into `draining` does not wipe the
device. It also remains draining when a heartbeat arrives.

```bash
export NODE_ID='replace-with-node-id'
curl --fail --silent --show-error --request POST "$VISIOX_API_URL/nodes/$NODE_ID/drain" | jq '{id, name, status, last_seen_at}'
sudo systemctl stop visiox-node-agent
```

Keep `/var/lib/visiox-agent` intact for a temporary maintenance window. Start
the service again after maintenance only when the node is intended to resume
its authenticated connection.

## 8. Uninstall

Uninstall is destructive because `agent.db` contains the device private key,
certificate, CA certificate, and unsent durable events. Stop and disable the
service before removing its state. Do not delete the state directory while the
service is running.

```bash
sudo systemctl disable --now visiox-node-agent
sudo rm -f /etc/systemd/system/visiox-node-agent.service
sudo systemctl daemon-reload
sudo rm -f /usr/local/bin/visiox-node-agent
sudo rm -f /etc/visiox-agent/agent.env
sudo rmdir /etc/visiox-agent
sudo rm -rf /var/lib/visiox-agent
sudo userdel visiox-agent
sudo groupdel visiox-agent
```

Run the drain operation first whenever the control plane is reachable. After
state removal, the device cannot authenticate as its former identity; a future
installation requires the planned re-enrollment procedure and a new one-time
token.
