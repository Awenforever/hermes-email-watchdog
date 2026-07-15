#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
if SCRIPTS.exists():
    sys.path.insert(0, str(SCRIPTS))
else:
    sys.path.insert(0, str(HERE))

import email_notification_renderer as renderer


def decision(
    *,
    message_key="test:m1",
    mode="summary_only",
    style="paragraph",
    summary="请在7月15日前提交材料。",
    points=None,
    original="none",
    category="学校通知",
    importance="high",
    action=False,
    deadline=False,
    attachments=False,
    risk="none",
    reminders=None,
):
    return {
        "schema_version": 2,
        "message_key": message_key,
        "classification": {"category": "school_notice", "label": category, "confidence": 0.95},
        "importance": {"level": importance, "reason": "test"},
        "notification": {
            "should_notify": True,
            "content_mode": mode,
            "summary_style": style,
            "summary": summary if style == "paragraph" else "",
            "key_points": list(points or []) if style == "bullets" else [],
            "original_policy": original,
            "original_reason": "test",
            "special_card": {
                "code_card": "code",
                "finance_card": "finance",
                "event_card": "event",
                "deadline_card": "deadline",
            }.get(mode, "none"),
        },
        "action": {
            "required": action,
            "type": "submit_form" if action else "",
            "description": "准备并提交材料" if action else "",
            "next_step": "先核对附件清单" if action else "",
        },
        "deadline": {
            "has_deadline": deadline,
            "datetime": "2026-07-15T17:00:00+08:00" if deadline else "",
            "date_text": "7月15日17:00前" if deadline else "",
            "confidence": 0.99 if deadline else 0.0,
        },
        "attachments": {
            "present": attachments,
            "policy": "list_only" if attachments else "none",
            "important_names": ["中期检查表.docx"] if attachments else [],
            "reason": "模板" if attachments else "",
        },
        "risk": {"level": risk, "notes": ["链接域名与发件人不一致"] if risk != "none" else []},
        "reminders": list(reminders or []),
        "memory_observation": {"sender_preference_candidate": None, "topic_tags": [], "user_preference_candidate": None},
        "evidence": {"source_fields": ["subject", "body"], "uncertainties": []},
    }


def email(**updates):
    value = {
        "id": "m1",
        "account": "USTC",
        "from_name": "Miracle",
        "from_addr": "sender@example.com",
        "subject": "中期检查通知",
        "date_sent": "2026-07-12T01:00:00+08:00",
        "body": "请在7月15日17:00前提交中期检查材料。\n需要导师签字。",
        "attachments": [],
        "has_attachments": False,
    }
    value.update(updates)
    return value


