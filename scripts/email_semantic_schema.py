#!/usr/bin/env python3
"""Strict SemanticDecisionV2 schema and deterministic fallback helpers.

Shadow-only contract. This module has no network, mailbox, delivery, or tool side effects.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any, Dict, Iterable, List, Mapping, Tuple

MARKER = "EMAIL_WATCHDOG_SEMANTIC_SCHEMA_V2"
SCHEMA_VERSION = 2

CATEGORIES = {
    "verification_code": "验证码",
    "account_security": "账户安全",
    "account_status_notice": "账户状态",
    "invoice_receipt": "发票/收据",
    "school_notice": "学校通知",
    "health_check_notice": "健康通知",
    "meeting_event": "会议/活动",
    "task_deadline": "任务/截止",
    "paper_manuscript_feedback": "论文/稿件反馈",
    "academic_opportunity_call": "学术机会/征集",
    "academic_report_digest": "学术报告摘要",
    "academic_alert_digest": "学术快讯",
    "research_feedback_thread": "科研讨论",
    "data_download_order_notice": "数据/下载通知",
    "system_automation_notice": "系统自动化通知",
    "newsletter_marketing": "订阅/营销",
    "delivery_logistics": "物流通知",
    "personal_or_general": "个人/一般邮件",
    "unknown_needs_llm": "待判断",
}
IMPORTANCE_LEVELS = {"low", "normal", "high", "critical"}
CONTENT_MODES = {
    "summary_only",
    "summary_plus_original",
    "original_only",
    "code_card",
    "finance_card",
    "event_card",
    "deadline_card",
}
SUMMARY_STYLES = {"paragraph", "bullets", "none"}
ORIGINAL_POLICIES = {"none", "full", "excerpt"}
SPECIAL_CARDS = {"none", "code", "finance", "event", "deadline"}
ATTACHMENT_POLICIES = {"none", "list_only", "download_safe", "download_all"}
RISK_LEVELS = {"none", "low", "medium", "high", "critical"}
SOURCE_FIELDS = {"subject", "body", "sender", "attachments", "rule_facts", "safety_facts"}

_FORBIDDEN_KEYS = {
    "send", "send_email", "reply", "reply_email", "forward", "delete", "trash",
    "move", "archive", "mark_read", "mark_unread", "click", "open_link",
    "tool", "tool_call", "tools", "execute", "command", "shell", "write_mailbox",
}

_ALLOWED = {
    "root": {
        "schema_version", "message_key", "classification", "importance", "notification",
        "action", "deadline", "attachments", "risk", "reminders", "memory_observation", "evidence",
    },
    "classification": {"category", "label", "confidence"},
    "importance": {"level", "reason"},
    "notification": {
        "should_notify", "content_mode", "summary_style", "summary", "key_points",
        "original_policy", "original_reason", "special_card",
    },
    "action": {"required", "type", "description", "next_step"},
    "deadline": {"has_deadline", "datetime", "date_text", "confidence"},
    "attachments": {"present", "policy", "important_names", "reason"},
    "risk": {"level", "notes"},
    "reminder": {"time", "kind", "message"},
    "memory_observation": {"sender_preference_candidate", "topic_tags", "user_preference_candidate"},
    "evidence": {"source_fields", "uncertainties"},
}


def _text(value: Any, limit: int = 0) -> str:
    if value is None:
        return ""
    text = str(value).replace("\x00", "").strip()
    if limit and len(text) > limit:
        text = text[:limit]
    return text


def _list_of_text(value: Any, limit_items: int, limit_chars: int) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out: List[str] = []
    for item in value[:limit_items]:
        text = _text(item, limit_chars)
        if text and text not in out:
            out.append(text)
    return out


def _bool(value: Any, default: bool = False) -> bool:
    return value if isinstance(value, bool) else default


def _confidence(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(0.0, min(1.0, number))


def _mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _sha(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8", "replace")).hexdigest()


def _unknown_keys(obj: Mapping[str, Any], allowed: Iterable[str], path: str) -> List[str]:
    allowed_set = set(allowed)
    return [f"{path}: unknown field {key}" for key in obj.keys() if key not in allowed_set]


def _scan_forbidden(value: Any, path: str = "root") -> List[str]:
    errors: List[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_norm = str(key).strip().lower()
            if key_norm in _FORBIDDEN_KEYS:
                errors.append(f"{path}: forbidden side-effect field {key}")
            errors.extend(_scan_forbidden(item, f"{path}.{key}"))
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            errors.extend(_scan_forbidden(item, f"{path}[{idx}]"))
    return errors


def _normalize_importance(value: Any) -> str:
    level = _text(value, 24).lower()
    aliases = {"medium": "normal", "urgent": "critical", "none": "normal"}
    return aliases.get(level, level)


def _normalize_category(value: Any) -> str:
    category = _text(value, 80).lower()
    aliases = {
        "paper_feedback": "paper_manuscript_feedback",
        "paper_manuscript": "paper_manuscript_feedback",
        "invoice": "invoice_receipt",
        "receipt": "invoice_receipt",
        "school": "school_notice",
        "meeting": "meeting_event",
        "deadline": "task_deadline",
        "personal": "personal_or_general",
        "unknown": "unknown_needs_llm",
    }
    return aliases.get(category, category)


def _base_decision(message_key: str) -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "message_key": message_key,
        "classification": {"category": "unknown_needs_llm", "label": CATEGORIES["unknown_needs_llm"], "confidence": 0.0},
        "importance": {"level": "normal", "reason": ""},
        "notification": {
            "should_notify": True,
            "content_mode": "summary_only",
            "summary_style": "paragraph",
            "summary": "邮件语义分析结果不可用，请查看原邮件。",
            "key_points": [],
            "original_policy": "none",
            "original_reason": "",
            "special_card": "none",
        },
        "action": {"required": False, "type": "", "description": "", "next_step": ""},
        "deadline": {"has_deadline": False, "datetime": "", "date_text": "", "confidence": 0.0},
        "attachments": {"present": False, "policy": "none", "important_names": [], "reason": ""},
        "risk": {"level": "none", "notes": []},
        "reminders": [],
        "memory_observation": {
            "sender_preference_candidate": None,
            "topic_tags": [],
            "user_preference_candidate": None,
        },
        "evidence": {"source_fields": [], "uncertainties": []},
    }


def normalize_and_validate(
    raw: Any,
    *,
    message_key: str,
    facts: Mapping[str, Any] | None = None,
) -> Tuple[Dict[str, Any] | None, List[str]]:
    """Normalize a model object into SemanticDecisionV2 and enforce strict safety rules."""
    facts = facts or {}
    if not isinstance(raw, Mapping):
        return None, ["root must be an object"]
    source = copy.deepcopy(dict(raw))
    errors = _scan_forbidden(source)
    errors.extend(_unknown_keys(source, _ALLOWED["root"], "root"))
    for required_key in sorted(_ALLOWED["root"] - set(source.keys())):
        errors.append(f"root: missing field {required_key}")

    decision = _base_decision(message_key)
    supplied_key = _text(source.get("message_key"), 512)
    if supplied_key and supplied_key != message_key:
        errors.append("message_key mismatch")
    schema_version = source.get("schema_version", SCHEMA_VERSION)
    if schema_version != SCHEMA_VERSION:
        errors.append("schema_version must equal 2")

    classification = _mapping(source.get("classification"))
    errors.extend(_unknown_keys(classification, _ALLOWED["classification"], "classification"))
    for required_key in sorted(_ALLOWED["classification"] - set(classification.keys())):
        errors.append(f"classification: missing field {required_key}")
    category = _normalize_category(classification.get("category"))
    if category not in CATEGORIES:
        errors.append("classification.category is not canonical")
        category = "unknown_needs_llm"
    decision["classification"] = {
        "category": category,
        "label": _text(classification.get("label"), 80) or CATEGORIES[category],
        "confidence": _confidence(classification.get("confidence"), 0.0),
    }

    importance = _mapping(source.get("importance"))
    errors.extend(_unknown_keys(importance, _ALLOWED["importance"], "importance"))
    for required_key in sorted(_ALLOWED["importance"] - set(importance.keys())):
        errors.append(f"importance: missing field {required_key}")
    level = _normalize_importance(importance.get("level"))
    if level not in IMPORTANCE_LEVELS:
        errors.append("importance.level is invalid")
        level = "normal"
    decision["importance"] = {"level": level, "reason": _text(importance.get("reason"), 300)}

    notification = _mapping(source.get("notification"))
    errors.extend(_unknown_keys(notification, _ALLOWED["notification"], "notification"))
    for required_key in sorted(_ALLOWED["notification"] - set(notification.keys())):
        errors.append(f"notification: missing field {required_key}")
    mode = _text(notification.get("content_mode"), 64).lower() or "summary_only"
    style = _text(notification.get("summary_style"), 32).lower() or "paragraph"
    original_policy = _text(notification.get("original_policy"), 32).lower() or "none"
    special_card = _text(notification.get("special_card"), 32).lower() or "none"
    if mode not in CONTENT_MODES:
        errors.append("notification.content_mode is invalid")
    if style not in SUMMARY_STYLES:
        errors.append("notification.summary_style is invalid")
    if original_policy not in ORIGINAL_POLICIES:
        errors.append("notification.original_policy is invalid")
    if special_card not in SPECIAL_CARDS:
        errors.append("notification.special_card is invalid")
    summary = _text(notification.get("summary"), 900)
    key_points = _list_of_text(notification.get("key_points"), 8, 300)
    if style == "paragraph":
        if not summary:
            errors.append("paragraph requires summary")
        if key_points:
            errors.append("paragraph forbids key_points")
    elif style == "bullets":
        if summary:
            errors.append("bullets forbids summary")
        if not key_points:
            errors.append("bullets requires key_points")
    elif style == "none" and (summary or key_points):
        errors.append("none summary_style forbids summary and key_points")
    if mode == "summary_only" and original_policy != "none":
        errors.append("summary_only requires original_policy=none")
    if mode in {"summary_plus_original", "original_only"} and original_policy not in {"full", "excerpt"}:
        errors.append(f"{mode} requires original_policy=full|excerpt")
    card_map = {
        "code_card": "code",
        "finance_card": "finance",
        "event_card": "event",
        "deadline_card": "deadline",
    }
    expected_card = card_map.get(mode)
    if expected_card and special_card != expected_card:
        errors.append(f"{mode} requires special_card={expected_card}")
    if not expected_card and special_card != "none":
        errors.append("non-card content_mode requires special_card=none")
    decision["notification"] = {
        "should_notify": _bool(notification.get("should_notify"), True),
        "content_mode": mode if mode in CONTENT_MODES else "summary_only",
        "summary_style": style if style in SUMMARY_STYLES else "paragraph",
        "summary": summary,
        "key_points": key_points,
        "original_policy": original_policy if original_policy in ORIGINAL_POLICIES else "none",
        "original_reason": _text(notification.get("original_reason"), 300),
        "special_card": special_card if special_card in SPECIAL_CARDS else "none",
    }

    action = _mapping(source.get("action"))
    errors.extend(_unknown_keys(action, _ALLOWED["action"], "action"))
    for required_key in sorted(_ALLOWED["action"] - set(action.keys())):
        errors.append(f"action: missing field {required_key}")
    action_required = _bool(action.get("required"), False)
    action_type = _text(action.get("type"), 80)
    if re.search(r"(?i)(send|reply|forward|delete|trash|move|archive|mark[_ -]?read|tool|execute|shell|command)", action_type):
        errors.append("action.type requests a forbidden side effect")
    action_description = _text(action.get("description"), 500)
    action_next = _text(action.get("next_step"), 500)
    if action_required and not (action_description or action_next):
        errors.append("required action needs description or next_step")
    if not action_required and (action_type or action_description or action_next):
        errors.append("non-required action must be empty")
    decision["action"] = {
        "required": action_required,
        "type": action_type,
        "description": action_description,
        "next_step": action_next,
    }

    deadline = _mapping(source.get("deadline"))
    errors.extend(_unknown_keys(deadline, _ALLOWED["deadline"], "deadline"))
    for required_key in sorted(_ALLOWED["deadline"] - set(deadline.keys())):
        errors.append(f"deadline: missing field {required_key}")
    has_deadline = _bool(deadline.get("has_deadline"), False)
    deadline_datetime = _text(deadline.get("datetime"), 100)
    deadline_text = _text(deadline.get("date_text"), 160)
    if has_deadline and not (deadline_datetime or deadline_text):
        errors.append("deadline requires datetime or date_text")
    if not has_deadline and (deadline_datetime or deadline_text):
        errors.append("no-deadline object must not contain deadline values")
    decision["deadline"] = {
        "has_deadline": has_deadline,
        "datetime": deadline_datetime,
        "date_text": deadline_text,
        "confidence": _confidence(deadline.get("confidence"), 0.0),
    }

    attachments = _mapping(source.get("attachments"))
    errors.extend(_unknown_keys(attachments, _ALLOWED["attachments"], "attachments"))
    for required_key in sorted(_ALLOWED["attachments"] - set(attachments.keys())):
        errors.append(f"attachments: missing field {required_key}")
    factual_present = bool(facts.get("attachments_present"))
    model_present = _bool(attachments.get("present"), factual_present)
    if model_present != factual_present:
        errors.append("attachments.present conflicts with deterministic facts")
    attachment_policy = _text(attachments.get("policy"), 40).lower() or "none"
    names = _list_of_text(attachments.get("important_names"), 20, 240)
    factual_names = _list_of_text(facts.get("attachment_names"), 20, 240)
    factual_name_set = {name.casefold() for name in factual_names}
    if names and (not factual_name_set or any(name.casefold() not in factual_name_set for name in names)):
        errors.append("attachments.important_names conflicts with deterministic facts")
    if attachment_policy not in ATTACHMENT_POLICIES:
        errors.append("attachments.policy is invalid")
    if not factual_present and (attachment_policy != "none" or names):
        errors.append("absent attachments require none/empty")
    decision["attachments"] = {
        "present": factual_present,
        "policy": attachment_policy if factual_present and attachment_policy in ATTACHMENT_POLICIES else "none",
        "important_names": names if factual_present else [],
        "reason": _text(attachments.get("reason"), 300) if factual_present else "",
    }

    risk = _mapping(source.get("risk"))
    errors.extend(_unknown_keys(risk, _ALLOWED["risk"], "risk"))
    for required_key in sorted(_ALLOWED["risk"] - set(risk.keys())):
        errors.append(f"risk: missing field {required_key}")
    risk_level = _text(risk.get("level"), 32).lower() or "none"
    notes = _list_of_text(risk.get("notes"), 8, 300)
    if risk_level not in RISK_LEVELS:
        errors.append("risk.level is invalid")
    if risk_level == "none" and notes:
        errors.append("risk none requires empty notes")
    decision["risk"] = {"level": risk_level if risk_level in RISK_LEVELS else "none", "notes": notes}

    reminders_value = source.get("reminders") or []
    if not isinstance(reminders_value, list):
        errors.append("reminders must be a list")
        reminders_value = []
    reminders: List[Dict[str, str]] = []
    for idx, reminder_raw in enumerate(reminders_value[:8]):
        reminder = _mapping(reminder_raw)
        errors.extend(_unknown_keys(reminder, _ALLOWED["reminder"], f"reminders[{idx}]"))
        item = {
            "time": _text(reminder.get("time"), 100),
            "kind": _text(reminder.get("kind"), 80),
            "message": _text(reminder.get("message"), 300),
        }
        if not item["time"] or not item["message"]:
            errors.append(f"reminders[{idx}] requires time and message")
        reminders.append(item)
    decision["reminders"] = reminders

    memory = _mapping(source.get("memory_observation"))
    errors.extend(_unknown_keys(memory, _ALLOWED["memory_observation"], "memory_observation"))
    for required_key in sorted(_ALLOWED["memory_observation"] - set(memory.keys())):
        errors.append(f"memory_observation: missing field {required_key}")
    sender_candidate = memory.get("sender_preference_candidate")
    user_candidate = memory.get("user_preference_candidate")
    if sender_candidate is not None and not isinstance(sender_candidate, (str, Mapping)):
        errors.append("sender_preference_candidate must be null, string, or object")
    if user_candidate is not None and not isinstance(user_candidate, (str, Mapping)):
        errors.append("user_preference_candidate must be null, string, or object")
    decision["memory_observation"] = {
        "sender_preference_candidate": sender_candidate,
        "topic_tags": _list_of_text(memory.get("topic_tags"), 12, 80),
        "user_preference_candidate": user_candidate,
    }

    evidence = _mapping(source.get("evidence"))
    errors.extend(_unknown_keys(evidence, _ALLOWED["evidence"], "evidence"))
    for required_key in sorted(_ALLOWED["evidence"] - set(evidence.keys())):
        errors.append(f"evidence: missing field {required_key}")
    source_fields = _list_of_text(evidence.get("source_fields"), 12, 40)
    invalid_sources = [field for field in source_fields if field not in SOURCE_FIELDS]
    if invalid_sources:
        errors.append("evidence.source_fields contains invalid values")
    decision["evidence"] = {
        "source_fields": [field for field in source_fields if field in SOURCE_FIELDS],
        "uncertainties": _list_of_text(evidence.get("uncertainties"), 8, 300),
    }

    if errors:
        return None, errors
    return decision, []


def summary_text(decision: Mapping[str, Any]) -> str:
    notification = _mapping(decision.get("notification"))
    style = notification.get("summary_style")
    if style == "paragraph":
        return _text(notification.get("summary"), 900)
    if style == "bullets":
        return "\n".join(_list_of_text(notification.get("key_points"), 8, 300))
    return ""


def summary_hash(decision: Mapping[str, Any]) -> str:
    return _sha(summary_text(decision))


def conservative_fallback(
    *,
    message_key: str,
    email: Mapping[str, Any] | None = None,
    rule_result: Mapping[str, Any] | None = None,
    analysis: Mapping[str, Any] | None = None,
    facts: Mapping[str, Any] | None = None,
    reason: str = "semantic_engine_fallback",
) -> Dict[str, Any]:
    """Create a valid conservative decision without reconstructing or storing the body."""
    email = email or {}
    rule_result = rule_result or {}
    analysis = analysis or {}
    facts = facts or {}
    decision = _base_decision(message_key)

    category = _normalize_category(
        rule_result.get("semantic_category")
        or rule_result.get("canonical_category")
        or analysis.get("semantic_category")
        or rule_result.get("category")
    )
    legacy_map = {
        "验证码": "verification_code",
        "账户安全": "account_security",
        "学校通知": "school_notice",
        "发票/收据": "invoice_receipt",
        "个人邮件": "personal_or_general",
        "广告": "newsletter_marketing",
        "物流": "delivery_logistics",
    }
    category = legacy_map.get(_text(rule_result.get("category"), 80), category)
    if category not in CATEGORIES:
        category = "unknown_needs_llm"

    importance = _normalize_importance(
        analysis.get("user_relevance") or rule_result.get("priority") or "normal"
    )
    if importance not in IMPORTANCE_LEVELS:
        importance = "normal"

    subject = re.sub(r"\s+", " ", _text(email.get("subject"), 300)).strip()
    code_candidates = [
        _text(item, 16)
        for item in (facts.get("code_candidates") or [])
        if re.fullmatch(r"\d{4,8}", _text(item, 16))
    ][:8]
    hints = facts.get("semantic_hints") if isinstance(facts.get("semantic_hints"), Mapping) else {}
    trusted_legacy_simple_code = bool(
        category == "verification_code"
        and _text(rule_result.get("action"), 40) == "simple_code"
    )
    is_code = bool(
        code_candidates
        and (hints.get("verification_code_phrase") or trusted_legacy_simple_code)
    )
    academic_subject = bool(hints.get("academic_report_subject_phrase"))
    if category == "verification_code" and not is_code:
        category = "academic_report_digest" if academic_subject else "unknown_needs_llm"
    if academic_subject and not is_code and category in {
        "invoice_receipt", "newsletter_marketing", "personal_or_general",
        "system_automation_notice", "unknown_needs_llm",
    }:
        category = "academic_report_digest"
    should_notify = analysis.get("should_notify") is not False and rule_result.get("action") != "skip"
    if category == "academic_report_digest":
        should_notify = True
        importance = "normal"

    decision["classification"] = {
        "category": "verification_code" if is_code else category,
        "label": CATEGORIES["verification_code" if is_code else category],
        "confidence": 0.35,
    }
    decision["importance"] = {
        "level": "high" if is_code else importance,
        "reason": "确定性降级结果；未使用模型语义结果。",
    }
    summary = "收到验证码邮件，请核对来源后使用。" if is_code else (
        f"已收到学术研究周报“{subject}”，请查看原文节选。"
        if category == "academic_report_digest" and subject
        else (
            f"邮件“{subject}”的语义分析暂不可用，请查看原邮件。"
            if subject else "邮件语义分析暂不可用，请查看原邮件。"
        )
    )
    decision["notification"] = {
        "should_notify": should_notify,
        "content_mode": (
            "code_card" if is_code
            else "summary_plus_original" if category == "academic_report_digest"
            else "summary_only"
        ),
        "summary_style": "paragraph",
        "summary": summary,
        "key_points": [],
        "original_policy": (
            "excerpt" if category == "academic_report_digest" and not is_code
            else "none"
        ),
        "original_reason": (
            "学术周报的具体内容需要保留原文上下文。"
            if category == "academic_report_digest" and not is_code
            else ""
        ),
        "special_card": "code" if is_code else "none",
    }

    action_raw = analysis.get("action_needed") if isinstance(analysis.get("action_needed"), Mapping) else {}
    action_required = bool(action_raw.get("required"))
    decision["action"] = {
        "required": action_required,
        "type": _text(action_raw.get("type"), 80) if action_required else "",
        "description": _text(action_raw.get("description"), 500) if action_required else "",
        "next_step": _text(action_raw.get("next_step"), 500) if action_required else "",
    }
    deadline_raw = analysis.get("deadline") if isinstance(analysis.get("deadline"), Mapping) else {}
    has_deadline = bool(deadline_raw.get("has_deadline"))
    decision["deadline"] = {
        "has_deadline": has_deadline,
        "datetime": _text(deadline_raw.get("datetime"), 100) if has_deadline else "",
        "date_text": _text(deadline_raw.get("date_text"), 160) if has_deadline else "",
        "confidence": _confidence(deadline_raw.get("confidence"), 0.0) if has_deadline else 0.0,
    }
    attachments_present = bool(facts.get("attachments_present"))
    decision["attachments"] = {
        "present": attachments_present,
        "policy": "list_only" if attachments_present else "none",
        "important_names": [],
        "reason": "附件事实来自确定性解析。" if attachments_present else "",
    }
    risk_notes = _list_of_text(analysis.get("risk_notes"), 8, 300)
    risk_level = "high" if category == "account_security" else ("medium" if risk_notes else "none")
    decision["risk"] = {"level": risk_level, "notes": risk_notes}
    decision["evidence"] = {
        "source_fields": ["subject", "attachments"] if attachments_present else ["subject"],
        "uncertainties": [_text(reason, 300) or "semantic_engine_fallback"],
    }
    return decision


def public_schema() -> Dict[str, Any]:
    """Return a compact machine-readable contract for diagnostics and tests."""
    return {
        "marker": MARKER,
        "schema_version": SCHEMA_VERSION,
        "categories": sorted(CATEGORIES),
        "importance_levels": sorted(IMPORTANCE_LEVELS),
        "content_modes": sorted(CONTENT_MODES),
        "summary_styles": sorted(SUMMARY_STYLES),
        "original_policies": sorted(ORIGINAL_POLICIES),
        "special_cards": sorted(SPECIAL_CARDS),
    }


if __name__ == "__main__":
    print(json.dumps(public_schema(), ensure_ascii=False, indent=2, sort_keys=True))
