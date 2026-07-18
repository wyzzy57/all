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
  or shell history. The Agent alone may automatically repeat its exact durable
  enrollment request after a lost response; a changed request ID, CSR/key,
  node name, or platform binding is rejected.
- The Agent generates its Ed25519 device private key locally. Enrollment sends
  a CSR and returns a device certificate plus the CA certificate, never a
  private key. The private key, certificate, and durable event spool remain in
  `/var/lib/visiox-agent/agent.db` with mode `0600`.
- The returned Agent CA is a **device issuer**: it verifies the Agent's client
  certificate at the control plane. It must never be used as the HTTPS or WSS
  server trust root. By default the Agent trusts operating-system roots for
  both enrollment HTTPS and the Gateway WSS connection. Set
  `VISIOX_AGENT_SERVER_CA_FILE` only when the control plane uses a private LAN
  server CA; the installer stores that public CA at
  `/etc/visiox-agent/server-ca.crt` for the service user.
- M1 does not add application authentication to the management endpoints.
  Production must enforce the operator mTLS boundary below before exposing
  those routes. Anyone who can create a token can authorize a new device during
  its lifetime.

### Mandatory Production Operator mTLS Boundary

Production is not ready until a TLS-terminating reverse proxy enforces and
verifies this policy. Do not rely on a management-network allowlist alone, and
do not forward a missing or untrusted operator certificate to `api-service`.
The production overlay keeps `api-service` off public networks and removes its
host port. Because API dependencies share an internal Docker network, the
application also requires an internal proxy authentication token for every
management route. Nginx reads that token from a Docker secret only after mTLS
succeeds, clears any client-supplied header with the same name, and injects its
own value. A dependency-network peer that calls `api-service` directly is
therefore rejected even though the network must remain bidirectional for API
dependencies. The local Compose `8000` publication in
`infra/compose/docker-compose.yml` is development-only and must not be exposed
in production.
Configure the proxy to require a client certificate trusted by the platform's
operator CA for these exact method/path combinations:

- `POST /agent/v1/enrollment-tokens`
- `GET /nodes`
- `GET /nodes/{id}`
- `GET /resource-pools`
- `POST /nodes/{id}/drain`

Apply the same boundary to any additional operator or admin route added later.
The proxy must keep `POST /agent/v1/enroll` reachable under its existing
one-time enrollment-token protocol and `WSS /agent/v1/connect` reachable under
its existing device-authentication protocol. In particular, do not apply a
path-only mTLS rule to all of `/agent/v1`, because that would block agent
enrollment. Make the proxy return `401` or `403` for missing or untrusted
operator certificates, and make that result happen before proxying to the
application.

From a release-check host on an untrusted or edge network, outside the
proxy-only backend network, use local operator certificate/key file variables.
Run the following block in Bash; its pipefail setting prevents a successful
JSON formatter from masking an authorized curl failure.
Set `VISIOX_DIRECT_BACKEND_URL` to a routable private backend listener address
that would be reachable if isolation were absent; do not use an unresolvable
name. The direct check must fail with curl exit `7` (connection denied) or `28`
(timeout). The commands below never print key material or an enrollment token:

```bash
# BEGIN operator mTLS and backend isolation verification
set -euo pipefail
export VISIOX_API_URL='https://visiox-control.example.internal'
export VISIOX_DIRECT_BACKEND_URL='http://api-service.production.internal:8000'
export VISIOX_OPERATOR_CERT_FILE='/secure/operator-client.crt'
export VISIOX_OPERATOR_KEY_FILE='/secure/operator-client.key'
export VISIOX_MTLS_CHECK_NODE_ID='00000000-0000-0000-0000-000000000000'

assert_direct_backend_denied() {
  if direct_output="$(curl --silent --show-error --connect-timeout 5 --max-time 10 --output /dev/null --write-out '%{http_code}' "$VISIOX_DIRECT_BACKEND_URL/health" 2>&1)"; then
    printf 'Expected direct backend connection denial or timeout, got: %s\n' "$direct_output" >&2
    exit 1
  else
    curl_status=$?
  fi
  case "$curl_status" in
    7|28) printf 'Direct backend access denied with curl exit %s\n' "$curl_status" ;;
    *) printf 'Expected direct backend connection denial or timeout (curl exit 7 or 28), got exit %s\n' "$curl_status" >&2; exit 1 ;;
  esac
}

assert_proxy_denies() {
  method=$1
  url=$2
  status="$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' --request "$method" "$url")"
  case "$status" in
    401|403) printf '%s %s denied with HTTP %s\n' "$method" "$url" "$status" ;;
    *) printf 'Expected proxy mTLS denial (401/403) for %s %s, got HTTP %s\n' "$method" "$url" "$status" >&2; exit 1 ;;
  esac
}

assert_direct_backend_denied
assert_proxy_denies POST "$VISIOX_API_URL/agent/v1/enrollment-tokens"
assert_proxy_denies GET "$VISIOX_API_URL/nodes"
assert_proxy_denies GET "$VISIOX_API_URL/nodes/$VISIOX_MTLS_CHECK_NODE_ID"
assert_proxy_denies GET "$VISIOX_API_URL/resource-pools"
assert_proxy_denies POST "$VISIOX_API_URL/nodes/$VISIOX_MTLS_CHECK_NODE_ID/drain"

if ! curl --fail --silent --show-error \
  --cert "$VISIOX_OPERATOR_CERT_FILE" \
  --key "$VISIOX_OPERATOR_KEY_FILE" \
  "$VISIOX_API_URL/nodes" | jq '{total, items: [.items[] | {id, name, status}]}'; then
  printf '%s\n' 'Authorized operator mTLS verification failed.' >&2
  exit 1
fi
# END operator mTLS and backend isolation verification
```

