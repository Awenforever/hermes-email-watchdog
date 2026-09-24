#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import email_editorial_review as editorial


def decision(category="personal_or_general", *, action=False):
    return {
        "classification": {"category": category, "label": "邮件"},
        "importance": {"level": "low"},
        "notification": {"should_notify": True, "summary": "摘要", "key_points": []},
        "action": {"required": action, "description": "历史引用中的请求" if action else ""},
        "deadline": {"has_deadline": False},
        "attachments": {"present": False, "policy": "none"},
        "risk": {"level": "none", "notes": []},
    }


class EditorialReviewTests(unittest.TestCase):
    def settings(self):
        return {
            "enabled": True, "mode": "shadow", "provider": "hermes_openai",
            "model": "deepseek-flash", "fallback_model": "qwen3.6-chat",
            "timeout_seconds": 20, "num_predict": 1000, "num_predict_hard_cap": 1600,
        }

    def test_grounded_evidence_tolerates_only_spacing_and_punctuation_changes(self):
        source = "请于 2026 年 1 月 23 日之前提交。"
        self.assertTrue(editorial._evidence_supported("请于2026年1月23日之前提交", source))
        self.assertFalse(editorial._evidence_supported("请于2026年2月23日之前提交", source))

    def test_low_value_survey_can_override_quoted_historical_action(self):
        email = {
            "subject": "Re: Ticket #7701", "from_name": "Support",
            "from_addr": "support@example.test", "date_sent": "2026-01-16T10:00:00Z",
            "body": "How would you rate our support? Earlier: Please verify the product.",
            "links": [], "attachments": [],
        }
        def transport(prompt, settings):
            return {"parsed": {
                "publish": False, "markdown": "", "selected_links": [],
                "attachment_intent": "ignore", "temporal": None,
                "live_action": {"required": False, "description": "", "evidence": ""},
                "review_notes": ["仅为满意度邀请，引用内容不是新任务"],
            }, "model": settings["model"], "metrics": {}}
        result = editorial.review_notification(
            email, decision(action=True), "candidate", transport=transport,
            settings_override=self.settings(),
        )
        self.assertTrue(result["ok"])
        self.assertFalse(result["publish"])
        applied = editorial.apply_review(decision(action=True), result)
        self.assertFalse(applied["notification"]["should_notify"])
        self.assertFalse(applied["action"]["required"])

    def test_editor_selects_source_link_and_recovers_temporal_evidence(self):
        email = {
            "subject": "全文订单处理完成", "from_addr": "orders@example.test",
            "date_sent": "2026-01-11 09:05+08:00",
            "body": "订单已处理完成，下载链接有效期为15天。\n支持: https://example.test/support",
            "links": [{"url": "https://example.test/support", "display_text": "支持"}],
            "attachments": [],
        }
        markdown = (
            "### 📄 文献已就绪\n\n**发件人** `orders@example.test`\n\n"
            "**主题** `全文订单处理完成`\n\n**状态**\n这是历史通知，原下载窗口已经结束。"
        )
        def transport(prompt, settings):
            return {"parsed": {
                "publish": True, "markdown": markdown,
                "selected_links": [{"index": 0, "label": "联系支持", "purpose": "support", "still_useful_reason": "可用于重新申请"}],
                "attachment_intent": "ignore",
                "live_action": {"required": False, "description": "", "evidence": ""},
                "temporal": {"value": "2026-01-26T09:05:00+08:00", "evidence": "链接有效期为15天", "status": "expired", "expired_link_indices": []},
                "review_notes": ["按邮件日期判断为历史窗口"],
            }, "model": settings["model"], "metrics": {}}
        result = editorial.review_notification(
            email, decision("data_download_order_notice"), "candidate",
            transport=transport, settings_override=self.settings(),
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["selected_links"][0]["index"], 0)
        applied = editorial.apply_review(decision("data_download_order_notice"), result)
        self.assertTrue(applied["deadline"]["has_deadline"])
        self.assertEqual(applied["deadline"]["date_text"], "2026-01-26T09:05:00+08:00")
        rendered = editorial.finalize_markdown(result, {"attachments": [], "schedule": []}, applied)
        self.assertIn("[联系支持](https://example.test/support)", rendered["text"])
        self.assertIn("**时效**", rendered["text"])
        self.assertIn("状态：**已过期**", rendered["text"])

    def test_changed_sender_is_rejected(self):
        email = {"subject": "Hello", "from_addr": "real@example.test", "body": "Hello", "links": []}
        def transport(prompt, settings):
            return {"parsed": {
                "publish": True,
                "markdown": "### 邮件\n\n**发件人** `fake@example.test`\n\n**主题** `Hello`",
                "selected_links": [], "attachment_intent": "ignore", "temporal": None,
                "live_action": {"required": False, "description": "", "evidence": ""},
            }, "model": settings["model"], "metrics": {}}
        result = editorial.review_notification(
            email, decision(), "candidate", transport=transport,
            settings_override=self.settings(),
        )
        self.assertFalse(result["ok"])
        self.assertTrue(any("sender is missing or changed" in item for item in result["errors"]))

    def test_expired_temporal_fact_requires_expired_link_inventory(self):
        email = {
            "subject": "资料已就绪", "from_addr": "archive@example.test",
            "body": "资料下载窗口有效期为7天。", "date_sent": "2026-01-01T08:00:00+08:00",
            "links": [{"url": "https://example.test/download", "display_text": "下载"}],
        }
        markdown = (
            "**发件人** `archive@example.test`\n\n**主题** `资料已就绪`\n\n"
            "请尽快下载资料。"
        )
        def transport(prompt, settings):
            return {"parsed": {
                "publish": True, "markdown": markdown,
                "selected_links": [{"index": 0, "label": "下载资料", "still_useful_reason": ""}],
                "attachment_intent": "ignore",
                "live_action": {"required": False, "description": "", "evidence": ""},
                "temporal": {
                    "value": "2026-01-08T08:00:00+08:00",
                    "evidence": "有效期为7天", "status": "expired",
                },
            }, "model": settings["model"], "metrics": {}}
        result = editorial.review_notification(
            email, decision(), "candidate", transport=transport,
            settings_override={**self.settings(), "fallback_model": ""},
        )
        self.assertFalse(result["ok"])
        self.assertTrue(any("expired_link_indices" in item for item in result["errors"]))

    def test_expired_link_inventory_removes_action_link(self):
        email = {
            "subject": "资料已就绪", "from_addr": "archive@example.test",
            "body": "资料下载窗口有效期为7天。", "date_sent": "2026-01-01T08:00:00+08:00",
            "links": [{"url": "https://example.test/download", "display_text": "下载"}],
        }
        def transport(prompt, settings):
            return {"parsed": {
                "publish": True,
                "markdown": "**发件人** `archive@example.test`\n\n**主题** `资料已就绪`\n\n下载窗口已经结束。",
                "selected_links": [{"index": 0, "label": "下载资料", "purpose": "action", "still_useful_reason": ""}],
                "attachment_intent": "ignore",
                "live_action": {"required": False, "description": "", "evidence": ""},
                "temporal": {
                    "value": "2026-01-08T08:00:00+08:00", "evidence": "有效期为7天",
                    "status": "expired", "expired_link_indices": [0],
                    "expired_link_evidence": {"0": "有效期为7天"},
                },
            }, "model": settings["model"], "metrics": {}}
        result = editorial.review_notification(
            email, decision(), "candidate", transport=transport,
            settings_override={**self.settings(), "fallback_model": ""},
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["selected_links"], [])

    def test_validation_failure_gets_one_model_owned_self_correction(self):
        email = {
            "subject": "资料已就绪", "from_addr": "archive@example.test",
            "body": "资料下载窗口有效期为7天。", "date_sent": "2026-01-01T08:00:00+08:00",
        }
        calls = []
        def transport(prompt, settings):
            calls.append(prompt)
            shown_sender = "archive@example.test" if len(calls) == 2 else "wrong@example.test"
            markdown = f"**发件人** `{shown_sender}`\n\n**主题** `资料已就绪`\n\n该下载窗口已过期。"
            return {"parsed": {
                "publish": True, "markdown": markdown, "selected_links": [],
                "attachment_intent": "ignore",
                "live_action": {"required": False, "description": "", "evidence": ""},
                "temporal": {
                    "value": "2026-01-08T08:00:00+08:00",
                    "evidence": "有效期为7天", "status": "expired", "expired_link_indices": [],
                },
            }, "model": settings["model"], "metrics": {}}
        result = editorial.review_notification(
            email, decision(), "candidate", transport=transport,
            settings_override={**self.settings(), "fallback_model": ""},
        )
        self.assertTrue(result["ok"])
        self.assertEqual(len(calls), 2)
        self.assertTrue(result["metrics"]["editorial_self_correction"])
        self.assertIn("已过期", result["markdown"])

    def test_model_markdown_cannot_smuggle_link_placeholder(self):
        email = {"subject": "Hello", "from_addr": "real@example.test", "body": "Hello"}
        def transport(prompt, settings):
            return {"parsed": {
                "publish": True,
                "markdown": "**发件人** `real@example.test`\n\n**主题** `Hello`\n\n[打开](link:0)",
                "selected_links": [], "attachment_intent": "ignore",
                "live_action": {"required": False, "description": "", "evidence": ""},
                "temporal": None,
            }, "model": settings["model"], "metrics": {}}
        result = editorial.review_notification(
            email, decision(), "candidate", transport=transport,
            settings_override={**self.settings(), "fallback_model": ""},
        )
        self.assertFalse(result["ok"])
        self.assertTrue(any("unverified link" in item for item in result["errors"]))

    def test_first_pass_deadline_must_be_explicitly_reviewed(self):
        email = {"subject": "Notice", "from_addr": "real@example.test", "body": "No current task."}
        first = decision()
        first["deadline"] = {"has_deadline": True, "date_text": "tomorrow"}
        calls = []
        def transport(prompt, settings):
            calls.append(prompt)
            temporal = None if len(calls) == 1 else {"status": "unknown", "value": "", "evidence": ""}
            return {"parsed": {
                "publish": True,
                "markdown": "**发件人** `real@example.test`\n\n**主题** `Notice`\n\n没有当前待办。",
                "selected_links": [], "attachment_intent": "ignore",
                "live_action": {"required": False, "description": "", "evidence": ""},
                "temporal": temporal,
            }, "model": settings["model"], "metrics": {}}
        result = editorial.review_notification(
            email, first, "candidate", transport=transport,
            settings_override={**self.settings(), "fallback_model": ""},
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["temporal"]["status"], "unknown")
        applied = editorial.apply_review(first, result)
        self.assertFalse(applied["deadline"]["has_deadline"])

    def test_old_action_link_is_downgraded_without_current_validity_evidence(self):
        email = {
            "subject": "Confirm", "from_addr": "real@example.test",
            "date_sent": "2025-01-01T08:00:00+08:00", "body": "Confirm at the supplied link.",
            "links": [{"url": "https://example.test/confirm", "display_text": "Confirm"}],
        }
        def transport(prompt, settings):
            return {"parsed": {
                "publish": True,
                "markdown": "**发件人** `real@example.test`\n\n**主题** `Confirm`\n\n请确认。",
                "selected_links": [{
                    "index": 0, "label": "确认", "purpose": "action",
                    "still_useful_reason": "没有写明有效期",
                }],
                "attachment_intent": "ignore",
                "live_action": {"required": False, "description": "", "evidence": ""},
                "temporal": {"status": "unknown", "value": "", "evidence": ""},
            }, "model": settings["model"], "metrics": {}}
        result = editorial.review_notification(
            email, decision(), "candidate", transport=transport,
            settings_override={**self.settings(), "fallback_model": ""},
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["selected_links"], [])
        self.assertEqual(result["temporal"]["status"], "historical")
        self.assertEqual(result["temporal"]["expired_link_indices"], [0])
        self.assertTrue(result["temporal"]["safety_floor_applied"])

    def test_overdue_obligation_can_keep_still_useful_payment_link(self):
        email = {
            "subject": "Invoice overdue", "from_addr": "billing@example.test",
            "date_sent": "2026-09-23T08:00:00+08:00",
            "body": "Invoice 42 is now overdue. Due date: 2026-09-07. View and pay.",
            "links": [{"url": "https://example.test/pay/42", "display_text": "View and pay"}],
        }
        raw = {
            "publish": True,
            "markdown": "**发件人** `billing@example.test`\n\n**主题** `Invoice overdue`\n\n账单已逾期，付款义务仍待处理。",
            "selected_links": [{
                "index": 0, "label": "查看并付款", "purpose": "action",
                "still_useful_reason": "账单逾期后付款义务仍持续",
            }],
            "attachment_intent": "ignore",
            "live_action": {"required": False, "description": "", "evidence": ""},
            "temporal": {
                "value": "2026-09-07", "evidence": "Due date: 2026-09-07",
                "status": "expired", "expired_link_indices": [],
            },
        }
        normalized, errors = editorial._normalize_result(raw, email, decision(), editorial._source_links(email))
        self.assertEqual(errors, [])
        self.assertEqual(normalized["selected_links"][0]["purpose"], "action")

    def test_critic_correction_wrapper_is_unwrapped(self):
        email = {"subject": "Hello", "from_addr": "real@example.test", "body": "Hello"}
        corrected = {
            "publish": True,
            "markdown": "**发件人** `real@example.test`\n\n**主题** `Hello`\n\n正文。",
            "selected_links": [], "attachment_intent": "ignore",
            "live_action": {"required": False, "description": "", "evidence": ""},
            "temporal": None,
        }
        normalized, errors = editorial._normalize_result(
            {"accept": False, "issues": ["rewrite"], "corrected": corrected},
            email, decision(), [],
        )
        self.assertEqual(errors, [])
        self.assertTrue(normalized["publish"])

    def test_tracking_wrapper_link_is_context_only_not_displayable(self):
        email = {
            "subject": "Security", "from_addr": "alerts@example.test", "body": "Review activity.",
            "links": [{
                "url": "https://url3243.email.example.test/ls/click?upn=" + "x" * 700,
                "display_text": "Review security",
            }],
        }
        links = editorial._source_links(email)
        self.assertFalse(links[0]["display_safe"])
        raw = {
            "publish": True,
            "markdown": "**发件人** `alerts@example.test`\n\n**主题** `Security`\n\n请核查登录活动。",
            "selected_links": [{
                "index": 0, "label": "核查", "purpose": "action", "still_useful_reason": "安全核查",
            }],
            "attachment_intent": "ignore",
            "live_action": {"required": False, "description": "", "evidence": ""},
            "temporal": None,
        }
        normalized, errors = editorial._normalize_result(raw, email, decision(), links)
        self.assertEqual(errors, [])
        self.assertEqual(normalized["selected_links"], [])

    def test_grounded_important_attachment_cannot_be_silently_downgraded(self):
        email = {
            "subject": "Invoice", "from_addr": "billing@example.test", "body": "Invoice attached.",
            "attachments": [{"filename": "invoice.pdf", "content_type": "application/pdf"}],
            "has_attachments": True,
        }
        first = decision()
        first["attachments"] = {
            "present": True, "policy": "download_safe", "important_names": ["invoice.pdf"],
        }
        raw = {
            "publish": True,
            "markdown": "**发件人** `billing@example.test`\n\n**主题** `Invoice`\n\n发票见附件。",
            "selected_links": [], "attachment_intent": "list",
            "live_action": {"required": False, "description": "", "evidence": ""},
            "temporal": None,
        }
        normalized, errors = editorial._normalize_result(raw, email, first, [])
        self.assertIsNone(normalized)
        self.assertIn("grounded important attachment cannot be downgraded from send", errors)

    def test_model_cannot_emit_runtime_owned_attachment_section(self):
        email = {"subject": "Report", "from_addr": "author@example.test", "body": "Report attached."}
        raw = {
            "publish": True,
            "markdown": "**发件人** `author@example.test`\n\n**主题** `Report`\n\n摘要。\n\n**附件**\n- report.pdf",
            "selected_links": [], "attachment_intent": "ignore",
            "live_action": {"required": False, "description": "", "evidence": ""},
            "temporal": None,
        }
        normalized, errors = editorial._normalize_result(raw, email, decision(), [])
        self.assertIsNone(normalized)
        self.assertTrue(any("runtime-owned section" in item for item in errors))

    def test_critic_cannot_weaken_runtime_history_floor(self):
        baseline = {
            "temporal": {
                "status": "historical", "value": "2025-01-01", "evidence": "",
                "expired_link_indices": [0], "expired_link_evidence": {},
                "safety_floor_applied": True,
            }
        }
        corrected = {
            "selected_links": [{"index": 0}, {"index": 1}],
            "live_action": {"required": True, "description": "click", "evidence": "click"},
            "temporal": None,
        }
        result = editorial._preserve_safety_floor(corrected, baseline)
        self.assertEqual(result["selected_links"], [{"index": 1}])
        self.assertFalse(result["live_action"]["required"])
        self.assertTrue(result["temporal"]["safety_floor_applied"])

    def test_old_message_cannot_republish_relative_time_as_current(self):
        email = {
            "subject": "Weekly", "from_addr": "author@example.test",
            "date_sent": "2026-07-03T22:39:00+08:00",
            "body": "CVPR 2026 还有约 4 个月。",
        }
        raw = {
            "publish": True,
            "markdown": "**发件人** `author@example.test`\n\n**主题** `Weekly`\n\nCVPR 2026 还有约 4 个月。",
            "selected_links": [], "attachment_intent": "ignore",
            "live_action": {"required": False, "description": "", "evidence": ""},
            "temporal": None,
        }
        normalized, errors = editorial._normalize_result(raw, email, decision(), [])
        self.assertEqual(errors, [])
        self.assertNotIn("还有约 4 个月", normalized["markdown"])
        self.assertIn("已省略无法可靠换算", normalized["review_notes"][-1])

    def test_suppressed_review_cannot_be_revived_by_temporal_effects(self):
        rendered = editorial.finalize_markdown(
            {
                "publish": False, "markdown": "",
                "temporal": {"status": "expired", "value": "2026-01-01"},
                "selected_links": [], "links": [], "review_notes": [],
            },
            {"attachments": [{"filename": "notice.pdf", "send_to_weixin": True}]},
            {"classification": {"category": "newsletter_marketing"}},
        )
        self.assertFalse(rendered["ok"])
        self.assertEqual(rendered["text"], "")

    def test_apply_review_marks_final_editorial_authority(self):
        first = decision()
        reviewed = editorial.apply_review(first, {
            "ok": True, "publish": False,
            "live_action": {"required": False}, "attachment_intent": "ignore",
        })
        self.assertTrue(reviewed["notification"]["editorial_reviewed"])
        self.assertFalse(reviewed["notification"]["should_notify"])

    def test_account_password_is_redacted_but_access_code_is_preserved(self):
        text, changed = editorial._redact_account_passwords(
            "登录 ID：`9140`\n明文密码 `Firedet@408`\n网盘提取密码 `RTVk`"
        )
        self.assertTrue(changed)
        self.assertNotIn("Firedet@408", text)
        self.assertIn("[已隐藏]", text)
        self.assertIn("RTVk", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
