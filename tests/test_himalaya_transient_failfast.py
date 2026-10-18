from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

# Isolate state BEFORE importing email_store (imported transitively by
# email_watch), which calls init_db() at import time. Without this the suite
# would open the PRODUCTION DB under /opt/data/plugin-data.
_TMP = Path(tempfile.mkdtemp(prefix="qoder-email-failfast-"))
os.environ["HERMES_EMAIL_WATCHDOG_STATE_ROOT"] = str(_TMP)
os.environ.pop("EMAIL_WATCHDOG_CONFIG", None)

import email_watch  # noqa: E402


class _FakeProc:
    """Stand-in for subprocess.Popen driving the graceful-timeout path."""

    def __init__(self, outcomes):
        # each outcome: ("timeout",) or ("return", stdout, stderr, rc)
        self._outcomes = list(outcomes)
        self.terminate_calls = 0
        self.kill_calls = 0
        self.returncode = None

    def communicate(self, timeout=None):
        item = self._outcomes.pop(0)
        if item[0] == "timeout":
            raise subprocess.TimeoutExpired(cmd="himalaya", timeout=timeout or 0)
        _, out, err, rc = item
        self.returncode = rc
        return out, err

    def terminate(self):
        self.terminate_calls += 1

    def kill(self):
        self.kill_calls += 1


class ClassifyFailureTests(unittest.TestCase):
    def test_connection_cap_is_transient(self):
        blob = (
            'authentication failed mechanism=Plain err=ResolveTask(UnexpectedNoResponse('
            'StatusBody { kind: No, code: Some(Alert), text: Text("Too many simultaneous '
            'connections. (Failure)") })) Error: 0: cannot build IMAP client 1: cannot '
            'authenticate to IMAP server'
        )
        self.assertEqual("transient", email_watch._classify_himalaya_failure(1, "", blob))

    def test_connection_refused_is_transient(self):
        blob = (
            "Error: 0: cannot build IMAP client 1: cannot connect to IMAP server "
            "imap.gmail.com:993 using STARTTLS 2: cannot connect to TCP stream "
            "3: Connection refused (os error 111)"
        )
        self.assertEqual("transient", email_watch._classify_himalaya_failure(1, "", blob))

    def test_tls_eof_is_transient(self):
        blob = "cannot connect to TLS stream 3: tls handshake eof"
        self.assertEqual("transient", email_watch._classify_himalaya_failure(1, "", blob))

    def test_close_notify_stream_error_is_transient(self):
        blob = (
            "cannot authenticate to IMAP server using LOGIN mechanism 2: stream error "
            "3: peer closed connection without sending TLS close_notify"
        )
        self.assertEqual("transient", email_watch._classify_himalaya_failure(1, "", blob))

    def test_timeout_rc_124_is_transient(self):
        self.assertEqual("transient", email_watch._classify_himalaya_failure(124, "", ""))

    def test_wizard_tty_is_config(self):
        blob = "Error: 0: cannot prompt boolean 1: The input device is not a TTY"
        self.assertEqual("config", email_watch._classify_himalaya_failure(1, "", blob))

    def test_unknown_flag_is_config(self):
        blob = "error: unexpected argument '--bogus' found"
        self.assertEqual("config", email_watch._classify_himalaya_failure(2, "", blob))

    def test_unrecognised_is_unknown(self):
        self.assertEqual("unknown", email_watch._classify_himalaya_failure(1, "", "something odd"))

    def test_empty_is_unknown(self):
        self.assertEqual("unknown", email_watch._classify_himalaya_failure(1, "", ""))


class GracefulTimeoutTests(unittest.TestCase):
    @patch("email_watch.subprocess.Popen")
    def test_passes_devnull_stdin(self, mock_popen):
        proc = _FakeProc([("return", "[]", "", 0)])
        mock_popen.return_value = proc
        email_watch._run_himalaya_subprocess(["himalaya"], 1, None)
        self.assertEqual(subprocess.DEVNULL, mock_popen.call_args.kwargs.get("stdin"))

    @patch("email_watch.subprocess.Popen")
    def test_sigterm_then_clean_exit_no_kill(self, mock_popen):
        proc = _FakeProc([("timeout",), ("return", "", "", -15)])
        mock_popen.return_value = proc
        rc, _out, _err, timed_out = email_watch._run_himalaya_subprocess(["himalaya"], 1, None)
        self.assertTrue(timed_out)
        self.assertEqual(1, proc.terminate_calls)
        self.assertEqual(0, proc.kill_calls)

    @patch("email_watch.subprocess.Popen")
    def test_sigkill_after_grace_timeout(self, mock_popen):
        proc = _FakeProc([("timeout",), ("timeout",), ("return", "", "", None)])
        mock_popen.return_value = proc
        rc, _out, _err, timed_out = email_watch._run_himalaya_subprocess(["himalaya"], 1, None)
        self.assertTrue(timed_out)
        self.assertEqual(1, proc.terminate_calls)
        self.assertEqual(1, proc.kill_calls)
        self.assertEqual(124, rc)

    @patch("email_watch.subprocess.Popen")
    def test_normal_exit_not_timed_out(self, mock_popen):
        proc = _FakeProc([("return", '[{"id":"1"}]', "", 0)])
        mock_popen.return_value = proc
        rc, out, err, timed_out = email_watch._run_himalaya_subprocess(["himalaya"], 1, None)
        self.assertFalse(timed_out)
        self.assertEqual(0, rc)
        self.assertEqual('[{"id":"1"}]', out)
        self.assertEqual(0, proc.terminate_calls)


