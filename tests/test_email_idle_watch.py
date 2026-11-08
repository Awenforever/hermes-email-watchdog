#!/usr/bin/env python3
"""Regression: the production watcher never opens its own connection.

watcher_from_account only ever returns a SessionWatcher; when a session cannot
be built it returns None so scanning covers the account. The old
PersistentImapWatcher class logged in on its own and bypassed the budget, so it
is gone and this test pins that it cannot come back.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import email_idle_watch as idle


class SessionWatcherTests(unittest.TestCase):
    def test_legacy_self_login_watcher_is_gone(self):
        self.assertFalse(hasattr(idle, "PersistentImapWatcher"))

    def test_account_without_opt_in_is_ignored(self):
        self.assertIsNone(idle.watcher_from_account({"label": "Gmail"}))

    def test_missing_config_yields_no_watcher(self):
        account = {"label": "Gmail", "id": "gmail", "idle_watch": True,
                   "himalaya_config": "/tmp/definitely-missing.toml"}
        self.assertIsNone(
            idle.watcher_from_account(account, wake=lambda: None),
            "no session means no watcher, never a private login",
        )


if __name__ == "__main__":
    unittest.main()
