#!/usr/bin/env python3
"""Receive-only SMTP catch-all that forwards mail to PagerMCP.

Accepts mail only for <uuid>@PAGER_DOMAIN (optionally restricted to a single
PAGER_SESSION_ID), uploads attachments to SHARE_URL (HTTPS PUT) and posts the
message to PAGER_URL/notify with the uuid as Bearer token.

There is deliberately NO outbound SMTP: this can never become an open relay.
The only outbound connections are HTTPS to PAGER_URL and SHARE_URL.
"""

import email
import email.policy
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid as uuidlib
from collections import defaultdict, deque
from email.message import Message

from aiosmtpd.controller import Controller
from aiosmtpd.smtp import SMTP as SMTPServer

LOG = logging.getLogger("pagersmtpd")

UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        LOG.warning("invalid %s=%r, using default %d", name, raw, default)
        return default


PAGER_DOMAIN = os.environ.get("PAGER_DOMAIN", "").strip().lower()
PAGER_URL = os.environ.get("PAGER_URL", "https://pager.ofloo.io").strip().rstrip("/")
SHARE_URL = os.environ.get("SHARE_URL", "https://share.h4ck.io").strip().rstrip("/")
PAGER_SESSION_ID = os.environ.get("PAGER_SESSION_ID", "").strip().lower()
SMTP_HOST = os.environ.get("SMTP_HOST", "0.0.0.0")
SMTP_PORT = env_int("SMTP_PORT", 8025)
MAX_MESSAGE_BYTES = env_int("MAX_MESSAGE_BYTES", 60000)
MAX_ATTACHMENT_BYTES = env_int("MAX_ATTACHMENT_BYTES", 104857600)  # 100 MB
MAX_ATTACHMENTS = env_int("MAX_ATTACHMENTS", 10)
SHARE_TIMEOUT_SECONDS = env_int("SHARE_TIMEOUT_SECONDS", 60)
PAGER_TIMEOUT_SECONDS = env_int("PAGER_TIMEOUT_SECONDS", 15)
RATE_LIMIT_MESSAGES = env_int("RATE_LIMIT_MESSAGES", 20)
RATE_LIMIT_WINDOW_SECONDS = env_int("RATE_LIMIT_WINDOW_SECONDS", 60)
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
PROJECT = os.environ.get("PAGER_PROJECT", "smtp")

if not PAGER_DOMAIN:
    print("FATAL: PAGER_DOMAIN environment variable is required", file=sys.stderr)
    sys.exit(2)
ALLOW_INSECURE = os.environ.get("PAGER_ALLOW_INSECURE", "") == "1"


def _require_https(name: str, url: str) -> None:
    if url.lower().startswith("https://"):
        return
    host = url.lower().split("://", 1)[-1].split("/", 1)[0].split(":")[0]
    if ALLOW_INSECURE and host in ("127.0.0.1", "localhost", "::1"):
        LOG.warning("%s is plain HTTP on %s — only allowed with PAGER_ALLOW_INSECURE=1", name, host)
        return
    print(f"FATAL: {name} must be HTTPS (got {url!r})", file=sys.stderr)
    sys.exit(2)


_require_https("PAGER_URL", PAGER_URL)
_require_https("SHARE_URL", SHARE_URL)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(message)s",
)

_rate_buckets: dict[str, deque[float]] = defaultdict(deque)


def rate_limited(peer: str) -> bool:
    now = time.monotonic()
    bucket = _rate_buckets[peer]
    while bucket and now - bucket[0] > RATE_LIMIT_WINDOW_SECONDS:
        bucket.popleft()
    if len(bucket) >= RATE_LIMIT_MESSAGES:
        return True
    bucket.append(now)
    return False


def safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name or "attachment")
    cleaned = cleaned.strip("._") or "attachment"
    return f"{uuidlib.uuid4().hex[:12]}_{cleaned[:180]}"


def https_request(
    method: str, url: str, timeout: int, data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=data, method=method)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read() if exc.fp else b""
        return exc.code, body


