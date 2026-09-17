#!/usr/bin/env python3
"""Exact regression for the controlled Newton real-E2E message."""
from __future__ import annotations

import importlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import email_feature_extractor
import email_semantic_core as core
import email_semantic_engine as engine
import email_watch


def exact_email():
    return {
        "id": "real-e2e-newton-1",
        "msg_id": "real-e2e-newton-1",
        "account": "USTC",
        "subject": "[SPAM] Read the latest issue of Newton — HERMES-E2E-20260724-1425",
        "from_addr": "e2e-sender@example.test",
        "from_name": "E2E Sender",
        "body": (
            "Read the latest issue of Newton\n\n"
            "Explore highlights from the newest issue, including quantum communication, "
            "advanced nanostructures, climate science, astrophysics, and other emerging "
            "research topics. This newsletter presents selected articles, editor comments, "
            "and recent scientific developments for general reading.\n\n"
            "No response or immediate action is required. There is no deadline, account "
            "alert, manuscript decision, invoice, payment request, or confidential material "
            "in this message.\n\n"
            "This is a controlled Hermes Email Watchdog end-to-end test message.\n"
            "Test ID: HERMES-E2E-NEWTON-SPAM-20260724-1425\n\n"
            "Manage preferences or unsubscribe from promotional updates."
        ),
        "attachments": [],
        "has_attachment": False,
        "has_attachments": False,
    }


def observed_bad_raw_core():
    return {
        "category": "newsletter_marketing",
        "confidence": 0.94,
        "importance": "critical",
        "importance_reason": "涉及多个科研主题",
        "should_notify": True,
        "content_mode": "summary_only",
        "summary_style": "paragraph",
        "summary": (
            "这是一封控制的Hermes Email Watchdog端到端测试邮件。"
            "这是2026年7月24日14:25的新版Newton期刊。"
        ),
        "key_points": ["介绍量子通信和纳米结构等研究主题。"],
        "summary_evidence": [
            "This is a controlled Hermes Email Watchdog end-to-end test message.",
            "Read the latest issue of Newton",
        ],
        "original_policy": "none",
        "original_reason": "",
        "action": None,
        "deadline": None,
        "attachment_policy": "none",
        "attachment_reason": "",
        "risk": {"level": "low", "notes": ["涉及科研主题"]},
        "topic_tags": ["Newton"],
        "uncertainties": [],
    }


