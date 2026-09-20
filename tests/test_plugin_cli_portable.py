#!/usr/bin/env python3
import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import types
import unittest
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
        self.assertTrue(self.command("enable")["enabled"])
        self.assertEqual((state / "enabled").read_text(encoding="utf-8"), "true\n")
        status = self.command("status")
        self.assertEqual(status["semantic_provider"], "USTC")
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


if __name__ == "__main__":
    unittest.main()
