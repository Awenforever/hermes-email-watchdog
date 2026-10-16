import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import email_actions


SETTINGS = {"timezone": "Asia/Shanghai", "reminder_offsets_minutes": [1440, 60]}


class ReminderListTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        email_actions.OUTBOX_FILE = root / "outbox.json"
        email_actions.ACTION_FILE = root / "actions.json"
        self.notification = (
            "### 📬 新邮件｜USTC\n\n**发件人** `person@example.com`\n\n**主题** `测试`"
        )
        metadata = {"mail_actions": [{
            "notification_text": self.notification,
            "message_id": "42", "account": "USTC", "account_type": "himalaya",
            "himalaya_config": str(root / "himalaya.toml"), "from_addr": "person@example.com",
            "subject": "测试", "risk_label": "low",
            "deadlines": [{
                "id": "42:main", "message_id": "42", "title": "提交材料",
                "deadline": "2030-01-02T09:00:00+08:00", "timezone": "Asia/Shanghai",
                "reminders": [],
            }],
        }]}
        email_actions.OUTBOX_FILE.write_text(
            json.dumps({"entries": {"x": {"text": self.notification, "metadata": metadata}}}),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp.cleanup()

    def context(self, text, mid="in-1"):
        return {
            "platform": "weixin", "authorized": True, "user_id": "paired",
            "message_id": mid, "message": text,
            "reference": {"present": True, "text": self.notification},
        }

    @patch("email_actions.email_delivery._sync_calendar_ics")
    @patch("email_actions.email_config.get_delivery_settings", return_value=SETTINGS)
    @patch("email_actions.email_store.get_schedules", return_value=[
        {"deadline": "2030-01-02T09:00:00+08:00", "title": "提交材料", "account": "USTC",
         "action_needed": "登录系统完成学期注册"},
    ])
    @patch("email_actions.email_store.update_schedule")
    @patch("email_actions.email_store.upsert_schedule")
    def test_reply_shows_offsets_and_active_list(self, upsert, update, get_sched, _settings, _sync):
        result = email_actions.handle_inbound(self.context("提醒我"))
        message = result["message"]
        self.assertIn("已创建邮件提醒", message)
        # Offsets come from the generated reminders, not a hardcoded string.
        self.assertIn("提前1天、提前1小时", message)
        self.assertNotIn("提前 24 小时、1 小时", message)
        # The active-schedule block is a readable list, not a cramped table.
        self.assertIn("**当前活跃日程**（共 1 项）", message)
        self.assertNotIn("| 截止 | 事项 | 账户 |", message)
        self.assertIn("**1. 提交材料**", message)
        self.assertIn("截止：2030-01-02 09:00 · 账户：USTC", message)
        # The task is shown in full so the user knows what to do.
        self.assertIn("待办：登录系统完成学期注册", message)

    @patch("email_actions.email_delivery._sync_calendar_ics")
    @patch("email_actions.email_config.get_delivery_settings", return_value=SETTINGS)
    @patch("email_actions.email_store.get_schedules", return_value=[])
    @patch("email_actions.email_store.update_schedule")
    @patch("email_actions.email_store.upsert_schedule")
    def test_schedule_id_is_account_scoped_and_legacy_retired(self, upsert, update, _get, _s, _sync):
        email_actions.handle_inbound(self.context("提醒我"))
        saved = upsert.call_args.args[0]
        self.assertEqual("USTC:42:main", saved["id"])
        self.assertEqual("USTC", saved["account"])
        update.assert_called_once_with("42:main", {"status": "superseded"})

    @patch("email_actions.email_delivery._sync_calendar_ics")
    @patch("email_actions.email_config.get_delivery_settings", return_value=SETTINGS)
    @patch("email_actions.email_store.get_schedules", return_value=[])
    @patch("email_actions.email_store.update_schedule")
    @patch("email_actions.email_store.upsert_schedule")
    def test_reminder_activation_is_idempotent_with_list(self, upsert, _update, _get, _s, _sync):
        first = email_actions.handle_inbound(self.context("提醒我", mid="same"))
        second = email_actions.handle_inbound(self.context("提醒我", mid="same"))
        self.assertEqual(first["message"], second["message"])

    @patch("email_actions.email_delivery._sync_calendar_ics")
    @patch("email_actions.email_config.get_delivery_settings", return_value=SETTINGS)
    @patch("email_actions.email_store.get_schedules", return_value=[
        {"deadline": "2030-01-02T09:00:00+08:00", "account": "USTC",
         "title": "Call for Paper: [Remote Sensing] (IF: 4.3, ISSN: 2072-4292) — Special Issue \"Advanced Robust Satellite Techniques\""},
    ])
    @patch("email_actions.email_store.update_schedule")
    @patch("email_actions.email_store.upsert_schedule")
    def test_long_title_is_shown_in_full_not_truncated(self, upsert, update, get_sched, _settings, _sync):
        message = email_actions.handle_inbound(self.context("提醒我"))["message"]
        self.assertIn("**当前活跃日程**", message)
        # The whole subject survives verbatim: no 36-char cut, no ellipsis.
        self.assertIn("Advanced Robust Satellite Techniques", message)
        self.assertNotIn("…", message.split("**当前活跃日程**", 1)[1])

    @patch("email_actions.email_delivery._sync_calendar_ics")
    @patch("email_actions.email_config.get_delivery_settings", return_value=SETTINGS)
    @patch("email_actions.email_store.get_schedules", return_value=[
        {"deadline": "2030-01-02T09:00:00+08:00", "title": "提交材料",
         "message_id": "USTC:1599727883", "id": "1599727883:main"},
    ])
    @patch("email_actions.email_store.update_schedule")
    @patch("email_actions.email_store.upsert_schedule")
    def test_legacy_row_account_recovered_from_scoped_message_id(self, upsert, update, get_sched, _settings, _sync):
        message = email_actions.handle_inbound(self.context("提醒我"))["message"]
        self.assertIn("截止：2030-01-02 09:00 · 账户：USTC", message)

    @patch("email_actions.email_delivery._sync_calendar_ics")
    @patch("email_actions.email_config.get_delivery_settings", return_value=SETTINGS)
    @patch("email_actions.email_store.get_schedules", return_value=[
        {"deadline": "2030-01-02T09:00:00+08:00", "title": "提交材料", "id": "main"},
    ])
    @patch("email_actions.email_store.update_schedule")
    @patch("email_actions.email_store.upsert_schedule")
    def test_unknown_account_renders_dash_not_blank(self, upsert, update, get_sched, _settings, _sync):
        message = email_actions.handle_inbound(self.context("提醒我"))["message"]
        self.assertIn("账户：—", message)

    @patch("email_actions.email_delivery._sync_calendar_ics")
    @patch("email_actions.email_config.get_delivery_settings", return_value=SETTINGS)
    @patch("email_actions.email_store.get_schedules", return_value=[
        {"deadline": "", "title": "防范电信网络诈骗明白纸（四）", "id": "USTC:1:main"},
    ])
    @patch("email_actions.email_store.update_schedule")
    @patch("email_actions.email_store.upsert_schedule")
    def test_missing_deadline_renders_placeholder(self, upsert, update, get_sched, _settings, _sync):
        message = email_actions.handle_inbound(self.context("提醒我"))["message"]
        self.assertIn("截止：无明确截止", message)


if __name__ == "__main__":
    unittest.main()
