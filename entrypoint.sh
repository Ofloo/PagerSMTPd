#!/bin/sh
# Non-root entrypoint: optionally create a user from PAGER_UID/PAGER_GID and
# re-exec as that user via su-exec; otherwise run as the built-in 'pager' user.
set -eu

PAGER_UID="${PAGER_UID:-}"
PAGER_GID="${PAGER_GID:-}"

if [ "$(id -u)" = "0" ]; then
    if [ -n "$PAGER_UID" ] && [ -n "$PAGER_GID" ]; then
        GROUP_NAME=pagerdyn
        if ! getent group "$PAGER_GID" >/dev/null 2>&1; then
            addgroup -g "$PAGER_GID" "$GROUP_NAME"
        else
            GROUP_NAME=$(getent group "$PAGER_GID" | cut -d: -f1)
        fi
        if ! getent passwd "$PAGER_UID" >/dev/null 2>&1; then
            adduser -D -u "$PAGER_UID" -G "$GROUP_NAME" "$GROUP_NAME"
        fi
        exec su-exec "$PAGER_UID:$PAGER_GID" /usr/local/bin/python3 /app/smtp_server.py
    fi
    if id pager >/dev/null 2>&1; then
        exec su-exec pager:pager /usr/local/bin/python3 /app/smtp_server.py
    fi
fi

exec /usr/local/bin/python3 /app/smtp_server.py