class ExactE2EGrounding(unittest.TestCase):
    def facts(self, msg=None):
        msg = msg or exact_email()
        return engine._facts(msg, email_feature_extractor.extract_features(msg))

    def test_01_negated_deadline_and_receipt_are_not_positive_signals(self):
        hints = self.facts()["semantic_hints"]
        self.assertTrue(hints["no_deadline_phrase"])
        self.assertTrue(hints["no_receipt_phrase"])
        self.assertFalse(hints["deadline_phrase"])
        self.assertFalse(hints["receipt_phrase"])
        self.assertTrue(hints["spam_subject_phrase"])
        self.assertTrue(hints["publication_issue_subject_phrase"])

    def test_02_opaque_test_id_time_is_removed_from_summary(self):
        decision, errors, repairs, _ = core.normalize_and_expand_detailed(
            observed_bad_raw_core(),
            message_key="USTC:real-e2e-newton-1",
            facts=self.facts(),
        )
        self.assertFalse(errors, errors)
        summary = decision["notification"]["summary"]
        self.assertEqual(decision["classification"]["category"], "newsletter_marketing")
        self.assertEqual(decision["importance"]["level"], "low")
        self.assertEqual(decision["risk"]["level"], "none")
        self.assertIn("Newton 期刊最新一期内容推广", summary)
        self.assertIn("受控的端到端测试邮件", summary)
        self.assertIn("无需回复或立即处理", summary)
        self.assertNotIn("14:25", summary)
        self.assertNotIn("2026年7月24日", summary)
        self.assertIn("grounding:drop_opaque_identifier_time_claim", repairs)
        self.assertIn("grounding:repair_spam_marketing_purpose_summary", repairs)

    def test_03_good_marketing_summary_is_still_preserved(self):
        raw = observed_bad_raw_core()
        raw.update({
            "summary": "Cell Press 推广 Newton 最新一期内容，无需立即处理。",
            "key_points": [],
            "summary_evidence": ["Read the latest issue of Newton"],
        })
        decision, errors, repairs, _ = core.normalize_and_expand_detailed(
            raw, message_key="USTC:good-summary", facts=self.facts()
        )
        self.assertFalse(errors, errors)
        self.assertEqual(
            decision["notification"]["summary"],
            "Cell Press 推广 Newton 最新一期内容，无需立即处理。",
        )
        self.assertNotIn("grounding:repair_spam_marketing_purpose_summary", repairs)

    def test_04_contrast_resets_negation_scope(self):
        msg = exact_email()
        msg["subject"] = "Invoice for your records"
        msg["body"] = (
            "There is no deadline for registration. However, the invoice is attached "
            "for your records."
        )
        hints = self.facts(msg)["semantic_hints"]
        self.assertFalse(hints["deadline_phrase"])
        self.assertTrue(hints["receipt_phrase"])

    def test_05_legacy_rule_does_not_treat_negative_invoice_list_as_invoice(self):
        result = email_watch.classify_rule(exact_email())
        self.assertNotEqual(result["category"], "发票/收据")
        self.assertNotEqual(result["category"], "付款/缴费")
        self.assertEqual(result["category"], "广告")

    def test_06_production_delivery_owns_canonical_learning_record(self):
        self.assertTrue(email_watch._delivery_owns_learning_record({
            "production_route": "adaptive_v1e",
        }))
        self.assertTrue(email_watch._delivery_owns_learning_record({
            "production_route": "legacy_fallback",
        }))
        self.assertFalse(email_watch._delivery_owns_learning_record({}))
        self.assertFalse(email_watch._delivery_owns_learning_record(None))


class RawSummaryProvenance(unittest.TestCase):
    def test_07_raw_summary_is_persisted_with_a_strict_cap(self):
        msg = exact_email()
        facts = engine._facts(msg, email_feature_extractor.extract_features(msg))
        decision, errors, repairs, _ = core.normalize_and_expand_detailed(
            observed_bad_raw_core(),
            message_key="USTC:raw-summary",
            facts=facts,
        )
        self.assertFalse(errors, errors)
        raw_summary = observed_bad_raw_core()["summary"]
        result = {
            "ok": True,
            "decision": decision,
            "message_key": "USTC:raw-summary",
            "facts": facts,
            "model": "qwen2.5:3b",
            "latency_ms": 67219,
            "fallback_used": False,
            "timeout": False,
            "cache_hit": False,
            "error_code": "",
            "llm_called": True,
            "decision_source": "llm_validated",
            "raw_category": "newsletter_marketing",
            "raw_importance": "critical",
            "raw_risk_level": "low",
            "raw_summary": raw_summary,
            "fallback_reason": "",
            "normalization_repairs": repairs,
            "trace_signals": engine._trace_signals(facts),
        }
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "learning.sqlite"
            rec = engine.persist_production_observation(
                msg, result, production_route="adaptive_v1e", db_path=db
            )
            self.assertTrue(rec["ok"], rec)
            con = sqlite3.connect(db)
            row = con.execute(
                "SELECT raw_summary,raw_importance,raw_risk_level,"
                "validated_category,trace_signals_json "
                "FROM semantic_observations"
            ).fetchone()
            con.close()
        self.assertEqual(row[0], raw_summary)
        self.assertLessEqual(len(row[0]), 600)
        self.assertEqual(row[1:4], ("critical", "low", "newsletter_marketing"))
        signals = json.loads(row[4])
        self.assertFalse(signals["deadline_phrase"])
        self.assertFalse(signals["receipt_phrase"])
        self.assertTrue(signals["no_deadline_phrase"])
        self.assertTrue(signals["no_receipt_phrase"])


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    )
    print(
        "NEWTON_REAL_E2E_GROUNDING_REGRESSION="
        f"{result.testsRun-len(result.failures)-len(result.errors)}/{result.testsRun}"
    )
    raise SystemExit(0 if result.wasSuccessful() else 1)