def upload_attachment(filename: str, payload: bytes) -> str:
    name = safe_filename(filename)
    status, body = https_request(
        "PUT",
        f"{SHARE_URL}/{name}",
        timeout=SHARE_TIMEOUT_SECONDS,
        data=payload,
        headers={"Content-Type": "application/octet-stream"},
    )
    if 200 <= status < 300:
        url = body.decode("utf-8", "replace").strip()
        LOG.info("attachment %s uploaded (%d bytes) -> %s", filename, len(payload), url)
        return url
    raise RuntimeError(f"share upload failed for {filename!r}: HTTP {status}")


def extract_text(msg: Message) -> str:
    chunks: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and part.get_content_disposition() != "attachment":
                try:
                    chunks.append(part.get_content())
                except Exception:
                    payload = part.get_payload(decode=True) or b""
                    chunks.append(payload.decode(part.get_content_charset() or "utf-8", "replace"))
    else:
        try:
            chunks.append(msg.get_content())
        except Exception:
            payload = msg.get_payload(decode=True) or b""
            chunks.append(payload.decode(msg.get_content_charset() or "utf-8", "replace"))
    return "\n".join(chunks).strip()


def extract_attachments(msg: Message) -> list[tuple[str, bytes]]:
    out: list[tuple[str, bytes]] = []
    for part in msg.walk():
        filename = part.get_filename()
        if part.get_content_disposition() == "attachment" or filename:
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            out.append((filename or "attachment", payload))
    return out


def notify_pager(mailbox_uuid: str, payload: dict) -> tuple[int, str]:
    body = json.dumps(payload).encode("utf-8")
    status, resp_body = https_request(
        "POST",
        f"{PAGER_URL}/notify",
        timeout=PAGER_TIMEOUT_SECONDS,
        data=body,
        headers={
            "Authorization": f"Bearer {mailbox_uuid}",
            "Content-Type": "application/json",
        },
    )
    return status, resp_body.decode("utf-8", "replace")[:500]


