#!/usr/bin/env python3
"""SMTP integration tests against the running server + mock backends."""

import email.policy
import json
import os
import smtplib
import socket
import subprocess
import sys
import time
import uuid
from email.message import EmailMessage

HOST = "127.0.0.1"
PORT = 18025
STATE_FILE = sys.argv[1]
CONTROL_FILE = sys.argv[2] if len(sys.argv) > 2 else None
DOMAIN = "test.local"
VALID_UUID = str(uuid.uuid4())

failures = []
procs = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}: {name}" + (f" ({detail})" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def set_control(**values):
    if not CONTROL_FILE:
        return
    with open(CONTROL_FILE, "w") as fh:
        json.dump(values, fh)


def start_server(port, **env_over):
    env = dict(os.environ)
    env.update({k: str(v) for k, v in env_over.items()})
    env["SMTP_PORT"] = str(port)
    proc = subprocess.Popen(
        [sys.executable, "smtp_server.py"], env=env,
        stdout=open(f"/tmp/pagersmtpd-test-{port}.log", "w"), stderr=subprocess.STDOUT,
    )
    procs.append(proc)
    deadline = time.time() + 15
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server on {port} exited early, see /tmp/pagersmtpd-test-{port}.log")
        try:
            s = socket.create_connection((HOST, port), timeout=0.3)
            s.close()
            return proc
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"server on {port} did not start")


def send(rcpt, data: bytes, port=PORT, extra_rcpts=()):
    s = smtplib.SMTP(HOST, port, timeout=15)
    s.ehlo("tester")
    try:
        code, msg = s.docmd("MAIL", "FROM:<sender@example.org>")
        assert code == 250, (code, msg)
        for addr in (rcpt, *extra_rcpts):
            code, msg = s.docmd("RCPT", f"TO:<{addr}>")
            if code >= 500:
                return code, msg
            assert code == 250, (code, msg)
        code, msg = s.docmd("DATA")
        if code >= 500:
            return code, msg
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


def build_mail(rcpt=None, subject="hello", body="test body", attach=None, filename="file.bin"):
    m = EmailMessage()
    m["From"] = "sender@example.org"
    m["To"] = rcpt or f"{VALID_UUID}@{DOMAIN}"
    m["Subject"] = subject
    m["Message-ID"] = f"<{uuid.uuid4().hex}@example.org>"
    m.set_content(body)
    if attach is not None:
        m.add_attachment(attach, maintype="application", subtype="octet-stream", filename=filename)
    return m.as_bytes(policy=email.policy.SMTP)  # CRLF line endings


# 1. valid uuid -> accepted, forwarded to pager
code, msg = send(f"{VALID_UUID}@{DOMAIN}", build_mail())
check("valid uuid RCPT+DATA accepted (250)", code == 250, f"{code} {msg}")
time.sleep(0.4)
st = state()
notes = [n for n in st["notify"] if n["mailbox"] == VALID_UUID]
check("pager received notify with Bearer uuid", len(notes) == 1)
if notes:
    p = notes[0]["payload"]
    check("payload has PROJECT/JOB_ID/status/message/logs",
          all(k in p for k in ("PROJECT", "JOB_ID", "status", "message", "logs")), json.dumps(p)[:200])
    check("message contains sender and subject", "sender@example.org" in p["message"] and "hello" in p["message"])
    check("logs contain body", "test body" in p["logs"])
    check("logs have no header block", not p["logs"].startswith("From:"), p["logs"][:80])

# 1b. uuid sender -> title is just the subject (no uuid noise in the page)
m = EmailMessage()
m["From"] = f"{uuid.uuid4()}@pager.ofloo.io"
m["To"] = f"{VALID_UUID}@{DOMAIN}"
m["Subject"] = "uuid-sender-subject"
m["Message-ID"] = f"<{uuid.uuid4().hex}@example.org>"
m.set_content("uuid sender body")
code, msg = send(f"{VALID_UUID}@{DOMAIN}", m.as_bytes(policy=email.policy.SMTP))
check("uuid sender accepted", code == 250, f"{code} {msg}")
time.sleep(0.4)
st = state()
notes = [n for n in st["notify"] if n["payload"]["message"] == "uuid-sender-subject"]
check("uuid sender: title is only the subject", len(notes) == 1,
      str([n["payload"]["message"] for n in st["notify"]])[-150:])

