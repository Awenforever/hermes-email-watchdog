#!/usr/bin/env python3
"""Regression: the gaps an audit of the test suite itself found.

These exist because the earlier versions of these tests passed while the code
they claimed to protect was wrong: a cooldown test that never checked whether a
socket was dialed, a watcher test that returned early, and no coverage at all for
messages dropped for a missing UID.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

_TMP = Path(tempfile.mkdtemp(prefix="audit2-"))
os.environ["HERMES_EMAIL_WATCHDOG_STATE_ROOT"] = str(_TMP)
os.environ["HERMES_EMAIL_WATCHDOG_OPERATION"] = "readonly"

import email_imap_session as sess  # noqa: E402


class _Conn:
    def __init__(self, header_meta=b'5 (UID 5 RFC822.SIZE 10 BODYSTRUCTURE ("TEXT" "PLAIN" "PLAIN" NIL))'):
        self.logins = 0
        self.closed = 0
        self.header_meta = header_meta
        self.commands = []

    def login(self, user, password):
        self.logins += 1

    def select(self, folder, readonly=False):
        return ("OK", [b"1"])

    def response(self, name):
        return ("UIDVALIDITY", [b"7"])

    def noop(self):
        return ("OK", [b""])

    def logout(self):
        self.closed += 1

    def xatom(self, *a):
        return ("OK", [b""])

    def uid(self, command, *args):
        self.commands.append(command)
        if command == "search":
            return ("OK", [b"1"])
        if "HEADER.FIELDS" in str(args):
            return ("OK", [(self.header_meta, b"Subject: x\r\n\r\n")])
        return ("OK", [(b"1 (UID 1 BODY[] {2}", b"hi")])


class _CountingConnector:
    def __init__(self, conn):
        self.conn = conn
        self.dials = 0

    def __call__(self, *a, **k):
        self.dials += 1
        return self.conn


class CooldownGateTests(unittest.TestCase):
    def setUp(self):
        sess.reset_sessions()
        sess.set_state_path(_TMP / "login_budget.json")

    def tearDown(self):
        sess.reset_sessions()
        sess.set_state_path(None)

    def test_cooldown_refuses_before_dialing(self):
        conn = _Conn()
        connector = _CountingConnector(conn)
        session = sess.ImapSession("h", 993, "u", "p", key="k1", connector=connector)
        session.budget_per_hour = 60
        sess.mark_throttled("k1", 60)
        with self.assertRaises(sess.ImapThrottled):
            session._open()
        self.assertEqual(connector.dials, 0, "must not open a socket while cooling down")
        self.assertEqual(conn.logins, 0)

    def test_budget_refuses_before_dialing(self):
        conn = _Conn()
        connector = _CountingConnector(conn)
        session = sess.ImapSession("h", 993, "u", "p", key="k2", connector=connector)
        session.budget_per_hour = 1
        session._open()  # consumes the single login
        before = connector.dials
        with self.assertRaises(sess.ImapThrottled):
            session._open()
        self.assertEqual(connector.dials, before, "exhausted budget must not dial again")


class MissingUidTests(unittest.TestCase):
    def setUp(self):
        sess.reset_sessions()

    def tearDown(self):
        sess.reset_sessions()

    def test_envelope_without_uid_is_dropped_and_counted(self):
        conn = _Conn(header_meta=b'5 (RFC822.SIZE 10 BODYSTRUCTURE ("TEXT" "PLAIN" "PLAIN" NIL))')
        session = sess.ImapSession("h", 993, "u", "p", connector=lambda *a, **k: conn)
        envelopes = session.list_recent(5)
        self.assertEqual(envelopes, [], "an envelope with no UID must never be returned")
        self.assertEqual(session.stats.get("missing_uid"), 1)


class SkippedAccountLogTests(unittest.TestCase):
    def test_skipped_accounts_are_named(self):
        import email_watch
        source = Path(email_watch.__file__).read_text(encoding="utf-8")
        self.assertIn("accounts not checked this scan", source)
        self.assertIn("skipped_accounts", source)


if __name__ == "__main__":
    unittest.main()
