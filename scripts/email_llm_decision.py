#!/usr/bin/env python3
"""
Ollama-backed LLM observer for Hermes Email Watchdog decision-engine shadow.
Shadow-only: result is logged as pseudo-label / candidate signal, never controls delivery.
"""
from __future__ import annotations

# EMAIL_WATCHDOG_LLM_SEMANTIC_BOUNDARIES_V1

import json
import re
import time
import urllib.request
from typing import Any, Dict

try:
    import email_safe_patterns
except Exception:
    email_safe_patterns = None

CANONICAL_CATEGORIES = {
    "verification_code",
    "account_security",
    "account_status_notice",
    "invoice_receipt",
    "school_notice",
    "health_check_notice",
    "meeting_event",
    "task_deadline",
    "paper_manuscript_feedback",
    "academic_opportunity_call",
    "academic_report_digest",
    "academic_alert_digest",
    "research_feedback_thread",
    "data_download_order_notice",
    "system_automation_notice",
    "newsletter_marketing",
    "delivery_logistics",
    "personal_or_general",
    "unknown_needs_llm",
}
ALIASES = {
    "security_alert": "account_security",
    "account_notification": "account_status_notice",
    "paper_feedback": "paper_manuscript_feedback",
    "paper_review": "paper_manuscript_feedback",
    "newsletter": "newsletter_marketing",
    "marketing": "newsletter_marketing",
    "system_notification": "system_automation_notice",
    "personal_task": "personal_or_general",
    "other": "personal_or_general",
    "unknown": "unknown_needs_llm",
}
IMPORTANCE = {"urgent", "high", "medium", "low", "ignore"}
RENDER_MODES = {"compact", "evidence", "full_excerpt", "code_only", "finance_card", "event_card", "deadline_card", "digest_card", "summary_only"}


def normalize_category(value: Any) -> str:
    cat = str(value or "").strip().lower()
    cat = re.sub(r"[^a-z0-9_]+", "_", cat).strip("_")
    cat = ALIASES.get(cat, cat)
    return cat if cat in CANONICAL_CATEGORIES else "unknown_needs_llm"


def _clamp_float(value: Any, default: float = 0.0) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return default


def _json_from_text(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text[start:end + 1])
    raise ValueError("LLM output did not contain valid JSON object")


def build_prompt(features: Dict[str, Any], rule_decision: Dict[str, Any]) -> str:
    payload = dict((features or {}).get("llm_payload") or {})
    # Keep prompt compact; local Ollama only, but avoid unnecessary raw retention.
    if payload.get("body_excerpt") and len(payload["body_excerpt"]) > 6000:
        payload["body_excerpt"] = payload["body_excerpt"][:6000]

    schema = {
        "semantic_category": "one canonical category",
        "importance": "urgent|high|medium|low|ignore",
        "needs_action": False,
        "deadline": None,
        "summary": "short Chinese summary",
        "evidence": ["short evidence phrase"],
        "recommended_render_mode": "compact|evidence|full_excerpt|code_only|finance_card|event_card|deadline_card|digest_card|summary_only",
        "confidence": 0.0,
        "negative_context_detected": False,
        "learned_pattern_candidate": None,
    }

    category_definitions = {
        "verification_code": "邮件实际提供可使用的一次性验证码、登录码、动态码或安全码；通常包含4至8位数字/字母代码和登录、验证、确认上下文。",
        "account_security": "真实的异常登录、安全告警、密码重置、账户被盗风险或需要立即处理的安全事件；仅仅发送验证码或提醒不要泄露验证码不属于此类。",
        "account_status_notice": "账户注册、启用、停用、过期、归档、订阅或资格状态变化，不是安全事件，也不是验证码。",
        "invoice_receipt": "发票、账单、收据、支付或付款结果。",
        "school_notice": "学校、学院、教务或行政通知。",
        "health_check_notice": "健康打卡、健康填报等通知。",
        "meeting_event": "会议、讲座、组会、答辩或活动安排。",
        "task_deadline": "明确要求提交、确认、处理或完成任务，并包含行动要求或截止时间。",
        "paper_manuscript_feedback": "真实论文审稿、返修、编辑决定或稿件反馈。",
        "academic_opportunity_call": "征稿、会议投稿、摘要征集、海报比赛或学术邀请。",
        "academic_report_digest": "学术周报、研究周报或报告型摘要。",
        "academic_alert_digest": "文献、引用或研究快讯。",
        "research_feedback_thread": "导师或合作者的研究讨论、论文修改与实验反馈线程。",
        "data_download_order_notice": "数据下载、全文传递、数据订单或下载就绪通知。",
        "system_automation_notice": "自动化任务、系统工作流、构建或生成结果通知。",
        "newsletter_marketing": "订阅通讯、营销、推广或促销。",
        "delivery_logistics": "快递、物流、取件、签收或运输状态。",
        "personal_or_general": "普通个人邮件或没有更具体类别的常规邮件。",
        "unknown_needs_llm": "信息不足、语义混合，或只是一般安全教育/反诈提醒且没有可使用验证码、没有真实安全事件。",
    }

    boundary_rules = [
        "如果正文实际包含4至8位可使用验证码，并有登录/验证/确认上下文，优先选择 verification_code；即使同时出现“安全”“不要泄露”等词，也不要改判为 account_security。",
        "account_security 必须是异常登录、密码重置、盗号风险等真实安全事件；不要因为验证码邮件带有安全提醒就选择 account_security。",
        "account_status_notice 只表示账户状态变化、注册、启停或过期，不表示验证码或安全告警。",
        "反诈或验证码安全教育邮件，如果没有实际可使用验证码、也没有真实安全事件，选择 unknown_needs_llm。",
        "Rule pre-decision 是重要证据。若其置信度大于等于0.86而你不同意，必须给出明确证据；此时 learned_pattern_candidate 必须为 null。",
        "仅当不存在 negative_context、规则没有 conflict、且你与非 catch-all 规则类别一致，或者规则类别为 personal_or_general/unknown_needs_llm 时，才可生成 learned_pattern_candidate。",
        "候选规则置信度必须至少0.75；不要用单个过宽泛词生成候选；不满足条件时 learned_pattern_candidate 必须为 null。",
    ]

    return (
        "你是 Hermes Email Watchdog 的 shadow-only 邮件语义观察器。\n"
        "你的输出只会被记录为伪标签和候选规则，不会控制真实推送、不会回复邮件、不会修改邮箱。\n"
        "请严格输出一个 JSON object，不要 Markdown，不要解释。\n\n"
        "Canonical category definitions:\n"
        + json.dumps(category_definitions, ensure_ascii=False, indent=2)
        + "\n\nBoundary and safety rules:\n- "
        + "\n- ".join(boundary_rules)
        + "\n\nRule pre-decision:\n"
        + json.dumps(rule_decision or {}, ensure_ascii=False, indent=2, default=str)
        + "\n\nEmail safe payload:\n"
        + json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        + "\n\nRequired JSON schema example:\n"
        + json.dumps(schema, ensure_ascii=False, indent=2)
    )