The direct backend request must fail with a connection denial or timeout, the
five unauthenticated proxy requests must each report `401` or `403`, and the
certificate-authenticated `GET /nodes` request must succeed. Keep the proxy
policy, backend isolation, and this negative/positive verification as a release
prerequisite; M1 is not production-ready without all three.

## 1. Configure the Production Control Plane

M1 production requires a durable, self-signed Ed25519 Agent CA. Create it once
on the control-plane host and retain an offline backup under the platform's key
management policy. The production Compose overlay injects the certificate and
private key into `api-service` as read-only Docker secrets; it does not mount a
writable PKI volume into the API container.

```bash
sudo install -d -o root -g root -m 0700 /srv/visiox/pki
sudo openssl genpkey -algorithm ED25519 -out /srv/visiox/pki/ca.key
sudo openssl req -x509 -new -key /srv/visiox/pki/ca.key -out /srv/visiox/pki/ca.crt -days 3650 -subj "/CN=Visiox Agent CA" -addext "basicConstraints=critical,CA:TRUE,pathlen:0" -addext "keyUsage=critical,keyCertSign,cRLSign"
sudo chmod 0600 /srv/visiox/pki/ca.key
sudo chmod 0644 /srv/visiox/pki/ca.crt
sudo openssl x509 -in /srv/visiox/pki/ca.crt -noout -subject -issuer -dates -text
sudo openssl x509 -in /srv/visiox/pki/ca.crt -noout -fingerprint -sha256
```

When the offline CA backup is created, record the colon-delimited SHA-256
fingerprint of the **public** `ca.crt` in secure inventory alongside the backup
location and recovery authorization. This inventory value is the durable
same-CA identity: do not derive it from an untrusted backup or from the live
host during recovery.

Create a separate, random proxy-to-API authorization token. This is an internal
Docker secret, not an operator credential; never print it or pass it as a
browser/curl header.

```bash
sudo install -d -o root -g root -m 0700 /srv/visiox/secrets
sudo sh -c 'umask 077; openssl rand -hex 32 > /srv/visiox/secrets/management-proxy-auth-token'
sudo chmod 0600 /srv/visiox/secrets/management-proxy-auth-token
```

The production deployment must set these host file paths before rendering the
Compose overlay:

```dotenv
VISIOX_ENV=production
VISIOX_AGENT_GATEWAY_ENABLED=true
VISIOX_AGENT_AUTO_GENERATE_CA=false
VISIOX_AGENT_CA_CERT_HOST_PATH=/srv/visiox/pki/ca.crt
VISIOX_AGENT_CA_KEY_HOST_PATH=/srv/visiox/pki/ca.key
VISIOX_MANAGEMENT_PROXY_AUTH_TOKEN_HOST_PATH=/srv/visiox/secrets/management-proxy-auth-token
VISIOX_AGENT_PUBLIC_WS_URL=wss://visiox-control.example.internal/agent/v1/connect
```

`docker compose ... config` and service startup fail closed when any of these
host files is absent. `api-service` receives the Agent CA files and proxy token
under `/run/secrets`; `management-proxy` receives only the proxy token. Do not
replace this secret wiring with environment literals, a database value, or a
writable named volume.

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

