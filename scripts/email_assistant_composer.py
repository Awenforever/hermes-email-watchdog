#!/usr/bin/env python3
"""Intent-aware Markdown composer for Email Watchdog.

The semantic model decides what the message means.  This module turns that
validated decision into a useful mobile notification.  It deliberately avoids
the old ``generic summary + raw excerpt`` template: transport chrome, greetings,
signatures and negative inventory ("no attachment", "no code") are never
promoted to user-facing highlights.
"""
from __future__ import annotations

import html
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Mapping, Sequence, Tuple
from urllib.parse import urlparse
from zoneinfo import ZoneInfo


COMPOSER_VERSION = "intelligent_v2.1"
MARKER = "EMAIL_WATCHDOG_INTENT_AWARE_COMPOSER_V2P1"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _items(value: Any) -> List[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _text(value: Any, limit: int = 4000) -> str:
    if isinstance(value, Mapping):
        value = (
            value.get("text")
            or value.get("summary")
            or value.get("point")
            or value.get("content")
            or ""
        )
    return str(value or "").replace("\x00", "").strip()[:limit]


def _clean(value: Any) -> str:
    text = html.unescape(_text(value, 30000)).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?i)\[image(?::[^\]]*)?\]|<img\b[^>]*>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>|</p\s*>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    lines: List[str] = []
    for raw in text.splitlines():
        line = re.sub(r"[\t\u00a0 ]+", " ", raw).strip()
        if line:
            lines.append(line)
        elif lines and lines[-1]:
            lines.append("")
    return "\n".join(lines).strip()


_NOISE = re.compile(
    r"(?i)^\s*(?:[-_=]{3,}\s*)?(?:forwarded message|original message|begin forwarded message)"
    r"|^\s*(?:from|to|cc|bcc|sent|date|subject|发件人|收件人|抄送|发送时间|主题)\s*[:：]"
    r"|^\s*(?:dear\b|hello\b|hi\b|尊敬的|您好|你好)[,，：:]?\s*$"
    r"|^\s*(?:thank you|thanks|sincerely|regards|best regards|此致|敬礼)[.!，,。]?\s*$"
    r"|^\s*(?:unsubscribe|privacy policy|all rights reserved|copyright\b|取消订阅|退订|隐私政策)"
)
_META_POINT = re.compile(
    r"(?i)^(?:这?是?一封)?(?:转发的?|forwarded)邮件|"
    r"^邮件为转发(?:内容|邮件|件)?|"
    r"^(?:邮件|正文)(?:中|里)?(?:包含|含有|提到|显示)|"
    r"^(?:无|没有|未发现)(?:附件|验证码|截止|明确)|"
    r"^附件(?:为|是|名为)|^邮件由.+自动发送|举报退订"
)


def _useful_point(value: Any) -> str:
    point = re.sub(r"^\s*(?:[-*•]|\d+[.)、])\s*", "", _clean(value)).strip()
    if not point or _NOISE.search(point) or _META_POINT.search(point):
        return ""
    if re.fullmatch(r"[-_=*#>\s]+", point):
        return ""
    return " ".join(point.split())[:360]


def _code(value: Any) -> str:
    value = _text(value, 400).replace("`", "′").replace("\n", " ")
    return f"`{value}`"


def _format_time(value: Any) -> str:
    raw = _text(value, 180)
    if not raw:
        return ""
    candidate = re.sub(r"\s+SGT$", " +0800", raw, flags=re.I)
    try:
        try:
            dt = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        except Exception:
            dt = parsedate_to_datetime(candidate)
        shanghai = ZoneInfo("Asia/Shanghai")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=shanghai)
        return dt.astimezone(shanghai).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return raw


def _sender(email: Mapping[str, Any]) -> str:
    name = _text(email.get("from_name"), 120).strip('"')
    address = _text(email.get("from_addr") or email.get("from_email") or email.get("sender"), 200)
    if name and address and name.casefold() not in address.casefold():
        return f"{name} <{address}>"
    return name or address or "未知发件人"


def _category(decision: Mapping[str, Any]) -> Tuple[str, str]:
    info = _mapping(decision.get("classification"))
    return _text(info.get("category"), 80), _text(info.get("label"), 80) or "邮件"


