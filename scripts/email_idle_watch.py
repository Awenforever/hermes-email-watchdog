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


class PersistentImapWatcher:
    """Watch one mailbox over a single long-lived IMAP connection."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        *,
        wake: Optional[Callable[[], None]] = None,
        interval: float = DEFAULT_INTERVAL,
        timeout: float = 60.0,
        connector: Optional[Callable[..., Any]] = None,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.username = username
        self.password = password
        self.wake = wake
        self.interval = max(1.0, float(interval))
        self.timeout = float(timeout)
        self._connector = connector or imaplib.IMAP4_SSL
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.stats: Dict[str, Any] = {
            "connects": 0, "errors": 0, "wakes": 0, "last_error": "",
            "baseline": None, "last_count": None,
        }

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="imap-watch", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=5)

    def _connect(self):
        ctx = ssl.create_default_context()
        return self._connector(self.host, self.port, ssl_context=ctx, timeout=self.timeout)

    def _count(self, conn) -> int:
        typ, data = conn.search(None, "ALL")
        if typ != "OK":
            raise RuntimeError("search failed: %s" % typ)
        return len((data[0] or b"").split())

    def _run(self) -> None:
        backoff = self.interval
        while not self._stop.is_set():
            conn = None
            try:
                conn = self._connect()
                conn.login(self.username, self.password)
                try:
                    conn.select("INBOX", readonly=True)
                except Exception:
                    # Some servers reject EXAMINE; fall back to SELECT so the
                    # watcher does not flap between connect and retry forever.
                    conn.select("INBOX")
                self.stats["connects"] += 1
                baseline = self._count(conn)
                self.stats["baseline"] = baseline
                self.stats["last_count"] = baseline
                backoff = self.interval
                while not self._stop.is_set():
                    if self._stop.wait(self.interval):
                        break
                    conn.noop()
                    current = self._count(conn)
                    self.stats["last_count"] = current
                    if current > baseline:
                        baseline = current
                        self.stats["wakes"] += 1
                        if self.wake:
                            try:
                                self.wake()
                            except Exception:
                                logger.exception("imap watcher wake callback failed")
            except Exception as exc:
                self.stats["errors"] += 1
                self.stats["last_error"] = "%s: %s" % (type(exc).__name__, str(exc)[:200])
                if _is_throttle(exc):
                    # A throttled provider must not be re-entered by a reconnect loop:
                    # every attempt is another login and keeps the throttle warm.
                    try:
                        import email_imap_session
                        email_imap_session.mark_throttled(self.username)
                        wait = email_imap_session.DEFAULT_THROTTLE_SECONDS
                    except Exception:
                        wait = DEFAULT_MAX_BACKOFF
                    if self._stop.wait(wait):
                        break
                    continue
                self.stats["last_error"] = "%s: %s" % (type(exc).__name__, str(exc)[:200])
                logger.warning("imap watcher %s:%s reconnect after error: %s", self.host, self.port, self.stats["last_error"])
                if self._stop.wait(backoff):
                    break
                backoff = min(DEFAULT_MAX_BACKOFF, backoff * 2)
            finally:
                if conn is not None:
                    try:
                        conn.logout()
                    except Exception:
                        pass


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
    return PersistentImapWatcher(
        host, port, login, password, wake=wake,
        interval=float(account.get("idle_interval") or DEFAULT_INTERVAL),
        connector=connector,
    )