### Deploy the Management-plane mTLS Proxy

Use `infra/compose/docker-compose.production-mtls.yml` with the base Compose
file for every production lifecycle command. The overlay adds the
`management-proxy` TLS listener, removes every inherited host-port publication,
and keeps `api-service` off the public network. Only the proxy's TLS port is
published. The proxy requests client certificates at TLS negotiation, requires
a certificate trusted by the operator CA for the documented management
method/path pairs, keeps enrollment and the Agent WebSocket public, and denies
all other paths by default.

Provision these files from the platform PKI or secret manager on the
control-plane host. `server.crt` must contain the server certificate and any
required intermediate chain. Do not copy any private key, operator certificate,
or CA private key into the repository, an image, a ticket, or a shell log.
The proxy needs only the public operator CA certificate; each operator keeps
their own client certificate and key in approved local secret storage. It also
reads `management-proxy-auth-token` from the Docker secret configured above and
overwrites any incoming `X-Visiox-Management-Proxy-Token` header, so an
operator cannot supply or spoof the internal authorization value.

```bash
sudo install -d -o root -g root -m 0750 /srv/visiox/management-tls
sudo install -d -o root -g root -m 0750 /srv/visiox/operator-ca
sudo install -o root -g root -m 0644 "$VISIOX_ISSUED_SERVER_CERT" /srv/visiox/management-tls/server.crt
sudo install -o root -g root -m 0600 "$VISIOX_ISSUED_SERVER_KEY" /srv/visiox/management-tls/server.key
sudo install -o root -g root -m 0644 "$VISIOX_OPERATOR_CA_CERT" /srv/visiox/operator-ca/operator-ca.crt

export VISIOX_MANAGEMENT_TLS_CERT_PATH='/srv/visiox/management-tls/server.crt'
export VISIOX_MANAGEMENT_TLS_KEY_PATH='/srv/visiox/management-tls/server.key'
export VISIOX_MANAGEMENT_OPERATOR_CA_PATH='/srv/visiox/operator-ca/operator-ca.crt'
export VISIOX_AGENT_CA_CERT_HOST_PATH='/srv/visiox/pki/ca.crt'
export VISIOX_AGENT_CA_KEY_HOST_PATH='/srv/visiox/pki/ca.key'
export VISIOX_MANAGEMENT_PROXY_AUTH_TOKEN_HOST_PATH='/srv/visiox/secrets/management-proxy-auth-token'
export VISIOX_AGENT_PUBLIC_WS_URL='wss://visiox-control.example.internal/agent/v1/connect'
```

Run the following commands from the repository root after setting the Agent CA
variables above. The `config` check must report only `management-proxy` as a
published service and no `api-service` port before anything is started.

```bash
set -euo pipefail
compose_args=(
  -f infra/compose/docker-compose.yml
  -f infra/compose/docker-compose.production-mtls.yml
)

docker compose "${compose_args[@]}" config --format json |
  jq -e '([.services | to_entries[] | select((.value.ports // []) | length > 0) | .key] == ["management-proxy"]) and ((.services["api-service"].ports // []) | length == 0)'
docker compose "${compose_args[@]}" up -d postgres

backup_dir='/srv/visiox/backups'
backup_file="$backup_dir/postgres-$(date -u +%Y%m%dT%H%M%SZ).dump"
sudo install -d -o root -g root -m 0700 "$backup_dir"
umask 077
docker compose "${compose_args[@]}" exec -T postgres \
  sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" --format=custom' > "$backup_file"
test -s "$backup_file"

stop_release() {
  docker compose "${compose_args[@]}" stop api-service || true
}

if ! docker compose "${compose_args[@]}" run --rm api-migrate; then
  stop_release
  printf '%s\n' 'Keep api-service stopped after a failed migration.' >&2
  exit 1
fi
if ! docker compose "${compose_args[@]}" run --rm api-migrate alembic current --check-heads; then
  stop_release
  printf '%s\n' 'Keep api-service stopped after a failed migration.' >&2
  exit 1
fi

docker compose "${compose_args[@]}" up -d --wait
docker compose "${compose_args[@]}" ps
```

`api-migrate` is a one-shot service. `api-service` has a
`service_completed_successfully` dependency on it, so a normal production
`up` cannot silently start the API against an old schema. The explicit
migration run above is intentionally repeated by the final `up`; Alembic
`upgrade head` is idempotent and the dependency remains a guard for future
recreates.

### Migration Failure and Controlled Rollback

