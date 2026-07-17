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

### Recover Lost or Damaged Agent CA Material

Use this procedure only to restore the same CA from the offline backup created
under the platform key-management policy. Replace
`/mnt/offline-backups/visiox-agent-ca` with the mounted or securely attached
offline-backup directory; never print, copy to a ticket, or otherwise expose
`ca.key`. Run these commands on the production Compose control-plane host.

```bash
export VISIOX_PKI_DIR='/srv/visiox/pki'
export VISIOX_OFFLINE_CA_BACKUP_DIR='/mnt/offline-backups/visiox-agent-ca'
export VISIOX_API_URL='https://visiox-control.example.internal'

docker compose -f infra/compose/docker-compose.yml stop api-service
sudo install -d -o root -g root -m 0700 "$VISIOX_PKI_DIR"
sudo install -o root -g root -m 0600 "$VISIOX_OFFLINE_CA_BACKUP_DIR/ca.key" "$VISIOX_PKI_DIR/ca.key"
sudo install -o root -g root -m 0644 "$VISIOX_OFFLINE_CA_BACKUP_DIR/ca.crt" "$VISIOX_PKI_DIR/ca.crt"
sudo chmod 0600 "$VISIOX_PKI_DIR/ca.key"
sudo chmod 0644 "$VISIOX_PKI_DIR/ca.crt"

# Validate the pair without displaying private-key material.
sudo bash -seu -- "$VISIOX_PKI_DIR/ca.key" "$VISIOX_PKI_DIR/ca.crt" <<'VALIDATE_AGENT_CA'
set -o pipefail
key="$1"
cert="$2"
openssl pkey -in "$key" -pubout -out /dev/null
openssl x509 -in "$cert" -pubkey -noout | openssl pkey -pubin -out /dev/null
test "$(openssl x509 -in "$cert" -noout -text | sed -n 's/ *Public Key Algorithm: //p' | head -n 1)" = 'ED25519'
test "$(openssl x509 -in "$cert" -noout -subject -nameopt RFC2253 | sed 's/^subject=//')" = "$(openssl x509 -in "$cert" -noout -issuer -nameopt RFC2253 | sed 's/^issuer=//')"
test "$(openssl pkey -in "$key" -pubout -outform DER | sha256sum)" = "$(openssl x509 -in "$cert" -pubkey -noout | openssl pkey -pubin -outform DER | sha256sum)"
openssl verify -x509_strict -check_ss_sig -CAfile "$cert" "$cert"
VALIDATE_AGENT_CA

docker compose -f infra/compose/docker-compose.yml up -d --no-deps api-service
docker compose -f infra/compose/docker-compose.yml logs --tail=100 api-service
curl --fail --silent --show-error "$VISIOX_API_URL/health"
```

The `stop`/`up` sequence restarts `api-service` after the restored read-only
mount is validated. The validation requires a parseable matching Ed25519 key
and certificate, a self-signed CA certificate valid now, and CA extensions;
it does not reveal private-key contents. If there is no valid backup, do not
attempt to preserve the existing device identities: schedule a maintenance
window, create and deploy a replacement CA using the production provisioning
steps above, then re-enroll every node with new one-time tokens.

## 2. Build and Transfer the Correct Binary

On the build host, produce both supported architectures:

```powershell
.\scripts\build-node-agent.ps1
Get-Item dist\visiox-node-agent-linux-amd64, dist\visiox-node-agent-linux-arm64
```

On the build host, select the binary from the target's reported architecture,
then transfer the selected binary, installer, and unit to the fixed paths used
in the installation step. `x86_64`/`amd64` selects the `amd64` binary; a
Jetson reports `aarch64`/`arm64` and selects the `arm64` binary.

```bash
EDGE_HOST='ops@edge-01'
VISIOX_AGENT_ARCH="$(ssh "$EDGE_HOST" 'case "$(uname -m)" in
  x86_64|amd64) printf "%s\n" amd64 ;;
  aarch64|arm64) printf "%s\n" arm64 ;;
  *) printf "Unsupported edge architecture: %s\n" "$(uname -m)" >&2; exit 1 ;;
esac')"

case "$VISIOX_AGENT_ARCH" in
  amd64|arm64) ;;
  *) printf 'Could not determine a supported edge architecture.\n' >&2; exit 1 ;;
esac

scp "dist/visiox-node-agent-linux-$VISIOX_AGENT_ARCH" "$EDGE_HOST:/tmp/visiox-node-agent"
scp apps/node-agent/packaging/install.sh "$EDGE_HOST:/tmp/visiox-node-agent-install.sh"
scp apps/node-agent/packaging/systemd/visiox-node-agent.service "$EDGE_HOST:/tmp/visiox-node-agent.service"
```

## 3. Create a One-Time Enrollment Token

Run this on a trusted control-plane operator host. It prints only the token's
ID, name, and expiry; it does not print the token value.

```bash
export VISIOX_API_URL='https://visiox-control.example.internal'
TOKEN_RESPONSE="$(curl --fail --silent --show-error --request POST "$VISIOX_API_URL/agent/v1/enrollment-tokens" --header 'Content-Type: application/json' --data '{"name":"edge-jetson-01"}')"
ENROLLMENT_TOKEN="$(printf '%s' "$TOKEN_RESPONSE" | jq -er '.token')"
printf '%s\n' "$TOKEN_RESPONSE" | jq '{id, name, expires_at}'
```

Securely transfer the value held in `ENROLLMENT_TOKEN` to the edge operator
through an approved secret channel, then enter it at the non-echoing prompt in
step 4. Do not paste the token into shell history, source control, tickets, or
logs. After that handoff is complete, run this separate cleanup command on the
same operator host:

```bash
unset ENROLLMENT_TOKEN
unset TOKEN_RESPONSE
```

Do not retry enrollment with the same token after success or failure. A token
is one-time and a successfully enrolled token will be rejected on reuse. If it
expires or is unavailable, create a new token instead.

## 4. Install and Start the Agent

On the target node, use the transferred absolute paths. The packaged installer
idempotently creates or reuses the `visiox-agent` group and binds the
`visiox-agent` system user to it as its primary group; do not pre-create the
account separately.

```bash
(
  umask 077
  trap 'rm -f /tmp/visiox-node-agent.env' EXIT HUP INT TERM
  read -rsp 'Enrollment token: ' VISIOX_AGENT_ENROLLMENT_TOKEN; echo
  cat > /tmp/visiox-node-agent.env <<EOF
VISIOX_AGENT_PLATFORM_URL=https://visiox-control.example.internal
VISIOX_AGENT_NODE_NAME=edge-jetson-01
VISIOX_AGENT_ENROLLMENT_TOKEN=$VISIOX_AGENT_ENROLLMENT_TOKEN
VISIOX_AGENT_STATE_DIR=/var/lib/visiox-agent
VISIOX_AGENT_VERSION=0.1.0
EOF
  unset VISIOX_AGENT_ENROLLMENT_TOKEN
  sudo sh /tmp/visiox-node-agent-install.sh /tmp/visiox-node-agent.env /tmp/visiox-node-agent /tmp/visiox-node-agent.service
)
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
