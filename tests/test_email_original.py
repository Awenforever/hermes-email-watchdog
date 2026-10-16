from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import email_actions
import email_original


class PrepareBlocksTests(unittest.TestCase):
    def test_paragraphs_become_blank_line_separated_blocks(self):
        blocks = email_original.prepare_blocks("第一段。\n\n\n\n第二段。")
        self.assertEqual(["第一段。", "第二段。"], blocks)

    def test_oversized_paragraph_is_split_below_block_cap(self):
        paragraph = "句子。" * 2000  # 6000 chars, no blank lines
        blocks = email_original.prepare_blocks(paragraph)
        self.assertGreater(len(blocks), 1)
        for block in blocks:
            self.assertLessEqual(len(block), email_original.MAX_BLOCK_CHARS)
        self.assertEqual(paragraph, "".join(blocks))

    def test_empty_and_html_only_bodies_produce_no_blocks(self):
        self.assertEqual([], email_original.prepare_blocks(""))
        self.assertEqual([], email_original.prepare_blocks("<html><body><img src='x'></body></html>"))


class RenderOriginalTests(unittest.TestCase):
    ACTION = {
        "subject": "会议通知",
        "from_name": "张三",
        "from_addr": "zhang@example.com",
        "account": "USTC",
    }

    def test_header_and_body_layout(self):
        fetched = {"text": "你好。\n\n请参加会议。", "date_sent": "Wed, 15 Oct 2026 09:30:00 +0800"}
        out = email_original.render_original(self.ACTION, fetched)
        self.assertTrue(out.startswith("### 📄 邮件原文"))
        self.assertIn("**主题** 会议通知", out)
        self.assertIn("**发件人** 张三 <zhang@example.com>", out)
        self.assertIn("**时间** 2026-10-15 09:30", out)
        self.assertIn("**账户** USTC", out)
        self.assertIn("你好。\n\n请参加会议。", out)

    def test_long_body_is_truncated_at_block_boundary_with_note(self):
        fetched = {"text": ("段落内容。" * 200 + "\n\n") * 40, "date_sent": ""}
        out = email_original.render_original(self.ACTION, fetched, max_chars=2000)
        self.assertIn("全文约", out)
        self.assertIn("已显示前", out)
        # Transport invariant: every blank-line-separated block must fit a
        # 2000-char WeChat bubble, including header and truncation note.
        for block in out.split("\n\n"):
            self.assertLessEqual(len(block), email_original.MAX_BLOCK_CHARS)

    def test_header_and_truncation_note_are_separate_blocks(self):
        fetched = {"text": ("句子。" * 3000), "date_sent": ""}
        out = email_original.render_original(self.ACTION, fetched, max_chars=3000)
        blocks = [b for b in out.split("\n\n") if b.strip()]
        self.assertTrue(blocks[0].startswith("### 📄 邮件原文"))
        self.assertTrue(blocks[0].endswith("---"))
        self.assertTrue(blocks[-1].startswith("＞ 全文约"))

    def test_textless_email_gets_friendly_refusal_with_attachment_count(self):
        fetched = {"text": "", "attachments": [{"filename": "scan.png"}, {"filename": "doc.pdf"}]}
        out = email_original.render_original(self.ACTION, fetched)
        self.assertIn("没有可显示的文本正文", out)
        self.assertIn("2 个附件", out)
        self.assertIn("### 📄 邮件原文", out)


class BuildOriginalReplyTests(unittest.TestCase):
    def test_non_himalaya_channel_is_refused(self):
        out = email_original.build_original_reply({"account_type": "agently"}, "1")
        self.assertIn("暂不支持", out)

    def test_missing_uid_or_config_is_refused(self):
        out = email_original.build_original_reply({"account_type": "himalaya", "himalaya_config": "/x.toml"}, "")
        self.assertIn("缺少定位邮件所需的信息", out)

    @patch("email_watch.read_himalaya", return_value={"text": "正文。", "date_sent": ""})
    def test_success_path_fetches_with_alias_and_renders(self, read):
        action = {
            "account_type": "himalaya", "himalaya_config": "/x.toml",
            "himalaya_account": "ustc", "subject": "S", "account": "USTC",
        }
        out = email_original.build_original_reply(action, "999")
        read.assert_called_once_with("/x.toml", "999", account="ustc")
        self.assertIn("正文。", out)

    @patch("email_watch.read_himalaya", return_value=None)
    def test_fetch_returning_none_raises_for_retryable_error(self, _read):
        with self.assertRaises(RuntimeError):
            email_original.build_original_reply(
                {"account_type": "himalaya", "himalaya_config": "/x.toml"}, "1"
            )

    @patch("email_watch.read_himalaya", side_effect=lambda *a, **k: time.sleep(3))
    def test_slow_fetch_hits_deadline_and_raises_timeout(self, _read):
        with self.assertRaises(TimeoutError):
            email_original.build_original_reply(
                {"account_type": "himalaya", "himalaya_config": "/x.toml"}, "1",
                deadline_seconds=1,
            )


class OriginalCommandDispatchTests(unittest.TestCase):
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
            "message_id": "USTC:42", "uid": "42", "account": "USTC",
            "account_type": "himalaya",
            "himalaya_config": str(root / "himalaya.toml"), "himalaya_account": "ustc",
            "from_addr": "person@example.com", "subject": "测试",
            "risk_label": "low", "deadlines": [],
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
            "reference": {"present": True, "text": reference if reference is not None else self.notification},
        }

    @patch("email_original.build_original_reply", return_value="### 📄 邮件原文\n---\n正文。")
    def test_quoting_push_and_sending_command_returns_original(self, build):
        result = email_actions.handle_inbound(self.context("@原文"))
        self.assertEqual("handled", result["decision"])
        self.assertIn("### 📄 邮件原文", result["message"])
        self.assertEqual("42", build.call_args[0][1])

    @patch("email_original.build_original_reply", side_effect=RuntimeError("imap down"))
    def test_fetch_failure_is_retryable_and_not_cached(self, _build):
        first = email_actions.handle_inbound(self.context("@原文", mid="in-1"))
        self.assertIn("获取邮件原文失败", first["message"])
        second = email_actions.handle_inbound(self.context("@原文", mid="in-1"))
        self.assertIn("获取邮件原文失败", second["message"])
        self.assertEqual(2, _build.call_count)

    def test_command_without_quote_gets_reference_hint(self):
        context = self.context("@原文")
        context["reference"] = {"present": False}
        result = email_actions.handle_inbound(context)
        self.assertEqual("handled", result["decision"])
        self.assertIn("未识别到所引用的邮件或草稿", result["message"])

    def test_command_with_trailing_text_gets_format_help(self):
        result = email_actions.handle_inbound(self.context("@原文 多余的参数"))
        self.assertEqual("handled", result["decision"])
        self.assertIn("命令格式无效", result["message"])

    def test_unresolvable_watchdog_reference_is_explained(self):
        result = email_actions.handle_inbound(self.context("@原文", reference="### 📬 新邮件｜USTC\n\n旧推送", mid="in-9"))
        self.assertEqual("handled", result["decision"])
        self.assertIn("无法还原对应邮件", result["message"])


if __name__ == "__main__":
    unittest.main()