# 1c. catch-all: several different uuids all accepted (no session lock)
other = str(uuid.uuid4())
code, msg = send(f"{other}@{DOMAIN}", build_mail(subject="catch-all-other-uuid"))
check("any valid uuid accepted (catch-all, no lock)", code == 250, f"{code} {msg}")
time.sleep(0.4)
st = state()
check("other uuid got its own page", any(n["mailbox"] == other for n in st["notify"]))

# 1d. version is visible in the SMTP greeting (banner)
with socket.create_connection((HOST, PORT), timeout=5) as sock:
    banner = sock.recv(512).decode("utf-8", "replace")
check("SMTP banner shows PagerSMPTd version", "PagerSMPTd" in banner, banner.strip())
time.sleep(0.3)
st = state()
check("payload carries smtpd_version", any(n["payload"].get("smtpd_version") for n in st["notify"]))

# 1e. cleaning: header noise / spam report / huge blobs are stripped
noisy = build_mail(subject="noisy", body="")

# build a body that mimics a forwarded spam report (Thunderbird NL headers)
noise_body = "\r\n".join([
    "Van: Wouter Snels <ofloo@h4ck.io>",
    "Onderwerp: test",
    "Bericht-ID: <abc@h4ck.io>",
    "DKIM-Signature: v=1; a=rsa-sha256; b=" + "X" * 400,
    "X-Spam-Flag: YES",
    "X-Spam-Status: Yes, score=5.4 required=5.0",
    "Autocrypt: addr=ofloo@h4ck.io; keydata=" + "Y" * 400,
    "Spam detection software, running on the system \"loki\"",
    "Content analysis details:   (5.4 points, 5.0 required)",
    " 3.5 BAYES_99               BODY: Bayes spam probability is 99 to 100%",
    "",
    "Echte berichttekst hier.",
])
noisy = build_mail(subject="noisy", body=noise_body)
code, msg = send(f"{VALID_UUID}@{DOMAIN}", noisy)
check("noisy mail accepted", code == 250, f"{code} {msg}")
time.sleep(0.4)
st = state()
notes = [n for n in st["notify"] if n["mailbox"] == VALID_UUID and "noisy" in n["payload"]["message"]]
check("noisy page received", len(notes) == 1)
if notes:
    logs = notes[0]["payload"]["logs"]
    check("DKIM/X-Spam/Autocrypt noise stripped",
          not any(k in logs for k in ("DKIM-Signature", "X-Spam", "Autocrypt", "Spam detection", "BAYES_99")), logs[:200])
    check("body survives cleaning", "Echte berichttekst hier." in logs)
    check("no overlong lines", all(len(l) <= 320 for l in logs.splitlines()))

# 1f. html-only mail -> text extracted without tags
html_mail = EmailMessage()
html_mail["From"] = "html@example.org"
html_mail["To"] = f"{VALID_UUID}@{DOMAIN}"
html_mail["Subject"] = "html-only"
html_mail["Message-ID"] = f"<{uuid.uuid4().hex}@example.org>"
html_mail.set_content("<html><body><p>Hallo <b>wereld</b></p><script>x()</script></body></html>", subtype="html")
code, msg = send(f"{VALID_UUID}@{DOMAIN}", html_mail.as_bytes(policy=email.policy.SMTP))
check("html-only mail accepted", code == 250, f"{code} {msg}")
time.sleep(0.4)
st = state()
notes = [n for n in st["notify"] if n["mailbox"] == VALID_UUID and "html-only" in n["payload"]["message"]]
check("html-only text extracted without tags",
      bool(notes) and "Hallo wereld" in notes[0]["payload"]["logs"] and "<p>" not in notes[0]["payload"]["logs"],
      notes[0]["payload"]["logs"][:150] if notes else "missing")