def _heading(category: str, body: str, subject: str) -> Tuple[str, str]:
    combined = f"{subject}\n{body}"
    if category == "invoice_receipt":
        return ("💳", "账单已逾期" if re.search(r"(?i)overdue|逾期", combined) else "账单与凭据")
    if category == "verification_code":
        return "🔐", "验证码"
    if category == "account_security":
        return "🛡️", "账户安全提醒"
    if category == "account_status_notice":
        return "🔗", "账户确认"
    if category == "meeting_event":
        return "📅", "活动与日程"
    if category == "task_deadline":
        return "⏰", "待办与截止时间"
    if category == "school_notice":
        return "🎓", "学校通知"
    if category == "academic_report_digest":
        return "📚", "研究简报"
    if category == "newsletter_marketing":
        return "📰", "订阅更新"
    return "📬", "新邮件"


def _first(patterns: Sequence[str], source: str, limit: int = 120) -> str:
    for pattern in patterns:
        match = re.search(pattern, source, re.I | re.S)
        if match:
            return " ".join(match.group(1).strip().split())[:limit]
    return ""


def _invoice_facts(source: str, decision: Mapping[str, Any]) -> List[Tuple[str, str]]:
    facts: List[Tuple[str, str]] = []
    invoice = _first((r"invoice(?:\s+(?:no\.?|number))?\s*[:#]?\s*([A-Z-]*\d[A-Z0-9-]{3,})", r"发票(?:号)?\s*[:：]?\s*([A-Z-]*\d[A-Z0-9-]{3,})"), source)
    amount = _first((r"balance\s+due\s*[:：]?\s*([^\n]{1,40})", r"(?:amount|total)\s+due\s*[:：]?\s*([^\n]{1,40})", r"应付(?:余额|金额)?\s*[:：]?\s*([^\n]{1,40})"), source)
    due = _first((r"due\s+date\s*[:：]?\s*([^\n]{1,40})", r"到期日\s*[:：]?\s*([^\n]{1,40})"), source)
    generated = _first((r"generated\s+(?:on\s+)?(\d{4}[/-]\d{1,2}[/-]\d{1,2})", r"生成(?:日期)?\s*[:：]?\s*([^\n]{1,40})"), source)
    method = _first((r"payment\s+method\s+(?:is\s*)?[:：]\s*([^\n]{1,80})", r"付款方式\s*[:：]?\s*([^\n]{1,80})"), source)
    deadline = _mapping(decision.get("deadline"))
    due = due or _text(deadline.get("date_text") or deadline.get("datetime"), 80)
    for label, value in (("发票号", invoice), ("应付金额", amount), ("到期日", due), ("生成日期", generated), ("付款方式", method)):
        if value and (label, value) not in facts:
            facts.append((label, value.rstrip(".。")))
    return facts


def _summary_points(decision: Mapping[str, Any], category: str) -> List[str]:
    notification = _mapping(decision.get("notification"))
    values: List[Any] = []
    if notification.get("summary"):
        values.append(notification.get("summary"))
    values.extend(_items(notification.get("key_points")))
    out: List[str] = []
    for raw in values:
        point = _useful_point(raw)
        if not point or any(point.casefold() == old.casefold() for old in out):
            continue
        if category == "invoice_receipt" and re.search(r"(?i)invoice|发票|balance due|应付|due date|到期|payment method|付款方式", point):
            continue
        out.append(point)
        if len(out) >= 4:
            break
    return out


def _links(email: Mapping[str, Any], category: str) -> List[Tuple[str, str]]:
    low_value = re.compile(
        r"(?i)unsubscribe|privacy|terms|contact|support|home|website|退订|隐私|条款|"
        r"举报|identity|agent\.qq\.com(?:/page/(?:identity|report))?"
    )
    research_link = re.compile(
        r"(?i)arxiv\.org|doi\.org|github\.com|openreview\.net|semanticscholar\.org|"
        r"(?:paper|论文|代码|code|dataset|数据集|report|报告|pdf)"
    )
    ranked: List[Tuple[int, str, str]] = []
    seen = set()
    for item in _items(email.get("links")):
        if isinstance(item, Mapping):
            url, label = _text(item.get("url"), 3000), _clean(item.get("display_text"))
        else:
            url, label = _text(item, 3000), ""
        if not url.lower().startswith(("https://", "http://")) or url in seen:
            continue
        seen.add(url)
        haystack = f"{label} {url}"
        score = 20
        if category == "academic_report_digest":
            score = 5 if research_link.search(haystack) else 50
        if category == "invoice_receipt" and re.search(r"(?i)viewinvoice|invoice|payment|billing", haystack):
            score, label = 0, "查看并处理账单"
        elif category == "account_status_notice" and re.search(r"(?i)confirm|verify|activate", haystack):
            score, label = 0, "确认账户或邮箱"
        elif category == "account_security" and re.search(r"(?i)security|activity|password|account", haystack):
            score, label = 0, "检查账户安全"
        elif re.search(r"(?i)confirm|verify|activate|reset|register|apply|download|meeting|invoice|payment", haystack):
            score = 5
        elif low_value.search(haystack):
            score = 50
        host = urlparse(url).netloc
        ranked.append((score, label or (f"打开 {host}" if host else "打开链接"), url))
    ranked.sort(key=lambda row: row[0])
    useful = [row for row in ranked if row[0] < 50]
    if useful and useful[0][0] == 0:
        useful = [row for row in useful if row[0] == 0]
    return [(re.sub(r"[\[\]]", "", label)[:80], url.replace(" ", "%20")) for _, label, url in useful[:3]]


