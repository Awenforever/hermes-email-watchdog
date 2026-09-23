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
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from zoneinfo import ZoneInfo


COMPOSER_VERSION = "intelligent_v3.0"
MARKER = "EMAIL_WATCHDOG_INTENT_AWARE_COMPOSER_V3"


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
    r"^(?:该|这)?邮件为转发(?:内容|邮件|件)?|"
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
    point = " ".join(point.split())[:360]
    # Compact model responses occasionally stop cleanly at their token budget
    # while leaving a syntactically valid JSON string containing half a
    # sentence.  Such fragments are worse than omitting the action entirely.
    if re.search(r"(?i)\b(?:a|an|the|to|of|for|with|or|and|provide|please|co)$", point):
        return ""
    if re.search(r"@[A-Z0-9._%+-]+\.[A-Z]?$", point, re.I):
        return ""
    if len(point) < 8 and re.search(r"[A-Za-z]", point):
        return ""
    return point


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
        mode = _account_status_mode(f"{subject}\n{body}")
        return {
            "confirmation": ("🔗", "账户确认"),
            "service": ("📵", "服务状态"),
            "authorization": ("🔐", "授权请求"),
            "approval": ("✅", "审批结果"),
        }.get(mode, ("📌", "账户状态更新"))
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
    invoice = _first((r"invoice(?:\s+(?:no\.?|number))?\s*[:#]?\s*([A-Z-]*\d[A-Z0-9-]{3,})", r"(?:发票(?:号码|号)?|号码)\s*[：:【]?\s*([A-Z-]*\d[A-Z0-9-]{3,})"), source)
    amount = _first((r"balance\s+due\s*[:：]?\s*([^\n]{1,40})", r"(?:amount|total)\s+due\s*[:：]?\s*([^\n]{1,40})", r"(?:应付(?:余额|金额)?|价税合计|开票金额|票价)\s*[:：]?\s*([¥￥$]?\s*\d+(?:\.\d{1,2})?\s*(?:元|USD|CNY)?)"), source)
    due = _first((r"due\s+date\s*[:：]?\s*([^\n]{1,40})", r"到期日\s*[:：]?\s*([^\n]{1,40})"), source)
    generated = _first((r"generated\s+(?:on\s+)?(\d{4}[/-]\d{1,2}[/-]\d{1,2})", r"生成(?:日期)?\s*[:：]?\s*([^\n]{1,40})"), source)
    method = _first((r"payment\s+method\s+(?:is\s*)?[:：]\s*([^\n]{1,80})", r"付款方式\s*[:：]?\s*([^\n]{1,80})"), source)
    deadline = _mapping(decision.get("deadline"))
    due = due or _text(deadline.get("date_text") or deadline.get("datetime"), 80)
    issuer = _first((r"【([^】]{2,80})】开具", r"(?:销售方|开票方|商户)\s*[:：]\s*([^\n，。]{2,80})"), source)
    buyer = _first((r"(?:购买方|抬头)\s*[:：]?\s*([^\n，。]{2,80})",), source)
    buyer = re.sub(r"^(?:(?:名称|单位)\s*[:：]\s*|为\s*)", "", buyer)
    if re.search(r"邮箱|信息|填写|开具成功", buyer):
        buyer = ""
    train = _first((r"(?:车次|train)\s*[:：]?\s*([A-Z]\d{1,5})",), source)
    travel_date = _first((r"(?:乘车日期|出行日期)\s*[:：]?\s*(20\d{2}[年/-]\d{1,2}[月/-]\d{1,2}日?)",), source)
    route = _first((r"([\u4e00-\u9fff]{2,12}\s*[-—至]\s*[\u4e00-\u9fff]{2,12})",), source) if train else ""
    for label, value in (("发票号", invoice), ("开票方", issuer), ("购买方", buyer), ("应付金额", amount), ("到期日", due), ("生成日期", generated), ("乘车日期", travel_date), ("车次", train), ("行程", route), ("付款方式", method)):
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


