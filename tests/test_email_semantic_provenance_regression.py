#!/usr/bin/env python3
"""Regression tests for nuanced spam handling and production semantic provenance."""
from __future__ import annotations
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import email_feature_extractor
import email_semantic_core as core
import email_semantic_engine as engine


def email(subject, body, sender="services@publisher.example"):
    return {
        "id": subject, "msg_id": subject, "account": "USTC",
        "subject": subject, "body": body, "from_addr": sender,
        "from_name": "Sender", "attachments": [],
        "has_attachment": False, "has_attachments": False,
    }


def model(category, summary, evidence, *, importance="normal", risk="none"):
    return {
        "category": category, "confidence": 0.85, "importance": importance,
        "importance_reason": "", "should_notify": True,
        "content_mode": "summary_only", "summary_style": "paragraph",
        "summary": summary, "key_points": [], "summary_evidence": [evidence],
        "original_policy": "none", "original_reason": "", "action": None,
        "deadline": None, "attachment_policy": "none", "attachment_reason": "",
        "risk": {"level": risk, "notes": []}, "topic_tags": [], "uncertainties": [],
    }


class SpamNuance(unittest.TestCase):
    def normalize(self, msg, raw):
        facts = engine._facts(msg, email_feature_extractor.extract_features(msg))
        return core.normalize_and_expand_detailed(raw, message_key="USTC:test", facts=facts)

    def test_01_spam_latest_issue_repairs_distracted_model(self):
        msg = email("[SPAM] Read the latest issue of Newton", "Read the latest issue of Newton. Research highlights. Manage preferences or unsubscribe.")
        raw = model("research_feedback_thread", "涉及多个前沿科学领域。", "Research highlights", importance="critical", risk="high")
        decision, errors, repairs, _ = self.normalize(msg, raw)
        self.assertFalse(errors)
        self.assertEqual(decision["classification"]["category"], "newsletter_marketing")
        self.assertEqual(decision["importance"]["level"], "low")
        self.assertEqual(decision["risk"]["level"], "none")
        self.assertIn("期刊最新一期", decision["notification"]["summary"])
        self.assertIn("consistency:spam_marketing_context_category", repairs)

    def test_02_grounded_model_marketing_summary_is_preserved(self):
        msg = email("[SPAM] Read the latest issue of Newton", "Read the latest issue of Newton. Unsubscribe.")
        raw = model("newsletter_marketing", "Cell Press 推广 Newton 最新一期内容。", "Read the latest issue of Newton")
        decision, errors, repairs, _ = self.normalize(msg, raw)
        self.assertFalse(errors)
        self.assertEqual(decision["notification"]["summary"], "Cell Press 推广 Newton 最新一期内容。")
        self.assertNotIn("grounding:repair_spam_marketing_purpose_summary", repairs)

    def test_03_spam_prefix_does_not_hide_manuscript_revision(self):
        body = "Your manuscript requires major revision. Please submit the revised manuscript by 30 July 2026."
        msg = email("[SPAM] Major revision decision for manuscript", body)
        raw = model("paper_manuscript_feedback", "稿件需要大修并重新提交。", "major revision")
        decision, errors, repairs, _ = self.normalize(msg, raw)
        self.assertFalse(errors)
        self.assertEqual(decision["classification"]["category"], "paper_manuscript_feedback")
        self.assertNotIn("consistency:spam_marketing_context_category", repairs)

    def test_04_spam_prefix_alone_is_not_absolute(self):
        msg = email("[SPAM] Message from collaborator", "I reviewed your method and have several comments.", "colleague@example.org")
        raw = model("personal_or_general", "合作者对研究方法提出了意见。", "reviewed your method")
        decision, errors, repairs, _ = self.normalize(msg, raw)
        self.assertFalse(errors)
        self.assertEqual(decision["classification"]["category"], "personal_or_general")
        self.assertNotIn("consistency:spam_marketing_context_category", repairs)


class ProvenancePersistence(unittest.TestCase):
    def test_05_production_trace_records_raw_and_validated_verdict(self):
        msg = email("[SPAM] Read the latest issue of Newton", "Read the latest issue of Newton. Unsubscribe.")
        facts = engine._facts(msg, email_feature_extractor.extract_features(msg))
        decision, errors, repairs, _ = core.normalize_and_expand_detailed(
            model("research_feedback_thread", "涉及多个前沿科学领域。", "Read the latest issue of Newton", importance="critical", risk="high"),
            message_key="USTC:trace", facts=facts,
        )
        self.assertFalse(errors)
        result = {
            "ok": True, "decision": decision, "message_key": "USTC:trace",
            "facts": facts, "model": "qwen2.5:3b", "latency_ms": 123,
            "fallback_used": False, "timeout": False, "cache_hit": False,
            "error_code": "", "llm_called": True,
            "decision_source": "llm_validated", "raw_category": "research_feedback_thread",
            "raw_importance": "critical", "raw_risk_level": "high",
            "fallback_reason": "", "normalization_repairs": repairs,
            "trace_signals": engine._trace_signals(facts),
        }
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "learning.sqlite"
            rec = engine.persist_production_observation(msg, result, production_route="adaptive_v1e", db_path=db)
            self.assertTrue(rec["ok"], rec)
            con = sqlite3.connect(db)
            row = con.execute("SELECT llm_called,decision_source,raw_category,validated_category,normalization_repairs_json,trace_signals_json,production_route FROM semantic_observations").fetchone()
            con.close()
        self.assertEqual(row[0], 1)
        self.assertEqual(row[1], "llm_validated")
        self.assertEqual(row[2], "research_feedback_thread")
        self.assertEqual(row[3], "newsletter_marketing")
        self.assertIn("spam_marketing_context_category", row[4])
        self.assertTrue(json.loads(row[5])["spam_subject_phrase"])
        self.assertEqual(row[6], "adaptive_v1e")

    def test_06_fallback_reason_is_persisted(self):
        msg = email("Routine notice", "No action is required.")
        facts = engine._facts(msg, email_feature_extractor.extract_features(msg))
        decision = engine.email_semantic_schema.conservative_fallback(
            message_key="USTC:fallback", email=msg, rule_result={}, analysis={}, facts=facts, reason="timeout"
        )
        result = {
            "decision": decision, "message_key": "USTC:fallback", "facts": facts,
            "model": "qwen2.5:3b", "latency_ms": 300000, "fallback_used": True,
            "timeout": True, "error_code": "timeout", "llm_called": True,
            "decision_source": "conservative_fallback", "fallback_reason": "SemanticEngineTimeout: timeout",
            "normalization_repairs": [], "trace_signals": engine._trace_signals(facts),
        }
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "learning.sqlite"
            rec = engine.persist_production_observation(msg, result, production_route="legacy_fallback", db_path=db)
            self.assertTrue(rec["ok"], rec)
            con = sqlite3.connect(db)
            row = con.execute("SELECT fallback_used,timeout,fallback_reason,production_route FROM semantic_observations").fetchone()
            con.close()
        self.assertEqual(row, (1, 1, "SemanticEngineTimeout: timeout", "legacy_fallback"))


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    print(f"SEMANTIC_PROVENANCE_REGRESSION={result.testsRun-len(result.failures)-len(result.errors)}/{result.testsRun}")
    raise SystemExit(0 if result.wasSuccessful() else 1)
