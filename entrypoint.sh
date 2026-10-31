#!/bin/sh
# Non-root entrypoint: optionally switch to PAGER_UID/PAGER_GID (or the built-in
# 'pager' user) via su-exec. The server itself never runs as root.
# Works on a read-only root filesystem: su-exec accepts numeric ids, so no
# /etc/passwd or /etc/group entry is required.
set -eu

PAGER_UID="${PAGER_UID:-}"
PAGER_GID="${PAGER_GID:-}"

if [ "$(id -u)" = "0" ]; then
    if [ -n "$PAGER_UID" ] && [ -n "$PAGER_GID" ]; then
        # Best effort: give the numeric ids names for readable logs/ps output.
        if [ -w /etc/group ] && [ -w /etc/passwd ]; then
            GROUP_NAME=pagerdyn
            if ! getent group "$PAGER_GID" >/dev/null 2>&1; then
                addgroup -g "$PAGER_GID" "$GROUP_NAME" 2>/dev/null || true
            else
                GROUP_NAME=$(getent group "$PAGER_GID" | cut -d: -f1)
            fi
            if ! getent passwd "$PAGER_UID" >/dev/null 2>&1; then
                adduser -D -u "$PAGER_UID" -G "$GROUP_NAME" "$GROUP_NAME" 2>/dev/null || true
            fi
        fi
        exec su-exec "$PAGER_UID:$PAGER_GID" /usr/local/bin/python3 /app/smtp_server.py
    fi
    if id pager >/dev/null 2>&1; then
        exec su-exec pager:pager /usr/local/bin/python3 /app/smtp_server.py
    fi
fi

exec /usr/local/bin/python3 /app/smtp_server.py
