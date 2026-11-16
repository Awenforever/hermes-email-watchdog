#!/usr/bin/env python3
"""Policy guards: they may only quiet marketing and raise two urgent classes."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import email_semantic_schema as S


def decision(category, level="normal", notify=True):
    return {
        "classification": {"category": category},
        "importance": {"level": level},
        "notification": {"should_notify": notify},
    }


def facts(subject="", body="", codes=None, attachments=False):
    return {
        "source_subject": subject,
        "source_body": body,
        "code_candidates": codes or [],
        "attachments_present": attachments,
    }


class MarketingTests(unittest.TestCase):
    def test_plain_marketing_is_silenced(self):
        out = S.apply_policy_guards(
            decision("newsletter_marketing"), facts("本周精选", "五篇值得一读的文章")
        )
        self.assertFalse(out["notification"]["should_notify"])

    def test_marketing_with_a_deadline_still_notifies(self):
        out = S.apply_policy_guards(
            decision("newsletter_marketing"), facts("会员续费", "优惠 8 折，10 月 30 日截止")
        )
        self.assertTrue(out["notification"]["should_notify"])

    def test_marketing_with_an_amount_still_notifies(self):
        out = S.apply_policy_guards(
            decision("newsletter_marketing"), facts("账单", "本期应缴 ￥128.00")
        )
        self.assertTrue(out["notification"]["should_notify"])

    def test_marketing_with_an_attachment_still_notifies(self):
        out = S.apply_policy_guards(
            decision("newsletter_marketing"), facts("活动手册", "见附件", attachments=True)
        )
        self.assertTrue(out["notification"]["should_notify"])

    def test_marketing_with_a_code_still_notifies(self):
        out = S.apply_policy_guards(
            decision("newsletter_marketing"), facts("活动", "邀请码", codes=["123456"])
        )
        self.assertTrue(out["notification"]["should_notify"])


class VerificationCodeTests(unittest.TestCase):
    def test_code_with_validity_window_becomes_critical(self):
        out = S.apply_policy_guards(
            decision("verification_code", "normal"), facts("验证码", "您的验证码是 123456，5 分钟内有效", ["123456"])
        )
        self.assertEqual(out["importance"]["level"], "critical")

    def test_code_without_window_is_left_alone(self):
        out = S.apply_policy_guards(
            decision("verification_code", "low"), facts("验证码", "123456", ["123456"])
        )
        self.assertEqual(out["importance"]["level"], "low")


class SecurityTests(unittest.TestCase):
    def test_credential_change_becomes_critical(self):
        out = S.apply_policy_guards(
            decision("account_security", "normal"), facts("安全提醒", "您的密码已被修改")
        )
        self.assertEqual(out["importance"]["level"], "critical")

    def test_plain_status_notice_is_left_alone(self):
        out = S.apply_policy_guards(
            decision("account_security", "normal"), facts("服务状态", "我们更新了隐私政策")
        )
        self.assertEqual(out["importance"]["level"], "normal")


class NoOverreachTests(unittest.TestCase):
    def test_other_categories_untouched(self):
        for category in ("invoice_receipt", "meeting_event", "academic_report_digest", "personal_or_general"):
            before = decision(category, "low")
            after = S.apply_policy_guards(before, facts("x", "y"))
            self.assertEqual(after["importance"]["level"], "low", category)
            self.assertTrue(after["notification"]["should_notify"], category)

    def test_never_downgrades_critical(self):
        out = S.apply_policy_guards(
            decision("verification_code", "critical"), facts("验证码", "5 分钟内有效", ["123456"])
        )
        self.assertEqual(out["importance"]["level"], "critical")

    def test_existing_suppression_is_not_flipped_back(self):
        out = S.apply_policy_guards(
            decision("newsletter_marketing", notify=False), facts("促销", "立即购买")
        )
        self.assertFalse(out["notification"]["should_notify"])


if __name__ == "__main__":
    unittest.main()
