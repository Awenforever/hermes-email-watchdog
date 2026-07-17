#!/usr/bin/env python3
"""Unified Ollama-first semantic engine for Hermes Email Watchdog.

Phase 1 is shadow-only. The engine may persist validated semantic observations, but
must never change production notification text, mailbox state, outbox state, or
candidate/rule tables.
"""
from __future__ import annotations

import ast
import hashlib
import importlib
import json
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

try:
    import email_config
    import email_feature_extractor
    import email_semantic_schema
    import email_semantic_core
except Exception:  # Import safety: delivery wrapper catches and logs failures.
    email_config = None
    email_feature_extractor = None
    email_semantic_schema = None
    email_semantic_core = None

MARKER = "EMAIL_WATCHDOG_SEMANTIC_ENGINE_READABLE_GROUNDED_CORE_ADAPTIVE_OUTPUT_BUDGET_SHADOW_V1"
PROMPT_VERSION = "semantic_v2_readable_grounded_core_v1u_20260713"
DEFAULT_DB_PATH = Path(
    os.environ.get(
        "EMAIL_LEARNING_DB",
        "/opt/data/.hermes-home/.hermes/email_learning/email_learning.sqlite",
    )
)

_CALL_LOCK = threading.Lock()


class SemanticEngineError(RuntimeError):
    pass


class SemanticEngineTimeout(SemanticEngineError):
    pass


