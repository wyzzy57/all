#!/bin/sh
set -eu

repo_root=${REPO_ROOT:-/repo}
installer="$repo_root/apps/node-agent/packaging/install.sh"
runbook="$repo_root/docs/runbooks/node-agent-onboarding.md"
mode=${1:-all}

if [ "$(id -u)" -ne 0 ]; then
    printf '%s\n' 'node-agent packaging smoke test must run as root' >&2
    exit 1
fi

test -r "$installer"
test -r "$runbook"

export DEBIAN_FRONTEND=noninteractive
apt-get update >/dev/null
apt-get install --yes --no-install-recommends openssl passwd >/dev/null

workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT HUP INT TERM
testbin="$workdir/bin"
mkdir -p "$testbin"

systemctl_log="$workdir/systemctl.log"
usermod_log="$workdir/usermod.log"
docker_log="$workdir/docker.log"
curl_log="$workdir/curl.log"
real_usermod=$(command -v usermod)
real_mv=$(command -v mv)

cat > "$testbin/systemctl" <<'SYSTEMCTL'
#!/bin/sh
set -eu
printf '%s\n' "$*" >> "$VISIOX_TEST_SYSTEMCTL_LOG"
SYSTEMCTL

cat > "$testbin/usermod" <<'USERMOD'
#!/bin/sh
set -eu
printf '%s\n' "$*" >> "$VISIOX_TEST_USERMOD_LOG"
exec "$VISIOX_TEST_REAL_USERMOD" "$@"
USERMOD

cat > "$testbin/sudo" <<'SUDO'
#!/bin/sh
set -eu
exec "$@"
SUDO

cat > "$testbin/docker" <<'DOCKER'
#!/bin/sh
set -eu
printf '%s\n' "$*" >> "$VISIOX_TEST_DOCKER_LOG"
case " $* " in
    *" up -d --no-deps api-service "*)
        if [ "${VISIOX_TEST_FAIL_STARTUP:-0}" = 1 ] && [ ! -e "$VISIOX_TEST_STATE/startup-failed" ]; then
            : > "$VISIOX_TEST_STATE/startup-failed"
            exit 1
        fi
        ;;
esac
DOCKER

cat > "$testbin/curl" <<'CURL'
#!/bin/sh
set -eu
printf '%s\n' "$*" >> "$VISIOX_TEST_CURL_LOG"
if [ "${VISIOX_TEST_FAIL_HEALTH:-0}" = 1 ]; then
    exit 22
fi
printf '%s\n' '{"status":"ok"}'
CURL

cat > "$testbin/mv" <<'MV'
#!/bin/sh
set -eu
destination=
for argument in "$@"; do
    destination=$argument
done
if [ "${VISIOX_TEST_FAIL_REPLACEMENT:-0}" = 1 ] && \
    [ "$destination" = "$VISIOX_PKI_DIR/ca.crt" ] && \
    [ ! -e "$VISIOX_TEST_STATE/replacement-failed" ]; then
    : > "$VISIOX_TEST_STATE/replacement-failed"
    exit 1
fi
exec "$VISIOX_TEST_REAL_MV" "$@"
MV

chmod 0755 "$testbin/systemctl" "$testbin/usermod" "$testbin/sudo" "$testbin/docker" "$testbin/curl" "$testbin/mv"

export PATH="$testbin:$PATH"
export VISIOX_TEST_SYSTEMCTL_LOG="$systemctl_log"
export VISIOX_TEST_USERMOD_LOG="$usermod_log"
export VISIOX_TEST_REAL_USERMOD="$real_usermod"
export VISIOX_TEST_REAL_MV="$real_mv"
export VISIOX_TEST_DOCKER_LOG="$docker_log"
export VISIOX_TEST_CURL_LOG="$curl_log"

sha256_file() {
    sha256sum "$1" | awk '{print $1}'
}

assert_installed_metadata() {
    test "$(stat -c '%U:%G:%a' /var/lib/visiox-agent)" = 'visiox-agent:visiox-agent:750'
    test "$(stat -c '%U:%G:%a' /etc/visiox-agent)" = 'root:visiox-agent:750'
    test "$(stat -c '%U:%G:%a' /etc/visiox-agent/agent.env)" = 'root:visiox-agent:640'
    test "$(stat -c '%U:%G:%a' /usr/local/bin/visiox-node-agent)" = 'root:root:755'
    test "$(stat -c '%U:%G:%a' /etc/systemd/system/visiox-node-agent.service)" = 'root:root:644'
}

