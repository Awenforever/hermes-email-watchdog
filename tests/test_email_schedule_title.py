#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import email_delivery


SETTINGS = {"timezone": "Asia/Shanghai", "reminder_offsets_minutes": [1440, 60], "create_reminders": True}

LONG_SUMMARY = (
    "- 这是一封关于2026年度研究生学期注册的重要通知邮件，要求所有在册研究生在规定时间内完成注册。\n"
    "- 邮件说明未按时注册将影响学籍状态，需要特别注意。\n"
    "- 邮件还附带了注册流程的详细步骤说明。"
)


class ConciseLabelTests(unittest.TestCase):
    def test_long_multibullet_summary_is_cut_at_a_meaningful_boundary(self):
        label = email_delivery._concise_label(LONG_SUMMARY, 36)
        self.assertLessEqual(len(label), 37)
        self.assertFalse(label.startswith("-"))
        self.assertNotIn("\n", label)
        # Must not dangle mid-clause: ends at a sentence boundary or an ellipsis.
        self.assertTrue(label.endswith("…") or label[-1] in "。；;！!？?")

    def test_short_text_is_preserved_verbatim(self):
        self.assertEqual(email_delivery._concise_label("提交材料", 36), "提交材料")

    def test_blank_and_none_are_empty(self):
        self.assertEqual(email_delivery._concise_label("", 36), "")
        self.assertEqual(email_delivery._concise_label(None, 36), "")
        self.assertEqual(email_delivery._concise_label("   \n  ", 36), "")


class ConciseScheduleTitleTests(unittest.TestCase):
    def test_prefers_specific_action_description(self):
        analysis = {"action_needed": {"description": "登录系统完成学期注册"}, "formatted_summary": LONG_SUMMARY}
        self.assertEqual(email_delivery._concise_schedule_title(analysis, {"subject": "通知"}), "登录系统完成学期注册")

    def test_falls_back_to_summary_then_subject_then_default(self):
        self.assertTrue(email_delivery._concise_schedule_title({"formatted_summary": LONG_SUMMARY}, {"subject": "通知"}))
        self.assertEqual(email_delivery._concise_schedule_title({}, {"subject": "会议提醒"}), "会议提醒")
        self.assertEqual(email_delivery._concise_schedule_title({}, {}), "邮件待办")


class UpsertScheduleTitleTests(unittest.TestCase):
    def test_offered_schedule_stores_concise_title_not_full_summary(self):
        analysis = {
            "deadline": {"has_deadline": True, "datetime": "2030-01-02T09:00:00+08:00", "timezone": "Asia/Shanghai"},
            "reminder_schedule": [],
            "action_needed": {"description": "完成学期注册"},
            "formatted_summary": LONG_SUMMARY,
        }
        email = {"id": "USTC:999", "subject": "注册通知", "date_sent": "2030-01-01 08:00+08:00"}
        with mock.patch.object(email_delivery, "email_store", mock.Mock()), mock.patch.object(
            email_delivery.email_config, "get_delivery_settings", return_value=SETTINGS
        ):
            out = email_delivery.upsert_schedule(email, analysis)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["status"], "offered")
        self.assertEqual(out[0]["title"], "完成学期注册")
        self.assertLessEqual(len(out[0]["title"]), 42)
        # The reminder message inherits the concise title, not the multi-bullet dump.
        self.assertTrue(all("这是一封关于" not in r.get("message", "") for r in out[0]["reminders"]))


class ReminderCardRenderingTests(unittest.TestCase):
    def test_legacy_long_title_card_is_summarized_not_dumped(self):
        # A schedule created before the concise-title fix still holds the full
        # multi-bullet summary; the reminder card renderer must summarize it.
        legacy = {
            "id": "USTC:999:main",
            "title": LONG_SUMMARY,
            "action_needed": "",
            "deadline": "2030-01-02T09:00:00+08:00",
            "timezone": "Asia/Shanghai",
            "status": "active",
            "reminder_json": __import__("json").dumps(
                [{"time": "2020-01-01T00:00:00+08:00", "kind": "提前1小时", "message": LONG_SUMMARY}]
            ),
            "reminders_sent_json": "[]",
        }
        store = mock.Mock()
        store.get_schedules.return_value = [legacy]
        with mock.patch.object(email_delivery, "email_store", store), mock.patch.object(
            email_delivery.email_config, "get_delivery_settings", return_value=SETTINGS
        ), mock.patch.object(email_delivery, "_sync_calendar_ics", return_value=""):
            alerts = email_delivery.collect_due_reminder_notifications()
        self.assertEqual(len(alerts), 1)
        card = alerts[0]
        self.assertIn("### ⏰ 邮件待办提醒", card)
        # The 2nd/3rd bullets of the raw dump must not survive into the card;
        # only a single concise line is rendered for the title and the 待办.
        self.assertNotIn("邮件说明未按时注册将影响学籍状态", card)
        self.assertNotIn("详细步骤说明", card)
        for line in card.splitlines():
            self.assertLessEqual(len(line), 80)


if __name__ == "__main__":
    unittest.main()
