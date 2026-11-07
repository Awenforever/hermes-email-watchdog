#!/usr/bin/env python3
"""A single long-lived IMAP session that both lists and reads mail.

The watchdog used to call the himalaya CLI for every mailbox operation, and each
call opens its own connection and performs its own LOGIN. A scan with six new
messages therefore cost seven logins, every cycle. Providers throttle login
rate, not just concurrent connections, so this pattern trips "Too many
simultaneous connections" and the account silently stops seeing mail.

This module keeps one authenticated connection per account and serves both the
envelope listing and the body fetches from it. It never mutates mailbox state:
every body fetch uses BODY.PEEK, so no flag is set and no message is marked read.
"""
from __future__ import annotations

import email
import email.policy
import imaplib
import re
import ssl
import threading
import time
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional

_DEFAULT_PORT = 993
_HEADER_FIELDS = "FROM TO SUBJECT DATE MESSAGE-ID"


def _is_throttle(exc):
    text = str(exc or '').lower()
    hints = tuple(globals().get('_THROTTLE_HINTS') or ())
    for hint in hints:
        if hint in text:
            return True
    return False


class ImapSessionError(RuntimeError):
    pass


class ImapSession:
    """One authenticated connection, reused by listing and reading."""

    def __init__(self, host: str, port: int = _DEFAULT_PORT, username: str = "",
                 password: str = "", folder: str = "INBOX", timeout: float = 60.0,
                 connector=None, key: str = "") -> None:
        self.host = host
        self.port = int(port or _DEFAULT_PORT)
        self.username = username
        self.password = password
        self.folder = folder or "INBOX"
        self.timeout = float(timeout)
        self._connector = connector or imaplib.IMAP4_SSL
        self._lock = threading.RLock()
        self._conn = None
        self.key = key
        self.stats: Dict[str, Any] = {"connects": 0, "logins": 0, "lists": 0, "reads": 0, "errors": 0}

    # -- connection -------------------------------------------------
    def _open(self):
        ctx = ssl.create_default_context()
        conn = self._connector(self.host, self.port, ssl_context=ctx, timeout=self.timeout)
        conn.login(self.username, self.password)
        self.stats["logins"] += 1
        if self.key:
            _LOGIN_TIMES.setdefault(self.key, []).append(time.time())
        self._announce(conn)
        try:
            conn.select(self.folder, readonly=True)
        except Exception:
            conn.select(self.folder)
        self.stats["connects"] += 1
        return conn

    def _announce(self, conn):
        """Some providers require an IMAP ID before they allow SELECT."""
        host = (self.host or '').lower()
        need = False
        for marker in ('163', '126', 'yeah'):
            if marker in host:
                need = True
                break
        if not need:
            return
        payload = '"name" "hermes-email-watchdog" "version" "1.0" "vendor" "dsh")'
        try:
            conn.xatom('ID', payload)
        except Exception:
            pass


    def _connection(self):
        if self._conn is None:
            self._conn = self._open()
        else:
            try:
                self._conn.noop()
            except Exception:
                self._close()
                self._conn = self._open()
        return self._conn

    def _close(self):
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.logout()
            except Exception:
                pass

    def close(self):
        with self._lock:
            self._close()

    def _run(self, fn, *args):
        with self._lock:
            for attempt in (1, 2):
                try:
                    return fn(self._connection(), *args)
                except Exception as exc:
                    self.stats["errors"] += 1
                    if _is_throttle(exc):
                        if self.key:
                            mark_throttled(self.key)
                        raise ImapThrottled(str(exc)[:200])
                    self._close()
                    if attempt == 2:
                        raise ImapSessionError(str(exc)[:200])
        raise ImapSessionError("unreachable")

    # -- operations -------------------------------------------------
    def list_recent(self, limit: int = 20) -> List[Dict[str, Any]]:
        return self._run(self._list_recent, int(limit))

    def _list_recent(self, conn, limit):
        typ, data = conn.uid("search", None, "ALL")
        if typ != "OK":
            raise RuntimeError("search failed: %s" % typ)
        uids = [u for u in (data[0] or b"").split() if u]
        if not uids:
            return []
        window = uids[-max(1, limit):]
        typ, fetched = conn.uid(
            "fetch", ",".join(u.decode() if isinstance(u, bytes) else str(u) for u in window),
            "(BODY.PEEK[HEADER.FIELDS (%s)] RFC822.SIZE BODYSTRUCTURE)" % _HEADER_FIELDS,
        )
        if typ != "OK":
            raise RuntimeError("fetch failed: %s" % typ)
        envelopes = []
        for item in fetched or []:
            if not isinstance(item, tuple) or len(item) < 2:
                continue
            meta, raw = item[0], item[1]
            if isinstance(meta, str):
                meta = meta.encode()
            uid = _uid_from_meta(meta)
            envelopes.append(_envelope(uid, raw, _size_from_meta(meta), _has_attachment(meta)))
        self.stats["lists"] += 1
        return envelopes

    def read_raw(self, uid):
        """Raw RFC 822 bytes, fetched with BODY.PEEK so nothing is marked read."""
        return self._run(self._read_raw, uid)

    def _read_raw(self, conn, uid):
        typ, data = conn.uid("fetch", str(uid), "(BODY.PEEK[])")
        if typ != "OK":
            raise RuntimeError("fetch failed: %s" % typ)
        for item in data or []:
            if isinstance(item, tuple) and len(item) >= 2:
                return item[1]
        return None

    def read_message(self, uid) -> Optional[Dict[str, Any]]:
        return self._run(self._read_message, uid)

    def _read_message(self, conn, uid):
        typ, data = conn.uid("fetch", str(uid), "(BODY.PEEK[])")
        if typ != "OK":
            raise RuntimeError("fetch failed: %s" % typ)
        raw = None
        for item in data or []:
            if isinstance(item, tuple) and len(item) >= 2:
                raw = item[1]
                break
        if raw is None:
            return None
        self.stats["reads"] += 1
        return _message(uid, raw)


