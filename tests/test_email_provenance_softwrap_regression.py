#!/usr/bin/env python3
from __future__ import annotations

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
import email_learning
import email_semantic_engine as engine


def wrapped_email() -> dict:
    return {
        "id": "wrapped-newton-1",
        "msg_id": "wrapped-newton-1",
        "account": "USTC",
        "subject": "[SPAM] Read the latest issue of Newton — WRAPPED-NEGATION",
        "from_addr": "sender@example.test",
        "body": (
            "Read the latest issue of Newton\n\n"
            "No response or immediate action is required. There is no deadline, account alert,\n"
            "manuscript decision, invoice, payment request, receipt, or confidential material\n"
            "in this message.\n\n"
            "Manage preferences or unsubscribe."
        ),
        "attachments": [],
    }


class SoftWrapNegationRegression(unittest.TestCase):
    def test_01_soft_line_wrap_does_not_reset_negation_scope(self):
        msg = wrapped_email()
        facts = engine._facts(msg, email_feature_extractor.extract_features(msg))
        hints = facts["semantic_hints"]
        self.assertTrue(hints["no_deadline_phrase"])
        self.assertTrue(hints["no_receipt_phrase"])
        self.assertFalse(hints["deadline_phrase"])
        self.assertFalse(hints["receipt_phrase"])

    def test_02_blank_line_resets_negation_scope(self):
        msg = wrapped_email()
        msg["body"] = (
            "There is no invoice or receipt in the first notice.\n\n"
            "Invoice attached for your records."
        )
        facts = engine._facts(msg, email_feature_extractor.extract_features(msg))
        self.assertTrue(facts["semantic_hints"]["receipt_phrase"])

    def test_03_contrast_word_resets_negation_scope_across_soft_wrap(self):
        msg = wrapped_email()
        msg["body"] = (
            "There is no invoice in the preliminary notice,\n"
            "however, payment successful and receipt attached."
        )
        facts = engine._facts(msg, email_feature_extractor.extract_features(msg))
        self.assertTrue(facts["semantic_hints"]["receipt_phrase"])


class UsedLlmProvenanceRegression(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_root = email_learning.ROOT
        self.old_db = email_learning.DB_PATH
        email_learning.ROOT = Path(self.tmp.name)
        email_learning.DB_PATH = Path(self.tmp.name) / "learning.sqlite"

    def tearDown(self):
        email_learning.ROOT = self.old_root
        email_learning.DB_PATH = self.old_db
        self.tmp.cleanup()

    def _record(self, *, llm_called: bool, model: str, route_lane: str = "durable") -> int:
        msg = wrapped_email()
        rule = {"category": "广告", "action": "needs_llm", "priority": "normal"}
        analysis = {
            "semantic_category": "newsletter_marketing",
            "final_category": "newsletter_marketing",
            "user_relevance": "low",
            "confidence": 0.95,
            "format_decision": "summary_only",
        }
        delivery = {
            "status": "pushed",
            "production_route": "adaptive_v1e",
            "route_lane": route_lane,
            "semantic": {
                "llm_called": llm_called,
                "model": model,
                "raw_category": "newsletter_marketing",
                "validated_category": "newsletter_marketing",
            },
        }
        rec = email_learning.record_decision(msg, rule, analysis, delivery, {"label": "USTC"})
        self.assertTrue(rec["ok"], rec)
        con = sqlite3.connect(email_learning.DB_PATH)
        try:
            row = con.execute("SELECT used_llm,decision_json FROM message_decisions").fetchone()
        finally:
            con.close()
        decision = json.loads(row[1])
        self.assertEqual(bool(decision["delivery"]["semantic"]["llm_called"]), llm_called)
        return int(row[0])

    def test_04_durable_semantic_delivery_sets_used_llm(self):
        self.assertEqual(self._record(llm_called=True, model="qwen2.5:3b"), 1)

    def test_05_fast_lane_remains_non_llm(self):
        self.assertEqual(self._record(llm_called=False, model="hermes", route_lane="fast"), 0)


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    )
    passed = result.testsRun - len(result.failures) - len(result.errors)
    print(f"PROVENANCE_SOFTWRAP_REGRESSION={passed}/{result.testsRun}")
    raise SystemExit(0 if result.wasSuccessful() else 1)
