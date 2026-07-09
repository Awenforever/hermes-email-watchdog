#!/usr/bin/env python3
"""Email delivery planner side effects and chat-ready formatting."""

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

try:
    import email_config
    import email_store
except ImportError:
    email_config = None
    email_store = None

TRUST_DOWNLOAD_TLDS = (".edu.cn", ".ac.cn", ".gov.cn", ".com", ".org", ".net", ".cn")
SUSPICIOUS_TLDS = (".tk", ".ml", ".ga", ".cf", ".gq", ".xyz", ".top", ".club")


def deliver_email(email: dict, rule_result: dict, analysis: dict, account: dict) -> dict:
    """Apply delivery decision and side effects. Returns notification payload."""
    analysis = analysis or {}
    if analysis.get("should_notify") is False:
        _persist_delivery(email, analysis, "", "skipped")
        return {"notification_text": "", "attachments": [], "schedule": [], "status": "skipped"}

    attachments = download_attachments(email, analysis, account)
    schedule = upsert_schedule(email, analysis)
    cron_entries = install_reminder_cron(schedule)
    text = format_notification(email, analysis, attachments, schedule)
    _persist_delivery(email, analysis, text, "pushed")
    return {
        "notification_text": text,
        "attachments": attachments,
        "schedule": schedule,
        "cron_entries": cron_entries,
        "status": "pushed",
    }


def format_notification(email: dict, analysis: dict, attachments: list, schedule: list) -> str:
    """Return chat-ready text, not channel-specific chunks."""
    importance = analysis.get("user_relevance") or email.get("importance") or "medium"
    category = analysis.get("semantic_category") or analysis.get("final_category") or email.get("rule_category") or "email"
    priority = {"urgent": "urgent", "high": "high", "medium": "medium", "low": "low", "ignore": "low"}.get(importance, importance)
    sender = _sender_display(email)
    subject = email.get("subject", "")
    fmt = analysis.get("format_decision") or ("full_body" if analysis.get("should_show_full_body") else "summary")

    lines = [f"[{email.get('account') or ''}] {priority} | {category}".strip()]
    lines.append(f"From: {sender}")
    lines.append(f"Subject: {subject}")

    if fmt == "code_extraction":
        code = analysis.get("code") or _extract_code(f"{subject}\n{email.get('body','')}")
        service = analysis.get("service") or category or "verification"
        lines.extend(["", f"Code: {code or '(not found)'}", f"Service: {service}"])
        expiry = analysis.get("expiry") or analysis.get("deadline", {}).get("date_text")
        if expiry:
            lines.append(f"Expiry: {expiry}")
        return "\n".join(lines).strip()

    summary = analysis.get("formatted_summary") or email.get("summary") or _clean_body(email.get("body", ""))[:300]
    if summary:
        lines.extend(["", "Summary:", summary.strip()])

    action = analysis.get("action_needed") or {}
    action_text = action.get("description") or action.get("next_step") or ""
    if action_text:
        lines.extend(["", "Action:", action_text.strip()])

    deadline = analysis.get("deadline") or {}
    if deadline.get("has_deadline") or deadline.get("datetime") or deadline.get("date_text"):
        lines.extend(["", "Deadline:", deadline.get("datetime") or deadline.get("date_text") or "unspecified"])

    if fmt == "full_body":
        body_rendering = analysis.get("body_rendering") or {}
        header_lines = [str(x).strip() for x in body_rendering.get("header_lines", []) if str(x).strip()]
        if header_lines:
            lines.extend(["", "Header:", *header_lines])
        sections = body_rendering.get("body_sections") or []
        if sections:
            lines.extend(["", "Body:"])
            for section in sections:
                title = section.get("title") or "Section"
                content = (section.get("content") or "").strip()
                if not content:
                    continue
                if section.get("format") == "code":
                    lines.extend(["", f"Structured content: {title}", "```", content, "```"])
                else:
                    lines.extend(["", f"{title}:", content])
        else:
            body = _clean_body(email.get("body", ""))
            if body:
                lines.extend(["", "Body:", body])
        signature = body_rendering.get("signature")
        if signature:
            lines.extend(["", "Signature:", str(signature).strip()])

    if attachments:
        lines.extend(["", "Attachments:"])
        for att in attachments:
            if att.get("local_path"):
                lines.append(f"- {att['local_path']}")
            else:
                lines.append(f"- {att.get('filename') or att.get('name') or '(listed only)'} ({att.get('download_status','listed')})")

    if schedule:
        lines.extend(["", "Reminders:"])
        for item in schedule:
            for reminder in item.get("reminders", []):
                lines.append(f"- {reminder.get('time')} {reminder.get('kind')}")

    return "\n".join(lines).strip()


