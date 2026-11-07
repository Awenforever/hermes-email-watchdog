#!/usr/bin/env python3
"""Regression: a burst larger than one page must not lose mail.

2026-11-07: 24 test mails all present in the mailbox, but 7 never entered the
database. A single newest-first page of 5 means anything older is pushed out of
the window by later mail and never fetched again.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

_TMP = Path(tempfile.mkdtemp(prefix="backfill-"))
os.environ["HERMES_EMAIL_WATCHDOG_STATE_ROOT"] = str(_TMP)
os.environ["HERMES_EMAIL_WATCHDOG_OPERATION"] = "readonly"

import email_watch  # noqa: E402


def _row(uid):
    return {"id": str(uid), "subject": "s%s" % uid}


class BackfillTests(unittest.TestCase):
    def setUp(self):
        # Other modules patch the same function; never leak the stub outward.
        self._orig_run = email_watch._run_himalaya_json
        self.calls = []

    def tearDown(self):
        email_watch._run_himalaya_json = self._orig_run

    def _patch(self, pages, size):
        def fake_run(config_path, args, timeout=30, account=None):
            page = int(args[args.index("--page") + 1])
            page_size = int(args[args.index("--page-size") + 1])
            self.calls.append((page, page_size))
            self.assertEqual(page_size, size)
            return {"envelopes": pages.get(page, [])}

        email_watch._run_himalaya_json = fake_run

    def test_burst_larger_than_one_page_is_fully_listed(self):
        pages = {1: [_row(i) for i in range(30, 10, -1)], 2: [_row(i) for i in range(10, 0, -1)]}
        self._patch(pages, size=20)
        rows = email_watch.list_himalaya_backfill("cfg.toml", {}, "Gmail:", page_size=20)
        self.assertEqual(len(rows), 30)
        self.assertEqual([c[0] for c in self.calls], [1, 2])

    def test_known_message_stops_paging(self):
        pages = {1: [_row(4), _row(3), _row(2)], 2: [_row(1), _row(0)]}
        self._patch(pages, size=3)
        rows = email_watch.list_himalaya_backfill("cfg.toml", {"Gmail:2"}, "Gmail:", page_size=3)
        self.assertEqual(len(rows), 3)
        self.assertEqual([c[0] for c in self.calls], [1])

    def test_short_page_ends_paging(self):
        pages = {1: [_row(9), _row(8)]}
        self._patch(pages, size=5)
        rows = email_watch.list_himalaya_backfill("cfg.toml", {}, "Gmail:", page_size=5)
        self.assertEqual(len(rows), 2)
        self.assertEqual([c[0] for c in self.calls], [1])

    def test_page_cap_bounds_the_scan(self):
        pages = {p: [_row(p * 100 + i) for i in range(5)] for p in range(1, 20)}
        self._patch(pages, size=5)
        rows = email_watch.list_himalaya_backfill("cfg.toml", {}, "Gmail:", page_size=5, max_pages=3)
        self.assertEqual(len(rows), 15)
        self.assertEqual([c[0] for c in self.calls], [1, 2, 3])


if __name__ == "__main__":
    unittest.main()