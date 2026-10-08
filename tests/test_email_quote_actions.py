import json
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import email_actions
import email_delivery


class EmailQuoteActionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        email_actions.OUTBOX_FILE = root / "outbox.json"
        email_actions.ACTION_FILE = root / "actions.json"
        self.notification = "### 📬 新邮件｜USTC\n\n**主题** `测试`"
        metadata = {"mail_actions": [{
            "notification_text": self.notification,
            "message_id": "42", "account": "USTC", "account_type": "himalaya",
            "himalaya_config": str(root / "himalaya.toml"), "from_addr": "person@example.com",
            "subject": "测试", "risk_label": "low",
            "deadlines": [{"id": "42:main", "message_id": "42", "title": "提交材料", "deadline": "2030-01-02T09:00:00+08:00", "timezone": "Asia/Shanghai", "reminders": []}],
        }]}
        email_actions.OUTBOX_FILE.write_text(json.dumps({"entries": {"x": {"text": self.notification, "metadata": metadata}}}), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def context(self, text, reference=None, mid="in-1"):
        return {"platform": "weixin", "authorized": True, "user_id": "paired", "message_id": mid, "message": text, "reference": {"present": True, "text": reference or self.notification}}

    def test_untrusted_hook_event_is_ignored(self):
        context = self.context("提醒我")
        context.pop("authorized")
        self.assertIsNone(email_actions.handle_inbound(context))

    @patch("email_actions.email_delivery._sync_calendar_ics")
    @patch("email_actions.email_store.upsert_schedule")
    def test_concurrent_duplicate_action_has_one_state_transition(self, upsert, sync):
        context = self.context("提醒我", mid="same-inbound")
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: email_actions.handle_inbound(dict(context)), range(16)))
        self.assertTrue(all(item["decision"] == "handled" for item in results))
        self.assertTrue(all(item["message"] == results[0]["message"] for item in results))
        upsert.assert_called_once()

    @patch("email_actions.email_delivery._sync_calendar_ics")
    @patch("email_actions.email_store.upsert_schedule")
    def test_exact_remind_me_activates_single_deadline_idempotently(self, upsert, sync):
        first = email_actions.handle_inbound(self.context("提醒我"))
        second = email_actions.handle_inbound(self.context("提醒我"))
        self.assertEqual(first["decision"], "handled")
        self.assertIn("已创建", first["message"])
        self.assertEqual(first["message"], second["message"])
        upsert.assert_called_once()

    @patch("email_actions.email_config.load_config", return_value={"reply": {"default_signature": "祝好\n测试者", "signatures": {}}})
    def test_reply_body_is_verbatim_and_signature_is_appended(self, _config):
        result = email_actions.handle_inbound(self.context("回复\n第一行\n第二行  ", mid="in-2"))
        self.assertIn("第一行\n第二行  \n\n祝好\n测试者", result["message"])
        state = json.loads(email_actions.ACTION_FILE.read_text(encoding="utf-8"))
        draft = next(iter(state["drafts"].values()))
        self.assertEqual(draft["body"], "第一行\n第二行  \n\n祝好\n测试者")

    def test_no_reply_sender_is_blocked(self):
        data = json.loads(email_actions.OUTBOX_FILE.read_text(encoding="utf-8"))
        data["entries"]["x"]["metadata"]["mail_actions"][0]["from_addr"] = "no-reply@example.com"
        email_actions.OUTBOX_FILE.write_text(json.dumps(data), encoding="utf-8")
        result = email_actions.handle_inbound(self.context("回复\n正文", mid="in-3"))
        self.assertIn("已阻止", result["message"])

    def test_reference_message_id_disambiguates_identical_bubbles(self):
        data = json.loads(email_actions.OUTBOX_FILE.read_text(encoding="utf-8"))
        first = data["entries"]["x"]
        first["parts"] = {"text": {"adapter_message_id": "bubble-a"}}
        second = json.loads(json.dumps(first))
        second["parts"]["text"]["adapter_message_id"] = "bubble-b"
        second["metadata"]["mail_actions"][0]["message_id"] = "43"
        second["metadata"]["mail_actions"][0]["subject"] = "另一个测试"
        data["entries"]["y"] = second
        email_actions.OUTBOX_FILE.write_text(json.dumps(data), encoding="utf-8")
        context = self.context("回复\n正文", mid="in-ref")
        context["reference"]["message_id"] = "bubble-b"
        result = email_actions.handle_inbound(context)
        self.assertIn("另一个测试", result["message"])

    @patch.dict("os.environ", {"HERMES_TIMEZONE": "Europe/Berlin"}, clear=False)
    def test_date_only_deadline_uses_nine_in_profile_timezone(self):
        value = email_delivery._resolve_deadline_value("2030-03-04", "2029-01-01", "auto")
        self.assertIn("2030-03-04T09:00", value)
        self.assertIn("+01:00", value)

    @patch("email_actions.email_config.load_config", return_value={"reply": {"outbound_enabled": True}})
    @patch("email_actions.subprocess.run")
    def test_confirmed_himalaya_transport_generates_then_sends_template(self, run, _config):
        config = Path(self.temp.name) / "himalaya.toml"
        config.write_text("[accounts.test]\n", encoding="utf-8")
        run.side_effect = [
            SimpleNamespace(returncode=0, stdout="From: a@example.test\nTo: b@example.test\nSubject: Re: 测试\n\nconfigured signature\n\n> quoted original", stderr=""),
            SimpleNamespace(returncode=0, stdout="sent", stderr=""),
        ]
        email_actions._send_himalaya_reply({"body": "原样正文", "mail": {"account_type": "himalaya", "himalaya_config": str(config), "message_id": "42", "himalaya_account": "test"}})
        self.assertIn("template", run.call_args_list[0].args[0])
        self.assertIn("reply", run.call_args_list[0].args[0])
        self.assertEqual(run.call_args_list[0].args[0][-1], "42")
        self.assertNotIn("原样正文", run.call_args_list[0].args[0])
        self.assertIn("send", run.call_args_list[1].args[0])
        sent_template = run.call_args_list[1].args[0][-1]
        self.assertEqual(sent_template.split("\n\n", 1)[1], "原样正文\n")
        self.assertNotIn("quoted original", sent_template)

    def test_reply_template_rejects_missing_recipient_headers(self):
        with self.assertRaises(RuntimeError):
            email_actions._replace_template_body("Subject: Re: x\n\nimplicit", "正文")

    @patch("email_actions._send_himalaya_reply", side_effect=subprocess.TimeoutExpired("himalaya", 60))
    def test_uncertain_send_is_never_automatically_retried(self, send):
        created = email_actions.handle_inbound(self.context("回复\n正文", mid="draft-source"))
        confirm = self.context("确认发送", reference=created["message"], mid="confirm-1")
        first = email_actions.handle_inbound(confirm)
        confirm["message_id"] = "confirm-2"
        second = email_actions.handle_inbound(confirm)
        self.assertIn("投递结果不确定", first["message"])
        self.assertIn("不会自动重试", second["message"])
        send.assert_called_once()


if __name__ == "__main__":
    unittest.main()
