#!/usr/bin/env python3
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, 'scripts')
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import email_semantic_core as core
import email_feature_extractor as feature_extractor
import email_semantic_schema as schema


def facts(subject, body, attachments=False):
    return {
        'attachments_present': attachments,
        'attachment_names': [],
        'source_subject': subject,
        'source_body': body,
    }


def base_core():
    return {
        'category': 'invoice_receipt',
        'confidence': 0.9,
        'importance': 'normal',
        'importance_reason': '电子收据已生成',
        'should_notify': True,
        'content_mode': 'summary_only',
        'summary_style': 'bullets',
        'summary': '',
        'key_points': ['电子收据已经生成，可在账单中心下载。'],
        'summary_evidence': ['电子收据已生成'],
        'original_policy': 'none',
        'original_reason': '',
        'action': None,
        'deadline': None,
        'attachment_policy': 'none',
        'attachment_reason': '',
        'risk': {'level': 'none', 'notes': []},
        'topic_tags': ['电子收据'],
        'uncertainties': [],
    }


class GroundingMatrix(unittest.TestCase):
    def test_supported_receipt(self):
        raw = base_core()
        dec, errors, repairs, _ = core.normalize_and_expand_detailed(
            raw, message_key='m1', facts=facts('电子收据下载通知', '您的电子收据已生成，请前往账单中心下载。无需回复。')
        )
        self.assertFalse(errors)
        self.assertEqual(dec['classification']['category'], 'invoice_receipt')
        self.assertFalse(dec['action']['required'])
        self.assertFalse(dec['deadline']['has_deadline'])
        self.assertEqual(dec['notification']['content_mode'], 'summary_only')

    def test_copied_school_example_is_rejected(self):
        raw = base_core()
        raw.update({
            'category': 'task_deadline',
            'content_mode': 'summary_plus_original',
            'summary_style': 'bullets',
            'key_points': ['需要在指定截止时间前提交材料', '材料需要完成签字并上传系统'],
            'summary_evidence': ['指定截止时间前', '导师签字'],
            'original_policy': 'full',
            'action': {'type': 'prepare_submission', 'description': '准备并提交材料', 'next_step': '完成签字', 'evidence': '需要提交材料'},
            'deadline': {'datetime': '', 'date_text': '指定截止时间前', 'confidence': 0.9, 'evidence': '指定截止时间前'},
        })
        dec, errors, repairs, _ = core.normalize_and_expand_detailed(
            raw, message_key='m2', facts=facts('电子收据下载通知', '您的电子收据已生成，请前往账单中心下载。无需回复。')
        )
        self.assertIsNone(dec)
        self.assertTrue(any('grounding insufficient' in e for e in errors))

    def test_unsupported_action_is_dropped(self):
        raw = base_core()
        raw['action'] = {'type': 'prepare_submission', 'description': '提交材料', 'next_step': '上传系统', 'evidence': '提交材料'}
        dec, errors, repairs, _ = core.normalize_and_expand_detailed(
            raw, message_key='m3', facts=facts('电子收据下载通知', '您的电子收据已生成，请前往账单中心下载。无需回复。')
        )
        self.assertFalse(errors)
        self.assertFalse(dec['action']['required'])
        self.assertIn('grounding:drop_unsupported_action', repairs)

    def test_unsupported_deadline_is_dropped_and_card_downgraded(self):
        raw = base_core()
        raw['content_mode'] = 'deadline_card'
        raw['deadline'] = {'datetime': '', 'date_text': '明天17点前', 'confidence': 0.9, 'evidence': '明天17点前'}
        dec, errors, repairs, _ = core.normalize_and_expand_detailed(
            raw, message_key='m4', facts=facts('电子收据下载通知', '您的电子收据已生成，请前往账单中心下载。无需回复。')
        )
        self.assertFalse(errors)
        self.assertFalse(dec['deadline']['has_deadline'])
        self.assertEqual(dec['notification']['content_mode'], 'summary_only')
        self.assertTrue(any(r.startswith('grounding:downgrade_deadline_card') for r in repairs))

    def test_supported_action_and_deadline(self):
        body = '请于2026年7月15日17:00前提交中期检查材料，并完成导师签字。'
        raw = base_core()
        raw.update({
            'category': 'school_notice',
            'importance': 'high',
            'content_mode': 'deadline_card',
            'key_points': ['需要提交中期检查材料。'],
            'summary_evidence': ['提交中期检查材料'],
            'action': {'type': 'prepare_submission', 'description': '提交中期检查材料', 'next_step': '完成导师签字', 'evidence': '提交中期检查材料'},
            'deadline': {'datetime': '2026-07-15T17:00:00+08:00', 'date_text': '2026年7月15日17:00前', 'confidence': 0.95, 'evidence': '2026年7月15日17:00前'},
        })
        dec, errors, repairs, _ = core.normalize_and_expand_detailed(raw, message_key='m5', facts=facts('中期检查通知', body))
        self.assertFalse(errors)
        self.assertTrue(dec['action']['required'])
        self.assertTrue(dec['deadline']['has_deadline'])
        self.assertEqual(dec['notification']['special_card'], 'deadline')
        self.assertTrue(dec['notification']['should_notify'])


    def test_clear_system_test_consistency_repair(self):
        raw = base_core()
        raw.update({
            'category': 'unknown_needs_llm',
            'content_mode': 'original_only',
            'summary_style': 'paragraph',
            'summary': '这是一次无害端到端测试，无需操作。',
            'key_points': [],
            'summary_evidence': ['无害端到端测试', '无需操作'],
            'original_policy': 'full',
        })
        f = facts('Hermes outbox E2E test', '这是一次无害端到端测试。No action is required.')
        f['semantic_hints'] = {'system_test_phrase': True, 'no_action_phrase': True}
        dec, errors, repairs, _ = core.normalize_and_expand_detailed(raw, message_key='m6', facts=f)
        self.assertFalse(errors)
        self.assertEqual(dec['classification']['category'], 'system_automation_notice')
        self.assertEqual(dec['notification']['content_mode'], 'summary_only')
        self.assertFalse(dec['action']['required'])

    def test_school_request_consistency_repair(self):
        body = '请于2026年7月15日17:00前提交中期检查材料，并完成导师签字后上传研究生管理系统。'
        raw = base_core()
        raw.update({
            'category': 'meeting_event',
            'content_mode': 'summary_only',
            'summary_style': 'paragraph',
            'summary': '研究生院要求在规定时间前提交中期检查材料。',
            'key_points': [],
            'summary_evidence': ['提交中期检查材料'],
            'original_policy': 'none',
            'action': None,
            'deadline': {'datetime': '2026-07-15T17:00:00+08:00', 'date_text': '2026年7月15日17:00前', 'confidence': 0.95, 'evidence': '2026年7月15日17:00前'},
        })
        f = facts('中期检查材料提交通知', body)
        f['semantic_hints'] = {
            'school_institution_phrase': True,
            'direct_request_phrase': True,
            'event_phrase': False,
            'no_action_phrase': False,
            'receipt_phrase': False,
            'deadline_phrase': True,
        }
        dec, errors, repairs, _ = core.normalize_and_expand_detailed(raw, message_key='m7', facts=f)
        self.assertFalse(errors)
        self.assertEqual(dec['classification']['category'], 'school_notice')
        self.assertTrue(dec['action']['required'])
        self.assertTrue(dec['deadline']['has_deadline'])
        self.assertEqual(dec['notification']['content_mode'], 'deadline_card')
        self.assertTrue(dec['notification']['should_notify'])

    def test_prompt_has_no_populated_school_example(self):
        prompt = core.build_prompt({'subject': 'x', 'body': 'y'})
        self.assertNotIn('材料需要完成签字并上传系统', prompt)
        self.assertNotIn('指定截止时间前', prompt)
        self.assertIn('GROUNDING RULES', prompt)
        self.assertIn('CLASSIFICATION GUIDE', prompt)
        self.assertIn('original_only: only a very short personal message', prompt)

    def test_clear_newsletter_marketing_consistency_repair(self):
        raw = base_core()
        raw.update({
            'category': 'personal_or_general',
            'importance': 'critical',
            'importance_reason': '模型错误地判为最高优先级',
            'should_notify': False,
            'summary': '这是一封每周产品简报，包含促销优惠，无需操作。',
            'summary_evidence': ['Weekly product newsletter and promotional offers'],
            'action': None,
            'deadline': None,
        })
        f = facts(
            subject='Weekly product newsletter and discount update',
            body='Weekly product newsletter and promotional offers. No action is required.',
        )
        f['semantic_hints'] = {
            'newsletter_marketing_phrase': True,
            'no_action_phrase': True,
            'direct_request_phrase': False,
        }
        dec, errors, repairs, _ = core.normalize_and_expand_detailed(
            raw, message_key='ground:newsletter', facts=f
        )
        self.assertFalse(errors)
        self.assertEqual(dec['classification']['category'], 'newsletter_marketing')
        self.assertEqual(dec['importance']['level'], 'low')
        self.assertIn('consistency:clear_newsletter_marketing_category', repairs)

    def test_grounded_school_send_type_is_canonicalized(self):
        body = '请于2026年7月15日17:00前提交中期检查材料，并完成导师签字后上传研究生管理系统。'
        raw = base_core()
        raw.update({
            'category': 'school_notice',
            'importance': 'high',
            'content_mode': 'deadline_card',
            'key_points': ['需要提交中期检查材料。'],
            'summary_evidence': ['提交中期检查材料'],
            'action': {
                'type': 'send_materials',
                'description': '提交中期检查材料',
                'next_step': '完成导师签字后上传系统',
                'evidence': '提交中期检查材料',
            },
            'deadline': {
                'datetime': '2026-07-15T17:00:00+08:00',
                'date_text': '2026年7月15日17:00前',
                'confidence': 0.95,
                'evidence': '2026年7月15日17:00前',
            },
        })
        f = facts('中期检查材料提交通知', body)
        f['semantic_hints'] = {
            'school_institution_phrase': True,
            'direct_request_phrase': True,
            'deadline_phrase': True,
            'risk_phrase': False,
        }
        dec, errors, repairs, _ = core.normalize_and_expand_detailed(raw, message_key='m8', facts=f)
        self.assertFalse(errors)
        self.assertTrue(dec['action']['required'])
        self.assertEqual(dec['action']['type'], 'review_and_complete')
        self.assertIn('safety:canonicalize_descriptive_action_type=review_and_complete', repairs)

    def test_marketing_hallucinated_risk_is_removed(self):
        raw = base_core()
        raw.update({
            'category': 'newsletter_marketing',
            'importance': 'critical',
            'should_notify': False,
            'summary_style': 'paragraph',
            'summary': '这是一封产品简报，无需操作。',
            'key_points': [],
            'summary_evidence': ['Weekly product newsletter'],
            'action': None,
            'deadline': None,
            'risk': {'level': 'high', 'notes': ['模型误报安全风险']},
        })
        f = facts('Weekly product newsletter', 'Weekly product newsletter. No action is required.')
        f['semantic_hints'] = {
            'newsletter_marketing_phrase': True,
            'direct_request_phrase': False,
            'no_action_phrase': True,
            'risk_phrase': False,
        }
        dec, errors, repairs, _ = core.normalize_and_expand_detailed(raw, message_key='m9', facts=f)
        self.assertFalse(errors)
        self.assertEqual(dec['risk']['level'], 'none')
        self.assertEqual(dec['importance']['level'], 'low')
        self.assertIn('grounding:drop_unsupported_benign_risk', repairs)


    def test_false_notify_is_repaired_for_grounded_action_and_deadline(self):
        body = '请于2026年7月15日17:00前提交中期检查材料，并完成导师签字。'
        raw = base_core()
        raw.update({
            'category': 'school_notice',
            'should_notify': False,
            'importance': 'high',
            'content_mode': 'deadline_card',
            'key_points': ['需要提交中期检查材料。'],
            'summary_evidence': ['提交中期检查材料'],
            'action': {'type': 'submit_form', 'description': '提交中期检查材料', 'next_step': '完成导师签字', 'evidence': '提交中期检查材料'},
            'deadline': {'datetime': '2026-07-15T17:00:00+08:00', 'date_text': '2026年7月15日17:00前', 'confidence': 0.95, 'evidence': '2026年7月15日17:00前'},
        })
        dec, errors, repairs, _ = core.normalize_and_expand_detailed(
            raw, message_key='m10', facts=facts('中期检查通知', body)
        )
        self.assertFalse(errors)
        self.assertTrue(dec['notification']['should_notify'])
        self.assertIn('consistency:action_requires_notification', repairs)


    def test_academic_report_digest_overrides_newsletter_marketing(self):
        raw = base_core()
        raw.update({
            'category': 'newsletter_marketing',
            'importance': 'low',
            'should_notify': True,
            'content_mode': 'summary_plus_original',
            'summary_style': 'paragraph',
            'summary': '这是一封学术研究周报，包含三篇相关论文和后续观察重点。',
            'key_points': [],
            'summary_evidence': ['2026-W28学术研究周报'],
            'original_policy': 'excerpt',
            'action': None,
            'deadline': None,
        })
        f = facts(
            subject='2026-W28学术研究周报',
            body='2026-W28学术研究周报包含三篇相关论文，并总结后续观察重点。',
        )
        f['semantic_hints'] = {
            'academic_report_phrase': True,
            'marketing_promotion_phrase': False,
            'newsletter_marketing_phrase': True,
            'direct_request_phrase': False,
            'school_institution_phrase': False,
            'receipt_phrase': False,
            'system_test_phrase': False,
            'event_phrase': False,
            'risk_phrase': False,
        }
        dec, errors, repairs, _ = core.normalize_and_expand_detailed(
            raw, message_key='ground:academic-report', facts=f
        )
        self.assertFalse(errors)
        self.assertEqual(dec['classification']['category'], 'academic_report_digest')
        self.assertIn('consistency:academic_report_digest_category', repairs)

    def test_academic_commercial_promotion_is_not_reclassified_as_report(self):
        raw = base_core()
        raw.update({
            'category': 'newsletter_marketing',
            'importance': 'critical',
            'should_notify': False,
            'content_mode': 'summary_only',
            'summary_style': 'paragraph',
            'summary': '这是一封研究软件折扣促销邮件。',
            'key_points': [],
            'summary_evidence': ['Research software discount offer'],
            'original_policy': 'none',
            'action': None,
            'deadline': None,
        })
        f = facts(
            subject='Research software discount offer',
            body='Special promotional offer for research software. Save 30 percent. No action is required.',
        )
        f['semantic_hints'] = {
            'academic_report_phrase': True,
            'marketing_promotion_phrase': True,
            'newsletter_marketing_phrase': True,
            'direct_request_phrase': False,
            'school_institution_phrase': False,
            'receipt_phrase': False,
            'system_test_phrase': False,
            'event_phrase': False,
            'risk_phrase': False,
        }
        dec, errors, repairs, _ = core.normalize_and_expand_detailed(
            raw, message_key='ground:academic-promo', facts=f
        )
        self.assertFalse(errors)
        self.assertEqual(dec['classification']['category'], 'newsletter_marketing')
        self.assertEqual(dec['importance']['level'], 'low')


    def test_academic_report_digest_consistency_caps_risk_and_forces_notify(self):
        raw = base_core()
        raw.update({
            'category': 'academic_report_digest',
            'importance': 'critical',
            'should_notify': False,
            'content_mode': 'summary_plus_original',
            'summary_style': 'paragraph',
            'summary': '这是一封学术研究周报，包含三篇相关论文和后续观察重点。',
            'key_points': [],
            'summary_evidence': ['2026-W28学术研究周报'],
            'original_policy': 'excerpt',
            'action': None,
            'deadline': None,
            'risk': {'level': 'low', 'notes': ['模型误报低风险']},
        })
        f = facts(
            subject='2026-W28学术研究周报',
            body='2026-W28学术研究周报已生成。报告包含三篇相关论文，并总结后续观察重点。',
        )
        f['attachments_present'] = True
        f['semantic_hints'] = {
            'academic_report_phrase': True,
            'marketing_promotion_phrase': False,
            'newsletter_marketing_phrase': True,
            'direct_request_phrase': False,
            'school_institution_phrase': False,
            'receipt_phrase': False,
            'system_test_phrase': False,
            'event_phrase': False,
            'risk_phrase': False,
        }
        dec, errors, repairs, _ = core.normalize_and_expand_detailed(
            raw, message_key='ground:academic-report-consistency', facts=f
        )
        self.assertFalse(errors)
        self.assertEqual(dec['classification']['category'], 'academic_report_digest')
        self.assertEqual(dec['risk']['level'], 'none')
        self.assertEqual(dec['importance']['level'], 'normal')
        self.assertTrue(dec['notification']['should_notify'])
        self.assertIn('grounding:drop_unsupported_benign_risk', repairs)
        self.assertIn('consistency:cap_benign_importance=normal', repairs)
        self.assertIn('consistency:academic_report_requires_notification', repairs)


    def test_academic_subject_primary_survives_incidental_body_marketing_and_test_terms(self):
        raw = base_core()
        raw.update({
            'category': 'academic_report_digest',
            'importance': 'critical',
            'should_notify': False,
            'content_mode': 'summary_plus_original',
            'summary_style': 'paragraph',
            'summary': '本期学术研究周报汇总论文进展和实验测试结果。',
            'key_points': [],
            'summary_evidence': ['2026-W28学术研究周报'],
            'original_policy': 'excerpt',
            'action': None,
            'deadline': None,
            'risk': {'level': 'low', 'notes': ['模型误报']},
        })
        f = facts(
            subject='2026-W28学术研究周报',
            body='本周完成模型测试并汇总论文进展。页脚包含 unsubscribe 和 special offer。',
        )
        f['semantic_hints'] = {
            'academic_report_subject_phrase': True,
            'marketing_subject_phrase': False,
            'system_test_subject_phrase': False,
            'academic_report_phrase': True,
            'marketing_promotion_phrase': True,
            'newsletter_marketing_phrase': True,
            'system_test_phrase': True,
            'direct_request_phrase': False,
            'school_institution_phrase': False,
            'receipt_phrase': False,
            'event_phrase': False,
            'risk_phrase': False,
        }
        dec, errors, repairs, _ = core.normalize_and_expand_detailed(
            raw, message_key='ground:academic-subject-primary', facts=f
        )
        self.assertFalse(errors)
        self.assertEqual(dec['classification']['category'], 'academic_report_digest')
        self.assertEqual(dec['risk']['level'], 'none')
        self.assertEqual(dec['importance']['level'], 'normal')
        self.assertTrue(dec['notification']['should_notify'])
        self.assertIn('consistency:academic_report_requires_notification', repairs)


    def test_academic_original_only_with_summary_is_repaired_to_summary_plus_original(self):
        raw = base_core()
        raw.update({
            'category': 'academic_report_digest',
            'importance': 'normal',
            'should_notify': True,
            'content_mode': 'original_only',
            'summary_style': 'paragraph',
            'summary': '本期学术研究周报汇总论文进展和实验结果。',
            'key_points': [],
            'summary_evidence': ['2026-W28学术研究周报'],
            'original_policy': 'full',
            'action': None,
            'deadline': None,
            'risk': {'level': 'none', 'notes': []},
        })
        f = facts(
            subject='2026-W28学术研究周报',
            body='本期学术研究周报汇总论文进展和实验结果。',
        )
        f['semantic_hints'] = {
            'academic_report_subject_phrase': True,
            'marketing_subject_phrase': False,
            'system_test_subject_phrase': False,
            'academic_report_phrase': True,
            'marketing_promotion_phrase': False,
            'newsletter_marketing_phrase': False,
            'system_test_phrase': False,
            'direct_request_phrase': False,
            'school_institution_phrase': False,
            'receipt_phrase': False,
            'event_phrase': False,
            'risk_phrase': False,
        }
        dec, errors, repairs, _ = core.normalize_and_expand_detailed(
            raw, message_key='ground:academic-original-only-repair', facts=f
        )
        self.assertFalse(errors)
        self.assertEqual(dec['classification']['category'], 'academic_report_digest')
        self.assertEqual(
            dec['notification']['content_mode'], 'summary_plus_original'
        )
        self.assertEqual(dec['notification']['summary_style'], 'paragraph')
        self.assertTrue(dec['notification']['summary'])
        self.assertEqual(dec['notification']['original_policy'], 'excerpt')
        self.assertIn(
            'consistency:academic_original_only_to_summary_plus_original', repairs
        )


    def test_grounded_verification_code_forces_code_card(self):
        raw = base_core()
        raw.update({
            'category': 'verification_code',
            'importance': 'high',
            'should_notify': False,
            'content_mode': 'original_only',
            'summary_style': 'paragraph',
            'summary': '登录验证码为482731。',
            'key_points': [],
            'summary_evidence': ['验证码为482731'],
            'original_policy': 'full',
            'action': None,
            'deadline': None,
            'risk': {'level': 'none', 'notes': []},
        })
        f = facts(subject='登录验证码', body='您的登录验证码为482731，10分钟内有效。')
        f['code_candidates'] = ['482731']
        f['semantic_hints'] = {
            'verification_code_phrase': True,
            'academic_report_subject_phrase': False,
        }
        dec, errors, repairs, _ = core.normalize_and_expand_detailed(
            raw, message_key='ground:verification-code-card', facts=f
        )
        self.assertFalse(errors)
        self.assertEqual(dec['classification']['category'], 'verification_code')
        self.assertEqual(dec['notification']['content_mode'], 'code_card')
        self.assertEqual(dec['notification']['special_card'], 'code')
        self.assertEqual(dec['notification']['original_policy'], 'none')
        self.assertTrue(dec['notification']['should_notify'])
        self.assertIn('consistency:grounded_verification_code_requires_code_card', repairs)


    def test_native_schema_requires_evidence(self):
        schema = core.ollama_format_schema()
        required = schema['required']
        self.assertIn('summary_evidence', required)
        action = schema['properties']['action']['anyOf'][1]
        deadline = schema['properties']['deadline']['anyOf'][1]
        self.assertIn('evidence', action['required'])
        self.assertIn('evidence', deadline['required'])


    def test_feature_extractor_rejects_years_dates_and_counters_without_code_context(self):
        text = (
            "⚚ 学术研究周报 2026-W28。"
            "生成时间：2026-07-12T16:03:27+08:00。"
            "run_id=20260712，raw_candidates=123456。"
        )
        self.assertEqual(feature_extractor.extract_code_candidates(text), [])

    def test_feature_extractor_accepts_grounded_verification_code(self):
        text = "您的登录验证码为 482731，10分钟内有效。"
        self.assertEqual(feature_extractor.extract_code_candidates(text), ["482731"])

    def test_conservative_fallback_trusted_simple_code_is_code_card(self):
        email = {
            "subject": "登录验证码",
            "body": "code 123456",
        }
        result = schema.conservative_fallback(
            message_key="verification:fallback",
            email=email,
            rule_result={"category": "验证码", "action": "simple_code"},
            analysis={},
            facts={
                "code_candidates": ["123456"],
                "attachments_present": False,
            },
            reason="test",
        )
        self.assertEqual(result["classification"]["category"], "verification_code")
        self.assertEqual(result["notification"]["content_mode"], "code_card")
        self.assertEqual(result["notification"]["special_card"], "code")
        self.assertTrue(result["notification"]["should_notify"])


    def test_conservative_fallback_does_not_turn_academic_year_into_code(self):
        email = {
            "subject": "⚚ 学术研究周报 2026-W28 — smoke remote sensing",
            "body": "生成时间：2026-07-12T16:03:27+08:00。",
        }
        result = schema.conservative_fallback(
            message_key="academic:fallback",
            email=email,
            rule_result={"category": "发票/收据", "action": "push"},
            analysis={"should_notify": True},
            facts={
                "code_candidates": [],
                "semantic_hints": {
                    "academic_report_subject_phrase": True,
                    "verification_code_phrase": False,
                },
                "attachments_present": False,
            },
            reason="test",
        )
        self.assertEqual(result["classification"]["category"], "academic_report_digest")
        self.assertEqual(result["notification"]["content_mode"], "summary_plus_original")
        self.assertNotEqual(result["notification"]["special_card"], "code")


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(GroundingMatrix)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(f'GROUNDING_MATRIX={result.testsRun - len(result.failures) - len(result.errors)}/{result.testsRun}')
    raise SystemExit(0 if result.wasSuccessful() else 1)
