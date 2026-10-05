from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


onboarding = load("windows_path_onboarding", "email_onboarding.py")
email_watch = load("windows_path_watch", "email_watch.py")
email_delivery = load("windows_path_delivery", "email_delivery.py")


class WindowsHimalayaPathTests(unittest.TestCase):
    WINDOWS_CONFIG = r"C:\Users\person\AppData\Local\hermes\plugin-data\email\ustc.toml"

    def test_drive_letter_is_removed_from_himalaya_argument(self):
        expected_cwd = r"C:\Users\person\AppData\Local\hermes\plugin-data\email"
        for module in (onboarding, email_watch, email_delivery):
            argument, cwd = module._himalaya_config_context(self.WINDOWS_CONFIG, "nt")
            self.assertEqual("ustc.toml", argument)
            self.assertEqual(expected_cwd, cwd)
            self.assertNotIn(":", argument)

    def test_posix_paths_remain_absolute(self):
        argument, cwd = email_watch._himalaya_config_context("/srv/hermes/ustc.toml", "posix")
        self.assertEqual("/srv/hermes/ustc.toml", argument)
        self.assertIsNone(cwd)

    def test_onboarding_validation_runs_from_config_directory(self):
        with tempfile.TemporaryDirectory() as td:
            config = Path(td) / "ustc.toml"
            config.write_text("", encoding="utf-8")
            completed = SimpleNamespace(returncode=0, stdout="[]", stderr="")
            with (
                mock.patch.object(onboarding, "_himalaya_binary", return_value="himalaya"),
                mock.patch.object(
                    onboarding,
                    "_himalaya_config_context",
                    return_value=("ustc.toml", self.WINDOWS_CONFIG.rsplit("\\", 1)[0]),
                ),
                mock.patch.object(subprocess, "run", return_value=completed) as run,
            ):
                result = onboarding._validate_himalaya_account(
                    {"id": "ustc", "himalaya_config": str(config)}
                )
            self.assertTrue(result["passed"])
            self.assertEqual("ustc.toml", run.call_args.args[0][2])
            self.assertEqual(self.WINDOWS_CONFIG.rsplit("\\", 1)[0], run.call_args.kwargs["cwd"])

    def test_fresh_enable_baselines_existing_envelopes_without_reading_bodies(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_path = root / "ustc.toml"
            config_path.write_text("", encoding="utf-8")
            seen_path = root / "seen.json"
            config = {
                "accounts": [
                    {
                        "id": "ustc",
                        "type": "himalaya",
                        "himalaya_config": str(config_path),
                        "config": str(config_path),
                    }
                ],
                "paths": {"seen": str(seen_path)},
                "watchdog": {"lookback": 5},
            }
            with mock.patch.object(
                onboarding,
                "_baseline_account_envelopes",
                return_value=[{"id": "old-1"}, {"message_id": "old-2"}],
            ) as envelopes:
                result = onboarding._initialize_seen_baseline(config, {})
            self.assertEqual({"ustc:old-1": True, "ustc:old-2": True}, json.loads(seen_path.read_text()))
            self.assertEqual(2, result["count"])
            envelopes.assert_called_once()

    def test_completed_baseline_is_never_replayed_or_rebuilt(self):
        with tempfile.TemporaryDirectory() as td:
            seen_path = Path(td) / "seen.json"
            seen_path.write_text('{"ustc:existing": true}', encoding="utf-8")
            config = {"accounts": [], "paths": {"seen": str(seen_path)}, "watchdog": {"lookback": 5}}
            with mock.patch.object(onboarding, "_baseline_account_envelopes") as envelopes:
                result = onboarding._initialize_seen_baseline(config, {"baseline_completed": True})
            self.assertFalse(result["initialized"])
            self.assertEqual({"ustc:existing": True}, json.loads(seen_path.read_text()))
            envelopes.assert_not_called()

    def test_migrated_environment_path_is_expanded_before_enable_baseline(self):
        with tempfile.TemporaryDirectory() as td:
            seen_path = Path(td) / "seen.json"
            seen_path.write_text('{"ustc:existing": true}', encoding="utf-8")
            config = {
                "accounts": [],
                "paths": {"seen": "$HERMES_EMAIL_WATCHDOG_STATE_ROOT/seen.json"},
                "watchdog": {"lookback": 5},
            }
            with (
                mock.patch.dict(os.environ, {"HERMES_EMAIL_WATCHDOG_STATE_ROOT": td}),
                mock.patch.object(onboarding, "_baseline_account_envelopes") as envelopes,
            ):
                result = onboarding._initialize_seen_baseline(config, {})
            self.assertFalse(result["initialized"])
            self.assertEqual(1, result["count"])
            envelopes.assert_not_called()


if __name__ == "__main__":
    unittest.main()
