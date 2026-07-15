#!/usr/bin/env python3
from __future__ import annotations
import sys, unittest
from pathlib import Path
from unittest import mock
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'scripts'))
import email_delivery

DECISION={
 "classification":{"category":"personal_or_general","label":"个人/一般邮件","confidence":0.9},
 "importance":{"level":"normal","reason":"x"},
 "notification":{"should_notify":True,"content_mode":"summary_only","summary_style":"paragraph","summary":"hello","key_points":[],"original_policy":"none","original_reason":"","special_card":"none"},
 "action":{"required":False,"type":"","description":"","next_step":""},
 "deadline":{"has_deadline":False,"datetime":"","date_text":"","confidence":0.0},
 "attachments":{"present":False,"policy":"none","important_names":[],"reason":""},
 "risk":{"level":"none","notes":[]},
 "evidence":{"source_fields":["subject"],"uncertainties":[]},
}
class DeliveryRouteTests(unittest.TestCase):
 def setUp(self):
  self.email={"id":"1","subject":"hello","body":"body","has_attachments":False,"attachments":[]}
  self.rule={"action":"needs_llm","category":"个人邮件"}
  self.analysis={"should_notify":True,"formatted_summary":"legacy"}
  self.account={}
 def common(self):
  return mock.patch.multiple(email_delivery,
    production_route_enabled=mock.DEFAULT,
    download_attachments=mock.DEFAULT,
    upsert_schedule=mock.DEFAULT,
    install_reminder_cron=mock.DEFAULT,
    _persist_delivery=mock.DEFAULT,
    _ew_prod_record_learning=mock.DEFAULT,
    _ew_prod_record_memory=mock.DEFAULT,
  )
 def test_01_durable_adaptive_exactly_one_text(self):
  with self.common() as m, \
       mock.patch('importlib.reload', side_effect=lambda m:m), \
       mock.patch('email_production_router.extract_features',return_value={"message_key":"m"}), \
       mock.patch('email_production_router.classify_fast_lane',return_value={"fast_lane":False}), \
       mock.patch('email_semantic_engine.analyze_email',return_value={"ok":True,"schema_valid":True,"fallback_used":False,"timeout":False,"decision":DECISION,"message_key":"m"}), \
       mock.patch('email_production_router.decision_to_legacy_analysis',return_value={"should_notify":True}), \
       mock.patch('email_notification_renderer.render_notification',return_value={"ok":True,"text":"ADAPTIVE","renderer_version":"adaptive_v1e"}) as render, \
       mock.patch.object(email_delivery,'_ew_v4_original_deliver_email') as legacy:
   m['production_route_enabled'].return_value=True; m['download_attachments'].return_value=[]; m['upsert_schedule'].return_value=[]; m['install_reminder_cron'].return_value=[]
   r=email_delivery.deliver_email(self.email,self.rule,self.analysis,self.account)
   self.assertEqual(render.call_args.kwargs['settings_override']['renderer'], 'adaptive_v1e')
   self.assertEqual(r['notification_text'],'ADAPTIVE')
   self.assertEqual(r['production_route'],'adaptive_v1e')
   self.assertEqual(r['renderer'].get('renderer_version'),'adaptive_v1e')
   self.assertFalse(r['legacy_fallback_used'])
   legacy.assert_not_called()
   m['_persist_delivery'].assert_called_once()
 def test_02_fast_lane_skips_llm(self):
  with self.common() as m, \
       mock.patch('importlib.reload', side_effect=lambda m:m), \
       mock.patch('email_production_router.extract_features',return_value={"message_key":"m"}), \
       mock.patch('email_production_router.classify_fast_lane',return_value={"fast_lane":True,"kind":"verification_code","reasons":["code"]}), \
       mock.patch('email_production_router.build_fast_decision',return_value=DECISION), \
       mock.patch('email_production_router.decision_to_legacy_analysis',return_value={"should_notify":True}), \
       mock.patch('email_semantic_engine.analyze_email') as llm, \
       mock.patch('email_notification_renderer.render_notification',return_value={"ok":True,"text":"FAST"}):
   m['production_route_enabled'].return_value=True; m['download_attachments'].return_value=[]; m['upsert_schedule'].return_value=[]; m['install_reminder_cron'].return_value=[]
   r=email_delivery.deliver_email(self.email,self.rule,self.analysis,self.account)
   self.assertEqual(r['route_lane'],'fast'); llm.assert_not_called()
 def test_03_semantic_fallback_uses_legacy_once(self):
  with self.common() as m, \
       mock.patch('importlib.reload', side_effect=lambda m:m), \
       mock.patch('email_production_router.extract_features',return_value={"message_key":"m"}), \
       mock.patch('email_production_router.classify_fast_lane',return_value={"fast_lane":False}), \
       mock.patch('email_semantic_engine.analyze_email',return_value={"ok":True,"schema_valid":True,"fallback_used":True,"timeout":True,"error_code":"timeout"}), \
       mock.patch('email_production_router.legacy_fallback_analysis',return_value={"should_notify":True}), \
       mock.patch.object(email_delivery,'_ew_v4_original_deliver_email',return_value={"notification_text":"LEGACY","status":"pushed"}) as legacy:
   m['production_route_enabled'].return_value=True
   r=email_delivery.deliver_email(self.email,self.rule,self.analysis,self.account)
   self.assertEqual(r['notification_text'],'LEGACY'); self.assertTrue(r['legacy_fallback_used']); legacy.assert_called_once()
 def test_04_renderer_failure_uses_legacy_once(self):
  with self.common() as m, \
       mock.patch('importlib.reload', side_effect=lambda m:m), \
       mock.patch('email_production_router.extract_features',return_value={"message_key":"m"}), \
       mock.patch('email_production_router.classify_fast_lane',return_value={"fast_lane":False}), \
       mock.patch('email_semantic_engine.analyze_email',return_value={"ok":True,"schema_valid":True,"fallback_used":False,"timeout":False,"decision":DECISION}), \
       mock.patch('email_production_router.decision_to_legacy_analysis',return_value={"should_notify":True}), \
       mock.patch('email_notification_renderer.render_notification',side_effect=RuntimeError('render')), \
       mock.patch('email_production_router.legacy_fallback_analysis',return_value={"should_notify":True}), \
       mock.patch.object(email_delivery,'_ew_v4_original_deliver_email',return_value={"notification_text":"LEGACY","status":"pushed"}) as legacy:
   m['production_route_enabled'].return_value=True; m['download_attachments'].return_value=[]; m['upsert_schedule'].return_value=[]; m['install_reminder_cron'].return_value=[]
   r=email_delivery.deliver_email(self.email,self.rule,self.analysis,self.account)
   self.assertTrue(r['legacy_fallback_used']); legacy.assert_called_once()
if __name__=='__main__':
 suite=unittest.defaultTestLoader.loadTestsFromTestCase(DeliveryRouteTests); result=unittest.TextTestRunner(verbosity=2).run(suite); print(f"PRODUCTION_DELIVERY_MATRIX={result.testsRun-len(result.failures)-len(result.errors)}/{result.testsRun}"); raise SystemExit(0 if result.wasSuccessful() else 1)