class RendererTests(unittest.TestCase):
    def settings(self, **updates):
        value = {
            "renderer": "adaptive_v1",
            "mode": "shadow",
            "original_policy": "auto",
            "original_max_chars": 5000,
            "show_priority": True,
            "show_category": True,
            "show_time": True,
            "show_debug_reason": False,
            "suppress_redundant_summary": True,
        }
        value.update(updates)
        return value

    def render(self, d=None, e=None, delivery=None, **settings):
        return renderer.render_notification(
            e or email(), d or decision(), delivery or {"notification_text": "legacy", "attachments": [], "schedule": []},
            {}, settings_override=self.settings(**settings),
        )

    def test_01_marker(self):
        self.assertEqual(renderer.MARKER, "EMAIL_WATCHDOG_ADAPTIVE_RENDERER_V1E")
        self.assertEqual(renderer.RENDERER_VERSION, "adaptive_v1e")

    def test_02_fixed_header(self):
        text = self.render()["text"]
        self.assertIn("### 📬 新邮件｜USTC", text)
        self.assertIn("`重要` · `学校通知` · `2026-07-12 01:00 SGT`", text)
        self.assertIn("**发件人**\n> Miracle <sender@example.com>", text)
        self.assertIn("**主题**\n> 中期检查通知", text)

    def test_03_paragraph_summary_only(self):
        text = self.render()["text"]
        self.assertEqual(text.count("**摘要**"), 1)
        self.assertIn("请在7月15日前提交材料。", text)
        self.assertNotIn("**要点**", text)
        self.assertNotIn("结构化摘录", text)

    def test_04_bullets_are_single_summary_layer(self):
        d = decision(style="bullets", summary="", points=["提交材料", "导师签字"])
        text = self.render(d=d)["text"]
        self.assertIn("**摘要**\n> - 提交材料\n> - 导师签字", text)
        self.assertNotIn("**要点**", text)
        self.assertNotIn("结构化摘录", text)

    def test_05_subject_summary_duplicate_suppressed(self):
        d = decision(summary="中期检查通知")
        result = self.render(d=d)
        self.assertNotIn("**摘要**", result["text"])
        self.assertEqual(result["duplicate_suppression_count"], 1)

    def test_06_duplicate_bullet_suppressed(self):
        d = decision(style="bullets", summary="", points=["提交材料", "提交材料", "导师签字"])
        result = self.render(d=d)
        self.assertEqual(result["text"].count("> - 提交材料"), 1)
        self.assertEqual(result["duplicate_suppression_count"], 1)

    def test_07_summary_only_has_no_original(self):
        text = self.render()["text"]
        self.assertNotIn("**原文**", text)
        self.assertNotIn("**原文节选**", text)

    def test_08_summary_plus_original_uses_real_body(self):
        d = decision(mode="summary_plus_original", original="full")
        e = email(body="REAL BODY EXACT WORDING")
        text = self.render(d=d, e=e)["text"]
        self.assertIn("**摘要**", text)
        self.assertIn("**原文**\n> REAL BODY EXACT WORDING", text)

    def test_09_long_original_is_explicit_excerpt(self):
        d = decision(mode="summary_plus_original", original="full")
        result = self.render(d=d, e=email(body="A" * 600), original_max_chars=200)
        self.assertIn("**原文节选**", result["text"])
        self.assertIn("已截断，完整正文请在邮箱中查看。", result["text"])
        self.assertTrue(result["original_truncated"])

    def test_10_original_is_not_model_reconstruction(self):
        d = decision(mode="summary_plus_original", original="full", summary="MODEL SUMMARY")
        text = self.render(d=d, e=email(body="TRUE SOURCE BODY"))["text"]
        original = text.split("**原文**", 1)[1]
        self.assertIn("TRUE SOURCE BODY", original)
        self.assertNotIn("MODEL SUMMARY", original)

    def test_11_original_only_omits_summary(self):
        d = decision(mode="original_only", style="none", summary="", original="full")
        text = self.render(d=d, e=email(body="Only exact content"))["text"]
        self.assertNotIn("**摘要**", text)
        self.assertIn("**原文**\n> Only exact content", text)

    def test_12_action_block_adds_new_information(self):
        d = decision(action=True, summary="需要完成中期检查。")
        text = self.render(d=d)["text"]
        self.assertIn("**待办**", text)
        self.assertIn("准备并提交材料", text)
        self.assertIn("下一步：先核对附件清单", text)

    def test_13_duplicate_action_is_suppressed(self):
        d = decision(action=True, summary="准备并提交材料")
        result = self.render(d=d)
        self.assertNotIn("**待办**\n> 准备并提交材料", result["text"])
        self.assertGreaterEqual(result["duplicate_suppression_count"], 1)

    def test_14_deadline_block(self):
        text = self.render(d=decision(deadline=True))["text"]
        self.assertIn("**截止时间**\n> 2026-07-15 17:00 SGT", text)

    def test_15_no_deadline_no_block(self):
        self.assertNotIn("**截止时间**", self.render()["text"])

    def test_16_attachment_block_requires_fact(self):
        d = decision(attachments=True)
        e = email(attachments=[{"filename": "中期检查表.docx"}], has_attachments=True)
        delivery = {"notification_text": "legacy", "attachments": [{"filename": "中期检查表.docx"}], "schedule": []}
        text = self.render(d=d, e=e, delivery=delivery)["text"]
        self.assertIn("**附件**\n> - 📎 中期检查表.docx", text)

    def test_17_absent_attachment_has_no_empty_block(self):
        text = self.render()["text"]
        self.assertNotIn("**附件**", text)
        self.assertNotIn("无附件", text)

    def test_18_model_reminder_alone_is_not_presented_as_created(self):
        d = decision(reminders=[{"time": "2026-07-14T09:00:00+08:00", "kind": "precheck", "message": "检查"}])
        self.assertNotIn("**提醒**", self.render(d=d)["text"])

    def test_19_actual_delivery_schedule_is_rendered(self):
        delivery = {"notification_text": "legacy", "attachments": [], "schedule": [{"time": "2026-07-14T09:00:00+08:00", "message": "检查材料"}]}
        text = self.render(delivery=delivery)["text"]
        self.assertIn("**提醒**\n> - 2026-07-14 09:00 SGT｜检查材料", text)

    def test_20_risk_block_only_for_non_none(self):
        text = self.render(d=decision(risk="high"))["text"]
        self.assertIn("**风险提示**", text)
        self.assertIn("链接域名与发件人不一致", text)

    def test_20b_risk_notes_render_as_single_mobile_stable_line(self):
        text = self.render(d=decision(risk="low"))["text"]
        risk_section = text.split("**风险提示**", 1)[1].split("**", 1)[0]
        self.assertIn("> 风险等级：low | 链接域名与发件人不一致", risk_section)
        self.assertEqual(1, sum(1 for line in risk_section.splitlines() if line.startswith("> ")))
        self.assertNotIn("> - ", risk_section)

    def test_20c_multiple_and_multiline_risk_notes_stay_on_one_line(self):
        d = decision(risk="medium")
        d["risk"]["notes"] = ["第一条风险说明\n继续说明", "第二条风险说明"]
        text = self.render(d=d)["text"]
        risk_section = text.split("**风险提示**", 1)[1].split("**", 1)[0]
        self.assertIn("> 风险等级：medium | 第一条风险说明 继续说明；第二条风险说明", risk_section)
        self.assertEqual(1, sum(1 for line in risk_section.splitlines() if line.startswith("> ")))
        self.assertNotIn("> - ", risk_section)

    def test_21_code_card(self):
        d = decision(mode="code_card", summary="用于登录", category="验证码")
        text = self.render(d=d, e=email(subject="登录验证码", body="验证码 482913，5分钟有效"))["text"]
        self.assertIn("**验证码**", text)
        self.assertIn("验证码：`482913`", text)
        self.assertNotIn("**摘要**", text)

    def test_22_finance_card(self):
        d = decision(mode="finance_card", summary="付款成功", category="发票/收据")
        text = self.render(d=d, e=email(body="Payment completed: SGD 18.50"))["text"]
        self.assertIn("**财务信息**", text)
        self.assertIn("金额：SGD 18.50", text)

    def test_23_event_card(self):
        d = decision(mode="event_card", summary="组会安排", deadline=True, category="会议/活动")
        text = self.render(d=d)["text"]
        self.assertIn("**会议/活动**", text)
        self.assertIn("时间：2026-07-15 17:00 SGT", text)

    def test_24_deadline_card(self):
        d = decision(mode="deadline_card", summary="中期检查", action=True, deadline=True, category="任务/截止")
        text = self.render(d=d)["text"]
        self.assertIn("**截止任务**", text)
        self.assertIn("任务：准备并提交材料", text)
        self.assertIn("截止：2026-07-15 17:00 SGT", text)
        self.assertNotIn("**待办**", text)
        self.assertNotIn("**截止时间**", text)

    def test_25_no_empty_or_debug_blocks(self):
        text = self.render()["text"]
        self.assertNotIn("无需操作", text)
        self.assertNotIn("无风险", text)
        self.assertNotIn("reason_code", text)

    def test_26_shadow_persistence(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.sqlite"
            result = renderer.shadow_compare(
                email(), decision(), {"notification_text": "LEGACY", "attachments": [], "schedule": []}, {},
                message_key="test:m1", settings_override=self.settings(), db_path=db,
            )
            self.assertTrue(result["ok"])
            conn = sqlite3.connect(db)
            row = conn.execute("SELECT message_key, adaptive_notification_shadow, production_chars FROM adaptive_renderer_observations").fetchone()
            conn.close()
            self.assertEqual(row[0], "test:m1")
            self.assertIn("**摘要**", row[1])
            self.assertEqual(row[2], len("LEGACY"))

    def test_27_upsert_one_row_per_message(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.sqlite"
            for summary in ("第一次", "第二次"):
                renderer.shadow_compare(
                    email(), decision(summary=summary), {"notification_text": "LEGACY", "attachments": [], "schedule": []}, {},
                    message_key="test:m1", settings_override=self.settings(), db_path=db,
                )
            conn = sqlite3.connect(db)
            count = conn.execute("SELECT COUNT(*) FROM adaptive_renderer_observations").fetchone()[0]
            shadow = conn.execute("SELECT adaptive_notification_shadow FROM adaptive_renderer_observations").fetchone()[0]
            conn.close()
            self.assertEqual(count, 1)
            self.assertIn("第二次", shadow)

    def test_28_production_text_not_persisted_in_plaintext(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.sqlite"
            secret = "PRODUCTION_ONLY_SECRET"
            renderer.shadow_compare(
                email(), decision(), {"notification_text": secret, "attachments": [], "schedule": []}, {},
                message_key="test:m1", settings_override=self.settings(), db_path=db,
            )
            conn = sqlite3.connect(db)
            rows = conn.execute("SELECT blocks_json, comparison_json, adaptive_notification_shadow FROM adaptive_renderer_observations").fetchone()
            conn.close()
            self.assertNotIn(secret, "\n".join(rows))

    def test_29_non_shadow_skips(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.sqlite"
            result = renderer.shadow_compare(
                email(), decision(), {"notification_text": "legacy"}, {}, message_key="test:m1",
                settings_override=self.settings(mode="active"), db_path=db,
            )
            self.assertTrue(result["skipped"])
            self.assertFalse(db.exists())

    def test_30_production_change_flag_is_false(self):
        with tempfile.TemporaryDirectory() as td:
            result = renderer.shadow_compare(
                email(), decision(), {"notification_text": "legacy"}, {}, message_key="test:m1",
                settings_override=self.settings(), db_path=Path(td) / "test.sqlite",
            )
            self.assertFalse(result["production_notification_changed"])
            self.assertFalse(result["weixin_send"])
            self.assertFalse(result["mailbox_write"])


    def test_31_academic_redundant_receipt_uses_grounded_body_highlights(self):
        d = decision(
            mode="summary_plus_original",
            summary="已收到学术研究周报：学术研究周报 2026-W28",
            original="excerpt",
            category="学术报告摘要",
        )
        d["classification"]["category"] = "academic_report_digest"
        e = email(
            subject="学术研究周报 2026-W28",
            body=(
                "# 学术研究周报 2026-W28\n\n"
                "## 本周总体判断\n"
                "本周重点关注多源卫星烟雾识别与跨区域泛化评估。\n\n"
                "## 下一步\n"
                "- 核对入选论文真实性与方向相关性。\n"
                "- 检查报告与交付回执完整性。\n\n"
                "---\nHermes ᥫᩣ\n"
                "此邮件由agent@example.com通过Agent Mail自动发送。举报退订"
            ),
        )
        result = self.render(d=d, e=e)
        text = result["text"]
        self.assertEqual(text.count("**摘要**"), 1)
        self.assertIn("- 本周重点关注多源卫星烟雾识别与跨区域泛化评估。", text)
        self.assertIn("- 核对入选论文真实性与方向相关性。", text)
        self.assertIn("**原文节选**", text)
        self.assertEqual(result["summary_fallback"], "academic_body_highlights")
        self.assertNotIn("Hermes ᥫᩣ", text)
        self.assertNotIn("Agent Mail", text)
        self.assertNotIn("**要点**", text)
        self.assertNotIn("结构化摘录", text)

    def test_32_academic_fallback_contains_only_real_body_text(self):
        body = (
            "## 研究进展\n"
            "完成火灾烟雾小目标识别对比实验。\n"
            "## 后续\n"
            "补充跨区域测试。"
        )
        d = decision(
            mode="summary_plus_original",
            summary="研究周报",
            original="excerpt",
            category="学术报告摘要",
        )
        d["classification"]["category"] = "academic_report_digest"
        result = self.render(d=d, e=email(subject="研究周报", body=body))
        summary_block = result["text"].split("**摘要**", 1)[1].split("**原文节选**", 1)[0]
        bullets = [line[4:] for line in summary_block.splitlines() if line.startswith("> - ")]
        self.assertTrue(bullets)
        for bullet in bullets:
            self.assertIn(bullet, body)

    def test_33_nonacademic_duplicate_summary_behavior_unchanged(self):
        d = decision(summary="中期检查通知")
        result = self.render(d=d)
        self.assertNotIn("**摘要**", result["text"])
        self.assertEqual(result["summary_fallback"], "")

    def test_34_all_named_content_blocks_use_blockquotes(self):
        d = decision(
            mode="summary_plus_original", original="full", action=True,
            deadline=True, attachments=True, risk="high",
        )
        e = email(attachments=[{"filename": "中期检查表.docx"}], has_attachments=True)
        delivery = {
            "notification_text": "legacy",
            "attachments": [{"filename": "中期检查表.docx"}],
            "schedule": [{"time": "2026-07-14T09:00:00+08:00", "message": "检查材料"}],
        }
        text = self.render(d=d, e=e, delivery=delivery)["text"]
        for title in ("发件人", "主题", "摘要", "待办", "截止时间", "附件", "提醒", "风险提示", "原文"):
            self.assertRegex(text, rf"\*\*{title}\*\*\n>")

    def test_35_code_card_extracts_validity(self):
        d = decision(mode="code_card", summary="收到验证码邮件，请核对来源后使用。", category="验证码")
        text = self.render(
            d=d,
            e=email(subject="账户登录验证码", body="你的验证码是 482731，有效期10分钟。请勿向他人泄露。"),
        )["text"]
        self.assertIn("**验证码**\n> 验证码：`482731`\n> 有效期：10 分钟", text)

    def test_36_code_card_omits_unstated_validity(self):
        d = decision(mode="code_card", summary="收到验证码邮件，请核对来源后使用。", category="验证码")
        text = self.render(d=d, e=email(subject="账户登录验证码", body="你的验证码是 482731。"))["text"]
        self.assertIn("> 验证码：`482731`", text)
        self.assertNotIn("有效期：", text)

    def test_37_code_safety_tip_is_visually_separate(self):
        d = decision(mode="code_card", summary="收到验证码邮件，请核对来源后使用。", category="验证码")
        text = self.render(d=d, e=email(subject="登录验证码", body="验证码 482731，5分钟有效"))["text"]
        self.assertIn("**安全提示**\n> 请核对发件人与邮件来源后使用验证码，不要向他人泄露。", text)
        self.assertNotIn("```text", text)
        code_section = text.split("**验证码**", 1)[1].split("**安全提示**", 1)[0]
        self.assertNotIn("收到验证码邮件", code_section)

    def test_38_multiline_original_keeps_quote_structure(self):
        d = decision(mode="summary_plus_original", original="full")
        text = self.render(d=d, e=email(body="第一段\n\n第二段"))["text"]
        self.assertIn("**原文**\n> 第一段\n>\n> 第二段", text)

    def test_34_all_named_content_blocks_use_blockquotes(self):
        d = decision(
            mode="summary_plus_original", original="full", action=True,
            deadline=True, attachments=True, risk="high",
        )
        e = email(attachments=[{"filename": "中期检查表.docx"}], has_attachments=True)
        delivery = {
            "notification_text": "legacy",
            "attachments": [{"filename": "中期检查表.docx"}],
            "schedule": [{"time": "2026-07-14T09:00:00+08:00", "message": "检查材料"}],
        }
        text = self.render(d=d, e=e, delivery=delivery)["text"]
        for title in ("发件人", "主题", "摘要", "待办", "截止时间", "附件", "提醒", "风险提示", "原文"):
            self.assertRegex(text, rf"\*\*{title}\*\*\n>")

    def test_35_code_card_extracts_validity(self):
        d = decision(mode="code_card", summary="收到验证码邮件，请核对来源后使用。", category="验证码")
        text = self.render(
            d=d,
            e=email(subject="账户登录验证码", body="你的验证码是 482731，有效期10分钟。请勿向他人泄露。"),
        )["text"]
        self.assertIn("**验证码**\n> 验证码：`482731`\n> 有效期：10 分钟", text)

    def test_36_code_card_omits_unstated_validity(self):
        d = decision(mode="code_card", summary="收到验证码邮件，请核对来源后使用。", category="验证码")
        text = self.render(d=d, e=email(subject="账户登录验证码", body="你的验证码是 482731。"))["text"]
        self.assertIn("> 验证码：`482731`", text)
        self.assertNotIn("有效期：", text)

    def test_37_code_safety_tip_is_visually_separate(self):
        d = decision(mode="code_card", summary="收到验证码邮件，请核对来源后使用。", category="验证码")
        text = self.render(d=d, e=email(subject="登录验证码", body="验证码 482731，5分钟有效"))["text"]
        self.assertIn("**安全提示**\n> 请核对发件人与邮件来源后使用验证码，不要向他人泄露。", text)
        self.assertNotIn("```text", text)
        code_section = text.split("**验证码**", 1)[1].split("**安全提示**", 1)[0]
        self.assertNotIn("收到验证码邮件", code_section)

    def test_38_multiline_original_keeps_quote_structure(self):
        d = decision(mode="summary_plus_original", original="full")
        text = self.render(d=d, e=email(body="第一段\n\n第二段"))["text"]
        self.assertIn("**原文**\n> 第一段\n>\n> 第二段", text)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(RendererTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(f"renderer_matrix={result.testsRun - len(result.failures) - len(result.errors)}/{result.testsRun} passed")
    raise SystemExit(0 if result.wasSuccessful() else 1)