def call_ollama(prompt: str, config: Dict[str, Any]) -> Dict[str, Any]:
    llm = (config or {}).get("llm") or {}
    url = str(llm.get("url") or "http://127.0.0.1:11434").rstrip("/")
    model = str(llm.get("model") or "qwen2.5:3b")
    timeout = int(llm.get("timeout_seconds") or 20)
    endpoint = url + "/api/generate"
    data = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": float(llm.get("temperature", 0.1)),
            "num_predict": int(llm.get("num_predict", 900)),
        },
    }).encode("utf-8")
    req = urllib.request.Request(endpoint, data=data, headers={"Content-Type": "application/json"})
    started = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
    elapsed_ms = int((time.time() - started) * 1000)
    outer = json.loads(raw)
    content = outer.get("response") or ""
    parsed = _json_from_text(content)
    return {
        "ok": True,
        "model": model,
        "elapsed_ms": elapsed_ms,
        "raw_outer_keys": sorted(outer.keys()),
        "raw_response": content,
        "parsed": parsed,
    }


def normalize_llm_output(raw: Dict[str, Any], features: Dict[str, Any] | None = None) -> Dict[str, Any]:
    parsed = dict((raw or {}).get("parsed") or raw or {})
    cat = normalize_category(parsed.get("semantic_category") or parsed.get("category"))
    importance = str(parsed.get("importance") or "medium").strip().lower()
    if importance not in IMPORTANCE:
        importance = "medium"
    render = str(parsed.get("recommended_render_mode") or parsed.get("render_mode") or "compact").strip().lower()
    if render not in RENDER_MODES:
        render = "compact"
    evidence = parsed.get("evidence") or []
    if isinstance(evidence, str):
        evidence = [evidence]
    evidence = [str(x)[:160] for x in evidence[:5] if str(x or "").strip()]

    negative_context = bool(parsed.get("negative_context_detected"))
    candidate = parsed.get("learned_pattern_candidate")
    candidate_status = {"ok": False, "error": "no candidate"}

    if isinstance(candidate, dict) and email_safe_patterns is not None:
        candidate_category = normalize_category(candidate.get("category") or cat)
        if candidate_category != cat:
            candidate_status = {
                "ok": False,
                "error": "candidate category does not match semantic_category",
                "candidate": candidate,
            }
            candidate = None
        else:
            candidate["category"] = cat
            candidate_status = email_safe_patterns.validate_candidate(candidate, cat)
            candidate = candidate_status.get("candidate") if candidate_status.get("ok") else None

        if candidate is not None and float(candidate.get("confidence") or 0.0) < 0.75:
            candidate_status = {
                "ok": False,
                "error": "candidate confidence below 0.75",
                "candidate": candidate,
            }
            candidate = None
        if candidate is not None and negative_context:
            candidate_status = {
                "ok": False,
                "error": "candidate blocked by LLM negative context",
                "candidate": candidate,
            }
            candidate = None
    else:
        candidate = None

    return {
        "schema_valid": True,
        "semantic_category": cat,
        "importance": importance,
        "needs_action": bool(parsed.get("needs_action")),
        "deadline": parsed.get("deadline") if parsed.get("deadline") not in {"", "null", "None"} else None,
        "summary": str(parsed.get("summary") or "")[:500],
        "evidence": evidence,
        "recommended_render_mode": render,
        "confidence": _clamp_float(parsed.get("confidence"), 0.5),
        "negative_context_detected": negative_context,
        "learned_pattern_candidate": candidate,
        "candidate_status": candidate_status,
    }


def analyze(features: Dict[str, Any], rule_decision: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    if not ((config or {}).get("llm") or {}).get("enabled", False):
        return {"ok": False, "llm_called": False, "error": "llm disabled"}
    prompt = build_prompt(features, rule_decision)
    try:
        raw = call_ollama(prompt, config)
        norm = normalize_llm_output(raw, features)
        norm.update({
            "ok": True,
            "llm_called": True,
            "model": raw.get("model"),
            "elapsed_ms": raw.get("elapsed_ms"),
            "raw_output_excerpt": str(raw.get("raw_response") or "")[:1200],
        })
        return norm
    except Exception as exc:
        return {
            "ok": False,
            "llm_called": True,
            "schema_valid": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
