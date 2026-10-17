"""Regression tests for the four ``@原文`` renderer-hardening fixes.

Each fix maps to a real defect found while stress-testing 180 long production
emails against the WeChat transport contract (the adapter packs blank-line
separated Markdown blocks into <=2000-char bubbles and HARD-TRUNCATES any
oversized block):

  Fix 1  token-safe oversized splitting (``_choose_cut`` / ``_split_oversized``)
  Fix 2  header field capping (``_cap``) so the header block never trips 2000
  Fix 3  ``clean_body(..., join_lines=False)`` preserves the author's line breaks
  Fix 4  ``DEFAULT_MAX_CHARS`` budget + over-budget truncation note
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

# Isolate state BEFORE importing modules that transitively import email_store
# (email_notification_renderer -> email_identity -> email_store), which calls
# init_db() at import time. Guarantees the in-container suite never opens the
# PRODUCTION DB under /opt/data/plugin-data.
_TMP = Path(tempfile.mkdtemp(prefix="qoder-email-original-"))
os.environ["HERMES_EMAIL_WATCHDOG_STATE_ROOT"] = str(_TMP)
os.environ.pop("EMAIL_WATCHDOG_CONFIG", None)

import email_original as eo  # noqa: E402
from email_notification_renderer import clean_body  # noqa: E402

CAP = eo.MAX_BLOCK_CHARS
BUBBLE = 2000  # weixin adapter hard-truncates any single block over this


class Fix1TokenSafeSplitTests(unittest.TestCase):
    """A safe sentence/token boundary must be preferred over a mid-token hard
    cut, and splitting must stay lossless and under the block cap."""

    def test_early_safe_break_preferred_over_midtoken_hardcut(self):
        prefix = "说明：" + "前言内容。" * 30  # 153 chars, last 。 at index 152
        run = "QWJjZGVmZ2hp" * 150  # 1800 marker-free chars straddling the cap
        para = prefix + run
        self.assertGreater(len(para), CAP)
        blocks = eo._split_oversized(para)
        # The OLD behaviour hard-cut the first window at CAP-1, mid-run. The fix
        # takes the early 。 boundary instead, keeping the run whole in remainder.
        self.assertEqual(len(prefix), len(blocks[0]))
        self.assertTrue(blocks[0].endswith("。"))
        self.assertLess(len(blocks[0]), CAP)
        for block in blocks:
            self.assertLessEqual(len(block), CAP)
        self.assertEqual(para, "".join(blocks))  # lossless (no boundary whitespace)

    def test_single_run_longer_than_cap_hardcuts_losslessly(self):
        big = "QWJjZGVmZ2hpamts" * 200  # 3200 marker-free chars, no safe boundary
        blocks = eo._split_oversized(big)
        self.assertGreater(len(blocks), 1)
        for block in blocks:
            self.assertLessEqual(len(block), CAP)
        self.assertEqual(big, "".join(blocks))

    def test_cjk_wall_without_punctuation_is_capped_and_lossless(self):
        wall = "邮件正文内容测试" * 700  # 5600 CJK chars, no markers, no spaces
        blocks = eo._split_oversized(wall)
        self.assertGreater(len(blocks), 1)
        for block in blocks:
            self.assertLessEqual(len(block), CAP)
        self.assertEqual(wall, "".join(blocks))

    def test_long_url_inside_short_text_is_never_split(self):
        url = "https://track.example.com/click?u=" + "A1b2C3" * 25  # ~183 chars
        para = f"请点击确认：{url} 谢谢。"
        self.assertLess(len(para), CAP)
        blocks = eo._split_oversized(para)
        self.assertEqual([para], blocks)  # fits under cap -> returned untouched
        self.assertTrue(any(url in b for b in blocks))

    def test_every_block_under_bubble_for_pathological_matrix(self):
        samples = {
            "wall_with_spaces": "lorem ipsum dolor sit amet " * 400,
            "cjk_no_punct": "测试内容" * 1500,
            "base64_run": "QWJjZGVmZ2hpamtsbW5vcA==" * 300,
            "mixed_paragraphs": ("标题。\n\n" + "段落内容 " * 200 + "\n\n" + "结尾。" * 500),
            "single_newlines": "行内容\n" * 2000,
        }
        for name, text in samples.items():
            blocks = eo.prepare_blocks(text)
            self.assertTrue(blocks, f"{name}: produced no blocks")
            for block in blocks:
                self.assertTrue(block.strip(), f"{name}: empty block")
                self.assertLessEqual(len(block), CAP, f"{name}: block over cap")
                self.assertLess(len(block), BUBBLE, f"{name}: block over bubble")


class Fix2HeaderCapTests(unittest.TestCase):
    """The header is emitted as ONE block; uncapped fields from automated mailers
    could push it past 2000 and lose the entire header to hard-truncation."""

    def test_cap_collapses_whitespace_ellipsizes_and_is_none_safe(self):
        self.assertEqual("a b", eo._cap("  a   b  ", 10))
        capped = eo._cap("x" * 500, 160)
        self.assertEqual(160, len(capped))
        self.assertTrue(capped.endswith("…"))
        self.assertEqual("", eo._cap(None, 160))

    def test_huge_header_fields_keep_header_block_far_under_bubble(self):
        action = {
            "subject": "主题" * 1000,  # 2000 chars
            "from_name": "名" * 300,
            "from_addr": "a" * 300 + "@example.com",
            "account": "账" * 100,
        }
        out = eo.render_original(action, {"text": "正文。", "date_sent": ""})
        header = out.split("\n\n")[0]
        self.assertTrue(header.startswith("### 📄 邮件原文"))
        self.assertLess(len(header), BUBBLE)
        self.assertLessEqual(len(header), CAP)

    def test_oversized_subject_rendered_with_ellipsis(self):
        action = {"subject": "S" * 400, "from_addr": "a@example.com", "account": "USTC"}
        out = eo.render_original(action, {"text": "正文。", "date_sent": ""})
        subject_line = next(ln for ln in out.splitlines() if ln.startswith("**主题**"))
        self.assertTrue(subject_line.rstrip().endswith("…"))
        self.assertLessEqual(len(subject_line), len("**主题** ") + 160)


class Fix3JoinLinesPreservationTests(unittest.TestCase):
    """``@原文`` means the *original* text: the author's single line breaks must
    survive instead of being reflowed into walls like the notification card."""

    def test_clean_body_join_lines_false_preserves_breaks(self):
        raw = "第一行\n第二行\n第三行"
        self.assertEqual(raw, clean_body(raw, join_lines=False))
        self.assertNotIn("\n", clean_body(raw, join_lines=True))

    def test_prepare_blocks_splits_on_blank_line_keeps_single_newlines(self):
        self.assertEqual(["行一\n行二", "行三"], eo.prepare_blocks("行一\n行二\n\n行三"))

    def test_render_original_keeps_author_line_breaks_in_body(self):
        out = eo.render_original(
            {"subject": "S", "account": "USTC"},
            {"text": "第一行\n第二行\n\n第二段", "date_sent": ""},
        )
        self.assertIn("第一行\n第二行", out)
        self.assertIn("第二段", out)


class Fix4BudgetNoteTests(unittest.TestCase):
    """Over-budget bodies get a truncation note; under-budget bodies do not;
    every emitted block stays under the transport cap either way."""

    def test_default_budget_constant(self):
        self.assertEqual(16000, eo.DEFAULT_MAX_CHARS)

    def test_under_budget_has_no_note(self):
        out = eo.render_original(
            {"subject": "S"}, {"text": "内容。" * 3000, "date_sent": ""}, max_chars=16000
        )  # 9000 chars
        self.assertNotIn("全文约", out)

    def test_over_budget_shows_note_and_caps_every_block(self):
        out = eo.render_original(
            {"subject": "S"}, {"text": "内容。" * 6000, "date_sent": ""}, max_chars=16000
        )  # 18000 chars
        self.assertIn("全文约", out)
        self.assertIn("已显示前", out)
        for block in out.split("\n\n"):
            if block.strip():
                self.assertLessEqual(len(block), CAP)

    def test_note_reports_total_and_shown_char_counts(self):
        out = eo.render_original(
            {"subject": "S"}, {"text": "内容。" * 6000, "date_sent": ""}, max_chars=16000
        )
        self.assertIn("全文约 18000 字", out)
        self.assertTrue(out.splitlines()[-1].startswith("＞ 全文约"))


if __name__ == "__main__":
    unittest.main()