def _attachment_lines(email: Mapping[str, Any], delivery: Mapping[str, Any]) -> List[str]:
    source = _items(delivery.get("attachments")) or _items(email.get("attachments"))
    out: List[str] = []
    for item in source:
        if isinstance(item, Mapping):
            name = _text(item.get("filename") or item.get("name"), 240)
            status = _text(item.get("download_status"), 40).lower()
            sent = item.get("send_to_weixin")
        else:
            name, status, sent = _text(item, 240), "", None
        if not name or name.casefold() in {"(attachments present)", "attachments present", "attachment present"}:
            continue
        if status == "downloaded" and sent is not False:
            suffix = " · 已附上"
        elif status == "downloaded":
            suffix = " · 已下载，未附送"
        elif status in {"download_failed", "processing_failed"}:
            suffix = " · 下载失败，请在邮箱查看"
        elif status in {"list_only", "listed"}:
            suffix = " · 仅列出，请在邮箱查看"
        else:
            suffix = ""
        out.append(f"- **{name.replace('*', '')}**{suffix}")
    return out[:8]


def _meaningful_excerpt(body: str, decision: Mapping[str, Any], category: str) -> str:
    if category not in {"school_notice", "paper_manuscript_feedback", "research_feedback_thread", "personal_or_general", "unknown_needs_llm"}:
        return ""
    notification = _mapping(decision.get("notification"))
    if _text(notification.get("original_policy"), 30) not in {"full", "excerpt"}:
        return ""
    paragraphs = re.split(r"\n\s*\n+", body)
    action = _mapping(decision.get("action"))
    signal = " ".join([
        _text(notification.get("summary")),
        " ".join(_text(x) for x in _items(notification.get("key_points"))),
        _text(action.get("description")), _text(action.get("next_step")),
    ]).casefold()
    tokens = set(re.findall(r"[a-z0-9]{3,}|[\u4e00-\u9fff]{2,}", signal))
    ranked: List[Tuple[int, int, str]] = []
    for index, raw in enumerate(paragraphs):
        paragraph = " ".join(_clean(raw).split())
        if len(paragraph) < 20 or _NOISE.search(paragraph) or _META_POINT.search(paragraph):
            continue
        ptokens = set(re.findall(r"[a-z0-9]{3,}|[\u4e00-\u9fff]{2,}", paragraph.casefold()))
        ranked.append((len(tokens & ptokens), -index, paragraph[:500]))
    ranked.sort(reverse=True)
    supported = [row[2] for row in ranked if row[0] > 0]
    # Cross-language summaries may not share lexical tokens with the quoted
    # English source. In that case select the first two already-cleaned content
    # paragraphs instead of falling back to the raw beginning of the message.
    chosen = supported[:2] if supported else [row[2] for row in sorted(ranked, key=lambda row: -row[1])[:2]]
    return " … ".join(chosen)[:800]


def _section(lines: List[str], title: str, content: Sequence[str]) -> None:
    clean = [x for x in content if _text(x)]
    if clean:
        lines.extend(["", f"**{title}**", *clean])