def download_attachments(email: dict, analysis: dict, account: dict) -> list:
    """Download allowed attachments and persist paths in email_store.attachments."""
    attachments = email.get("attachments") or []
    has_attachments = email.get("has_attachments") or email.get("has_attachment") or bool(attachments)
    if not has_attachments:
        return []

    policy = (analysis.get("attachment_handling") or {}).get("policy") or "none"
    policy = _policy_after_domain_guard(policy, email)
    if policy in ("none", "list_only"):
        listed = _list_attachments(email, attachments, policy)
        for att in listed:
            _persist_attachment(email, att)
        return listed

    settings = email_config.get_delivery_settings() if email_config else {}
    if settings.get("auto_download_attachments", True) is False:
        return _list_attachments(email, attachments, "listed")

    save_root = _save_root(policy)
    save_dir = os.path.join(save_root, datetime.now().strftime("%Y-%m"))
    os.makedirs(save_dir, exist_ok=True)
    saved_paths = []

    if account.get("type") == "himalaya":
        cfg = account.get("himalaya_config") or account.get("config")
        if cfg:
            saved_paths = _download_himalaya(cfg, email.get("msg_id") or email.get("id"), save_dir)
    elif account.get("type") == "agently":
        for att in attachments:
            aid = att.get("attachment_id") or att.get("id")
            if aid:
                path = _download_agently(email.get("msg_id") or email.get("id"), aid, save_dir)
                if path:
                    saved_paths.append(path)

    result = []
    if saved_paths:
        for path in saved_paths:
            att = {
                "filename": os.path.basename(path),
                "local_path": path,
                "download_status": "downloaded",
                "policy": policy,
            }
            _persist_attachment(email, att)
            result.append(att)
    else:
        result = _list_attachments(email, attachments, "download_failed")
        for att in result:
            _persist_attachment(email, att)
    return result


def upsert_schedule(email: dict, analysis: dict) -> list:
    """Persist deadlines/reminders in schedule store and email_store actions."""
    if not email_store:
        return []
    settings = email_config.get_delivery_settings() if email_config else {}
    if settings.get("create_reminders", True) is False:
        return []

    deadline = analysis.get("deadline") or {}
    reminders = analysis.get("reminder_schedule") or []
    if not (deadline.get("has_deadline") or deadline.get("datetime") or reminders):
        return []

    msg_id = email.get("id") or email.get("msg_id")
    sched_id = f"{msg_id}:main"
    action = analysis.get("action_needed") or {}
    item = {
        "id": sched_id,
        "message_id": msg_id,
        "title": analysis.get("formatted_summary") or email.get("subject", ""),
        "action_needed": action.get("description") or action.get("next_step") or "",
        "deadline": deadline.get("datetime"),
        "timezone": deadline.get("timezone") or settings.get("timezone", "Asia/Shanghai"),
        "status": "active",
        "reminder_json": json.dumps(reminders, ensure_ascii=False),
    }
    email_store.upsert_schedule(item)
    return [{**item, "reminders": reminders}]


def install_reminder_cron(schedule_items: list) -> list:
    """Create/update managed reminder cron entries or return planned entries if disabled."""
    settings = email_config.get_delivery_settings() if email_config else {}
    entries = []
    for item in schedule_items:
        for reminder in item.get("reminders", []):
            entries.append({
                "schedule_id": item.get("id"),
                "time": reminder.get("time"),
                "kind": reminder.get("kind"),
                "managed": bool(settings.get("managed_cron", False)),
            })
    return entries


def _policy_after_domain_guard(policy: str, email: dict) -> str:
    domain = (email.get("from_domain") or _domain(email.get("from_addr") or email.get("from_email") or "")).lower()
    if not domain:
        return "list_only" if policy not in ("none", "list_only") else policy
    if any(domain.endswith(tld) for tld in SUSPICIOUS_TLDS):
        return "list_only"
    if any(domain.endswith(tld) for tld in TRUST_DOWNLOAD_TLDS):
        return policy
    return "list_only" if policy not in ("none", "list_only") else policy