Keep `api-service` stopped after a failed migration. Do not issue a blind
`alembic downgrade` in production: data migrations may not be reversible. Fix
the migration and repeat the backup, upgrade, and `--check-heads` commands, or
roll back under an approved maintenance window. Restore the verified backup with the previous release before restarting api-service. The rollback operator
must stop the API, use the previous release's known-compatible image and
migration revision, restore the custom-format archive, verify the intended
revision, and only then start the API:

```bash
docker compose "${compose_args[@]}" stop api-service
cat "$backup_file" | docker compose "${compose_args[@]}" exec -T postgres \
  sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists'
# Switch Compose images/configuration back to the approved previous release.
docker compose "${compose_args[@]}" run --rm api-migrate alembic current --check-heads
docker compose "${compose_args[@]}" up -d api-service
```

After startup, run the operator mTLS and direct-backend isolation verification
block in the Security Boundaries section from an untrusted or edge network.
It is the release check for the five protected routes, public enrollment, and
backend reachability. Confirm the public WSS endpoint with a real Agent before
enrolling production nodes.

Check certificate freshness before a planned rotation without printing private
key material:

```bash
openssl x509 -in "$VISIOX_MANAGEMENT_TLS_CERT_PATH" -noout -checkend 2592000
test "$(openssl pkey -in "$VISIOX_MANAGEMENT_TLS_KEY_PATH" -pubout -outform DER | sha256sum)" = "$(openssl x509 -in "$VISIOX_MANAGEMENT_TLS_CERT_PATH" -pubkey -noout | openssl pkey -pubin -outform DER | sha256sum)"
```

Keep the file names stable during renewal. Because the overlay bind-mounts the
individual certificate files, an atomic host-side file replacement is picked up
by recreating the proxy rather than by relying on an in-container reload:

```bash
docker compose "${compose_args[@]}" up -d --force-recreate --no-deps management-proxy
docker compose "${compose_args[@]}" exec -T management-proxy nginx -t
```

When certificate contents are updated in place, `nginx -t` followed by
`nginx -s reload` is acceptable. For an operator CA rotation, temporarily use
a CA bundle that trusts both the old and new operator issuers, recreate the
proxy, distribute new operator credentials, then remove the retired issuer
only after the migration window closes. Re-run the release check after every
certificate, CA, proxy, or Compose change.

### Recover Lost or Damaged Agent CA Material

Use this procedure only to restore the same CA from the offline backup created
under the platform key-management policy. Replace
`/mnt/offline-backups/visiox-agent-ca` with the mounted or securely attached
offline-backup directory; never print, copy to a ticket, or otherwise expose
`ca.key`. Run these commands on the production Compose control-plane host.

