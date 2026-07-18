#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "install.sh must run as root" >&2
    exit 1
fi
if [ "$#" -ne 3 ]; then
    echo "usage: $0 AGENT_ENV_SOURCE BINARY_SOURCE UNIT_SOURCE" >&2
    exit 2
fi

agent_env_source=$1
binary_source=$2
unit_source=$3
installed_server_ca=/etc/visiox-agent/server-ca.crt

server_ca_source=$(sed -n 's/^VISIOX_AGENT_SERVER_CA_FILE=//p' "$agent_env_source" | tail -n 1)
if [ -n "$server_ca_source" ]; then
    case "$server_ca_source" in
        /*) ;;
        *)
            echo "VISIOX_AGENT_SERVER_CA_FILE must be an absolute readable file" >&2
            exit 1
            ;;
    esac
    if [ ! -f "$server_ca_source" ] || [ ! -r "$server_ca_source" ]; then
        echo "VISIOX_AGENT_SERVER_CA_FILE is unavailable" >&2
        exit 1
    fi
fi

if ! getent group visiox-agent >/dev/null 2>&1; then
    groupadd --system visiox-agent
fi
if ! id visiox-agent >/dev/null 2>&1; then
    useradd --system --home-dir /var/lib/visiox-agent --shell /usr/sbin/nologin \
        --gid visiox-agent --no-create-home visiox-agent
elif [ "$(id -g visiox-agent)" != "$(getent group visiox-agent | cut -d: -f3)" ]; then
    usermod --gid visiox-agent visiox-agent
fi

install -d -o visiox-agent -g visiox-agent -m 0750 /var/lib/visiox-agent
install -d -o root -g visiox-agent -m 0750 /etc/visiox-agent
install -o root -g visiox-agent -m 0640 "$agent_env_source" /etc/visiox-agent/agent.env
sed -i '/^VISIOX_AGENT_SERVER_CA_FILE=/d' /etc/visiox-agent/agent.env
if [ -n "$server_ca_source" ]; then
    if [ "$server_ca_source" != "$installed_server_ca" ]; then
        install -o root -g visiox-agent -m 0640 "$server_ca_source" "$installed_server_ca"
    else
        chown root:visiox-agent "$installed_server_ca"
        chmod 0640 "$installed_server_ca"
    fi
    printf '%s\n' "VISIOX_AGENT_SERVER_CA_FILE=$installed_server_ca" >> /etc/visiox-agent/agent.env
else
    rm -f "$installed_server_ca"
fi
install -o root -g root -m 0755 "$binary_source" /usr/local/bin/visiox-node-agent
install -o root -g root -m 0644 "$unit_source" /etc/systemd/system/visiox-node-agent.service

systemctl daemon-reload
systemctl enable --now visiox-node-agent