class SemanticEngineTruncated(SemanticEngineError):
    def __init__(self, message: str, metrics: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.metrics = dict(metrics or {})


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8", "replace")).hexdigest()


def _safe_error(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}".replace("\n", " ").replace("\r", " ")
    return text[:500]


def _settings(override: Mapping[str, Any] | None = None) -> Dict[str, Any]:
    defaults = {
        "enabled": True,
        "mode": "shadow",
        "provider": "ollama",
        "endpoint": "http://127.0.0.1:11434",
        "model": "qwen2.5:3b",
        "timeout_seconds": 300,
        "temperature": 0.1,
        "max_body_chars": 12000,
        "max_parallel": 1,
        "cache_by_message_hash": True,
        "protocol": "readable_grounded_core_v1u",
        "num_thread": 5,
        "num_predict_mode": "adaptive",
        "num_predict": 1800,
        "num_predict_simple": 600,
        "num_predict_standard": 1000,
        "num_predict_complex": 1600,
        "num_predict_hard_cap": 1800,
    }
    if email_config is not None and hasattr(email_config, "get_semantic_engine_settings"):
        try:
            defaults.update(email_config.get_semantic_engine_settings() or {})
        except Exception:
            pass
    if override:
        defaults.update(dict(override))
    defaults["enabled"] = bool(defaults.get("enabled", True))
    defaults["mode"] = str(defaults.get("mode") or "shadow").strip().lower()
    defaults["provider"] = str(defaults.get("provider") or "ollama").strip().lower()
    defaults["endpoint"] = str(defaults.get("endpoint") or "http://127.0.0.1:11434").rstrip("/")
    defaults["model"] = str(defaults.get("model") or "qwen2.5:3b")
    defaults["timeout_seconds"] = max(1, min(1800, int(defaults.get("timeout_seconds") or 300)))
    defaults["temperature"] = max(0.0, min(1.0, float(defaults.get("temperature", 0.1))))
    defaults["max_body_chars"] = max(1000, min(50000, int(defaults.get("max_body_chars") or 12000)))
    defaults["max_parallel"] = max(1, min(4, int(defaults.get("max_parallel") or 1)))
    defaults["cache_by_message_hash"] = bool(defaults.get("cache_by_message_hash", True))
    defaults["protocol"] = str(defaults.get("protocol") or "readable_grounded_core_v1u").strip().lower()
    defaults["num_thread"] = max(1, min(32, int(defaults.get("num_thread") or 5)))
    defaults["num_predict_mode"] = str(defaults.get("num_predict_mode") or "adaptive").strip().lower()
    if defaults["num_predict_mode"] not in {"adaptive", "fixed"}:
        defaults["num_predict_mode"] = "adaptive"
    defaults["num_predict"] = max(256, min(2400, int(defaults.get("num_predict") or 1800)))
    defaults["num_predict_simple"] = max(384, min(1000, int(defaults.get("num_predict_simple") or 600)))
    defaults["num_predict_standard"] = max(
        defaults["num_predict_simple"],
        min(1600, int(defaults.get("num_predict_standard") or 1000)),
    )
    defaults["num_predict_complex"] = max(
        defaults["num_predict_standard"],
        min(2200, int(defaults.get("num_predict_complex") or 1600)),
    )
    defaults["num_predict_hard_cap"] = max(
        384,
        min(2400, int(defaults.get("num_predict_hard_cap") or defaults["num_predict"])),
    )
    return defaults


def _attachment_names(email: Mapping[str, Any]) -> list[str]:
    raw = email.get("attachments") or email.get("attachment_list") or []
    names: list[str] = []
    if isinstance(raw, list):
        for item in raw[:20]:
            if isinstance(item, Mapping):
                value = item.get("filename") or item.get("name") or item.get("path")
            else:
                value = item
            text = str(value or "").strip()
            if text and text not in names:
                names.append(text[:240])
    return names


def _semantic_hints(email: Mapping[str, Any], subject: str, body: str, features: Mapping[str, Any]) -> Dict[str, bool]:
    sender_name = str(email.get("from_name") or email.get("sender_name") or "")
    sender_domain = str(features.get("sender_domain") or "")
    combined = f"{subject}\n{body}"
    sender_context = f"{sender_name}\n{sender_domain}\n{subject}"
    def has(pattern: str, text: str = combined) -> bool:
        return bool(re.search(pattern, text, re.I | re.S))
    academic_subject_pattern = (
        r"学术研究周报|学术周报|研究周报|文献周报|论文周报|学术报告摘要|"
        r"研究报告摘要|文献综述|论文综述|"
        r"academic research weekly report|research weekly digest|literature weekly digest|"
        r"scholarly weekly report|paper digest|literature digest"
    )
    marketing_subject_pattern = (
        r"promotional offers?|discount(?: updates?)?|coupon|sale|special offer|buy now|"
        r"促销(?:活动|信息|更新)?|营销邮件|优惠(?:活动|信息|更新)?|折扣(?:活动|信息|更新)?|"
        r"限时(?:优惠|折扣|活动)?|满减|领券|特价|立即购买"
    )
    system_test_subject_pattern = (
        r"\be2e\b|end[- ]to[- ]end|outbox|watchdog|pipeline validation|"
        r"系统测试|端到端测试|链路测试|状态检查"
    )
    return {
        "no_action_phrase": has(r"no action (?:is )?required|无需操作|无需回复|不需要操作|仅供参考"),
        "no_deadline_phrase": has(
            r"no (?:immediate )?deadline|without (?:an? )?deadline|deadline is not imposed|"
            r"暂无截止|没有截止|无截止|不设截止|未规定截止"
        ),
        "account_security_phrase": has(
            r"可疑(?:账户)?登录|异常登录|陌生设备|未授权(?:登录|访问)|不是你本人|"
            r"立即修改密码|账户安全(?:警报|提醒)|security alert|suspicious (?:login|activity)|"
            r"unusual (?:login|sign[- ]?in)|unauthori[sz]ed (?:login|access)|"
            r"new device (?:login|sign[- ]?in)|change your password immediately"
        ),
        "health_check_phrase": has(
            r"健康体检|年度体检|体检预约|体检通知|健康检查|"
            r"health check|medical checkup|annual physical|physical examination"
        ),
        "manuscript_feedback_phrase": has(
            r"(?:manuscript|paper|submission).{0,40}(?:revision comments?|review comments?|"
            r"editor(?:ial)? comments?|revisions?|major revision|minor revision)|"
            r"(?:revision comments?|review comments?|editor(?:ial)? comments?).{0,40}(?:manuscript|paper|submission)|"
            r"稿件.{0,30}(?:修改意见|审稿意见|返修意见|编辑意见)|"
            r"论文.{0,30}(?:修改意见|审稿意见|返修意见|编辑意见)"
        ),
        "research_feedback_phrase": has(
            r"(?:实验方案|研究方案|研究计划|研究方法|课题方案|项目方案).{0,40}(?:反馈|建议|意见)|"
            r"(?:反馈|建议|意见).{0,40}(?:实验方案|研究方案|研究计划|研究方法|课题方案|项目方案)|"
            r"(?:research|experiment|methodology|proposal|project plan).{0,50}(?:feedback|suggestions?|comments?)|"
            r"(?:feedback|suggestions?|comments?).{0,50}(?:research|experiment|methodology|proposal|project plan)"
        ),
        "direct_request_phrase": has(
            r"\bplease\b|\bkindly\b|\bsubmit\b|\bupload\b|\bconfirm\b|"
            r"\bcomplete\b|\bsign\b|请于|请在|请尽快|请务必|务必|须于|"
            r"需要.{0,12}(?:提交|上传|确认|完成|签字|填写|参加)|"
            r"(?:提交|上传|确认|完成|签字|填写).{0,8}(?:前|截止)"
        ),
        "deadline_phrase": has(
            r"deadline|due by|截止|不晚于|请于.{0,24}前|须于.{0,24}前|"
            r"(?:19|20)\d{2}年\d{1,2}月\d{1,2}日.{0,12}(?:前|截止)"
        ),
        "receipt_phrase": has(r"收据|发票|receipt|invoice|payment successful|付款成功|支付成功|账单"),
        "school_institution_phrase": bool(
            re.search(r"(?:edu\.cn|ac\.cn)$", sender_domain, re.I)
            or re.search(r"研究生院|学院|学校|教务|学位|培养|中期检查|graduate school|university|college", sender_context, re.I)
        ),
        "event_phrase": has(r"会议|周会|讲座|研讨会|活动|会议室|会议链接|meeting|seminar|webinar|conference|appointment"),
        "system_test_phrase": has(r"e2e|end[- ]to[- ]end|测试|test|validation|outbox|watchdog|monitoring|pipeline|状态检查"),
        "academic_report_subject_phrase": has(academic_subject_pattern, subject),
        "marketing_subject_phrase": has(marketing_subject_pattern, subject),
        "system_test_subject_phrase": has(system_test_subject_pattern, subject),
        "academic_report_phrase": bool(
            has(
                r"学术研究周报|学术周报|研究周报|文献周报|论文周报|学术报告摘要|"
                r"研究报告摘要|文献综述|论文综述|学术(?:报告|摘要|速递|汇总)|"
                r"研究(?:报告|摘要|速递|汇总)"
            )
            or (
                has(r"学术|研究|论文|文献|期刊|publication|journal|paper|research|literature|scholarly")
                and has(r"周报|月报|报告|摘要|综述|速递|汇总|weekly|monthly|report|digest|review|roundup|briefing")
            )
        ),
        "marketing_promotion_phrase": has(
            r"promotional offers?|discount(?: updates?)?|coupon|sale|special offer|buy now|"
            r"促销(?:活动|信息|更新)?|营销邮件|优惠(?:活动|信息|更新)?|折扣(?:活动|信息|更新)?|"
            r"限时(?:优惠|折扣|活动)?|满减|领券|特价|立即购买"
        ),
        "newsletter_marketing_phrase": has(
            r"\bnewsletter\b|promotional offers?|discount updates?|unsubscribe|manage preferences|"
            r"退订|取消订阅|促销(?:活动|信息|更新)?|营销邮件|优惠(?:活动|信息|更新)?|折扣(?:活动|信息|更新)?"
        ),
        "verification_code_phrase": has(
            r"验证码|校验码|动态口令|一次性密码|登录码|安全码|认证码|短信码|"
            r"\botp\b|one[- ]time password|verification code|security code|"
            r"authentication code|auth code|passcode|\b2fa\b"
        ),
        "risk_phrase": has(
            r"\bphishing\b|suspicious (?:login|activity)|unauthori[sz]ed|security alert|"
            r"account locked|password reset|verify your account|credential theft|fraud|malware|"
            r"钓鱼|可疑(?:登录|活动)|异常登录|未授权|安全警报|账户锁定|密码重置|"
            r"验证账户|凭据盗取|欺诈|恶意软件"
        ),
    }

def _facts(email: Mapping[str, Any], features: Mapping[str, Any]) -> Dict[str, Any]:
    profile = features.get("attachment_profile") if isinstance(features.get("attachment_profile"), Mapping) else {}
    names = _attachment_names(email)
    present = bool(
        profile.get("has_attachments")
        or email.get("has_attachments")
        or email.get("has_attachment")
        or names
    )
    subject_text = str(email.get("subject") or "")[:600]
    body_raw = email.get("body") or email.get("body_plain") or email.get("plain") or email.get("text") or ""
    body_text = email_feature_extractor.clean_text(body_raw, 12000) if email_feature_extractor is not None else str(body_raw)[:12000]
    return {
        "attachments_present": present,
        "attachment_names": names,
        "code_candidates": list(features.get("code_candidates") or [])[:8],
        "sender_domain": str(features.get("sender_domain") or "")[:200],
        "body_shape": dict(features.get("body_shape") or {}),
        "semantic_hints": _semantic_hints(email, subject_text, body_text, features),
        # Ephemeral grounding source. It is used only for deterministic validation
        # and is never persisted in semantic_observations.
        "source_subject": subject_text,
        "source_body": body_text,
    }



def _select_output_budget(
    email: Mapping[str, Any],
    features: Mapping[str, Any],
    facts: Mapping[str, Any],
    settings: Mapping[str, Any],
) -> Dict[str, Any]:
    """Select a deterministic output-token tier from observable mail complexity.

    This chooser never asks the model to classify its own budget. It uses only
    extracted facts and remains a resource boundary, not a semantic verdict.
    """
    mode = str(settings.get("num_predict_mode") or "adaptive").strip().lower()
    legacy_cap = max(256, int(settings.get("num_predict") or 1800))
    hard_cap = min(
        legacy_cap,
        max(384, int(settings.get("num_predict_hard_cap") or legacy_cap)),
    )
    if mode == "fixed":
        selected = min(legacy_cap, hard_cap)
        return {
            "mode": "fixed",
            "tier": "fixed",
            "num_predict": selected,
            "hard_cap": hard_cap,
            "target_output_tokens": max(240, min(1100, int(selected * 0.72))),
            "reasons": ["fixed_mode"],
        }

    hints = dict(facts.get("semantic_hints") or {})
    shape = dict(features.get("body_shape") or facts.get("body_shape") or {})
    attachments = dict(features.get("attachment_profile") or {})
    body_chars = int(shape.get("body_chars") or 0)
    body_lines = int(shape.get("body_lines") or 0)
    attachment_count = int(attachments.get("count") or 0)
    code_candidates = list(facts.get("code_candidates") or [])
    verification_grounded = bool(code_candidates and hints.get("verification_code_phrase"))

    complex_reasons: list[str] = []
    if hints.get("academic_report_subject_phrase") or hints.get("academic_report_phrase"):
        complex_reasons.append("academic_report")
    if hints.get("direct_request_phrase") and hints.get("deadline_phrase"):
        complex_reasons.append("action_and_deadline")
    if hints.get("direct_request_phrase") and attachment_count:
        complex_reasons.append("action_with_attachment")
    if attachment_count >= 3:
        complex_reasons.append("multiple_attachments")
    if shape.get("table_hint") and (
        hints.get("direct_request_phrase")
        or hints.get("deadline_phrase")
        or attachment_count >= 2
    ):
        complex_reasons.append("structured_complex_body")
    if body_chars >= 8000 or body_lines >= 120:
        complex_reasons.append("large_source")
    if hints.get("risk_phrase") and hints.get("direct_request_phrase"):
        complex_reasons.append("security_action")

    simple_reasons: list[str] = []
    if verification_grounded:
        simple_reasons.append("grounded_verification_code")
    if hints.get("system_test_phrase") and hints.get("no_action_phrase"):
        simple_reasons.append("informational_system_notice")
    if (
        hints.get("marketing_promotion_phrase")
        and hints.get("no_action_phrase")
        and body_chars <= 3000
    ):
        simple_reasons.append("short_marketing_no_action")
    if (
        hints.get("receipt_phrase")
        and not hints.get("direct_request_phrase")
        and body_chars <= 3000
    ):
        simple_reasons.append("short_receipt")
    if (
        body_chars <= 1200
        and body_lines <= 20
        and attachment_count == 0
        and not hints.get("direct_request_phrase")
        and not hints.get("deadline_phrase")
        and not hints.get("event_phrase")
        and not hints.get("academic_report_phrase")
        and not hints.get("risk_phrase")
    ):
        simple_reasons.append("short_low_structure")

    if complex_reasons:
        tier = "complex"
        requested = int(settings.get("num_predict_complex") or 1600)
        target = 1100
        reasons = complex_reasons
    elif simple_reasons:
        tier = "simple"
        requested = int(settings.get("num_predict_simple") or 600)
        target = 420
        reasons = simple_reasons
    else:
        tier = "standard"
        requested = int(settings.get("num_predict_standard") or 1000)
        target = 720
        reasons = []
        if hints.get("direct_request_phrase"):
            reasons.append("action_present")
        if hints.get("deadline_phrase"):
            reasons.append("deadline_present")
        if hints.get("event_phrase"):
            reasons.append("event_context")
        if attachment_count:
            reasons.append("attachments_present")
        if body_chars >= 2500 or body_lines >= 40:
            reasons.append("moderate_source")
        if not reasons:
            reasons.append("ordinary_mail")

    selected = max(256, min(requested, hard_cap))
    return {
        "mode": "adaptive",
        "tier": tier,
        "num_predict": selected,
        "hard_cap": hard_cap,
        "target_output_tokens": min(target, max(240, int(selected * 0.78))),
        "reasons": reasons[:8],
        "body_chars": body_chars,
        "body_lines": body_lines,
        "attachment_count": attachment_count,
    }


def _safe_payload(
    email: Mapping[str, Any],
    features: Mapping[str, Any],
    rule_result: Mapping[str, Any],
    analysis: Mapping[str, Any],
    settings: Mapping[str, Any],
) -> Dict[str, Any]:
    llm_payload = dict(features.get("llm_payload") or {})
    body_raw = email.get("body") or email.get("body_plain") or email.get("plain") or email.get("text") or ""
    body = email_feature_extractor.clean_text(body_raw, int(settings.get("max_body_chars") or 12000))
    subject = str(llm_payload.get("subject") or "")[:600]
    semantic_hints = _semantic_hints(email, subject, body, features)
    return {
        "message_key": str(features.get("message_key") or ""),
        "sender": {
            "domain": str(llm_payload.get("from_domain") or "")[:200],
            "name": str(llm_payload.get("from_name") or "")[:120],
        },
        "subject": subject,
        "body": body,
        "attachments": {
            "present": bool((features.get("attachment_profile") or {}).get("has_attachments")),
            "names": _attachment_names(email),
            "profile": dict(features.get("attachment_profile") or {}),
        },
        "deterministic_facts": {
            "code_candidates": list(features.get("code_candidates") or [])[:8],
            "body_shape": dict(features.get("body_shape") or {}),
            "semantic_hints": semantic_hints,
        },
        "legacy_context_non_authoritative": {
            "rule_category": str(rule_result.get("category") or "")[:120],
            "rule_action": str(rule_result.get("action") or "")[:80],
            "rule_priority": str(rule_result.get("priority") or "")[:40],
            "legacy_semantic_category": str(analysis.get("semantic_category") or "")[:120],
            "legacy_should_notify": analysis.get("should_notify"),
        },
        "memory_examples": [],
        "_response_budget": dict(settings.get("_response_budget") or {}),
    }


def build_prompt(payload: Mapping[str, Any]) -> str:
    """Build the readable balanced semantic-core prompt.

    Keep the exact safety phrases retained by the original semantic matrix.
    """
    if email_semantic_core is None:
        raise SemanticEngineError("semantic core unavailable")
    return email_semantic_core.build_prompt(payload)


def _coerce_json_mapping(value: Any, *, depth: int = 0) -> Dict[str, Any] | None:
    """Accept only one semantic object, with narrow wrappers tolerated.

    Ollama occasionally serializes the object as a JSON string, a singleton
    list, or under one generic wrapper key. These are transport-shape issues,
    not semantic permission to accept arbitrary structures.
    """
    if depth > 2:
        return None
    if isinstance(value, dict):
        if len(value) == 1:
            only_key = next(iter(value))
            only_value = value[only_key]
            if str(only_key).strip().lower() in {"result", "output", "data", "response"}:
                nested = _coerce_json_mapping(only_value, depth=depth + 1)
                if nested is not None:
                    return nested
        return value
    if isinstance(value, list) and len(value) == 1:
        return _coerce_json_mapping(value[0], depth=depth + 1)
    if isinstance(value, str) and value.strip():
        try:
            nested = json.loads(value)
        except Exception:
            try:
                nested = ast.literal_eval(value)
            except Exception:
                return None
        return _coerce_json_mapping(nested, depth=depth + 1)
    return None


def _balanced_object_candidates(text: str) -> list[str]:
    """Return balanced top-level object substrings without executing content."""
    candidates: list[str] = []
    start: int | None = None
    depth = 0
    in_string = False
    quote = ""
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                in_string = False
            continue
        if char in {'"', "'"}:
            in_string = True
            quote = char
            continue
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                candidates.append(text[start:index + 1])
                start = None
    return candidates


def _candidate_variants(text: str) -> list[str]:
    raw = (text or "").lstrip("\ufeff").strip()
    variants: list[str] = []

    def add(value: str) -> None:
        value = (value or "").strip()
        if value and value not in variants:
            variants.append(value)

    add(raw)
    if raw.startswith("```"):
        fenced = raw.strip("`").strip()
        if fenced.lower().startswith("json"):
            fenced = fenced[4:].strip()
        add(fenced)

    for match in re.finditer(r"```(?:json)?\s*(.*?)```", raw, re.I | re.S):
        add(match.group(1))

    for candidate in _balanced_object_candidates(raw):
        add(candidate)

    # Narrow syntax repairs for common model formatting mistakes.
    for value in list(variants):
        add(re.sub(r",\s*([}\]])", r"\1", value))
    return variants


def _extract_json_object_detailed(text: str) -> Tuple[Dict[str, Any], str]:
    """Parse one JSON-like object and report the deterministic recovery strategy."""
    raw = (text or "").lstrip("\ufeff").strip()
    last_error = ""

    # A syntactically complete top-level value is authoritative. If it is a
    # multi-item list or scalar, do not cherry-pick an embedded object from it.
    for parser_name, parser in (("json", json.loads), ("python_literal", ast.literal_eval)):
        try:
            direct_value = parser(raw)
        except Exception as exc:
            last_error = f"{parser_name}:{type(exc).__name__}"
            continue
        mapping = _coerce_json_mapping(direct_value)
        if mapping is not None:
            return mapping, parser_name
        raise SemanticEngineError(f"model response top-level value is not one object ({parser_name})")

    for candidate in _candidate_variants(raw):
        if candidate == raw:
            continue
        for parser_name, parser in (("json", json.loads), ("python_literal", ast.literal_eval)):
            try:
                value = parser(candidate)
            except Exception as exc:
                last_error = f"{parser_name}:{type(exc).__name__}"
                continue
            mapping = _coerce_json_mapping(value)
            if mapping is not None:
                return mapping, f"{parser_name}_extracted"
            last_error = f"{parser_name}:non_mapping"
    raise SemanticEngineError(
        "model response is not a JSON object"
        + (f" ({last_error})" if last_error else "")
    )


def _extract_json_object(text: str) -> Dict[str, Any]:
    value, _strategy = _extract_json_object_detailed(text)
    return value


def _ollama_request(
    prompt: str,
    settings: Mapping[str, Any],
    *,
    timeout_seconds: int,
    temperature: float,
    num_predict: int,
    response_format: Any = None,
) -> Dict[str, Any]:
    endpoint = str(settings.get("endpoint") or "http://127.0.0.1:11434").rstrip("/") + "/api/generate"
    body = json.dumps(
        {
            "model": str(settings.get("model") or "qwen2.5:3b"),
            "prompt": prompt,
            "stream": False,
            "format": (
                response_format
                if response_format is not None
                else (
                    email_semantic_core.ollama_format_schema()
                    if email_semantic_core is not None and hasattr(email_semantic_core, "ollama_format_schema")
                    else "json"
                )
            ),
            "options": {
                "temperature": float(temperature),
                "num_predict": int(num_predict),
                "num_thread": int(settings.get("num_thread", 5)),
            },
        }
    ).encode("utf-8")
    request = urllib.request.Request(endpoint, data=body, headers={"Content-Type": "application/json"})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=max(1, int(timeout_seconds))) as response:
            outer_text = response.read().decode("utf-8", "replace")
    except TimeoutError as exc:
        raise SemanticEngineTimeout("Ollama request timed out") from exc
    except urllib.error.URLError as exc:
        if isinstance(getattr(exc, "reason", None), TimeoutError):
            raise SemanticEngineTimeout("Ollama request timed out") from exc
        raise SemanticEngineError(f"Ollama request failed: {exc}") from exc

    elapsed_ms = int((time.monotonic() - started) * 1000)
    try:
        outer = json.loads(outer_text)
    except Exception as exc:
        raise SemanticEngineError("Ollama outer response is not valid JSON") from exc
    content = str(outer.get("response") or "")
    metrics = {
        "total_duration_ms": int(outer.get("total_duration") or 0) // 1_000_000,
        "load_duration_ms": int(outer.get("load_duration") or 0) // 1_000_000,
        "prompt_eval_count": int(outer.get("prompt_eval_count") or 0),
        "prompt_eval_duration_ms": int(outer.get("prompt_eval_duration") or 0) // 1_000_000,
        "eval_count": int(outer.get("eval_count") or 0),
        "eval_duration_ms": int(outer.get("eval_duration") or 0) // 1_000_000,
        "done_reason": str(outer.get("done_reason") or "")[:80],
        "response_chars": len(content),
    }
    if metrics["eval_count"] and metrics["eval_duration_ms"]:
        metrics["eval_tokens_per_second"] = round(
            metrics["eval_count"] / (metrics["eval_duration_ms"] / 1000.0), 3
        )
    else:
        metrics["eval_tokens_per_second"] = 0.0
    return {
        "content": content,
        "latency_ms": elapsed_ms,
        "metrics": metrics,
    }


