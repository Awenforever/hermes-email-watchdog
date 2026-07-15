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
            "production_route_enabled": True, "all_mail_push": True,
            "legacy_fallback_enabled": True, "fast_lane_enabled": True,
            "renderer": "adaptive_v1e", "mode": "production",
        })
        self.settings_patch.start()
    def tearDown(self): self.settings_patch.stop()
    def features(self, codes=None, hints=None):
        return {"message_key":"m1", "code_candidates": codes or [], "semantic_hints": hints or {}}
    def test_01_enabled(self):
        self.assertTrue(router.production_enabled())
        self.assertEqual(router.settings()["renderer"], "adaptive_v1e")
    def test_02_grounded_code_fast(self):
        lane=router.classify_fast_lane({"subject":"登录验证码","body":"验证码 482731"}, self.features(["482731"], {"verification_code_phrase":True}))
        self.assertEqual(lane["kind"], "verification_code")
    def test_03_year_not_code(self):
        lane=router.classify_fast_lane({"subject":"2026 学术周报","body":"本周三篇论文"}, self.features([], {}))
        self.assertFalse(lane["fast_lane"])
    def test_04_urgent_security(self):
        lane=router.classify_fast_lane({"subject":"可疑登录，请立即确认","body":"如非本人立即修改密码"}, self.features())
        self.assertEqual(lane["kind"], "account_security")
    def test_05_nonurgent_security_durable(self):
        lane=router.classify_fast_lane({"subject":"账户资料更新完成","body":"无需回复"}, self.features())
        self.assertFalse(lane["fast_lane"])
    def test_06_urgent_meeting(self):
        lane=router.classify_fast_lane({"subject":"会议将在30分钟后开始","body":"请立即参加"}, self.features())
        self.assertEqual(lane["kind"], "meeting_event")
    def test_07_urgent_deadline(self):
        lane=router.classify_fast_lane({"subject":"今天完成材料确认","body":"请立即提交"}, self.features())
        self.assertEqual(lane["kind"], "task_deadline")
    def test_08_all_mail_legacy_fallback_pushes(self):
        a=router.legacy_fallback_analysis({"subject":"营销邮件"},{"action":"skip","category":"广告"},{"should_notify":False},"x")
        self.assertTrue(a["should_notify"])
    def test_09_decision_conversion_pushes(self):
        d={"classification":{"category":"newsletter_marketing"},"importance":{"level":"low"},"notification":{"should_notify":False,"content_mode":"summary_only","summary":"资讯"},"action":{},"deadline":{},"attachments":{},"risk":{}}
        a=router.decision_to_legacy_analysis(d,{})
        self.assertTrue(a["should_notify"])
    def test_10_fast_code_schema(self):
        email={"id":"1","subject":"登录验证码","body":"验证码 482731","attachments":[],"has_attachments":False}
        features=router.extract_features(email)
        lane=router.classify_fast_lane(email,features)
        d=router.build_fast_decision(email,{"action":"simple_code","category":"验证码"},{"should_notify":True},features,lane)
        self.assertEqual(d["classification"]["category"],"verification_code")
        self.assertEqual(d["notification"]["content_mode"],"code_card")

if __name__ == '__main__':
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(ProductionRouterTests)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    print(f"PRODUCTION_ROUTER_MATRIX={result.testsRun-len(result.failures)-len(result.errors)}/{result.testsRun}")
    raise SystemExit(0 if result.wasSuccessful() else 1)
