#!/usr/bin/env python3
"""Regression: bounded concurrent semantic pre-warm (R16).

The two-phase delivery path pre-warms the semantic decision cache for several
emails in parallel, then delivers serially. These tests pin the two safety
properties: the worker count is clamped, and a pre-warm failure is inert
(the delivery pass then computes that email inline).
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

_TMP = Path(tempfile.mkdtemp(prefix="qoder-email-r16-"))
os.environ["HERMES_EMAIL_WATCHDOG_STATE_ROOT"] = str(_TMP)
os.environ.pop("EMAIL_WATCHDOG_CONFIG", None)

import email_delivery  # noqa: E402
import email_semantic_engine  # noqa: E402
import email_watch  # noqa: E402


class AnalysisWorkerTests(unittest.TestCase):
    def test_worker_count_is_clamped(self):
        cases = {"analysis_workers": 99}
        with mock.patch.object(email_watch.email_config, "get_watchdog_settings", return_value=cases):
            self.assertEqual(email_watch._analysis_workers(), 6)
        with mock.patch.object(email_watch.email_config, "get_watchdog_settings", return_value={"analysis_workers": 0}):
            self.assertEqual(email_watch._analysis_workers(), 1)
        with mock.patch.object(email_watch.email_config, "get_watchdog_settings", return_value={}):
            self.assertEqual(email_watch._analysis_workers(), 3)


class PrepareSemanticsTests(unittest.TestCase):
    def test_failure_returns_empty_dict(self):
        with mock.patch.object(email_semantic_engine, "analyze_email", side_effect=RuntimeError("boom")):
            self.assertEqual(email_delivery.prepare_delivery_semantics({}, {}, {}), {})

    def test_success_uses_shadow_cache_settings(self):
        seen = {}
        def fake(email, rule, analysis, settings_override=None):
            seen.update(settings_override or {})
            return {"ok": True}
        with mock.patch.object(email_semantic_engine, "analyze_email", side_effect=fake):
            out = email_delivery.prepare_delivery_semantics({"id": 1}, {}, {})
        self.assertEqual(out, {"ok": True})
        self.assertEqual(seen.get("mode"), "shadow")
        self.assertTrue(seen.get("cache_by_message_hash"))


if __name__ == "__main__":
    unittest.main()
