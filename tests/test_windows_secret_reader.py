from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("email_onboarding_secret_test", ROOT / "scripts" / "email_onboarding.py")
onboarding = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = onboarding
SPEC.loader.exec_module(onboarding)


class WindowsSecretReaderTests(unittest.TestCase):
    def test_windows_reader_is_strictly_scoped_to_one_user_environment_variable(self):
        command = onboarding._secret_command_from_env("EMAIL_WATCHDOG_IMAP_PASSWORD", "nt")
        self.assertIn("powershell.exe", command)
        self.assertIn("GetEnvironmentVariable('EMAIL_WATCHDOG_IMAP_PASSWORD','User')", command)
        self.assertEqual("powershell.exe", onboarding._validate_secret_command(command))

    def test_posix_reader_uses_printenv(self):
        command = onboarding._secret_command_from_env("EMAIL_WATCHDOG_IMAP_PASSWORD", "posix")
        self.assertEqual("printenv EMAIL_WATCHDOG_IMAP_PASSWORD", command)
        self.assertEqual("printenv", onboarding._validate_secret_command(command))

    def test_environment_name_and_powershell_body_reject_injection(self):
        with self.assertRaises(onboarding.OnboardingError):
            onboarding._secret_command_from_env("EMAIL_PASSWORD;echo", "nt")
        with self.assertRaises(onboarding.OnboardingError):
            onboarding._validate_secret_command(
                'powershell.exe -NoProfile -NonInteractive -Command "Get-ChildItem Env:"'
            )


if __name__ == "__main__":
    unittest.main()
