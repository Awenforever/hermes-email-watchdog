#!/usr/bin/env python3
from __future__ import annotations
import asyncio, importlib.util, json, tempfile, unittest
from pathlib import Path
from unittest import mock
HANDLER=Path(__file__).with_name('handler_baseline.py')
spec=importlib.util.spec_from_file_location('handler_under_test',HANDLER); h=importlib.util.module_from_spec(spec); spec.loader.exec_module(h)
class OutboxTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=Path(tempfile.mkdtemp()); h.OUTBOX_FILE=self.tmp/'outbox.json'; h.STATUS_FILE=self.tmp/'status.json'; h.SEEN_FILE=self.tmp/'seen.json'; h._once_lock=asyncio.Lock()
  h.SEEN_FILE.write_text('{"x":true}\n',encoding='utf-8')
  h.OUTBOX_RETRY_BASE_SECONDS=0; h.OUTBOX_RETRY_MAX_SECONDS=0
 async def test_01_failure_then_retry_one_entry_same_id(self):
  calls=[]
  async def send(text,delivery_id=None):
   calls.append((text,delivery_id))
   if len(calls)==1: raise RuntimeError('transient')
   return type('R',(),{'success':True,'message_id':'m'})()
  outputs=iter(['PAYLOAD',''])
  with mock.patch.object(h,'_call_watchdog',side_effect=lambda:next(outputs)), mock.patch.object(h,'_send_weixin',side_effect=send):
   await h._run_once()
   data=json.loads(h.OUTBOX_FILE.read_text()); self.assertEqual(len(data['entries']),1); entry=next(iter(data['entries'].values())); self.assertEqual(entry['status'],'pending'); first_id=entry['delivery_id']; self.assertEqual(json.loads(h.STATUS_FILE.read_text())['state'],'degraded')
   await h._run_once(); data=json.loads(h.OUTBOX_FILE.read_text()); self.assertEqual(len(data['entries']),1); entry=next(iter(data['entries'].values())); self.assertEqual(entry['status'],'delivered'); self.assertEqual(entry['delivery_id'],first_id); self.assertEqual(calls[0][1],calls[1][1])
 async def test_02_overlap_guard(self):
  h._once_lock=asyncio.Lock(); await h._once_lock.acquire()
  try:
   with mock.patch.object(h,'_call_watchdog') as call: await h._run_once(); call.assert_not_called()
  finally: h._once_lock.release()
if __name__=='__main__':
 suite=unittest.defaultTestLoader.loadTestsFromTestCase(OutboxTests); result=unittest.TextTestRunner(verbosity=2).run(suite); print(f"OUTBOX_IDEMPOTENCY_MATRIX={result.testsRun-len(result.failures)-len(result.errors)}/{result.testsRun}"); raise SystemExit(0 if result.wasSuccessful() else 1)