class FailFastVariantTests(unittest.TestCase):
    ARGS = ["envelope", "list", "--page-size", "5", "--output", "json"]

    @patch("email_watch._run_himalaya_subprocess")
    def test_transient_cap_fails_fast_single_variant(self, mock_run):
        mock_run.return_value = (1, "", "Too many simultaneous connections. (Failure)", False)
        with self.assertRaises(email_watch.HimalayaCommandError):
            email_watch._run_himalaya_json("/cfg/config_gmail.toml", self.ARGS, account="gmail")
        self.assertEqual(1, mock_run.call_count)

    @patch("email_watch._run_himalaya_subprocess")
    def test_timeout_fails_fast_single_variant(self, mock_run):
        mock_run.return_value = (124, "", "", True)
        with self.assertRaises(email_watch.HimalayaCommandError):
            email_watch._run_himalaya_json("/cfg/config_gmail.toml", self.ARGS, account="gmail")
        self.assertEqual(1, mock_run.call_count)

    @patch("email_watch._run_himalaya_subprocess")
    def test_connection_refused_fails_fast_single_variant(self, mock_run):
        mock_run.return_value = (1, "", "Connection refused (os error 111)", False)
        with self.assertRaises(email_watch.HimalayaCommandError):
            email_watch._run_himalaya_json("/cfg/config_gmail.toml", self.ARGS, account="gmail")
        self.assertEqual(1, mock_run.call_count)

    @patch("email_watch._run_himalaya_subprocess")
    def test_config_error_falls_back_to_more_variants(self, mock_run):
        mock_run.return_value = (1, "", "cannot prompt boolean: The input device is not a TTY", False)
        with self.assertRaises(email_watch.HimalayaCommandError):
            email_watch._run_himalaya_json("/cfg/config_gmail.toml", self.ARGS, account="gmail")
        self.assertGreater(mock_run.call_count, 1)

    @patch("email_watch._run_himalaya_subprocess")
    def test_export_transient_fails_fast(self, mock_run):
        mock_run.return_value = (1, "", "tls handshake eof", False)
        with self.assertRaises(email_watch.HimalayaCommandError):
            email_watch._export_himalaya_message("/cfg/config_gmail.toml", "42", account="gmail")
        self.assertEqual(1, mock_run.call_count)


class CheckFailureCooldownTests(unittest.TestCase):
    def setUp(self):
        path = email_watch._check_failure_state_path()
        if os.path.exists(path):
            os.remove(path)

    def test_first_emits_repeat_suppressed(self):
        exc = RuntimeError("himalaya failed rc=1; Too many simultaneous connections. (Failure)")
        self.assertTrue(email_watch._should_emit_check_failure("Gmail", exc))
        self.assertFalse(email_watch._should_emit_check_failure("Gmail", exc))
        self.assertFalse(email_watch._should_emit_check_failure("Gmail", exc))

    def test_varying_transient_wording_collapses_to_one_signature(self):
        a = RuntimeError("himalaya failed rc=1; Connection refused (os error 111)")
        b = RuntimeError("himalaya failed rc=124; timeout after 30s")
        c = RuntimeError("himalaya failed rc=1; tls handshake eof")
        self.assertTrue(email_watch._should_emit_check_failure("Gmail", a))
        self.assertFalse(email_watch._should_emit_check_failure("Gmail", b))
        self.assertFalse(email_watch._should_emit_check_failure("Gmail", c))

    def test_different_class_still_emits(self):
        transient = RuntimeError("himalaya failed rc=1; Too many simultaneous connections")
        config = RuntimeError("himalaya failed rc=1; cannot prompt boolean not a tty")
        self.assertTrue(email_watch._should_emit_check_failure("Gmail", transient))
        self.assertTrue(email_watch._should_emit_check_failure("Gmail", config))

    def test_clear_on_success_allows_immediate_reemit(self):
        exc = RuntimeError("himalaya failed rc=1; Too many simultaneous connections")
        self.assertTrue(email_watch._should_emit_check_failure("Gmail", exc))
        email_watch._clear_check_failure("Gmail")
        self.assertTrue(email_watch._should_emit_check_failure("Gmail", exc))

    def test_cooldown_expiry_reemits(self):
        exc = RuntimeError("himalaya failed rc=1; Connection refused (os error 111)")
        self.assertTrue(email_watch._should_emit_check_failure("Gmail", exc))
        path = email_watch._check_failure_state_path()
        with open(path, encoding="utf-8") as fh:
            state = json.load(fh)
        state["Gmail"]["ts"] = state["Gmail"]["ts"] - email_watch._CHECK_FAILURE_COOLDOWN_SECONDS - 1
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
        self.assertTrue(email_watch._should_emit_check_failure("Gmail", exc))

    def test_accounts_tracked_independently(self):
        exc = RuntimeError("himalaya failed rc=1; Too many simultaneous connections")
        self.assertTrue(email_watch._should_emit_check_failure("Gmail", exc))
        self.assertTrue(email_watch._should_emit_check_failure("USTC", exc))
        self.assertFalse(email_watch._should_emit_check_failure("Gmail", exc))


if __name__ == "__main__":
    unittest.main()
