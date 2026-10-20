#!/usr/bin/env python3
"""On-demand interaction-hint tests (Option A).

The push-card guidance footer must advertise each action ONLY when that action
would actually succeed: ``@提醒`` only when the mail yielded schedulable
deadlines, ``@回复`` only when the reply gate would accept it, while
``@转发``/``@原文`` are always offered.

The KEY invariant: the advertised ``@回复`` can never disagree with the enforced
reply gate (``email_actions._reply_block_reason``), because both consult the same
``email_delivery.reply_block_reason`` over identically-derived fields. Also guards
that the dead ``agently-cli`` transport code stays removed while the inbound
"Agently Mail" sender-label mapping is kept.

Synthetic fixtures only; no private mail, no network, no prod writes.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

_TMP = Path(tempfile.mkdtemp(prefix="qoder-email-ondemand-"))
os.environ["HERMES_EMAIL_WATCHDOG_STATE_ROOT"] = str(_TMP)
os.environ.pop("EMAIL_WATCHDOG_CONFIG", None)

import email_delivery  # noqa: E402
import email_actions  # noqa: E402


def _hint(schedule, replyable=True):
    return email_delivery.append_interaction_guidance("正文", schedule, replyable)


class ReminderHintOnDemandTests(unittest.TestCase):
    def test_no_deadline_hides_reminder(self):
        self.assertNotIn("@提醒", _hint([]))

    def test_non_deadline_items_do_not_count(self):
        self.assertNotIn("@提醒", _hint([{"title": "x"}, {"deadline": ""}]))

    def test_single_deadline_singular_reminder(self):
        hint = _hint([{"deadline": "2030-03-01T09:00"}])
        self.assertIn("`@提醒`以设置自动提醒", hint)
        self.assertNotIn("@提醒 1,2", hint)

    def test_multi_deadline_numbered_reminder(self):
        hint = _hint([{"deadline": "2030-03-01T09:00"}, {"deadline": "2030-05-20T18:00"}])
        self.assertIn("`@提醒 1,2`以设置自动提醒", hint)


class ReplyHintOnDemandTests(unittest.TestCase):
    def test_replyable_shows_reply(self):
        self.assertIn("`@回复` 换行写正文", _hint([], replyable=True))

    def test_not_replyable_hides_reply(self):
        self.assertNotIn("@回复", _hint([], replyable=False))

    def test_forward_and_original_always_present(self):
        for replyable in (True, False):
            hint = _hint([], replyable=replyable)
            self.assertIn("`@转发 目标邮箱` 换行可附言", hint)
            self.assertIn("`@原文` 查看原文", hint)

    def test_noreply_push_card_omits_reply_and_reminder(self):
        # The reported defect: a noreply security mail with no deadline must not
        # advertise @回复 or @提醒.
        replyable = email_delivery._compute_replyable(
            {"from_addr": "noreply@oyunfor.com", "rule_category": "security_alert"},
            {"type": "himalaya"},
            None,
        )
        hint = _hint([], replyable)
        self.assertNotIn("@回复", hint)
        self.assertNotIn("@提醒", hint)
        self.assertIn("`@转发 目标邮箱` 换行可附言", hint)
        self.assertIn("`@原文` 查看原文", hint)


class ReplyBlockReasonTests(unittest.TestCase):
    def test_noreply_sender_blocked(self):
        for sender in ("noreply@oyunfor.com", "no-reply@x.com", "do-not-reply@x.com",
                       "mailer-daemon@x.com", "bounce@x.com", ""):
            self.assertTrue(email_delivery.reply_block_reason(sender, "", "", "himalaya"),
                            msg="expected block for sender=%r" % sender)

    def test_high_risk_blocked(self):
        for risk in ("high", "critical", "高", "严重"):
            self.assertTrue(email_delivery.reply_block_reason("person@example.com", risk, "", "himalaya"))

    def test_auto_category_blocked(self):
        # Canonical automated/notification categories from
        # email_semantic_schema.CATEGORIES must all be non-replyable.
        for cat in ("verification_code", "account_security", "account_status_notice",
                    "invoice_receipt", "health_check_notice", "system_automation_notice",
                    "newsletter_marketing", "delivery_logistics", "data_download_order_notice",
                    "academic_report_digest", "academic_alert_digest"):
            self.assertTrue(email_delivery.reply_block_reason("person@example.com", "", cat, "himalaya"),
                            msg="expected block for category=%r" % cat)

    def test_human_categories_replyable(self):
        # Human-correspondence categories must NOT be over-blocked.
        for cat in ("personal_or_general", "research_feedback_thread",
                    "paper_manuscript_feedback", "meeting_event", "task_deadline",
                    "school_notice", "academic_opportunity_call", "unknown_needs_llm"):
            self.assertEqual("", email_delivery.reply_block_reason("person@example.com", "low", cat, "himalaya"),
                             msg="expected replyable for category=%r" % cat)

    def test_system_automation_notice_push_card_omits_reply(self):
        # Reported defect: a GitHub CI notification (system_automation_notice) from a
        # plain notifications@ address must NOT advertise @回复.
        replyable = email_delivery._compute_replyable(
            {"from_addr": "notifications@github.com", "rule_category": ""},
            {"type": "himalaya"},
            {"risk": {"level": "low"},
             "decision": {"classification": {"category": "system_automation_notice"}}},
        )
        self.assertFalse(replyable)
        hint = _hint([], replyable)
        self.assertNotIn("@回复", hint)
        self.assertIn("`@转发 目标邮箱` 换行可附言", hint)
        self.assertIn("`@原文` 查看原文", hint)

    def test_non_himalaya_blocked(self):
        for acct in ("agently", "", "imap"):
            self.assertTrue(email_delivery.reply_block_reason("person@example.com", "", "", acct))

    def test_normal_himalaya_replyable(self):
        self.assertEqual("", email_delivery.reply_block_reason("person@example.com", "low", "work", "himalaya"))


class HintMatchesActionGateTests(unittest.TestCase):
    """The advertised @回复 must equal what the action layer would enforce."""

    def _check(self, sender, risk, category, account_type):
        semantic = {
            "risk": {"level": risk},
            "decision": {"classification": {"category": category}},
        }
        email = {"from_addr": sender, "rule_category": ""}
        account = {"type": account_type}
        action = {
            "from_addr": sender, "risk_label": risk,
            "category": category, "account_type": account_type,
        }
        replyable = email_delivery._compute_replyable(email, account, semantic)
        action_block = email_actions._reply_block_reason(action)
        label = (sender, risk, category, account_type)
        self.assertEqual(replyable, action_block == "", msg="hint/action disagree for %r" % (label,))
        hint = email_delivery.append_interaction_guidance("正文", [], replyable)
        self.assertEqual("@回复" in hint, replyable, msg="hint/@回复 mismatch for %r" % (label,))
        self.assertEqual("@回复" in hint, action_block == "", msg="hint/gate mismatch for %r" % (label,))

    def test_matrix(self):
        senders = ["person@example.com", "noreply@oyunfor.com", ""]
        risks = ["", "low", "high", "严重"]
        categories = ["", "work", "verification_code", "newsletter_marketing"]
        account_types = ["himalaya", "agently", ""]
        for sender in senders:
            for risk in risks:
                for category in categories:
                    for account_type in account_types:
                        self._check(sender, risk, category, account_type)

    def test_legacy_route_uses_rule_category_when_no_semantic(self):
        # Simple/legacy delivery returns no `semantic`; the action layer then
        # falls back to email.rule_category. The hint must derive it identically.
        email = {"from_addr": "person@example.com", "rule_category": "verification_code"}
        account = {"type": "himalaya"}
        self.assertFalse(email_delivery._compute_replyable(email, account, None))
        action = {"from_addr": "person@example.com", "risk_label": "",
                  "category": "verification_code", "account_type": "himalaya"}
        self.assertTrue(email_actions._reply_block_reason(action))

    def test_legacy_route_replyable_when_rule_category_normal(self):
        email = {"from_addr": "person@example.com", "rule_category": "work"}
        account = {"type": "himalaya"}
        self.assertTrue(email_delivery._compute_replyable(email, account, None))


class DeadAgentlyTransportRemovedTests(unittest.TestCase):
    def test_no_agently_cli_transport_refs(self):
        scripts = sorted((ROOT / "scripts").glob("*.py"))
        self.assertTrue(scripts)
        offenders = []
        for path in scripts:
            text = path.read_text(encoding="utf-8")
            if "agently-cli" in text:
                offenders.append(path.name + ":agently-cli")
            for dead in ("def list_agently", "def read_agently",
                         "def download_agently_attachment", "def _download_agently"):
                if dead in text:
                    offenders.append("%s:%s" % (path.name, dead))
        self.assertEqual([], offenders)

    def test_sender_label_mapping_is_kept(self):
        # The inbound "Agently Mail" display-name mapping is NOT transport and
        # must survive the dead-code removal.
        trust = (ROOT / "scripts" / "email_trust.py").read_text(encoding="utf-8")
        self.assertIn("Agently Mail", trust)


if __name__ == "__main__":
    unittest.main(verbosity=2)
