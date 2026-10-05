#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import email_assistant_composer as composer
import email_evidence_contract as evidence
import email_editorial_review
import email_semantic_engine
import email_semantic_schema


class EvidenceContractTests(unittest.TestCase):
    def review_invitation(self):
        return {
            "account": "USTC",
            "subject": "Invitation to review a manuscript for Cluster Computing",
            "from_name": "Cluster Computing",
            "from_addr": "peer-review@springernature.com",
            "date_received": "2026-10-03T23:04:28+08:00",
            "body": (
                "Please let us know if you are available by accepting or declining the invitation.\n"
                "Accept or decline: https://reviewer-feedback.springernature.com/review-invitation/token\n"
                "Dashboard: https://reviewer.springernature.com/\n"
                "Unsubscribe: https://reviewer-feedback.springernature.com/reviewer-opt-out/token"
            ),
            "links": [
                {
                    "url": "https://reviewer-feedback.springernature.com/review-invitation/token",
                    "display_text": "Accept or decline this invitation and view due date",
                },
                {"url": "https://reviewer.springernature.com/", "display_text": "Reviewer Dashboard"},
                {
                    "url": "https://reviewer-feedback.springernature.com/reviewer-opt-out/token",
                    "display_text": "Stop all future invitations",
                },
            ],
            "attachments": [],
        }

    def decision(self, link_ids):
        return {
            "classification": {"category": "paper_manuscript_feedback", "label": "论文/稿件反馈"},
            "importance": {"level": "high"},
            "notification": {
                "content_mode": "summary_only", "summary_style": "bullets", "summary": "",
                "key_points": ["期刊邀请审稿，需要接受或拒绝。"], "original_policy": "none",
            },
            "action": {
                "required": True, "type": "review_and_complete",
                "description": "接受或拒绝审稿邀请", "next_step": "", "link_ids": link_ids,
            },
            "deadline": {"has_deadline": False},
            "risk": {"level": "none", "notes": []},
        }

    def test_inventory_is_structural_not_action_verb_allowlist(self):
        items = evidence.link_inventory(self.review_invitation())
        self.assertEqual([item["id"] for item in items], ["link_0", "link_1", "link_2"])
        self.assertTrue(items[0]["display_safe"])
        self.assertTrue(items[1]["display_safe"])
        self.assertFalse(items[2]["display_safe"])

    def test_opaque_mail_chrome_url_is_rejected_by_source_label(self):
        email = {
            "links": [{
                "url": "https://mailer.example/opaque/7f8d9a",
                "display_text": "Manage email preferences",
            }]
        }
        item = evidence.link_inventory(email)[0]
        self.assertFalse(item["display_safe"])
        self.assertEqual(item["display_policy"], "mail_chrome_action")

    def test_model_selected_source_id_is_materialized_without_copying_url(self):
        result = composer.render_notification(
            self.review_invitation(), self.decision(["link_0"]), {}, {}
        )
        self.assertIn(
            "[Accept or decline this invitation and view due date]"
            "(https://reviewer-feedback.springernature.com/review-invitation/token)",
            result["text"],
        )
        self.assertNotIn("reviewer-opt-out", result["text"])

    def test_missing_model_selection_preserves_all_safe_links_neutrally(self):
        result = composer.render_notification(
            self.review_invitation(), self.decision([]), {}, {}
        )
        self.assertIn("review-invitation/token", result["text"])
        self.assertIn("reviewer.springernature.com", result["text"])
        self.assertNotIn("reviewer-opt-out", result["text"])

    def test_all_model_failure_is_transparent_and_lossless(self):
        result = evidence.render_lossless_fallback(
            self.review_invitation(), {}, reason="all routes failed"
        )
        self.assertEqual(result["renderer_version"], "evidence_lossless_v1")
        self.assertIn("智能分析暂不可用", result["text"])
        self.assertIn("review-invitation/token", result["text"])
        self.assertIn("Reviewer Dashboard", result["text"])
        self.assertNotIn("reviewer-opt-out", result["text"])
        self.assertNotIn("please decli\n", result["text"].lower())

    def test_editorial_failure_preserves_semantic_draft_and_all_safe_artifacts(self):
        email = self.review_invitation()
        email["attachments"] = [{"filename": "manuscript.pdf"}]
        result = evidence.render_evidence_complete_draft(
            email, "请接受或拒绝本次审稿邀请。", reason="editorial routes failed",
            delivered_attachments=[{"filename": "manuscript.pdf", "download_status": "downloaded"}],
        )
        self.assertEqual(result["renderer_version"], "evidence_complete_draft_v1")
        self.assertIn("请接受或拒绝本次审稿邀请", result["text"])
        self.assertIn("review-invitation/token", result["text"])
        self.assertIn("reviewer.springernature.com", result["text"])
        self.assertNotIn("reviewer-opt-out", result["text"])
        self.assertIn("manuscript.pdf", result["text"])
        self.assertIn("已附上", result["text"])

    def test_artifact_inventory_forces_independent_critic_even_when_model_omits_it(self):
        review = {
            "publish": True, "selected_links": [], "attachment_intent": "ignore",
            "live_action": {"required": False}, "temporal": {},
        }
        links = [{"display_safe": True}, {"display_safe": False}]
        self.assertTrue(email_editorial_review._needs_independent_critic(review, links, []))
        self.assertTrue(email_editorial_review._needs_independent_critic(review, [], [{"name": "a.pdf"}]))
        self.assertFalse(email_editorial_review._needs_independent_critic(review, [], []))

    def test_explicit_models_then_return_to_hermes_default_route(self):
        def route(_prompt, settings):
            model = settings.get("model")
            if model:
                raise email_semantic_engine.SemanticEngineError(f"403 for {model}")
            return {"parsed": {"ok": True}, "model": "hermes-live", "metrics": {}}

        with mock.patch.object(email_semantic_engine, "_call_model_once", side_effect=route) as call:
            result = email_semantic_engine.call_ollama("prompt", {
                "provider": "hermes",
                "model": "pinned-primary",
                "fallback_model": "pinned-fallback",
                "inherit_default_on_failure": True,
            })
        self.assertEqual([item.args[1]["model"] for item in call.call_args_list], [
            "pinned-primary", "pinned-fallback", "",
        ])
        self.assertEqual(result["model"], "hermes-live")
        self.assertTrue(result["metrics"]["hermes_default_route_used"])

    def test_editorial_review_uses_hermes_default_after_both_pins_fail(self):
        attempts = []

        def route(_prompt, settings):
            model = settings.get("model", "")
            attempts.append(model)
            if model:
                raise email_semantic_engine.SemanticEngineError(f"403 for {model}")
            return {"parsed": {"candidate": True}, "model": "hermes-live", "metrics": {}}

        normalized = {
            "ok": True,
            "publish": True,
            "live_action": {"required": False, "description": "", "evidence": ""},
        }
        with (
            mock.patch.object(email_semantic_engine, "_call_model_once", side_effect=route),
            mock.patch.object(email_editorial_review, "_normalize_result", return_value=(normalized, [])),
            mock.patch.object(email_editorial_review, "_needs_independent_critic", return_value=False),
        ):
            result = email_editorial_review.review_notification(
                self.review_invitation(), self.decision(["link_0"]), "draft",
                settings_override={
                    "provider": "hermes",
                    "model": "pinned-primary",
                    "fallback_model": "pinned-fallback",
                    "inherit_default_on_failure": True,
                },
            )
        self.assertEqual(attempts, ["pinned-primary", "pinned-fallback", ""])
        self.assertTrue(result["ok"])
        self.assertEqual(result["model"], "hermes-live")

    def test_schema_accepts_only_safe_source_link_ids(self):
        raw = email_semantic_schema._base_decision("message-1")
        raw["notification"].update({
            "summary_style": "bullets",
            "summary": "",
            "key_points": ["Action is required."],
        })
        raw["action"] = {
            "required": True,
            "type": "review_and_complete",
            "description": "Accept or decline the invitation",
            "next_step": "",
            "link_ids": ["link_0"],
        }
        facts = {
            "attachments_present": False,
            "attachment_names": [],
            "source_link_inventory": [
                {"id": "link_0", "display_safe": True},
                {"id": "link_1", "display_safe": False},
            ],
        }
        decision, errors = email_semantic_schema.normalize_and_validate(
            raw, message_key="message-1", facts=facts
        )
        self.assertEqual(errors, [])
        self.assertEqual(decision["action"]["link_ids"], ["link_0"])

        raw["action"]["link_ids"] = ["link_1"]
        decision, errors = email_semantic_schema.normalize_and_validate(
            raw, message_key="message-1", facts=facts
        )
        self.assertIsNone(decision)
        self.assertIn("action.link_ids contains unknown or unsafe source IDs", errors)

    def test_schema_invalid_pins_continue_to_hermes_default_route(self):
        email = {
            "id": "schema-route", "account": "USTC", "subject": "普通通知",
            "body": "这是一封普通通知，无需回复。", "attachments": [], "links": [],
        }
        features = email_semantic_engine.email_feature_extractor.extract_features(email)
        message_key = features["message_key"]
        valid = email_semantic_schema._base_decision(message_key)
        valid["classification"] = {
            "category": "personal_or_general", "label": "个人/一般邮件", "confidence": 0.9,
        }
        valid["importance"] = {"level": "normal", "reason": "普通通知"}
        valid["notification"].update({
            "summary": "收到一封普通通知。", "original_reason": "", "special_card": "none",
        })
        primary = {"parsed": {"unexpected": True}, "model": "primary", "metrics": {}}
        invalid_fallback = {"parsed": None, "model": "fallback", "metrics": {}}
        default = {"parsed": valid, "model": "hermes-live", "metrics": {}}
        with (
            mock.patch.object(email_semantic_engine, "call_ollama", return_value=primary),
            mock.patch.object(
                email_semantic_engine, "_call_model_once",
                side_effect=[invalid_fallback, default],
            ) as call,
        ):
            result = email_semantic_engine.analyze_email(
                email, {"category": "unknown_needs_llm", "action": "needs_llm"}, {},
                settings_override={
                    "mode": "shadow", "cache_by_message_hash": False,
                    "provider": "hermes", "model": "primary", "fallback_model": "fallback",
                    "inherit_default_on_failure": True,
                },
            )
        self.assertEqual([item.args[1]["model"] for item in call.call_args_list], ["fallback", ""])
        self.assertFalse(result["fallback_used"])
        self.assertTrue(result["model_fallback_used"])
        self.assertEqual(result["model"], "hermes-live")
        self.assertTrue(result["ollama_metrics"]["hermes_default_route_used"])


if __name__ == "__main__":
    unittest.main()
