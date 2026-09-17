#!/usr/bin/env python3
"""
Hermes Email Watchdog decision-engine feature extractor.
Shadow-only helper: no mailbox writes, no seen writes, no delivery changes.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from typing import Any, Dict, List

MAX_BODY_CHARS_FOR_LLM = 6000
MAX_BODY_CHARS_FOR_FEATURES = 12000


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def sha256_text(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8", "replace")).hexdigest()


def clean_text(value: Any, limit: int | None = None) -> str:
    text = _text(value)
    text = text.replace("\\n", "\n").replace("\\t", "\t")
    text = html.unescape(text)
    text = re.sub(r"(?i)<(br|br/|br\s*/)>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|tr|h[1-6])\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00a0", " ")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    if limit is not None and len(text) > limit:
        return text[:limit]
    return text


def inline_text(value: Any, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", clean_text(value)).strip()
    if len(text) > limit:
        return text[: max(0, limit - 1)].rstrip() + "…"
    return text


def sender_domain(addr: Any) -> str:
    text = _text(addr).strip().lower()
    # Accept "Name <<redacted-email>>" too.
    m = re.search(r"<([^<>@\s]+@[^<>\s]+)>", text)
    if m:
        text = m.group(1)
    if "@" not in text:
        return ""
    return text.rsplit("@", 1)[-1].strip(" >")


def sender_email(addr: Any) -> str:
    text = _text(addr).strip().lower()
    m = re.search(r"<([^<>@\s]+@[^<>\s]+)>", text)
    if m:
        return m.group(1)
    m = re.search(r"\b[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+\b", text)
    return m.group(0) if m else ""


def tokenize(text: str, max_tokens: int = 120) -> List[str]:
    tokens: List[str] = []
    # English/number-ish tokens plus Chinese phrase chunks.
    for token in re.findall(r"[A-Za-z][A-Za-z0-9_\-]{1,}|[0-9]{2,}|[\u4e00-\u9fff]{2,}", text or ""):
        token = token.lower().strip("_-")
        if 2 <= len(token) <= 48:
            tokens.append(token)
    seen = set()
    out = []
    for t in tokens:
        if t not in seen:
            out.append(t)
            seen.add(t)
        if len(out) >= max_tokens:
            break
    return out


_CODE_CONTEXT_RE = re.compile(
    r"(?i)(验证码|校验码|动态口令|一次性密码|登录码|安全码|认证码|短信码|"
    r"\botp\b|one[- ]time password|verification code|security code|"
    r"authentication code|auth code|passcode|\b2fa\b)"
)


def extract_code_candidates(text: str) -> List[str]:
    """Return only 4-8 digit values grounded by nearby authentication context.

    Bare years, compact dates, timestamps, order numbers, paper counts and other
    numeric facts must never enter the verification-code fast path.
    """
    source = text or ""
    out: List[str] = []
    seen = set()
    for match in re.finditer(r"(?<!\d)(\d{4,8})(?!\d)", source):
        start = max(0, match.start() - 96)
        end = min(len(source), match.end() + 96)
        window = source[start:end]
        if not _CODE_CONTEXT_RE.search(window):
            continue
        code = match.group(1)
        if code not in seen:
            out.append(code)
            seen.add(code)
        if len(out) >= 8:
            break
    return out


def _attachment_profile(email: Dict[str, Any]) -> Dict[str, Any]:
    attachments = email.get("attachments") or email.get("attachment_list") or []
    names = []
    if isinstance(attachments, list):
        for item in attachments[:20]:
            if isinstance(item, dict):
                names.append(_text(item.get("filename") or item.get("name") or item.get("path")))
            else:
                names.append(_text(item))
    has_attachment = bool(email.get("has_attachments") or email.get("has_attachment") or names)
    suffixes = []
    for name in names:
        m = re.search(r"\.([A-Za-z0-9]{1,8})$", name)
        if m:
            suffixes.append(m.group(1).lower())
    return {
        "has_attachments": has_attachment,
        "count": len(names),
        "suffixes": sorted(set(suffixes))[:20],
        "name_hashes": [sha256_text(n)[:16] for n in names[:20] if n],
    }


def body_shape(subject: str, body_raw: str, body: str) -> Dict[str, Any]:
    urls = re.findall(r"https?://[^\s<>'\")]+", body_raw or body, flags=re.I)
    tracking = [u for u in urls if re.search(r"(utm_|track|click|redirect|unsubscribe|open\.gif|pixel)", u, re.I)]
    lines = body.splitlines()
    return {
        "body_chars": len(body),
        "body_lines": len(lines),
        "subject_chars": len(subject),
        "url_count": len(urls),
        "tracking_url_count": len(tracking),
        "html_like": bool(re.search(r"(?i)<html|<body|<table|</div>|</p>", body_raw or "")),
        "markdown_heading_count": len(re.findall(r"(?m)^\s{0,3}#{1,6}\s+", body)),
        "blockquote_line_count": len([ln for ln in lines if ln.strip().startswith(">")]),
        "table_hint": bool(re.search(r"(\|.+\|)|(<table)|(\t.+\t)", body_raw or body, re.I)),
        "reply_forward_hint": bool(re.search(r"(?i)(^|\n)\s*(from|发件人|sent|date|subject|主题)\s*[:：]", body[:4000])),
        "newsletter_hint": bool(re.search(r"(?i)(unsubscribe|退订|view in browser|manage preferences)", body[:8000])),
        "deadline_hint": bool(re.search(r"(?i)(deadline|due by|before|截止|请于|不晚于|报名|提交|确认|完成)", subject + "\n" + body[:4000])),
        "action_hint": bool(re.search(r"(?i)(please|kindly|submit|confirm|revise|review|pay|download|请|提交|确认|回复|修改|缴费|下载|填写)", subject + "\n" + body[:4000])),
    }


def message_key(email: Dict[str, Any]) -> str:
    account = _text(email.get("account") or email.get("account_id") or "unknown")
    msg_id = _text(email.get("msg_id") or email.get("id") or email.get("message_id") or "")
    if msg_id:
        return f"{account}:{msg_id}"
    seed = "|".join([
        account,
        _text(email.get("from_addr") or email.get("from_email") or email.get("sender") or ""),
        _text(email.get("subject") or ""),
        _text(email.get("date_sent") or email.get("date") or ""),
    ])
    return f"{account}:sha256:{sha256_text(seed)[:24]}"


def extract_features(email: Dict[str, Any]) -> Dict[str, Any]:
    email = email or {}
    subject = clean_text(email.get("subject") or "", 600)
    body_raw = _text(email.get("body") or email.get("body_plain") or email.get("plain") or email.get("text") or "")
    body = clean_text(body_raw, MAX_BODY_CHARS_FOR_FEATURES)
    from_addr = _text(email.get("from_addr") or email.get("from_email") or email.get("sender") or "")
    from_name = _text(email.get("from_name") or email.get("sender_name") or "")
    sender = sender_email(from_addr)
    domain = sender_domain(from_addr)
    combined = f"{subject}\n{body}"
    subj_tokens = tokenize(subject, 80)
    body_tokens = tokenize(body[:4000], 160)
    all_tokens = sorted(set(subj_tokens + body_tokens))
    return {
        "message_key": message_key(email),
        "account": _text(email.get("account") or email.get("account_id") or ""),
        "msg_id": _text(email.get("msg_id") or email.get("id") or email.get("message_id") or ""),
        "subject_hash": sha256_text(subject),
        "subject_preview": inline_text(subject, 180),
        "sender_hash": sha256_text(sender or from_addr.lower()),
        "sender_domain": domain,
        "from_name_hash": sha256_text(from_name.lower()) if from_name else "",
        "subject_tokens": subj_tokens,
        "body_tokens": body_tokens[:120],
        "tokens": all_tokens[:220],
        "code_candidates": extract_code_candidates(combined),
        "attachment_profile": _attachment_profile(email),
        "body_shape": body_shape(subject, body_raw, body),
        "body_excerpt_hash": sha256_text(body[:2000]),
        "llm_payload": {
            "from_domain": domain,
            "from_name": inline_text(from_name, 80),
            "subject": subject,
            "body_excerpt": body[:MAX_BODY_CHARS_FOR_LLM],
            "attachments": _attachment_profile(email),
        },
    }


def compact_features_for_storage(features: Dict[str, Any]) -> Dict[str, Any]:
    """Remove raw LLM body payload before persistent storage."""
    out = dict(features or {})
    payload = dict(out.pop("llm_payload", {}) or {})
    if payload:
        out["llm_payload_hash"] = sha256_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))
        out["llm_subject_preview"] = inline_text(payload.get("subject") or "", 180)
        out["llm_body_excerpt_hash"] = sha256_text(payload.get("body_excerpt") or "")
    return out
