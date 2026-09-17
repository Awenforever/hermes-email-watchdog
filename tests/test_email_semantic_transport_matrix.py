#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import email_semantic_engine as engine


class _FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc, tb):
        return False
    def read(self):
        return self.payload


class SemanticTransportRecoveryMatrix(unittest.TestCase):
    def test_direct_object(self):
        value, strategy = engine._extract_json_object_detailed('{"category":"x"}')
        self.assertEqual(value["category"], "x")
        self.assertEqual(strategy, "json")

    def test_fenced_object(self):
        value, strategy = engine._extract_json_object_detailed(
            '```json\n{"category":"x"}\n```'
        )
        self.assertEqual(value["category"], "x")
        self.assertIn("extracted", strategy)

    def test_prose_wrapped_balanced_object(self):
        value, _ = engine._extract_json_object_detailed(
            'Here is the result:\n{"category":"x","summary":"ok"}\nDone.'
        )
        self.assertEqual(value["summary"], "ok")

    def test_json_string_wrapped_object(self):
        value, _ = engine._extract_json_object_detailed(
            json.dumps('{"category":"x"}')
        )
        self.assertEqual(value["category"], "x")

    def test_singleton_list_wrapped_object(self):
        value, _ = engine._extract_json_object_detailed(
            '[{"category":"x"}]'
        )
        self.assertEqual(value["category"], "x")

    def test_generic_wrapper_object(self):
        value, _ = engine._extract_json_object_detailed(
            '{"result":{"category":"x"}}'
        )
        self.assertEqual(value["category"], "x")

    def test_python_literal_object(self):
        value, strategy = engine._extract_json_object_detailed(
            "{'category': 'x', 'should_notify': True}"
        )
        self.assertTrue(value["should_notify"])
        self.assertIn("python_literal", strategy)

    def test_trailing_comma_repair(self):
        value, _ = engine._extract_json_object_detailed(
            '{"category":"x","key_points":["a",],}'
        )
        self.assertEqual(value["key_points"], ["a"])

    def test_reject_plain_prose(self):
        with self.assertRaises(engine.SemanticEngineError):
            engine._extract_json_object_detailed("This is only prose.")

    def test_reject_multi_item_list(self):
        with self.assertRaises(engine.SemanticEngineError):
            engine._extract_json_object_detailed(
                '[{"category":"a"},{"category":"b"}]'
            )

    def test_bounded_retry_repairs_output_without_reanalyzing_email(self):
        calls = []
        original = engine.urllib.request.urlopen

        def fake_urlopen(request, timeout):
            payload = json.loads(request.data.decode("utf-8"))
            calls.append({"payload": payload, "timeout": timeout})
            if len(calls) == 1:
                response = {
                    "response": "category=academic_report_digest; should_notify=true",
                    "done_reason": "stop",
                    "eval_count": 5,
                    "eval_duration": 1_000_000_000,
                }
            else:
                response = {
                    "response": '{"category":"academic_report_digest","should_notify":true}',
                    "done_reason": "stop",
                    "eval_count": 8,
                    "eval_duration": 1_000_000_000,
                }
            return _FakeResponse(response)

        engine.urllib.request.urlopen = fake_urlopen
        try:
            result = engine.call_ollama(
                "ORIGINAL EMAIL PROMPT WITH PRIVATE BODY",
                {
                    "provider": "ollama",
                    "endpoint": "http://127.0.0.1:11434",
                    "model": "qwen2.5:3b",
                    "timeout_seconds": 300,
                    "temperature": 0.1,
                    "num_predict": 1400,
                    "num_thread": 5,
                },
            )
        finally:
            engine.urllib.request.urlopen = original

        self.assertEqual(result["parsed"]["category"], "academic_report_digest")
        self.assertEqual(result["metrics"]["retry_count"], 1)
        self.assertEqual(result["metrics"]["retry_kind"], "json_repair_only")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["timeout"], 300)
        self.assertEqual(calls[1]["timeout"], 180)
        self.assertEqual(calls[1]["payload"]["options"]["temperature"], 0.0)
        self.assertLessEqual(calls[1]["payload"]["options"]["num_predict"], 420)
        self.assertEqual(calls[1]["payload"]["format"], "json")
        self.assertIsInstance(calls[0]["payload"]["format"], dict)
        self.assertTrue(calls[1]["payload"]["prompt"].startswith("JSON-REPAIR:"))
        self.assertIn("category=academic_report_digest", calls[1]["payload"]["prompt"])
        self.assertNotIn("ORIGINAL EMAIL PROMPT", calls[1]["payload"]["prompt"])

    def test_truncated_primary_skips_json_repair(self):
        calls = []
        original = engine.urllib.request.urlopen

        def fake_urlopen(request, timeout):
            payload = json.loads(request.data.decode("utf-8"))
            calls.append({"payload": payload, "timeout": timeout})
            return _FakeResponse({
                "response": '{"category":"academic_report_digest","summary":"unfinished',
                "done_reason": "length",
                "eval_count": 1400,
                "eval_duration": 1_000_000_000,
            })

        engine.urllib.request.urlopen = fake_urlopen
        try:
            with self.assertRaises(engine.SemanticEngineTruncated) as ctx:
                engine.call_ollama(
                    "test prompt",
                    {
                        "provider": "ollama",
                        "endpoint": "http://127.0.0.1:11434",
                        "model": "qwen2.5:3b",
                        "timeout_seconds": 300,
                        "temperature": 0.1,
                        "num_predict": 1400,
                        "num_thread": 5,
                    },
                )
        finally:
            engine.urllib.request.urlopen = original

        self.assertEqual(len(calls), 1)
        self.assertEqual(ctx.exception.metrics["done_reason"], "length")
        self.assertEqual(
            ctx.exception.metrics["retry_kind"],
            "skipped_for_truncated_primary",
        )

    def test_ollama_schema_has_compact_output_bounds(self):
        schema = engine.email_semantic_core.ollama_format_schema()
        props = schema["properties"]
        self.assertEqual(props["summary"]["maxLength"], 360)
        self.assertEqual(props["key_points"]["maxItems"], 4)
        self.assertEqual(props["key_points"]["items"]["maxLength"], 180)
        self.assertEqual(props["risk"]["properties"]["notes"]["maxItems"], 3)
        self.assertEqual(props["uncertainties"]["maxItems"], 3)

    def test_json_repair_prompt_is_bounded(self):
        prompt = engine._json_repair_prompt("x" * 20000)
        self.assertLess(len(prompt), 13000)
        self.assertIn("<TRUNCATED_FOR_FORMAT_REPAIR>", prompt)

    def test_valid_primary_keeps_full_semantics_and_skips_repair(self):
        calls = []
        original = engine.urllib.request.urlopen

        def fake_urlopen(request, timeout):
            payload = json.loads(request.data.decode("utf-8"))
            calls.append({"payload": payload, "timeout": timeout})
            return _FakeResponse({
                "response": '{"category":"academic_report_digest"}',
                "done_reason": "stop",
                "eval_count": 8,
                "eval_duration": 1_000_000_000,
            })

        engine.urllib.request.urlopen = fake_urlopen
        try:
            result = engine.call_ollama(
                "test prompt",
                {
                    "provider": "ollama",
                    "endpoint": "http://127.0.0.1:11434",
                    "model": "qwen2.5:3b",
                    "timeout_seconds": 300,
                    "temperature": 0.1,
                    "num_predict": 1400,
                    "num_thread": 5,
                },
            )
        finally:
            engine.urllib.request.urlopen = original

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["timeout"], 300)
        self.assertEqual(result["metrics"]["retry_count"], 0)
        self.assertEqual(result["metrics"]["json_repair_timeout_budget_seconds"], 180)
        self.assertEqual(result["metrics"]["max_end_to_end_budget_seconds"], 480)

    def _adaptive_settings(self, **overrides):
        value = {
            "num_predict_mode": "adaptive",
            "num_predict": 1800,
            "num_predict_simple": 600,
            "num_predict_standard": 1000,
            "num_predict_complex": 1600,
            "num_predict_hard_cap": 1800,
        }
        value.update(overrides)
        return engine._settings(value)

    def _budget_for(self, subject, body, *, attachments=None, settings=None):
        email = {
            "id": "budget-test", "msg_id": "budget-test", "account": "audit",
            "subject": subject, "body": body,
            "from_addr": "sender@example.invalid", "from_name": "Sender",
            "attachments": attachments or [],
            "has_attachment": bool(attachments), "has_attachments": bool(attachments),
        }
        features = engine.email_feature_extractor.extract_features(email)
        facts = engine._facts(email, features)
        return engine._select_output_budget(
            email, features, facts, settings or self._adaptive_settings()
        )

    def test_adaptive_budget_grounded_code_is_simple(self):
        profile = self._budget_for(
            "账户登录验证码", "你的登录验证码是 482731，有效期10分钟。"
        )
        self.assertEqual(profile["tier"], "simple")
        self.assertEqual(profile["num_predict"], 600)
        self.assertIn("grounded_verification_code", profile["reasons"])

    def test_adaptive_budget_short_system_notice_is_simple(self):
        profile = self._budget_for(
            "Watchdog E2E 状态检查", "测试通过，无需操作。"
        )
        self.assertEqual(profile["tier"], "simple")
        self.assertEqual(profile["num_predict"], 600)

    def test_adaptive_budget_ordinary_mail_is_standard(self):
        profile = self._budget_for(
            "项目近况", "本周项目进展正常，后续安排将在下一次会议中讨论。"
        )
        self.assertEqual(profile["tier"], "standard")
        self.assertEqual(profile["num_predict"], 1000)

    def test_adaptive_budget_academic_digest_is_complex(self):
        profile = self._budget_for(
            "学术研究周报｜烟雾遥感进展",
            "本期汇总论文、实验结果和跨区域泛化评估，无需回复。",
        )
        self.assertEqual(profile["tier"], "complex")
        self.assertEqual(profile["num_predict"], 1600)
        self.assertIn("academic_report", profile["reasons"])

    def test_adaptive_budget_action_deadline_is_complex(self):
        profile = self._budget_for(
            "学院材料提交通知",
            "请于2026年7月20日前填写表格、上传材料并确认提交结果。",
        )
        self.assertEqual(profile["tier"], "complex")
        self.assertEqual(profile["num_predict"], 1600)
        self.assertIn("action_and_deadline", profile["reasons"])

    def test_adaptive_budget_respects_hard_cap(self):
        profile = self._budget_for(
            "学术研究周报｜复杂报告", "本期汇总多项研究进展。",
            settings=self._adaptive_settings(num_predict=1200, num_predict_hard_cap=1200),
        )
        self.assertEqual(profile["tier"], "complex")
        self.assertEqual(profile["num_predict"], 1200)
        self.assertEqual(profile["hard_cap"], 1200)

if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(
        SemanticTransportRecoveryMatrix
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(
        "EMAIL_WATCHDOG_SEMANTIC_TRANSPORT_RECOVERY_MATRIX "
        f"passed={result.testsRun - len(result.failures) - len(result.errors)}/{result.testsRun}"
    )
    raise SystemExit(0 if result.wasSuccessful() else 1)