```bash
export VISIOX_PKI_DIR='/srv/visiox/pki'
export VISIOX_OFFLINE_CA_BACKUP_DIR='/mnt/offline-backups/visiox-agent-ca'
export VISIOX_COMPOSE_BASE_FILE='infra/compose/docker-compose.yml'
export VISIOX_COMPOSE_PRODUCTION_OVERLAY='infra/compose/docker-compose.production-mtls.yml'
# Replace with the public-cert SHA-256 fingerprint stored in secure inventory.
export VISIOX_EXPECTED_CA_SHA256_FINGERPRINT='AA:BB:...'

sudo bash -seu -- "$VISIOX_PKI_DIR" "$VISIOX_OFFLINE_CA_BACKUP_DIR" "$VISIOX_COMPOSE_BASE_FILE" "$VISIOX_COMPOSE_PRODUCTION_OVERLAY" "$VISIOX_EXPECTED_CA_SHA256_FINGERPRINT" <<'RECOVER_AGENT_CA'
set -o pipefail
pki_dir=$1
offline_backup_dir=$2
compose_base_file=$3
compose_production_overlay=$4
expected_ca_fingerprint=$5
stage_dir=
replacement_dir=
rollback_dir=
api_stop_attempted=0
live_replaced=0
had_live_key=0
had_live_cert=0

validate_ca_pair() {
  key=$1
  cert=$2
  openssl pkey -in "$key" -pubout -out /dev/null
  openssl x509 -in "$cert" -pubkey -noout | openssl pkey -pubin -out /dev/null
  test "$(openssl x509 -in "$cert" -noout -text | sed -n 's/ *Public Key Algorithm: //p' | head -n 1)" = 'ED25519'
  test "$(openssl x509 -in "$cert" -noout -subject -nameopt RFC2253 | sed 's/^subject=//')" = "$(openssl x509 -in "$cert" -noout -issuer -nameopt RFC2253 | sed 's/^issuer=//')"
  test "$(openssl pkey -in "$key" -pubout -outform DER | sha256sum)" = "$(openssl x509 -in "$cert" -pubkey -noout | openssl pkey -pubin -outform DER | sha256sum)"
  openssl x509 -in "$cert" -noout -text | grep -A1 'Basic Constraints' | grep -F 'CA:TRUE' >/dev/null
  openssl verify -x509_strict -check_ss_sig -CAfile "$cert" "$cert"
}

normalize_ca_fingerprint() {
  value=$(printf '%s' "$1" | tr -d ':')
  case "$value" in
    ''|*[!0-9A-Fa-f]*) return 1 ;;
  esac
  [ "$(printf '%s' "$value" | wc -c)" -eq 64 ] || return 1
  printf '%s\n' "$value" | tr '[:lower:]' '[:upper:]'
}

certificate_sha256_fingerprint() {
  raw_fingerprint=$(openssl x509 -in "$1" -noout -fingerprint -sha256 | awk -F= 'NF == 2 { print $2 }')
  normalize_ca_fingerprint "$raw_fingerprint"
}

compose_api() {
  docker compose -f "$compose_base_file" -f "$compose_production_overlay" "$@"
}

verify_same_ca_identity() {
  if ! staged_ca_fingerprint=$(certificate_sha256_fingerprint "$stage_dir/ca.crt"); then
    printf '%s\n' 'Could not determine the staged CA certificate SHA-256 fingerprint.' >&2
    exit 1
  fi
  if ! expected_ca_fingerprint=$(normalize_ca_fingerprint "$expected_ca_fingerprint"); then
    if [ -r "$pki_dir/ca.crt" ]; then
      printf '%s\n' 'The secure-inventory expected CA fingerprint is missing or malformed; do not continue with same-CA recovery.' >&2
    else
      printf '%s\n' 'No trustworthy expected CA fingerprint and no readable live ca.crt; do not continue with same-CA recovery. Use replacement CA maintenance and node re-enrollment.' >&2
    fi
    exit 1
  fi
  if [ "$staged_ca_fingerprint" != "$expected_ca_fingerprint" ]; then
    printf '%s\n' 'The staged CA certificate does not match the secure-inventory expected CA fingerprint.' >&2
    exit 1
  fi
  if [ -r "$pki_dir/ca.crt" ]; then
    if ! live_ca_fingerprint=$(certificate_sha256_fingerprint "$pki_dir/ca.crt"); then
      printf '%s\n' 'Could not determine the readable live CA certificate SHA-256 fingerprint.' >&2
      exit 1
    fi
    if [ "$staged_ca_fingerprint" != "$live_ca_fingerprint" ]; then
      printf '%s\n' 'The staged CA certificate does not match the readable live ca.crt.' >&2
      exit 1
    fi
  fi
}

restore_live_ca() {
  restore_failed=0
  if [ "$had_live_key" -eq 1 ]; then
    mv -f -- "$rollback_dir/ca.key" "$pki_dir/ca.key" || restore_failed=1
  else
    rm -f -- "$pki_dir/ca.key" || restore_failed=1
  fi
  if [ "$had_live_cert" -eq 1 ]; then
    mv -f -- "$rollback_dir/ca.crt" "$pki_dir/ca.crt" || restore_failed=1
  else
    rm -f -- "$pki_dir/ca.crt" || restore_failed=1
  fi
  return "$restore_failed"
}

recover_cleanup() {
  status=$?
  rollback_failed=0
  trap - EXIT HUP INT TERM
  if [ "$status" -ne 0 ]; then
    if [ "$live_replaced" -eq 1 ]; then
      if [ "$api_stop_attempted" -eq 1 ]; then
        compose_api stop api-service || rollback_failed=1
      fi
      if ! restore_live_ca; then
        rollback_failed=1
        printf '%s\n' 'CA recovery rollback could not restore every prior file.' >&2
      fi
    fi
    if [ "$api_stop_attempted" -eq 1 ]; then
      compose_api up -d --no-deps api-service || rollback_failed=1
    fi
  fi
  [ -z "$stage_dir" ] || rm -rf -- "$stage_dir" || rollback_failed=1
  [ -z "$replacement_dir" ] || rm -rf -- "$replacement_dir" || rollback_failed=1
  [ -z "$rollback_dir" ] || rm -rf -- "$rollback_dir" || rollback_failed=1
  if [ "$rollback_failed" -ne 0 ]; then
    printf '%s\n' 'CA recovery cleanup or rollback failed; keep the control plane in maintenance and investigate.' >&2
    [ "$status" -ne 0 ] || status=1
  fi
  exit "$status"
}

trap recover_cleanup EXIT HUP INT TERM

# Stage and validate the offline backup before touching the live CA or service.
stage_dir=$(mktemp -d /run/visiox-agent-ca-recovery.XXXXXX)
chmod 0700 "$stage_dir"
install -o root -g root -m 0600 "$offline_backup_dir/ca.key" "$stage_dir/ca.key"
install -o root -g root -m 0644 "$offline_backup_dir/ca.crt" "$stage_dir/ca.crt"
validate_ca_pair "$stage_dir/ca.key" "$stage_dir/ca.crt"
verify_same_ca_identity

install -d -o root -g root -m 0700 "$pki_dir"
api_stop_attempted=1
compose_api stop api-service

# Keep rollback files and replacement files on the live filesystem for atomic mv.
rollback_dir=$(mktemp -d "$pki_dir/.ca-rollback.XXXXXX")
replacement_dir=$(mktemp -d "$pki_dir/.ca-replacement.XXXXXX")
chmod 0700 "$rollback_dir" "$replacement_dir"
if [ -e "$pki_dir/ca.key" ]; then
  cp -p -- "$pki_dir/ca.key" "$rollback_dir/ca.key"
  had_live_key=1
fi
if [ -e "$pki_dir/ca.crt" ]; then
  cp -p -- "$pki_dir/ca.crt" "$rollback_dir/ca.crt"
  had_live_cert=1
fi
install -o root -g root -m 0600 "$stage_dir/ca.key" "$replacement_dir/ca.key"
install -o root -g root -m 0644 "$stage_dir/ca.crt" "$replacement_dir/ca.crt"

live_replaced=1
mv -f -- "$replacement_dir/ca.key" "$pki_dir/ca.key"
mv -f -- "$replacement_dir/ca.crt" "$pki_dir/ca.crt"

compose_api up -d --no-deps api-service
compose_api logs --tail=100 api-service
compose_api exec -T api-service python -c "from urllib.request import urlopen; response = urlopen('http://127.0.0.1:8000/health', timeout=10); response.read(); raise SystemExit(response.status != 200)"
live_replaced=0
RECOVER_AGENT_CA
```

