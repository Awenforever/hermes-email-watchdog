#!/usr/bin/env python3
from __future__ import annotations
import importlib, os, sys, unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import email_config
import email_production_router as router

class ProductionRouterTests(unittest.TestCase):
    def setUp(self):
        self.settings_patch = mock.patch.object(router, "settings", return_value={
            "production_route_enabled": True, "all_mail_push": False,
            "legacy_fallback_enabled": True,
            "renderer": "adaptive_v1f", "mode": "production",
        })
        self.settings_patch.start()
    def tearDown(self): self.settings_patch.stop()
    def test_01_enabled(self):
        self.assertTrue(router.production_enabled())
        self.assertEqual(router.settings()["renderer"], "adaptive_v1f")
    def test_08_low_value_legacy_fallback_is_silent(self):
        a=router.legacy_fallback_analysis({"subject":"营销邮件"},{"action":"skip","category":"广告"},{"should_notify":False},"x")
        self.assertFalse(a["should_notify"])
    def test_09_low_value_decision_is_silent(self):
        d={"classification":{"category":"newsletter_marketing"},"importance":{"level":"low"},"notification":{"should_notify":False,"content_mode":"summary_only","summary":"资讯"},"action":{},"deadline":{},"attachments":{},"risk":{}}
        a=router.decision_to_legacy_analysis(d,{})
        self.assertFalse(a["should_notify"])
    def test_12_required_action_pushes(self):
        d={"classification":{"category":"task"},"importance":{"level":"normal"},"notification":{"should_notify":True},"action":{"required":True},"deadline":{},"risk":{}}
        self.assertTrue(router.should_push_notification(d))
    def test_13_weekly_report_pushes(self):
        d={"classification":{"category":"academic_report_digest"},"importance":{"level":"normal"},"notification":{"should_notify":True},"action":{},"deadline":{},"risk":{}}
        self.assertTrue(router.should_push_notification(d))
    def test_13b_academic_alert_pushes(self):
        d={"classification":{"category":"academic_alert_digest"},"importance":{"level":"low"},"notification":{"should_notify":True},"action":{},"deadline":{},"risk":{}}
        self.assertTrue(router.should_push_notification(d))
    def test_14_all_mail_policy_remains_explicit_opt_in(self):
        d={"classification":{"category":"newsletter_marketing"},"importance":{"level":"low"},"notification":{"should_notify":False},"action":{},"deadline":{},"risk":{}}
        with mock.patch.object(router, "settings", return_value={"all_mail_push":True}):
            self.assertTrue(router.should_push_notification(d))
    def test_15_account_status_notice_always_pushes(self):
        d={"classification":{"category":"account_status_notice"},"importance":{"level":"normal"},"notification":{"should_notify":False},"action":{},"deadline":{},"attachments":{},"risk":{}}
        self.assertTrue(router.should_push_notification(d))
    def test_16_personal_correspondence_respects_semantic_notify(self):
        d={"classification":{"category":"personal_or_general"},"importance":{"level":"low"},"notification":{"should_notify":True},"action":{},"deadline":{},"attachments":{},"risk":{}}
        self.assertTrue(router.should_push_notification(d))
    def test_17_school_and_research_updates_respect_semantic_notify(self):
        for category in ("school_notice", "research_feedback_thread", "paper_manuscript_feedback"):
            d={"classification":{"category":category},"importance":{"level":"normal"},"notification":{"should_notify":True},"action":{},"deadline":{},"attachments":{},"risk":{}}
            self.assertTrue(router.should_push_notification(d), category)
    def test_18_ready_download_respects_semantic_notify(self):
        d={"classification":{"category":"data_download_order_notice"},"importance":{"level":"normal"},"notification":{"should_notify":True},"action":{},"deadline":{},"attachments":{},"risk":{}}
        self.assertTrue(router.should_push_notification(d))

if __name__ == '__main__':
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(ProductionRouterTests)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    print(f"PRODUCTION_ROUTER_MATRIX={result.testsRun-len(result.failures)-len(result.errors)}/{result.testsRun}")
    raise SystemExit(0 if result.wasSuccessful() else 1)
