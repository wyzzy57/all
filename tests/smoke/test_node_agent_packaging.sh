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
apt-get install --yes --no-install-recommends jq openssl passwd >/dev/null

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
real_install=$(command -v install)

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
if [ "${VISIOX_TEST_MTLS_VERIFICATION:-0}" = 1 ]; then
    method=GET
    cert=0
    key=0
    url=
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --request)
                method=$2
                shift 2
                ;;
            --cert)
                cert=1
                shift 2
                ;;
            --key)
                key=1
                shift 2
                ;;
            --output|--write-out|--connect-timeout|--max-time)
                shift 2
                ;;
            --silent|--show-error|--fail)
                shift
                ;;
            *)
                url=$1
                shift
                ;;
        esac
    done
    printf '%s|%s|cert=%s|key=%s\n' "$method" "$url" "$cert" "$key" >> "$VISIOX_TEST_CURL_LOG"
    case "$url" in
        "$VISIOX_DIRECT_BACKEND_URL"/health)
            printf '%s\n' 'curl: (7) Failed to connect to backend' >&2
            exit 7
            ;;
        "$VISIOX_API_URL"/*)
            if [ "$cert" = 1 ] && [ "$key" = 1 ]; then
                if [ "${VISIOX_TEST_FAIL_AUTHORIZED_CURL:-0}" = 1 ]; then
                    exit 22
                fi
                printf '%s\n' '{"total":0,"items":[]}'
            else
                printf '%s' 401
            fi
            exit 0
            ;;
        *)
            printf '%s\n' "unexpected curl URL: $url" >&2
            exit 2
            ;;
    esac
fi
printf '%s\n' "$*" >> "$VISIOX_TEST_CURL_LOG"
if [ "${VISIOX_TEST_FAIL_HEALTH:-0}" = 1 ]; then
    exit 22
fi
printf '%s\n' '{"status":"ok"}'
CURL

cat > "$testbin/install" <<'INSTALL'
#!/bin/sh
set -eu
destination=
for argument in "$@"; do
    destination=$argument
done
case "$destination" in
    "${VISIOX_PKI_DIR:-}"/.ca-replacement.*/ca.crt)
        if [ "${VISIOX_TEST_FAIL_PRE_REPLACEMENT:-0}" = 1 ] && \
            [ ! -e "$VISIOX_TEST_STATE/pre-replacement-failed" ]; then
            : > "$VISIOX_TEST_STATE/pre-replacement-failed"
            exit 1
        fi
        ;;
esac
exec "$VISIOX_TEST_REAL_INSTALL" "$@"
INSTALL

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

chmod 0755 "$testbin/systemctl" "$testbin/usermod" "$testbin/sudo" "$testbin/docker" "$testbin/curl" "$testbin/install" "$testbin/mv"

export PATH="$testbin:$PATH"
export VISIOX_TEST_SYSTEMCTL_LOG="$systemctl_log"
export VISIOX_TEST_USERMOD_LOG="$usermod_log"
export VISIOX_TEST_REAL_USERMOD="$real_usermod"
export VISIOX_TEST_REAL_MV="$real_mv"
export VISIOX_TEST_REAL_INSTALL="$real_install"
export VISIOX_TEST_DOCKER_LOG="$docker_log"
export VISIOX_TEST_CURL_LOG="$curl_log"

sha256_file() {
    sha256sum "$1" | awk '{print $1}'
}

assert_output_excludes_private_key() {
    key_file=$1
    output_file=$2
    if grep -F -f "$key_file" "$output_file" >/dev/null; then
        printf '%s\n' 'private-key-output=unexpected' >&2
        exit 1
    fi
}

