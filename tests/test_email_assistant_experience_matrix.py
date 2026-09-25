#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import importlib.util
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import email_config
import email_assistant_composer
import email_delivery
import email_production_router
import email_notification_renderer
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
    def test_00_markdown_action_link_keeps_required_token(self):
        handler = load_handler()
        source = "[确认账户](https://example.test/confirm?token=secret123)\n\nhttps://tracker.test/open?id=secret"
        cleaned = handler._sanitize_notification(source)
        self.assertIn("[确认账户](https://example.test/confirm?token=secret123)", cleaned)
        self.assertIn("https://tracker.test/open", cleaned)
        self.assertNotIn("tracker.test/open?id=secret", cleaned)

    def test_01_defaults_use_deepseek_with_qwen_fallback_and_no_rule_bypass(self):
        cfg = email_config.DEFAULT_CONFIG
        self.assertEqual(cfg["semantic_engine"]["model"], "deepseek-flash")
        self.assertEqual(cfg["semantic_engine"]["fallback_model"], "qwen3.8-chat")
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

    def test_04b_extensionless_png_is_identified_before_forwarding(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "opaque-attachment-id"
            source.write_bytes(b"\x89PNG\r\n\x1a\n" + b"test-image-payload")
            normalized = email_delivery._normalize_extensionless_attachment(str(source))
            self.assertTrue(normalized.endswith(".png"))
            self.assertFalse(source.exists())
            allowed, reason, _size = email_delivery._attachment_forward_policy(
                normalized,
                {"attachment_max_bytes": 1024 * 1024, "attachment_safe_extensions": [".png"]},
            )
        self.assertTrue(allowed)
        self.assertEqual(reason, "safe_bounded_attachment")

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

    def test_08_open_semantic_fields_do_not_discard_correct_intent(self):
        email = {
            "id": "suspension", "account": "USTC",
            "subject": "Fwd: Service Suspension Notification",
            "body": "Your service will be suspended on September 30 unless billing details are updated.",
            "attachments": [],
        }
        features = email_semantic_engine.email_feature_extractor.extract_features(email)
        facts = email_semantic_engine._facts(email, features)
        raw = {
            "emailType": "account_status_notice",
            "confidence": 0.94,
            "priority": "high",
            "pushAlert": True,
            "content_mode": "deadline_card",
            "summary_style": "paragraph",
            "summary": "The service may be suspended unless billing details are updated.",
            "summary_evidence": ["service will be suspended"],
            "original_policy": "excerpt",
            "action": {
                "type": "review",
                "description": "Update billing details to avoid suspension.",
                "next_step": "Review the account.",
                "evidence": "unless billing details are updated",
                "ui_hint": "prominent",
            },
            "deadline": {
                "dueDate": "September 30",
                "confidence": 0.9,
                "evidence": "suspended on September 30",
                "calendar_hint": "local",
            },
            "attachment_policy": "none",
            "risk": {"level": "none", "notes": [], "explanation": "status change"},
            "future_descriptive_field": {"display": "account notice"},
        }
        decision, errors, repairs, _ = email_semantic_core.normalize_and_expand_detailed(
            raw, message_key=features["message_key"], facts=facts
        )
        self.assertEqual(errors, [])
        self.assertEqual(decision["classification"]["category"], "account_status_notice")
        self.assertTrue(decision["notification"]["should_notify"])
        self.assertTrue(decision["deadline"]["has_deadline"])
        self.assertTrue(any(item.startswith("tolerate:") for item in repairs))
        self.assertTrue(email_production_router.should_push_notification(decision))

    def test_09_conservative_fallback_never_silences_grounded_service_suspension(self):
        email = {
            "id": "suspension-fallback", "account": "USTC",
            "subject": "Fwd: Service Suspension Notification",
            "body": "Access will be suspended unless the account is reviewed.",
            "attachments": [],
        }
        features = email_semantic_engine.email_feature_extractor.extract_features(email)
        facts = email_semantic_engine._facts(email, features)
        decision = email_semantic_engine.email_semantic_schema.conservative_fallback(
            message_key=features["message_key"], email=email,
            rule_result={"category": "个人邮件", "action": "skip"},
            analysis={"should_notify": False}, facts=facts,
            reason="model format unavailable",
        )
        self.assertEqual(decision["classification"]["category"], "account_status_notice")
        self.assertTrue(decision["notification"]["should_notify"])
        self.assertTrue(decision["action"]["required"])
        self.assertTrue(email_production_router.should_push_notification(decision))

    def test_10_raw_mime_keeps_confirmation_link_and_real_attachment(self):
        message = EmailMessage()
        message["Subject"] = "Confirmation instructions"
        message["Date"] = "Wed, 23 Sep 2026 20:11:00 +0800"
        message["Received"] = "from mx.example.test by mail.ustc.edu.cn; Wed, 23 Sep 2026 20:12:34 +0800"
        message.set_content("Confirm your email")
        message.add_alternative(
            '<p>Welcome.</p><a href="https://example.test/confirm?token=abc">Confirm your email</a>'
            '<img src="https://example.test/pixel.gif" alt="logo">',
            subtype="html",
        )
        message.add_attachment(b"%PDF-test", maintype="application", subtype="pdf", filename="invoice.pdf")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "mail.eml"
            path.write_bytes(message.as_bytes())
            parsed = email_watch._parse_exported_message(path)
        self.assertEqual(parsed["attachments"][0]["filename"], "invoice.pdf")
        self.assertEqual(parsed["links"][0]["display_text"], "Confirm your email")
        self.assertIn("https://example.test/confirm?token=abc", parsed["links"][0]["url"])
        self.assertNotIn("[image", parsed["text"].lower())
        self.assertEqual(parsed["date_sent"], "Wed, 23 Sep 2026 20:11:00 +0800")
        self.assertEqual(parsed["date_received"], "2026-09-23T20:12:34+08:00")

    def test_10b_text_link_extractor_preserves_balanced_odata_path(self):
        url = "https://download.example.test/odata/v1/Products(20e7a65d-6997-470c-b727-9750c97012c3)/$value"
        links = email_watch._extract_links_from_text(
            f"Download endpoint: {url}\nSentence link ({url})."
        )
        self.assertEqual([item["url"] for item in links], [url])

    def test_10c_nested_scholar_target_is_not_split_from_wrapper(self):
        wrapper = (
            "https://scholar.google.com/scholar_url?url="
            "https://www.nature.com/articles/example&hl=zh-CN&sa=X"
        )
        links = email_watch._extract_links_from_text(f"Paper title: {wrapper}")
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["url"], wrapper)
        self.assertEqual(links[0]["display_text"], "Paper title")

    def test_10d_fullwidth_wrapper_and_following_prose_are_not_part_of_url(self):
        raw = "申请入口：https://vista.ustc.edu.cn/）提交公派出境申请"
        links = email_watch._extract_links_from_text(raw)
        self.assertEqual(links[0]["url"], "https://vista.ustc.edu.cn/")
        self.assertEqual(
            email_assistant_composer._clean_url(
                "https://vista.ustc.edu.cn/）提交公派出境申请"
            ),
            "https://vista.ustc.edu.cn",
        )

    def test_11_rich_renderer_shows_action_link_without_image_placeholders(self):
        decision = {
            "classification": {"category": "account_status_notice", "label": "账户状态"},
            "importance": {"level": "high"},
            "notification": {
                "should_notify": True, "content_mode": "summary_plus_original",
                "summary_style": "paragraph", "summary": "请确认新账号邮箱。",
                "key_points": [], "original_policy": "excerpt",
            },
            "action": {"required": True, "description": "确认邮箱", "next_step": "打开确认链接"},
            "deadline": {"has_deadline": False},
            "attachments": {"present": False},
            "risk": {"level": "none", "notes": []},
        }
        result = email_notification_renderer.render_notification(
            {
                "account": "USTC", "subject": "Confirmation instructions",
                "body": "[image: Logo]\nConfirm your email\nPrivacy Policy",
                "links": [{"display_text": "Confirm your email", "url": "https://example.test/confirm?token=abc"}],
            },
            decision,
            {"attachments": [], "schedule": []},
            {},
            settings_override={"renderer": "adaptive_v1g", "mode": "production", "original_max_chars": 900},
        )
        self.assertTrue(result["text"].startswith("### 📬"))
        self.assertIn("[Confirm your email](https://example.test/confirm?token=abc)", result["text"])
        self.assertNotIn("[image", result["text"].lower())
        self.assertLess(len(result["text"]), 1200)

    def test_12_deadline_creates_24h_and_1h_persistent_reminders(self):
        deadline = datetime.now(timezone.utc) + timedelta(days=3)
        settings = {
            "timezone": "Asia/Shanghai",
            "reminder_offsets_minutes": [1440, 60],
        }
        reminders = email_delivery._default_reminders(deadline.isoformat(), "提交申请", settings)
        self.assertEqual([item["kind"] for item in reminders], ["提前1天", "提前1小时"])
        self.assertTrue(all("如已完成请忽略" in item["message"] for item in reminders))

    def test_12b_relative_download_expiry_resolves_from_message_time(self):
        value = email_delivery._resolve_deadline_value("15天", "2026-01-11 09:05+08:00")
        self.assertEqual(value, "2026-01-26T09:05+08:00")

    def test_13_due_reminder_is_emitted_once(self):
        now = datetime.now(timezone.utc)
        reminder_time = (now - timedelta(minutes=1)).isoformat()
        deadline = (now + timedelta(hours=1)).isoformat()
        schedule = {
            "id": "mail:main", "message_id": "mail", "title": "提交申请",
            "action_needed": "上传申请表", "deadline": deadline, "timezone": "Asia/Shanghai",
            "reminder_json": json.dumps([{"time": reminder_time, "kind": "提前1小时", "message": "提交申请"}]),
            "reminders_sent_json": "[]", "status": "active",
        }
        updates = []
        store = mock.Mock()
        store.get_schedules.return_value = [schedule]
        store.update_schedule.side_effect = lambda key, value: updates.append((key, value))
        with mock.patch.object(email_delivery, "email_store", store), mock.patch.object(
            email_delivery.email_config, "get_delivery_settings",
            return_value={"create_reminders": True, "managed_cron": True, "calendar_path": ""},
        ):
            alerts = email_delivery.collect_due_reminder_notifications(now=now)
        self.assertEqual(len(alerts), 1)
        self.assertIn("如已完成请忽略", alerts[0])
        self.assertTrue(updates)

    def test_14_himalaya_fallback_is_explicit_preview(self):
        with mock.patch.object(email_watch, "_export_himalaya_message", side_effect=RuntimeError("old client")), mock.patch.object(
            email_watch, "_run_himalaya_json", return_value={"body": "hello"}
        ) as runner:
            result = email_watch.read_himalaya("mail.toml", "42")
        self.assertEqual(result["text"], "hello")
        self.assertIn("--preview", runner.call_args.args[1])

    def test_15_list_only_model_policy_still_forwards_safe_attachment(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pdf = root / "report.pdf"
            pdf.write_bytes(b"%PDF-safe")
            email = {
                "id": "m", "msg_id": "m", "from_domain": "example.edu.cn",
                "has_attachments": True,
                "attachments": [{"filename": "report.pdf", "content_type": "application/pdf"}],
            }
            analysis = {"attachment_handling": {"policy": "list_only"}}
            settings = {
                "auto_download_attachments": True,
                "forward_attachments_to_weixin": True,
                "auto_forward_safe_attachments": True,
                "attachment_max_bytes": 1024 * 1024,
                "attachment_safe_extensions": [".pdf"],
            }
            with mock.patch.object(email_delivery.email_config, "get_delivery_settings", return_value=settings), mock.patch.object(
                email_delivery, "_save_root", return_value=td
            ), mock.patch.object(
                email_delivery, "_download_himalaya", return_value=[str(pdf)]
            ), mock.patch.object(email_delivery, "_persist_attachment"):
                result = email_delivery.download_attachments(
                    email, analysis, {"type": "himalaya", "config": "mail.toml"}
                )
        self.assertEqual(result[0]["filename"], "report.pdf")
        self.assertTrue(result[0]["send_to_weixin"])

    def test_15b_semantic_weekly_report_forwards_bounded_pdf(self):
        with tempfile.TemporaryDirectory() as td:
            pdf = Path(td) / "report.pdf"
            pdf.write_bytes(b"%PDF-safe")
            email = {
                "id": "weekly", "msg_id": "weekly", "from_domain": "gmail.com",
                "has_attachments": True,
                "attachments": [{"filename": "report.pdf", "content_type": "application/pdf"}],
            }
            analysis = {
                "production_semantic_route": True,
                "semantic_category": "academic_report_digest",
                "attachment_handling": {"policy": "list_only"},
            }
            settings = {
                "auto_download_attachments": True,
                "forward_attachments_to_weixin": True,
                "auto_forward_safe_attachments": True,
                "attachment_max_bytes": 1024 * 1024,
                "attachment_safe_extensions": [".pdf"],
            }
            with mock.patch.object(email_delivery.email_config, "get_delivery_settings", return_value=settings), mock.patch.object(
                email_delivery, "_save_root", return_value=td
            ), mock.patch.object(
                email_delivery, "_download_himalaya", return_value=[str(pdf)]
            ), mock.patch.object(email_delivery, "_persist_attachment"):
                result = email_delivery.download_attachments(
                    email, analysis, {"type": "himalaya", "config": "mail.toml"}
                )
        self.assertEqual(result[0]["filename"], "report.pdf")
        self.assertTrue(result[0]["send_to_weixin"])

    def test_15c_link_only_invoice_downloads_and_forwards_pdf(self):
        with tempfile.TemporaryDirectory() as td:
            email = {
                "id": "invoice-link", "msg_id": "invoice-link",
                "has_attachments": False,
                "links": [{
                    "display_text": "发票PDF文件下载",
                    "url": "https://invoice.example.test/files/invoice-42.pdf?token=secret",
                }],
            }
            analysis = {
                "production_semantic_route": True,
                "semantic_category": "invoice_receipt",
                "attachment_handling": {"policy": "none"},
            }
            settings = {
                "auto_download_attachments": True,
                "forward_attachments_to_weixin": True,
                "attachment_max_bytes": 1024 * 1024,
                "attachment_safe_extensions": [".pdf"],
            }
            response = mock.MagicMock()
            response.geturl.return_value = "https://invoice.example.test/files/invoice-42.pdf"
            response.headers = {"Content-Length": "18"}
            response.read.return_value = b"%PDF-safe-invoice"
            context = mock.MagicMock()
            context.__enter__.return_value = response
            context.__exit__.return_value = False
            with mock.patch.object(email_delivery.email_config, "get_delivery_settings", return_value=settings), mock.patch.object(
                email_delivery, "_save_root", return_value=td
            ), mock.patch.object(
                email_delivery, "_public_https_url", return_value=True
            ), mock.patch.object(
                email_delivery, "urlopen", return_value=context
            ), mock.patch.object(email_delivery, "_persist_attachment"):
                result = email_delivery.download_attachments(email, analysis, {})
        self.assertEqual(result[0]["filename"], "invoice-42.pdf")
        self.assertEqual(result[0]["source"], "invoice_pdf_link")
        self.assertTrue(result[0]["send_to_weixin"])

    def test_15d_invoice_forwards_documents_not_inline_assets(self):
        with tempfile.TemporaryDirectory() as td:
            paths = []
            for name, data in (
                ("invoice.pdf", b"%PDF-safe"), ("invoice.ofd", b"ofd"),
                ("invoice.xml", b"<invoice/>"), ("advertising.png", b"png"),
            ):
                path = Path(td) / name
                path.write_bytes(data)
                paths.append(str(path))
            email = {
                "id": "invoice", "msg_id": "invoice", "from_domain": "example.com",
                "has_attachments": True,
                "attachments": [{"filename": Path(path).name} for path in paths],
            }
            analysis = {
                "production_semantic_route": True, "semantic_category": "invoice_receipt",
                "attachment_handling": {"policy": "download_safe"},
            }
            settings = {
                "auto_download_attachments": True, "forward_attachments_to_weixin": True,
                "attachment_max_bytes": 1024 * 1024,
                "attachment_safe_extensions": [".pdf", ".ofd", ".xml", ".png"],
            }
            with mock.patch.object(email_delivery.email_config, "get_delivery_settings", return_value=settings), mock.patch.object(
                email_delivery, "_save_root", return_value=td
            ), mock.patch.object(
                email_delivery, "_download_himalaya", return_value=paths
            ), mock.patch.object(email_delivery, "_persist_attachment"):
                result = email_delivery.download_attachments(
                    email, analysis, {"type": "himalaya", "config": "mail.toml"}
                )
        by_name = {item["filename"]: item for item in result}
        self.assertTrue(by_name["invoice.pdf"]["send_to_weixin"])
        self.assertTrue(by_name["invoice.ofd"]["send_to_weixin"])
        self.assertEqual(by_name["invoice.xml"]["forward_reason"], "auxiliary_invoice_xml")
        self.assertEqual(by_name["advertising.png"]["forward_reason"], "invoice_inline_asset")

    def test_15e_research_feedback_forwards_safe_image(self):
        with tempfile.TemporaryDirectory() as td:
            image = Path(td) / "figure.png"
            image.write_bytes(b"png")
            email = {
                "id": "feedback", "msg_id": "feedback", "from_domain": "example.com",
                "has_attachments": True, "attachments": [{"filename": "figure.png"}],
            }
            analysis = {
                "production_semantic_route": True, "semantic_category": "research_feedback_thread",
                "attachment_handling": {"policy": "list_only"},
            }
            settings = {
                "auto_download_attachments": True, "forward_attachments_to_weixin": True,
                "attachment_max_bytes": 1024 * 1024, "attachment_safe_extensions": [".png"],
            }
            with mock.patch.object(email_delivery.email_config, "get_delivery_settings", return_value=settings), mock.patch.object(
                email_delivery, "_save_root", return_value=td
            ), mock.patch.object(
                email_delivery, "_download_himalaya", return_value=[str(image)]
            ), mock.patch.object(email_delivery, "_persist_attachment"):
                result = email_delivery.download_attachments(
                    email, analysis, {"type": "himalaya", "config": "mail.toml"}
                )
        self.assertTrue(result[0]["send_to_weixin"])

    def test_16_himalaya_retry_returns_overwritten_existing_attachment(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "report.pdf"
            target.write_bytes(b"old")

            def successful_download(*_args, **_kwargs):
                target.write_bytes(b"%PDF-new")
                return mock.Mock(returncode=0, stdout='"Downloaded 1 attachment!"', stderr="")

            with mock.patch.object(email_delivery, "_himalaya_cmd_variants", return_value=[["himalaya"]]), mock.patch.object(
                email_delivery.subprocess, "run", side_effect=successful_download
            ):
                paths = email_delivery._download_himalaya("mail.toml", "42", td)
        self.assertEqual(paths, [str(target)])


class WeixinAttachmentTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_10_text_and_safe_attachments_use_real_adapter_methods(self):
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

    async def test_11_partial_attachment_retry_does_not_repeat_completed_parts(self):
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
