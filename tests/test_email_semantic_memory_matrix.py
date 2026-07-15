#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import email_semantic_memory as mem


def decision(**overrides):
    base = {
        "classification": {"category": "school_notice", "label": "学校通知", "confidence": 0.9},
        "importance": {"level": "high", "reason": "deadline"},
        "notification": {
            "should_notify": True,
            "content_mode": "summary_plus_original",
            "summary_style": "bullets",
            "summary": "",
            "key_points": ["submit form"],
            "original_policy": "full",
            "original_reason": "details matter",
            "special_card": "none",
        },
        "memory_observation": {
            "sender_preference_candidate": {"original_policy": "full"},
            "topic_tags": ["研究生培养", "中期检查"],
            "user_preference_candidate": "学校通知附原文",
        },
    }
    for key, value in overrides.items():
        base[key] = value
    return base


class SemanticMemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "memory.sqlite"
        self.email = {"from": "Advisor <advisor@example.edu>", "subject": "Midterm", "body": "secret body"}
        self.renderer = {"render": {"content_mode": "summary_plus_original"}}

    def tearDown(self):
        self.tmp.cleanup()

    def rows(self, table):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
        finally:
            conn.close()

    def test_01_marker(self):
        self.assertEqual(mem.MARKER, "EMAIL_WATCHDOG_SEMANTIC_MEMORY_SHADOW_V1")

    def test_02_schema_version(self):
        self.assertEqual(mem.SCHEMA_VERSION, 1)

    def test_03_defaults_shadow(self):
        s = mem._settings({})
        self.assertEqual(s["mode"], "shadow")
        self.assertFalse(s["runtime_activation"])

    def test_04_runtime_activation_cannot_be_enabled(self):
        s = mem._settings({"runtime_activation": True})
        self.assertFalse(s["runtime_activation"])

    def test_05_schema_create(self):
        self.assertTrue(mem.ensure_schema(self.db)["ok"])
        conn = sqlite3.connect(self.db)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        self.assertIn("semantic_feedback", tables)
        self.assertIn("semantic_memories", tables)

    def test_06_schema_idempotent(self):
        self.assertTrue(mem.ensure_schema(self.db)["ok"])
        self.assertTrue(mem.ensure_schema(self.db)["ok"])

    def test_07_no_raw_body_column(self):
        mem.ensure_schema(self.db)
        conn = sqlite3.connect(self.db)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(semantic_memories)")}
        conn.close()
        self.assertNotIn("body", cols)
        self.assertNotIn("raw_body", cols)

    def test_08_disabled_skips(self):
        out = mem.shadow_observe(self.email, decision(), message_key="m1", db_path=self.db, settings_override={"enabled": False})
        self.assertTrue(out["skipped"])

    def test_09_non_shadow_skips(self):
        out = mem.shadow_observe(self.email, decision(), message_key="m1", db_path=self.db, settings_override={"mode": "active"})
        self.assertTrue(out["skipped"])
        self.assertFalse(out["runtime_activation"])

    def test_10_sender_memory_recorded(self):
        out = mem.shadow_observe(self.email, decision(), message_key="m1", renderer=self.renderer, db_path=self.db)
        self.assertTrue(out["ok"])
        types = {r["memory_type"] for r in self.rows("semantic_memories")}
        self.assertIn("sender_semantics", types)

    def test_11_sender_scope_is_hashed(self):
        mem.shadow_observe(self.email, decision(), message_key="m1", db_path=self.db)
        row = next(r for r in self.rows("semantic_memories") if r["memory_type"] == "sender_semantics")
        self.assertEqual(len(row["scope_key"]), 64)
        self.assertNotIn("advisor@example.edu", row["scope_key"])

    def test_12_domain_memory_recorded(self):
        mem.shadow_observe(self.email, decision(), message_key="m1", db_path=self.db)
        row = next(r for r in self.rows("semantic_memories") if r["memory_type"] == "domain_semantics")
        self.assertEqual(row["scope_key"], "example.edu")

    def test_13_topic_memories_recorded(self):
        mem.shadow_observe(self.email, decision(), message_key="m1", db_path=self.db)
        topics = [r for r in self.rows("semantic_memories") if r["memory_type"] == "topic_semantics"]
        self.assertEqual(len(topics), 2)

    def test_14_display_mode_memory_recorded(self):
        mem.shadow_observe(self.email, decision(), message_key="m1", db_path=self.db)
        types = {r["memory_type"] for r in self.rows("semantic_memories")}
        self.assertIn("display_mode_sender", types)

    def test_15_sender_candidate_recorded_shadow(self):
        mem.shadow_observe(self.email, decision(), message_key="m1", db_path=self.db)
        row = next(r for r in self.rows("semantic_memories") if r["memory_type"] == "model_sender_preference_candidate")
        self.assertEqual(row["status"], "shadow")

    def test_16_user_candidate_recorded_shadow(self):
        mem.shadow_observe(self.email, decision(), message_key="m1", db_path=self.db)
        row = next(r for r in self.rows("semantic_memories") if r["memory_type"] == "model_user_preference_candidate")
        self.assertEqual(row["ground_truth_count"], 0)

    def test_17_model_observation_not_ground_truth(self):
        mem.shadow_observe(self.email, decision(), message_key="m1", db_path=self.db)
        for row in self.rows("semantic_memories"):
            self.assertEqual(row["positive_count"], 0)
            self.assertEqual(row["ground_truth_count"], 0)
            self.assertEqual(row["source"], "model_observation")

    def test_18_same_message_is_idempotent(self):
        mem.shadow_observe(self.email, decision(), message_key="m1", db_path=self.db)
        mem.shadow_observe(self.email, decision(), message_key="m1", db_path=self.db)
        row = next(r for r in self.rows("semantic_memories") if r["memory_type"] == "sender_semantics")
        self.assertEqual(row["observation_count"], 1)

    def test_19_new_message_increments(self):
        mem.shadow_observe(self.email, decision(), message_key="m1", db_path=self.db)
        mem.shadow_observe(self.email, decision(), message_key="m2", db_path=self.db)
        row = next(r for r in self.rows("semantic_memories") if r["memory_type"] == "sender_semantics")
        self.assertEqual(row["observation_count"], 2)

    def test_20_evidence_is_bounded(self):
        for i in range(15):
            mem.shadow_observe(self.email, decision(), message_key=f"m{i}", db_path=self.db, settings_override={"max_evidence_keys": 10})
        row = next(r for r in self.rows("semantic_memories") if r["memory_type"] == "sender_semantics")
        doc = json.loads(row["memory_json"])
        self.assertLessEqual(len(doc["evidence_message_hashes"]), 10)

    def test_21_body_not_persisted(self):
        mem.shadow_observe(self.email, decision(), message_key="m1", db_path=self.db)
        text = "\n".join(r["memory_json"] for r in self.rows("semantic_memories"))
        self.assertNotIn("secret body", text)

    def test_22_feedback_requires_explicit_flag(self):
        out = mem.record_feedback("m1", "category_correction", new_value="personal", db_path=self.db)
        self.assertFalse(out["ok"])
        self.assertFalse(out["ground_truth_written"])

    def test_23_feedback_type_is_validated(self):
        out = mem.record_feedback("m1", "model_agreement", new_value="x", explicit_user_feedback=True, db_path=self.db)
        self.assertFalse(out["ok"])

    def test_24_explicit_feedback_is_ground_truth(self):
        out = mem.record_feedback("m1", "category_correction", old_value="school", new_value="personal", explicit_user_feedback=True, db_path=self.db)
        self.assertTrue(out["ground_truth_written"])
        self.assertEqual(self.rows("semantic_feedback")[0]["is_ground_truth"], 1)

    def test_25_explicit_feedback_confirms_memory(self):
        out = mem.record_feedback(
            "m1", "content_mode_preference", new_value="summary_only",
            memory_type="sender_content_mode", scope_key="abc", explicit_user_feedback=True, db_path=self.db,
        )
        self.assertTrue(out["memory_updated"])
        row = self.rows("semantic_memories")[0]
        self.assertEqual(row["status"], "confirmed")
        self.assertEqual(row["source"], "explicit_user_feedback")
        self.assertEqual(row["ground_truth_count"], 1)

    def test_26_retrieve_confirmed_only(self):
        mem.shadow_observe(self.email, decision(), message_key="m0", db_path=self.db)
        mem.record_feedback(
            "m1", "sender_preference", new_value="important",
            memory_type="sender_preference", scope_key="abc", explicit_user_feedback=True, db_path=self.db,
        )
        out = mem.retrieve_confirmed(db_path=self.db)
        self.assertEqual(len(out["items"]), 1)
        self.assertEqual(out["items"][0]["status"], "confirmed")

    def test_27_retrieval_is_bounded(self):
        for i in range(8):
            mem.record_feedback(
                f"m{i}", "topic_preference", new_value=f"v{i}",
                memory_type="topic_preference", scope_key=f"k{i}", explicit_user_feedback=True, db_path=self.db,
            )
        out = mem.retrieve_confirmed(limit=5, db_path=self.db)
        self.assertEqual(len(out["items"]), 5)

    def test_28_prompt_examples_hard_disabled(self):
        self.assertEqual(mem.prompt_examples(self.email), [])

    def test_29_status_contract(self):
        mem.ensure_schema(self.db)
        out = mem.status(self.db)
        self.assertFalse(out["runtime_activation"])
        self.assertFalse(out["candidate_promotion_executed"])
        self.assertFalse(out["learned_category_rules_written"])

    def test_30_confirmed_memory_not_overwritten_by_model(self):
        sender_hash = mem._sender_identity(self.email)["sender_hash"]
        mem.record_feedback(
            "m0", "sender_preference", new_value="always important",
            memory_type="sender_semantics", scope_key=sender_hash,
            explicit_user_feedback=True, db_path=self.db,
        )
        before = self.rows("semantic_memories")[0]
        mem.shadow_observe(self.email, decision(), message_key="m1", db_path=self.db)
        after = next(r for r in self.rows("semantic_memories") if r["memory_type"] == "sender_semantics")
        self.assertEqual(after["status"], "confirmed")
        self.assertEqual(after["source"], "explicit_user_feedback")
        self.assertEqual(after["memory_json"], before["memory_json"])

    def test_31_model_candidate_text_is_not_persisted(self):
        d = decision()
        d["memory_observation"]["user_preference_candidate"] = "secret body"
        mem.shadow_observe(self.email, d, message_key="m1", db_path=self.db)
        row = next(r for r in self.rows("semantic_memories") if r["memory_type"] == "model_user_preference_candidate")
        self.assertNotIn("secret body", row["memory_json"])
        self.assertIn("candidate_hash", row["memory_json"])

    def test_32_invalid_input_safe(self):
        out = mem.shadow_observe(self.email, {}, message_key="", db_path=self.db)
        self.assertFalse(out["ok"])
        self.assertFalse(out["runtime_activation"])


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(SemanticMemoryTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(f"semantic_memory_matrix={result.testsRun - len(result.failures) - len(result.errors)}/{result.testsRun} passed")
    raise SystemExit(0 if result.wasSuccessful() else 1)