assert_api_restart_attempted() {
    expected_attempts=$1
    restart_attempts=$(grep -Fxc 'compose -f /tmp/compose.yml up -d --no-deps api-service' "$docker_log" || true)
    if [ "$restart_attempts" -lt "$expected_attempts" ]; then
        printf '%s\n' 'api-restart-attempted=missing' >&2
        exit 1
    fi
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

extract_documented_installer_expansion() {
    documented_installer_script="$workdir/documented-node-agent-install.sh"
    start_marker='# BEGIN audited node-agent installer expansion'
    end_marker='# END audited node-agent installer expansion'
    start_count=$(grep -Fxc "$start_marker" "$runbook" || true)
    end_count=$(grep -Fxc "$end_marker" "$runbook" || true)
    if [ "$start_count" != 1 ] || [ "$end_count" != 1 ]; then
        printf '%s\n' 'documented-installer-expansion-block=missing' >&2
        exit 1
    fi
    sed -n "/^$start_marker$/,/^$end_marker$/p" "$runbook" | sed '1d;$d' > "$documented_installer_script"
    if [ ! -s "$documented_installer_script" ]; then
        printf '%s\n' 'documented-installer-expansion-block=empty' >&2
        exit 1
    fi
}

run_documented_installer_expansion_test() {
    extract_documented_installer_expansion
    documented_installer_input="$workdir/documented-installer-input"
    mkdir -p "$documented_installer_input"
    printf '%s\n' 'VISIOX_AGENT_PLATFORM_URL=https://example.invalid' > "$documented_installer_input/agent.env"
    printf '%s\n' '#!/bin/sh' 'exit 0' > "$documented_installer_input/visiox-node-agent"
    chmod 0755 "$documented_installer_input/visiox-node-agent"
    printf '%s\n' '[Unit]' 'Description=fixture' > "$documented_installer_input/visiox-node-agent.service"
    cp "$documented_installer_input/agent.env" /tmp/visiox-node-agent.env
    cp "$documented_installer_input/visiox-node-agent" /tmp/visiox-node-agent
    cp "$documented_installer_input/visiox-node-agent.service" /tmp/visiox-node-agent.service

    "$real_usermod" --gid visiox-agent-wrong-primary visiox-agent
    : > "$systemctl_log"
    : > "$usermod_log"
    bash "$documented_installer_script" > "$workdir/documented-installer-expansion.out" 2>&1
    test "$(id -gn visiox-agent)" = 'visiox-agent'
    test "$(grep -c '^--gid visiox-agent visiox-agent$' "$usermod_log")" = 1
    assert_installed_metadata
    grep -Fqx 'daemon-reload' "$systemctl_log"
    grep -Fqx 'enable --now visiox-node-agent' "$systemctl_log"
    rm -f /tmp/visiox-node-agent.env /tmp/visiox-node-agent /tmp/visiox-node-agent.service
    printf '%s\n' 'documented-installer-expansion=passed'
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

copy_ca() {
    source_dir=$1
    destination_dir=$2
    mkdir -p "$destination_dir"
    cp "$source_dir/ca.key" "$destination_dir/ca.key"
    cp "$source_dir/ca.crt" "$destination_dir/ca.crt"
}

ca_certificate_sha256_fingerprint() {
    openssl x509 -in "$1" -noout -fingerprint -sha256 | awk -F= 'NF == 2 { print $2 }'
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
    expected_ca_fingerprint=$5

    : > "$docker_log"
    : > "$curl_log"
    set +e
    VISIOX_PKI_DIR="$pki_dir" \
        VISIOX_OFFLINE_CA_BACKUP_DIR="$offline_backup_dir" \
        VISIOX_API_URL='https://visiox-control.example.invalid' \
        VISIOX_COMPOSE_FILE='/tmp/compose.yml' \
        VISIOX_EXPECTED_CA_SHA256_FINGERPRINT="$expected_ca_fingerprint" \
        VISIOX_TEST_STATE="$state_dir" \
        VISIOX_TEST_FAIL_PRE_REPLACEMENT="${VISIOX_TEST_FAIL_PRE_REPLACEMENT:-0}" \
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
    copy_ca "$success_live" "$success_offline"
    mkdir -p "$success_state"
    success_expected=$(ca_certificate_sha256_fingerprint "$success_live/ca.crt")
    VISIOX_TEST_FAIL_STARTUP=0 VISIOX_TEST_FAIL_HEALTH=0 \
        run_documented_recovery "$success_live" "$success_offline" "$success_state" "$recovery_root/success.out" "$success_expected"
    test "$recovery_status" = 0
    assert_live_ca_matches "$success_offline" "$success_live"
    test "$(stat -c '%a' "$success_live/ca.key")" = 600
    test "$(stat -c '%a' "$success_live/ca.crt")" = 644
    assert_output_excludes_private_key "$success_offline/ca.key" "$recovery_root/success.out"
    grep -F 'stop api-service' "$docker_log" >/dev/null
    grep -F 'up -d --no-deps api-service' "$docker_log" >/dev/null
    grep -F 'logs --tail=100 api-service' "$docker_log" >/dev/null
    printf '%s\n' 'same-ca-recovery-successful-replacement=passed'

    wrong_valid_live="$recovery_root/wrong-valid-live"
    wrong_valid_offline="$recovery_root/wrong-valid-offline"
    wrong_valid_state="$recovery_root/wrong-valid-state"
    make_ca "$wrong_valid_live"
    make_ca "$wrong_valid_offline"
    mkdir -p "$wrong_valid_state"
    old_wrong_valid_key=$(sha256_file "$wrong_valid_live/ca.key")
    old_wrong_valid_cert=$(sha256_file "$wrong_valid_live/ca.crt")
    wrong_valid_expected=$(ca_certificate_sha256_fingerprint "$wrong_valid_live/ca.crt")
    VISIOX_TEST_FAIL_STARTUP=0 VISIOX_TEST_FAIL_HEALTH=0 \
        run_documented_recovery "$wrong_valid_live" "$wrong_valid_offline" "$wrong_valid_state" "$recovery_root/wrong-valid.out" "$wrong_valid_expected"
    test "$recovery_status" -ne 0
    test "$(sha256_file "$wrong_valid_live/ca.key")" = "$old_wrong_valid_key"
    test "$(sha256_file "$wrong_valid_live/ca.crt")" = "$old_wrong_valid_cert"
    test ! -s "$docker_log"
    assert_output_excludes_private_key "$wrong_valid_offline/ca.key" "$recovery_root/wrong-valid.out"
    printf '%s\n' 'wrong-valid-ca-rejected-before-api-stop=passed'

    live_disagreement_live="$recovery_root/live-disagreement-live"
    live_disagreement_offline="$recovery_root/live-disagreement-offline"
    live_disagreement_state="$recovery_root/live-disagreement-state"
    make_ca "$live_disagreement_live"
    make_ca "$live_disagreement_offline"
    mkdir -p "$live_disagreement_state"
    old_live_disagreement_key=$(sha256_file "$live_disagreement_live/ca.key")
    old_live_disagreement_cert=$(sha256_file "$live_disagreement_live/ca.crt")
    live_disagreement_expected=$(ca_certificate_sha256_fingerprint "$live_disagreement_offline/ca.crt")
    VISIOX_TEST_FAIL_STARTUP=0 VISIOX_TEST_FAIL_HEALTH=0 \
        run_documented_recovery "$live_disagreement_live" "$live_disagreement_offline" "$live_disagreement_state" "$recovery_root/live-disagreement.out" "$live_disagreement_expected"
    test "$recovery_status" -ne 0
    test "$(sha256_file "$live_disagreement_live/ca.key")" = "$old_live_disagreement_key"
    test "$(sha256_file "$live_disagreement_live/ca.crt")" = "$old_live_disagreement_cert"
    test ! -s "$docker_log"
    assert_output_excludes_private_key "$live_disagreement_offline/ca.key" "$recovery_root/live-disagreement.out"
    printf '%s\n' 'readable-live-ca-disagreement-rejected-before-api-stop=passed'

    missing_identity_live="$recovery_root/missing-identity-live"
    missing_identity_offline="$recovery_root/missing-identity-offline"
    missing_identity_state="$recovery_root/missing-identity-state"
    make_ca "$missing_identity_offline"
    mkdir -p "$missing_identity_state"
    VISIOX_TEST_FAIL_STARTUP=0 VISIOX_TEST_FAIL_HEALTH=0 \
        run_documented_recovery "$missing_identity_live" "$missing_identity_offline" "$missing_identity_state" "$recovery_root/missing-identity.out" ''
    test "$recovery_status" -ne 0
    test ! -e "$missing_identity_live"
    test ! -s "$docker_log"
    assert_output_excludes_private_key "$missing_identity_offline/ca.key" "$recovery_root/missing-identity.out"
    printf '%s\n' 'missing-ca-identity-aborts-before-api-stop=passed'

    mismatch_live="$recovery_root/mismatch-live"
    mismatch_offline="$recovery_root/mismatch-offline"
    mismatch_state="$recovery_root/mismatch-state"
    make_ca "$mismatch_live"
    make_ca "$mismatch_offline"
    openssl genpkey -algorithm ED25519 -out "$mismatch_offline/ca.key" >/dev/null 2>&1
    mkdir -p "$mismatch_state"
    old_mismatch_key=$(sha256_file "$mismatch_live/ca.key")
    old_mismatch_cert=$(sha256_file "$mismatch_live/ca.crt")
    mismatch_expected=$(ca_certificate_sha256_fingerprint "$mismatch_live/ca.crt")
    VISIOX_TEST_FAIL_STARTUP=0 VISIOX_TEST_FAIL_HEALTH=0 \
        run_documented_recovery "$mismatch_live" "$mismatch_offline" "$mismatch_state" "$recovery_root/mismatch.out" "$mismatch_expected"
    test "$recovery_status" -ne 0
    test "$(sha256_file "$mismatch_live/ca.key")" = "$old_mismatch_key"
    test "$(sha256_file "$mismatch_live/ca.crt")" = "$old_mismatch_cert"
    test ! -s "$docker_log"
    assert_output_excludes_private_key "$mismatch_offline/ca.key" "$recovery_root/mismatch.out"
    printf '%s\n' 'invalid-staged-mismatch-preserves-live-ca=passed'

    pre_replacement_live="$recovery_root/pre-replacement-live"
    pre_replacement_offline="$recovery_root/pre-replacement-offline"
    pre_replacement_state="$recovery_root/pre-replacement-state"
    make_ca "$pre_replacement_live"
    copy_ca "$pre_replacement_live" "$pre_replacement_offline"
    mkdir -p "$pre_replacement_state"
    old_pre_replacement_key=$(sha256_file "$pre_replacement_live/ca.key")
    old_pre_replacement_cert=$(sha256_file "$pre_replacement_live/ca.crt")
    pre_replacement_expected=$(ca_certificate_sha256_fingerprint "$pre_replacement_live/ca.crt")
    VISIOX_TEST_FAIL_PRE_REPLACEMENT=1 VISIOX_TEST_FAIL_REPLACEMENT=0 VISIOX_TEST_FAIL_STARTUP=0 VISIOX_TEST_FAIL_HEALTH=0 \
        run_documented_recovery "$pre_replacement_live" "$pre_replacement_offline" "$pre_replacement_state" "$recovery_root/pre-replacement.out" "$pre_replacement_expected"
    test "$recovery_status" -ne 0
    test "$(sha256_file "$pre_replacement_live/ca.key")" = "$old_pre_replacement_key"
    test "$(sha256_file "$pre_replacement_live/ca.crt")" = "$old_pre_replacement_cert"
    assert_api_restart_attempted 1
    printf '%s\n' 'pre-replacement-failure-preserves-live-ca-and-restarts-api=passed'

    replacement_live="$recovery_root/replacement-live"
    replacement_offline="$recovery_root/replacement-offline"
    replacement_state="$recovery_root/replacement-state"
    make_ca "$replacement_live"
    copy_ca "$replacement_live" "$replacement_offline"
    mkdir -p "$replacement_state"
    old_replacement_key=$(sha256_file "$replacement_live/ca.key")
    old_replacement_cert=$(sha256_file "$replacement_live/ca.crt")
    replacement_expected=$(ca_certificate_sha256_fingerprint "$replacement_live/ca.crt")
    VISIOX_TEST_FAIL_REPLACEMENT=1 VISIOX_TEST_FAIL_STARTUP=0 VISIOX_TEST_FAIL_HEALTH=0 \
        run_documented_recovery "$replacement_live" "$replacement_offline" "$replacement_state" "$recovery_root/replacement.out" "$replacement_expected"
    test "$recovery_status" -ne 0
    test "$(sha256_file "$replacement_live/ca.key")" = "$old_replacement_key"
    test "$(sha256_file "$replacement_live/ca.crt")" = "$old_replacement_cert"
    assert_api_restart_attempted 1
    assert_output_excludes_private_key "$replacement_offline/ca.key" "$recovery_root/replacement.out"
    printf '%s\n' 'replacement-failure-restores-live-ca=passed'

    startup_live="$recovery_root/startup-live"
    startup_offline="$recovery_root/startup-offline"
    startup_state="$recovery_root/startup-state"
    make_ca "$startup_live"
    copy_ca "$startup_live" "$startup_offline"
    mkdir -p "$startup_state"
    old_startup_key=$(sha256_file "$startup_live/ca.key")
    old_startup_cert=$(sha256_file "$startup_live/ca.crt")
    startup_expected=$(ca_certificate_sha256_fingerprint "$startup_live/ca.crt")
    VISIOX_TEST_FAIL_STARTUP=1 VISIOX_TEST_FAIL_HEALTH=0 \
        run_documented_recovery "$startup_live" "$startup_offline" "$startup_state" "$recovery_root/startup.out" "$startup_expected"
    test "$recovery_status" -ne 0
    test "$(sha256_file "$startup_live/ca.key")" = "$old_startup_key"
    test "$(sha256_file "$startup_live/ca.crt")" = "$old_startup_cert"
    assert_api_restart_attempted 2
    assert_output_excludes_private_key "$startup_offline/ca.key" "$recovery_root/startup.out"
    printf '%s\n' 'startup-failure-restores-live-ca=passed'

    health_live="$recovery_root/health-live"
    health_offline="$recovery_root/health-offline"
    health_state="$recovery_root/health-state"
    make_ca "$health_live"
    copy_ca "$health_live" "$health_offline"
    mkdir -p "$health_state"
    old_health_key=$(sha256_file "$health_live/ca.key")
    old_health_cert=$(sha256_file "$health_live/ca.crt")
    health_expected=$(ca_certificate_sha256_fingerprint "$health_live/ca.crt")
    VISIOX_TEST_FAIL_STARTUP=0 VISIOX_TEST_FAIL_HEALTH=1 \
        run_documented_recovery "$health_live" "$health_offline" "$health_state" "$recovery_root/health.out" "$health_expected"
    test "$recovery_status" -ne 0
    test "$(sha256_file "$health_live/ca.key")" = "$old_health_key"
    test "$(sha256_file "$health_live/ca.crt")" = "$old_health_cert"
    assert_api_restart_attempted 2
    assert_output_excludes_private_key "$health_offline/ca.key" "$recovery_root/health.out"
    printf '%s\n' 'health-failure-restores-live-ca=passed'
}

extract_documented_mtls_verification() {
    mtls_script="$workdir/documented-mtls-verification.sh"
    start_marker='# BEGIN operator mTLS and backend isolation verification'
    end_marker='# END operator mTLS and backend isolation verification'
    start_count=$(grep -Fxc "$start_marker" "$runbook" || true)
    end_count=$(grep -Fxc "$end_marker" "$runbook" || true)
    if [ "$start_count" != 1 ] || [ "$end_count" != 1 ]; then
        printf '%s\n' 'documented-mtls-verification-block=missing' >&2
        exit 1
    fi
    sed -n "/^$start_marker$/,/^$end_marker$/p" "$runbook" | sed '1d;$d' > "$mtls_script"
    if [ ! -s "$mtls_script" ]; then
        printf '%s\n' 'documented-mtls-verification-block=empty' >&2
        exit 1
    fi
}

assert_mtls_curl_event() {
    if ! grep -Fqx "$1" "$curl_log"; then
        printf '%s\n' 'documented-mtls-curl-event=missing' >&2
        exit 1
    fi
}

run_documentation_checks() {
    extract_documented_mtls_verification
    : > "$curl_log"
    set +e
    VISIOX_TEST_MTLS_VERIFICATION=1 VISIOX_TEST_FAIL_AUTHORIZED_CURL=0 \
        bash "$mtls_script" > "$workdir/mtls-verification.out" 2>&1
    mtls_status=$?
    set -e
    if [ "$mtls_status" -ne 0 ]; then
        printf '%s\n' 'documented-mtls-verification=failed' >&2
        exit 1
    fi
    assert_mtls_curl_event 'POST|https://visiox-control.example.internal/agent/v1/enrollment-tokens|cert=0|key=0'
    assert_mtls_curl_event 'GET|https://visiox-control.example.internal/nodes|cert=0|key=0'
    assert_mtls_curl_event 'POST|https://visiox-control.example.internal/nodes/00000000-0000-0000-0000-000000000000/drain|cert=0|key=0'
    assert_mtls_curl_event 'GET|http://api-service.production.internal:8000/health|cert=0|key=0'
    assert_mtls_curl_event 'GET|https://visiox-control.example.internal/nodes|cert=1|key=1'

    # jq returns success when it receives no input, so this exercises pipefail.
    printf '' | jq '{total, items: [.items[] | {id, name, status}]}' >/dev/null
    : > "$curl_log"
    set +e
    VISIOX_TEST_MTLS_VERIFICATION=1 VISIOX_TEST_FAIL_AUTHORIZED_CURL=1 \
        bash "$mtls_script" > "$workdir/mtls-authorized-curl-failure.out" 2>&1
    mtls_authorized_curl_failure_status=$?
    set -e
    test "$mtls_authorized_curl_failure_status" -ne 0
    assert_mtls_curl_event 'GET|https://visiox-control.example.internal/nodes|cert=1|key=1'
    printf '%s\n' 'operator-mtls-authorized-curl-failure-is-not-masked=passed'
    printf '%s\n' 'operator-mtls-documentation=passed'
}

case "$mode" in
    installer)
        run_installer_tests
        run_documented_installer_expansion_test
        ;;
    recovery)
        run_recovery_tests
        ;;
    documentation)
        run_documentation_checks
        ;;
    all)
        run_installer_tests
        run_documented_installer_expansion_test
        run_recovery_tests
        run_documentation_checks
        ;;
    *)
        printf '%s\n' "usage: $0 [installer|recovery|documentation|all]" >&2
        exit 2
        ;;
esac
