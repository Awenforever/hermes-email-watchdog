#!/usr/bin/env python3
"""Regression: one login must serve many list and read operations.

Every himalaya CLI call logs in again; a scan with six new messages cost seven
logins per cycle, which is exactly what providers throttle. This session must
authenticate once and then serve both listing and reading from that connection,
without ever mutating mailbox state.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import email_imap_session as sess


RAW = (
    b"From: Bybit <noreply@email-service.bybit.com>\r\n"
    b"To: me@example.com\r\n"
    b"Subject: [Bybit] OTP\r\n"
    b"Date: Sat, 07 Nov 2026 05:05:39 +0000\r\n"
    b"Message-ID: <abc@example>\r\n"
    b"MIME-Version: 1.0\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
    b"your code is 482913\r\n"
)

HEADERS = RAW.split(b"\r\n\r\n")[0] + b"\r\n\r\n"


class FakeConn:
    def __init__(self, uids=(b"4", b"5", b"6"), body=RAW):
        self.uids = list(uids)
        self.body = body
        self.logins = 0
        self.logouts = 0
        self.commands = []
        self.noops = 0
        self.selected = None

    def login(self, user, password):
        self.logins += 1

    def select(self, folder, readonly=False):
        self.selected = (folder, readonly)
        return ("OK", [b"1"])

    def noop(self):
        self.noops += 1
        return ("OK", [b"noop"])

    def logout(self):
        self.logouts += 1

    def uid(self, command, *args):
        self.commands.append((command, args))
        if command == "search":
            return ("OK", [b" ".join(self.uids)])
        if command == "fetch":
            target = str(args[0])
            if "HEADER.FIELDS" in str(args[1]):
                return ("OK", [(b'5 (UID 5 RFC822.SIZE 120 BODYSTRUCTURE ("TEXT" "PLAIN" "PLAIN" NIL))', HEADERS)])
            return ("OK", [(b"5 (UID 5 BODY[] {%d}" % len(self.body), self.body)])
        return ("OK", [b""])


class SessionTests(unittest.TestCase):
    def _session(self, conn):
        s = sess.ImapSession("imap.example", 993, "u", "p", connector=lambda *a, **k: conn)
        return s

    def test_one_login_serves_list_and_read(self):
        conn = FakeConn()
        s = self._session(conn)
        envelopes = s.list_recent(10)
        self.assertEqual(len(envelopes), 1)
        s.read_message("5")
        s.read_message("5")
        self.assertEqual(conn.logins, 1, "each operation must reuse the single connection")
        self.assertEqual(s.stats["connects"], 1)
        self.assertEqual(s.stats["reads"], 2)

    def test_envelope_shape(self):
        conn = FakeConn()
        env = self._session(conn).list_recent(5)[0]
        self.assertEqual(env["id"], "5")
        self.assertEqual(env["subject"], "[Bybit] OTP")
        self.assertEqual(env["from"], {"name": "Bybit", "addr": "noreply@email-service.bybit.com"})

    def test_read_never_sets_seen(self):
        conn = FakeConn()
        self._session(conn).read_message("5")
        fetch_args = [c for c in conn.commands if c[0] == "fetch"]
        self.assertTrue(fetch_args)
        for _cmd, args in fetch_args:
            self.assertIn("PEEK", str(args), "bodies must be fetched with BODY.PEEK")
        self.assertEqual(conn.selected, ("INBOX", True))

    def test_message_text(self):
        conn = FakeConn()
        msg = self._session(conn).read_message("5")
        self.assertIn("482913", msg["text"])
        self.assertTrue(msg["date_received"].startswith("2026-11-07"))

    def test_reconnects_after_failure(self):
        class Flaky(FakeConn):
            def __init__(self):
                super().__init__()
                self.calls = 0

            def uid(self, command, *args):
                self.calls += 1
                if self.calls == 1:
                    raise OSError("connection reset")
                return super().uid(command, *args)

        conn = Flaky()
        envelopes = self._session(conn).list_recent(5)
        self.assertEqual(len(envelopes), 1)
        self.assertGreaterEqual(conn.logins, 2)


if __name__ == "__main__":
    unittest.main()
