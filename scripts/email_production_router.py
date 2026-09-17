#!/usr/bin/env python3
"""Production routing policy for Hermes Email Watchdog.

This module is deterministic except for the durable semantic call made by
``email_delivery``. It never accesses a mailbox, outbox, or Weixin directly.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Mapping

import email_config
import email_feature_extractor
import email_semantic_engine
import email_semantic_schema

MARKER = "EMAIL_WATCHDOG_PRODUCTION_ROUTER_V1"

_CODE_RE = re.compile(
    r"(?i)(验证码|校验码|动态口令|一次性密码|登录码|安全码|认证码|短信码|"
    r"verification code|one[- ]time password|security code|authentication code|"
    r"auth code|passcode|\botp\b|2fa)"
)
_SECURITY_RE = re.compile(
    r"(?i)(可疑登录|异常登录|未经授权|账户被锁|账号被锁|账户入侵|密码重置|"
    r"立即修改密码|suspicious login|unauthorized|account locked|account compromise|"
    r"password reset|security alert)"
)
_URGENCY_RE = re.compile(
    r"(?i)(立即|马上|尽快|今天|今日|今晚|分钟后|小时后|有效期\s*\d+\s*分钟|"
    r"immediately|urgent|within\s+\d+\s+(?:minutes?|hours?)|"
    r"in\s+\d+\s+(?:minutes?|hours?)|expires?\s+in)"
)
_MEETING_RE = re.compile(r"(?i)(会议|例会|活动|讲座|面试|meeting|event|webinar|interview)")
_DEADLINE_RE = re.compile(r"(?i)(截止|提交|完成|回复|确认|deadline|due|submit|respond|complete)")


def _text(value: Any, limit: int = 0) -> str:
    out = "" if value is None else str(value).replace("\\x00", "").strip()
    if limit and len(out) > limit:
        out = out[: max(0, limit - 1)].rstrip() + "…"
    return out


def settings() -> Dict[str, Any]:
    cfg = dict(email_config.get_notification_settings() or {})
    return {
        "production_route_enabled": bool(cfg.get("production_route_enabled", False)),
        "all_mail_push": bool(cfg.get("all_mail_push", True)),
        "legacy_fallback_enabled": bool(cfg.get("legacy_fallback_enabled", True)),
        "fast_lane_enabled": bool(cfg.get("fast_lane_enabled", True)),
        "renderer": str(cfg.get("renderer") or "adaptive_v1e"),
        "mode": str(cfg.get("mode") or "shadow"),
    }


def production_enabled() -> bool:
    cfg = settings()
    return bool(cfg["production_route_enabled"] and cfg["mode"] == "production")


def extract_features(email: Mapping[str, Any]) -> Dict[str, Any]:
    return dict(email_feature_extractor.extract_features(dict(email or {})) or {})


def classify_fast_lane(email: Mapping[str, Any], features: Mapping[str, Any]) -> Dict[str, Any]:
    cfg = settings()
    if not cfg.get("fast_lane_enabled"):
        return {"fast_lane": False, "kind": "", "reasons": []}
    subject = _text(email.get("subject"), 800)
    body = _text(email.get("body"), 6000)
    scope = subject + "\n" + body
    hints = features.get("semantic_hints") if isinstance(features.get("semantic_hints"), Mapping) else {}
    candidates = [
        _text(item, 16) for item in list(features.get("code_candidates") or [])
        if re.fullmatch(r"\d{4,8}", _text(item, 16))
    ]
    if candidates and (_CODE_RE.search(scope) or hints.get("verification_code_phrase")):
        return {"fast_lane": True, "kind": "verification_code", "reasons": ["grounded_verification_code"], "code": candidates[0]}
    if _SECURITY_RE.search(scope) and _URGENCY_RE.search(scope):
        return {"fast_lane": True, "kind": "account_security", "reasons": ["urgent_account_security"]}
    if _URGENCY_RE.search(scope) and _MEETING_RE.search(scope):
        return {"fast_lane": True, "kind": "meeting_event", "reasons": ["explicit_near_term_meeting"]}
    if _URGENCY_RE.search(scope) and _DEADLINE_RE.search(scope):
        return {"fast_lane": True, "kind": "task_deadline", "reasons": ["explicit_near_term_deadline"]}
    return {"fast_lane": False, "kind": "", "reasons": []}


def _base_fast_decision(email: Mapping[str, Any], rule_result: Mapping[str, Any], analysis: Mapping[str, Any], features: Mapping[str, Any]) -> Dict[str, Any]:
    facts = email_semantic_engine._facts(email or {}, features or {})
    message_key = str(features.get("message_key") or "")
    decision = email_semantic_schema.conservative_fallback(
        message_key=message_key,
        email=email,
        rule_result=rule_result,
        analysis=analysis,
        facts=facts,
        reason="deterministic_fast_lane",
    )
    return decision


def build_fast_decision(email: Mapping[str, Any], rule_result: Mapping[str, Any], analysis: Mapping[str, Any], features: Mapping[str, Any], lane: Mapping[str, Any]) -> Dict[str, Any]:
    kind = str(lane.get("kind") or "")
    subject = _text(email.get("subject"), 260) or "无主题"
    body = _text(email.get("body"), 1200)
    decision = _base_fast_decision(email, rule_result, analysis, features)
    label = email_semantic_schema.CATEGORIES.get(kind, kind)
    decision["classification"] = {"category": kind, "label": label, "confidence": 0.99}
    decision["importance"] = {"level": "critical" if kind == "account_security" else "high", "reason": "确定性及时性通道。"}
    decision["notification"]["should_notify"] = True
    decision["evidence"]["uncertainties"] = []
    decision["evidence"]["source_fields"] = ["subject", "body"]

    if kind == "verification_code":
        decision["notification"].update({
            "content_mode": "code_card", "summary_style": "paragraph",
            "summary": "收到验证码邮件，请核对来源后使用。", "key_points": [],
            "original_policy": "none", "original_reason": "", "special_card": "code",
        })
        decision["action"] = {"required": False, "type": "", "description": "", "next_step": ""}
        decision["deadline"] = {"has_deadline": False, "datetime": "", "date_text": "", "confidence": 0.0}
        decision["risk"] = {"level": "none", "notes": []}
    elif kind == "account_security":
        decision["notification"].update({
            "content_mode": "summary_plus_original", "summary_style": "paragraph",
            "summary": f"账户安全提醒：{subject}", "key_points": [],
            "original_policy": "excerpt", "original_reason": "需要保留安全通知原文。", "special_card": "none",
        })
        decision["action"] = {"required": True, "type": "review_security", "description": "请立即核对该账户安全事件是否由本人触发。", "next_step": "如非本人操作，立即修改密码并检查登录设备。"}
        decision["deadline"] = {"has_deadline": False, "datetime": "", "date_text": "", "confidence": 0.0}
        decision["risk"] = {"level": "high", "notes": ["该邮件包含明确的紧急账户安全信号。"]}
    elif kind == "meeting_event":
        decision["notification"].update({
            "content_mode": "event_card", "summary_style": "paragraph",
            "summary": subject, "key_points": [], "original_policy": "none",
            "original_reason": "", "special_card": "event",
        })
        decision["action"] = {"required": True, "type": "attend", "description": "该会议或活动即将开始。", "next_step": "立即查看原邮件中的时间、地点或会议链接。"}
        decision["deadline"] = {"has_deadline": True, "datetime": "", "date_text": _text(body or subject, 180), "confidence": 0.9}
        decision["risk"] = {"level": "none", "notes": []}
    elif kind == "task_deadline":
        decision["notification"].update({
            "content_mode": "deadline_card", "summary_style": "paragraph",
            "summary": subject, "key_points": [], "original_policy": "none",
            "original_reason": "", "special_card": "deadline",
        })
        decision["action"] = {"required": True, "type": "complete_task", "description": "该事项存在明确的临近处理要求。", "next_step": "立即查看原邮件并完成要求。"}
        decision["deadline"] = {"has_deadline": True, "datetime": "", "date_text": _text(body or subject, 180), "confidence": 0.9}
        decision["risk"] = {"level": "none", "notes": []}
    validated, errors = email_semantic_schema.normalize_and_validate(
        decision,
        message_key=str(features.get("message_key") or ""),
        facts=email_semantic_engine._facts(email or {}, features or {}),
    )
    if errors or validated is None:
        raise RuntimeError("fast decision schema invalid: " + "; ".join(errors[:6]))
    return validated


def decision_to_legacy_analysis(decision: Mapping[str, Any], original: Mapping[str, Any] | None = None) -> Dict[str, Any]:
    original = dict(original or {})
    classification = decision.get("classification") if isinstance(decision.get("classification"), Mapping) else {}
    importance = decision.get("importance") if isinstance(decision.get("importance"), Mapping) else {}
    notification = decision.get("notification") if isinstance(decision.get("notification"), Mapping) else {}
    action = decision.get("action") if isinstance(decision.get("action"), Mapping) else {}
    deadline = decision.get("deadline") if isinstance(decision.get("deadline"), Mapping) else {}
    attachments = decision.get("attachments") if isinstance(decision.get("attachments"), Mapping) else {}
    risk = decision.get("risk") if isinstance(decision.get("risk"), Mapping) else {}
    summary = _text(notification.get("summary"), 1600)
    if not summary:
        summary = "\n".join(f"- {_text(item, 300)}" for item in list(notification.get("key_points") or [])[:6] if _text(item))
    result = dict(original)
    result.update({
        "semantic_category": classification.get("category") or "unknown_needs_llm",
        "final_category": classification.get("category") or "unknown_needs_llm",
        "user_relevance": importance.get("level") or "normal",
        "confidence": classification.get("confidence") or 0.0,
        "should_notify": True,
        "format_decision": notification.get("content_mode") or "summary_only",
        "formatted_summary": summary,
        "action_needed": {
            "required": bool(action.get("required")), "type": _text(action.get("type"), 100),
            "description": _text(action.get("description"), 500), "next_step": _text(action.get("next_step"), 500),
        },
        "deadline": {
            "has_deadline": bool(deadline.get("has_deadline")), "datetime": _text(deadline.get("datetime"), 120),
            "date_text": _text(deadline.get("date_text"), 200), "timezone": "Asia/Shanghai",
            "confidence": deadline.get("confidence") or 0.0,
        },
        "reminder_schedule": [],
        "attachment_handling": {
            "policy": "list_only" if attachments.get("present") else "none",
            "wanted_types": [], "reason": _text(attachments.get("reason"), 300),
        },
        "body_rendering": {"header_lines": [], "body_sections": [], "signature": None},
        "risk_notes": list(risk.get("notes") or [])[:8],
        "production_semantic_route": True,
    })
    return result


def legacy_fallback_analysis(email: Mapping[str, Any], rule_result: Mapping[str, Any], original: Mapping[str, Any] | None, reason: str) -> Dict[str, Any]:
    original = dict(original or {})
    original["should_notify"] = True
    original.setdefault("semantic_category", rule_result.get("category") or "unknown_needs_llm")
    original.setdefault("user_relevance", "normal")
    original.setdefault("format_decision", "legacy_fallback")
    original.setdefault("formatted_summary", _text(email.get("subject"), 500) or "收到新邮件，请查看原文。")
    original.setdefault("action_needed", {"required": False, "description": None, "type": "none", "next_step": None})
    original.setdefault("deadline", {"has_deadline": False, "datetime": None, "date_text": None, "timezone": "Asia/Shanghai", "confidence": 0})
    original.setdefault("reminder_schedule", [])
    original.setdefault("attachment_handling", {"policy": "list_only" if email.get("has_attachments") or email.get("has_attachment") else "none", "wanted_types": [], "reason": "legacy fallback"})
    original.setdefault("body_rendering", {"header_lines": [], "body_sections": [], "signature": None})
    original.setdefault("risk_notes", [])
    original["production_fallback_reason"] = _text(reason, 500)
    return original
