#!/usr/bin/env python3
"""Unmocked integration test for reminder activation (objective item f).

Drives the real ``_activate_reminders`` path against a real SQLite DB (no store
mocking) to prove:
  * the transient ``reminders`` key no longer becomes a phantom column and the
    INSERT/UPDATE succeed (regression for the 100%-crash bug);
  * a missing/pruned ``messages`` row (the schedules.message_id FK target) is
    auto-created so activation is not silently lost;
  * re-activation is idempotent (one active row).
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="qoder-email-f-"))
os.environ["HERMES_EMAIL_WATCHDOG_STATE_ROOT"] = str(_TMP)
os.environ.pop("EMAIL_WATCHDOG_CONFIG", None)

import email_store  # noqa: E402
import email_actions  # noqa: E402


class ReminderActivationTests(unittest.TestCase):
    def setUp(self):
        email_store.init_db()
        conn = email_store._get_conn()
        conn.execute("DELETE FROM schedules")
        conn.execute("DELETE FROM messages")
        conn.commit()

    def _action(self, msg_id):
        return {
            "message_id": msg_id,
            "account": "USTC",
            "account_type": "himalaya",
            "subject": "test subject",
            "from_addr": "someone@example.com",
            "from_name": "Someone",
            "deadlines": [
                {
                    "id": f"{msg_id}:main",
                    "message_id": msg_id,
                    "title": "pay tuition",
                    "action_needed": "pay",
                    "deadline": "2099-01-01T09:00:00+08:00",
                    "timezone": "Asia/Shanghai",
                    "status": "offered",
                    "reminders": [{"kind": "offset", "offset_minutes": 1440}],
                }
            ],
        }

    def test_01_activation_inserts_real_schedule_row(self):
        msg_id = "itest-msg-0001"
        result = email_actions._activate_reminders(self._action(msg_id), [1])
        self.assertTrue(result.startswith("\u2705 \u5df2\u521b\u5efa\u90ae\u4ef6\u63d0\u9192"), result)
        rows = email_store.get_schedules(status="active")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["id"], f"USTC:{msg_id}:main")
        self.assertEqual(row["account"], "USTC")
        self.assertEqual(row["message_id"], msg_id)
        self.assertNotIn("reminders", row)

    def test_02_fk_target_auto_created_when_message_row_missing(self):
        msg_id = "itest-msg-orphan"
        self.assertIsNone(email_store.get_message(msg_id))
        result = email_actions._activate_reminders(self._action(msg_id), [1])
        self.assertTrue(result.startswith("\u2705 \u5df2\u521b\u5efa\u90ae\u4ef6\u63d0\u9192"), result)
        self.assertIsNotNone(email_store.get_message(msg_id))
        self.assertEqual(len(email_store.get_schedules(status="active")), 1)

    def test_03_reactivation_is_idempotent(self):
        msg_id = "itest-msg-idem"
        email_actions._activate_reminders(self._action(msg_id), [1])
        email_actions._activate_reminders(self._action(msg_id), [1])
        self.assertEqual(len(email_store.get_schedules(status="active")), 1)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ReminderActivationTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    ok = result.testsRun - len(result.failures) - len(result.errors)
    print(f"REMINDER_ACTIVATION_INTEGRATION={ok}/{result.testsRun}")
    raise SystemExit(0 if result.wasSuccessful() else 1)
