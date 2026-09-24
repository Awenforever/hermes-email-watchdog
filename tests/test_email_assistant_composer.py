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

    def test_verification_card_ignores_year_and_shows_contextual_code(self):
        email = {
            "account": "USTC", "subject": "Confirm your Mozilla account to sync",
            "from_addr": "accounts@firefox.com",
            "body": "Copyright 2026 Mozilla. Your confirmation code is 943433 and expires in 5 minutes.",
        }
        decision = {
            "classification": {"category": "verification_code", "label": "验证码"},
            "importance": {"level": "high"},
            "notification": {"content_mode": "code_card", "summary": "", "key_points": [], "original_policy": "none"},
            "action": {"required": False}, "deadline": {"has_deadline": False},
            "risk": {"level": "none", "notes": []},
        }
        text = composer.render_notification(email, decision)["text"]
        self.assertIn("## `943433`", text)
        self.assertNotIn("## `2026`", text)

    def test_footer_mechanics_inside_model_point_are_not_highlights(self):
        email = {
            "account": "USTC",
            "subject": "[DeepSeek Service status] Confirm your subscription",
            "from_addr": "noreply@statuspage.io",
            "body": "Confirm subscription\nhttps://status.deepseek.com/subscriptions/confirm/token",
            "links": [{
                "url": "https://status.deepseek.com/subscriptions/confirm/token",
                "display_text": "Confirm subscription",
            }],
        }
        decision = {
            "classification": {"category": "system_automation_notice", "label": "系统自动化通知"},
            "importance": {"level": "low"},
            "notification": {
                "content_mode": "summary_only", "summary_style": "bullets", "summary": "",
                "key_points": [
                    "DeepSeek Service 状态通知订阅确认邮件，需点击链接激活订阅。",
                    "邮件说明订阅状态更新，并附状态页与退订链接。",
                ],
                "original_policy": "none",
            },
            "action": {"required": True}, "deadline": {"has_deadline": False},
            "risk": {"level": "none", "notes": []},
        }
        text = composer.render_notification(email, decision)["text"]
        self.assertIn("需点击链接激活订阅", text)
        self.assertNotIn("退订", text)

    def test_fraud_awareness_is_not_rendered_as_a_task(self):
        email = {
            "account": "USTC", "subject": "重要提醒：多名同学遭遇境外诈骗，请注意防范！",
            "from_addr": "outgoing@ustc.edu.cn",
            "body": "请增强防范意识，遇可疑情况通过官方渠道核实，绝不转账。",
        }
        decision = {
            "classification": {"category": "school_notice", "label": "学校通知"},
            "importance": {"level": "high"},
            "notification": {
                "content_mode": "summary_only", "summary_style": "bullets", "summary": "",
                "key_points": ["学校提醒境外交流同学注意防范电信诈骗。"],
                "original_policy": "none",
            },
            "action": {"required": True, "description": "请务必高度重视，增强防范意识"},
            "deadline": {"has_deadline": False}, "risk": {"level": "none", "notes": []},
        }
        text = composer.render_notification(email, decision)["text"]
        self.assertIn("通知重点", text)
        self.assertNotIn("**需要处理**", text)

    def test_stale_action_without_deadline_requires_status_check(self):
        email = {
            "account": "USTC", "subject": "URGENT - article is ready for review",
            "from_addr": "editor@example.org", "date_sent": "2026-06-06 15:34 +0800",
            "body": "Please download the proof and return your corrections.",
        }
        decision = {
            "classification": {"category": "paper_manuscript_feedback", "label": "论文/稿件反馈"},
            "importance": {"level": "high"},
            "notification": {
                "content_mode": "summary_only", "summary_style": "paragraph",
                "summary": "编辑要求下载校对稿并返回修改。", "key_points": [],
                "original_policy": "none",
            },
            "action": {"required": True, "description": "请尽快下载校对稿并回复编辑"},
            "deadline": {"has_deadline": False}, "risk": {"level": "none", "notes": []},
        }
        text = composer.render_notification(email, decision)["text"]
        self.assertIn("这是一封历史邮件", text)
        self.assertNotIn("请尽快下载", text)

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

    def test_academic_digest_unwraps_model_points_and_drops_footer_links(self):
        email = {
            "account": "USTC",
            "subject": "Fw: 学术研究周报 2026-W33",
            "from_name": "吴金宏",
            "from_addr": "vive@mail.ustc.edu.cn",
            "body": "本期关注 HighFM 与真实数据质量。",
            "links": [
                {"url": "https://agent.qq.com/page/identity?token=x", "display_text": "valentines@agent.qq.com"},
                {"url": "https://agent.qq.com/page/report?type=report", "display_text": "举报"},
                {"url": "https://agent.qq.com/page/report?type=unsubscribe", "display_text": "退订"},
            ],
            "attachments": [{"filename": "report.pdf"}],
        }
        decision = {
            "classification": {"category": "academic_report_digest", "label": "学术报告摘要"},
            "importance": {"level": "normal"},
            "notification": {
                "summary": "", "key_points": [
                    {"text": "本期聚焦 HighFM。", "evidence": "HighFM 那篇尤其值得关注"},
                    {"text": "真实数据质量比数量更重要。", "evidence": "AI 合成数据不如真实数据"},
                ],
                "original_policy": "none",
            },
            "action": {"required": False}, "deadline": {"has_deadline": False},
            "risk": {"level": "none", "notes": []},
        }
        text = composer.render_notification(
            email, decision,
            {"attachments": [{"filename": "report.pdf", "download_status": "downloaded", "send_to_weixin": True}]},
        )["text"]
        self.assertIn("本期聚焦 HighFM。", text)
        self.assertIn("真实数据质量比数量更重要。", text)
        self.assertNotIn("{'text'", text)
        self.assertNotIn("快捷操作", text)
        self.assertNotIn("agent.qq.com", text)
        self.assertIn("**report.pdf** · 已附上", text)

    def test_attachment_failure_is_explicit_not_silent(self):
        email = {"subject": "周报", "from_addr": "x@example.test", "attachments": [{"filename": "report.pdf"}]}
        decision = {
            "classification": {"category": "academic_report_digest", "label": "学术报告摘要"},
            "importance": {"level": "normal"},
            "notification": {"summary": "本期研究周报。", "key_points": [], "original_policy": "none"},
            "action": {"required": False}, "deadline": {"has_deadline": False},
            "risk": {"level": "none", "notes": []},
        }
        text = composer.render_notification(
            email, decision,
            {"attachments": [{"filename": "report.pdf", "download_status": "download_failed"}]},
        )["text"]
        self.assertIn("**report.pdf** · 下载失败，请在邮箱查看", text)

    def test_truncated_model_action_is_replaced_by_complete_security_guidance(self):
        email = {
            "account": "USTC", "subject": "[GitHub] OAuth application added",
            "from_name": "GitHub", "body": "OpenCode was authorized with read:user.",
            "links": [{"url": "https://github.com/settings/security-log", "display_text": "security log"}],
        }
        decision = {
            "classification": {"category": "account_security", "label": "账户安全"},
            "importance": {"level": "high"},
            "notification": {"summary": "OpenCode 已获授权访问账户。", "key_points": [], "original_policy": "none"},
            "action": {"required": True, "description": "please co", "next_step": ""},
            "deadline": {"has_deadline": False}, "risk": {"level": "none", "notes": []},
        }
        text = composer.render_notification(email, decision)["text"]
        self.assertNotIn("please co", text)
        self.assertIn("如非本人操作", text)

    def test_scholar_alert_uses_titles_and_cleans_tracking_urls(self):
        email = {
            "account": "USTC", "subject": "Kaiming He - 新的结果", "from_addr": "scholaralerts-noreply@google.com",
            "body": "A Useful Paper on Vision\n( https://example.org/paper.pdf&hl=zh-CN&sa=X&scisig=secret )",
            "links": [{"url": "https://example.org/paper.pdf&hl=zh-CN&sa=X&scisig=secret", "display_text": ""}],
        }
        decision = {
            "classification": {"category": "academic_report_digest", "label": "学术报告摘要"},
            "importance": {"level": "low"},
            "notification": {"summary": "新增一条检索结果。", "key_points": [], "original_policy": "none"},
            "action": {"required": False}, "deadline": {"has_deadline": False}, "risk": {"level": "none", "notes": []},
        }
        text = composer.render_notification(email, decision)["text"]
        self.assertIn("[A Useful Paper on Vision](https://example.org/paper.pdf)", text)
        self.assertNotIn("scisig", text)

    def test_academic_alert_category_uses_same_scholar_link_quality(self):
        email = {
            "account": "USTC", "subject": "Kaiming He - 新的结果",
            "from_addr": "scholaralerts-noreply@google.com",
            "body": "A Useful Paper on Vision\n( https://example.org/paper.pdf&hl=zh-CN&sa=X&scisig=secret )",
            "links": [{"url": "https://example.org/paper.pdf&hl=zh-CN&sa=X&scisig=secret", "display_text": ""}],
        }
        decision = {
            "classification": {"category": "academic_alert_digest", "label": "学术快讯"},
            "importance": {"level": "low"},
            "notification": {"summary": "新增一条检索结果。", "key_points": [], "original_policy": "none"},
            "action": {"required": False}, "deadline": {"has_deadline": False},
            "risk": {"level": "none", "notes": []},
        }
        text = composer.render_notification(email, decision)["text"]
        self.assertIn("### 🔎 学术快讯", text)
        self.assertIn("[A Useful Paper on Vision](https://example.org/paper.pdf)", text)
        self.assertNotIn("scisig", text)

    def test_invoice_policy_link_is_not_presented_as_payment_action(self):
        email = {
            "account": "USTC", "subject": "网上购票系统-电子发票通知", "from_addr": "12306@rails.com.cn",
            "body": "发票号码：26349119423004141192，车次：G7449，票价：32.00元。",
            "links": [{"url": "https://www.12306.cn/mormhweb/mobile_zxdt/notice.html", "display_text": "铁路电子发票政策公告"}],
        }
        decision = {
            "classification": {"category": "invoice_receipt", "label": "发票/收据"},
            "importance": {"level": "normal"},
            "notification": {"summary": "", "key_points": [], "original_policy": "none"},
            "action": {"required": False}, "deadline": {"has_deadline": False}, "risk": {"level": "none", "notes": []},
        }
        text = composer.render_notification(email, decision)["text"]
        self.assertIn("`26349119423004141192`", text)
        self.assertIn("`G7449`", text)
        self.assertNotIn("快捷操作", text)
        self.assertNotIn("政策公告", text)

    def test_apc_notice_uses_summary_and_payment_link_not_fake_date(self):
        email = {
            "account": "USTC", "subject": "Reminder: PAST DUE - Please Submit Your IEEE Article Processing Charge(s)",
            "from_addr": "no-reply@email.copyright.com",
            "body": "Please verify the charges and follow the steps to make a payment or generate an invoice.",
            "links": [{
                "url": "https://oa.copyright.com/apc-payment-ui/overview?id=abc&chargeset=CHARGES",
                "display_text": "Pay charges now / Raise an invoice",
            }],
        }
        decision = {
            "classification": {"category": "invoice_receipt", "label": "发票/收据"},
            "importance": {"level": "high"},
            "notification": {
                "content_mode": "summary_only", "summary_style": "bullets", "summary": "",
                "key_points": [
                    "IEEE通过RightsLink催缴已逾期的文章处理费。",
                    "请核实费用并付款或生成发票。",
                ],
                "original_policy": "none",
            },
            "action": {"required": False}, "deadline": {"has_deadline": False},
            "risk": {"level": "none", "notes": []},
        }
        text = composer.render_notification(email, decision)["text"]
        self.assertIn("**账单摘要**", text)
        self.assertIn("IEEE通过RightsLink催缴", text)
        self.assertIn("[查看并处理账单]", text)
        self.assertNotIn("生成日期", text)

    def test_english_model_action_is_rewritten_for_orcid_authorization(self):
        email = {
            "account": "USTC", "subject": "[ORCID] You have new notifications",
            "from_addr": "DoNotReply@notify.orcid.org",
            "body": "Crossref would like to auto-update your ORCID record. Please click Grant permissions.",
            "links": [{"url": "https://orcid.org/inbox/encrypted/token/action", "display_text": "Grant permission"}],
        }
        decision = {
            "classification": {"category": "account_status_notice", "label": "账户状态"},
            "importance": {"level": "normal"},
            "notification": {"summary": "Crossref 请求更新 ORCID 成果。", "key_points": [], "original_policy": "none"},
            "action": {"required": True, "description": "please visit your ORCID", "next_step": ""},
            "deadline": {"has_deadline": False}, "risk": {"level": "none", "notes": []},
        }
        text = composer.render_notification(email, decision)["text"]
        self.assertNotIn("please visit", text)
        self.assertIn("确认是否授权 Crossref", text)
        self.assertIn("授权 Crossref 更新 ORCID", text)


if __name__ == "__main__":
    unittest.main()