run_installer_tests() {
    input_dir="$workdir/installer-input"
    mkdir -p "$input_dir"
    agent_env="$input_dir/agent.env"
    binary="$input_dir/visiox-node-agent"
    unit="$input_dir/visiox-node-agent.service"

    printf '%s\n' 'VISIOX_AGENT_PLATFORM_URL=https://example.invalid' > "$agent_env"
    printf '%s\n' '#!/bin/sh' 'exit 0' > "$binary"
    chmod 0755 "$binary"
    printf '%s\n' '[Unit]' 'Description=fixture' > "$unit"

    : > "$systemctl_log"
    : > "$usermod_log"
    sh "$installer" "$agent_env" "$binary" "$unit"
    test "$(id -gn visiox-agent)" = 'visiox-agent'
    test ! -s "$usermod_log"
    printf '%s\n' 'fresh-install=passed'

    : > "$usermod_log"
    sh "$installer" "$agent_env" "$binary" "$unit"
    test "$(id -gn visiox-agent)" = 'visiox-agent'
    test ! -s "$usermod_log"
    printf '%s\n' 'second-install-idempotent=passed'

    groupadd --system visiox-agent-wrong-primary
    "$real_usermod" --gid visiox-agent-wrong-primary visiox-agent
    test "$(id -gn visiox-agent)" = 'visiox-agent-wrong-primary'

    : > "$usermod_log"
    sh "$installer" "$agent_env" "$binary" "$unit"
    test "$(id -gn visiox-agent)" = 'visiox-agent'
    test "$(grep -c '^--gid visiox-agent visiox-agent$' "$usermod_log")" = 1
    assert_installed_metadata
    test "$(wc -l < "$systemctl_log" | tr -d ' ')" = 6
    printf '%s\n' 'existing-wrong-primary-group-reconciled=passed'
    printf '%s\n' 'ownership-and-modes=passed'
}

make_ca() {
    ca_dir=$1
    mkdir -p "$ca_dir"
    openssl genpkey -algorithm ED25519 -out "$ca_dir/ca.key" >/dev/null 2>&1
    openssl req -x509 -new -key "$ca_dir/ca.key" -out "$ca_dir/ca.crt" -days 1 \
        -subj '/CN=Visiox Agent CA' \
        -addext 'basicConstraints=critical,CA:TRUE,pathlen:0' \
        -addext 'keyUsage=critical,keyCertSign,cRLSign' >/dev/null 2>&1
}

extract_documented_recovery() {
    recovery_script="$workdir/documented-ca-recovery.sh"
    sed -n '/^sudo bash -seu -- /,/^RECOVER_AGENT_CA$/p' "$runbook" > "$recovery_script"
    test "$(grep -c '^RECOVER_AGENT_CA$' "$recovery_script")" = 1
    test -s "$recovery_script"
}

run_documented_recovery() {
    pki_dir=$1
    offline_backup_dir=$2
    state_dir=$3
    output_file=$4

    : > "$docker_log"
    : > "$curl_log"
    set +e
    VISIOX_PKI_DIR="$pki_dir" \
        VISIOX_OFFLINE_CA_BACKUP_DIR="$offline_backup_dir" \
        VISIOX_API_URL='https://visiox-control.example.invalid' \
        VISIOX_COMPOSE_FILE='/tmp/compose.yml' \
        VISIOX_TEST_STATE="$state_dir" \
        VISIOX_TEST_FAIL_REPLACEMENT="${VISIOX_TEST_FAIL_REPLACEMENT:-0}" \
        VISIOX_TEST_FAIL_STARTUP="${VISIOX_TEST_FAIL_STARTUP:-0}" \
        VISIOX_TEST_FAIL_HEALTH="${VISIOX_TEST_FAIL_HEALTH:-0}" \
        sh "$recovery_script" > "$output_file" 2>&1
    recovery_status=$?
    set -e
}

assert_live_ca_matches() {
    expected_dir=$1
    actual_dir=$2
    test "$(sha256_file "$actual_dir/ca.key")" = "$(sha256_file "$expected_dir/ca.key")"
    test "$(sha256_file "$actual_dir/ca.crt")" = "$(sha256_file "$expected_dir/ca.crt")"
}

