from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

# Isolate state BEFORE importing email_store, which email_watch/email_delivery
# import transitively and which calls init_db() at import time. Without this the
# in-container suite would open the PRODUCTION DB under /opt/data/plugin-data.
_TMP = Path(tempfile.mkdtemp(prefix="qoder-email-himalaya-"))
os.environ["HERMES_EMAIL_WATCHDOG_STATE_ROOT"] = str(_TMP)
os.environ.pop("EMAIL_WATCHDOG_CONFIG", None)

import email_delivery  # noqa: E402
import email_watch  # noqa: E402


class CmdVariantConfigScopingTests(unittest.TestCase):
    """Part A — a known config path must NEVER yield a bare (no ``-c``) call.

    A bare himalaya invocation with no default ``config.toml`` present drops into
    the interactive config wizard, which on a TTY-less daemon fails as
    "cannot prompt boolean / The input device is not a TTY" and masks the real
    per-account error. It could also select a wrong default account.
    """

    ARGS = ["envelope", "list", "--page-size", "5", "--output", "json"]

    def _assert_all_carry_config(self, variants):
        self.assertTrue(variants)
        for cmd in variants:
            self.assertTrue(
                "-c" in cmd or "--config" in cmd,
                f"bare (wizard-triggering) variant leaked: {cmd}",
            )

    def test_watch_variants_all_carry_config_when_path_known(self):
        self._assert_all_carry_config(
            email_watch._himalaya_cmd_variants("/cfg/config_ustc.toml", self.ARGS)
        )

    def test_watch_account_qualified_variants_also_carry_config(self):
        variants = email_watch._himalaya_cmd_variants(
            "/cfg/config_ustc.toml", self.ARGS, account="ustc"
        )
        self.assertTrue(any("-a" in cmd and "ustc" in cmd for cmd in variants))
        self._assert_all_carry_config(variants)

    def test_watch_empty_config_still_allows_bare_invocation(self):
        variants = email_watch._himalaya_cmd_variants("", ["folder", "list"])
        self.assertTrue(variants)
        self.assertTrue(any("-c" not in cmd and "--config" not in cmd for cmd in variants))

    def test_delivery_variants_all_carry_config_when_path_known(self):
        args = ["message", "export", "42", "--full", "--destination", "/tmp/x.eml"]
        self._assert_all_carry_config(
            email_delivery._himalaya_cmd_variants("/cfg/config_ustc.toml", args)
        )

    def test_delivery_empty_config_still_allows_bare_invocation(self):
        variants = email_delivery._himalaya_cmd_variants("", ["folder", "list"])
        self.assertTrue(any("-c" not in cmd and "--config" not in cmd for cmd in variants))


class ReadPathStdinTests(unittest.TestCase):
    """Part B — read-path himalaya calls must pass ``stdin=DEVNULL`` so an
    interactive prompt can never hang or confusingly fail on the daemon's
    inherited stdin. The SEND path is deliberately excluded: it pipes the MML
    message via ``stdin=input`` (see email_actions), so DEVNULL there would break
    sending."""

    @patch("email_watch.subprocess.run")
    def test_run_himalaya_json_uses_devnull_stdin(self, mock_run):
        mock_run.return_value = SimpleNamespace(returncode=0, stdout='[{"id":"1"}]', stderr="")
        out = email_watch._run_himalaya_json(
            "/cfg/config_ustc.toml", ["envelope", "list", "--output", "json"]
        )
        self.assertEqual([{"id": "1"}], out)
        self.assertTrue(mock_run.called)
        self.assertEqual(subprocess.DEVNULL, mock_run.call_args.kwargs.get("stdin"))

    @patch("email_watch.subprocess.run")
    def test_export_message_uses_devnull_stdin_on_every_attempt(self, mock_run):
        mock_run.return_value = SimpleNamespace(returncode=1, stdout="", stderr="imap down")
        with self.assertRaises(email_watch.HimalayaCommandError):
            email_watch._export_himalaya_message("/cfg/config_ustc.toml", "42")
        self.assertTrue(mock_run.called)
        for call in mock_run.call_args_list:
            self.assertEqual(subprocess.DEVNULL, call.kwargs.get("stdin"))

    @patch("email_delivery.subprocess.run")
    def test_delivery_raw_export_uses_devnull_stdin(self, mock_run):
        mock_run.return_value = SimpleNamespace(returncode=1, stdout="", stderr="imap down")
        result = email_delivery._download_himalaya_from_raw_message(
            "/cfg/config_ustc.toml", "42", "/tmp/hermes-nonexistent-save-dir"
        )
        self.assertIsNone(result)  # all variants failed -> export unsupported
        self.assertTrue(mock_run.called)
        for call in mock_run.call_args_list:
            self.assertEqual(subprocess.DEVNULL, call.kwargs.get("stdin"))


class ErrorReportingTests(unittest.TestCase):
    """Part C — the surfaced error must be the PRIMARY (real config) attempt, not
    a trailing fallback whose wizard/TTY noise masks the true cause."""

    def test_format_reports_first_informative_attempt_not_last(self):
        attempts = [
            {"rc": 1, "stderr": "connection timed out"},   # primary = real cause
            {"rc": 1, "stderr": "cannot prompt boolean"},  # masked fallback noise
        ]
        msg = str(email_watch.HimalayaCommandError(["envelope", "list"], attempts))
        self.assertIn("connection timed out", msg)
        self.assertNotIn("cannot prompt boolean", msg)
        self.assertIn("2 variant(s) tried", msg)

    def test_format_skips_empty_primary_to_first_with_detail(self):
        attempts = [
            {"rc": 124, "error": ""},                 # no detail
            {"rc": 1, "stderr": "auth failed"},       # first informative
        ]
        msg = str(email_watch.HimalayaCommandError(["envelope", "list"], attempts))
        self.assertIn("auth failed", msg)

    def test_format_single_attempt_has_no_variant_suffix(self):
        msg = str(email_watch.HimalayaCommandError(["envelope", "list"], [{"rc": 1, "stderr": "boom"}]))
        self.assertIn("boom", msg)
        self.assertNotIn("variant(s) tried", msg)

    def test_format_no_attempts(self):
        self.assertIn("no attempts", str(email_watch.HimalayaCommandError(["envelope", "list"], [])))


class CheckFailureRegressionTests(unittest.TestCase):
    """End-to-end regression for the reported "### ⚠️ USTC 检查失败 / cannot prompt
    boolean / not a TTY" alert: when the real check fails, the plugin must (1)
    never attempt the bare wizard-triggering variant and (2) surface the real
    error, not a wizard prompt error."""

    @patch("email_watch.subprocess.run")
    def test_failed_check_surfaces_real_error_and_never_runs_bare_variant(self, mock_run):
        mock_run.return_value = SimpleNamespace(returncode=1, stdout="", stderr="network unreachable")
        with self.assertRaises(email_watch.HimalayaCommandError) as ctx:
            email_watch._run_himalaya_json(
                "/cfg/config_ustc.toml",
                ["envelope", "list", "--page-size", "5", "--output", "json"],
                account="ustc",
            )
        self.assertTrue(mock_run.called)
        for call in mock_run.call_args_list:
            cmd = call.args[0]
            self.assertTrue(
                "-c" in cmd or "--config" in cmd,
                f"bare wizard variant attempted: {cmd}",
            )
            self.assertEqual(subprocess.DEVNULL, call.kwargs.get("stdin"))
        message = str(ctx.exception)
        self.assertIn("network unreachable", message)
        self.assertNotIn("cannot prompt boolean", message)
        self.assertNotIn("not a TTY", message)


if __name__ == "__main__":
    unittest.main()
