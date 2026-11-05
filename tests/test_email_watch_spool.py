#!/usr/bin/env python3
"""Regression: incremental notification spool.

The watchdog used to hold every notification until the whole scan returned, so
an early email waited behind later ones. The scan now appends each notification
to a spool file that the hook drains while the scan is still running. These
tests pin the writer contract: one JSON object per line, text plus metadata, and
a silent no-op when the hook did not arm a spool path.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

_TMP = Path(tempfile.mkdtemp(prefix="qoder-email-spool-"))
os.environ["HERMES_EMAIL_WATCHDOG_STATE_ROOT"] = str(_TMP)
os.environ.pop("EMAIL_WATCHDOG_CONFIG", None)

import email_watch  # noqa: E402


class NotificationSpoolTests(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.get("EMAIL_WATCHDOG_NOTIFY_SPOOL")
        os.environ.pop("EMAIL_WATCHDOG_NOTIFY_SPOOL", None)

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("EMAIL_WATCHDOG_NOTIFY_SPOOL", None)
        else:
            os.environ["EMAIL_WATCHDOG_NOTIFY_SPOOL"] = self._saved

    def test_appends_one_json_object_per_notification(self):
        path = _TMP / "spool.jsonl"
        os.environ["EMAIL_WATCHDOG_NOTIFY_SPOOL"] = str(path)
        email_watch._spool_alert("第一封", {"model_name": "m1", "mail_actions": []})
        email_watch._spool_alert("第二封", {"model_name": "m2"})
        lines = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        first = json.loads(lines[0])
        self.assertEqual(first["text"], "第一封")
        self.assertEqual(first["metadata"]["model_name"], "m1")
        self.assertIn("ts", first)

    def test_no_op_when_hook_did_not_arm_a_spool(self):
        email_watch._spool_alert("ignored", {})  # must not raise

    def test_empty_text_is_not_written(self):
        path = _TMP / "spool-empty.jsonl"
        os.environ["EMAIL_WATCHDOG_NOTIFY_SPOOL"] = str(path)
        email_watch._spool_alert("", {"model_name": "m"})
        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