def _uid_from_meta(meta: bytes) -> str:
    match = re.search(rb"UID\s+(\d+)", meta or b"")
    return match.group(1).decode() if match else ""


def _size_from_meta(meta: bytes) -> int:
    match = re.search(rb"RFC822\.SIZE\s+(\d+)", meta or b"")
    return int(match.group(1)) if match else 0


def _has_attachment(meta: bytes) -> bool:
    text = (meta or b"").upper()
    return b"ATTACHMENT" in text


def _addr(value: str) -> Dict[str, str]:
    text = str(value or "").strip()
    parsed = email.utils.getaddresses([text])
    if parsed:
        name, addr = parsed[0]
        return {"name": name or "", "addr": addr or ""}
    return {"name": "", "addr": text}


def _envelope(uid: str, raw_headers: bytes, size: int, has_attachment: bool) -> Dict[str, Any]:
    msg = email.message_from_bytes(raw_headers or b"", policy=email.policy.default)
    sender = _addr(msg.get("From", ""))
    return {
        "id": uid,
        "message_id": uid,
        "subject": str(msg.get("Subject", "") or ""),
        "from": sender,
        "to": _addr(msg.get("To", "")),
        "date": str(msg.get("Date", "") or ""),
        "message_id_header": str(msg.get("Message-ID", "") or ""),
        "has_attachment": has_attachment,
        "attachments": [],
        "size": size,
    }


def _body_text(msg) -> str:
    plain = ""
    html = ""
    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        try:
            payload = part.get_content()
        except Exception:
            try:
                payload = part.get_payload(decode=True).decode("utf-8", "replace")
            except Exception:
                continue
        if ctype == "text/plain" and not plain:
            plain = payload
        elif ctype == "text/html" and not html:
            html = payload
    if plain:
        return plain
    if html:
        text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
        text = re.sub(r"(?i)<br\s*/?>", "\n", text)
        text = re.sub(r"(?i)</p\s*>", "\n", text)
        text = re.sub(r"<[^>]+>", " ", text)
        return email.utils.unquote(text).strip()
    return ""


