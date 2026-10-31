#!/usr/bin/env bash
# Integration tests: start mock pager + mock share, start smtp_server.py in-process
# via env overrides, drive it with smtplib. No external network.
set -euo pipefail
cd "$(dirname "$0")/.."

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"; [ -n "${SMTP_PID:-}" ] && kill "$SMTP_PID" 2>/dev/null || true; [ -n "${MOCK_PID:-}" ] && kill "$MOCK_PID" 2>/dev/null || true' EXIT

python3 tests/mock_servers.py "$WORK/mock.env" "$WORK/state.json" >/dev/null 2>&1 &
MOCK_PID=$!
sleep 0.5

set -a
# shellcheck disable=SC1090
source "$WORK/mock.env"
set +a
export PAGER_DOMAIN=test.local
export PAGER_SESSION_ID=""
export SMTP_HOST=127.0.0.1
export SMTP_PORT=18025
export MAX_MESSAGE_BYTES=60000
export MAX_ATTACHMENT_BYTES=104857600
export RATE_LIMIT_MESSAGES=20
export RATE_LIMIT_WINDOW_SECONDS=60
export LOG_LEVEL=DEBUG
export PAGER_ALLOW_INSECURE=1

python3 smtp_server.py >"$WORK/smtp.log" 2>&1 &
SMTP_PID=$!
sleep 1

python3 tests/test_smtp.py "$WORK/state.json"
echo "ALL TESTS PASSED"