run_recovery_tests() {
    extract_documented_recovery
    recovery_root="$workdir/recovery"
    mkdir -p "$recovery_root"

    success_live="$recovery_root/success-live"
    success_offline="$recovery_root/success-offline"
    success_state="$recovery_root/success-state"
    make_ca "$success_live"
    make_ca "$success_offline"
    mkdir -p "$success_state"
    VISIOX_TEST_FAIL_STARTUP=0 VISIOX_TEST_FAIL_HEALTH=0 \
        run_documented_recovery "$success_live" "$success_offline" "$success_state" "$recovery_root/success.out"
    test "$recovery_status" = 0
    assert_live_ca_matches "$success_offline" "$success_live"
    test "$(stat -c '%a' "$success_live/ca.key")" = 600
    test "$(stat -c '%a' "$success_live/ca.crt")" = 644
    grep -F 'stop api-service' "$docker_log" >/dev/null
    grep -F 'up -d --no-deps api-service' "$docker_log" >/dev/null
    grep -F 'logs --tail=100 api-service' "$docker_log" >/dev/null
    printf '%s\n' 'ca-recovery-successful-replacement=passed'

    mismatch_live="$recovery_root/mismatch-live"
    mismatch_offline="$recovery_root/mismatch-offline"
    mismatch_state="$recovery_root/mismatch-state"
    make_ca "$mismatch_live"
    make_ca "$mismatch_offline"
    openssl genpkey -algorithm ED25519 -out "$mismatch_offline/ca.key" >/dev/null 2>&1
    mkdir -p "$mismatch_state"
    old_mismatch_key=$(sha256_file "$mismatch_live/ca.key")
    old_mismatch_cert=$(sha256_file "$mismatch_live/ca.crt")
    VISIOX_TEST_FAIL_STARTUP=0 VISIOX_TEST_FAIL_HEALTH=0 \
        run_documented_recovery "$mismatch_live" "$mismatch_offline" "$mismatch_state" "$recovery_root/mismatch.out"
    test "$recovery_status" -ne 0
    test "$(sha256_file "$mismatch_live/ca.key")" = "$old_mismatch_key"
    test "$(sha256_file "$mismatch_live/ca.crt")" = "$old_mismatch_cert"
    test ! -s "$docker_log"
    if grep -F -f "$mismatch_offline/ca.key" "$recovery_root/mismatch.out" >/dev/null; then
        printf '%s\n' 'private-key-output=unexpected' >&2
        exit 1
    fi
    printf '%s\n' 'invalid-staged-mismatch-preserves-live-ca=passed'

    replacement_live="$recovery_root/replacement-live"
    replacement_offline="$recovery_root/replacement-offline"
    replacement_state="$recovery_root/replacement-state"
    make_ca "$replacement_live"
    make_ca "$replacement_offline"
    mkdir -p "$replacement_state"
    old_replacement_key=$(sha256_file "$replacement_live/ca.key")
    old_replacement_cert=$(sha256_file "$replacement_live/ca.crt")
    VISIOX_TEST_FAIL_REPLACEMENT=1 VISIOX_TEST_FAIL_STARTUP=0 VISIOX_TEST_FAIL_HEALTH=0 \
        run_documented_recovery "$replacement_live" "$replacement_offline" "$replacement_state" "$recovery_root/replacement.out"
    test "$recovery_status" -ne 0
    test "$(sha256_file "$replacement_live/ca.key")" = "$old_replacement_key"
    test "$(sha256_file "$replacement_live/ca.crt")" = "$old_replacement_cert"
    printf '%s\n' 'replacement-failure-restores-live-ca=passed'

    startup_live="$recovery_root/startup-live"
    startup_offline="$recovery_root/startup-offline"
    startup_state="$recovery_root/startup-state"
    make_ca "$startup_live"
    make_ca "$startup_offline"
    mkdir -p "$startup_state"
    old_startup_key=$(sha256_file "$startup_live/ca.key")
    old_startup_cert=$(sha256_file "$startup_live/ca.crt")
    VISIOX_TEST_FAIL_STARTUP=1 VISIOX_TEST_FAIL_HEALTH=0 \
        run_documented_recovery "$startup_live" "$startup_offline" "$startup_state" "$recovery_root/startup.out"
    test "$recovery_status" -ne 0
    test "$(sha256_file "$startup_live/ca.key")" = "$old_startup_key"
    test "$(sha256_file "$startup_live/ca.crt")" = "$old_startup_cert"
    printf '%s\n' 'startup-failure-restores-live-ca=passed'

    health_live="$recovery_root/health-live"
    health_offline="$recovery_root/health-offline"
    health_state="$recovery_root/health-state"
    make_ca "$health_live"
    make_ca "$health_offline"
    mkdir -p "$health_state"
    old_health_key=$(sha256_file "$health_live/ca.key")
    old_health_cert=$(sha256_file "$health_live/ca.crt")
    VISIOX_TEST_FAIL_STARTUP=0 VISIOX_TEST_FAIL_HEALTH=1 \
        run_documented_recovery "$health_live" "$health_offline" "$health_state" "$recovery_root/health.out"
    test "$recovery_status" -ne 0
    test "$(sha256_file "$health_live/ca.key")" = "$old_health_key"
    test "$(sha256_file "$health_live/ca.crt")" = "$old_health_cert"
    printf '%s\n' 'health-failure-restores-live-ca=passed'
}

run_documentation_checks() {
    for required in \
        'POST /agent/v1/enrollment-tokens' \
        'GET /nodes' \
        'POST /nodes/{id}/drain' \
        'POST /agent/v1/enroll' \
        'WSS /agent/v1/connect' \
        'VISIOX_OPERATOR_CERT_FILE' \
        'VISIOX_OPERATOR_KEY_FILE' \
        'Production is not ready'; do
        grep -F "$required" "$runbook" >/dev/null
    done
    grep -F -- '--cert "$VISIOX_OPERATOR_CERT_FILE"' "$runbook" >/dev/null
    grep -F -- '--key "$VISIOX_OPERATOR_KEY_FILE"' "$runbook" >/dev/null
    grep -E '401[|/]403' "$runbook" >/dev/null
    printf '%s\n' 'operator-mtls-documentation=passed'
}

case "$mode" in
    installer)
        run_installer_tests
        ;;
    recovery)
        run_recovery_tests
        ;;
    documentation)
        run_documentation_checks
        ;;
    all)
        run_installer_tests
        run_recovery_tests
        run_documentation_checks
        ;;
    *)
        printf '%s\n' "usage: $0 [installer|recovery|documentation|all]" >&2
        exit 2
        ;;
esac
