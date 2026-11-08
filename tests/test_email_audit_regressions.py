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


    def test_cooldown_blocks_fallback_login(self):
        """A throttled account must not be re-entered by the CLI fallback."""
        sess.mark_throttled("k3", 60)
        with self.assertRaises(sess.ImapThrottled):
            sess.reserve_login("k3", budget_per_hour=60)

    def test_cooldown_blocks_reconnect(self):
        """The session itself must refuse to reconnect while cooling down."""
        conn = _Conn()
        session = sess.ImapSession("h", 993, "u", "p", key="k4", connector=lambda *a, **k: conn)
        session.budget_per_hour = 60
        sess.mark_throttled("k4", 60)
        with self.assertRaises(sess.ImapThrottled):
            session._open()


    def test_uidvalidity_change_clears_seen_for_that_account(self):
        import email_watch
        state = _TMP / 'uidvalidity.json'
        seen_file = _TMP / 'seen.json'
        class _Sess:
            uidvalidity = 111
        original_load = email_watch.load_seen
        original_save = email_watch.save_seen
        store = {'Gmail:1': True, 'Gmail:2': True, 'USTC:9': True}
        email_watch.load_seen = lambda: dict(store)
        email_watch.save_seen = lambda s: store.clear() or store.update(s)
        try:
            state.write_text('{"Gmail": 100}', encoding='utf-8')
            _orig = email_watch._uidvalidity_path
            email_watch._uidvalidity_path = lambda: state
            email_watch._check_uidvalidity('Gmail', _Sess())
            email_watch._uidvalidity_path = _orig
        finally:
            email_watch.load_seen = original_load
            email_watch.save_seen = original_save
        self.assertNotIn('Gmail:1', store, 'rebuilt mailbox must not keep suppressing mail')
        self.assertIn('USTC:9', store, 'other accounts untouched')
        self.assertIn('"Gmail": 111', state.read_text(encoding='utf-8'))

    def test_attachment_download_is_charged(self):
        """A CLI attachment fetch is a real login and must hit the budget."""
        import email_watch, email_delivery
        calls = []
        original = email_watch._charge_himalaya_login
        email_watch._charge_himalaya_login = lambda: calls.append(1)
        original_key = email_watch._HIMALAYA_BUDGET_KEY
        email_watch._HIMALAYA_BUDGET_KEY = 'gmail'
        try:
            try:
                email_delivery._download_himalaya('/tmp/none.toml', '1', str(_TMP / 'attach-out'))
            except Exception:
                pass  # the CLI itself is absent here; the charge must happen anyway
        finally:
            email_watch._charge_himalaya_login = original
            email_watch._HIMALAYA_BUDGET_KEY = original_key
        self.assertTrue(calls, 'attachment fetch must be charged')

if __name__ == "__main__":
    unittest.main()
