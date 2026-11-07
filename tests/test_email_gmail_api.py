#!/usr/bin/env python3
"""Regression: Gmail REST provider shapes must match the IMAP provider."""
from __future__ import annotations

import base64
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import email_gmail_api as api


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


MESSAGE = {
    "id": "18f0",
    "threadId": "t-1",
    "internalDate": "1794027940000",
    "payload": {
        "mimeType": "multipart/mixed",
        "headers": [
            {"name": "From", "value": "Bybit <noreply@email-service.bybit.com>"},
            {"name": "To", "value": "me@example.com"},
            {"name": "Subject", "value": "[Bybit]邮箱验证码"},
            {"name": "Date", "value": "Sat, 07 Nov 2026 05:05:39 +0000"},
        ],
        "parts": [
            {"mimeType": "text/plain", "body": {"data": _b64("验证码 482913")}},
            {"mimeType": "application/pdf", "filename": "proof.pdf", "body": {"size": 1024, "attachmentId": "a1"}},
        ],
    },
}


class FakeRequest:
    def __init__(self, payload): self._payload = payload
    def execute(self): return self._payload


class FakeUsers:
    def __init__(self): self.calls = []
    def messages(self): return self


class FakeService:
    def __init__(self, listing, messages): self._listing = listing; self._messages = messages; self.fetched = []
    def users(self): return self
    def messages(self): return self
    def list(self, **kw):
        self._list_kw = kw
        return FakeRequest(self._listing)
    def get(self, **kw):
        self.fetched.append(kw)
        return FakeRequest(self._messages.get(kw.get("id"), {}))


class GmailApiShapeTests(unittest.TestCase):
    def test_envelope_matches_imap_shape(self):
        env = api.envelope_from_message(MESSAGE)
        self.assertEqual(env["id"], "18f0")
        self.assertEqual(env["from"]["addr"], "noreply@email-service.bybit.com")
        self.assertEqual(env["from"]["name"], "Bybit")
        self.assertEqual(env["subject"], "[Bybit]邮箱验证码")
        self.assertTrue(env["has_attachment"])
        self.assertEqual(env["attachments"][0]["filename"], "proof.pdf")

    def test_body_prefers_plain_text(self):
        self.assertEqual(api.extract_body(MESSAGE), "验证码 482913")

    def test_html_used_when_no_plain(self):
        msg = {"payload": {"mimeType": "text/html", "body": {"data": _b64("<p>hello</p><br>world")}}}
        self.assertIn("hello", api.extract_body(msg))
        self.assertIn("world", api.extract_body(msg))

    def test_read_message_returns_text_and_received(self):
        svc = FakeService({}, {"18f0": MESSAGE})
        out = api.read_message(svc, "18f0")
        self.assertEqual(out["text"], "验证码 482913")
        self.assertTrue(out["date_received"].startswith("2026-11-07"))

    def test_list_messages_fetches_metadata_per_id(self):
        svc = FakeService({"messages": [{"id": "18f0"}]}, {"18f0": MESSAGE})
        envs = api.list_messages(svc, max_results=5)
        self.assertEqual(len(envs), 1)
        self.assertEqual(svc._list_kw["maxResults"], 5)
        self.assertEqual(svc.fetched[0]["format"], "metadata")

    def test_not_configured_without_files(self):
        os.environ["HERMES_EMAIL_WATCHDOG_STATE_ROOT"] = "/tmp/definitely-missing-gmail-api"
        os.environ.pop("EMAIL_GMAIL_CLIENT_SECRET", None)
        os.environ.pop("EMAIL_GMAIL_TOKEN", None)
        self.assertFalse(api.is_configured())


if __name__ == "__main__":
    unittest.main()
