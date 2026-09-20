#!/usr/bin/env python3
"""Regressions distilled from real production messages; no network or writes."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import email_feature_extractor
import email_semantic_core as core
import email_semantic_engine as engine


PARTY_DUES_BODY = (
    "【中科大】党费缴纳日，初心校准时。请您及时通过智慧门户、中国科大APP或财务处微信公众号，"
    "进入\"缴费大厅\"栏目完成缴纳。如遇支付问题，可致电财务处党费专线：0551-63606585。"
)


class RealMessageRegressions(unittest.TestCase):
    def test_party_dues_is_a_grounded_direct_action(self):
        message = {
            "subject": "党费交纳",
            "body": PARTY_DUES_BODY,
            "from_addr": "urp@ustc.edu.cn",
            "has_attachments": False,
        }
        extracted = email_feature_extractor.extract_features(message)
        facts = engine._facts(message, extracted)
        self.assertTrue(facts["semantic_hints"]["direct_request_phrase"])

        raw = {
            "category": "school_notice",
            "confidence": 0.95,
            "importance": "normal",
            "importance_reason": "学校党费缴纳通知",
            "should_notify": True,
            "content_mode": "summary_only",
            "summary_style": "paragraph",
            "summary": "学校提醒通过指定入口完成党费缴纳。",
            "key_points": [],
            "summary_evidence": ["进入\"缴费大厅\"栏目完成缴纳"],
            "original_policy": "none",
            "original_reason": "",
            "action": None,
            "deadline": None,
            "attachment_policy": "none",
            "attachment_reason": "",
            "risk": {"level": "none", "notes": []},
            "topic_tags": ["党费"],
            "uncertainties": [],
            ", ": "",
        }
        decision, errors, repairs, _ = core.normalize_and_expand_detailed(
            raw, message_key="real:party-dues", facts=facts
        )
        self.assertFalse(errors)
        self.assertTrue(decision["action"]["required"])
        self.assertIn("缴", decision["action"]["description"])
        self.assertIn("cleanup:drop_empty_punctuation_key", repairs)
        self.assertIn("consistency:infer_grounded_direct_action", repairs)

    def test_nonempty_unknown_punctuation_key_still_fails_closed(self):
        raw = {
            "category": "personal_or_general",
            ", ": "meaningful-unexpected-content",
        }
        _, errors, repairs, _ = core.normalize_and_expand_detailed(
            raw,
            message_key="real:punctuation-hard-error",
            facts={"source_subject": "hello", "source_body": "hello"},
        )
        self.assertTrue(any("unknown field" in error for error in errors))
        self.assertNotIn("cleanup:drop_empty_punctuation_key", repairs)


if __name__ == "__main__":
    unittest.main(verbosity=2)
