from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


class DistributionContractTests(unittest.TestCase):
    def test_public_defaults_inherit_hermes_models(self):
        import sys
        sys.path.insert(0, str(ROOT / "scripts"))
        import email_config

        semantic = email_config.DEFAULT_CONFIG["semantic_engine"]
        self.assertEqual("hermes", semantic["provider"])
        self.assertEqual("", semantic["model"])
        self.assertEqual("", semantic["fallback_model"])

    def test_delivery_uses_configured_non_weixin_adapter(self):
        with tempfile.TemporaryDirectory() as raw:
            state = Path(raw)
            config = state / "config.json"
            config.write_text(json.dumps({
                "delivery": {"target": {"platform": "feishu", "chat_id": "chat-1"}}
            }), encoding="utf-8")
            env = {
                "HERMES_EMAIL_WATCHDOG_STATE_ROOT": str(state),
                "HERMES_EMAIL_WATCHDOG_SKILL_DIR": str(ROOT),
                "EMAIL_WATCHDOG_CONFIG": str(config),
            }
            with mock.patch.dict(os.environ, env, clear=False):
                spec = importlib.util.spec_from_file_location(
                    "email_watchdog_distribution_handler",
                    ROOT / "hooks" / "hermes-email-watchdog" / "handler.py",
                )
                assert spec and spec.loader
                handler = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(handler)

            calls = []

            class Adapter:
                async def send(self, chat_id, text, metadata=None):
                    calls.append((chat_id, text, metadata.get("_delivery_id")))
                    return SimpleNamespace(success=True, message_id="m", error="")

            handler._runner_ref = lambda: SimpleNamespace(adapters={"feishu": Adapter()})
            asyncio.run(handler._send_channel("hello", "delivery-1", {}))
            self.assertEqual([("chat-1", "hello", "delivery-1")], calls)


if __name__ == "__main__":
    unittest.main()
