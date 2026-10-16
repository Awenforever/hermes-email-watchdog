#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import email_delivery
import email_watch


class DueAlertObservabilityTests(unittest.TestCase):
    def test_collection_failure_is_logged_not_silently_swallowed(self):
        with mock.patch.object(
            email_delivery, "collect_due_reminder_notifications", side_effect=RuntimeError("database is locked")
        ):
            with self.assertLogs("email_watch", level="WARNING") as captured:
                alerts = email_watch._due_reminder_alerts()
        self.assertEqual(alerts, [])
        self.assertTrue(
            any("due-reminder collection failed" in line for line in captured.output),
            captured.output,
        )

    def test_due_alerts_pass_through_on_success(self):
        with mock.patch.object(
            email_delivery, "collect_due_reminder_notifications", return_value=["card-one", "card-two"]
        ):
            self.assertEqual(email_watch._due_reminder_alerts(), ["card-one", "card-two"])


if __name__ == "__main__":
    unittest.main()
