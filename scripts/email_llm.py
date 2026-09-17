#!/usr/bin/env python3
"""Semantic delivery planning for Hermes Email Watchdog."""

import json
import os
import re
import ssl
import sys
import urllib.request
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

try:
    import email_config
    import email_store
except ImportError:
    email_config = None
    email_store = None

FORMAT_CHOICES = {"full_body", "summary", "code_extraction"}
RELEVANCE_CHOICES = {"ignore", "low", "medium", "high", "urgent"}
ACTION_TYPES = {"reply", "read", "submit_form", "pay", "attend", "revise_document", "download", "monitor", "none"}
ATTACHMENT_POLICIES = {"download_all", "download_safe", "download_invoices_only", "list_only", "none"}


def should_use_llm(rule_result: dict, email: dict = None) -> bool:
    """False only for skip and high-confidence simple cases."""
    action = (rule_result or {}).get("action")
    return action not in {"skip", "simple_code", "high_confidence_simple"}


def analyze_email(email: dict, rule_result: dict) -> dict:
    """Return structured semantic analysis for delivery."""
    settings = email_config.get_llm_settings() if email_config else {}
    if settings.get("enabled", True) is False:
        return fallback_analysis(email, rule_result, "llm disabled")
    try:
        prompt = build_prompt(email, rule_result, datetime.now(timezone.utc).isoformat(), _user_context())
        raw = call_hermes_aux(prompt, settings)
        return validate_analysis(raw, email, rule_result)
    except Exception as exc:
        return fallback_analysis(email, rule_result, str(exc))


def call_hermes_aux(prompt: str, settings: dict) -> dict:
    """Call any OpenAI-compatible endpoint. Config-driven, no hardcoded provider."""
    return call_llm(prompt, settings)


