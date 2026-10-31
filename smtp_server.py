#!/usr/bin/env python3
"""Receive-only SMTP catch-all that forwards mail to PagerMCP.

Accepts mail for any <uuid>@PAGER_DOMAIN, uploads attachments to SHARE_URL
(HTTPS PUT) and posts the message to PAGER_URL/notify with that uuid as Bearer
token. Any well-formed uuid is accepted; PagerMCP decides whether the mailbox
exists (unknown/full mailboxes are reported back to the SMTP client).

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

import socket as _socket

from aiosmtpd import controller as _controller_mod
from aiosmtpd.controller import Controller
from aiosmtpd.smtp import SMTP as SMTPServer


def _safe_get_localhost() -> str:
    """aiosmtpd's readiness check uses get_localhost(); its IPv6 probe crashes
    or is unreachable where IPv6 is disabled (FreeBSD jails, minimal
    containers). We always listen on IPv4 (SMTP_HOST defaults to 0.0.0.0), so
    the internal check must use IPv4 too."""
    return "127.0.0.1"


_controller_mod.get_localhost = _safe_get_localhost

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


def read_version() -> str:
    """Version from APP_VERSION, then the VERSION file shipped next to us."""
    candidates = [
        os.environ.get("APP_VERSION", "").strip(),
        "/app/VERSION",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "VERSION"),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            with open(candidate, encoding="utf-8") as fh:
                value = fh.read().strip()
            if value:
                return value
        except OSError:
            continue
    return "unknown"


VERSION = read_version()
BUILD_NUMBER = os.environ.get("BUILD_NUMBER", "").strip()
VERSION_STRING = f"{VERSION} build {BUILD_NUMBER}" if BUILD_NUMBER else VERSION
IDENT = f"PagerSMPTd {VERSION_STRING}"

PAGER_DOMAIN = os.environ.get("PAGER_DOMAIN", "").strip().lower()
PAGER_URL = os.environ.get("PAGER_URL", "https://pager.ofloo.io").strip().rstrip("/")
SHARE_URL = os.environ.get("SHARE_URL", "https://share.h4ck.io").strip().rstrip("/")
SMTP_HOST = os.environ.get("SMTP_HOST", "0.0.0.0")
SMTP_PORT = env_int("SMTP_PORT", 8025)
MAX_MESSAGE_BYTES = env_int("MAX_MESSAGE_BYTES", 60000)
MAX_ATTACHMENT_BYTES = env_int("MAX_ATTACHMENT_BYTES", 104857600)  # 100 MB
MAX_ATTACHMENTS = env_int("MAX_ATTACHMENTS", 10)
# Hard cap on the whole DATA blob (headers + text + all attachments). This is
# what the server advertises in EHLO SIZE and buffers in memory; without it a
# sender could push MAX_ATTACHMENT_BYTES * MAX_ATTACHMENTS (~1 GB) into RAM.
MAX_TOTAL_BYTES = env_int(
    "MAX_TOTAL_BYTES", MAX_MESSAGE_BYTES + MAX_ATTACHMENT_BYTES
)
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
    if ALLOW_INSECURE:
        LOG.warning("%s is plain HTTP (%s) — only allowed with PAGER_ALLOW_INSECURE=1", name, url)
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
_rate_calls = 0


def _prune_rate_buckets(now: float) -> None:
    """Drop buckets for peers that went quiet, so memory stays bounded."""
    horizon = RATE_LIMIT_WINDOW_SECONDS * 4
    stale = [key for key, bucket in _rate_buckets.items()
             if not bucket or now - bucket[-1] > horizon]
    for key in stale:
        _rate_buckets.pop(key, None)


def rate_limited(peer: str) -> bool:
    global _rate_calls
    now = time.monotonic()
    _rate_calls += 1
    if _rate_calls % 100 == 0:
        _prune_rate_buckets(now)
    bucket = _rate_buckets[peer]
    while bucket and now - bucket[0] > RATE_LIMIT_WINDOW_SECONDS:
        bucket.popleft()
    if len(bucket) >= RATE_LIMIT_MESSAGES:
        return True
    bucket.append(now)
    return False


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow redirects: a 3xx on a PUT must not silently become a GET."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name or "attachment")
    cleaned = cleaned.strip("._") or "attachment"
    return f"{uuidlib.uuid4().hex[:12]}_{cleaned[:180]}"


def https_request(
    method: str, url: str, timeout: int, data: bytes | None = None,
    headers: dict[str, str] | None = None, max_response: int = 8192,
) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=data, method=method)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            return resp.status, resp.read(max_response)
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(max_response) if exc.fp else b""
        except Exception:
            body = b""
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
        if not url.lower().startswith(("http://", "https://")):
            url = f"{SHARE_URL}/{name}"
        LOG.info("attachment %s uploaded (%d bytes) -> %s", filename, len(payload), url)
        return url
    raise RuntimeError(f"share upload failed for {filename!r}: HTTP {status}")


def iter_leaf_parts(part: Message):
    """Yield real content leaves, descending into message/rfc822 (forwards)."""
    ctype = part.get_content_type()
    if ctype == "message/rfc822":
        payload = part.get_payload()
        if isinstance(payload, list):
            for sub in payload:
                yield from iter_leaf_parts(sub)
        elif isinstance(payload, Message):
            yield from iter_leaf_parts(payload)
        return
    if part.is_multipart():
        for sub in part.get_payload():
            yield from iter_leaf_parts(sub)
        return
    yield part


def find_rfc822(part: Message):
    """Yield message/rfc822 parts (a forwarded/attached original mail)."""
    if part.get_content_type() == "message/rfc822":
        yield part
        return
    if part.is_multipart():
        for sub in part.get_payload():
            yield from find_rfc822(sub)


def extract_text(msg: Message) -> str:
    plain: list[str] = []
    html_parts: list[str] = []

    def collect(part: Message) -> None:
        if part.get_content_disposition() == "attachment":
            return
        ctype = part.get_content_type()
        try:
            content = part.get_content()
        except Exception:
            payload = part.get_payload(decode=True) or b""
            content = payload.decode(part.get_content_charset() or "utf-8", "replace")
        if ctype == "text/plain":
            plain.append(content)
        elif ctype == "text/html":
            html_parts.append(content)

    # A spam/virus scanner wraps the original mail as message/rfc822 and adds
    # its own report text. The report is noise: prefer the real message.
    forwarded = list(find_rfc822(msg))
    source = forwarded[0] if forwarded else msg
    if source.is_multipart() or source.get_content_type() == "message/rfc822":
        for part in iter_leaf_parts(source):
            collect(part)
    else:
        collect(source)

    plain_text = "\n".join(plain).strip()
    if plain_text:
        return plain_text
    # Many clients send an empty text/plain part next to a filled text/html one.
    if html_parts:
        return html_to_text("\n".join(html_parts)).strip()
    return ""


# Headers that carry no value for the human reading a page (spam scoring,
# transport traces, signatures, MIME plumbing). Matched case-insensitively in
# English and the common localized forms Thunderbird/Outlook emit.
NOISE_HEADER_RE = re.compile(
    r"^(received|return-path|delivered-to|dkim-signature|domainkey-signature|"
    r"arc-[a-z-]+|authentication-results|received-spf|arc-seal|arc-message-signature|"
    r"x-spam[a-z-]*|x-virus[a-z-]*|x-mailer|user-agent|useragent|x-mailer|"
    r"autocrypt|content-language|x-envelope-(from|to)|x-original-to|x-uidl|"
    r"mime-version|mime-versie|mime-version|content-type|inhoud-type|"
    r"content-transfer-encoding|inhoudsoverbrengcodering|"
    r"message-id|bericht-id|in-reply-to|references|reply-to|antwoord-naar|"
    r"list-[a-z-]+|precedence|x-priority|importance|prioriteit|"
    r"x-spam-level|x-spam-status|x-spam-flag|x-spam-checker-version|"
    r"van|aan|onderwerp|datum|von|betreff|nachricht-id|"
    r"de|objet|identifiant-du-message)\s*:",
    re.IGNORECASE,
)

SPAM_BLOCK_MARKERS = (
    "spam detection software",
    "content analysis details:",
    "content preview:",
    "spamassassin",
)


def clean_text(text: str) -> str:
    """Strip header noise, spam reports and giant base64 blobs from a body."""
    lines = []
    skip_report = False
    for line in text.splitlines():
        stripped = line.strip()
        low = stripped.lower()
        if skip_report:
            # The SpamAssassin block ends at the first blank line.
            if not stripped:
                skip_report = False
            continue
        if any(low.startswith(marker) for marker in SPAM_BLOCK_MARKERS):
            skip_report = True
            continue
        if NOISE_HEADER_RE.match(stripped):
            continue
        # SpamAssassin score table rows and their indented continuations.
        if re.match(r"^-?\d+\.\d+\s+[A-Z0-9_]{3,}\s{2,}", stripped):
            continue
        if re.match(r"^(pts rule name|-{2,}\s)", stripped, re.IGNORECASE):
            continue
        if re.match(r"^\[[A-Z][^\]]*\]$", stripped) or re.match(r"^\[score:", stripped):
            continue
        if len(line) > 300:
            line = line[:300] + "…"
        lines.append(line.rstrip())
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def html_to_text(html: str) -> str:
    import html as htmlmod

    html = re.sub(r"(?is)<(script|style|head)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>", "\n", html)
    html = re.sub(r"(?i)</(p|div|tr|li|h[1-6]|blockquote|table)>", "\n", html)
    html = re.sub(r"<[^>]+>", " ", html)
    html = htmlmod.unescape(html).replace("\xa0", " ")
    lines = []
    for line in html.splitlines():
        line = re.sub(r"[ \t]+", " ", line).strip()
        if line:
            lines.append(line)
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def sender_address(sender_header: str) -> str:
    from email.utils import parseaddr

    return parseaddr(sender_header)[1] or sender_header.strip()


def notification_title(sender: str, subject: str) -> str:
    """Title for the page: sender + subject, unless the sender is a mailbox uuid.

    A uuid sender carries no information for the reader, so in that case only
    the subject is used.
    """
    address = sender_address(sender)
    local = address.rpartition("@")[0]
    subject = (subject or "").strip()
    if UUID_RE.match(local or ""):
        return subject or f"Mail from {address}"
    if subject:
        return f"{address or sender} — {subject}"
    return f"Mail from {address or sender}"


def extract_attachments(msg: Message) -> list[tuple[str, bytes]]:
    """Real file attachments only: forwarded mails (message/rfc822) are body."""
    out: list[tuple[str, bytes]] = []
    for part in iter_leaf_parts(msg):
        filename = part.get_filename()
        if part.get_content_type() in ("text/plain", "text/html"):
            continue
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
        max_response=2048,
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
        envelope.rcpt_tos.append(address)
        LOG.info("accepted RCPT from %s: %s", peer, address)
        return "250 OK"

    async def handle_DATA(self, server, session, envelope):
        peer = session.peer[0] if session.peer else "unknown"
        try:
            return self._process(peer, envelope)
        except Exception:
            LOG.exception("unhandled error processing DATA from %s", peer)
            return "451 4.3.0 temporary local error, try again later"

    def _process(self, peer: str, envelope) -> str:
        raw = envelope.content
        if len(raw) > MAX_TOTAL_BYTES:
            LOG.info("DATA from %s too large: %d bytes > %d", peer, len(raw), MAX_TOTAL_BYTES)
            return f"550 5.3.4 message too large ({len(raw)} > {MAX_TOTAL_BYTES} bytes)"

        msg = email.message_from_bytes(raw, policy=email.policy.default)

        # Message size limit applies to headers + text body only.
        # Attachments go to SHARE_URL under their own (much larger) limit.
        text_bytes = len(str(msg.get("Subject", "")).encode())
        text_bytes += len(str(msg.get("From", "")).encode())
        text_bytes += len(extract_text(msg).encode("utf-8", "replace"))
        if text_bytes > MAX_MESSAGE_BYTES:
            LOG.info("message from %s too large: %d bytes > %d", peer, text_bytes, MAX_MESSAGE_BYTES)
            return f"550 5.3.4 message too large ({text_bytes} > {MAX_MESSAGE_BYTES} bytes)"

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

        # Upload once, reuse the URLs for every recipient on this transaction.
        attachment_urls: list[str] = []
        log_notes: list[str] = []
        for name, payload in attachments:
            try:
                attachment_urls.append(upload_attachment(name, payload))
            except Exception as exc:
                LOG.error("attachment upload failed: %s", exc)
                log_notes.append(f"attachment upload failed: {name}: {exc}")

        subject = str(msg.get("Subject", ""))
        sender = str(msg.get("From", ""))
        message_id = str(msg.get("Message-ID", "")) or uuidlib.uuid4().hex
        body_text = clean_text(extract_text(msg))
        # No header block: the page shows the text body only. Transport and
        # spam headers are dropped by clean_text() as well.
        logs = body_text[:6000]
        if log_notes:
            logs = (logs + "\n" + "\n".join(log_notes)).strip()
        title = notification_title(sender, subject)

        recipients = sorted({addr.rpartition("@")[0].lower() for addr in envelope.rcpt_tos})
        permanent = False
        temporary = False
        for local in recipients:
            payload = {
                "PROJECT": PROJECT,
                "JOB_ID": message_id,
                # Numeric status matches the PagerAPP page contract (1 = warning,
                # i.e. a new mail that needs attention). The human word is kept
                # in status_text.
                "status": 1,
                "status_text": "received",
                "exit_code": 0,
                "message": title,
                "logs": logs,
                "attachments": attachment_urls,
                "source": "smtp",
                "smtpd_version": VERSION_STRING,
                "rcpt": f"{local}@{PAGER_DOMAIN}",
            }
            try:
                status, resp = notify_pager(local, payload)
            except Exception as exc:
                LOG.error("pager notify network error for %s: %s", local, exc)
                temporary = True
                continue

            if 200 <= status < 300:
                LOG.info("forwarded mail from %s to pager mailbox %s (HTTP %d)", peer, local, status)
            elif status == 429:
                LOG.warning("pager 429 full mailbox %s", local)
                temporary = True
            elif 400 <= status < 500:
                LOG.error("pager rejected %s (HTTP %d): %s", local, status, resp)
                permanent = True
            else:
                LOG.error("pager server error for %s (HTTP %d): %s", local, status, resp)
                temporary = True

        if temporary:
            return "451 4.3.0 temporary failure contacting pager, try again later"
        if permanent:
            return "550 5.3.0 pager rejected message"
        return "250 OK queued for delivery to pager"


class PagerController(Controller):
    def _trigger_server(self):
        """Connect to loopback to force the factory to run.

        aiosmtpd connects to self.hostname, but the wildcard address
        (0.0.0.0/::) is not connectable on FreeBSD. Docker also needs the
        server bound to the wildcard, so only the probe is redirected.
        """
        host = self.hostname
        if host in (None, "", "0.0.0.0", "::"):
            host = "127.0.0.1"
        with _socket.create_connection((host, self.port), 1.0) as sock:
            sock.recv(1024)

    def factory(self):
        return SMTPServer(
            self.handler,
            data_size_limit=MAX_TOTAL_BYTES,
            decode_data=False,
            ident=IDENT,
        )


def main() -> None:
    LOG.info("PagerSMPTd %s", VERSION_STRING)
    LOG.info(
        "starting receive-only SMTP on %s:%d domain=%s pager=%s share=%s "
        "max_total=%d max_msg=%d max_att=%d max_attachments=%d rate=%d/%ds",
        SMTP_HOST, SMTP_PORT, PAGER_DOMAIN, PAGER_URL, SHARE_URL,
        MAX_TOTAL_BYTES, MAX_MESSAGE_BYTES, MAX_ATTACHMENT_BYTES, MAX_ATTACHMENTS,
        RATE_LIMIT_MESSAGES, RATE_LIMIT_WINDOW_SECONDS,
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
