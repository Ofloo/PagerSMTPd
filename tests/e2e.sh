#!/usr/bin/env bash
# End-to-end test against the REAL PagerMCP (PAGER_URL) and REAL share.h4ck.io
# (SHARE_URL), using the session uuid from .pager_session (or PAGER_SESSION_ID).
# Requires a running pager-smtpd (docker compose up -d) on 127.0.0.1:${SMTP_HOST_PORT:-2525}
# and a .env with PAGER_DOMAIN etc. Sends one real mail and verifies the page
# arrives in the mailbox via GET /mailboxes/<uuid>/messages.
set -euo pipefail
cd "$(dirname "$0")/.."

[ -f .env ] && { set -a; source .env; set +a; }
: "${PAGER_DOMAIN:?PAGER_DOMAIN missing (set in .env)}"
: "${PAGER_URL:=https://pager.ofloo.io}"
UUID="${PAGER_SESSION_ID:-$(tr -d ' \n' < .pager_session)}"
HOST="127.0.0.1"
PORT="${SMTP_HOST_PORT:-2525}"

echo "E2E: sending mail to ${UUID}@${PAGER_DOMAIN} via ${HOST}:${PORT}"
python3 - "$UUID" "$PAGER_DOMAIN" "$HOST" "$PORT" <<'PY'
import smtplib, sys, uuid
from email.message import EmailMessage
import email.policy
uuid_, domain, host, port = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
m = EmailMessage()
m["From"] = "e2e@pagersmtpd.local"
m["To"] = f"{uuid_}@{domain}"
m["Subject"] = f"e2e-{uuid.uuid4().hex[:8]}"
m["Message-ID"] = f"<e2e-{uuid.uuid4().hex}@pagersmtpd.local>"
m.set_content("PagerSMPTd end-to-end test body")
m.add_attachment(b"e2e-attachment-payload" * 10, maintype="application",
                 subtype="octet-stream", filename="e2e.bin")
s = smtplib.SMTP(host, port, timeout=20)
s.ehlo("e2e")
code, msg = s.docmd("MAIL", "FROM:<e2e@pagersmtpd.local>")
assert code == 250, (code, msg)
code, msg = s.docmd("RCPT", f"TO:<{uuid_}@{domain}>")
assert code == 250, (code, msg)
code, msg = s.docmd("DATA")
assert code == 354, (code, msg)
s.send(m.as_bytes(policy=email.policy.SMTP) + b"\r\n.\r\n")
code, msg = s.docmd("")
print(f"SMTP result: {code} {msg.decode(errors='replace')}")
assert code == 250, (code, msg)
s.quit()
PY

echo "Verifying page in mailbox ${UUID} ..."
python3 - "$UUID" "$PAGER_URL" <<'PY'
import json, sys, time, urllib.request
uuid_, base = sys.argv[1], sys.argv[2].rstrip("/")
for i in range(15):
    req = urllib.request.Request(f"{base}/mailboxes/{uuid_}/messages")
    req.add_header("Authorization", f"Bearer {uuid_}")
    with urllib.request.urlopen(req, timeout=10) as r:
        msgs = json.load(r)
    recent = [m for m in msgs if m.get("PROJECT") == "smtp"]
    if recent:
        m = recent[-1]
        print("FOUND page:", json.dumps({k: m.get(k) for k in ("JOB_ID", "status", "message", "attachments")}, indent=2)[:600])
        assert m.get("attachments"), "expected attachment URL in payload"
        print("E2E OK")
        sys.exit(0)
    time.sleep(2)
print("E2E FAILED: no page with PROJECT=smtp in mailbox after 30s")
sys.exit(1)
PY
