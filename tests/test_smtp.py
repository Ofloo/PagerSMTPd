#!/usr/bin/env python3
"""SMTP integration tests against the running server + mock backends."""

import email.policy
import json
import os
import smtplib
import subprocess
import sys
import time
import uuid
from email.message import EmailMessage

HOST, PORT = "127.0.0.1", 18025
STATE_FILE = sys.argv[1]
DOMAIN = "test.local"
VALID_UUID = str(uuid.uuid4())
SESSION_UUID = str(uuid.uuid4())

failures = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}: {name}" + (f" ({detail})" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def send(rcpt, data: bytes, expect_smtp_code=None):
    s = smtplib.SMTP(HOST, PORT, timeout=10)
    s.ehlo("tester")
    try:
        code, msg = s.docmd("MAIL", "FROM:<sender@example.org>")
        assert code == 250, (code, msg)
        code, msg = s.docmd("RCPT", f"TO:<{rcpt}>")
        if (expect_smtp_code is not None and expect_smtp_code >= 500) or code >= 500:
            return code, msg
        assert code == 250, (code, msg)
        code, msg = s.docmd("DATA")
        assert code == 354, (code, msg)
        s.send(data + b"\r\n.\r\n")
        code, msg = s.docmd("")
        return code, msg
    finally:
        try:
            s.quit()
        except Exception:
            pass


def state():
    for _ in range(50):
        try:
            with open(STATE_FILE) as fh:
                return json.load(fh)
        except Exception:
            time.sleep(0.1)
    raise RuntimeError("state file not readable")


def build_mail(subject="hello", body="test body", attach=None, attach_size=None):
    m = EmailMessage()
    m["From"] = "sender@example.org"
    m["To"] = f"{VALID_UUID}@{DOMAIN}"
    m["Subject"] = subject
    m["Message-ID"] = f"<{uuid.uuid4().hex}@example.org>"
    m.set_content(body)
    if attach is not None:
        m.add_attachment(attach, maintype="application", subtype="octet-stream",
                         filename="file.bin" if attach_size is None else attach_size)
    return m.as_bytes(policy=email.policy.SMTP)  # CRLF line endings


# 1. valid uuid -> accepted, forwarded to pager
code, msg = send(f"{VALID_UUID}@{DOMAIN}", build_mail())
check("valid uuid RCPT+DATA accepted (250)", code == 250, f"{code} {msg}")
time.sleep(0.5)
st = state()
notes = [n for n in st["notify"] if n["mailbox"] == VALID_UUID]
check("pager received notify with Bearer uuid", len(notes) == 1)
if notes:
    p = notes[0]["payload"]
    check("payload has PROJECT/JOB_ID/status/message/logs",
          all(k in p for k in ("PROJECT", "JOB_ID", "status", "message", "logs")), json.dumps(p)[:200])
    check("message contains subject", "hello" in p["message"])
    check("logs contain body", "test body" in p["logs"])

# 2. wrong domain -> 550
code, msg = send(f"{VALID_UUID}@other.example", build_mail())
check("wrong domain rejected (550)", code == 550, f"{code} {msg}")

# 3. invalid uuid local part -> 550
code, msg = send(f"not-a-uuid@{DOMAIN}", build_mail())
check("invalid uuid rejected (550)", code == 550, f"{code} {msg}")

# 4. external recipient relay attempt -> 550 (anti open relay)
code, msg = send("victim@evil.example", build_mail())
check("external relay attempt rejected (550)", code == 550, f"{code} {msg}")

# 5. VRFY refused
s = smtplib.SMTP(HOST, PORT, timeout=10)
s.ehlo("tester")
code, msg = s.docmd("VRFY", "someone")
check("VRFY answered without leaking (252/502)", code in (252, 502), f"{code} {msg}")
s.quit()

