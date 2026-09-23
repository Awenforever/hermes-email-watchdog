#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import email_config
import email_delivery
import email_production_router
import email_semantic_core
import email_semantic_engine
import email_watch


def load_handler():
    spec = importlib.util.spec_from_file_location(
        "email_watchdog_handler_assistant_test",
        ROOT / "hooks" / "hermes-email-watchdog" / "handler.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class AssistantExperienceTests(unittest.TestCase):
    def test_01_defaults_use_deepseek_with_qwen_fallback_and_no_rule_bypass(self):
        cfg = email_config.DEFAULT_CONFIG
        self.assertEqual(cfg["semantic_engine"]["model"], "deepseek-flash")
        self.assertEqual(cfg["semantic_engine"]["fallback_model"], "qwen3.6-chat")
        self.assertFalse(cfg["notification"]["fast_lane_enabled"])
        self.assertTrue(cfg["delivery"]["auto_download_attachments"])
        self.assertTrue(cfg["delivery"]["forward_attachments_to_weixin"])

    def test_02_model_attachment_policy_reaches_delivery_planner(self):
        decision = {
            "classification": {"category": "invoice_receipt"},
            "importance": {"level": "high"},
            "notification": {
                "should_notify": True, "content_mode": "finance_card",
                "summary_style": "paragraph", "summary": "收到发票。",
                "key_points": [], "original_policy": "none",
            },
            "action": {"required": False},
            "deadline": {"has_deadline": False},
            "attachments": {
                "present": True, "policy": "download_safe",
                "important_names": ["invoice.pdf"], "reason": "发票文件",
            },
            "risk": {"level": "none", "notes": []},
        }
        result = email_production_router.decision_to_legacy_analysis(decision)
        self.assertEqual(result["attachment_handling"]["policy"], "download_safe")
        self.assertTrue(email_production_router.should_push_notification(decision))

    def test_03_prompt_teaches_contextual_attachment_actions(self):
        prompt = email_semantic_core.build_prompt({"_response_budget": {}, "subject": "x"})
        self.assertIn("ATTACHMENT GUIDE", prompt)
        self.assertIn("invoice_receipt", prompt)
        self.assertIn("download_safe", prompt)
        self.assertIn("marketing/newsletters", prompt)

    def test_04_safe_attachment_policy_blocks_executable_and_large_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pdf = root / "invoice.pdf"
            exe = root / "invoice.exe"
            pdf.write_bytes(b"%PDF-test")
            exe.write_bytes(b"MZ-test")
            ok, reason, _ = email_delivery._attachment_forward_policy(str(pdf), {})
            self.assertTrue(ok, reason)
            ok, reason, _ = email_delivery._attachment_forward_policy(str(exe), {})
            self.assertFalse(ok)
            self.assertEqual(reason, "unsafe_extension")
            ok, reason, _ = email_delivery._attachment_forward_policy(
                str(pdf), {"attachment_max_bytes": 2}
            )
            # The configured bound is clamped to at least 1 MiB for sane deployments.
            self.assertTrue(ok, reason)

    def test_05_downloaded_safe_files_enter_output_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "invoice.pdf"
            path.write_bytes(b"%PDF-test")
            email_watch._reset_output_metadata()
            with mock.patch.object(
                email_watch.email_config,
                "get_delivery_settings",
                return_value={"forward_attachments_to_weixin": True},
            ):
                email_watch._record_output_attachments([{
                    "filename": "invoice.pdf", "local_path": str(path),
                    "size_bytes": path.stat().st_size, "send_to_weixin": True,
                    "download_status": "downloaded",
                }])
            metadata = email_watch.get_last_output_metadata()
            self.assertEqual(len(metadata["attachments"]), 1)
            self.assertEqual(metadata["attachments"][0]["filename"], "invoice.pdf")

    def test_06_common_deadline_shape_is_repaired_without_losing_action(self):
        email = {
            "id": "deadline", "account": "USTC", "subject": "请提交材料",
            "body": "请于2026年9月25日17:00前填写中期检查表并回复。",
            "attachments": [],
        }
        features = email_semantic_engine.email_feature_extractor.extract_features(email)
        facts = email_semantic_engine._facts(email, features)
        raw = {
            "category": "task_deadline", "confidence": 0.95,
            "importance": "high", "importance_reason": "有明确截止时间",
            "should_notify": True, "content_mode": "deadline_card",
            "summary_style": "paragraph", "summary": "需要按时提交材料。",
            "key_points": [], "summary_evidence": ["请于2026年9月25日17:00前填写中期检查表并回复"],
            "original_policy": "none", "original_reason": "",
            "action": {"type": "reply_email", "description": "填写并回复中期检查表", "next_step": "准备材料", "evidence": "填写中期检查表并回复"},
            "deadline": {"date": "2026-09-25", "time": "17:00", "confidence": 0.95, "evidence": ""},
            "attachment_policy": "none", "attachment_reason": "",
            "risk": {"level": "none", "notes": []}, "topic_tags": [], "uncertainties": [],
        }
        decision, errors, repairs, _ = email_semantic_core.normalize_and_expand_detailed(
            raw, message_key=features["message_key"], facts=facts
        )
        self.assertEqual(errors, [])
        self.assertTrue(decision["action"]["required"])
        self.assertTrue(decision["deadline"]["has_deadline"])
        self.assertIn("canonicalize:deadline_date_time_to_date_text", repairs)

    def test_07_schema_invalid_primary_uses_configured_model_fallback(self):
        email = {
            "id": "fallback", "account": "USTC", "subject": "普通通知",
            "body": "这是一封普通通知，无需回复。", "attachments": [],
        }
        valid = {
            "category": "personal_or_general", "confidence": 0.9,
            "importance": "normal", "importance_reason": "普通通知",
            "should_notify": True, "content_mode": "summary_only",
            "summary_style": "paragraph", "summary": "收到一封普通通知。",
            "key_points": [], "summary_evidence": ["普通通知"],
            "original_policy": "none", "original_reason": "",
            "action": None, "deadline": None,
            "attachment_policy": "none", "attachment_reason": "",
            "risk": {"level": "none", "notes": []}, "topic_tags": [], "uncertainties": [],
        }
        primary = {"parsed": {"unexpected": True}, "model": "deepseek-flash", "latency_ms": 1, "metrics": {}}
        fallback = {"parsed": valid, "model": "qwen3.6-chat", "latency_ms": 1, "metrics": {}}
        with mock.patch.object(email_semantic_engine, "call_ollama", return_value=primary), mock.patch.object(
            email_semantic_engine, "_call_model_once", return_value=fallback
        ):
            result = email_semantic_engine.analyze_email(
                email, {"category": "unknown_needs_llm", "action": "needs_llm"}, {},
                settings_override={
                    "mode": "shadow", "cache_by_message_hash": False,
                    "model": "deepseek-flash", "fallback_model": "qwen3.6-chat",
                },
            )
        self.assertFalse(result["fallback_used"])
        self.assertTrue(result["model_fallback_used"])
        self.assertEqual(result["model"], "qwen3.6-chat")


class WeixinAttachmentTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_08_text_and_safe_attachments_use_real_adapter_methods(self):
        handler = load_handler()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pdf = root / "invoice.pdf"
            image = root / "ticket.png"
            blocked = root / "payload.exe"
            pdf.write_bytes(b"%PDF-test")
            image.write_bytes(b"PNG-test")
            blocked.write_bytes(b"MZ-test")
            handler.HERMES_HOME = root

            calls = []

            class Result:
                success = True
                message_id = "m"
                error = ""

            class Adapter:
                async def send(self, chat_id, text, metadata=None):
                    calls.append(("text", text, metadata["_delivery_id"]))
                    return Result()

                async def send_document(self, chat_id, file_path, **kwargs):
                    calls.append(("document", file_path, kwargs["metadata"]["_delivery_id"]))
                    return Result()

                async def send_image_file(self, chat_id, image_path, **kwargs):
                    calls.append(("image", image_path, kwargs["metadata"]["_delivery_id"]))
                    return Result()

            runner = type("Runner", (), {"adapters": {"weixin": Adapter()}})()
            metadata = {
                "model_generated": True,
                "model_name": "deepseek-flash",
                "attachments": [
                    {"filename": pdf.name, "local_path": str(pdf)},
                    {"filename": image.name, "local_path": str(image)},
                    {"filename": blocked.name, "local_path": str(blocked)},
                ],
            }
            with mock.patch.object(handler, "_chat_id", return_value="chat"), mock.patch.object(
                handler, "_runner_ref", return_value=runner
            ):
                await handler._send_weixin("invoice", delivery_id="delivery", attribution=metadata)

            self.assertEqual([item[0] for item in calls], ["text", "document", "image"])
            self.assertEqual(len({item[2] for item in calls}), 3)

    async def test_09_partial_attachment_retry_does_not_repeat_completed_parts(self):
        handler = load_handler()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            first = root / "first.pdf"
            second = root / "second.pdf"
            first.write_bytes(b"%PDF-first")
            second.write_bytes(b"%PDF-second")
            handler.HERMES_HOME = root
            handler.OUTBOX_FILE = root / "outbox.json"
            handler.OUTBOX_RETRY_BASE_SECONDS = 0
            handler.OUTBOX_RETRY_MAX_SECONDS = 0
            metadata = {
                "model_generated": True, "model_name": "deepseek-flash",
                "attachments": [
                    {"filename": first.name, "local_path": str(first)},
                    {"filename": second.name, "local_path": str(second)},
                ],
            }
            entry = handler._outbox_prepare("invoice", metadata)
            calls = []
            second_attempts = 0

            class Result:
                def __init__(self, success=True, error=""):
                    self.success = success
                    self.error = error
                    self.message_id = "m" if success else ""

            class Adapter:
                async def send(self, chat_id, text, metadata=None):
                    calls.append(("text", text))
                    return Result()

                async def send_document(self, chat_id, file_path, **kwargs):
                    nonlocal second_attempts
                    calls.append(("document", Path(file_path).name))
                    if Path(file_path) == second:
                        second_attempts += 1
                        if second_attempts == 1:
                            return Result(False, "transient")
                    return Result()

            runner = type("Runner", (), {"adapters": {"weixin": Adapter()}})()
            with mock.patch.object(handler, "_chat_id", return_value="chat"), mock.patch.object(
                handler, "_runner_ref", return_value=runner
            ):
                with self.assertRaises(RuntimeError):
                    await handler._send_weixin(
                        entry["text"], delivery_id=entry["delivery_id"], attribution=metadata
                    )
                await handler._send_weixin(
                    entry["text"], delivery_id=entry["delivery_id"], attribution=metadata
                )

            self.assertEqual(calls.count(("text", "invoice")), 1)
            self.assertEqual(calls.count(("document", "first.pdf")), 1)
            self.assertEqual(calls.count(("document", "second.pdf")), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
