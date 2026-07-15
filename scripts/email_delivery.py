#!/usr/bin/env python3
"""Email delivery planner side effects and chat-ready formatting."""

import hashlib
import json
import os
import re
import subprocess
import shutil
from html import unescape
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
    analysis = analysis or {}
    attachments = attachments or []
    schedule = schedule or []
    import re
    from html import unescape as _html_unescape

    def clean(value, default=""):
        if value is None:
            return default
        text = str(value)
        text = text.replace("\\n", "\n").replace("\\t", "\t")
        text = _html_unescape(text)
        text = re.sub(r"<(br|BR)\s*/?>", "\n", text)
        text = re.sub(r"</(p|div|li|tr|h[1-6])\s*>", "\n", text, flags=re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = text.replace("\u00a0", " ")
        text = re.sub(r"[ \t\f\v]+", " ", text)
        text = re.sub(r" *\n *", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip() or default

    def inline(value, default="", limit=260):
        text = clean(value, default)
        text = re.sub(r"\s+", " ", text).strip()
        text = text.replace("`", "'")
        if limit and len(text) > limit:
            return text[: max(0, limit - 1)].rstrip() + "…"
        return text

    def strip_headers_footers(text):
        kept = []
        for raw in clean(text).splitlines():
            line = raw.strip()
            if not line:
                kept.append("")
                continue
            if re.match(r"(?i)^(from|to|cc|bcc|subject|body|date|sent)\s*[:：]", line):
                continue
            if re.match(r"^(发件人|收件人|抄送|主题|正文|时间)\s*[:：]", line):
                continue
            if line.lower() in {"body:", "summary:", "正文:", "摘要:"}:
                continue
            if re.search(r"(举报退订|unsubscribe|auto[- ]?sent|automatically sent|此邮件由.*自动发送)", line, re.I):
                continue
            kept.append(line)
        text = "\n".join(kept)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def is_heading_or_list(line):
        s = line.strip()
        if not s:
            return True
        if re.match(r"^([#>*\-•·]|\d+[.)、]|[一二三四五六七八九十]+[、.])\s*", s):
            return True
        if re.match(r"^(附件|摘要|要点|正文|原文|建议|截止|提醒|风险提示)\s*[:：]?$", s):
            return True
        if re.match(r"^[A-Z][A-Z0-9 _/\-]{2,}$", s):
            return True
        return False

    def is_soft_break(prev, curr):
        p = prev.rstrip()
        c = curr.lstrip()
        if not p or not c:
            return False
        if is_heading_or_list(c):
            return False
        if re.search(r"[。！？!?；;：:]$", p):
            return False
        if re.search(r"[.!?]$", p) and re.search(r"^[A-Z0-9\"'“‘]", c):
            return False
        if re.match(r"^(Best|Regards|Thanks|Sincerely|此致|敬礼|署名|落款)$", c, re.I):
            return False
        if re.search(r"[A-Za-z0-9,，、)\]）]$", p) and re.search(r"^[A-Za-z0-9(（]", c):
            return True
        if re.search(r"[\u4e00-\u9fffA-Za-z0-9,，、]$", p) and re.search(r"^[\u4e00-\u9fffA-Za-z0-9]", c):
            return True
        return False

    def paragraphize(text, max_paragraphs=None, max_chars=None):
        text = strip_headers_footers(text)
        if not text:
            return []
        raw_lines = [x.strip() for x in text.splitlines()]
        paragraphs = []
        cur = ""
        for line in raw_lines:
            if not line:
                if cur:
                    paragraphs.append(cur.strip())
                    cur = ""
                continue
            if not cur:
                cur = line
                continue
            if is_soft_break(cur, line):
                glue = "" if re.search(r"[\u4e00-\u9fff]$", cur) and re.search(r"^[\u4e00-\u9fff]", line) else " "
                cur = cur.rstrip() + glue + line.lstrip()
            else:
                paragraphs.append(cur.strip())
                cur = line
        if cur:
            paragraphs.append(cur.strip())

        cleaned = []
        seen = set()
        total = 0
        for p in paragraphs:
            p = re.sub(r"\s+", " ", p).strip()
            p = re.sub(r"\s+([，。！？；：,.!?;:])", r"\1", p)
            p = re.sub(r"([（(])\s+", r"\1", p)
            p = re.sub(r"\s+([）)])", r"\1", p)
            if not p:
                continue
            key = re.sub(r"\s+", "", p.lower())[:80]
            if key in seen:
                continue
            seen.add(key)
            if max_chars and total + len(p) > max_chars:
                remain = max_chars - total
                if remain > 60:
                    cleaned.append(p[: remain - 1].rstrip() + "…")
                break
            cleaned.append(p)
            total += len(p)
            if max_paragraphs and len(cleaned) >= max_paragraphs:
                break
        return cleaned

    def norm(value):
        text = clean(value)
        text = re.sub(r"[`*_>\-\s\"'“”‘’\[\]（）()<>：:，,。.!！?？|｜]+", "", text)
        return text.lower()

    def quote_block(text, max_paragraphs=6, max_chars=1200):
        paras = paragraphize(text, max_paragraphs=max_paragraphs, max_chars=max_chars)
        if not paras:
            return ""
        # Preserve paragraph boundaries inside Markdown blockquote.
        # A plain blank line would end the quote; `>` keeps the visual gap within it.
        # Visible blank quote line: Weixin may collapse a bare '>' line, so use ideographic space.
        # Separate blockquote paragraphs with a real Markdown blank line for Weixin-visible spacing.
        return "\n\n".join("> " + p for p in paras)

    def sender_text():
        name = inline(email.get("from_name"), limit=100).strip().strip('"').strip()
        addr = inline(email.get("from_addr") or email.get("from_email"), limit=160)
        if name and addr and name.lower() not in addr.lower():
            return f"{name} <{addr}>"
        return name or addr or inline(_sender_display(email), "未知发件人", 220)

    def level_text(value):
        value = inline(value, "medium", 60).lower()
        return {"urgent": "紧急", "high": "重要", "medium": "普通", "low": "低优先级", "ignore": "低优先级", "skip": "跳过"}.get(value, value or "普通")

    subject = inline(email.get("subject"), "无主题", 260)
    account = inline(email.get("account"), "Email", 80)
    sender = sender_text()
    raw_sender = (email.get("from_addr") or email.get("from_email") or "") + " " + (email.get("from_name") or "")
    body_raw = email.get("body") or email.get("body_plain") or email.get("plain") or email.get("text") or ""
    body = strip_headers_footers(body_raw)
    summary_raw = strip_headers_footers(analysis.get("formatted_summary") or email.get("summary") or "")

    def is_weekly_report():
        blob = f"{subject}\n{sender}\n{raw_sender}".lower()
        return ("周报" in blob or "weekly briefing" in blob or "weekly-briefing" in blob or "agent mail" in body.lower())

    def category_text(value):
        if is_weekly_report():
            return "学术周报"
        value = inline(value, "email", 80)
        return {
            "verification_code": "验证码", "invoice_receipt": "发票/收据", "school_notice": "学校通知",
            "personal_task": "个人邮件", "paper_feedback": "论文/审稿", "meeting_event": "会议/活动",
            "marketing": "营销", "security_alert": "安全提醒", "system_notification": "系统通知",
            "email": "邮件", "other": "邮件",
        }.get(value, value)

    def local_time_text(value):
        raw = inline(value, limit=180)
        if not raw:
            return ""
        try:
            from datetime import datetime, timezone, timedelta
            from email.utils import parsedate_to_datetime
            s = raw.replace("Z", "+00:00")
            try:
                d = datetime.fromisoformat(s)
            except Exception:
                d = parsedate_to_datetime(raw)
            if d.tzinfo is None:
                return raw
            return d.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M SGT")
        except Exception:
            return raw

    def action_text():
        action = analysis.get("action_needed") if isinstance(analysis.get("action_needed"), dict) else {}
        if not action:
            return ""
        return inline(action.get("description") or action.get("next_step") or ("需要处理" if action.get("required") else ""), limit=220)

    def has_deadline():
        deadline = analysis.get("deadline") if isinstance(analysis.get("deadline"), dict) else {}
        return bool(deadline.get("has_deadline") or deadline.get("datetime") or deadline.get("date_text"))

    def should_drop_summary(summary):
        if not summary:
            return True
        s_norm = norm(summary)
        subj_norm = norm(subject)
        if len(s_norm) < 12:
            return True
        if subj_norm and (s_norm in subj_norm or subj_norm in s_norm):
            return True
        if re.match(r"^\[[^\]]+\]\s*", summary.strip()) and subj_norm in s_norm:
            return True
        return False

    def make_bullets():
        kp = analysis.get("key_points") or analysis.get("points")
        if isinstance(kp, list):
            items = [inline(x, limit=120) for x in kp if inline(x, limit=120)]
            if items:
                return items[:4]
        paras = paragraphize(body, max_paragraphs=8, max_chars=1800)
        if len(body) < 360 and not is_weekly_report():
            return []
        keywords = [
            "arxiv", "crossref", "web_search", "search", "irrelevant", "dataset", "benchmark", "satellite",
            "realtime", "real-time", "hyperspectral", "segmentation", "detection", "smoke", "wildfire",
            "paper", "review", "submission", "attachment", "pdf", "report", "deadline", "submit",
            "meeting", "need", "完成", "报告", "周报", "数据集", "星载", "实时", "高光谱", "分割", "检测", "论文", "附件",
        ]
        scored = []
        for idx, p in enumerate(paras):
            low = p.lower()
            score = max(0, 5 - idx) * 0.3
            score += min(4, len(re.findall(r"[A-Z][A-Za-z0-9\-]{2,}", p))) * 0.6
            score += min(3, len(re.findall(r"\d+", p))) * 0.4
            for kw in keywords:
                if kw.lower() in low:
                    score += 1.0
            if len(p) < 16:
                score -= 2.0
            if re.match(r"^(hey|hi|hello|dear)\b", p, re.I):
                score -= 2.0
            scored.append((score, idx, p))
        scored.sort(key=lambda x: (-x[0], x[1]))
        picked = []
        seen = set()
        for score, idx, p in scored:
            if score < 0.8 and picked:
                continue
            item = inline(p, limit=130)
            key = norm(item)[:70]
            if not key or key in seen:
                continue
            seen.add(key)
            picked.append((idx, item))
            if len(picked) >= (4 if is_weekly_report() else 3):
                break
        picked.sort()
        return [x for _, x in picked]

    def attachment_name(att):
        name = inline(att.get("filename") or att.get("name") or "", limit=180)
        bad = {"", "(attachments present)", "attachments present", "attachment present", "listed", "unknown"}
        if name.strip().lower() in bad:
            return ""
        return name

    importance = analysis.get("user_relevance") or email.get("importance") or "medium"
    category = analysis.get("semantic_category") or analysis.get("final_category") or email.get("rule_category") or "email"
    level = level_text(importance)
    category_label = category_text(category)
    date_sent = local_time_text(email.get("date_sent"))
    action = action_text()

    if (analysis.get("format_decision") or "") == "code_extraction":
        code = inline(analysis.get("code") or _extract_code(f"{subject}\n{body}"), "未识别", 80)
        lines = [f"### 🔐 验证码｜{account}", "", f"`{level}` · `{category_label}`", "", f"**验证码**  \n{code}", "", f"**发件人**  \n{sender}", "", f"**主题**  \n{subject}"]
        if date_sent:
            lines.extend(["", f"**时间**  \n{date_sent}"])
        return "\n".join(lines).strip()

    icon = "🚨" if level == "紧急" else ("📌" if level == "重要" else ("📰" if is_weekly_report() else "📬"))
    title = "学术周报" if is_weekly_report() else "新邮件"
    meta = f"`{level}` · `{category_label}`"
    if date_sent:
        meta += f" · `{date_sent}`"

    lines = [f"### {icon} {title}｜{account}", "", meta, "", "**发件人**", sender, "", "**主题**", subject]

    summary_ok = not should_drop_summary(summary_raw)
    bullets = make_bullets()
    excerpt = quote_block(body, max_paragraphs=(7 if is_weekly_report() else 5), max_chars=(1400 if is_weekly_report() else 1100))

    if summary_ok:
        summary_block = quote_block(summary_raw, max_paragraphs=3, max_chars=420)
        if summary_block:
            lines.extend(["", "**摘要**", summary_block])

    if bullets:
        lines.extend(["", "**要点**"])
        for i, item in enumerate(bullets, 1):
            lines.append(f"{i}. {item}")

    show_excerpt = bool(excerpt) and (not bullets or level in {"重要", "紧急"} or action or has_deadline() or len(body) <= 1400 or is_weekly_report())
    if show_excerpt:
        lines.extend(["", "**原文摘录**", excerpt])

    if action:
        lines.extend(["", f"**建议**  \n{action}"])

    deadline = analysis.get("deadline") if isinstance(analysis.get("deadline"), dict) else {}
    if deadline.get("has_deadline") or deadline.get("datetime") or deadline.get("date_text"):
        lines.append(f"**截止 / 时间**  \n{inline(deadline.get('datetime') or deadline.get('date_text') or '未明确', limit=180)}")

    if attachments:
        good = []
        generic_count = 0
        for att in attachments[:6]:
            name = attachment_name(att)
            if name:
                local_path = inline(att.get("local_path"), limit=220)
                good.append(f"- 📎 {name}" + (f"｜已保存：`{local_path}`" if local_path else ""))
            else:
                generic_count += 1
        lines.extend(["", "**附件**"])
        if good:
            lines.extend(good)
            if len(attachments) > len(good):
                lines.append(f"- 📎 另有 {len(attachments) - len(good)} 个附件未展开")
        else:
            count = len(attachments) or generic_count
            lines.append(f"- 📎 有附件 {count} 个，文件名未解析，请在邮箱查看")
    elif email.get("has_attachments") or email.get("has_attachment"):
        lines.extend(["", "**附件**", "- 📎 有附件，文件名未解析，请在邮箱查看"])

    reminder_lines = []
    for item in schedule:
        if not isinstance(item, dict):
            continue
        for reminder in item.get("reminders", []) or []:
            if not isinstance(reminder, dict):
                continue
            when = inline(reminder.get("time"), limit=120)
            kind = inline(reminder.get("kind"), limit=80)
            msg = inline(reminder.get("message"), limit=180)
            line = " ".join(x for x in [when, kind, msg] if x)
            if line:
                reminder_lines.append(line)
            if len(reminder_lines) >= 4:
                break
        if len(reminder_lines) >= 4:
            break
    if reminder_lines:
        lines.extend(["", "**提醒 / Reminders:**"])
        lines.extend([f"- {x}" for x in reminder_lines])

    risk_notes = []
    for x in analysis.get("risk_notes", []) or []:
        note = inline(x, limit=180)
        if not note:
            continue
        if note.lower() in {"llm disabled", "llm_disabled", "local llm disabled"}:
            continue
        risk_notes.append(note)
    if risk_notes:
        lines.extend(["", "**风险提示**"])
        lines.extend([f"- {x}" for x in risk_notes[:3]])

    text = "\n".join(line.rstrip() for line in lines if line is not None).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


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


# EMAIL_WATCHDOG_HIMALAYA_BIN_RESOLUTION_V1
def _himalaya_binary():
    candidates = [
        os.environ.get("HIMALAYA_BIN", "").strip(),
        "/opt/data/bin/himalaya",
        shutil.which("himalaya") or "",
        "himalaya",
    ]
    seen = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        expanded = os.path.expanduser(candidate)
        if expanded == "himalaya" or os.path.exists(expanded):
            return expanded
    return "himalaya"

def _himalaya_cmd_variants(config_path, args):
    cfg = os.path.expanduser(config_path or "")
    base = _himalaya_binary()
    variants = []
    if cfg:
        variants.append([base, "-c", cfg] + list(args))
        variants.append([base, "--config", cfg] + list(args))
    variants.append([base] + list(args))
    unique = []
    seen = set()
    for cmd in variants:
        key = tuple(cmd)
        if key not in seen:
            unique.append(cmd)
            seen.add(key)
    return unique


def _download_himalaya(config_path, msg_id, save_dir):
    before = _snapshot(save_dir)
    os.makedirs(save_dir, exist_ok=True)
    for cmd in _himalaya_cmd_variants(config_path, ["attachment", "download", str(msg_id), "--downloads-dir", save_dir]):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except FileNotFoundError:
            return []
        except Exception:
            continue
        if result.returncode == 0:
            after = _snapshot(save_dir)
            return sorted(after - before)
    return []

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
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n", text)
    text = re.sub(r'^(?:From|To|Cc|Bcc|Subject|Date|Reply-To|Message-ID|MIME-Version|Content-Type|Content-Transfer-Encoding|Return-Path|Received|X-[A-Za-z-]+):[^\n]*\n?', '', text, flags=re.MULTILINE | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
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

# EMAIL_WATCHDOG_STRUCTURED_FORMAT_AND_LEARNING_FIX_V4
# Safe appended wrappers. They do not change mailbox state or seen state.

def _ew_v4_clean(value, default=""):
    import re
    from html import unescape as _html_unescape
    if value is None:
        return default
    text = str(value)
    text = text.replace("\\n", "\n").replace("\\t", "\t")
    text = _html_unescape(text)
    text = re.sub(r"<(br|BR)\s*/?>", "\n", text)
    text = re.sub(r"</(p|div|li|tr|h[1-6])\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() or default


def _ew_v4_inline(value, default="", limit=260):
    import re
    text = _ew_v4_clean(value, default)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace("`", "'").replace("**", "").replace("__", "")
    if limit and len(text) > limit:
        return text[: max(0, limit - 1)].rstrip() + "…"
    return text


def _ew_v4_norm(value):
    import re
    text = _ew_v4_clean(value)
    text = re.sub(r"[`*_>\-\s\"'“”‘’\[\]（）()<>：:，,。.!！?？|｜#]+", "", text)
    return text.lower()


def _ew_v4_strip_headers_footers(text):
    import re
    kept = []
    for raw in _ew_v4_clean(text).splitlines():
        line = raw.strip()
        if not line:
            kept.append("")
            continue
        if re.match(r"(?i)^(from|to|cc|bcc|subject|body|date|sent)\s*[:：]", line):
            continue
        if re.match(r"^(发件人|收件人|抄送|主题|正文|时间)\s*[:：]", line):
            continue
        if line.lower() in {"body:", "summary:", "正文:", "摘要:"}:
            continue
        if re.search(r"(举报退订|unsubscribe|auto[- ]?sent|automatically sent|此邮件由.*自动发送)", line, re.I):
            continue
        kept.append(line)
    text = "\n".join(kept)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _ew_v4_shape(text):
    import re
    raw = _ew_v4_strip_headers_footers(text)
    lines0 = [x.strip() for x in raw.splitlines() if x.strip()]
    headings = sum(1 for x in lines0 if re.match(r"^#{1,6}\s+\S", x))
    bullets = sum(1 for x in lines0 if re.match(r"^\s*[-*•]\s+\S", x))
    numbered = sum(1 for x in lines0 if re.match(r"^\s*\d+[.)、]\s+\S", x))
    quote = sum(1 for x in lines0 if x.startswith(">"))
    markers = headings + bullets + numbered + quote
    return {"headings": headings, "bullets": bullets, "numbered": numbered, "markers": markers, "lines": len(lines0)}


def _ew_v4_is_weekly(email, body):
    subject = _ew_v4_inline((email or {}).get("subject"), limit=260).lower()
    sender = ((email or {}).get("from_addr") or (email or {}).get("from_email") or "") + " " + ((email or {}).get("from_name") or "")
    blob = f"{subject}\n{sender}\n{body}".lower()
    return ("周报" in blob or "weekly briefing" in blob or "weekly-briefing" in blob or "agent mail" in blob)


def _ew_v4_is_structured(email, body):
    body = body or ""
    shape = _ew_v4_shape(body)
    weekly = _ew_v4_is_weekly(email, body)
    # v2 failed because the sample was only 289 chars. Use content shape first, length second.
    if weekly and (shape["headings"] >= 1 or shape["bullets"] >= 2 or shape["numbered"] >= 2) and len(body) >= 120:
        return True
    if shape["headings"] >= 2 and len(body) >= 160:
        return True
    if (shape["bullets"] >= 4 or shape["numbered"] >= 3) and len(body) >= 180:
        return True
    if shape["lines"] and (shape["markers"] / max(1, shape["lines"])) >= 0.28 and shape["markers"] >= 3 and len(body) >= 160:
        return True
    return False


def _ew_v4_plain_line(line, limit=220):
    import re
    line = _ew_v4_clean(line)
    line = re.sub(r"^\s*>+\s*", "", line)
    line = re.sub(r"^\s{0,3}#{1,6}\s*", "", line)
    line = re.sub(r"^\s*[-*•]\s+", "", line)
    line = re.sub(r"^\s*(\d+)[.)、]\s+", r"\1. ", line)
    line = line.replace("**", "").replace("__", "").replace("`", "")
    line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
    line = re.sub(r"\s+", " ", line).strip()
    return _ew_v4_inline(line, limit=limit) if line else ""


def _ew_v4_skip_title(text, subject, out_len):
    n = _ew_v4_norm(text)
    subj = _ew_v4_norm(subject)
    if not n:
        return True
    if out_len == 0 and subj and (n in subj or subj in n):
        return True
    if out_len == 0 and ("学术研究周报" in text or "weekly briefing" in text.lower()):
        return True
    return False


def _ew_v4_structured_excerpt(text, subject="", max_lines=12, max_chars=1500):
    import re
    raw = _ew_v4_strip_headers_footers(text)
    out = []
    seen = set()
    total = 0
    in_fence = False
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = re.match(r"^#{1,6}\s+(.+)$", line)
        if m:
            title = _ew_v4_plain_line(m.group(1), limit=80)
            if _ew_v4_skip_title(title, subject, len(out)):
                continue
            item = f"【{title}】" if title else ""
        elif re.match(r"^\s*[-*•]\s+\S", line):
            item = "• " + _ew_v4_plain_line(line, limit=180)
        elif re.match(r"^\s*\d+[.)、]\s+\S", line):
            item = _ew_v4_plain_line(line, limit=180)
        else:
            item = _ew_v4_plain_line(line, limit=220)
        if not item:
            continue
        key = _ew_v4_norm(item)[:90]
        if not key or key in seen:
            continue
        seen.add(key)
        if total + len(item) > max_chars:
            break
        out.append(item)
        total += len(item)
        if len(out) >= max_lines:
            break
    return "\n".join(out).strip()


def _ew_v4_key_points(text, subject="", max_items=4):
    import re
    raw = _ew_v4_strip_headers_footers(text)
    out = []
    seen = set()
    last_heading = ""
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("```"):
            continue
        m = re.match(r"^#{1,6}\s+(.+)$", line)
        if m:
            title = _ew_v4_plain_line(m.group(1), limit=80)
            if _ew_v4_skip_title(title, subject, len(out)):
                continue
            last_heading = title
            continue
        item = _ew_v4_plain_line(line, limit=130)
        if not item:
            continue
        if last_heading and not re.match(r"^\d+\.\s", item) and not re.match(r"^[A-Za-z0-9_\- ]+[:：]", item):
            item = f"{last_heading}：{item}"
            last_heading = ""
        key = _ew_v4_norm(item)[:80]
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= max_items:
            break
    return out


def _ew_v4_level(value):
    value = _ew_v4_inline(value, "medium", 60).lower()
    return {"urgent": "紧急", "high": "重要", "medium": "普通", "low": "低优先级", "ignore": "低优先级", "skip": "跳过"}.get(value, value or "普通")


def _ew_v4_category(value, weekly=False):
    if weekly:
        return "学术周报"
    value = _ew_v4_inline(value, "email", 80)
    return {"verification_code": "验证码", "invoice_receipt": "发票/收据", "school_notice": "学校通知", "personal_task": "个人邮件", "paper_feedback": "论文/审稿", "meeting_event": "会议/活动", "marketing": "营销", "security_alert": "安全提醒", "system_notification": "系统通知", "newsletter": "邮件", "email": "邮件", "other": "邮件"}.get(value, value)


def _ew_v4_time(value):
    raw = _ew_v4_inline(value, limit=180)
    if not raw:
        return ""
    try:
        from datetime import datetime, timezone, timedelta
        from email.utils import parsedate_to_datetime
        s = raw.replace("Z", "+00:00")
        try:
            d = datetime.fromisoformat(s)
        except Exception:
            d = parsedate_to_datetime(raw)
        if d.tzinfo is None:
            return raw
        return d.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M SGT")
    except Exception:
        return raw


def _ew_v4_sender(email):
    name = _ew_v4_inline((email or {}).get("from_name"), limit=100).strip().strip('"').strip()
    addr = _ew_v4_inline((email or {}).get("from_addr") or (email or {}).get("from_email"), limit=160)
    if name and addr and name.lower() not in addr.lower():
        return f"{name} <{addr}>"
    return name or addr or "未知发件人"


def _ew_v4_attachment_name(att):
    if not isinstance(att, dict):
        return ""
    name = _ew_v4_inline(att.get("filename") or att.get("name") or "", limit=180)
    if name.strip().lower() in {"", "(attachments present)", "attachments present", "attachment present", "listed", "unknown"}:
        return ""
    return name


def _ew_v4_format_structured(email, analysis, attachments, schedule):
    email = email or {}
    analysis = analysis or {}
    attachments = attachments or []
    subject = _ew_v4_inline(email.get("subject"), "无主题", 260)
    account = _ew_v4_inline(email.get("account"), "Email", 80)
    sender = _ew_v4_sender(email)
    body = _ew_v4_strip_headers_footers(email.get("body") or email.get("body_plain") or email.get("plain") or email.get("text") or "")
    summary_raw = _ew_v4_strip_headers_footers(analysis.get("formatted_summary") or email.get("summary") or "")
    weekly = _ew_v4_is_weekly(email, body)
    level = _ew_v4_level(analysis.get("user_relevance") or email.get("importance") or "medium")
    category_label = _ew_v4_category(analysis.get("semantic_category") or analysis.get("final_category") or email.get("rule_category") or "email", weekly=weekly)
    date_sent = _ew_v4_time(email.get("date_sent"))
    icon = "🚨" if level == "紧急" else ("📌" if level == "重要" else ("📰" if weekly else "📬"))
    title = "学术周报" if weekly else "新邮件"
    meta = f"`{level}` · `{category_label}`"
    if date_sent:
        meta += f" · `{date_sent}`"
    lines = [f"### {icon} {title}｜{account}", "", meta, "", "**发件人**", sender, "", "**主题**", subject]

    if summary_raw:
        summary_lines = []
        ex = _ew_v4_structured_excerpt(summary_raw, subject=subject, max_lines=5, max_chars=360)
        for x in ex.splitlines():
            if x.startswith("【") and x.endswith("】"):
                continue
            if x.startswith("• "):
                x = x[2:]
            if x:
                summary_lines.append(_ew_v4_inline(x, limit=180))
            if len(summary_lines) >= 2:
                break
        if summary_lines:
            lines.extend(["", "**摘要**"] + summary_lines)

    bullets = _ew_v4_key_points(body, subject=subject, max_items=4)
    if bullets:
        lines.extend(["", "**要点**"])
        for idx, item in enumerate(bullets, 1):
            lines.append(f"{idx}. {item}")

    excerpt = _ew_v4_structured_excerpt(body, subject=subject, max_lines=(12 if weekly else 10), max_chars=(1500 if weekly else 1200))
    if excerpt:
        lines.extend(["", "**结构化摘录**", excerpt])

    if attachments:
        good = []
        generic_count = 0
        for att in attachments[:6]:
            name = _ew_v4_attachment_name(att)
            if name:
                local_path = _ew_v4_inline(att.get("local_path"), limit=220)
                good.append(f"- 📎 {name}" + (f"｜已保存：`{local_path}`" if local_path else ""))
            else:
                generic_count += 1
        lines.extend(["", "**附件**"])
        if good:
            lines.extend(good)
            if len(attachments) > len(good):
                lines.append(f"- 📎 另有 {len(attachments) - len(good)} 个附件未展开")
        else:
            count = len(attachments) or generic_count
            lines.append(f"- 📎 有附件 {count} 个，文件名未解析，请在邮箱查看")
    elif email.get("has_attachments") or email.get("has_attachment"):
        lines.extend(["", "**附件**", "- 📎 有附件，文件名未解析，请在邮箱查看"])

    return "\n".join(lines).strip()


try:
    _ew_v4_original_format_notification = format_notification
except NameError:
    _ew_v4_original_format_notification = None

if _ew_v4_original_format_notification is not None and not getattr(_ew_v4_original_format_notification, "_email_watchdog_structured_wrapper_v4", False):
    def format_notification(email, analysis, attachments, schedule):
        body0 = _ew_v4_strip_headers_footers((email or {}).get("body") or (email or {}).get("body_plain") or (email or {}).get("plain") or (email or {}).get("text") or "")
        if _ew_v4_is_structured(email or {}, body0):
            return _ew_v4_format_structured(email or {}, analysis or {}, attachments or [], schedule or [])
        return _ew_v4_original_format_notification(email, analysis, attachments, schedule)
    format_notification._email_watchdog_structured_wrapper_v4 = True


# EMAIL_WATCHDOG_DELIVERY_LEARNING_WRAPPER_V4
try:
    _ew_v4_deliver_for_learning_wrap = deliver_email
except NameError:
    _ew_v4_deliver_for_learning_wrap = None

if _ew_v4_deliver_for_learning_wrap is not None and not getattr(_ew_v4_deliver_for_learning_wrap, "_email_watchdog_learning_wrapper_v4", False):
    _ew_v4_original_deliver_email = _ew_v4_deliver_for_learning_wrap

    def deliver_email(email, rule_result, analysis, account):
        result = _ew_v4_original_deliver_email(email, rule_result, analysis, account)
        try:
            import email_learning
            rec = email_learning.record_decision(email or {}, rule_result or {}, analysis or {}, result or {}, account or {})
            try:
                import logging
                logging.getLogger(__name__).warning("Hermes Email Watchdog learning shadow record ok=%s", rec.get("ok"))
            except Exception:
                pass
        except Exception as exc:
            try:
                import logging
                logging.getLogger(__name__).warning("Hermes Email Watchdog learning shadow record failed: %r", exc)
            except Exception:
                pass
        return result
    deliver_email._email_watchdog_learning_wrapper_v4 = True



# EMAIL_WATCHDOG_SEMANTIC_ENGINE_AND_ADAPTIVE_RENDERER_SHADOW_V3
# Normal delivery and legacy-rule skip paths share one observation-only pipeline.
# The helper never changes production notification bytes and never calls mailbox,
# attachment, reminder, cron, outbox, or Weixin side effects.
def _ew_run_semantic_renderer_memory_shadow(email, rule_result, analysis, result, account):
    semantic_rec = {}
    renderer_rec = {}
    memory_rec = {}
    decision = None
    try:
        # EMAIL_WATCHDOG_SEMANTIC_ENGINE_RELOAD_EACH_DELIVERY_V1
        # email_delivery itself is reloaded each watchdog poll. Reload the semantic
        # engine here so source/config upgrades take effect without restarting the
        # Hermes container; production notification bytes remain untouched.
        import importlib
        import email_semantic_engine
        email_semantic_engine = importlib.reload(email_semantic_engine)
        semantic_rec = email_semantic_engine.shadow_observe(
            email or {}, rule_result or {}, analysis or {}, result or {}, account or {}
        )
        decision = semantic_rec.get("decision") if isinstance(semantic_rec, dict) else None
        if semantic_rec.get("ok") and isinstance(decision, dict):
            try:
                import email_notification_renderer
                renderer_rec = email_notification_renderer.shadow_compare(
                    email or {},
                    decision,
                    result or {},
                    account or {},
                    message_key=semantic_rec.get("message_key") or "",
                )
            except Exception as exc:
                renderer_rec = {"ok": False, "error": repr(exc), "production_notification_changed": False}
            try:
                # EMAIL_WATCHDOG_SEMANTIC_MEMORY_SHADOW_INTEGRATION_V1
                import email_semantic_memory
                memory_rec = email_semantic_memory.shadow_observe(
                    email or {},
                    decision,
                    account or {},
                    message_key=semantic_rec.get("message_key") or "",
                    renderer=renderer_rec,
                    semantic_meta=semantic_rec,
                )
            except Exception as exc:
                memory_rec = {"ok": False, "error": repr(exc), "runtime_activation": False}
        try:
            import logging
            classification = decision.get("classification") if isinstance(decision, dict) else {}
            render_meta = renderer_rec.get("render") if isinstance(renderer_rec, dict) else {}
            logging.getLogger(__name__).warning(
                "Hermes Email Watchdog semantic+renderer+memory shadow semantic_ok=%s llm_called=%s category=%s fallback=%s cache_hit=%s renderer_ok=%s mode=%s chars=%s duplicate_suppressions=%s memory_ok=%s memory_observations=%s runtime_activation=%s source=%s",
                semantic_rec.get("ok"), semantic_rec.get("llm_called"), classification.get("category"),
                semantic_rec.get("fallback_used"), semantic_rec.get("cache_hit"), renderer_rec.get("ok"),
                render_meta.get("content_mode"), render_meta.get("notification_chars"),
                render_meta.get("duplicate_suppression_count"), memory_rec.get("ok"),
                memory_rec.get("observation_count"), memory_rec.get("runtime_activation"),
                (result or {}).get("shadow_source") or "normal_delivery",
            )
        except Exception:
            pass
    except Exception as exc:
        try:
            import logging
            logging.getLogger(__name__).warning("Hermes Email Watchdog semantic+renderer shadow failed: %r", exc)
        except Exception:
            pass
    return {
        "ok": bool(semantic_rec.get("ok")) and (not renderer_rec or bool(renderer_rec.get("ok"))),
        "semantic": semantic_rec,
        "renderer": renderer_rec,
        "memory": memory_rec,
        "production_notification_changed": False,
        "weixin_send": False,
        "mailbox_write": False,
        "attachment_side_effect": False,
        "reminder_side_effect": False,
    }


def observe_shadow_only(email, rule_result, account, *, legacy_reason="legacy_rule_skip"):
    """Observe a legacy-skipped email without invoking production delivery.

    This is the bridge that removes the old rule-classifier blind spot while the
    LLM-first engine remains shadow-only. The semantic decision may recommend a
    notification, but this function intentionally returns no production text and
    performs no delivery side effects.
    """
    analysis = {
        "semantic_category": "",
        "user_relevance": "",
        "should_notify": None,
        "formatted_summary": "",
        "action_needed": None,
        "deadline": None,
        "risk_notes": [],
        "shadow_only": True,
        "shadow_source": str(legacy_reason or "legacy_rule_skip")[:80],
    }
    result = {
        "notification_text": "",
        "attachments": [],
        "schedule": [],
        "status": "legacy_skipped_shadow_only",
        "shadow_only": True,
        "shadow_source": str(legacy_reason or "legacy_rule_skip")[:80],
    }
    observed = _ew_run_semantic_renderer_memory_shadow(
        email or {}, rule_result or {}, analysis, result, account or {}
    )
    return {
        "ok": bool(observed.get("ok")),
        "status": "legacy_skipped_shadow_only",
        "notification_text": "",
        "production_delivery_called": False,
        "production_notification_changed": False,
        "weixin_send": False,
        "mailbox_write": False,
        "attachment_side_effect": False,
        "reminder_side_effect": False,
        "observation": observed,
    }


try:
    _ew_phase3_deliver_for_shadow_wrap = deliver_email
except NameError:
    _ew_phase3_deliver_for_shadow_wrap = None

if _ew_phase3_deliver_for_shadow_wrap is not None and not getattr(_ew_phase3_deliver_for_shadow_wrap, "_email_watchdog_semantic_renderer_shadow_v3", False):
    _ew_phase3_original_deliver_email = _ew_phase3_deliver_for_shadow_wrap

    def deliver_email(email, rule_result, analysis, account):
        result = _ew_phase3_original_deliver_email(email, rule_result, analysis, account)
        _ew_run_semantic_renderer_memory_shadow(
            email or {}, rule_result or {}, analysis or {}, result or {}, account or {}
        )
        return result

    deliver_email._email_watchdog_semantic_renderer_shadow_v3 = True


# EMAIL_WATCHDOG_PRODUCTION_ADAPTIVE_ROUTE_V1
# One route owns the final notification bytes. Fast deterministic decisions and
# durable semantic decisions both feed Adaptive Renderer V1b. Any failure calls
# the legacy base delivery exactly once. The existing scheduler/outbox remains
# the sole Weixin delivery owner.
def production_route_enabled():
    try:
        import email_production_router
        return bool(email_production_router.production_enabled())
    except Exception:
        return False


def _ew_prod_record_learning(email, rule_result, analysis, result, account):
    try:
        import email_learning
        email_learning.record_decision(email or {}, rule_result or {}, analysis or {}, result or {}, account or {})
    except Exception:
        pass


def _ew_prod_record_memory(email, decision, account, semantic_meta, renderer_meta):
    try:
        import email_semantic_memory
        email_semantic_memory.shadow_observe(
            email or {}, decision or {}, account or {},
            message_key=(semantic_meta or {}).get("message_key") or "",
            renderer=renderer_meta or {}, semantic_meta=semantic_meta or {},
        )
    except Exception:
        pass


try:
    _ew_prod_previous_deliver_email = deliver_email
except NameError:
    _ew_prod_previous_deliver_email = None

if _ew_prod_previous_deliver_email is not None and not getattr(_ew_prod_previous_deliver_email, "_email_watchdog_production_adaptive_route_v1", False):
    def deliver_email(email, rule_result, analysis, account):
        if not production_route_enabled():
            return _ew_prod_previous_deliver_email(email, rule_result, analysis, account)

        import importlib
        import email_production_router
        import email_feature_extractor
        import email_notification_renderer
        import email_semantic_engine

        email_production_router = importlib.reload(email_production_router)
        email_feature_extractor = importlib.reload(email_feature_extractor)
        email_notification_renderer = importlib.reload(email_notification_renderer)
        email_semantic_engine = importlib.reload(email_semantic_engine)

        semantic_meta = {}
        renderer_meta = {}
        route_lane = "durable"
        route_reason = []
        try:
            features = email_production_router.extract_features(email or {})
            lane = email_production_router.classify_fast_lane(email or {}, features)
            if lane.get("fast_lane"):
                route_lane = "fast"
                route_reason = list(lane.get("reasons") or [])
                decision = email_production_router.build_fast_decision(
                    email or {}, rule_result or {}, analysis or {}, features, lane
                )
                semantic_meta = {
                    "ok": True, "schema_valid": True, "fallback_used": False,
                    "timeout": False, "decision": decision,
                    "message_key": features.get("message_key") or "",
                    "fast_lane": True, "fast_lane_kind": lane.get("kind") or "",
                    "core_protocol": "readable_grounded_core_v1u",
                }
            else:
                semantic_meta = email_semantic_engine.analyze_email(
                    email or {}, rule_result or {}, analysis or {},
                    settings_override={"mode": "shadow", "cache_by_message_hash": True},
                )
                if not semantic_meta.get("ok") or not semantic_meta.get("schema_valid"):
                    raise RuntimeError("semantic engine did not return a valid decision")
                if semantic_meta.get("fallback_used") or semantic_meta.get("timeout"):
                    raise RuntimeError(
                        "semantic engine fallback: " + str(semantic_meta.get("error_code") or "unknown")
                    )
                decision = semantic_meta.get("decision")
                if not isinstance(decision, dict):
                    raise RuntimeError("semantic decision missing")

            prod_analysis = email_production_router.decision_to_legacy_analysis(decision, analysis or {})
            attachments = download_attachments(email or {}, prod_analysis, account or {})
            schedule = upsert_schedule(email or {}, prod_analysis)
            cron_entries = install_reminder_cron(schedule)
            renderer_meta = email_notification_renderer.render_notification(
                email or {}, decision,
                {"attachments": attachments, "schedule": schedule},
                account or {},
                settings_override={
                    "renderer": "adaptive_v1e", "mode": "production",
                    "original_policy": "auto", "show_debug_reason": False,
                },
            )
            text = str(renderer_meta.get("text") or "").strip()
            if not renderer_meta.get("ok") or not text:
                raise RuntimeError("adaptive renderer returned empty or invalid text")

            _persist_delivery(email or {}, prod_analysis, text, "pushed")
            result = {
                "notification_text": text,
                "attachments": attachments,
                "schedule": schedule,
                "cron_entries": cron_entries,
                "status": "pushed",
                "production_route": "adaptive_v1e",
                "route_lane": route_lane,
                "route_reasons": route_reason,
                "semantic": semantic_meta,
                "renderer": renderer_meta,
                "legacy_fallback_used": False,
            }
            _ew_prod_record_learning(email, rule_result, prod_analysis, result, account)
            _ew_prod_record_memory(email, decision, account, semantic_meta, renderer_meta)
            return result
        except Exception as exc:
            fallback_analysis = email_production_router.legacy_fallback_analysis(
                email or {}, rule_result or {}, analysis or {}, repr(exc)
            )
            # Call the unwrapped legacy base once. This preserves attachment,
            # reminder and persistence behavior without running a second semantic
            # observation or producing a second notification candidate.
            result = _ew_v4_original_deliver_email(
                email or {}, rule_result or {}, fallback_analysis, account or {}
            )
            result = dict(result or {})
            result.update({
                "production_route": "legacy_fallback",
                "route_lane": route_lane,
                "route_reasons": route_reason,
                "legacy_fallback_used": True,
                "production_route_error": repr(exc)[:800],
            })
            _ew_prod_record_learning(email, rule_result, fallback_analysis, result, account)
            try:
                import logging
                logging.getLogger(__name__).warning(
                    "Hermes Email Watchdog production adaptive route fell back to legacy: %r", exc
                )
            except Exception:
                pass
            return result

    deliver_email._email_watchdog_production_adaptive_route_v1 = True
