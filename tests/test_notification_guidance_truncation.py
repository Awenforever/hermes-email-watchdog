from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

HANDLER = Path(__file__).with_name("handler_baseline.py")
_spec = importlib.util.spec_from_file_location("handler_guidance_under_test", HANDLER)
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)


class GuidanceTruncationTests(unittest.TestCase):
    def test_long_body_keeps_trailing_hint(self):
        body = "### 📬 新邮件｜USTC\n\n" + ("正文内容。" * 900)
        guidance = "> 引用本消息操作：`@回复` 换行写正文 ｜ `@转发 收件邮箱` 可附言"
        value = body + "\n\n" + guidance
        out = h._truncate_preserving_guidance(value, 1800)
        self.assertIn("已截断", out)
        self.assertIn(guidance, out)
        self.assertLessEqual(len(out), 1800)

    def test_multi_line_guidance_block_is_preserved(self):
        body = "标题\n\n" + ("长" * 4000)
        guidance = "> 如需添加提醒，请引用本消息回复 `@提醒`。\n> 引用本消息操作：`@回复` 换行写正文 ｜ `@转发 收件邮箱` 可附言"
        out = h._truncate_preserving_guidance(body + "\n\n" + guidance, 1800)
        self.assertIn("@提醒", out)
        self.assertIn("@转发 收件邮箱", out)
        self.assertLessEqual(len(out), 1800)

    def test_trailing_body_quote_is_not_mistaken_for_guidance(self):
        # A long email whose tail is a quoted line (starts with '>') but is NOT
        # the interaction hint must be truncated as ordinary body content.
        value = ("正文。" * 900) + "\n> 这是被引用的原文片段"
        out = h._truncate_preserving_guidance(value, 1800)
        self.assertIn("已截断", out)
        self.assertNotIn("这是被引用的原文片段", out)
        self.assertLessEqual(len(out), 1800)

    def test_short_value_is_untouched_by_helper_contract(self):
        value = "短正文\n\n> 引用本消息操作：`@回复` 换行写正文 ｜ `@转发 收件邮箱` 可附言"
        # The helper is only invoked when over the cap; emulate a large cap.
        out = h._sanitize_notification(value)
        self.assertIn("@回复", out)
        self.assertNotIn("已截断", out)


if __name__ == "__main__":
    unittest.main()