The offline backup is first copied into a root-only temporary directory and
validated there. Before it changes the live PKI directory, stops `api-service`, or
replaces files, the procedure requires the secure-inventory fingerprint to
match the staged public certificate and, when readable, requires the staged
certificate to match live `ca.crt` too. A readable live certificate is a
corroborating check, not a replacement for the secure-inventory identity. Only
then does the procedure stop `api-service`, retain any existing live files as
rollback copies, and replace each live file with an atomic `mv` from a
same-filesystem replacement directory. The exit trap restores the previous
files only after replacement begins, and always attempts to restart the service
after a stop attempt if recovery fails before, during, or after replacement. The
validation requires a parseable matching Ed25519 key and certificate, a
self-signed CA certificate valid now, and CA constraints; it does not reveal
private-key contents. If the secure-inventory identity is unavailable, the
staged CA differs from it, or no trustworthy identity exists with no readable
live certificate, do not attempt to preserve existing device identities:
schedule a maintenance window, create and deploy a replacement CA using the
production provisioning steps above, then re-enroll every node with new
one-time tokens.

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
# Only when the control-plane HTTPS/WSS certificate is not rooted in the edge
# host's operating-system trust store:
if [ -n "${VISIOX_PRIVATE_LAN_SERVER_CA_FILE:-}" ]; then
  scp "$VISIOX_PRIVATE_LAN_SERVER_CA_FILE" "$EDGE_HOST:/tmp/visiox-server-ca.crt"
fi
```

## 3. Create a One-Time Enrollment Token

Run this on a trusted control-plane operator host with the mTLS file variables
from the production boundary already set. It prints only the token's ID, name,
and expiry; it does not print the token value.

```bash
export VISIOX_API_URL='https://visiox-control.example.internal'
TOKEN_RESPONSE="$(curl --fail --silent --show-error \
  --cert "$VISIOX_OPERATOR_CERT_FILE" \
  --key "$VISIOX_OPERATOR_KEY_FILE" \
  --request POST "$VISIOX_API_URL/agent/v1/enrollment-tokens" \
  --header 'Content-Type: application/json' \
  --data '{"name":"edge-jetson-01"}')"
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

