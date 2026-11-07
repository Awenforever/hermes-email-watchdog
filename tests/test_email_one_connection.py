#!/usr/bin/env python3
"""Regression: watching must not cost a second connection.

The idle watcher used to hold its own connection, so every account cost two: one
for the NOOP loop and one for fetching. Both are the same mailbox, so one
authenticated session can serve both, which keeps login and connection counts at
the minimum.
"""
from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import email_imap_session as sess


RAW = (
    b"From: a@example.com\r\nSubject: hi\r\n\r\nbody\r\n"
)


class FakeConn:
    def __init__(self, counts=(1, 1, 2)):
        self.counts = list(counts)
        self.logins = 0
        self.logouts = 0
        self.noop_calls = 0
        self.idx = 0

    def login(self, user, password):
        self.logins += 1

    def select(self, folder, readonly=False):
        return ("OK", [b"1"])

    def noop(self):
        self.noop_calls += 1
        return ("OK", [b""])

    def logout(self):
        self.logouts += 1

    def uid(self, command, *args):
        if command == "search":
            n = self.counts[min(self.idx, len(self.counts) - 1)]
            self.idx += 1
            return ("OK", [b" ".join(b"%d" % i for i in range(1, n + 1))])
        if command == "fetch":
            return ("OK", [(b"1 (UID 1 BODY[] {%d}" % len(RAW), RAW)])
        return ("OK", [b""])


class OneConnectionTests(unittest.TestCase):
    def tearDown(self):
        sess.reset_sessions()

    def test_watcher_and_fetch_share_one_login(self):
        conn = FakeConn()
        session = sess.get_session("Gmail", "imap.example", 993, "u", "p",
                                   connector=lambda *a, **k: conn)
        fired = threading.Event()
        session.start_watcher(fired.set, interval=1.0)
        try:
            self.assertTrue(fired.wait(8), "watcher never woke the scan")
            session.list_recent(5)
        finally:
            session.stop_watcher()
        self.assertEqual(conn.logins, 1, "watching and fetching must share one login")
        self.assertEqual(session.stats["connects"], 1)
        self.assertEqual(session.stats["wakes"], 1)

    def test_cache_returns_same_session(self):
        conn = FakeConn()
        a = sess.get_session("k1", "h", 993, "u", "p", connector=lambda *x, **y: conn)
        b = sess.get_session("k1", "h", 993, "u", "p", connector=lambda *x, **y: conn)
        self.assertIs(a, b)

    def test_throttle_blocks_new_sessions(self):
        sess.mark_throttled("k2", 60)
        with self.assertRaises(sess.ImapThrottled):
            sess.get_session("k2", "h", 993, "u", "p")


if __name__ == "__main__":
    unittest.main()
