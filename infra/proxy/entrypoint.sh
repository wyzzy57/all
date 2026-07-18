#!/bin/sh
set -eu

token_file="${VISIOX_MANAGEMENT_PROXY_AUTH_TOKEN_FILE:-/run/secrets/management_proxy_auth_token}"

if [ ! -r "$token_file" ]; then
    printf '%s\n' 'management proxy authentication token is unavailable' >&2
    exit 1
fi

token="$(cat "$token_file")"
carriage_return="$(printf '\r')"
case "$token" in
    *"$carriage_return") token="${token%$carriage_return}" ;;
esac
case "$token" in
    ''|*[!ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-]*)
        printf '%s\n' 'management proxy authentication token is invalid' >&2
        exit 1
        ;;
esac

if [ "${#token}" -lt 32 ] || [ "${#token}" -gt 256 ]; then
    printf '%s\n' 'management proxy authentication token is invalid' >&2
    exit 1
fi

umask 077
export VISIOX_MANAGEMENT_PROXY_AUTH_TOKEN="$token"
envsubst '${VISIOX_MANAGEMENT_PROXY_AUTH_TOKEN}' < /etc/nginx/nginx.conf.template > /etc/nginx/nginx.conf
unset VISIOX_MANAGEMENT_PROXY_AUTH_TOKEN

exec nginx -g 'daemon off;'