def call_llm(prompt: str, settings: dict) -> dict:
    """Call any OpenAI-compatible endpoint. Config-driven, no hardcoded provider."""
    endpoint = settings.get("endpoint")
    model = settings.get("model", "")
    api_key_env = settings.get("api_key_env", "")
    api_key = os.environ.get(api_key_env, "")
    if not endpoint:
        raise RuntimeError("llm.endpoint is required")

    data = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": settings.get("temperature", 0.1),
        "max_tokens": settings.get("max_tokens", 2000),
    }).encode()

    req = urllib.request.Request(endpoint, data=data, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    })

    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=settings.get("timeout_seconds", 90), context=ctx) as resp:
        result = json.loads(resp.read())
        content = result["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        return json.loads(content)


def build_prompt(email: dict, rule_result: dict, now: str, user_context: dict) -> str:
    settings = email_config.get_llm_settings() if email_config else {}
    max_body = int(settings.get("max_body_chars", 12000))
    email_payload = dict(email)
    if email_payload.get("body"):
        email_payload["body"] = email_payload["body"][:max_body]
    timezone_name = (email_config.get_delivery_settings() if email_config else {}).get("timezone", "Asia/Shanghai")
    return f"""You are Hermes Email Watchdog's semantic delivery planner.

Your job is not to classify into a fixed taxonomy. Your job is to understand what this email means to the user and decide what the chat notification must contain so the user does not need to open email again.

Current time: {now}
User timezone: {timezone_name}
Rule pre-classifier result:
{json.dumps(rule_result, ensure_ascii=False, indent=2)}

Email:
{json.dumps(email_payload, ensure_ascii=False, indent=2)}

Principles:
1. Preserve enough information for action. If the email is a school notice, paper/editor feedback, task request, meeting logistics, form request, or anything requiring decisions, use full_body and structure it for reading.
2. Do not show full body when it adds no value. Invoices, receipts, payment confirmations, shipment notices, and routine account notices usually need a summary plus key fields and saved attachments.
3. Verification/security codes should use code_extraction: service/platform, code, expiry if present, sender, subject.
4. Generalize from meaning, not from category names. The rule category is only a hint and may be wrong.
5. Extract deadlines and action items precisely. Resolve relative dates using current time and timezone. If uncertain, keep the original date text and lower confidence.
6. Design reminders only when they help progress: e.g. deadline minus 7d/3d/1d, same-day reminder, or custom progress checkpoints for revisions and multi-step tasks. Do not create reminders for passive FYI mail.
7. Attachment handling should be explicit. Choose download_safe/download_all/download_invoices_only/list_only/none and explain why.
8. If sender/content appears risky, do not recommend opening links or downloading suspicious attachments.
9. Output must be valid JSON matching the schema. No markdown outside JSON.

Return exactly this JSON object:
{{
  "id": string,
  "semantic_category": string,
  "user_relevance": "ignore" | "low" | "medium" | "high" | "urgent",
  "confidence": number,
  "should_notify": boolean,
  "should_show_full_body": boolean,
  "format_decision": "full_body" | "summary" | "code_extraction",
  "formatted_summary": string,
  "action_needed": {{
    "required": boolean,
    "description": string | null,
    "type": "reply" | "read" | "submit_form" | "pay" | "attend" | "revise_document" | "download" | "monitor" | "none",
    "next_step": string | null
  }},
  "deadline": {{
    "has_deadline": boolean,
    "datetime": string | null,
    "date_text": string | null,
    "timezone": string,
    "confidence": number
  }},
  "reminder_schedule": [
    {{"time": string, "kind": string, "message": string}}
  ],
  "attachment_handling": {{
    "policy": "download_all" | "download_safe" | "download_invoices_only" | "list_only" | "none",
    "wanted_types": [string],
    "reason": string
  }},
  "body_rendering": {{
    "header_lines": [string],
    "body_sections": [
      {{"title": string, "content": string, "format": "paragraph" | "code"}}
    ],
    "signature": string | null
  }},
  "risk_notes": [string],
  "llm_notes": string
}}"""


def validate_analysis(raw: dict, email: dict, rule_result: dict) -> dict:
    """Normalize missing fields and enforce enum values."""
    if not isinstance(raw, dict):
        return fallback_analysis(email, rule_result, "non-object llm response")
    fallback = fallback_analysis(email, rule_result, "")
    out = {**fallback, **raw}
    out["id"] = str(out.get("id") or email.get("id") or email.get("msg_id") or "")
    out["user_relevance"] = out.get("user_relevance") if out.get("user_relevance") in RELEVANCE_CHOICES else fallback["user_relevance"]
    out["format_decision"] = out.get("format_decision") if out.get("format_decision") in FORMAT_CHOICES else fallback["format_decision"]
    out["should_notify"] = bool(out.get("should_notify", True))
    out["should_show_full_body"] = bool(out.get("should_show_full_body", out["format_decision"] == "full_body"))

    action = out.get("action_needed") if isinstance(out.get("action_needed"), dict) else {}
    action_type = action.get("type") if action.get("type") in ACTION_TYPES else "none"
    out["action_needed"] = {
        "required": bool(action.get("required", action_type != "none")),
        "description": action.get("description") or None,
        "type": action_type,
        "next_step": action.get("next_step") or None,
    }

    deadline = out.get("deadline") if isinstance(out.get("deadline"), dict) else {}
    timezone_name = (email_config.get_delivery_settings() if email_config else {}).get("timezone", "Asia/Shanghai")
    out["deadline"] = {
        "has_deadline": bool(deadline.get("has_deadline") or deadline.get("datetime") or deadline.get("date_text")),
        "datetime": deadline.get("datetime"),
        "date_text": deadline.get("date_text"),
        "timezone": deadline.get("timezone") or timezone_name,
        "confidence": float(deadline.get("confidence", 0) or 0),
    }

    attachment = out.get("attachment_handling") if isinstance(out.get("attachment_handling"), dict) else {}
    policy = attachment.get("policy") if attachment.get("policy") in ATTACHMENT_POLICIES else fallback["attachment_handling"]["policy"]
    out["attachment_handling"] = {
        "policy": policy,
        "wanted_types": attachment.get("wanted_types") if isinstance(attachment.get("wanted_types"), list) else [],
        "reason": attachment.get("reason") or "",
    }

    rendering = out.get("body_rendering") if isinstance(out.get("body_rendering"), dict) else {}
    sections = rendering.get("body_sections") if isinstance(rendering.get("body_sections"), list) else []
    out["body_rendering"] = {
        "header_lines": rendering.get("header_lines") if isinstance(rendering.get("header_lines"), list) else [],
        "body_sections": [
            {
                "title": str(s.get("title") or "Body"),
                "content": str(s.get("content") or ""),
                "format": s.get("format") if s.get("format") in {"paragraph", "code"} else "paragraph",
            }
            for s in sections if isinstance(s, dict)
        ],
        "signature": rendering.get("signature"),
    }
    out["reminder_schedule"] = out.get("reminder_schedule") if isinstance(out.get("reminder_schedule"), list) else []
    out["risk_notes"] = out.get("risk_notes") if isinstance(out.get("risk_notes"), list) else []
    out["confidence"] = float(out.get("confidence", 0.5) or 0.5)
    return out


def fallback_analysis(email: dict, rule_result: dict, reason: str) -> dict:
    category = (rule_result or {}).get("category") or "other"
    action = (rule_result or {}).get("action") or "needs_llm"
    body = _clean_body(email.get("body", ""))
    has_attachment = bool(email.get("has_attachments") or email.get("has_attachment") or email.get("attachments"))
    fmt = "summary"
    semantic = "other"
    relevance = "medium"
    attachment_policy = "none"
    if action == "simple_code" or category == "验证码":
        fmt = "code_extraction"
        semantic = "verification_code"
        relevance = "urgent"
    elif "发票" in category or "invoice" in category.lower():
        semantic = "invoice_receipt"
        relevance = "high"
        attachment_policy = "download_invoices_only" if has_attachment else "none"
    elif any(word in category for word in ("学校", "论文", "会议", "表格", "个人")):
        fmt = "full_body"
        semantic = "school_notice" if "学校" in category else "personal_task"
        relevance = "high" if "论文" in category else "medium"
        attachment_policy = "download_safe" if has_attachment else "none"
    elif has_attachment:
        attachment_policy = "download_safe"

    return {
        "id": str(email.get("id") or email.get("msg_id") or ""),
        "semantic_category": semantic,
        "user_relevance": relevance,
        "confidence": 0.35 if reason else 0.6,
        "should_notify": action != "skip",
        "should_show_full_body": fmt == "full_body",
        "format_decision": fmt,
        "formatted_summary": (rule_result or {}).get("summary") or email.get("subject") or body[:220],
        "action_needed": {"required": False, "description": None, "type": "none", "next_step": None},
        "deadline": {"has_deadline": False, "datetime": None, "date_text": None, "timezone": "Asia/Shanghai", "confidence": 0},
        "reminder_schedule": [],
        "attachment_handling": {"policy": attachment_policy, "wanted_types": [], "reason": reason or "fallback rule policy"},
        "body_rendering": {
            "header_lines": [],
            "body_sections": [{"title": "Body", "content": body, "format": "paragraph"}] if fmt == "full_body" and body else [],
            "signature": None,
        },
        "risk_notes": [reason] if reason else [],
        "llm_notes": f"fallback analysis: {reason}" if reason else "fallback analysis",
    }


def process_pending():
    """Backfill helper for pending messages; main watchdog calls analyze_email synchronously."""
    if not email_store:
        return ""
    rows = email_store._get_conn().execute(
        """SELECT * FROM messages
           WHERE push_status='pending' AND (analysis_json IS NULL OR analysis_json='')
           ORDER BY date_sent DESC LIMIT 10"""
    ).fetchall()
    count = 0
    for row in rows:
        msg = dict(row)
        rule_result = {"category": msg.get("rule_category") or "other", "action": "needs_llm"}
        analysis = analyze_email(msg, rule_result)
        email_store.update_message_fields(msg["id"], {
            "analysis_json": json.dumps(analysis, ensure_ascii=False),
            "semantic_category": analysis.get("semantic_category"),
            "format_decision": analysis.get("format_decision"),
        })
        count += 1
    return f"LLM backfill processed {count} messages" if count else ""


def _user_context():
    return {"account_emails": email_config.get_account_emails() if email_config else []}


def _clean_body(body):
    text = (body or "").replace("\\n", "\n").replace("\\t", "\t")
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'&[a-z]+;', ' ', text)
    text = re.sub(r'\s+\n', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


if __name__ == "__main__":
    output = process_pending()
    if output:
        print(output)
