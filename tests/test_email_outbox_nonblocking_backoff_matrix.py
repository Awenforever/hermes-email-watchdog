#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

HANDLER = Path(__file__).with_name("handler_baseline.py")
spec = importlib.util.spec_from_file_location("handler_nonblocking_under_test", HANDLER)
h = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(h)


class Result:
    success = True
    error = ""
    message_id = "isolated-message"


class NonBlockingBackoffTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="email-watchdog-nonblocking-"))
        h.OUTBOX_FILE = self.tmp / "outbox.json"
        h.STATUS_FILE = self.tmp / "status.json"
        h.SEEN_FILE = self.tmp / "seen.json"
        h.SEEN_FILE.write_text('{"stable":true}\n', encoding="utf-8")
        h._once_lock = asyncio.Lock()
        h.OUTBOX_RETRY_BASE_SECONDS = 60
        h.OUTBOX_RETRY_MAX_SECONDS = 3600

    def write_entries(self, rows: list[dict]) -> None:
        h.OUTBOX_FILE.write_text(
            json.dumps({"version": 1, "entries": {row["delivery_id"]: row for row in rows}}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def load_entries(self) -> dict:
        return json.loads(h.OUTBOX_FILE.read_text(encoding="utf-8"))["entries"]

    async def test_01_backoff_is_exponential_and_bounded(self):
        self.assertEqual(h._outbox_retry_delay_seconds(1), 60)
        self.assertEqual(h._outbox_retry_delay_seconds(2), 120)
        self.assertEqual(h._outbox_retry_delay_seconds(3), 240)
        self.assertEqual(h._outbox_retry_delay_seconds(7), 3600)
        self.assertEqual(h._outbox_retry_delay_seconds(198), 3600)

    async def test_02_existing_high_attempt_entry_is_deferred_and_mailbox_polling_runs(self):
        now = datetime.now().astimezone()
        self.write_entries([{
            "id": "old", "delivery_id": "old", "text_hash": "hash", "text": "old payload",
            "status": "pending", "attempts": 198,
            "created_at": (now - timedelta(hours=9)).isoformat(timespec="seconds"),
            "updated_at": now.isoformat(timespec="seconds"),
            "last_attempt_at": now.isoformat(timespec="seconds"),
        }])
        with mock.patch.object(h, "_call_watchdog", return_value="") as poll, mock.patch.object(h, "_send_weixin", side_effect=AssertionError("deferred entry must not send")):
            await h._run_once()
        poll.assert_called_once()
        row = self.load_entries()["old"]
        self.assertEqual(row["attempts"], 198)
        status = json.loads(h.STATUS_FILE.read_text(encoding="utf-8"))
        self.assertEqual(status["state"], "running")
        self.assertEqual(status["pending_deferred"], 1)

    async def test_03_due_failure_is_preserved_and_does_not_block_polling(self):
        old = datetime.now().astimezone() - timedelta(days=1)
        self.write_entries([{
            "id": "due", "delivery_id": "due", "text_hash": "same-hash", "text": "durable payload",
            "status": "pending", "attempts": 1,
            "created_at": old.isoformat(timespec="seconds"),
            "updated_at": old.isoformat(timespec="seconds"),
            "last_attempt_at": old.isoformat(timespec="seconds"),
        }])
        async def fail_send(text, delivery_id=None):
            raise RuntimeError("forced transport failure")
        with mock.patch.object(h, "_call_watchdog", return_value="") as poll, mock.patch.object(h, "_send_weixin", side_effect=fail_send):
            await h._run_once()
        poll.assert_called_once()
        row = self.load_entries()["due"]
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["text"], "durable payload")
        self.assertEqual(row["text_hash"], "same-hash")
        self.assertEqual(row["attempts"], 2)
        self.assertEqual(row["retry_delay_seconds"], 120)
        self.assertTrue(row["next_attempt_at"])
        status = json.loads(h.STATUS_FILE.read_text(encoding="utf-8"))
        self.assertEqual(status["state"], "degraded")
        self.assertEqual(status["pending_total"], 1)

    async def test_04_failed_first_due_entry_does_not_block_later_due_entry(self):
        old = datetime.now().astimezone() - timedelta(days=1)
        rows = []
        for name in ("first", "second"):
            rows.append({
                "id": name, "delivery_id": name, "text_hash": name + "-hash", "text": name + " payload",
                "status": "pending", "attempts": 1,
                "created_at": (old + timedelta(seconds=len(rows))).isoformat(timespec="seconds"),
                "updated_at": old.isoformat(timespec="seconds"),
                "last_attempt_at": old.isoformat(timespec="seconds"),
            })
        self.write_entries(rows)
        async def send(text, delivery_id=None):
            if delivery_id == "first":
                raise RuntimeError("first failed")
            return Result()
        with mock.patch.object(h, "_send_weixin", side_effect=send):
            summary = await h._flush_outbox(limit=3)
        self.assertEqual(summary["attempted"], 2)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["sent"], 1)
        entries = self.load_entries()
        self.assertEqual(entries["first"]["status"], "pending")
        self.assertEqual(entries["second"]["status"], "delivered")

    async def test_05_direct_delivery_failure_becomes_degraded_without_exception(self):
        async def fail_send(text, delivery_id=None):
            raise RuntimeError("direct send failed")
        with mock.patch.object(h, "_call_watchdog", return_value="new notification"), mock.patch.object(h, "_send_weixin", side_effect=fail_send):
            await h._run_once()
        entries = self.load_entries()
        self.assertEqual(len(entries), 1)
        row = next(iter(entries.values()))
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["attempts"], 1)
        self.assertEqual(row["retry_delay_seconds"], 60)
        status = json.loads(h.STATUS_FILE.read_text(encoding="utf-8"))
        self.assertEqual(status["state"], "degraded")
        self.assertEqual(status["last_delivery_id"], row["delivery_id"])

    async def test_06_due_old_failure_still_processes_new_mail_and_preserves_both(self):
        old = datetime.now().astimezone() - timedelta(days=1)
        self.write_entries([{
            "id": "old-due", "delivery_id": "old-due", "text_hash": "old-hash", "text": "old payload",
            "status": "pending", "attempts": 4,
            "created_at": old.isoformat(timespec="seconds"),
            "updated_at": old.isoformat(timespec="seconds"),
            "last_attempt_at": old.isoformat(timespec="seconds"),
        }])
        attempted = []
        async def fail_all(text, delivery_id=None):
            attempted.append(delivery_id)
            raise RuntimeError("transport unavailable")
        with mock.patch.object(h, "_call_watchdog", return_value="new notification") as poll, mock.patch.object(h, "_send_weixin", side_effect=fail_all):
            await h._run_once()
        poll.assert_called_once()
        entries = self.load_entries()
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries["old-due"]["status"], "pending")
        self.assertEqual(entries["old-due"]["attempts"], 5)
        new_rows = [row for key, row in entries.items() if key != "old-due"]
        self.assertEqual(len(new_rows), 1)
        self.assertEqual(new_rows[0]["status"], "pending")
        self.assertEqual(new_rows[0]["attempts"], 1)
        self.assertEqual(len(attempted), 2)
        status = json.loads(h.STATUS_FILE.read_text(encoding="utf-8"))
        self.assertEqual(status["state"], "degraded")
        self.assertEqual(status["pending_total"], 2)

    async def test_07_due_old_failure_does_not_block_successful_new_delivery(self):
        old = datetime.now().astimezone() - timedelta(days=1)
        self.write_entries([{
            "id": "old-due", "delivery_id": "old-due", "text_hash": "old-hash", "text": "old payload",
            "status": "pending", "attempts": 2,
            "created_at": old.isoformat(timespec="seconds"),
            "updated_at": old.isoformat(timespec="seconds"),
            "last_attempt_at": old.isoformat(timespec="seconds"),
        }])
        async def mixed_send(text, delivery_id=None):
            if delivery_id == "old-due":
                raise RuntimeError("old transport failure")
            return Result()
        with mock.patch.object(h, "_call_watchdog", return_value="new notification") as poll, mock.patch.object(h, "_send_weixin", side_effect=mixed_send):
            await h._run_once()
        poll.assert_called_once()
        entries = self.load_entries()
        self.assertEqual(entries["old-due"]["status"], "pending")
        delivered = [row for key, row in entries.items() if key != "old-due"]
        self.assertEqual(len(delivered), 1)
        self.assertEqual(delivered[0]["status"], "delivered")
        status = json.loads(h.STATUS_FILE.read_text(encoding="utf-8"))
        self.assertEqual(status["state"], "degraded")
        self.assertEqual(status["pending_total"], 1)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(NonBlockingBackoffTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    passed = result.testsRun - len(result.failures) - len(result.errors)
    print(f"OUTBOX_NONBLOCKING_BACKOFF_MATRIX={passed}/{result.testsRun}")
    raise SystemExit(0 if result.wasSuccessful() else 1)
