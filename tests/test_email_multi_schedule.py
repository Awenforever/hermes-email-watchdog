#!/usr/bin/env python3
"""Multi-schedule (deadlines[]) pipeline + @提醒 selection integration tests.

Proves a single email with several distinct deadlines (e.g. a CFP whose
abstract and full-paper dates differ, and whose sessions are in different
venues) is extracted, fanned out into N account-scoped schedule rows, offered
as a numbered card, and selectively activated via ``@提醒 1,3``.

Synthetic CFP fixture only; no private mail, no network, no prod writes.
Runs entirely against a temp state root.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="qoder-email-multisched-"))
os.environ["HERMES_EMAIL_WATCHDOG_STATE_ROOT"] = str(_TMP)
os.environ.pop("EMAIL_WATCHDOG_CONFIG", None)

import email_semantic_core as core  # noqa: E402
import email_production_router as router  # noqa: E402
import email_delivery  # noqa: E402
import email_actions  # noqa: E402
import email_store  # noqa: E402

SETTINGS = {
    "timezone": "Asia/Shanghai",
    "reminder_offsets_minutes": [1440, 60],
    "create_reminders": True,
    "managed_cron": True,
}
email_delivery.email_config.get_delivery_settings = lambda: SETTINGS
email_actions.email_config.get_delivery_settings = lambda: SETTINGS

SUBJECT = "征稿通知：第三届遥感技术研讨会（CFP）"
BODY = (
    "第三届遥感技术研讨会现开放征稿。请提交论文。"
    "摘要提交截止：2030年3月1日。"
    "全文提交截止：2030年5月20日。"
    "会议地点：北京国际会议中心；线上分会场另行通知。"
)
FACTS = {
    "attachments_present": False,
    "attachment_names": [],
    "code_candidates": [],
    "sender_domain": "conference.org",
    "source_subject": SUBJECT,
    "source_body": BODY,
}
RAW = {
    "category": "academic_opportunity_call",
    "confidence": 0.9,
    "importance": "high",
    "importance_reason": "存在明确投稿截止时间",
    "should_notify": True,
    "content_mode": "deadline_card",
    "summary_style": "bullets",
    "summary": "",
    "key_points": ["摘要提交截止为2030年3月1日", "全文提交截止为2030年5月20日"],
    "summary_evidence": ["摘要提交截止：2030年3月1日", "全文提交截止：2030年5月20日"],
    "original_policy": "none",
    "original_reason": "",
    "action": {
        "type": "submit_form",
        "description": "提交论文摘要与全文",
        "next_step": "在截止日期前完成投稿",
        "evidence": "请提交论文",
    },
    "deadline": None,
    "deadlines": [
        {
            "datetime": "2030-03-01T23:59:00",
            "date_text": "2030年3月1日",
            "label": "摘要提交截止",
            "venue": "北京国际会议中心",
            "confidence": 0.9,
            "evidence": "摘要提交截止：2030年3月1日",
        },
        {
            "datetime": "2030-05-20T23:59:00",
            "date_text": "2030年5月20日",
            "label": "全文提交截止",
            "venue": "线上",
            "confidence": 0.9,
            "evidence": "全文提交截止：2030年5月20日",
        },
    ],
    "attachment_policy": "none",
    "attachment_reason": "",
    "risk": {"level": "none", "notes": []},
    "topic_tags": ["征稿"],
    "uncertainties": [],
}


class MultiSchedulePipelineTests(unittest.TestCase):
    def setUp(self):
        email_store.init_db()
        conn = email_store._get_conn()
        conn.execute("DELETE FROM schedules")
        conn.execute("DELETE FROM messages")
        conn.commit()

    def _decision(self):
        decision, errors, _repairs, _keys = core.normalize_and_expand_detailed(
            RAW, message_key="cfp-1", facts=FACTS
        )
        self.assertFalse(errors, errors[:4])
        return decision

    def _items(self):
        analysis = router.decision_to_legacy_analysis(self._decision(), {})
        email = {
            "id": "cfp-1", "msg_id": "cfp-1", "account": "USTC",
            "subject": SUBJECT, "date_sent": "Mon, 14 Oct 2026 04:36:00 +0000",
        }
        return email_delivery.upsert_schedule(email, analysis)

    def test_core_grounds_two_deadlines_and_derives_singular(self):
        decision = self._decision()
        dl = decision.get("deadlines") or []
        self.assertEqual(len(dl), 2)
        self.assertEqual(dl[0]["label"], "摘要提交截止")
        self.assertEqual(dl[0]["venue"], "北京国际会议中心")
        self.assertEqual(dl[1]["label"], "全文提交截止")
        self.assertEqual(dl[1]["venue"], "线上")
        sing = decision.get("deadline") or {}
        self.assertTrue(sing.get("has_deadline"))
        self.assertEqual(sing.get("datetime"), dl[0].get("datetime"))

    def test_legacy_analysis_carries_deadlines_list(self):
        analysis = router.decision_to_legacy_analysis(self._decision(), {})
        al = analysis.get("deadlines") or []
        self.assertEqual(len(al), 2)
        self.assertEqual(al[0]["label"], "摘要提交截止")
        self.assertEqual(al[0]["venue"], "北京国际会议中心")

    def test_upsert_fans_out_into_two_offered_rows(self):
        items = self._items()
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["id"], "cfp-1:1")
        self.assertEqual(items[1]["id"], "cfp-1:2")
        self.assertTrue(all(i["status"] == "offered" for i in items))
        self.assertIn("摘要提交截止", items[0]["title"])
        self.assertIn("北京国际会议中心", items[0]["title"])
        self.assertTrue(items[0].get("reminders"))

    def test_guidance_card_is_numbered_with_venues(self):
        guidance = email_delivery.append_interaction_guidance("通知正文", self._items())
        self.assertIn("1. `", guidance)
        self.assertIn("摘要提交截止", guidance)
        self.assertIn("北京国际会议中心", guidance)
        self.assertIn("线上", guidance)
        self.assertIn("@提醒 1,3", guidance)
        self.assertNotIn("提醒我", guidance)

    def test_activate_multi_select_creates_two_scoped_rows(self):
        items = self._items()
        reply = email_actions._activate_reminders(
            {"account": "USTC", "deadlines": [dict(i) for i in items]}, [1, 2]
        )
        self.assertIn("已创建邮件提醒", reply)
        self.assertIn("摘要提交截止", reply)
        self.assertIn("全文提交截止", reply)
        ids = sorted(r["id"] for r in email_store.get_schedules("active", 50))
        self.assertEqual(ids, ["USTC:cfp-1:1", "USTC:cfp-1:2"])

    def test_single_deadline_back_compat_uses_main_id(self):
        analysis = {
            "deadline": {
                "has_deadline": True, "datetime": "2030-03-01T23:59:00",
                "date_text": "2030年3月1日", "timezone": "Asia/Shanghai",
            },
            "action_needed": {"required": True, "next_step": "提交摘要"},
        }
        items = email_delivery.upsert_schedule({"id": "s-1", "subject": "单一截止"}, analysis)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], "s-1:main")

    def test_zero_candidate_reminder_wording(self):
        msg = email_actions._activate_reminders({"account": "USTC", "deadlines": []}, [1])
        self.assertIn("没有可创建的有效截止时间", msg)


class ReminderCommandDispatchTests(unittest.TestCase):
    """Drive the real ``handle_inbound`` @提醒 branch over a 2-deadline push."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        email_actions.OUTBOX_FILE = root / "outbox.json"
        email_actions.ACTION_FILE = root / "actions.json"
        email_store.init_db()
        conn = email_store._get_conn()
        conn.execute("DELETE FROM schedules")
        conn.execute("DELETE FROM messages")
        conn.commit()

        items = self._build_items()
        self.notification = (
            "### 📬 新邮件｜USTC\n\n**发件人** `cfp@conference.org`\n\n**主题** " + SUBJECT
        )
        metadata = {"mail_actions": [{
            "notification_text": self.notification,
            "message_id": "cfp-1", "account": "USTC", "account_type": "himalaya",
            "himalaya_config": str(root / "himalaya.toml"),
            "from_addr": "cfp@conference.org", "subject": SUBJECT,
            "risk_label": "low", "deadlines": items,
        }]}
        email_actions.OUTBOX_FILE.write_text(
            json.dumps({"entries": {"x": {"text": self.notification, "metadata": metadata}}}),
            encoding="utf-8",
        )

    def tearDown(self):
        self._tmp.cleanup()

    def _build_items(self):
        analysis = {
            "deadlines": [
                {"datetime": "2030-03-01T23:59:00", "date_text": "2030年3月1日",
                 "label": "摘要提交截止", "venue": "北京国际会议中心", "timezone": "Asia/Shanghai"},
                {"datetime": "2030-05-20T23:59:00", "date_text": "2030年5月20日",
                 "label": "全文提交截止", "venue": "线上", "timezone": "Asia/Shanghai"},
            ],
            "action_needed": {"required": True, "next_step": "投稿"},
        }
        email = {"id": "cfp-1", "msg_id": "cfp-1", "account": "USTC",
                 "subject": SUBJECT, "date_sent": "Mon, 14 Oct 2026 04:36:00 +0000"}
        return email_delivery.upsert_schedule(email, analysis)

    def context(self, text, mid="in-1"):
        return {"platform": "weixin", "authorized": True, "user_id": "paired",
                "message_id": mid, "message": text,
                "reference": {"present": True, "text": self.notification}}

    def test_bare_remind_with_two_candidates_asks_for_numbers(self):
        result = email_actions.handle_inbound(self.context("@提醒"))
        self.assertEqual("handled", result["decision"])
        self.assertIn("检测到 2 个截止时间", result["message"])
        self.assertIn("@提醒 1,3", result["message"])
        self.assertEqual([], email_store.get_schedules("active", 50))

    def test_inline_selection_activates_only_chosen(self):
        result = email_actions.handle_inbound(self.context("@提醒 2"))
        self.assertIn("已创建邮件提醒", result["message"])
        self.assertIn("全文提交截止", result["message"])
        self.assertNotIn("摘要提交截止", result["message"])
        ids = [r["id"] for r in email_store.get_schedules("active", 50)]
        self.assertEqual(["USTC:cfp-1:2"], ids)

    def test_bare_numeric_selection_activates_chosen(self):
        result = email_actions.handle_inbound(self.context("1"))
        self.assertIn("已创建邮件提醒", result["message"])
        ids = [r["id"] for r in email_store.get_schedules("active", 50)]
        self.assertEqual(["USTC:cfp-1:1"], ids)

    def test_unrecognized_trailing_text_gets_format_hint(self):
        result = email_actions.handle_inbound(self.context("@提醒 明天"))
        self.assertEqual("handled", result["decision"])
        self.assertIn("请在 `@提醒` 后填写编号", result["message"])
        self.assertEqual([], email_store.get_schedules("active", 50))


if __name__ == "__main__":
    unittest.main(verbosity=2)
