#!/usr/bin/env python3
"""Regression: a skipped editorial review must not cost a code card its code.

0.16.0 skipped the second model pass for verification codes but let the skip
fall into the evidence-only fallback renderer, which preserves links and drops
the credential. The card then said "验证码" with no code in it.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

_TMP = Path(tempfile.mkdtemp(prefix="qoder-codecard-"))
os.environ["HERMES_EMAIL_WATCHDOG_STATE_ROOT"] = str(_TMP)
os.environ.pop("EMAIL_WATCHDOG_CONFIG", None)

import email_assistant_composer as composer  # noqa: E402


class CodeCardRenderTests(unittest.TestCase):
    def _email(self):
        return {
            "account": "Gmail",
            "subject": "[npm] OTP for sign up: 552178",
            "from_addr": "noreply@npmjs.com",
            "from_name": "npm",
            "date_sent": "2026-11-07 17:20 +0800",
            "body": "Your one-time password is 552178. It expires in 5 minutes.",
            "links": [],
            "attachments": [],
        }

    def _decision(self):
        return {
            "classification": {"category": "verification_code", "label": "验证码"},
            "importance": {"level": "high"},
            "notification": {
                "content_mode": "code_card",
                "summary": "邮箱验证码 552178，5 分钟内有效。",
                "key_points": [],
                "should_notify": True,
            },
            "action": {"required": False, "description": ""},
            "deadline": {"has_deadline": False},
            "risk": {"level": "none", "notes": []},
        }

    def test_composer_keeps_code_in_card(self):
        out = composer.render_notification(
            self._email(), self._decision(), {"attachments": [], "schedule": []},
            {"label": "Gmail"},
        )
        text = str(out.get("text") or "")
        self.assertIn("552178", text)
        self.assertIn("验证码", text)

    def test_composer_card_is_not_the_evidence_fallback(self):
        out = composer.render_notification(
            self._email(), self._decision(), {"attachments": [], "schedule": []},
            {"label": "Gmail"},
        )
        text = str(out.get("text") or "")
        self.assertNotIn("编辑审校暂不可用", text)


if __name__ == "__main__":
    unittest.main()