# 1g. forwarded mail (message/rfc822) body is used; the .eml is not uploaded
inner = EmailMessage()
inner["From"] = "forwarded@example.org"
inner["To"] = f"{VALID_UUID}@{DOMAIN}"
inner["Subject"] = "inner-subject"
inner["X-Spam-Status"] = "Yes"
inner.set_content("Dit is de doorgestuurde tekst.")
outer = EmailMessage()
outer["From"] = "forwarder@example.org"
outer["To"] = f"{VALID_UUID}@{DOMAIN}"
outer["Subject"] = "fwd-mail"
outer["Message-ID"] = f"<{uuid.uuid4().hex}@example.org>"
outer.set_content("Zie hieronder.")
outer.add_attachment(inner, filename="ForwardedMessage.eml")
code, msg = send(f"{VALID_UUID}@{DOMAIN}", outer.as_bytes(policy=email.policy.SMTP))
check("forwarded mail accepted", code == 250, f"{code} {msg}")
time.sleep(0.5)
st = state()
notes = [n for n in st["notify"] if n["mailbox"] == VALID_UUID and "fwd-mail" in n["payload"]["message"]]
check("forwarded body text present", bool(notes) and "Dit is de doorgestuurde tekst." in notes[0]["payload"]["logs"],
      notes[0]["payload"]["logs"][:150] if notes else "missing")
check("forwarded .eml not uploaded as attachment", bool(notes) and notes[0]["payload"]["attachments"] == [],
      str(notes[0]["payload"]["attachments"]) if notes else "missing")
check("forwarded headers not in logs", bool(notes) and "X-Spam-Status" not in notes[0]["payload"]["logs"])

# 2. wrong domain -> 550
code, msg = send(f"{VALID_UUID}@other.example", build_mail())
check("wrong domain rejected (550)", code == 550, f"{code} {msg}")

# 3. invalid uuid local part -> 550
code, msg = send(f"not-a-uuid@{DOMAIN}", build_mail())
check("invalid uuid rejected (550)", code == 550, f"{code} {msg}")

# 4. external recipient relay attempt -> 550 (anti open relay)
code, msg = send("victim@evil.example", build_mail())
check("external relay attempt rejected (550)", code == 550, f"{code} {msg}")

# 5. VRFY refused / no AUTH advertised
s = smtplib.SMTP(HOST, PORT, timeout=10)
s.ehlo("tester")
code, msg = s.docmd("VRFY", "someone")
check("VRFY answered without leaking (252/502)", code in (252, 502), f"{code} {msg}")
s.quit()
s = smtplib.SMTP(HOST, PORT, timeout=10)
_, ehlo = s.ehlo("tester")
check("AUTH is not advertised on a public MX", b"AUTH" not in ehlo)
check("SIZE advertised equals MAX_TOTAL_BYTES", b"SIZE 105457600" in ehlo, ehlo.decode()[:120])
s.quit()

# 6. message too large -> 550
code, msg = send(f"{VALID_UUID}@{DOMAIN}", build_mail(body="A" * 70000))
check("oversized text body rejected (550)", code == 550 and b"too large" in msg.lower(), f"{code} {msg}")

# 7. attachment upload -> share URL in pager payload
att = b"\x00\x01attachment-bytes" * 100
code, msg = send(f"{VALID_UUID}@{DOMAIN}", build_mail(subject="with-att", attach=att))
check("mail with attachment accepted", code == 250, f"{code} {msg}")
time.sleep(0.5)
st = state()
notes = [n for n in st["notify"] if n["mailbox"] == VALID_UUID and "with-att" in n["payload"]["message"]]
check("pager got mail with attachment", len(notes) == 1)
if notes:
    urls = notes[0]["payload"].get("attachments", [])
    check("attachments[] contains share URL", len(urls) == 1 and urls[0].startswith("http"), str(urls))
check("share mock stored the file", len(st["share"]) >= 1, str(list(st["share"])[:3]))
if st["share"]:
    check("share content matches", any(v.encode("latin-1") == att for v in st["share_bodies"].values()))

# 8. big-but-under-limit attachment passes (1 MB)
big_att = os.urandom(1024 * 1024)
code, msg = send(f"{VALID_UUID}@{DOMAIN}", build_mail(subject="big-att-ok", attach=big_att))
check("1MB attachment accepted", code == 250, f"{code} {msg}")