# 6. message too large -> 550
big = build_mail(body="A" * 70000)  # text body > MAX_MESSAGE_BYTES -> 550
code, msg = send(f"{VALID_UUID}@{DOMAIN}", big)
check("oversized DATA rejected (550)", code == 550 and b"too large" in msg.lower(), f"{code} {msg}")

# 7. attachment upload -> share URL in pager payload
att = b"\x00\x01attachment-bytes" * 100
code, msg = send(f"{VALID_UUID}@{DOMAIN}", build_mail(subject="with-att", attach=att))
check("mail with attachment accepted", code == 250, f"{code} {msg}")
time.sleep(0.7)
st = state()
notes = [n for n in st["notify"] if n["mailbox"] == VALID_UUID and "with-att" in n["payload"]["message"]]
check("pager got mail with attachment", len(notes) == 1)
if notes:
    urls = notes[0]["payload"].get("attachments", [])
    check("attachments[] contains share URL", len(urls) == 1 and urls[0].startswith("http"), str(urls))
check("share mock stored the file", len(st["share"]) >= 1, str(list(st["share"])[:3]))
if st["share"]:
    check("share content matches", any(v.encode("latin-1") == att for v in st["share_bodies"].values()))

# 8. big-but-under-limit attachment passes (1 MB); over-limit attachment is
#    tested on a second instance with MAX_ATTACHMENT_BYTES=1024 in section 8b.
big_att = os.urandom(1024 * 1024)
code, msg = send(f"{VALID_UUID}@{DOMAIN}", build_mail(subject="big-att-ok", attach=big_att))
check("1MB attachment accepted", code == 250, f"{code} {msg}")

# 8b. small-attachment-limit instance: attachment > limit -> 550 attachment too large
env = dict(os.environ)
env["SMTP_PORT"] = "18027"
env.pop("PAGER_SESSION_ID", None)
env["MAX_ATTACHMENT_BYTES"] = "1024"
proc3 = subprocess.Popen([sys.executable, "smtp_server.py"], env=env,
                         stdout=open("/tmp/smtp3.log", "w"), stderr=subprocess.STDOUT)
time.sleep(1)
try:
    s = smtplib.SMTP(HOST, 18027, timeout=10)
    s.ehlo("t")
    s.docmd("MAIL", "FROM:<a@b.c>")
    s.docmd("RCPT", f"TO:<{VALID_UUID}@{DOMAIN}>")
    s.docmd("DATA")
    s.send(build_mail(subject="att-too-big", attach=b"Z" * 2048) + b"\r\n.\r\n")
    code, msg = s.docmd("")
    check("oversized attachment rejected (550 too large)", code == 550 and b"attachment too large" in msg.lower(), f"{code} {msg}")
    s.quit()
finally:
    proc3.terminate()
    proc3.wait(timeout=5)

# 9. PAGER_SESSION_ID lock: start second instance with lock and test
import subprocess
env = dict(os.environ)
env["PAGER_SESSION_ID"] = SESSION_UUID
env["SMTP_PORT"] = "18026"
proc = subprocess.Popen([sys.executable, "smtp_server.py"], env=env,
                        stdout=open("/tmp/smtp2.log", "w"), stderr=subprocess.STDOUT)
time.sleep(1)
try:
    s = smtplib.SMTP(HOST, 18026, timeout=10)
    s.ehlo("t")
    s.docmd("MAIL", "FROM:<a@b.c>")
    code, msg = s.docmd("RCPT", f"TO:<{VALID_UUID}@{DOMAIN}>")
    check("locked: other uuid rejected (550)", code == 550, f"{code} {msg}")
    code, msg = s.docmd("RCPT", f"TO:<{SESSION_UUID}@{DOMAIN}>")
    check("locked: session uuid accepted (250)", code == 250, f"{code} {msg}")
    s.quit()
finally:
    proc.terminate()
    proc.wait(timeout=5)

if failures:
    print(f"\n{len(failures)} FAILURES: {failures}")
    sys.exit(1)
print("\nALL SMTP TESTS PASSED")