def _message(uid, raw: bytes) -> Dict[str, Any]:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    sender = _addr(msg.get("From", ""))
    attachments = []
    for part in msg.walk():
        filename = part.get_filename()
        if not filename:
            continue
        attachments.append({"filename": filename, "size_bytes": len(part.get_payload(decode=True) or b"")})
    received = ""
    try:
        received = parsedate_to_datetime(str(msg.get("Date", ""))).isoformat()
    except Exception:
        received = str(msg.get("Date", "") or "")
    return {
        "id": uid,
        "message_id": uid,
        "subject": str(msg.get("Subject", "") or ""),
        "from": sender,
        "to": _addr(msg.get("To", "")),
        "date_sent": str(msg.get("Date", "") or ""),
        "date_received": received,
        "text": _body_text(msg),
        "has_attachment": bool(attachments),
        "attachments": attachments,
    }

_SESSIONS = {}
_THROTTLED_UNTIL = {}
_LOGIN_TIMES = {}
_THROTTLE_HINTS = ("too many simultaneous", "rate limit", "throttl", "too many connections", "unavailable")
DEFAULT_THROTTLE_SECONDS = 180.0
DEFAULT_LOGIN_BUDGET = 20


class ImapThrottled(ImapSessionError):
    """The provider is refusing new sessions; do not open another one yet."""


def throttled_until(key):
    return float(_THROTTLED_UNTIL.get(str(key), 0.0))


def mark_throttled(key, seconds=DEFAULT_THROTTLE_SECONDS):
    _THROTTLED_UNTIL[str(key)] = time.time() + float(seconds)


def clear_throttle(key):
    _THROTTLED_UNTIL.pop(str(key), None)


def reset_sessions():
    for session in list(_SESSIONS.values()):
        try:
            session.close()
        except Exception:
            pass
    _SESSIONS.clear()
    _THROTTLED_UNTIL.clear()
    _LOGIN_TIMES.clear()


def get_session(key, host, port, username, password, folder="INBOX",
                budget_per_hour=DEFAULT_LOGIN_BUDGET, connector=None):
    """One session per account for the life of the process.

    The hook reloads email_watch every cycle but not this module, so this cache
    outlives a scan. A throttled provider is remembered too: while the cooldown
    is active no new login is attempted at all.
    """
    key = str(key)
    now = time.time()
    if _THROTTLED_UNTIL.get(key, 0) > now:
        raise ImapThrottled("throttled cooldown active for %s" % key)
    cached = _SESSIONS.get(key)
    if cached is not None:
        return cached
    if budget_per_hour:
        recent = [t for t in _LOGIN_TIMES.get(key, []) if now - t < 3600]
        _LOGIN_TIMES[key] = recent
        if len(recent) >= int(budget_per_hour):
            raise ImapThrottled("login budget exhausted for %s (%d/hour)" % (key, budget_per_hour))
    session = ImapSession(host, port, username, password, folder=folder, connector=connector)
    session.key = key
    _SESSIONS[key] = session
    return session

def reserve_login(key, budget_per_hour=DEFAULT_LOGIN_BUDGET):
    """Account for one login on a path that does not use a session.

    The himalaya fallback authenticates per command; it cannot share the cached
    session, so it must at least share the budget. Raises ImapThrottled when the
    hourly allowance is spent.
    """
    key = str(key)
    now = time.time()
    recent = [t for t in _LOGIN_TIMES.get(key, []) if now - t < 3600]
    if budget_per_hour and len(recent) >= int(budget_per_hour):
        _LOGIN_TIMES[key] = recent
        raise ImapThrottled("login budget exhausted for %s (%d/hour)" % (key, budget_per_hour))
    recent.append(now)
    _LOGIN_TIMES[key] = recent
    return len(recent)


def login_count(key):
    now = time.time()
    return len([t for t in _LOGIN_TIMES.get(str(key), []) if now - t < 3600])
