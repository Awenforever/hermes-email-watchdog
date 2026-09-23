#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import email_assistant_composer as composer


class AssistantComposerTests(unittest.TestCase):
    def invoice_email(self):
        return {
            "account": "USTC",
            "subject": "Fwd: Invoice Overdue Notice",
            "from_name": "Miracle",
            "from_addr": "sender@example.test",
            "date": "2026-09-23 20:11 SGT",
            "body": """---------- Forwarded message ---------
From: Mitce
Dear Edward King,

This is a billing notice that your invoice no. 21770340 which was generated on 2026/08/28 is now overdue.

Your payment method is: Alipay支付寶
Invoice: 21770340
Balance Due: $3.00 USD
Due Date: 2026/09/07
You can login to your client area to view and pay the invoice.
Copyright © Mitce, All rights reserved.""",
            "attachments": [{"filename": "Invoice-21770340.pdf", "size_bytes": 167658}],
            "links": [{"url": "https://mitce.io/viewinvoice.php?id=21770340", "display_text": ""}],
        }

    def invoice_decision(self):
        return {
            "classification": {"category": "invoice_receipt", "label": "发票/收据"},
            "importance": {"level": "high"},
            "notification": {
                "content_mode": "deadline_card",
                "summary_style": "bullets",
                "summary": "",
                "key_points": [
                    "---------- Forwarded message ---------",
                    "这是一封转发的账单通知：发票号21770340现已逾期。",
                    "应付余额$3.00 USD，到期日2026/09/07。",
                ],
                "original_policy": "excerpt",
            },
            "action": {"required": False},
            "deadline": {"has_deadline": True, "date_text": "2026-09-07"},
            "risk": {"level": "none", "notes": []},
        }

    def test_invoice_is_an_intent_card_not_a_body_template(self):
        result = composer.render_notification(
            self.invoice_email(), self.invoice_decision(),
            {"attachments": [{
                "filename": "Invoice-21770340.pdf",
                "download_status": "downloaded",
                "send_to_weixin": True,
            }]},
            {},
        )
        text = result["text"]
        self.assertIn("### 💳 账单已逾期｜USTC", text)
        self.assertIn("**发件人** `Miracle <sender@example.test>`", text)
        self.assertIn("**主题** `Fwd: Invoice Overdue Notice`", text)
        self.assertIn("**发票号**：`21770340`", text)
        self.assertIn("**应付金额**：`$3.00 USD`", text)
        self.assertIn("**到期日**：`2026/09/07`", text)
        self.assertIn("**付款方式**：`Alipay支付寶`", text)
        self.assertIn("[查看并处理账单](https://mitce.io/viewinvoice.php?id=21770340)", text)
        self.assertIn("**Invoice-21770340.pdf** · 已附上", text)
        self.assertNotIn("Forwarded message", text)
        self.assertNotIn("**要点**", text)
        self.assertNotIn("原文", text)
        self.assertNotIn("Dear Edward", text)

    def test_transport_and_negative_inventory_are_not_highlights(self):
        email = {
            "account": "USTC", "subject": "Fwd: Confirmation instructions",
            "from_name": "Miracle", "from_addr": "person@example.test",
            "body": "---------- Forwarded message ---------\nConfirm your email",
            "links": [{"url": "https://example.test/confirm?t=abc", "display_text": "Confirm your email"}],
        }
        decision = {
            "classification": {"category": "account_status_notice", "label": "账户状态"},
            "importance": {"level": "normal"},
            "notification": {
                "content_mode": "summary_only", "summary_style": "bullets", "summary": "",
                "key_points": [
                    "邮件为转发内容，正文含 Confirm your email 确认链接。",
                    "无附件，无验证码数字，无明确截止时间。",
                    "请通过链接确认邮箱。",
                ],
                "original_policy": "none",
            },
            "action": {"required": False}, "deadline": {"has_deadline": False},
            "risk": {"level": "none", "notes": []},
        }
        text = composer.render_notification(email, decision)["text"]
        self.assertIn("请通过链接确认邮箱", text)
        self.assertIn("[确认账户或邮箱]", text)
        self.assertNotIn("无附件", text)
        self.assertNotIn("转发内容", text)

    def test_curated_original_is_one_coherent_quote_not_mail_chrome(self):
        email = {
            "subject": "Fwd: Please review the draft", "from_addr": "person@example.test",
            "body": """---------- Forwarded message ---------
From: Person

Dear Edward,

Please review sections two and three and send comments before Friday.

The revised figures are attached for comparison.

Best regards""",
        }
        decision = {
            "classification": {"category": "personal_or_general", "label": "个人邮件"},
            "importance": {"level": "high"},
            "notification": {
                "content_mode": "summary_plus_original", "summary_style": "paragraph",
                "summary": "请审阅第二、三节并在周五前反馈。", "key_points": [],
                "original_policy": "excerpt",
            },
            "action": {"required": True, "description": "审阅第二、三节", "next_step": "周五前反馈"},
            "deadline": {"has_deadline": False}, "risk": {"level": "none", "notes": []},
        }
        text = composer.render_notification(email, decision)["text"]
        self.assertIn("**原文依据**", text)
        self.assertEqual(text.count("> "), 1)
        self.assertNotIn("Forwarded message", text)
        self.assertNotIn("Dear Edward", text)


if __name__ == "__main__":
    unittest.main()
