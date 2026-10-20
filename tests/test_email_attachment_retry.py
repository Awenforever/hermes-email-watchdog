#!/usr/bin/env python3
"""Regression: attachment download must have a BOUNDED retry fallback.

Himalaya does a fresh IMAP login per invocation, so a single poll can hit a
transient timeout / provider hiccup (seen live as Gmail rc=124). The old path
made exactly one attempt, so one blip permanently marked the attachment
``download_failed`` and it was never re-fetched. ``_download_himalaya_with_retry``
retries a bounded number of times with a short linear backoff, stops as soon as
any file lands, and bails immediately (no retries) when the himalaya binary is
missing — that is not a transient condition.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="qoder-email-attretry-"))
os.environ["HERMES_EMAIL_WATCHDOG_STATE_ROOT"] = str(_TMP)
os.environ.pop("EMAIL_WATCHDOG_CONFIG", None)

import email_delivery  # noqa: E402
from unittest.mock import patch  # noqa: E402

_CFG = "/cfg/config_ustc.toml"
_UID = "987654321"
_SAVE = str(_TMP / "att")


class RetryFallbackTests(unittest.TestCase):
    def test_success_first_try_does_not_retry_or_sleep(self):
        with patch("time.sleep") as slp, \
             patch.object(email_delivery, "_download_himalaya", return_value=["/a.pdf"]) as dl:
            out = email_delivery._download_himalaya_with_retry(_CFG, _UID, _SAVE)
        self.assertEqual(["/a.pdf"], out)
        self.assertEqual(1, dl.call_count)
        slp.assert_not_called()

    def test_transient_empty_then_success_recovers(self):
        with patch("time.sleep") as slp, \
             patch.object(email_delivery, "_download_himalaya", side_effect=[[], ["/a.pdf"]]) as dl:
            out = email_delivery._download_himalaya_with_retry(_CFG, _UID, _SAVE)
        self.assertEqual(["/a.pdf"], out)
        self.assertEqual(2, dl.call_count)
        self.assertEqual(1, slp.call_count)

    def test_exhausts_bounded_attempts_then_returns_empty(self):
        n = email_delivery._ATTACHMENT_DOWNLOAD_ATTEMPTS
        with patch("time.sleep") as slp, \
             patch.object(email_delivery, "_download_himalaya", return_value=[]) as dl:
            out = email_delivery._download_himalaya_with_retry(_CFG, _UID, _SAVE)
        self.assertEqual([], out)
        self.assertEqual(n, dl.call_count)
        # backoff sleeps happen BETWEEN attempts only
        self.assertEqual(n - 1, slp.call_count)

    def test_missing_binary_bails_without_retry_or_sleep(self):
        with patch("time.sleep") as slp, \
             patch.object(email_delivery, "_download_himalaya", side_effect=FileNotFoundError) as dl:
            out = email_delivery._download_himalaya_with_retry(_CFG, _UID, _SAVE)
        self.assertEqual([], out)
        self.assertEqual(1, dl.call_count)
        slp.assert_not_called()

    def test_backoff_is_linear_in_attempt_number(self):
        base = email_delivery._ATTACHMENT_DOWNLOAD_BACKOFF_SECONDS
        with patch("time.sleep") as slp, \
             patch.object(email_delivery, "_download_himalaya", side_effect=[[], [], ["/a.pdf"]]):
            email_delivery._download_himalaya_with_retry(_CFG, _UID, _SAVE)
        self.assertEqual([base * 1, base * 2], [c.args[0] for c in slp.call_args_list])

    def test_attempts_are_bounded_and_small(self):
        # Guard against an accidental unbounded/large retry loop.
        self.assertLessEqual(email_delivery._ATTACHMENT_DOWNLOAD_ATTEMPTS, 5)
        self.assertGreaterEqual(email_delivery._ATTACHMENT_DOWNLOAD_ATTEMPTS, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