def _account_status_mode(source: str) -> str:
    if re.search(r"(?i)orcid|grant permissions?|crossref|授权", source):
        return "authorization"
    if re.search(r"(?i)disconnect|suspension|suspended|service.{0,20}(?:stop|cease)|停服|暂停服务|服务.{0,12}(?:断开|停止)", source):
        return "service"
    if re.search(r"审核.{0,12}(?:通过|完成)|approved|approval", source):
        return "approval"
    if re.search(r"(?i)confirm(?:ation)?|verify|activate|确认(?:邮箱|邮件|账户)|激活", source):
        return "confirmation"
    return "status"


def _weekly_report_points(body: str) -> List[str]:
    paragraphs = [" ".join(_clean(x).split()) for x in re.split(r"\n\s*\n+", body)]
    candidates: List[Tuple[int, int, str]] = []
    for index, paragraph in enumerate(paragraphs):
        if len(paragraph) < 45 or _NOISE.search(paragraph):
            continue
        if re.search(r"(?i)附件|祝研究顺利|下周见|自动发送|举报|退订|tel:|中国科学技术大学|重点实验室", paragraph):
            continue
        score = sum(bool(re.search(pattern, paragraph, re.I)) for pattern in (
            r"foundation model|HighFM|Mamba|论文|文献",
            r"反直觉|数据质量|领域对齐|方法论|迁移",
            r"FireSat|开源|动态|卫星|烟雾",
        ))
        if score:
            sentences = re.split(r"(?<=[。！？.!?])\s*", paragraph)
            concise = "".join(sentences[:3]).strip()
            if len(concise) > 260:
                concise = concise[:257].rstrip("，,；; ") + "…"
            candidates.append((-score, index, concise))
    candidates.sort()
    out: List[str] = []
    for _, _, point in candidates:
        if point and point not in out:
            out.append(point)
        if len(out) >= 3:
            break
    return out


