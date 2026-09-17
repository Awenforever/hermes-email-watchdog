#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import email_semantic_core as core
import email_semantic_engine as engine

def valid_core(category: str, summary: str, evidence: str, *, should_notify: bool = False):
    return {
        "category": category,
        "confidence": 0.8,
        "importance": "normal",
        "importance_reason": "test",
        "should_notify": should_notify,
        "content_mode": "summary_only",
        "summary_style": "paragraph",
        "summary": summary,
        "key_points": [],
        "summary_evidence": [evidence],
        "original_policy": "none",
        "original_reason": "",
        "action": None,
        "deadline": None,
        "attachment_policy": "none",
        "attachment_reason": "",
        "risk": {"level": "none", "notes": []},
        "topic_tags": [],
        "uncertainties": [],
    }

def facts_for(subject: str, body: str):
    email = {
        "id": "test",
        "msg_id": "test",
        "subject": subject,
        "body": body,
        "from_addr": "sender@example.invalid",
        "from_name": "Test Sender",
        "attachments": [],
        "has_attachment": False,
        "has_attachments": False,
    }
    features = {
        "sender_domain": "example.invalid",
        "code_candidates": [],
        "body_shape": {"body_chars": len(body), "body_lines": body.count("\n") + 1},
        "attachment_profile": {"has_attachments": False, "count": 0},
    }
    return engine._facts(email, features)

class FullPolicyMatrix(unittest.TestCase):
    def expand(self, raw, subject, body):
        return core.normalize_and_expand_detailed(
            raw,
            message_key=f"policy:{subject}",
            facts=facts_for(subject, body),
        )

    def test_account_security_boundary(self):
        subject = "检测到可疑账户登录，请立即确认"
        body = "系统检测到一个新的设备登录。如果不是你本人操作，请立即修改密码。"
        raw = valid_core("account_status_notice", "检测到可疑账户登录。", "可疑账户登录")
        decision, errors, repairs, _ = self.expand(raw, subject, body)
        self.assertFalse(errors)
        self.assertEqual(decision["classification"]["category"], "account_security")
        self.assertTrue(decision["notification"]["should_notify"])
        self.assertIn("consistency:grounded_account_security_category", repairs)

    def test_health_check_boundary(self):
        subject = "年度健康体检预约通知"
        body = "年度健康体检预约已经开放，请根据个人安排选择日期。"
        raw = valid_core("school_notice", "年度健康体检预约已经开放。", "健康体检预约已经开放")
        decision, errors, repairs, _ = self.expand(raw, subject, body)
        self.assertFalse(errors)
        self.assertEqual(decision["classification"]["category"], "health_check_notice")
        self.assertIn("consistency:grounded_health_check_category", repairs)

    def test_manuscript_feedback_boundary_and_no_deadline(self):
        subject = "Manuscript revision comments"
        body = "The editor returned detailed revision comments for the manuscript. No immediate deadline is imposed."
        raw = valid_core(
            "task_deadline",
            "The editor returned revision comments.",
            "revision comments for the manuscript",
        )
        raw["deadline"] = {
            "datetime": "",
            "date_text": "No immediate deadline",
            "confidence": 0.8,
            "evidence": "No immediate deadline is imposed",
        }
        raw["content_mode"] = "deadline_card"
        decision, errors, repairs, _ = self.expand(raw, subject, body)
        self.assertFalse(errors)
        self.assertEqual(decision["classification"]["category"], "paper_manuscript_feedback")
        self.assertFalse(decision["deadline"]["has_deadline"])
        self.assertNotEqual(decision["notification"]["content_mode"], "deadline_card")
        self.assertIn("consistency:grounded_manuscript_feedback_category", repairs)
        self.assertIn("grounding:explicit_no_deadline_clears_deadline", repairs)

    def test_research_feedback_boundary(self):
        subject = "关于弱浮力火焰实验方案的反馈"
        body = "我看过你的实验方案，建议补充低压与落塔条件的对应关系。"
        raw = valid_core("academic_report_digest", "建议补充实验条件对应关系。", "建议补充低压与落塔条件的对应关系")
        decision, errors, repairs, _ = self.expand(raw, subject, body)
        self.assertFalse(errors)
        self.assertEqual(decision["classification"]["category"], "research_feedback_thread")
        self.assertIn("consistency:grounded_research_feedback_category", repairs)

    def test_all_mail_eventual_push_marketing(self):
        subject = "本周产品资讯与优惠"
        body = "本周产品资讯已更新，同时提供限时优惠，可随时退订。"
        raw = valid_core("newsletter_marketing", "本周产品资讯与优惠已更新。", "本周产品资讯已更新", should_notify=False)
        decision, errors, repairs, _ = self.expand(raw, subject, body)
        self.assertFalse(errors)
        self.assertTrue(decision["notification"]["should_notify"])
        self.assertIn("policy:all_mail_eventual_push", repairs)

    def test_all_mail_eventual_push_personal(self):
        subject = "周末聚餐安排"
        body = "大家计划周末一起聚餐，等人数确定后再决定具体时间。"
        raw = valid_core("personal_or_general", "大家计划周末一起聚餐。", "大家计划周末一起聚餐", should_notify=False)
        decision, errors, repairs, _ = self.expand(raw, subject, body)
        self.assertFalse(errors)
        self.assertTrue(decision["notification"]["should_notify"])
        self.assertIn("policy:all_mail_eventual_push", repairs)

if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(FullPolicyMatrix)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(f"FULL_POLICY_MATRIX={result.testsRun - len(result.failures) - len(result.errors)}/{result.testsRun}")
    raise SystemExit(0 if result.wasSuccessful() else 1)