Do not manually retry enrollment with the same token. Before its first request,
the Agent persists its generated key and stable enrollment request ID. If the
server commits but the response is lost, the restarted Agent automatically
repeats that exact request and receives the original certificate response. Any
different request ID, CSR/key, node name, or platform binding is rejected. If
the Agent has no pending request or enrolled identity, create a new token
instead.

## 4. Install and Start the Agent

On the target node, use the transferred absolute paths. The packaged installer
idempotently creates or reuses the `visiox-agent` group and binds the
`visiox-agent` system user to it as its primary group; do not pre-create the
account separately.

```bash
(
  umask 077
  trap 'rm -f /tmp/visiox-node-agent.env /tmp/visiox-server-ca.crt' EXIT HUP INT TERM
  read -rsp 'Enrollment token: ' VISIOX_AGENT_ENROLLMENT_TOKEN; echo
  cat > /tmp/visiox-node-agent.env <<EOF
VISIOX_AGENT_PLATFORM_URL=https://visiox-control.example.internal
VISIOX_AGENT_NODE_NAME=edge-jetson-01
VISIOX_AGENT_ENROLLMENT_TOKEN=$VISIOX_AGENT_ENROLLMENT_TOKEN
VISIOX_AGENT_STATE_DIR=/var/lib/visiox-agent
VISIOX_AGENT_VERSION=0.1.0
EOF
  if [ -r /tmp/visiox-server-ca.crt ]; then
    printf '%s\n' 'VISIOX_AGENT_SERVER_CA_FILE=/tmp/visiox-server-ca.crt' >> /tmp/visiox-node-agent.env
  fi
  unset VISIOX_AGENT_ENROLLMENT_TOKEN
  sudo sh /tmp/visiox-node-agent-install.sh /tmp/visiox-node-agent.env /tmp/visiox-node-agent /tmp/visiox-node-agent.service
)
sudo systemctl status visiox-node-agent --no-pager
sudo journalctl -u visiox-node-agent -n 200 --no-pager
```

Leave `VISIOX_AGENT_SERVER_CA_FILE` unset when the control-plane server
certificate chains to the edge host's operating-system roots. When a private
LAN server CA is required, the installer copies the supplied public CA to
`/etc/visiox-agent/server-ca.crt`, makes it readable only by the
`visiox-agent` group, and rewrites the installed environment file to that
stable path:

```dotenv
VISIOX_AGENT_SERVER_CA_FILE=/etc/visiox-agent/server-ca.crt
```

The same setting is used for the initial enrollment HTTPS request and every
subsequent Gateway WSS connection. It does not alter device certificate
verification and must not point to the Agent issuer CA returned by enrollment.

The supported installer invocation above is the primary procedure. The
following audited expansion is a review and emergency-manual-install reference
that mirrors install.sh exactly. Do not run it after the supported installer:
use it only instead of that invocation, with the same transferred files still
present at the fixed /tmp paths. The status and journal commands above remain
the required post-install checks.

```bash
# BEGIN audited node-agent installer expansion
if ! getent group visiox-agent >/dev/null 2>&1; then
  sudo groupadd --system visiox-agent
fi
if ! id visiox-agent >/dev/null 2>&1; then
  sudo useradd --system --home-dir /var/lib/visiox-agent --shell /usr/sbin/nologin \
    --gid visiox-agent --no-create-home visiox-agent
elif [ "$(id -g visiox-agent)" != "$(getent group visiox-agent | cut -d: -f3)" ]; then
  sudo usermod --gid visiox-agent visiox-agent
fi
installed_server_ca=/etc/visiox-agent/server-ca.crt
server_ca_source=$(sed -n 's/^VISIOX_AGENT_SERVER_CA_FILE=//p' /tmp/visiox-node-agent.env | tail -n 1)
if [ -n "$server_ca_source" ]; then
  case "$server_ca_source" in
    /*) ;;
    *) printf '%s\n' 'VISIOX_AGENT_SERVER_CA_FILE must be an absolute readable file' >&2; exit 1 ;;
  esac
  if [ ! -f "$server_ca_source" ] || [ ! -r "$server_ca_source" ]; then
    printf '%s\n' 'VISIOX_AGENT_SERVER_CA_FILE is unavailable' >&2
    exit 1
  fi
fi
sudo install -d -o visiox-agent -g visiox-agent -m 0750 /var/lib/visiox-agent
sudo install -d -o root -g visiox-agent -m 0750 /etc/visiox-agent
sudo install -o root -g visiox-agent -m 0640 /tmp/visiox-node-agent.env /etc/visiox-agent/agent.env
sudo sed -i '/^VISIOX_AGENT_SERVER_CA_FILE=/d' /etc/visiox-agent/agent.env
if [ -n "$server_ca_source" ]; then
  if [ "$server_ca_source" != "$installed_server_ca" ]; then
    sudo install -o root -g visiox-agent -m 0640 "$server_ca_source" "$installed_server_ca"
  else
    sudo chown root:visiox-agent "$installed_server_ca"
    sudo chmod 0640 "$installed_server_ca"
  fi
  printf '%s\n' "VISIOX_AGENT_SERVER_CA_FILE=$installed_server_ca" | sudo tee -a /etc/visiox-agent/agent.env >/dev/null
else
  sudo rm -f "$installed_server_ca"
fi
sudo install -o root -g root -m 0755 /tmp/visiox-node-agent /usr/local/bin/visiox-node-agent
sudo install -o root -g root -m 0644 /tmp/visiox-node-agent.service /etc/systemd/system/visiox-node-agent.service
sudo systemctl daemon-reload
sudo systemctl enable visiox-node-agent
sudo systemctl restart visiox-node-agent
# END audited node-agent installer expansion
```

