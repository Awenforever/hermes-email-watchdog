#!/usr/bin/env python3
"""Regression: the audit findings that could silently stop a mailbox.

Three of them shared one shape: the watchdog looked healthy while an account did
no work. These tests pin the fixes.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

_TMP = Path(tempfile.mkdtemp(prefix="audit-"))
os.environ["HERMES_EMAIL_WATCHDOG_STATE_ROOT"] = str(_TMP)
os.environ["HERMES_EMAIL_WATCHDOG_OPERATION"] = "readonly"

import email_imap_session as sess  # noqa: E402
import email_idle_watch as idle  # noqa: E402


class _Conn:
    def __init__(self):
        self.logins = 0
        self.commands = []

    def login(self, user, password):
        self.logins += 1

    def select(self, folder, readonly=False):
        return ("OK", [b"1"])

    def noop(self):
        return ("OK", [b""])

    def logout(self):
        pass

    def xatom(self, name, *args):
        self.commands.append((name, args))
        return ("OK", [b"ID completed"])

    def uid(self, command, *args):
        if command == "search":
            return ("OK", [b"1"])
        return ("OK", [(b"1 (UID 1 BODY[] {5}", b"hello")])


class AuditRegressionTests(unittest.TestCase):
    def setUp(self):
        sess.reset_sessions()
        sess.set_state_path(_TMP / "login_budget.json")

    def tearDown(self):
        sess.reset_sessions()
        sess.set_state_path(None)

    def test_imap_id_payload_is_well_formed(self):
        conn = _Conn()
        session = sess.ImapSession("imap.163.com", 993, "u", "p", key="163",
                                  connector=lambda *a, **k: conn)
        session._open()
        calls = [c for c in conn.commands if c[0] == "ID"]
        self.assertTrue(calls, "163 requires an ID command before SELECT")
        payload = calls[0][1][0]
        self.assertTrue(payload.startswith("("), payload)
        self.assertTrue(payload.endswith(")"), payload)
        self.assertEqual(payload.count("("), payload.count(")"), payload)

    def test_reconnect_respects_the_login_budget(self):
        conn = _Conn()
        session = sess.get_session("k", "h", 993, "u", "p", budget_per_hour=1,
                                   connector=lambda *a, **k: conn)
        session._open()  # first login, budget now spent
        with self.assertRaises(sess.ImapThrottled):
            session._open()

    def test_watcher_never_falls_back_to_a_private_login(self):
        account = {
            "label": "Gmail",
            "id": "gmail",
            "idle_iteration": 1,
            "himalaya_config": str(_TMP / "missing.toml"),
        }
        built = idle.watcher_from_account(account, wake=lambda: None)
        self.assertIsNone(built, "a watcher must not open its own login behind the budget")


if __name__ == "__main__":
    unittest.main()
