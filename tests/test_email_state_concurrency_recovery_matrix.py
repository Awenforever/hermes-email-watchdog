#!/usr/bin/env python3
"""Concurrency and interruption regressions for repository-owned state."""

from __future__ import annotations

import concurrent.futures
import hashlib
import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ONBOARDING = ROOT / "scripts" / "email_onboarding.py"
HANDLER = ROOT / "hooks" / "hermes-email-watchdog" / "handler.py"


def canonical_sha(value: dict) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class StateConcurrencyRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="email-watchdog-state-concurrency.")
        self.base = Path(self.tmp.name)
        self.state = self.base / "state"
        self.state.mkdir()
        self.fake = self.base / "himalaya"
        self.fake.write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            "cfg=''\n"
            "prev=''\n"
            "for arg in \"$@\"; do\n"
            "  if [ \"$prev\" = '-c' ] || [ \"$prev\" = '--config' ]; then cfg=\"$arg\"; fi\n"
            "  prev=\"$arg\"\n"
            "done\n"
            "if [ -n \"$cfg\" ] && grep -q SLOW_FAIL \"$cfg\" 2>/dev/null; then\n"
            f"  : > {self.base / 'slow-fail-entered'}\n"
            "  sleep 1\n"
            "  exit 9\n"
            "fi\n"
            "if [ -n \"$cfg\" ] && grep -q SLOW_PASS \"$cfg\" 2>/dev/null; then\n"
            f"  : > {self.base / 'slow-pass-entered'}\n"
            "  sleep 20\n"
            "fi\n"
            "case \"$*\" in\n"
            "  *'envelope list'*) printf '[]\\n'; exit 0 ;;\n"
            "  *'--version'*) printf 'fake\\n'; exit 0 ;;\n"
            "  *'--help'*) printf 'fake help\\n'; exit 0 ;;\n"
            "esac\n"
            "exit 2\n",
            encoding="utf-8",
        )
        self.fake.chmod(0o755)
        self.configs = self.base / "configs"
        self.configs.mkdir()
        for name, marker in (
            ("a", "BASELINE_A"),
            ("b", "GOOD_B"),
            ("fail", "SLOW_FAIL"),
            ("kill", "SLOW_PASS"),
        ):
            (self.configs / f"{name}.toml").write_text(
                f"# {marker}\n"
                "[accounts.test]\n"
                "email='test@example.invalid'\n"
                "backend.type='imap'\n"
                "backend.host='fake.invalid'\n"
                "backend.port=993\n"
                "backend.encryption.type='tls'\n"
                "backend.auth.type='password'\n"
                "backend.auth.cmd='pass show fake/test'\n",
                encoding="utf-8",
            )
        self.env = os.environ.copy()
        self.env.update(
            {
                "PYTHONPATH": str(ROOT / "scripts"),
                "HIMALAYA_BIN": str(self.fake),
                "HERMES_EMAIL_WATCHDOG_STATE_ROOT": str(self.state),
                "EMAIL_WATCHDOG_CONFIG": str(self.state / "email_watchdog_config.json"),
                "HERMES_EMAIL_WATCHDOG_ENABLED_FILE": str(self.state / "email_watchdog_enabled"),
                "HERMES_EMAIL_WATCHDOG_ONBOARDING_FILE": str(self.state / "email_watchdog_onboarding.json"),
                "HERMES_EMAIL_WATCHDOG_ONBOARDING_BACKUP_DIR": str(self.state / "backups"),
                "HERMES_EMAIL_WATCHDOG_OWNED_HIMALAYA_DIR": str(self.state / "owned-himalaya"),
                "HERMES_EMAIL_WATCHDOG_ONBOARDING_LOCK_FILE": str(self.state / "onboarding.lock"),
            }
        )
        self.inputs: dict[str, Path] = {}
        for name, chat in (("a", "chat-a"), ("b", "chat-b"), ("fail", "chat-fail"), ("kill", "chat-kill")):
            data = {
                "accounts": [
                    {
                        "id": "test",
                        "type": "himalaya",
                        "label": "Test",
                        "email": "test@example.invalid",
                        "himalaya_config": str(self.configs / f"{name}.toml"),
                        "enabled": True,
                    }
                ],
                "delivery_target": {"platform": "weixin", "chat_id": chat},
                "enable": True,
            }
            path = self.base / f"{name}.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            self.inputs[name] = path
        self.apply("a")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_onboarding(self, command: str, name: str | None = None, timeout: int = 30):
        args = [sys.executable, str(ONBOARDING), command]
        if name is not None:
            args += ["--input-json", str(self.inputs[name])]
        else:
            args += ["--json"]
        return subprocess.run(args, env=self.env, capture_output=True, text=True, timeout=timeout)

    def apply(self, name: str):
        cp = self.run_onboarding("apply", name)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        data = json.loads(cp.stdout)
        self.assertTrue(data["passed"])
        return data

    def load_handler(self, suffix: str, onboarding: Path | None = None, outbox: Path | None = None):
        old = os.environ.copy()
        try:
            os.environ.update(
                {
                    "HERMES_EMAIL_WATCHDOG_STATE_ROOT": str(self.state),
                    "HERMES_EMAIL_WATCHDOG_SKILL_DIR": str(ROOT),
                    "HERMES_EMAIL_WATCHDOG_ONBOARDING_FILE": str(
                        onboarding or self.state / "email_watchdog_onboarding.json"
                    ),
                    "HERMES_EMAIL_WATCHDOG_OUTBOX_FILE": str(
                        outbox or self.state / "email_watchdog_outbox.json"
                    ),
                }
            )
            spec = importlib.util.spec_from_file_location(f"handler_{suffix}", HANDLER)
            module = importlib.util.module_from_spec(spec)
            assert spec.loader
            spec.loader.exec_module(module)
            return module
        finally:
            os.environ.clear()
            os.environ.update(old)

    def test_01_context_capture_uses_serialized_unique_atomic_writes(self):
        target = self.state / "context.json"
        module = self.load_handler("context", onboarding=target)
        messages = [f"配置邮件监控 {i}" for i in range(100)]

        def capture(i: int):
            module._capture_onboarding_target(
                {
                    "message": messages[i],
                    "platform": "weixin",
                    "chat_id": f"chat-{i}",
                    "session_id": f"session-{i}",
                    "user_id": f"user-{i}",
                }
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=24) as ex:
            list(ex.map(capture, range(len(messages))))
        state = json.loads(target.read_text(encoding="utf-8"))
        raw = json.dumps(state, ensure_ascii=False)
        self.assertTrue(str(state["pending_target"]["chat_id"]).startswith("chat-"))
        self.assertTrue(state.get("message_sha256"))
        self.assertTrue(all(message not in raw for message in messages))

    def test_02_outbox_read_modify_write_is_serialized(self):
        target = self.state / "outbox.json"
        module = self.load_handler("outbox", outbox=target)
        errors = []

        def add(i: int):
            try:
                module._outbox_prepare(f"message-{i}")
            except Exception as exc:
                errors.append(exc)

        with concurrent.futures.ThreadPoolExecutor(max_workers=24) as ex:
            list(ex.map(add, range(150)))
        self.assertFalse(errors)
        data = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(len(data["entries"]), 150)
        self.assertTrue(all(row["status"] == "pending" for row in data["entries"].values()))

    def test_03_failed_apply_cannot_rollback_a_later_success(self):
        marker = self.base / "slow-fail-entered"
        marker.unlink(missing_ok=True)
        fail = subprocess.Popen(
            [sys.executable, str(ONBOARDING), "apply", "--input-json", str(self.inputs["fail"])],
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.time() + 5
        while time.time() < deadline and not marker.exists():
            time.sleep(0.02)
        self.assertTrue(marker.exists())
        good = self.apply("b")
        fail_stdout, fail_stderr = fail.communicate(timeout=10)
        self.assertNotEqual(fail.returncode, 0, fail_stdout)
        config = json.loads((self.state / "email_watchdog_config.json").read_text(encoding="utf-8"))
        state = json.loads((self.state / "email_watchdog_onboarding.json").read_text(encoding="utf-8"))
        final_hash = canonical_sha(config)
        self.assertEqual(final_hash, good["config_sha256"], fail_stderr)
        self.assertEqual(state["last_config_sha256"], final_hash)

    def test_04_sigkill_during_validation_does_not_publish_uncommitted_config(self):
        baseline_config = json.loads((self.state / "email_watchdog_config.json").read_text(encoding="utf-8"))
        baseline_state = json.loads((self.state / "email_watchdog_onboarding.json").read_text(encoding="utf-8"))
        baseline_enabled = (self.state / "email_watchdog_enabled").read_text(encoding="utf-8")
        marker = self.base / "slow-pass-entered"
        marker.unlink(missing_ok=True)
        proc = subprocess.Popen(
            [sys.executable, str(ONBOARDING), "apply", "--input-json", str(self.inputs["kill"])],
            env=self.env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            start_new_session=True,
        )
        deadline = time.time() + 15
        while time.time() < deadline and not marker.exists():
            time.sleep(0.02)
        self.assertTrue(marker.exists())
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=5)
        self.assertLess(proc.returncode, 0)
        current_config = json.loads((self.state / "email_watchdog_config.json").read_text(encoding="utf-8"))
        current_state = json.loads((self.state / "email_watchdog_onboarding.json").read_text(encoding="utf-8"))
        current_enabled = (self.state / "email_watchdog_enabled").read_text(encoding="utf-8")
        self.assertEqual(current_config, baseline_config)
        self.assertEqual(current_state, baseline_state)
        self.assertEqual(current_enabled, baseline_enabled)


if __name__ == "__main__":
    unittest.main(verbosity=2)
