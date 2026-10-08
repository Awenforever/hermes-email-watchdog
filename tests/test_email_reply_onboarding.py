import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import email_onboarding
import tempfile
from unittest import mock


class ReplyOnboardingTests(unittest.TestCase):
    def test_generated_account_can_include_confirmed_smtp_backend(self):
        text = email_onboarding._build_himalaya_toml({
            "email": "user@gmail.com", "display_name": "User",
            "secret_env": "MAIL_APP_PASSWORD", "reply_enabled": True,
        }, "gmail")
        self.assertIn('backend.type = "imap"', text)
        self.assertIn('message.send.backend.type = "smtp"', text)
        self.assertIn('message.send.backend.host = "smtp.gmail.com"', text)
        self.assertNotIn("MAIL_APP_PASSWORD=", text)

    def test_generated_account_is_receive_only_by_default(self):
        text = email_onboarding._build_himalaya_toml({
            "email": "user@gmail.com", "secret_env": "MAIL_APP_PASSWORD",
        }, "gmail")
        self.assertNotIn("message.send.backend.type", text)

    def test_safety_transport_flag_matches_confirmed_reply_setting(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            account_config = root / "himalaya.toml"
            account_config.write_text(
                '[accounts.test]\nemail = "user@example.com"\ndefault = true\n'
                '[accounts.test.backend]\ntype = "imap"\n'
                '[accounts.test.message.send.backend]\ntype = "smtp"\n',
                encoding="utf-8",
            )
            payload = {
                "accounts": [{"id": "test", "type": "himalaya", "himalaya_config": str(account_config)}],
                "delivery_target": {"platform": "weixin", "chat_id": "paired"},
                "options": {"reply_outbound_enabled": True, "default_signature": ""},
            }
            with mock.patch.object(email_onboarding, "CONFIG_PATH", root / "missing.json"):
                planned = email_onboarding._plan_internal(payload)["config"]
            self.assertIs(planned["reply"]["outbound_enabled"], True)
            self.assertIs(planned["safety"]["outbound_email_enabled"], True)


if __name__ == "__main__":
    unittest.main()
