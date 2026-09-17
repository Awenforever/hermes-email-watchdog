#!/usr/bin/env python3
"""Adaptive notification renderer for Hermes Email Watchdog.

Phase 2 contract:
- Build a shadow-only adaptive notification from SemanticDecisionV2.
- Never mutate production delivery output.
- Never call Ollama, Weixin, mailbox tools, or schedulers.
- Original text comes only from deterministic cleaning of the real email body.
- Persist shadow comparison data for later canary review.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
import os
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

try:
    import email_config
except Exception:  # pragma: no cover - defensive import for isolated tests
    email_config = None

MARKER = "EMAIL_WATCHDOG_ADAPTIVE_RENDERER_V1E"
RENDERER_VERSION = "adaptive_v1e"
DEFAULT_DB_PATH = Path(
    os.environ.get(
        "EMAIL_LEARNING_DB",
        "/opt/data/.hermes-home/.hermes/email_learning/email_learning.sqlite",
    )
)

_DEFAULT_SETTINGS: Dict[str, Any] = {
    "renderer": "adaptive_v1e",
    "mode": "shadow",
    "original_policy": "auto",
    "original_max_chars": 5000,
    "show_priority": True,
    "show_category": True,
    "show_time": True,
    "show_debug_reason": False,
    "suppress_redundant_summary": True,
}

_ALLOWED_MODES = {
    "summary_only",
    "summary_plus_original",
    "original_only",
    "code_card",
    "finance_card",
    "event_card",
    "deadline_card",
}
_ALLOWED_ORIGINAL = {"none", "full", "excerpt"}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8", "replace")).hexdigest()


def _text(value: Any, limit: int = 0) -> str:
    if value is None:
        return ""
    text = str(value).replace("\x00", "").strip()
    if limit and len(text) > limit:
        return text[:limit]
    return text


def _mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: Any) -> List[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _settings(override: Mapping[str, Any] | None = None) -> Dict[str, Any]:
    result = dict(_DEFAULT_SETTINGS)
    if email_config is not None:
        try:
            getter = getattr(email_config, "get_notification_settings", None)
            configured = getter() if callable(getter) else {}
            if isinstance(configured, Mapping):
                result.update(configured)
        except Exception:
            pass
    if isinstance(override, Mapping):
        result.update(override)
    result["renderer"] = _text(result.get("renderer"), 64) or "adaptive_v1e"
    result["mode"] = _text(result.get("mode"), 32).lower() or "shadow"
    result["original_policy"] = _text(result.get("original_policy"), 32).lower() or "auto"
    try:
        result["original_max_chars"] = max(200, min(20000, int(result.get("original_max_chars") or 5000)))
    except (TypeError, ValueError):
        result["original_max_chars"] = 5000
    for key in ("show_priority", "show_category", "show_time", "show_debug_reason", "suppress_redundant_summary"):
        result[key] = bool(result.get(key, _DEFAULT_SETTINGS[key]))
    return result


def _body_source(email: Mapping[str, Any]) -> str:
    for key in ("cleaned_body", "body_plain", "plain", "text", "body"):
        value = email.get(key)
        if value:
            return str(value)
    return ""


def clean_body(value: Any) -> str:
    """Deterministically clean real message text without model rewriting."""
    text = _text(value)
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    replacements = {
        "&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">",
        "&quot;": '"', "&#39;": "'",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    lines: List[str] = []
    blank = False
    footer_started = False
    for raw in text.splitlines():
        line = re.sub(r"[\t\u00a0 ]+", " ", raw).strip()
        low = line.casefold()
        if re.match(r"^(from|to|cc|bcc|subject|date|sent):\s", low):
            continue
        if re.match(r"^(发件人|收件人|抄送|主题|日期|发送时间)[:：]", line):
            continue
        if re.match(r"^[-_]{5,}$", line):
            continue
        if low.startswith(("unsubscribe", "view this email in your browser", "privacy policy")):
            footer_started = True
        if line.startswith(("取消订阅", "退订", "隐私政策")):
            footer_started = True
        if (
            re.search(r"(?i)sent\s+via\s+agent\s*mail|report\s+spam.*unsubscribe", line)
            or re.search(r"通过\s*Agent\s*Mail\s*自动发送|举报退订", line, re.I)
            or re.fullmatch(r"Hermes\s*ᥫᩣ", line, re.I)
        ):
            footer_started = True
        if footer_started:
            continue
        if not line:
            if lines and not blank:
                lines.append("")
            blank = True
            continue
        blank = False
        lines.append(line)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines).strip()


def _norm(value: Any) -> str:
    text = clean_body(value).casefold()
    text = re.sub(r"[`*_#>\-•·\s\W]+", "", text, flags=re.UNICODE)
    return text


def _token_set(value: Any) -> set[str]:
    text = clean_body(value).casefold()
    ascii_tokens = re.findall(r"[a-z0-9]{2,}", text)
    cn_tokens = re.findall(r"[\u4e00-\u9fff]{2,}", text)
    return set(ascii_tokens + cn_tokens)


def _redundant(a: Any, b: Any) -> bool:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
    if len(shorter) >= 8 and shorter in longer and len(shorter) / max(1, len(longer)) >= 0.72:
        return True
    ta, tb = _token_set(a), _token_set(b)
    if ta and tb:
        overlap = len(ta & tb) / max(1, min(len(ta), len(tb)))
        if overlap >= 0.9 and abs(len(ta) - len(tb)) <= 2:
            return True
    return False


def _priority_label(level: Any) -> str:
    return {
        "critical": "紧急",
        "high": "重要",
        "normal": "普通",
        "low": "低优先级",
    }.get(_text(level, 32).lower(), "普通")


def _category_label(decision: Mapping[str, Any]) -> str:
    classification = _mapping(decision.get("classification"))
    return _text(classification.get("label"), 80) or _text(classification.get("category"), 80) or "邮件"


def _format_time(value: Any) -> str:
    raw = _text(value, 180)
    if not raw:
        return ""
    try:
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            return raw
        return dt.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M SGT")
    except Exception:
        return raw


def _sender(email: Mapping[str, Any]) -> str:
    name = _text(email.get("from_name"), 100).strip('"')
    address = _text(email.get("from_addr") or email.get("from_email") or email.get("sender"), 180)
    if name and address and name.casefold() not in address.casefold():
        return f"{name} <{address}>"
    return name or address or "未知发件人"


def _account(email: Mapping[str, Any], account: Mapping[str, Any] | None) -> str:
    account = account or {}
    return _text(email.get("account") or account.get("name") or account.get("id") or account.get("email"), 80) or "Email"


def _extract_code(text: str) -> str:
    candidates = re.findall(r"(?<!\d)(\d{4,8})(?!\d)", text or "")
    return candidates[0] if candidates else ""


def _normalise_validity(value: str, unit: str) -> str:
    unit_low = unit.casefold()
    unit_map = {
        "秒": "秒", "秒钟": "秒", "second": "秒", "seconds": "秒",
        "分钟": "分钟", "分": "分钟", "minute": "分钟", "minutes": "分钟",
        "小时": "小时", "hour": "小时", "hours": "小时",
        "天": "天", "日": "天", "day": "天", "days": "天",
    }
    return f"{value} {unit_map.get(unit_low, unit)}"


def _extract_code_validity(text: str) -> str:
    """Extract a grounded validity window when the source explicitly states one."""
    source = clean_body(text)
    if not source:
        return ""
    patterns = [
        re.compile(r"有效期(?:为|是)?\s*[:：]?\s*(\d{1,3})\s*(秒钟?|分钟|分|小时|天|日)", re.I),
        re.compile(r"(\d{1,3})\s*(秒钟?|分钟|分|小时|天|日)(?:内)?有效", re.I),
        re.compile(r"(?:请)?在\s*(\d{1,3})\s*(秒钟?|分钟|分|小时|天|日)内(?:使用|完成)", re.I),
        re.compile(r"(?:valid\s+for|expires?\s+in)\s*(\d{1,3})\s*(seconds?|minutes?|hours?|days?)", re.I),
    ]
    for pattern in patterns:
        match = pattern.search(source)
        if match:
            return _normalise_validity(match.group(1), match.group(2))
    return ""


def _extract_amounts(text: str) -> List[str]:
    pattern = re.compile(
        r"(?<!\w)(?:CNY|RMB|USD|SGD|EUR|GBP|JPY|HKD|AUD|CAD|¥|￥|\$|€|£)\s?\d[\d,]*(?:\.\d{1,2})?"
        r"|(?<!\w)\d[\d,]*(?:\.\d{1,2})?\s?(?:元|人民币|美元|新加坡元|欧元|英镑|日元|港币)",
        re.IGNORECASE,
    )
    out: List[str] = []
    for match in pattern.findall(text or ""):
        item = _text(match, 80)
        if item and item not in out:
            out.append(item)
        if len(out) >= 4:
            break
    return out


def _attachment_names(email: Mapping[str, Any], delivery: Mapping[str, Any], decision: Mapping[str, Any]) -> List[str]:
    out: List[str] = []
    sources: List[Any] = []
    sources.extend(_list(delivery.get("attachments")))
    sources.extend(_list(email.get("attachments")))
    semantic = _mapping(decision.get("attachments"))
    sources.extend(_list(semantic.get("important_names")))
    for item in sources:
        if isinstance(item, Mapping):
            name = _text(item.get("filename") or item.get("name"), 220)
        else:
            name = _text(item, 220)
        if not name or name.casefold() in {"listed", "unknown", "attachment present", "attachments present"}:
            continue
        if name not in out:
            out.append(name)
        if len(out) >= 8:
            break
    return out


def _schedule_lines(delivery: Mapping[str, Any]) -> List[str]:
    out: List[str] = []
    for item in _list(delivery.get("schedule")):
        if not isinstance(item, Mapping):
            continue
        when = _text(item.get("time") or item.get("datetime") or item.get("when"), 160)
        message = _text(item.get("message") or item.get("title") or item.get("kind"), 260)
        line = "｜".join(part for part in (_format_time(when) or when, message) if part)
        if line and line not in out:
            out.append(line)
    return out


def _blockquote_lines(body_lines: Sequence[str]) -> List[str]:
    """Render block content as one visually separated Markdown quote block."""
    out: List[str] = []
    for item in body_lines:
        value = _text(item)
        if not value:
            continue
        for raw in value.splitlines():
            line = raw.rstrip()
            out.append(f"> {line}" if line else ">")
    return out


def _append_block(lines: List[str], blocks: List[str], title: str, body_lines: Sequence[str]) -> None:
    quoted = _blockquote_lines(body_lines)
    if not quoted:
        return
    lines.extend(["", f"**{title}**"])
    lines.extend(quoted)
    blocks.append(title)


def _append_code_block(lines: List[str], blocks: List[str], title: str, body_lines: Sequence[str]) -> None:
    """Render only genuinely copy-oriented code, command, log, or raw structured text."""
    clean = [_text(item) for item in body_lines if _text(item)]
    if not clean:
        return
    lines.extend(["", f"**{title}**", "```text"])
    lines.extend(clean)
    lines.append("```")
    blocks.append(title)


def _summary_lines(
    subject: str,
    decision: Mapping[str, Any],
    *,
    suppress_redundant: bool,
) -> Tuple[List[str], int]:
    notification = _mapping(decision.get("notification"))
    style = _text(notification.get("summary_style"), 32).lower()
    suppressions = 0
    if style == "paragraph":
        summary = clean_body(notification.get("summary"))
        if summary and suppress_redundant and _redundant(subject, summary):
            return [], 1
        return ([summary] if summary else []), suppressions
    if style == "bullets":
        out: List[str] = []
        seen: List[str] = []
        for raw in _list(notification.get("key_points")):
            point = clean_body(raw)
            if not point:
                continue
            if suppress_redundant and _redundant(subject, point):
                suppressions += 1
                continue
            if any(_redundant(point, old) for old in seen):
                suppressions += 1
                continue
            seen.append(point)
            out.append(f"- {point}")
        return out, suppressions
    return [], suppressions



_ACADEMIC_PRIMARY_HEADING_RE = re.compile(
    r"(?i)总体判断|本周判断|摘要|概览|结论|重点|主要进展|研究进展|"
    r"summary|overview|highlights?|conclusions?|findings?"
)
_ACADEMIC_NEXT_HEADING_RE = re.compile(
    r"(?i)下一步|后续|建议|观察重点|待跟进|next\s+steps?|recommendations?"
)
_ACADEMIC_SKIP_LINE_RE = re.compile(
    r"(?i)^(?:生成时间|更新时间|发送时间|作者|时间|doi|链接|url|来源)\s*[:：]|"
    r"^(?:raw_candidates|candidate_deduped|cross_week_deduped|hard_filter_passed|"
    r"selected_count|rejected_count|queries)\s*[:：]|"
    r"^(?:https?://|doi\s*:)"
)


def _academic_digest_highlight_lines(
    body: str,
    subject: str,
    *,
    max_items: int = 3,
) -> List[str]:
    """Extract a small grounded digest layer from the real academic-report body.

    The output is deterministic and consists only of source-body text. It is
    used when the model summary was safely replaced by a subject-only receipt
    and would otherwise disappear as a duplicate of the subject.
    """
    source = clean_body(body)
    if not source:
        return []

    candidates: List[Tuple[int, int, str]] = []
    current_heading = ""
    order = 0
    for raw in source.splitlines():
        line = _text(raw, 360)
        if not line:
            continue
        heading_match = re.match(r"^#{1,6}\s+(.+?)\s*$", line)
        if heading_match:
            current_heading = _text(heading_match.group(1), 180)
            continue
        if re.match(r"^[-_]{3,}$", line):
            continue
        plain = re.sub(r"^[-*•]\s+", "", line).strip()
        plain = re.sub(r"^\*\*([^*]+)\*\*\s*[:：]?\s*", r"\1：", plain).strip()
        if not plain or len(_norm(plain)) < 8:
            continue
        if _ACADEMIC_SKIP_LINE_RE.search(plain):
            continue
        if _redundant(subject, plain):
            continue
        if re.fullmatch(r"[\\W_\\d\\s]+", plain, re.UNICODE):
            continue

        priority = 3
        if _ACADEMIC_PRIMARY_HEADING_RE.search(current_heading):
            priority = 0
        elif _ACADEMIC_NEXT_HEADING_RE.search(current_heading):
            priority = 1
        elif current_heading:
            priority = 2
        candidates.append((priority, order, _text(plain, 240)))
        order += 1

    selected: List[str] = []
    for _priority, _order, item in sorted(candidates, key=lambda row: (row[0], row[1])):
        if any(_redundant(item, old) for old in selected):
            continue
        selected.append(item)
        if len(selected) >= max_items:
            break
    return [f"- {item}" for item in selected]


def _effective_original_policy(notification: Mapping[str, Any], settings: Mapping[str, Any]) -> str:
    global_policy = _text(settings.get("original_policy"), 32).lower() or "auto"
    requested = _text(notification.get("original_policy"), 32).lower() or "none"
    if global_policy == "always":
        return "full" if requested == "none" else requested
    if global_policy == "never":
        return "none"
    return requested if requested in _ALLOWED_ORIGINAL else "none"


def _special_card_lines(
    mode: str,
    email: Mapping[str, Any],
    decision: Mapping[str, Any],
    body: str,
) -> Tuple[str, List[str]]:
    notification = _mapping(decision.get("notification"))
    summary_style = _text(notification.get("summary_style"), 32).lower()
    if summary_style == "paragraph":
        summary = clean_body(notification.get("summary"))
    else:
        summary = "；".join(clean_body(x) for x in _list(notification.get("key_points")) if clean_body(x))
    if mode == "code_card":
        source = "\n".join([_text(email.get("subject")), body])
        code = _extract_code(source)
        validity = _extract_code_validity(source)
        values = [f"验证码：`{code}`" if code else "验证码：请查看原邮件"]
        if validity:
            values.append(f"有效期：{validity}")
        return "验证码", values
    if mode == "finance_card":
        amounts = _extract_amounts("\n".join([_text(email.get("subject")), body, summary]))
        values = [summary]
        values.extend(f"金额：{amount}" for amount in amounts)
        return "财务信息", [x for x in values if x]
    if mode == "event_card":
        deadline = _mapping(decision.get("deadline"))
        when = _format_time(deadline.get("datetime")) or _text(deadline.get("date_text"), 160)
        values = [summary, f"时间：{when}" if when else ""]
        return "会议/活动", [x for x in values if x]
    if mode == "deadline_card":
        action = _mapping(decision.get("action"))
        deadline = _mapping(decision.get("deadline"))
        when = _format_time(deadline.get("datetime")) or _text(deadline.get("date_text"), 160)
        values = [summary]
        if action.get("description"):
            values.append(f"任务：{_text(action.get('description'), 500)}")
        if when:
            values.append(f"截止：{when}")
        if action.get("next_step"):
            values.append(f"下一步：{_text(action.get('next_step'), 500)}")
        return "截止任务", [x for x in values if x]
    return "", []


def render_notification(
    email: Mapping[str, Any],
    decision: Mapping[str, Any],
    delivery: Mapping[str, Any] | None = None,
    account: Mapping[str, Any] | None = None,
    *,
    settings_override: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Render one adaptive shadow notification with deterministic deduplication."""
    email = email or {}
    decision = decision or {}
    delivery = delivery or {}
    settings = _settings(settings_override)
    notification = _mapping(decision.get("notification"))
    mode = _text(notification.get("content_mode"), 64).lower() or "summary_only"
    if mode not in _ALLOWED_MODES:
        mode = "summary_only"
    subject = _text(email.get("subject"), 260) or "无主题"
    body = clean_body(_body_source(email))
    importance = _mapping(decision.get("importance"))
    lines: List[str] = [f"### 📬 新邮件｜{_account(email, account)}"]
    blocks: List[str] = []
    meta: List[str] = []
    if settings.get("show_priority"):
        meta.append(_priority_label(importance.get("level")))
    if settings.get("show_category"):
        meta.append(_category_label(decision))
    if settings.get("show_time"):
        sent = _format_time(email.get("date_sent") or email.get("date") or email.get("sent_at"))
        if sent:
            meta.append(sent)
    if meta:
        lines.extend(["", " · ".join(f"`{item}`" for item in meta if item)])
    _append_block(lines, blocks, "发件人", [_sender(email)])
    _append_block(lines, blocks, "主题", [subject])

    duplicate_suppressions = 0
    summary_fallback = ""
    if mode in {"code_card", "finance_card", "event_card", "deadline_card"}:
        title, card_lines = _special_card_lines(mode, email, decision, body)
        _append_block(lines, blocks, title, card_lines)
        if mode == "code_card":
            _append_block(
                lines,
                blocks,
                "安全提示",
                ["请核对发件人与邮件来源后使用验证码，不要向他人泄露。"],
            )
    else:
        summary_lines, count = _summary_lines(
            subject,
            decision,
            suppress_redundant=bool(settings.get("suppress_redundant_summary")),
        )
        duplicate_suppressions += count
        classification = _mapping(decision.get("classification"))
        if (
            mode != "original_only"
            and not summary_lines
            and _text(classification.get("category"), 80).lower() == "academic_report_digest"
        ):
            summary_lines = _academic_digest_highlight_lines(body, subject)
            if summary_lines:
                summary_fallback = "academic_body_highlights"
        if mode != "original_only":
            _append_block(lines, blocks, "摘要", summary_lines)

    action = _mapping(decision.get("action"))
    if mode != "deadline_card" and bool(action.get("required")):
        action_lines: List[str] = []
        description = _text(action.get("description"), 500)
        next_step = _text(action.get("next_step"), 500)
        summary_text = "\n".join(_summary_lines(subject, decision, suppress_redundant=False)[0])
        if description and not _redundant(description, summary_text):
            action_lines.append(description)
        elif description:
            duplicate_suppressions += 1
        if next_step and not _redundant(next_step, description) and not _redundant(next_step, summary_text):
            action_lines.append(f"下一步：{next_step}")
        elif next_step:
            duplicate_suppressions += 1
        _append_block(lines, blocks, "待办", action_lines)

    deadline = _mapping(decision.get("deadline"))
    if mode != "deadline_card" and bool(deadline.get("has_deadline")):
        when = _format_time(deadline.get("datetime")) or _text(deadline.get("date_text"), 160)
        _append_block(lines, blocks, "截止时间", [when])

    names = _attachment_names(email, delivery, decision)
    semantic_attachments = _mapping(decision.get("attachments"))
    attachment_present = bool(semantic_attachments.get("present")) or bool(names) or bool(email.get("has_attachments") or email.get("has_attachment"))
    if attachment_present:
        if names:
            attachment_lines = [f"- 📎 {name}" for name in names]
        else:
            attachment_lines = ["- 📎 有附件，文件名未解析，请在邮箱查看"]
        _append_block(lines, blocks, "附件", attachment_lines)

    schedule_lines = _schedule_lines(delivery)
    if schedule_lines:
        _append_block(lines, blocks, "提醒", [f"- {item}" for item in schedule_lines])

    risk = _mapping(decision.get("risk"))
    if _text(risk.get("level"), 32).lower() not in {"", "none"}:
        risk_level = f"风险等级：{_text(risk.get('level'), 32)}"
        risk_notes = [
            " ".join(clean_body(note).split())
            for note in _list(risk.get("notes"))
            if clean_body(note)
        ]
        risk_line = risk_level + (f" | {'；'.join(risk_notes)}" if risk_notes else "")
        _append_block(lines, blocks, "风险提示", [risk_line])

    original_policy = _effective_original_policy(notification, settings)
    original_heading = "原文"
    original_text = body
    truncated = False
    if original_policy == "excerpt" or (original_policy == "full" and len(original_text) > int(settings["original_max_chars"])):
        limit = int(settings["original_max_chars"])
        if len(original_text) > limit:
            original_text = original_text[:limit].rstrip()
            truncated = True
        original_heading = "原文节选"
    if original_policy in {"full", "excerpt"} and original_text:
        if truncated:
            original_text += "\n\n已截断，完整正文请在邮箱中查看。"
        _append_block(lines, blocks, original_heading, [original_text])

    text = "\n".join(lines).strip()
    return {
        "ok": True,
        "marker": MARKER,
        "renderer_version": RENDERER_VERSION,
        "text": text,
        "blocks": blocks,
        "content_mode": mode,
        "original_policy": original_policy,
        "notification_chars": len(text),
        "duplicate_suppression_count": duplicate_suppressions,
        "summary_fallback": summary_fallback,
        "original_truncated": truncated,
    }


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
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
            CREATE TABLE IF NOT EXISTS adaptive_renderer_observations (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              message_key TEXT UNIQUE NOT NULL,
              renderer_version TEXT NOT NULL,
              content_mode TEXT,
              original_policy TEXT,
              production_notification_hash TEXT NOT NULL,
              adaptive_notification_hash TEXT NOT NULL,
              production_chars INTEGER DEFAULT 0,
              adaptive_chars INTEGER DEFAULT 0,
              duplicate_suppression_count INTEGER DEFAULT 0,
              blocks_json TEXT NOT NULL,
              comparison_json TEXT NOT NULL,
              adaptive_notification_shadow TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_adaptive_renderer_created ON adaptive_renderer_observations(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_adaptive_renderer_mode ON adaptive_renderer_observations(content_mode)")
        conn.commit()
        return {"ok": True, "db_path": str(path), "table": "adaptive_renderer_observations"}
    except Exception as exc:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return {"ok": False, "db_path": str(path), "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
    finally:
        if conn is not None:
            conn.close()


def _persist_shadow(
    *,
    db_path: Path,
    message_key: str,
    production_text: str,
    rendered: Mapping[str, Any],
) -> Dict[str, Any]:
    schema = ensure_schema(db_path)
    if not schema.get("ok"):
        return schema
    now = datetime.now(timezone.utc).isoformat()
    adaptive_text = _text(rendered.get("text"))
    comparison = {
        "byte_equal": production_text == adaptive_text,
        "production_chars": len(production_text),
        "adaptive_chars": len(adaptive_text),
        "char_delta": len(adaptive_text) - len(production_text),
        "duplicate_suppression_count": int(rendered.get("duplicate_suppression_count") or 0),
        "blocks": list(rendered.get("blocks") or []),
        "content_mode": rendered.get("content_mode"),
        "original_policy": rendered.get("original_policy"),
    }
    conn: Optional[sqlite3.Connection] = None
    try:
        conn = _connect(db_path)
        conn.execute(
            """
            INSERT INTO adaptive_renderer_observations (
              message_key, renderer_version, content_mode, original_policy,
              production_notification_hash, adaptive_notification_hash,
              production_chars, adaptive_chars, duplicate_suppression_count,
              blocks_json, comparison_json, adaptive_notification_shadow,
              created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(message_key) DO UPDATE SET
              renderer_version=excluded.renderer_version,
              content_mode=excluded.content_mode,
              original_policy=excluded.original_policy,
              production_notification_hash=excluded.production_notification_hash,
              adaptive_notification_hash=excluded.adaptive_notification_hash,
              production_chars=excluded.production_chars,
              adaptive_chars=excluded.adaptive_chars,
              duplicate_suppression_count=excluded.duplicate_suppression_count,
              blocks_json=excluded.blocks_json,
              comparison_json=excluded.comparison_json,
              adaptive_notification_shadow=excluded.adaptive_notification_shadow,
              updated_at=excluded.updated_at
            """,
            (
                message_key,
                RENDERER_VERSION,
                _text(rendered.get("content_mode"), 80),
                _text(rendered.get("original_policy"), 32),
                _sha(production_text),
                _sha(adaptive_text),
                len(production_text),
                len(adaptive_text),
                int(rendered.get("duplicate_suppression_count") or 0),
                _json(rendered.get("blocks") or []),
                _json(comparison),
                adaptive_text,
                now,
                now,
            ),
        )
        conn.commit()
        return {"ok": True, "db_path": str(db_path), "message_key": message_key, "comparison": comparison}
    except Exception as exc:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return {"ok": False, "db_path": str(db_path), "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
    finally:
        if conn is not None:
            conn.close()


def shadow_compare(
    email: Mapping[str, Any],
    decision: Mapping[str, Any],
    delivery: Mapping[str, Any],
    account: Mapping[str, Any] | None = None,
    *,
    message_key: str = "",
    settings_override: Mapping[str, Any] | None = None,
    db_path: Path | str | None = None,
) -> Dict[str, Any]:
    """Render and persist shadow comparison while preserving production output."""
    settings = _settings(settings_override)
    if settings.get("renderer") != "adaptive_v1":
        return {"ok": True, "skipped": True, "reason": "adaptive renderer disabled"}
    if settings.get("mode") != "shadow":
        return {"ok": True, "skipped": True, "reason": "phase2 requires shadow mode"}
    rendered = render_notification(email, decision, delivery, account, settings_override=settings)
    production_text = _text(delivery.get("notification_text"))
    stable_key = _text(message_key, 512)
    if not stable_key:
        stable_key = _text(decision.get("message_key"), 512)
    if not stable_key:
        stable_key = _sha("|".join((_text(email.get("account")), _text(email.get("id") or email.get("message_id")), _text(email.get("subject")))))
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    persist = _persist_shadow(
        db_path=path,
        message_key=stable_key,
        production_text=production_text,
        rendered=rendered,
    )
    return {
        "ok": bool(persist.get("ok")),
        "message_key": stable_key,
        "adaptive_notification_shadow": rendered.get("text", ""),
        "render": rendered,
        "persist": persist,
        "production_notification_changed": False,
        "weixin_send": False,
        "mailbox_write": False,
    }


def status(db_path: Path | str | None = None) -> Dict[str, Any]:
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    result: Dict[str, Any] = {
        "marker": MARKER,
        "renderer_version": RENDERER_VERSION,
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
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='adaptive_renderer_observations'"
        ).fetchone()
        result["table_exists"] = bool(table)
        if table:
            result["observation_count"] = int(conn.execute("SELECT COUNT(*) FROM adaptive_renderer_observations").fetchone()[0])
        return result
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
        return result
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    print(json.dumps(status(), ensure_ascii=False, indent=2, sort_keys=True))