class PagerHandler:
    async def handle_NOOP(self, server, session, envelope, arg):
        return "250 OK"

    async def handle_VRFY(self, server, session, envelope, arg):
        return "252 2.1.5 Cannot VRFY, but will accept the message and attempt delivery"

    async def handle_EXPN(self, server, session, envelope, arg):
        return "502 5.5.1 EXPN not supported"

    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):
        peer = session.peer[0] if session.peer else "unknown"
        if rate_limited(peer):
            LOG.warning("rate limit exceeded for peer %s", peer)
            return "451 4.7.1 rate limit exceeded, try again later"
        local, _, domain = address.rpartition("@")
        domain = domain.lower()
        if domain != PAGER_DOMAIN or not UUID_RE.match(local or ""):
            LOG.info("rejected RCPT from %s: %s (domain=%s)", peer, address, domain)
            return f"550 5.1.1 mailbox <{address}> not accepted here"
        if PAGER_SESSION_ID and local.lower() != PAGER_SESSION_ID:
            LOG.info("rejected RCPT from %s: %s not the configured session mailbox", peer, address)
            return "550 5.1.1 mailbox not accepted here"
        envelope.rcpt_tos.append(address)
        LOG.info("accepted RCPT from %s: %s", peer, address)
        return "250 OK"

    async def handle_DATA(self, server, session, envelope):
        peer = session.peer[0] if session.peer else "unknown"
        raw = envelope.content
        msg = email.message_from_bytes(raw, policy=email.policy.default)

        # Message size limit applies to headers + text body only.
        # Attachments go to SHARE_URL under their own (much larger) limit.
        text_bytes = len(msg.get("Subject", "").encode()) + len(str(msg.get("From", "")).encode())
        text_bytes += len(extract_text(msg).encode("utf-8", "replace"))
        if text_bytes > MAX_MESSAGE_BYTES:
            LOG.info("message from %s too large: %d bytes > %d", peer, text_bytes, MAX_MESSAGE_BYTES)
            return f"550 5.3.4 message too large ({text_bytes} > {MAX_MESSAGE_BYTES} bytes)"

        mailbox = envelope.rcpt_tos[0]
        local = mailbox.rpartition("@")[0]

        subject = str(msg.get("Subject", ""))
        sender = str(msg.get("From", ""))
        message_id = str(msg.get("Message-ID", "")) or uuidlib.uuid4().hex

        attachments = extract_attachments(msg)
        if len(attachments) > MAX_ATTACHMENTS:
            LOG.info("too many attachments from %s: %d > %d", peer, len(attachments), MAX_ATTACHMENTS)
            return f"550 5.3.4 too many attachments ({len(attachments)} > {MAX_ATTACHMENTS})"
        for name, payload in attachments:
            if len(payload) > MAX_ATTACHMENT_BYTES:
                LOG.info(
                    "attachment from %s too large: %s %d bytes > %d",
                    peer, name, len(payload), MAX_ATTACHMENT_BYTES,
                )
                return (
                    f"550 5.3.4 attachment too large "
                    f"({name}: {len(payload)} > {MAX_ATTACHMENT_BYTES} bytes)"
                )

        attachment_urls: list[str] = []
        log_notes: list[str] = []
        for name, payload in attachments:
            try:
                attachment_urls.append(upload_attachment(name, payload))
            except Exception as exc:
                LOG.error("attachment upload failed: %s", exc)
                log_notes.append(f"attachment upload failed: {name}: {exc}")

        body_text = extract_text(msg)
        logs = body_text[:4000]
        if log_notes:
            logs = (logs + "\n" + "\n".join(log_notes)).strip()

        payload = {
            "PROJECT": PROJECT,
            "JOB_ID": message_id,
            "status": "received",
            "message": f"Mail from {sender} — {subject}".strip(" —"),
            "logs": logs,
            "attachments": attachment_urls,
            "source": "smtp",
            "rcpt": mailbox,
        }

        try:
            status, resp = notify_pager(local, payload)
        except Exception as exc:
            LOG.error("pager notify network error for %s: %s", mailbox, exc)
            return "451 4.3.0 temporary failure contacting pager, try again later"

        if 200 <= status < 300:
            LOG.info("forwarded mail from %s to pager mailbox %s (HTTP %d)", peer, local, status)
            return "250 OK queued for delivery to pager"
        if status == 429:
            LOG.warning("pager 429 full mailbox %s", local)
            return "451 4.3.0 mailbox full, try again later"
        if 400 <= status < 500:
            LOG.error("pager rejected (HTTP %d): %s", status, resp)
            return f"550 5.3.0 pager rejected message (HTTP {status})"
        LOG.error("pager server error (HTTP %d): %s", status, resp)
        return "451 4.3.0 pager temporary failure, try again later"


class PagerController(Controller):
    def factory(self):
        return SMTPServer(
            self.handler,
            data_size_limit=MAX_MESSAGE_BYTES + MAX_ATTACHMENT_BYTES * MAX_ATTACHMENTS + 1048576,
            decode_data=False,
        )


def main() -> None:
    LOG.info(
        "starting receive-only SMTP on %s:%d domain=%s pager=%s share=%s "
        "max_msg=%d max_att=%d rate=%d/%ds session_lock=%s",
        SMTP_HOST, SMTP_PORT, PAGER_DOMAIN, PAGER_URL, SHARE_URL,
        MAX_MESSAGE_BYTES, MAX_ATTACHMENT_BYTES,
        RATE_LIMIT_MESSAGES, RATE_LIMIT_WINDOW_SECONDS,
        PAGER_SESSION_ID or "any-uuid",
    )
    controller = PagerController(
        PagerHandler(), hostname=SMTP_HOST, port=SMTP_PORT
    )
    controller.start()
    LOG.info("listening on %s:%d", SMTP_HOST, SMTP_PORT)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        controller.stop()


if __name__ == "__main__":
    main()
