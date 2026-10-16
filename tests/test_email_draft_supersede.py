import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import email_actions


class DraftSupersedeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        email_actions.OUTBOX_FILE = root / "outbox.json"
        email_actions.ACTION_FILE = root / "actions.json"
        self.notification = (
            "### 📬 新邮件｜USTC\n\n`普通` · `账户状态` · `2026-10-08 10:24`\n\n"
            "**发件人** `person@example.com`\n\n**主题** `测试`"
        )
        metadata = {"mail_actions": [{
            "notification_text": self.notification,
            "message_id": "42", "account": "USTC", "account_type": "himalaya",
            "himalaya_config": str(root / "himalaya.toml"), "from_addr": "person@example.com",
            "subject": "测试", "risk_label": "low", "deadlines": [],
        }]}
        email_actions.OUTBOX_FILE.write_text(
            json.dumps({"entries": {"x": {"text": self.notification, "metadata": metadata}}}),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp.cleanup()

    def context(self, text, reference=None, mid="in-1"):
        return {
            "platform": "weixin", "authorized": True, "user_id": "paired",
            "message_id": mid, "message": text,
            "reference": {"present": True, "text": reference or self.notification},
        }

    def drafts(self):
        return json.loads(email_actions.ACTION_FILE.read_text(encoding="utf-8"))["drafts"]

    @patch("email_actions.email_config.load_config", return_value={"reply": {"default_signature": "", "signatures": {}}})
    def test_requoting_reply_supersedes_prior_pending_draft(self, _config):
        first = email_actions.handle_inbound(self.context("@回复\n旧正文", mid="in-1"))
        second = email_actions.handle_inbound(self.context("@回复\n新正文", mid="in-2"))
        self.assertIn("已自动作废", second["message"])
        self.assertNotIn("已自动作废", first["message"])
        drafts = self.drafts()
        pending = [d for d in drafts.values() if d.get("status") == "pending"]
        superseded = [d for d in drafts.values() if d.get("status") == "superseded"]
        self.assertEqual(1, len(pending))
        self.assertEqual(1, len(superseded))
        self.assertEqual("新正文", pending[0]["user_body"])
        self.assertEqual("旧正文", superseded[0]["user_body"])

    @patch("email_actions._send_himalaya_draft")
    @patch("email_actions.email_config.load_config", return_value={"reply": {"default_signature": "", "signatures": {}}})
    def test_superseded_draft_is_never_sent(self, _config, send):
        first = email_actions.handle_inbound(self.context("@回复\n旧正文", mid="in-1"))
        old_preview = first["message"]
        email_actions.handle_inbound(self.context("@回复\n新正文", mid="in-2"))
        confirm = email_actions.handle_inbound(
            self.context("@确认发送", reference=old_preview, mid="confirm-old")
        )
        self.assertEqual("handled", confirm["decision"])
        self.assertIn("取代", confirm["message"])
        self.assertNotIn("已发送", confirm["message"])
        send.assert_not_called()

    @patch("email_actions.email_config.load_config", return_value={"reply": {"default_signature": "", "signatures": {}}})
    def test_forward_supersedes_prior_reply_for_same_email(self, _config):
        email_actions.handle_inbound(self.context("@回复\n旧正文", mid="in-1"))
        forwarded = email_actions.handle_inbound(
            self.context("@转发 friend@example.com\n请查收", mid="in-2")
        )
        self.assertIn("已自动作废", forwarded["message"])
        drafts = self.drafts()
        pending = [d for d in drafts.values() if d.get("status") == "pending"]
        self.assertEqual(1, len(pending))
        self.assertEqual("forward", pending[0]["kind"])

    @patch("email_actions.email_config.load_config", return_value={"reply": {"default_signature": "", "signatures": {}}})
    def test_different_source_emails_do_not_supersede_each_other(self, _config):
        email_actions.handle_inbound(self.context("@回复\n正文A", mid="in-1"))
        data = json.loads(email_actions.OUTBOX_FILE.read_text(encoding="utf-8"))
        other = json.loads(json.dumps(data["entries"]["x"]))
        other["metadata"]["mail_actions"][0]["message_id"] = "99"
        other["metadata"]["mail_actions"][0]["subject"] = "另一封"
        other_text = "### 📬 新邮件｜USTC\n\n**发件人** `other@example.com`\n\n**主题** `另一封`"
        other["text"] = other_text
        other["metadata"]["mail_actions"][0]["notification_text"] = other_text
        other["metadata"]["mail_actions"][0]["from_addr"] = "other@example.com"
        data["entries"]["y"] = other
        email_actions.OUTBOX_FILE.write_text(json.dumps(data), encoding="utf-8")
        email_actions.handle_inbound(self.context("@回复\n正文B", reference=other_text, mid="in-2"))
        drafts = self.drafts()
        pending = [d for d in drafts.values() if d.get("status") == "pending"]
        superseded = [d for d in drafts.values() if d.get("status") == "superseded"]
        self.assertEqual(2, len(pending))
        self.assertEqual(0, len(superseded))

    @patch("email_actions.email_config.load_config", return_value={"reply": {"default_signature": "", "signatures": {}}})
    def test_sent_draft_is_not_superseded_by_new_draft(self, _config):
        email_actions.handle_inbound(self.context("@回复\n旧正文", mid="in-1"))
        state = json.loads(email_actions.ACTION_FILE.read_text(encoding="utf-8"))
        for draft in state["drafts"].values():
            draft["status"] = "sent"
        email_actions.ACTION_FILE.write_text(json.dumps(state), encoding="utf-8")
        second = email_actions.handle_inbound(self.context("@回复\n新正文", mid="in-2"))
        self.assertNotIn("已自动作废", second["message"])
        drafts = self.drafts()
        statuses = sorted(d.get("status") for d in drafts.values())
        self.assertEqual(["pending", "sent"], statuses)


if __name__ == "__main__":
    unittest.main()
