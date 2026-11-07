#!/usr/bin/env python3
"""Regression: the login budget must survive a process restart.

It used to live only in memory, so a restart reset the allowance and the same
account could be pushed past a provider's limit again and again. The budget now
lives in a file next to the database.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import email_imap_session as sess


class _Conn:
    def __init__(self):
        self.logins = 0

    def login(self, user, password):
        self.logins += 1

    def select(self, folder, readonly=False):
        return ("OK", [b"1"])

    def noop(self):
        return ("OK", [b""])

    def logout(self):
        pass

    def uid(self, command, *args):
        return ("OK", [b""])


class PersistedBudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="budget-"))
        self.state = self.tmp / "login_budget.json"
        sess.reset_sessions()
        sess.set_state_path(self.state)

    def tearDown(self):
        sess.reset_sessions()
        sess.set_state_path(None)

    def test_budget_file_written_on_login(self):
        conn = _Conn()
        session = sess.get_session("Gmail", "h", 993, "u", "p", connector=lambda *a, **k: conn)
        session.list_recent(1)
        self.assertTrue(self.state.exists())
        self.assertIn("Gmail", self.state.read_text(encoding="utf-8"))

    def test_restart_keeps_the_allowance(self):
        conn = _Conn()
        for _ in range(3):
            sess.reset_sessions()
            sess.set_state_path(self.state)
            session = sess.get_session("Gmail", "h", 993, "u", "p", connector=lambda *a, **k: conn)
            session.list_recent(1)
        self.assertEqual(sess.login_count("Gmail"), 3)

    def test_exhausted_budget_survives_restart(self):
        for _ in range(2):
            sess.reserve_login("Gmail", budget_per_hour=2)
        with self.assertRaises(sess.ImapThrottled):
            sess.reserve_login("Gmail", budget_per_hour=2)
        # simulate a restart
        sess.reset_sessions()
        sess.set_state_path(self.state)
        with self.assertRaises(sess.ImapThrottled):
            sess.reserve_login("Gmail", budget_per_hour=2)

    def test_old_entries_expire(self):
        stale = time.time() - 7200
        self.state.write_text('{"logins": {"Gmail": [%f]}}' % stale, encoding="utf-8")
        sess.set_state_path(self.state)
        self.assertEqual(sess.login_count("Gmail"), 0)


if __name__ == "__main__":
    unittest.main()
