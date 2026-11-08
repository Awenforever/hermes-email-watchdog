#!/usr/bin/env python3
"""Regression: a spool that cannot be cleared must never be replayed.

The handler clears the notification spool at the start of every cycle. That
clear used to be wrapped in a bare except, so a failure left the previous
cycle's records in the file while the drain still started at offset zero —
every notification in the spool was delivered again.
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

_spec = importlib.util.spec_from_file_location(
    "watchdog_handler", ROOT / "hooks" / "hermes-email-watchdog" / "handler.py"
)
handler = importlib.util.module_from_spec(_spec)
try:
    _spec.loader.exec_module(handler)
except Exception as exc:  # pragma: no cover - import environment problems
    handler = None
    _IMPORT_ERROR = exc


@unittest.skipIf(handler is None, "handler module cannot be imported here")
class SpoolReplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="spool-"))
        self.spool = self.tmp / "notify-spool.jsonl"

    def test_clean_spool_starts_at_zero(self):
        self.spool.write_text('{"text": "old"}\n', encoding="utf-8")
        self.assertEqual(handler._prepare_spool(self.spool), 0)
        self.assertEqual(self.spool.read_text(encoding="utf-8"), "")

    def test_unclearable_spool_starts_after_history(self):
        payload = '{"text": "old-1"}\n{"text": "old-2"}\n'
        self.spool.write_text(payload, encoding="utf-8")
        size = self.spool.stat().st_size
        original = Path.write_text

        def refuse(self_path, *a, **k):
            raise OSError("simulated read-only spool")

        Path.write_text = refuse
        try:
            offset = handler._prepare_spool(self.spool)
        finally:
            Path.write_text = original
        self.assertEqual(offset, size, "history must be skipped, not replayed")


if __name__ == "__main__":
    unittest.main()
