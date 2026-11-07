#!/usr/bin/env python3
"""Regression: the persistent IMAP watcher must wake the scan, never mutate."""
from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import email_idle_watch as w


class FakeConn:
    def __init__(self, counts, store=None):
        self._counts = list(counts)
        self._store = store if store is not None else {}
        self._store.setdefault("conn", self)
        self.logged_in = False
        self.selected = None
        self.noops = 0
        self.logged_out = False

    def login(self, user, password):
        self._store["login"] = (user, password)
        self.logged_in = True

    def select(self, mailbox, readonly=False):
        self.selected = (mailbox, readonly)
        return ("OK", [b"1"])

    def search(self, charset, criteria):
        idx = min(self.noops, len(self._counts) - 1)
        n = self._counts[idx]
        return ("OK", [b" ".join(b"%d" % i for i in range(1, n + 1))])

    def noop(self):
        self.noops += 1
        return ("OK", [b"noop"])

    def logout(self):
        self.logged_out = True


class WatcherTests(unittest.TestCase):
    def test_wake_fires_when_count_grows(self):
        store = {}
        created = []

        def connector(host, port, ssl_context=None, timeout=None):
            conn = FakeConn([1, 1, 2], store)
            created.append((host, port, conn))
            return conn

        fired = threading.Event()
        watcher = w.PersistentImapWatcher(
            "imap.example", 993, "u", "p",
            wake=fired.set, interval=1.0, connector=connector,
        )
        watcher.start()
        try:
            self.assertTrue(fired.wait(8), "watcher never woke the scan")
        finally:
            watcher.stop()
        self.assertEqual(created[0][0], "imap.example")
        self.assertEqual(store["login"], ("u", "p"))
        self.assertEqual(watcher.stats["wakes"], 1)
        self.assertEqual(watcher.stats["connects"], 1)

    def test_read_only_selection(self):
        store = {}
        conn = FakeConn([5], store)
        watcher = w.PersistentImapWatcher(
            "imap.example", 993, "u", "p", interval=1.0,
            connector=lambda *a, **k: conn,
        )
        watcher.start()
        time.sleep(1.5)
        watcher.stop()
        self.assertEqual(conn.selected, ("INBOX", True))
        self.assertTrue(conn.logged_out)

    def test_account_without_opt_in_is_ignored(self):
        self.assertIsNone(w.watcher_from_account({"label": "Gmail"}))


if __name__ == "__main__":
    unittest.main()