def render_notification(
    email: Mapping[str, Any],
    decision: Mapping[str, Any],
    delivery: Mapping[str, Any] | None = None,
    account: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    email, decision, delivery, account = email or {}, decision or {}, delivery or {}, account or {}
    category, label = _category(decision)
    body = _clean(email.get("body") or email.get("body_plain") or email.get("plain") or email.get("text"))
    subject = _text(email.get("subject"), 280) or "无主题"
    icon, title = _heading(category, body, subject)
    account_label = _text(email.get("account") or account.get("name") or account.get("id") or account.get("email"), 80) or "Email"
    importance = _text(_mapping(decision.get("importance")).get("level"), 30).lower()
    priority = {"critical": "紧急", "high": "重要", "normal": "普通", "low": "低优先级"}.get(importance, "普通")
    sent = _format_time(
        email.get("date_sent") or email.get("date") or email.get("sent_at") or email.get("cached_at")
    )
    meta = " · ".join(_code(x) for x in (priority, label, sent) if x)
    lines = [f"### {icon} {title}｜{account_label}", "", meta, "", f"**发件人** {_code(_sender(email))}", "", f"**主题** {_code(subject)}"]
    blocks = ["发件人", "主题"]

    if category == "invoice_receipt":
        facts = _invoice_facts(f"{subject}\n{body}", decision)
        _section(lines, "账单信息", [f"- **{key}**：{_code(value)}" for key, value in facts])
        if facts:
            blocks.append("账单信息")
    elif category == "verification_code":
        code = _first((r"(?<!\d)(\d{4,8})(?!\d)",), f"{subject}\n{body}")
        _section(lines, "验证码", [f"## {_code(code)}" if code else "请在原邮件中查看验证码。"])
        blocks.append("验证码")
    else:
        points = _summary_points(decision, category)
        if points:
            section_name = {
                "account_security": "安全提醒", "account_status_notice": "需要确认",
                "meeting_event": "活动信息", "task_deadline": "任务说明",
                "school_notice": "通知重点", "academic_report_digest": "内容摘要",
                "newsletter_marketing": "内容摘要",
            }.get(category, "邮件摘要")
            rendered = [points[0]] if len(points) == 1 else [f"- {point}" for point in points]
            _section(lines, section_name, rendered)
            blocks.append(section_name)

    action = _mapping(decision.get("action"))
    deadline = _mapping(decision.get("deadline"))
    action_lines: List[str] = []
    if bool(action.get("required")):
        description = _useful_point(action.get("description"))
        next_step = _useful_point(action.get("next_step"))
        if description:
            action_lines.append(description)
        if next_step and next_step.casefold() != description.casefold():
            action_lines.append(f"下一步：{next_step}")
    if bool(deadline.get("has_deadline")):
        due = _format_time(deadline.get("datetime")) or _text(deadline.get("date_text"), 160)
        if due and category != "invoice_receipt":
            action_lines.append(f"截止时间：{_code(due)}")
    if action_lines:
        _section(lines, "需要处理", [f"- {x}" for x in action_lines])
        blocks.append("需要处理")

    links = _links(email, category)
    if links:
        _section(lines, "快捷操作", [f"- [{label}]({url})" for label, url in links])
        blocks.append("快捷操作")

    attachment_lines = _attachment_lines(email, delivery)
    if attachment_lines:
        _section(lines, "附件", attachment_lines)
        blocks.append("附件")

    schedules = []
    for item in _items(delivery.get("schedule")):
        if isinstance(item, Mapping):
            due = _format_time(item.get("deadline") or item.get("time") or item.get("datetime"))
            if due:
                schedules.append(f"- 已记录截止时间 {_code(due)}，将按设置提前提醒")
    if schedules:
        _section(lines, "提醒", schedules)
        blocks.append("提醒")

    excerpt = _meaningful_excerpt(body, decision, category)
    if excerpt:
        _section(lines, "原文依据", [f"> {excerpt}"])
        blocks.append("原文依据")

    risk = _mapping(decision.get("risk"))
    notes = [_useful_point(x) for x in _items(risk.get("notes"))]
    notes = [x for x in notes if x]
    if _text(risk.get("level"), 30).lower() not in {"", "none"} and notes:
        _section(lines, "风险提示", [f"> {'；'.join(notes)}"])
        blocks.append("风险提示")

    text = "\n".join(x for x in lines if x is not None).strip()
    return {
        "ok": True,
        "marker": MARKER,
        "renderer_version": COMPOSER_VERSION,
        "text": text,
        "blocks": blocks,
        "content_mode": _text(_mapping(decision.get("notification")).get("content_mode"), 64),
        "original_policy": "curated" if excerpt else "none",
        "notification_chars": len(text),
        "duplicate_suppression_count": 0,
        "original_truncated": False,
    }