def _json_repair_prompt(primary_content: str) -> str:
    """Build a format-only repair request from the first model output.

    The repair call never receives the original email prompt. It may only
    serialize semantics already present in the first response, and the normal
    grounding validator still rejects unsupported facts afterwards.
    """
    content = (primary_content or "").strip()
    # Bound the repair prompt while retaining both the beginning and end, where
    # JSON envelopes and closing fields are usually located.
    if len(content) > 12000:
        content = content[:8000] + "\n<TRUNCATED_FOR_FORMAT_REPAIR>\n" + content[-4000:]
    return (
        "JSON-REPAIR: Convert the malformed candidate below into exactly one "
        "valid JSON object matching the supplied response schema. Preserve only "
        "information already present in the candidate. Do not analyze the email "
        "again, add facts, explain, use Markdown, or return multiple objects. "
        "If a field cannot be recovered, use a conservative empty/default value.\n\n"
        "MALFORMED-CANDIDATE-BEGIN\n"
        + content
        + "\nMALFORMED-CANDIDATE-END"
    )


def call_ollama(prompt: str, settings: Mapping[str, Any]) -> Dict[str, Any]:
    """Call Ollama with deterministic recovery and one format-only repair call.

    ``timeout_seconds`` is the full budget for the primary semantic analysis.
    A malformed but completed primary response may then receive one independent
    format-only JSON repair call. The repair call sees only the first model
    response, never the original email prompt, and uses lightweight ``format=json``
    rather than the full semantic response schema. With the default settings the
    worst-case wall time is 300 + 180 seconds, while normal valid responses still
    perform exactly one call.
    """
    if str(settings.get("provider") or "ollama").lower() != "ollama":
        raise SemanticEngineError("unsupported semantic provider")

    total_started = time.monotonic()
    primary_budget = max(1, int(settings.get("timeout_seconds") or 300))
    repair_budget = max(120, min(240, int(primary_budget * 0.6)))

    primary = _ollama_request(
        prompt,
        settings,
        timeout_seconds=primary_budget,
        temperature=float(settings.get("temperature", 0.1)),
        num_predict=int(settings.get("num_predict", 1800)),
    )
    try:
        parsed, parse_strategy = _extract_json_object_detailed(primary["content"])
        metrics = dict(primary["metrics"])
        metrics.update(
            {
                "retry_count": 0,
                "parse_strategy": parse_strategy,
                "primary_timeout_budget_seconds": primary_budget,
                "json_repair_timeout_budget_seconds": repair_budget,
                "max_end_to_end_budget_seconds": primary_budget + repair_budget,
            }
        )
        return {
            "parsed": parsed,
            "latency_ms": int((time.monotonic() - total_started) * 1000),
            "model": str(settings.get("model") or "qwen2.5:3b"),
            "metrics": metrics,
        }
    except SemanticEngineError as primary_error:
        primary_metrics = dict(primary.get("metrics") or {})
        requested_predict = int(settings.get("num_predict", 1800))
        done_reason = str(primary_metrics.get("done_reason") or "").strip().lower()
        eval_count = int(primary_metrics.get("eval_count") or 0)
        if done_reason in {"length", "max_tokens"} or (
            eval_count >= requested_predict and requested_predict > 0
        ):
            primary_metrics.update(
                {
                    "retry_count": 0,
                    "retry_kind": "skipped_for_truncated_primary",
                    "parse_strategy": "",
                    "primary_timeout_budget_seconds": primary_budget,
                    "json_repair_timeout_budget_seconds": repair_budget,
                    "max_end_to_end_budget_seconds": primary_budget,
                    "requested_num_predict": requested_predict,
                }
            )
            raise SemanticEngineTruncated(
                f"primary model output truncated at num_predict={requested_predict}",
                primary_metrics,
            ) from primary_error

        repair_prompt = _json_repair_prompt(primary["content"])
        repair_predict = max(
            280,
            min(420, int(int(settings.get("num_predict", 1800)) * 0.5)),
        )
        repair = _ollama_request(
            repair_prompt,
            settings,
            timeout_seconds=repair_budget,
            temperature=0.0,
            num_predict=repair_predict,
            response_format="json",
        )
        try:
            parsed, parse_strategy = _extract_json_object_detailed(repair["content"])
        except SemanticEngineError as repair_error:
            raise SemanticEngineError(
                "model response is not a JSON object after format-only JSON repair"
            ) from repair_error

        metrics = dict(repair["metrics"])
        metrics.update(
            {
                "retry_count": 1,
                "retry_kind": "json_repair_only",
                "parse_strategy": parse_strategy,
                "primary_done_reason": str(primary["metrics"].get("done_reason") or "")[:80],
                "primary_eval_count": int(primary["metrics"].get("eval_count") or 0),
                "primary_response_chars": int(primary["metrics"].get("response_chars") or 0),
                "primary_latency_ms": int(primary.get("latency_ms") or 0),
                "primary_timeout_budget_seconds": primary_budget,
                "json_repair_timeout_budget_seconds": repair_budget,
                "max_end_to_end_budget_seconds": primary_budget + repair_budget,
                "repair_num_predict": repair_predict,
                "repair_prompt_chars": len(repair_prompt),
            }
        )
        return {
            "parsed": parsed,
            "latency_ms": int((time.monotonic() - total_started) * 1000),
            "model": str(settings.get("model") or "qwen2.5:3b"),
            "metrics": metrics,
        }


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def ensure_schema(db_path: Path | str = DEFAULT_DB_PATH) -> Dict[str, Any]:
    path = Path(db_path)
    conn: Optional[sqlite3.Connection] = None
    try:
        conn = _connect(path)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS semantic_observations (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              message_key TEXT UNIQUE NOT NULL,
              sender_domain TEXT,
              category TEXT,
              importance TEXT,
              content_mode TEXT,
              summary_hash TEXT,
              decision_json TEXT NOT NULL,
              model TEXT,
              prompt_version TEXT,
              schema_version INTEGER,
              latency_ms INTEGER,
              schema_valid INTEGER,
              fallback_used INTEGER,
              timeout INTEGER DEFAULT 0,
              cache_hit INTEGER DEFAULT 0,
              error_code TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        existing = {row[1] for row in conn.execute("PRAGMA table_info(semantic_observations)")}
        additive_columns = {
            "timeout": "INTEGER DEFAULT 0",
            "cache_hit": "INTEGER DEFAULT 0",
            "error_code": "TEXT",
            "updated_at": "TEXT",
        }
        for name, definition in additive_columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE semantic_observations ADD COLUMN {name} {definition}")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_semantic_observations_created ON semantic_observations(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_semantic_observations_category ON semantic_observations(category)")
        conn.commit()
        return {"ok": True, "db_path": str(path), "table": "semantic_observations"}
    except Exception as exc:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return {"ok": False, "db_path": str(path), "error": _safe_error(exc)}
    finally:
        if conn is not None:
            conn.close()


