#!/usr/bin/env python3
"""Persistent IMAP watcher for accounts that must not be re-logged every poll.

Every poll through the tunnel costs a fresh TLS handshake plus a Gmail login.
Repeated logins are what Gmail throttles ("Too many simultaneous connections"),
and a throttled login silently delays the next scan. This watcher keeps ONE
connection open, notices new mail with a cheap NOOP + COUNT, and wakes the scan
loop immediately instead of waiting for the next interval.

It never mutates the mailbox: no STORE, no flags, no expunge - read-only count.
"""
from __future__ import annotations

import imaplib
import logging
import ssl
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL = 5.0
DEFAULT_MAX_BACKOFF = 60.0


def _password_from_cmd(cmd: str) -> str:
    if not cmd:
        return ""
    try:
        out = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=20)
        return (out.stdout or "").strip()
    except Exception:
        return ""


def _is_throttle(exc):
    text = str(exc or '').lower()
    for hint in ("too many simultaneous", "rate limit", "throttl", "too many connections"):
        if hint in text:
            return True
    return False


class SessionWatcher:
    """Wake the scan from the shared session instead of opening another login."""

    def __init__(self, session, wake=None, interval=DEFAULT_INTERVAL):
        self.session = session
        self.wake = wake
        self.interval = max(1.0, float(interval))

    @property
    def stats(self):
        return self.session.stats

    def start(self):
        self.session.start_watcher(self.wake, self.interval)

    def stop(self):
        self.session.stop_watcher()


def watcher_from_account(account: Dict[str, Any], wake=None, connector=None):
    """Build a watcher from a plugin account entry, or return None.

    Only accounts that opt in (idle_watch: true) get one; everything else keeps
    the plain polling behaviour.
    """
    if not isinstance(account, dict) or not account.get("idle_watch"):
        return None
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover
        import tomli as tomllib  # type: ignore
    path = account.get("himalaya_config") or account.get("config")
    if not path or not Path(str(path)).is_file():
        return None
    data = tomllib.loads(Path(str(path)).read_text(encoding="utf-8"))
    accounts = data.get("accounts") or {}
    alias = str(account.get("himalaya_account") or "").strip()
    entry = accounts.get(alias) if alias else None
    if entry is None:
        entry = next((v for v in accounts.values() if isinstance(v, dict)), None)
    if not isinstance(entry, dict):
        return None
    backend = entry.get("backend") or {}
    host = str(backend.get("host") or "")
    port = int(backend.get("port") or 993)
    login = str(backend.get("login") or "")
    auth = backend.get("auth") or {}
    cmd = str(auth.get("cmd") or "")
    password = _password_from_cmd(cmd) if cmd else ""
    if not host or not login or not password:
        return None
    try:
        import email_imap_session
        key = str(account.get('id') or account.get('label') or account.get('name') or '')
        session = email_imap_session.get_session(key, host, port, login, password)
        return SessionWatcher(
            session, wake=wake,
            interval=float(account.get('idle_interval') or DEFAULT_INTERVAL),
        )
    except Exception:
        # Never fall back to a watcher that logs in on its own: it would bypass
        # the shared budget and quietly reopen the connection churn this whole
        # design exists to prevent. Without a session, scanning still covers it.
        return None
    return None