#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import types
import uuid

SKILL_DIR = Path(os.environ.get("HERMES_EMAIL_WATCHDOG_SKILL_DIR", "/opt/data/skills/hermes-email-watchdog"))
HANDLER_PATH = SKILL_DIR / "hooks/hermes-email-watchdog/handler.py"


def load_handler(outbox: Path):
    os.environ["HERMES_EMAIL_WATCHDOG_OUTBOX_FILE"] = str(outbox)
    spec = importlib.util.spec_from_file_location("watchdog_" + uuid.uuid4().hex, HANDLER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("handler load failed")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def entries(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    value = data.get("entries", {})
    assert isinstance(value, dict)
    return value


def test_failure_and_reload_flush() -> None:
    with tempfile.TemporaryDirectory(prefix="email-watchdog-outbox-") as td:
        outbox = Path(td) / "outbox.json"
        first = load_handler(outbox)
        first.OUTBOX_RETRY_BASE_SECONDS = 0
        first.OUTBOX_RETRY_MAX_SECONDS = 0
        entry = first._outbox_prepare("isolated-payload")
        first._outbox_mark_attempt(entry)
        first._outbox_mark_failed(entry, RuntimeError("forced failure"))
        row = entries(outbox)[entry["delivery_id"]]
        assert row["status"] == "pending"
        assert row["attempts"] == 1
        print("OUTBOX_FAILURE_PENDING_RETAINED_OK")

        second = load_handler(outbox)
        second.OUTBOX_RETRY_BASE_SECONDS = 0
        second.OUTBOX_RETRY_MAX_SECONDS = 0

        class Result:
            success = True
            error = ""
            message_id = "isolated-message"

        async def fake_send(text, delivery_id=None):
            assert text == "isolated-payload"
            assert delivery_id
            return Result()

        second._send_weixin = fake_send
        assert asyncio.run(second._flush_outbox())["sent"] == 1
        assert entries(outbox)[entry["delivery_id"]]["status"] == "delivered"
        print("MODULE_RELOAD_PENDING_FLUSH_OK")


def test_sendresult() -> None:
    with tempfile.TemporaryDirectory(prefix="email-watchdog-sendresult-") as td:
        module = load_handler(Path(td) / "outbox.json")
        os.environ["HERMES_EMAIL_WATCHDOG_WEIXIN_CHAT_ID"] = "isolated-chat"

        class Result:
            def __init__(self, success, error="", message_id=None):
                self.success = success
                self.error = error
                self.message_id = message_id

        class Adapter:
            def __init__(self, result):
                self.result = result
                self.metadata = None

            async def send(self, chat_id, text, metadata):
                self.metadata = dict(metadata)
                return self.result

        failed = Adapter(Result(False, "forced"))
        module._runner_ref = lambda: types.SimpleNamespace(adapters={"weixin": failed})
        try:
            asyncio.run(module._send_weixin("x", delivery_id="failure-id"))
        except RuntimeError:
            pass
        else:
            raise AssertionError("success=false accepted")
        assert failed.metadata["_delivery_id"] == "failure-id"

        success = Adapter(Result(True, "", "message-id"))
        module._runner_ref = lambda: types.SimpleNamespace(adapters={"weixin": success})
        result = asyncio.run(module._send_weixin("y", delivery_id="success-id"))
        assert result.success is True
        assert success.metadata["_delivery_id"] == "success-id"
        print("SENDRESULT_TRUE_FALSE_SEMANTICS_OK")


if __name__ == "__main__":
    test_failure_and_reload_flush()
    test_sendresult()
    print("EMAIL_WATCHDOG_OUTBOX_RUNTIME_CONTRACT_OK")
