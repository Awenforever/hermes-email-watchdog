#!/usr/bin/env python3
"""Regression matrix for spam-marked journal promotion and model attribution."""
from __future__ import annotations
import asyncio, importlib.util, sys, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1]; SCRIPTS=ROOT/"scripts"; sys.path.insert(0,str(SCRIPTS))
import email_feature_extractor
import email_semantic_core as core
import email_semantic_engine as engine

def newton_email():
 return {"id":"newton-1","msg_id":"newton-1","account":"USTC","subject":"[SPAM] Read the latest issue of Newton","from_addr":"services-solutions@cellpress-cp.email.elsevier.com","from_name":"Elisa De Ranieri","body":"Read the latest issue of Newton. This issue covers research in cell processes, quantum communication and nanostructures. Comments from researchers are included. Manage preferences or unsubscribe.","has_attachment":False,"has_attachments":False,"attachments":[]}
def bad_model_core():
 return {"category":"research_feedback_thread","confidence":0.91,"importance":"critical","importance_reason":"对科研人员和机构有重大影响","should_notify":True,"content_mode":"summary_plus_original","summary_style":"bullets","summary":"","key_points":["涉及多个前沿科学领域","研究内容广泛","重要性高"],"summary_evidence":["research in cell processes","quantum communication","nanostructures"],"original_policy":"excerpt","original_reason":"保留原文","action":None,"deadline":None,"attachment_policy":"none","attachment_reason":"","risk":{"level":"high","notes":["需特别注意保密性及知识产权问题"]},"topic_tags":["科研"],"uncertainties":[]}
class NewtonSpamRegression(unittest.TestCase):
 def test_01_hints(self):
  m=newton_email(); h=engine._facts(m,email_feature_extractor.extract_features(m))["semantic_hints"]; self.assertTrue(h["spam_subject_phrase"]); self.assertTrue(h["publication_issue_subject_phrase"]); self.assertTrue(h["newsletter_marketing_phrase"])
 def test_02_repair(self):
  m=newton_email(); d,e,r,_=core.normalize_and_expand_detailed(bad_model_core(),message_key="USTC:newton-1",facts=engine._facts(m,email_feature_extractor.extract_features(m))); self.assertFalse(e); self.assertEqual(d["classification"]["category"],"newsletter_marketing"); self.assertEqual(d["classification"]["label"],"订阅/营销"); self.assertEqual(d["importance"]["level"],"low"); self.assertEqual(d["risk"],{"level":"none","notes":[]}); self.assertEqual(d["notification"]["content_mode"],"summary_only"); self.assertIn("期刊最新一期",d["notification"]["summary"]); self.assertIn("consistency:spam_marketing_context_category",r); self.assertIn("grounding:drop_unsupported_benign_risk",r)
 def test_03_global_risk_grounding(self):
  m=newton_email(); m["subject"]="Research plan meeting"; m["body"]="The research plan meeting will be held online next Monday."; v=bad_model_core(); v.update({"category":"meeting_event","importance":"normal","summary_style":"paragraph","summary":"将举行研究方案会议。","key_points":[],"summary_evidence":["Research plan meeting"]}); d,e,r,_=core.normalize_and_expand_detailed(v,message_key="USTC:meeting",facts=engine._facts(m,email_feature_extractor.extract_features(m))); self.assertFalse(e); self.assertEqual(d["classification"]["category"],"meeting_event"); self.assertEqual(d["risk"]["level"],"none"); self.assertIn("grounding:drop_unsupported_risk",r)
class AttributionRegression(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); spec=importlib.util.spec_from_file_location("handler_attr",ROOT/"hooks/hermes-email-watchdog/handler.py"); self.h=importlib.util.module_from_spec(spec); spec.loader.exec_module(self.h); self.h.OUTBOX_FILE=Path(self.tmp.name)/"outbox.json"; self.h._chat_id=lambda:"chat"
 def tearDown(self): self.tmp.cleanup()
 def test_04_model_attribution(self):
  calls=[]
  class A:
   async def send(s,chat_id,text,metadata=None): calls.append(dict(metadata or {})); return SimpleNamespace(success=True,message_id="m")
  self.h._runner_ref=lambda:SimpleNamespace(adapters={"weixin":A()}); e=self.h._outbox_prepare("model generated",{"model_name":"qwen2.5:3b","model_generated":True}); asyncio.run(self.h._send_weixin(e["text"],e["delivery_id"],e.get("metadata"))); self.assertEqual(calls[0]["model_name"],"qwen2.5:3b"); self.assertFalse(calls[0]["is_system"])
 def test_05_deterministic_attribution(self):
  calls=[]
  class A:
   async def send(s,chat_id,text,metadata=None): calls.append(dict(metadata or {})); return SimpleNamespace(success=True,message_id="m")
  self.h._runner_ref=lambda:SimpleNamespace(adapters={"weixin":A()}); e=self.h._outbox_prepare("deterministic"); asyncio.run(self.h._send_weixin(e["text"],e["delivery_id"],e.get("metadata"))); self.assertEqual(calls[0]["model_name"],"hermes"); self.assertTrue(calls[0]["is_system"])
if __name__=="__main__":
 result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])); print(f"NEWTON_SPAM_REGRESSION={result.testsRun-len(result.failures)-len(result.errors)}/{result.testsRun}"); raise SystemExit(0 if result.wasSuccessful() else 1)
