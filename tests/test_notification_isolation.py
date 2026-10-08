import asyncio
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
_previous_skill_dir = os.environ.get("HERMES_EMAIL_WATCHDOG_SKILL_DIR")
os.environ["HERMES_EMAIL_WATCHDOG_SKILL_DIR"] = str(ROOT)
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location(
    "email_watchdog_live_handler", ROOT / "hooks" / "hermes-email-watchdog" / "handler.py"
)
handler = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(handler)
if _previous_skill_dir is None:
    os.environ.pop("HERMES_EMAIL_WATCHDOG_SKILL_DIR", None)
else:
    os.environ["HERMES_EMAIL_WATCHDOG_SKILL_DIR"] = _previous_skill_dir


class NotificationIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        handler.OUTBOX_FILE = root / "outbox.json"
        handler.STATUS_FILE = root / "status.json"
        handler.SEEN_FILE = root / "seen.json"
        handler.SEEN_FILE.write_text("{}\n", encoding="utf-8")
        handler._once_lock = asyncio.Lock()
        handler._LAST_WATCHDOG_ITEMS = []

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_each_mail_is_a_distinct_durable_quote_target(self):
        action_a = {"notification_text": "邮件 A", "message_id": "a", "attachments": [{"local_path": "a.pdf"}]}
        action_b = {"notification_text": "邮件 B", "message_id": "b", "attachments": [{"local_path": "b.pdf"}]}

        def poll():
            handler._LAST_WATCHDOG_ITEMS = [
                ("邮件 A", {"model_name": "model-a", "model_generated": True, "attachments": action_a["attachments"], "mail_actions": [action_a]}),
                ("邮件 B", {"model_name": "model-b", "model_generated": True, "attachments": action_b["attachments"], "mail_actions": [action_b]}),
            ]
            return "邮件 A\n\n---\n\n邮件 B"

        sent = []

        async def send(text, delivery_id=None, attribution=None):
            sent.append((text, delivery_id, attribution))
            return SimpleNamespace(success=True, message_id=f"sent-{len(sent)}", error="")

        with mock.patch.object(handler, "_call_watchdog", side_effect=poll), mock.patch.object(handler, "_send_weixin", side_effect=send):
            await handler._run_once()

        self.assertEqual([item[0] for item in sent], ["邮件 A", "邮件 B"])
        self.assertEqual(sent[0][2]["mail_actions"][0]["message_id"], "a")
        self.assertEqual(sent[1][2]["mail_actions"][0]["message_id"], "b")
        data = json.loads(handler.OUTBOX_FILE.read_text(encoding="utf-8"))
        self.assertEqual(len(data["entries"]), 2)
        for entry in data["entries"].values():
            self.assertEqual(len(entry["metadata"]["mail_actions"]), 1)
            self.assertEqual(entry["status"], "delivered")

    def test_identical_text_for_distinct_message_ids_is_not_deduplicated(self):
        first = handler._outbox_prepare("相同正文", {"mail_actions": [{"account": "A", "message_id": "1"}]})
        second = handler._outbox_prepare("相同正文", {"mail_actions": [{"account": "A", "message_id": "2"}]})
        self.assertNotEqual(first["delivery_id"], second["delivery_id"])
        data = json.loads(handler.OUTBOX_FILE.read_text(encoding="utf-8"))
        self.assertEqual(len(data["entries"]), 2)


if __name__ == "__main__":
    unittest.main()