# 9. multiple recipients in one transaction: one upload, both mailboxes notified
rcpt_a, rcpt_b = str(uuid.uuid4()), str(uuid.uuid4())
tag = uuid.uuid4().hex[:8]
code, msg = send(
    f"{rcpt_a}@{DOMAIN}",
    build_mail(subject=f"multi-{tag}", attach=b"MULTI-ATT"),
    extra_rcpts=(f"{rcpt_b}@{DOMAIN}",),
)
check("multi-recipient transaction accepted", code == 250, f"{code} {msg}")
time.sleep(0.5)
st = state()
a_notes = [n for n in st["notify"] if n["mailbox"] == rcpt_a and tag in n["payload"]["message"]]
b_notes = [n for n in st["notify"] if n["mailbox"] == rcpt_b and tag in n["payload"]["message"]]
check("both recipients notified", len(a_notes) == 1 and len(b_notes) == 1)
if a_notes and b_notes:
    check("both got the same attachment URL",
          a_notes[0]["payload"]["attachments"] == b_notes[0]["payload"]["attachments"],
          f"{a_notes[0]['payload']['attachments']} vs {b_notes[0]['payload']['attachments']}")

# 10. pager error mapping (429/5xx -> 451, 4xx -> 550)
set_control(notify_status=429)
code, msg = send(f"{VALID_UUID}@{DOMAIN}", build_mail(subject="pager-429"))
check("pager 429 -> 451", code == 451, f"{code} {msg}")
set_control(notify_status=500)
code, msg = send(f"{VALID_UUID}@{DOMAIN}", build_mail(subject="pager-500"))
check("pager 5xx -> 451", code == 451, f"{code} {msg}")
set_control(notify_status=401)
code, msg = send(f"{VALID_UUID}@{DOMAIN}", build_mail(subject="pager-401"))
check("pager 4xx -> 550 (permanent)", code == 550, f"{code} {msg}")

# 11. share failure: mail still accepted, URL absent, note in logs
set_control(share_status=500)
code, msg = send(f"{VALID_UUID}@{DOMAIN}", build_mail(subject="share-fail", attach=b"FAILED-UPLOAD"))
check("share failure does not reject the mail", code == 250, f"{code} {msg}")
time.sleep(0.5)
st = state()
notes = [n for n in st["notify"] if n["mailbox"] == VALID_UUID and "share-fail" in n["payload"]["message"]]
check("share failure recorded in logs", bool(notes) and "attachment upload failed" in notes[0]["payload"]["logs"],
      notes[0]["payload"]["logs"][:120] if notes else "no note")
if notes:
    check("no attachments[] on failed upload", notes[0]["payload"]["attachments"] == [])
set_control()

# 12. oversized attachment (small limit instance)
start_server(18027, MAX_ATTACHMENT_BYTES=1024)
s = smtplib.SMTP(HOST, 18027, timeout=15)
s.ehlo("t")
s.docmd("MAIL", "FROM:<a@b.c>")
s.docmd("RCPT", f"TO:<{VALID_UUID}@{DOMAIN}>")
s.docmd("DATA")
s.send(build_mail(subject="att-too-big", attach=b"Z" * 2048) + b"\r\n.\r\n")
code, msg = s.docmd("")
check("oversized attachment rejected (550 too large)", code == 550 and b"attachment too large" in msg.lower(), f"{code} {msg}")
s.quit()

# 13. total DATA cap (small MAX_TOTAL_BYTES instance) -> rejected before buffering
start_server(18028, MAX_TOTAL_BYTES=5000)
s = smtplib.SMTP(HOST, 18028, timeout=15)
s.ehlo("t")
s.docmd("MAIL", "FROM:<a@b.c>")
s.docmd("RCPT", f"TO:<{VALID_UUID}@{DOMAIN}>")
s.docmd("DATA")
s.send(build_mail(subject="total-cap", attach=b"Q" * 20000) + b"\r\n.\r\n")
code, msg = s.docmd("")
check("total DATA cap rejected (550/552)", code in (550, 552), f"{code} {msg}")
s.quit()

# 14. rate limit (limit=2 instance) -> 3rd RCPT is 451
start_server(18029, RATE_LIMIT_MESSAGES=2)
s = smtplib.SMTP(HOST, 18029, timeout=15)
s.ehlo("t")
codes = []
for _ in range(3):
    s.docmd("MAIL", "FROM:<a@b.c>")
    codes.append(s.docmd("RCPT", f"TO:<{VALID_UUID}@{DOMAIN}>")[0])
    s.docmd("RSET")
check("rate limit: first two accepted, third 451", codes == [250, 250, 451], str(codes))
s.quit()

for proc in procs:
    proc.terminate()
for proc in procs:
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()

if failures:
    print(f"\n{len(failures)} FAILURES: {failures}")
    sys.exit(1)
print("\nALL SMTP TESTS PASSED")