def _download_himalaya(config_path, msg_id, save_dir):
    before = _snapshot(save_dir)
    cmd = ["himalaya", "-c", config_path, "attachment", "download", str(msg_id), "--downloads-dir", save_dir]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except Exception:
        return []
    if result.returncode != 0:
        return []
    after = _snapshot(save_dir)
    return sorted(after - before)


def _download_agently(msg_id, att_id, save_dir):
    cmd = ["agently-cli", "attachment", "+download", "--msg", str(msg_id), "--att", str(att_id), "--output", save_dir]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except Exception:
        return None
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
        return data.get("data", {}).get("saved_to")
    except Exception:
        return None


def _snapshot(path):
    if not os.path.isdir(path):
        return set()
    return {os.path.join(path, name) for name in os.listdir(path) if os.path.isfile(os.path.join(path, name))}


def _save_root(policy):
    if email_config:
        return email_config.get_path("invoice_dir" if policy == "download_invoices_only" else "attachment_dir")
    return os.path.expanduser("~/Documents/Invoices" if policy == "download_invoices_only" else "~/Documents/EmailAttachments")


def _list_attachments(email, attachments, status):
    if attachments:
        return [
            {
                "filename": a.get("filename") or a.get("name") or a.get("id") or "attachment",
                "download_status": status,
                "policy": status,
            }
            for a in attachments
        ]
    return [{"filename": "(attachments present)", "download_status": status, "policy": status}]


def _persist_attachment(email, att):
    if not email_store:
        return
    msg_id = email.get("id") or email.get("msg_id")
    filename = att.get("filename") or "attachment"
    local_path = att.get("local_path") or ""
    att_id = hashlib.sha256(f"{msg_id}:{filename}:{local_path}".encode()).hexdigest()[:24]
    email_store.add_attachment({
        "id": att_id,
        "message_id": msg_id,
        "filename": filename,
        "local_path": local_path,
        "download_status": att.get("download_status", "listed"),
        "source": "watchdog",
    })


def _persist_delivery(email, analysis, text, status):
    if not email_store:
        return
    msg_id = email.get("id") or email.get("msg_id")
    deadline = analysis.get("deadline") or {}
    action = analysis.get("action_needed") or {}
    data = {
        "push_status": status,
        "llm_category": analysis.get("semantic_category", ""),
        "final_category": analysis.get("semantic_category") or email.get("rule_category", ""),
        "semantic_category": analysis.get("semantic_category", ""),
        "importance": analysis.get("user_relevance", "medium"),
        "needs_reply": 1 if action.get("type") == "reply" or action.get("required") else 0,
        "has_deadline": 1 if deadline.get("has_deadline") or deadline.get("datetime") else 0,
        "deadline": deadline.get("datetime"),
        "deadline_timezone": deadline.get("timezone"),
        "summary_short": (analysis.get("formatted_summary") or "")[:500],
        "summary_long": analysis.get("formatted_summary") or "",
        "action_summary": action.get("description") or action.get("next_step") or "",
        "format_decision": analysis.get("format_decision"),
        "analysis_json": json.dumps(analysis, ensure_ascii=False),
        "attachment_policy": (analysis.get("attachment_handling") or {}).get("policy"),
        "delivered_text_hash": hashlib.sha256((text or "").encode()).hexdigest() if text else "",
    }
    email_store.update_message_fields(msg_id, data)
    if status == "pushed":
        email_store.mark_pushed(msg_id)


def _clean_body(body):
    text = (body or "").replace("\\n", "\n").replace("\\t", "\t")
    text = re.sub(r'^(?:From|To|Cc|Bcc|Subject|Date|Reply-To|Message-ID|MIME-Version|Content-Type|Content-Transfer-Encoding|Return-Path|Received|X-[A-Za-z-]+):[^\n]*\n?', '', text, flags=re.MULTILINE | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'&[a-z]+;', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _extract_code(text):
    for pat in [r"(?:code|码|验证码)[：:\s]*(\d{4,8})", r"\b(\d{6})\b", r"(\d{4,8})"]:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(1)
    return ""


def _sender_display(email):
    name = (email.get("from_name") or "").strip('"\' ')
    addr = email.get("from_addr") or email.get("from_email") or ""
    return f"{name} <{addr}>" if name and addr and name != addr else (name or addr or "?")


def _domain(addr):
    return addr.split("@", 1)[1] if "@" in addr else ""
