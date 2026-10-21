#!/usr/bin/env python3
"""Production routing policy for Hermes Email Watchdog.

This module is deterministic except for the durable semantic call made by
``email_delivery``. It never accesses a mailbox, outbox, or Weixin directly.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping

import email_config

MARKER = "EMAIL_WATCHDOG_PRODUCTION_ROUTER_V1"


def _text(value: Any, limit: int = 0) -> str:
    out = "" if value is None else str(value).replace("\\x00", "").strip()
    if limit and len(out) > limit:
        out = out[: max(0, limit - 1)].rstrip() + "…"
    return out


def settings() -> Dict[str, Any]:
    cfg = dict(email_config.get_notification_settings() or {})
    return {
        "production_route_enabled": bool(cfg.get("production_route_enabled", False)),
        "all_mail_push": bool(cfg.get("all_mail_push", False)),
        "legacy_fallback_enabled": bool(cfg.get("legacy_fallback_enabled", True)),
        "renderer": str(cfg.get("renderer") or "intelligent_v2"),
        "mode": str(cfg.get("mode") or "shadow"),
    }


def production_enabled() -> bool:
    cfg = settings()
    return bool(cfg["production_route_enabled"] and cfg["mode"] == "production")


def decision_to_legacy_analysis(decision: Mapping[str, Any], original: Mapping[str, Any] | None = None) -> Dict[str, Any]:
    original = dict(original or {})
    classification = decision.get("classification") if isinstance(decision.get("classification"), Mapping) else {}
    importance = decision.get("importance") if isinstance(decision.get("importance"), Mapping) else {}
    notification = decision.get("notification") if isinstance(decision.get("notification"), Mapping) else {}
    action = decision.get("action") if isinstance(decision.get("action"), Mapping) else {}
    deadline = decision.get("deadline") if isinstance(decision.get("deadline"), Mapping) else {}
    deadlines_raw = decision.get("deadlines") if isinstance(decision.get("deadlines"), list) else []
    deadlines_list = [
        {
            "datetime": _text(item.get("datetime"), 120),
            "date_text": _text(item.get("date_text"), 200),
            "label": _text(item.get("label"), 120),
            "venue": _text(item.get("venue"), 120),
            "confidence": item.get("confidence") or 0.0,
        }
        for item in deadlines_raw
        if isinstance(item, Mapping) and (_text(item.get("datetime"), 120) or _text(item.get("date_text"), 200))
    ]
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
        "should_notify": should_push_notification(decision),
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
        "deadlines": deadlines_list,
        "reminder_schedule": [],
        "attachment_handling": {
            "policy": (
                _text(attachments.get("policy"), 40)
                if attachments.get("present")
                else "none"
            ) or ("list_only" if attachments.get("present") else "none"),
            "wanted_types": [], "reason": _text(attachments.get("reason"), 300),
        },
        "body_rendering": {"header_lines": [], "body_sections": [], "signature": None},
        "risk_notes": list(risk.get("notes") or [])[:8],
        "production_semantic_route": True,
    })
    return result


def should_push_notification(decision: Mapping[str, Any]) -> bool:
    """Return the production push decision; silent mail remains persisted for audit/digest."""
    cfg = settings()
    if cfg.get("all_mail_push"):
        return True
    classification = decision.get("classification") if isinstance(decision.get("classification"), Mapping) else {}
    importance = decision.get("importance") if isinstance(decision.get("importance"), Mapping) else {}
    notification = decision.get("notification") if isinstance(decision.get("notification"), Mapping) else {}
    if notification.get("editorial_reviewed") is True:
        return bool(notification.get("should_notify", True))
    action = decision.get("action") if isinstance(decision.get("action"), Mapping) else {}
    deadline = decision.get("deadline") if isinstance(decision.get("deadline"), Mapping) else {}
    risk = decision.get("risk") if isinstance(decision.get("risk"), Mapping) else {}
    category = _text(classification.get("category"), 80).lower()
    attachment_info = decision.get("attachments") if isinstance(decision.get("attachments"), Mapping) else {}
    if category in {
        "verification_code", "account_security", "account_status_notice", "invoice_receipt",
    }:
        return True
    if category == "data_download_order_notice":
        return bool(notification.get("should_notify", True))
    if bool(attachment_info.get("present")) and _text(attachment_info.get("policy"), 40) in {
        "download_safe", "download_all",
    }:
        return True
    if category in {"academic_report_digest", "academic_alert_digest"}:
        return bool(notification.get("should_notify", True))
    # Personal correspondence is not advertising.  If the semantic model has
    # explicitly chosen to notify, preserve that human communication even when
    # it has no formal task/deadline and is naturally low priority.
    if category == "personal_or_general":
        return bool(notification.get("should_notify", True))
    if category in {"school_notice", "research_feedback_thread", "paper_manuscript_feedback"}:
        return bool(notification.get("should_notify", True))
    if _text(importance.get("level"), 32).lower() in {"high", "critical", "urgent"}:
        return True
    if bool(action.get("required")) or bool(deadline.get("has_deadline")):
        return True
    if _text(risk.get("level"), 32).lower() in {"high", "critical"}:
        return True
    return False


def legacy_fallback_analysis(email: Mapping[str, Any], rule_result: Mapping[str, Any], original: Mapping[str, Any] | None, reason: str) -> Dict[str, Any]:
    original = dict(original or {})
    priority = _text(rule_result.get("priority"), 32).lower()
    category = _text(rule_result.get("category"), 80).lower()
    original["should_notify"] = bool(
        priority in {"urgent", "high", "critical"}
        or category == "academic_report_digest"
        or rule_result.get("action") in {"simple_code", "push_urgent"}
    )
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