def _clean_url(value: str) -> str:
    value = html.unescape(value or "").strip().strip("<>()[]{}.,，。")
    # Some mail-to-text converters append Scholar tracking parameters to a
    # direct article URL with '&' even when the original URL has no query.
    value = re.split(r"&(?:hl|sa|d|ei|scisig|oi|html|pos|folt|rt)=", value, maxsplit=1, flags=re.I)[0]
    try:
        parsed = urlparse(value)
        query = parse_qsl(parsed.query, keep_blank_values=True)
        if parsed.netloc.casefold() == "scholar.google.com" and parsed.path.rstrip("/") == "/scholar_url":
            target = next((v for k, v in query if k.casefold() == "url"), "")
            if target.startswith(("https://", "http://")):
                return _clean_url(target)
        kept = [(k, v) for k, v in query
                if k.lower() not in {"utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "scisig", "oi", "ei", "sa", "hl"}]
        path = parsed.path
        if path == "/":
            path = ""
        return urlunparse((parsed.scheme, parsed.netloc, path, parsed.params, urlencode(kept), ""))
    except Exception:
        return value


def _body_link_pairs(email: Mapping[str, Any]) -> List[Tuple[str, str]]:
    body = _clean(email.get("body") or email.get("body_plain") or email.get("text"))
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    out: List[Tuple[str, str]] = []
    for index, line in enumerate(lines):
        match = re.search(r"https?://[^\s)>]+", line)
        if not match:
            continue
        label = re.sub(r"[:：\s(*>]+$", "", line[:match.start()]).strip()
        for previous in ([] if label else reversed(lines[max(0, index - 2):index])):
            candidate = re.sub(r"^[(*>\s]+|[)*>\s]+$", "", previous).strip()
            if (12 <= len(candidate) <= 220 and not candidate.lower().startswith(("http://", "https://"))
                    and not _NOISE.search(candidate)):
                label = candidate
                break
        out.append((label, _clean_url(match.group(0))))
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
    candidates: List[Any] = list(_items(email.get("links")))
    if category == "academic_report_digest":
        candidates = [{"display_text": label, "url": url} for label, url in _body_link_pairs(email)] + candidates
    for item in candidates:
        if isinstance(item, Mapping):
            url, label = _text(item.get("url"), 3000), _clean(item.get("display_text"))
        else:
            url, label = _text(item, 3000), ""
        url = _clean_url(url)
        if not url.lower().startswith(("https://", "http://")) or url in seen:
            continue
        seen.add(url)
        haystack = f"{label} {url}"
        score = 50
        if category == "academic_report_digest":
            title_like = bool(label and 12 <= len(label) <= 220 and not re.search(r"(?i)打开|click|view|pdf$|www\.", label))
            score = 0 if title_like and research_link.search(haystack) else (5 if research_link.search(haystack) else 50)
        if category == "invoice_receipt" and re.search(r"(?i)viewinvoice|pay(?:ment)?(?:/|\?|$)|billing/(?:invoice|pay)|download[^\s]*invoice", haystack):
            score, label = 0, "查看并处理账单"
        elif category == "invoice_receipt":
            score = 50
        elif category == "account_status_notice":
            mode = _account_status_mode(f"{email.get('subject','')}\n{email.get('body','')}")
            if mode == "confirmation" and re.search(r"(?i)confirm|verify|activate", haystack):
                score, label = 0, "确认账户或邮箱"
            elif mode == "authorization" and re.search(r"(?i)grant|permission|orcid\.org/inbox|doi\.org", haystack):
                score = 0 if re.search(r"(?i)grant|permission|/action", haystack) else 5
                label = "授权 Crossref 更新 ORCID" if score == 0 else ("查看 ORCID 通知" if "orcid.org" in url else "查看论文 DOI")
            elif mode == "service" and re.search(r"(?i)(?:log\s*in|login|account|billing|support|ticket)", label):
                score, label = 0, "登录官方账户查看服务状态"
            elif mode == "approval" and re.search(r"(?i)approval|taskcenter|matterapproval|审批", haystack):
                score, label = 0, "查看审批详情"
        elif category == "account_security" and re.search(r"(?i)security|activity|password|account", haystack):
            score, label = 0, "检查账户安全"
        elif re.search(r"(?i)confirm|verify|activate|reset|register|apply|download|meeting|invoice|payment", haystack):
            score = 5
        elif low_value.search(haystack):
            score = 50
        host = urlparse(url).netloc
        if category == "academic_report_digest" and label:
            label = re.sub(r"\s+", " ", label).strip(" -*•")
        ranked.append((score, label or (f"打开 {host}" if host else "打开链接"), url))
    ranked.sort(key=lambda row: row[0])
    useful = [row for row in ranked if row[0] < 50]
    if useful and useful[0][0] == 0:
        useful = [row for row in useful if row[0] == 0]
    return [(re.sub(r"[\[\]]", "", label)[:120], url.replace(" ", "%20")) for _, label, url in useful[:4]]


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
        elif status == "downloaded" and _text(item.get("forward_reason"), 60) == "archive_expanded":
            suffix = " · 已解包，见下列文件"
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
    if category not in {"personal_or_general", "unknown_needs_llm"}:
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
        paragraph = re.sub(r"^(?:>\s*)+", "", " ".join(_clean(raw).split())).strip()
        if len(paragraph) < 20 or _NOISE.search(paragraph) or _META_POINT.search(paragraph):
            continue
        ptokens = set(re.findall(r"[a-z0-9]{3,}|[\u4e00-\u9fff]{2,}", paragraph.casefold()))
        ranked.append((len(tokens & ptokens), -index, paragraph))
    ranked.sort(reverse=True)
    supported = [row[2] for row in ranked if row[0] > 0]
    # Cross-language summaries may not share lexical tokens with the quoted
    # English source. In that case select the first two already-cleaned content
    # paragraphs instead of falling back to the raw beginning of the message.
    chosen = supported[:1] if supported else [row[2] for row in sorted(ranked, key=lambda row: -row[1])[:1]]
    if not chosen:
        return ""
    excerpt = chosen[0]
    sentences = re.split(r"(?<=[。！？.!?])\s+", excerpt)
    result = " ".join(sentences[:3]).strip()
    return result if len(result) <= 320 else result[:317].rstrip("，,;； ") + "…"


def _fallback_action(category: str, body: str, points: Sequence[str]) -> str:
    if category == "account_security":
        return "请确认是否由本人授权；如非本人操作，立即撤销授权、修改密码并检查登录设备。"
    if category == "account_status_notice":
        mode = _account_status_mode(body)
        if mode == "confirmation":
            return "打开下方链接完成账户或邮箱确认；若并非本人注册，请忽略并检查账户安全。"
        if mode == "service":
            return "登录官方账户核实服务状态；如仍需保留或恢复服务，请仅通过官方渠道处理。"
        if mode == "authorization":
            return "登录 ORCID 通知中心，确认是否授权 Crossref 自动更新已发表成果。"
        return "查看账户状态，并按邮件中的官方说明处理。"
    if category in {"paper_manuscript_feedback", "research_feedback_thread"}:
        return "核对正文及附件中的修改意见，确认图表和修订内容无误后尽快回复作者或编辑。"
    if category == "school_notice":
        ranked = sorted(points, key=lambda point: (
            0 if re.search(r"(?:发送至|提交|填写).*(?:@|邮箱)|(?:@|邮箱).*(?:发送|提交)", point) else
            1 if re.search(r"(?:请于|截止).*(?:填写|提交|发送)", point) else
            2 if re.search(r"(?:填写|提交|发送至)", point) else 3
        ))
        for point in ranked:
            if re.search(r"(?:请于|截止|填写|提交|发送至)", point) and _useful_point(point):
                return point
        for point in points:
            if re.search(r"(?:需|务必|请|签到|提前|到场|参加)", point) and _useful_point(point):
                return point
    return ""


def _deadline_display(deadline: Mapping[str, Any], email: Mapping[str, Any]) -> Tuple[str, bool]:
    raw_datetime = _text(deadline.get("datetime"), 120)
    raw_text = _text(deadline.get("date_text"), 160)
    display = _format_time(raw_datetime) or raw_text
    if not display:
        return "", False
    candidate = raw_datetime or raw_text
    parsed = None
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except Exception:
        match = re.search(r"(?:(20\d{2})\D+)?(\d{1,2})\D+(\d{1,2})(?:\D+(\d{1,2})[:：](\d{2}))?", candidate)
        if match:
            year = int(match.group(1)) if match.group(1) else None
            if year is None:
                sent = _format_time(email.get("date_sent") or email.get("date"))
                year_match = re.match(r"(20\d{2})", sent)
                year = int(year_match.group(1)) if year_match else datetime.now().year
            try:
                parsed = datetime(year, int(match.group(2)), int(match.group(3)), int(match.group(4) or 23), int(match.group(5) or 59), tzinfo=ZoneInfo("Asia/Shanghai"))
            except Exception:
                parsed = None
    if parsed is not None:
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        expired = parsed.astimezone(ZoneInfo("Asia/Shanghai")) < datetime.now(ZoneInfo("Asia/Shanghai"))
        return display, expired
    return display, False


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
        semantic_source = "\n".join(
            [_text(_mapping(decision.get("notification")).get("summary"))]
            + [_text(x) for x in _items(_mapping(decision.get("notification")).get("key_points"))]
        )
        # Model-grounded points come first because HTML-to-text tables often
        # flatten all column headings before all values (for example
        # "开票金额 ... 发票号码 ... 32.00"), which makes positional regex
        # extraction from the raw body misleading.
        facts = _invoice_facts(f"{semantic_source}\n{subject}\n{body}", decision)
        _section(lines, "账单信息", [f"- **{key}**：{_code(value)}" for key, value in facts])
        if facts:
            blocks.append("账单信息")
        else:
            points = _summary_points(decision, "")
            _section(lines, "账单摘要", [f"- {point}" for point in points])
            if points:
                blocks.append("账单摘要")
    elif category == "verification_code":
        code = _first((r"(?<!\d)(\d{4,8})(?!\d)",), f"{subject}\n{body}")
        _section(lines, "验证码", [f"## {_code(code)}" if code else "请在原邮件中查看验证码。"])
        blocks.append("验证码")
    else:
        points = _summary_points(decision, category)
        if category == "academic_report_digest" and re.search(r"周报|weekly", subject, re.I):
            richer = _weekly_report_points(body)
            if richer:
                points = richer
        if points:
            status_mode = _account_status_mode(f"{subject}\n{body}")
            section_name = {
                "account_security": "安全提醒", "account_status_notice": "需要确认",
                "meeting_event": "活动信息", "task_deadline": "任务说明",
                "school_notice": "通知重点", "academic_report_digest": "内容摘要",
                "newsletter_marketing": "内容摘要",
            }.get(category, "邮件摘要")
            if category == "account_status_notice":
                section_name = {
                    "confirmation": "需要确认", "service": "服务状态",
                    "authorization": "授权说明", "approval": "审批结果",
                }.get(status_mode, "状态更新")
            rendered = [points[0]] if len(points) == 1 else [f"- {point}" for point in points]
            _section(lines, section_name, rendered)
            blocks.append(section_name)

    action = _mapping(decision.get("action"))
    deadline = _mapping(decision.get("deadline"))
    action_lines: List[str] = []
    points_for_action = _summary_points(decision, category)
    if bool(action.get("required")):
        description = _useful_point(action.get("description"))
        next_step = _useful_point(action.get("next_step"))
        subject_key = re.sub(r"\W+", "", subject).casefold()
        for name, value in (("description", description), ("next_step", next_step)):
            mostly_english = bool(value) and len(re.findall(r"[A-Za-z]", value)) > max(12, len(value) * 0.55)
            repeated_subject = bool(value and subject_key and subject_key[:30] in re.sub(r"\W+", "", value).casefold())
            if len(value) > 200 or mostly_english or repeated_subject:
                if name == "description":
                    description = ""
                else:
                    next_step = ""
        if re.search(r"防范.*(?:诈骗|电诈)|反诈", subject):
            description = next_step = ""
        if description:
            action_lines.append(description)
        if next_step and next_step.casefold() != description.casefold():
            action_lines.append(f"下一步：{next_step}")
        if not action_lines and not re.search(r"防范.*(?:诈骗|电诈)|反诈", subject):
            fallback = _fallback_action(category, f"{subject}\n{body}", points_for_action)
            if fallback:
                action_lines.append(fallback)
    due, expired = _deadline_display(deadline, email)
    if bool(deadline.get("has_deadline")):
        if due and category != "invoice_receipt":
            if expired:
                action_lines = [
                    ("该活动时间已过；请仅在仍需补办或了解后续安排时查看原邮件。"
                     if _text(_mapping(decision.get("notification")).get("special_card"), 30) == "event"
                     else "邮件中的截止日期已过；如事项仍有效，请先核实当前状态再处理。")
                ]
                action_lines.append(f"原截止时间：{_code(due)}")
            else:
                action_lines.append(f"截止时间：{_code(due)}")
    if action_lines:
        _section(lines, "需要处理", [f"- {x}" for x in action_lines])
        blocks.append("需要处理")

    links = _links(email, category)
    if links:
        link_title = "论文与资料" if category == "academic_report_digest" else "快捷操作"
        _section(lines, link_title, [f"- [{label}]({url})" for label, url in links])
        blocks.append(link_title)

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
        _section(lines, "风险提示", [f"> {'；'.join(x.rstrip('。.!！；; ') for x in notes)}。"])
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