def _cache_lookup(
    db_path: Path,
    message_key: str,
    settings: Mapping[str, Any],
) -> Optional[Dict[str, Any]]:
    if not bool(settings.get("cache_by_message_hash", True)) or not db_path.exists():
        return None
    conn: Optional[sqlite3.Connection] = None
    try:
        conn = sqlite3.connect(str(db_path), timeout=5)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT decision_json, model, prompt_version, schema_version, latency_ms,
                   fallback_used, timeout, error_code
            FROM semantic_observations WHERE message_key=? LIMIT 1
            """,
            (message_key,),
        ).fetchone()
        if not row:
            return None
        if row["model"] != str(settings.get("model") or ""):
            return None
        if row["prompt_version"] != PROMPT_VERSION or int(row["schema_version"] or 0) != 2:
            return None
        decision = json.loads(row["decision_json"])
        return {
            "decision": decision,
            "latency_ms": int(row["latency_ms"] or 0),
            "fallback_used": bool(row["fallback_used"]),
            "timeout": bool(row["timeout"]),
            "error_code": row["error_code"] or "",
        }
    except Exception:
        return None
    finally:
        if conn is not None:
            conn.close()


def _persist_observation(
    *,
    db_path: Path,
    message_key: str,
    facts: Mapping[str, Any],
    decision: Mapping[str, Any],
    model: str,
    latency_ms: int,
    fallback_used: bool,
    timeout: bool,
    cache_hit: bool,
    error_code: str,
) -> Dict[str, Any]:
    schema_result = ensure_schema(db_path)
    if not schema_result.get("ok"):
        return schema_result
    conn: Optional[sqlite3.Connection] = None
    now = _now()
    try:
        conn = _connect(db_path)
        classification = decision.get("classification") if isinstance(decision.get("classification"), Mapping) else {}
        importance = decision.get("importance") if isinstance(decision.get("importance"), Mapping) else {}
        notification = decision.get("notification") if isinstance(decision.get("notification"), Mapping) else {}
        conn.execute(
            """
            INSERT INTO semantic_observations (
              message_key, sender_domain, category, importance, content_mode,
              summary_hash, decision_json, model, prompt_version, schema_version,
              latency_ms, schema_valid, fallback_used, timeout, cache_hit,
              error_code, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(message_key) DO UPDATE SET
              sender_domain=excluded.sender_domain,
              category=excluded.category,
              importance=excluded.importance,
              content_mode=excluded.content_mode,
              summary_hash=excluded.summary_hash,
              decision_json=excluded.decision_json,
              model=excluded.model,
              prompt_version=excluded.prompt_version,
              schema_version=excluded.schema_version,
              latency_ms=excluded.latency_ms,
              schema_valid=excluded.schema_valid,
              fallback_used=excluded.fallback_used,
              timeout=excluded.timeout,
              cache_hit=excluded.cache_hit,
              error_code=excluded.error_code,
              updated_at=excluded.updated_at
            """,
            (
                message_key,
                str(facts.get("sender_domain") or "")[:200],
                str(classification.get("category") or "unknown_needs_llm")[:120],
                str(importance.get("level") or "normal")[:40],
                str(notification.get("content_mode") or "summary_only")[:80],
                email_semantic_schema.summary_hash(decision),
                _json(decision),
                model[:200],
                PROMPT_VERSION,
                2,
                max(0, int(latency_ms or 0)),
                int(bool(fallback_used)),
                int(bool(timeout)),
                int(bool(cache_hit)),
                error_code[:200],
                now,
                now,
            ),
        )
        conn.commit()
        return {"ok": True, "db_path": str(db_path), "message_key": message_key}
    except Exception as exc:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return {"ok": False, "db_path": str(db_path), "error": _safe_error(exc)}
    finally:
        if conn is not None:
            conn.close()


def analyze_email(
    email: Mapping[str, Any],
    rule_result: Mapping[str, Any],
    analysis: Mapping[str, Any],
    *,
    settings_override: Mapping[str, Any] | None = None,
    transport: Callable[[str, Mapping[str, Any]], Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    global email_semantic_core
    settings = _settings(settings_override)
    # Source upgrades must take effect without a container restart. The delivery
    # wrapper reloads this engine on each delivery; reload the core explicitly too.
    if email_semantic_core is not None:
        try:
            email_semantic_core = importlib.reload(email_semantic_core)
        except Exception:
            pass
    if not settings.get("enabled"):
        return {"ok": True, "skipped": True, "reason": "semantic engine disabled", "settings": settings}
    if settings.get("mode") != "shadow":
        return {"ok": True, "skipped": True, "reason": "phase1 requires shadow mode", "settings": settings}
    if email_feature_extractor is None or email_semantic_schema is None or email_semantic_core is None:
        raise SemanticEngineError("semantic dependencies unavailable")

    features = email_feature_extractor.extract_features(dict(email or {}))
    facts = _facts(email or {}, features)
    message_key = str(features.get("message_key") or "")
    output_budget = _select_output_budget(email or {}, features, facts, settings)
    effective_settings = dict(settings)
    effective_settings["num_predict"] = int(output_budget["num_predict"])
    effective_settings["_response_budget"] = output_budget
    payload = _safe_payload(email or {}, features, rule_result or {}, analysis or {}, effective_settings)
    prompt = build_prompt(payload)
    started = time.monotonic()
    timeout = False
    fallback_used = False
    error_code = ""
    raw_errors: list[str] = []
    normalization_repairs: list[str] = []
    model_core_keys: list[str] = []
    ollama_metrics: Dict[str, Any] = {}
    model = str(settings.get("model") or "qwen2.5:3b")

    try:
        caller = transport or call_ollama
        with _CALL_LOCK:
            response = caller(prompt, effective_settings)
        latency_ms = int(response.get("latency_ms") or ((time.monotonic() - started) * 1000))
        model = str(response.get("model") or model)
        ollama_metrics = dict(response.get("metrics") or {})
        if hasattr(email_semantic_core, "normalize_and_expand_detailed"):
            decision, errors, normalization_repairs, model_core_keys = (
                email_semantic_core.normalize_and_expand_detailed(
                    response.get("parsed"), message_key=message_key, facts=facts
                )
            )
        else:
            decision, errors = email_semantic_core.normalize_and_expand(
                response.get("parsed"), message_key=message_key, facts=facts
            )
            model_core_keys = sorted(
                str(key) for key in (response.get("parsed") or {}).keys()
            ) if isinstance(response.get("parsed"), Mapping) else []
        if errors or decision is None:
            raw_errors = errors or ["schema normalization failed"]
            raise SemanticEngineError("; ".join(raw_errors[:6]))
    except SemanticEngineTruncated as exc:
        fallback_used = True
        error_code = "output_truncated"
        latency_ms = int((time.monotonic() - started) * 1000)
        ollama_metrics = dict(getattr(exc, "metrics", {}) or {})
        raw_errors = [_safe_error(exc)]
        decision = email_semantic_schema.conservative_fallback(
            message_key=message_key,
            email=email,
            rule_result=rule_result,
            analysis=analysis,
            facts=facts,
            reason=_safe_error(exc),
        )
    except SemanticEngineTimeout as exc:
        timeout = True
        fallback_used = True
        error_code = "timeout"
        latency_ms = int((time.monotonic() - started) * 1000)
        decision = email_semantic_schema.conservative_fallback(
            message_key=message_key,
            email=email,
            rule_result=rule_result,
            analysis=analysis,
            facts=facts,
            reason=_safe_error(exc),
        )
    except Exception as exc:
        fallback_used = True
        error_code = "schema_invalid" if raw_errors else "engine_error"
        latency_ms = int((time.monotonic() - started) * 1000)
        decision = email_semantic_schema.conservative_fallback(
            message_key=message_key,
            email=email,
            rule_result=rule_result,
            analysis=analysis,
            facts=facts,
            reason=_safe_error(exc),
        )

    validated, validation_errors = email_semantic_schema.normalize_and_validate(
        decision, message_key=message_key, facts=facts
    )
    if validation_errors or validated is None:
        raise SemanticEngineError("internal fallback schema invalid: " + "; ".join(validation_errors[:6]))
    return {
        "ok": True,
        "decision": validated,
        "message_key": message_key,
        "facts": facts,
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "schema_version": 2,
        "latency_ms": latency_ms,
        "schema_valid": True,
        "fallback_used": fallback_used,
        "timeout": timeout,
        "error_code": error_code,
        "raw_errors": raw_errors[:10],
        "normalization_repairs": normalization_repairs[:20],
        "model_core_keys": model_core_keys[:40],
        "prompt_hash": _sha(prompt),
        "core_protocol": str(settings.get("protocol") or "readable_grounded_core_v1u"),
        "num_thread": int(settings.get("num_thread") or 5),
        "num_predict": int(output_budget.get("num_predict") or 0),
        "num_predict_hard_cap": int(output_budget.get("hard_cap") or 0),
        "output_budget_mode": str(output_budget.get("mode") or ""),
        "output_budget_tier": str(output_budget.get("tier") or ""),
        "output_budget_reasons": list(output_budget.get("reasons") or [])[:8],
        "target_output_tokens": int(output_budget.get("target_output_tokens") or 0),
        "timeout_seconds": int(settings.get("timeout_seconds") or 300),
        "ollama_metrics": ollama_metrics,
    }


def shadow_observe(
    email: Mapping[str, Any],
    rule_result: Mapping[str, Any],
    analysis: Mapping[str, Any],
    delivery: Mapping[str, Any],
    account: Mapping[str, Any] | None = None,
    *,
    settings_override: Mapping[str, Any] | None = None,
    transport: Callable[[str, Mapping[str, Any]], Dict[str, Any]] | None = None,
    db_path: Path | str | None = None,
) -> Dict[str, Any]:
    """Observe one delivered/skipped decision without altering the production result."""
    try:
        settings = _settings(settings_override)
        if not settings.get("enabled"):
            return {"ok": True, "skipped": True, "reason": "semantic engine disabled"}
        if settings.get("mode") != "shadow":
            return {"ok": True, "skipped": True, "reason": "phase1 requires shadow mode"}
        if email_feature_extractor is None or email_semantic_schema is None or email_semantic_core is None:
            return {"ok": False, "error": "semantic dependencies unavailable"}

        features = email_feature_extractor.extract_features(dict(email or {}))
        message_key = str(features.get("message_key") or "")
        path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
        cached = _cache_lookup(path, message_key, settings)
        if cached is not None:
            persist = _persist_observation(
                db_path=path,
                message_key=message_key,
                facts=_facts(email or {}, features),
                decision=cached["decision"],
                model=str(settings.get("model") or ""),
                latency_ms=cached["latency_ms"],
                fallback_used=cached["fallback_used"],
                timeout=cached["timeout"],
                cache_hit=True,
                error_code=cached["error_code"],
            )
            return {
                "ok": bool(persist.get("ok")),
                "cache_hit": True,
                "llm_called": False,
                "message_key": message_key,
                "decision": cached["decision"],
                "persist": persist,
                "production_notification_changed": False,
                "candidate_promotion_executed": False,
                "learned_category_rules_written": False,
            }

        result = analyze_email(
            email or {},
            rule_result or {},
            analysis or {},
            settings_override=settings,
            transport=transport,
        )
        if result.get("skipped"):
            return result
        persist = _persist_observation(
            db_path=path,
            message_key=result["message_key"],
            facts=result["facts"],
            decision=result["decision"],
            model=result["model"],
            latency_ms=result["latency_ms"],
            fallback_used=result["fallback_used"],
            timeout=result["timeout"],
            cache_hit=False,
            error_code=result["error_code"],
        )
        return {
            "ok": bool(persist.get("ok")),
            "cache_hit": False,
            "llm_called": True,
            "message_key": result["message_key"],
            "decision": result["decision"],
            "model": result["model"],
            "prompt_version": result["prompt_version"],
            "schema_version": result["schema_version"],
            "latency_ms": result["latency_ms"],
            "schema_valid": result["schema_valid"],
            "fallback_used": result["fallback_used"],
            "timeout": result["timeout"],
            "error_code": result["error_code"],
            "raw_errors": result.get("raw_errors") or [],
            "normalization_repairs": result.get("normalization_repairs") or [],
            "model_core_keys": result.get("model_core_keys") or [],
            "core_protocol": result.get("core_protocol"),
            "num_thread": result.get("num_thread"),
            "num_predict": result.get("num_predict"),
            "num_predict_hard_cap": result.get("num_predict_hard_cap"),
            "output_budget_mode": result.get("output_budget_mode"),
            "output_budget_tier": result.get("output_budget_tier"),
            "output_budget_reasons": result.get("output_budget_reasons") or [],
            "target_output_tokens": result.get("target_output_tokens"),
            "timeout_seconds": result.get("timeout_seconds"),
            "ollama_metrics": result.get("ollama_metrics") or {},
            "persist": persist,
            "production_notification_changed": False,
            "candidate_promotion_executed": False,
            "learned_category_rules_written": False,
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": _safe_error(exc),
            "production_notification_changed": False,
            "candidate_promotion_executed": False,
            "learned_category_rules_written": False,
        }


def status(db_path: Path | str | None = None) -> Dict[str, Any]:
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    result: Dict[str, Any] = {
        "marker": MARKER,
        "prompt_version": PROMPT_VERSION,
        "db_path": str(path),
        "table_exists": False,
        "observation_count": 0,
    }
    if not path.exists():
        return result
    conn: Optional[sqlite3.Connection] = None
    try:
        conn = sqlite3.connect(str(path), timeout=5)
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='semantic_observations'"
        ).fetchone()
        result["table_exists"] = bool(table)
        if table:
            result["observation_count"] = int(
                conn.execute("SELECT COUNT(*) FROM semantic_observations").fetchone()[0]
            )
        return result
    except Exception as exc:
        result["error"] = _safe_error(exc)
        return result
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    print(json.dumps(status(), ensure_ascii=False, indent=2, sort_keys=True))
