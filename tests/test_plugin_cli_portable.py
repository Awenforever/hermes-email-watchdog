#!/usr/bin/env python3
import contextlib
import importlib.util
import io
import json
import hashlib
import tarfile
import zipfile
import sys
import tempfile
import types
import unittest
from unittest import mock
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PluginCliPortableTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "profile"
        constants = types.ModuleType("hermes_constants")
        constants.get_hermes_home = lambda: self.home
        self.previous = sys.modules.get("hermes_constants")
        sys.modules["hermes_constants"] = constants
        spec = importlib.util.spec_from_file_location("email_watchdog_plugin_cli_test", ROOT / "plugin_cli.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def tearDown(self):
        if self.previous is None:
            sys.modules.pop("hermes_constants", None)
        else:
            sys.modules["hermes_constants"] = self.previous
        self.temp.cleanup()

    def command(self, action):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            rc = self.module.email_watchdog_command(Namespace(email_watchdog_action=action))
        self.assertEqual(rc, 0)
        return json.loads(output.getvalue())

    def test_enable_disable_markers_are_explicit_and_status_is_redacted(self):
        state = self.home / "plugin-data" / "hermes-email-watchdog"
        state.mkdir(parents=True)
        (state / "config.json").write_text(
            json.dumps({
                "semantic_engine": {"provider_name": "USTC", "model": "qwen3.6-chat"},
                "notification": {"renderer": "adaptive_v1f"},
            }),
            encoding="utf-8",
        )
        (state / "enabled").write_text("true\n", encoding="utf-8")
        self.assertEqual((state / "enabled").read_text(encoding="utf-8"), "true\n")
        status = self.command("status")
        self.assertEqual(status["semantic_provider"], "hermes")
        self.assertEqual(status["semantic_model"], "qwen3.6-chat")
        self.assertEqual(status["notification_renderer"], "adaptive_v1f")
        self.assertNotIn("api_key", json.dumps(status).lower())
        self.assertFalse(self.command("disable")["enabled"])
        self.assertEqual((state / "enabled").read_text(encoding="utf-8"), "false\n")
        self.assertFalse(self.command("status")["enabled"])

    def test_install_runtime_is_profile_scoped_and_backed_up(self):
        first = self.command("install-runtime")
        hook = self.home / "hooks" / "hermes-email-watchdog"
        self.assertEqual(Path(first["hook"]), hook)
        self.assertTrue((hook / "HOOK.yaml").is_file())
        second = self.command("install-runtime")
        self.assertTrue(Path(second["backup"]).is_dir())
        self.assertTrue((Path(second["backup"]) / "HOOK.yaml").is_file())

    def test_enable_fails_closed_until_read_only_validation_passes(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            rc = self.module.email_watchdog_command(Namespace(email_watchdog_action="enable"))
        self.assertNotEqual(rc, 0)
        self.assertFalse((self.home / "plugin-data" / "hermes-email-watchdog" / "enabled").exists())

    def test_setup_uses_the_same_redacted_onboarding_status_engine(self):
        with mock.patch.object(self.module, "_run_onboarding", return_value=0) as run:
            rc = self.module.email_watchdog_command(Namespace(email_watchdog_action="setup"))
        self.assertEqual(rc, 0)
        run.assert_called_once_with("status")

    def test_himalaya_assets_cover_acceptance_platforms(self):
        self.assertEqual("himalaya.x86_64-linux.tgz", self.module._himalaya_asset("Linux", "AMD64")[0])
        self.assertEqual("himalaya.x86_64-windows.zip", self.module._himalaya_asset("Windows", "x86_64")[0])
        self.assertEqual("himalaya.aarch64-linux.tgz", self.module._himalaya_asset("Linux", "arm64")[0])

    def test_himalaya_install_requires_consent(self):
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            rc = self.module._install_himalaya(False)
        self.assertEqual(2, rc)
        self.assertFalse((self.home / "bin" / "himalaya").exists())

    def test_himalaya_install_verifies_archive_and_publishes_atomically(self):
        executable = b"test-himalaya-binary"
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            info = tarfile.TarInfo("release/himalaya")
            info.size = len(executable)
            archive.addfile(info, io.BytesIO(executable))
        payload = buffer.getvalue()
        digest = hashlib.sha256(payload).hexdigest()
        checked = Namespace(returncode=0, stdout="himalaya v1.2.0\n", stderr="")
        output = io.StringIO()
        with mock.patch.object(self.module, "_himalaya_asset", return_value=("himalaya.x86_64-linux.tgz", digest)), mock.patch.object(
            self.module, "_download", return_value=payload
        ), mock.patch.object(self.module.subprocess, "run", return_value=checked), contextlib.redirect_stdout(output):
            self.assertEqual(0, self.module._install_himalaya(True))
        result = json.loads(output.getvalue())
        installed = Path(result["binary"])
        self.assertEqual(executable, installed.read_bytes())
        self.assertEqual(digest, result["archive_sha256"])

    def test_himalaya_install_rejects_checksum_mismatch(self):
        with mock.patch.object(self.module, "_himalaya_asset", return_value=("himalaya.x86_64-linux.tgz", "0" * 64)), mock.patch.object(
            self.module, "_download", return_value=b"tampered"
        ), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(2, self.module._install_himalaya(True))
        self.assertFalse((self.home / "bin" / "himalaya").exists())

    def test_windows_release_accepts_identical_duplicate_executables(self):
        executable = b"signed-windows-himalaya"
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, mode="w") as archive:
            archive.writestr("result/bin/himalaya.exe", executable)
            archive.writestr("himalaya.x86_64-windows.tgz", b"nested release archive")
            archive.writestr("himalaya.exe", executable)
        self.assertEqual(
            executable,
            self.module._binary_from_archive("himalaya.x86_64-windows.zip", buffer.getvalue()),
        )

    def test_windows_release_rejects_conflicting_duplicate_executables(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, mode="w") as archive:
            archive.writestr("result/bin/himalaya.exe", b"one")
            archive.writestr("himalaya.exe", b"two")
        with self.assertRaisesRegex(RuntimeError, "conflicting executables"):
            self.module._binary_from_archive("himalaya.x86_64-windows.zip", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