The service runs as `visiox-agent`, reads `/etc/visiox-agent` but has a
read-only system filesystem, and may write only below
`/var/lib/visiox-agent`. Do not change ownership of `agent.db`, copy it off the
node, or inspect it with a tool that can modify BoltDB.

## 5. Verify Enrollment and Remove the Bootstrap Secret

After the Agent reports online, find the dynamically created node. A real node
appears in `GET /nodes`; no seed data is required.

```bash
curl --fail --silent --show-error \
  --cert "$VISIOX_OPERATOR_CERT_FILE" \
  --key "$VISIOX_OPERATOR_KEY_FILE" \
  "$VISIOX_API_URL/nodes" | jq -er '.items[] | select(.name == "edge-jetson-01") | {id, name, status, architecture, platform_kind, last_seen_at}'
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
certificate enters its 30-day renewal window. The Agent generates a CSR for
the same locally retained private key and durably records a renewal request.
The control plane stages a candidate certificate while the old certificate
remains valid. The Agent durably stores the candidate before it acknowledges
readiness; only then does the control plane activate it and reject the old
certificate. If a candidate, acknowledgement, or activation response is lost,
the Agent retains the old and pending certificates across restart and recovers
on reconnect without moving the private key. After a long outage, a still-valid
pending candidate is promoted on reconnect; if both the current and pending
certificates expire, the Agent clears the stale candidate and stops for planned
node re-enrollment rather than retrying indefinitely. Do not edit Agent state
files to force this sequence.

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
docker compose -f infra/compose/docker-compose.yml -f infra/compose/docker-compose.production-mtls.yml \
  exec -T api-service python -c "from urllib.request import urlopen; response = urlopen('http://127.0.0.1:8000/health', timeout=10); response.read(); raise SystemExit(response.status != 200)"
curl --fail --silent --show-error \
  --cert "$VISIOX_OPERATOR_CERT_FILE" \
  --key "$VISIOX_OPERATOR_KEY_FILE" \
  "$VISIOX_API_URL/nodes" | jq '.items[] | {id, name, status, last_seen_at, certificate_expires_at}'
```

## 7. Drain Before Maintenance or Removal

Draining is a control-plane state change. It preserves the node's device
identity and local state; putting a node into `draining` does not wipe the
device. It also remains draining when a heartbeat arrives.

```bash
export NODE_ID='replace-with-node-id'
curl --fail --silent --show-error \
  --cert "$VISIOX_OPERATOR_CERT_FILE" \
  --key "$VISIOX_OPERATOR_KEY_FILE" \
  --request POST "$VISIOX_API_URL/nodes/$NODE_ID/drain" | jq '{id, name, status, last_seen_at}'
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
sudo rm -f /etc/visiox-agent/server-ca.crt
sudo rmdir /etc/visiox-agent
sudo rm -rf /var/lib/visiox-agent
sudo userdel visiox-agent
sudo groupdel visiox-agent
```

Run the drain operation first whenever the control plane is reachable. After
state removal, the device cannot authenticate as its former identity; a future
installation requires the planned re-enrollment procedure and a new one-time
token.
