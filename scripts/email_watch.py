#!/usr/bin/env python3
"""
Email Watchdog v2 — multi-account monitoring with 15+ classification categories,
attachment downloading, sleep-time suppression, and WeChat push.

Runs as no_agent cron job. Zero tokens when idle. Silent during sleep hours.
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone, time as dtime
from pathlib import Path

# ── New v3 modules ──
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
try:
    import email_store
    import email_trust
    import email_risk
    import email_push
    HAS_V3 = True
except ImportError as e:
    HAS_V3 = False

# ── Contact & thread integration ──
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
try:
    import email_contacts
    import email_reply
    HAS_MODULES = True
except ImportError:
    HAS_MODULES = False

# ── Configuration ───────────────────────────────────────────────

SEEN_FILE = os.path.expanduser("~/.hermes/email_watch_seen.json")
LOOKBACK = 5
MAX_PER_TICK = 1  # push 1 email per tick; WeChat limit = 10 consecutive messages
SLEEP_START = 0   # 00:00
SLEEP_END = 6     # 06:00
INVOICE_DIR = os.path.expanduser("~/Documents/Invoices")
ATTACHMENT_DIR = os.path.expanduser("~/Documents/EmailAttachments")

ACCOUNTS = [
    {"name": "USTC", "type": "himalaya",
     "config": os.path.expanduser("~/.config/himalaya/config_ustc.toml"),
     "email": "wmwen@mail.ustc.edu.cn"},
    {"name": "Gmail", "type": "himalaya",
     "config": os.path.expanduser("~/.config/himalaya/config_gmail.toml"),
     "email": "wmwen1999@gmail.com"},
    {"name": "Agently", "type": "agently",
     "email": "augenstern@agent.qq.com"},
]


# ── Email Content Cache ──────────────────────────────────────

CACHE_DIR = os.path.expanduser("~/.hermes/email_cache")
MAX_CACHED = 200  # keep last 200 emails

def _cache_full_email(acct_name, msg_id, subject, from_addr, from_name, body, has_attachments):
    """Save full email body to disk for later retrieval via WeChat commands."""
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        cache_file = os.path.join(CACHE_DIR, f"{msg_id}.json")
        data = {
            "account": acct_name, "msg_id": msg_id,
            "subject": subject, "from_addr": from_addr, "from_name": from_name,
            "body": body, "has_attachments": has_attachments,
            "cached_at": datetime.now().isoformat(),
        }
        with open(cache_file, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        # Cleanup old cache
        files = sorted(Path(CACHE_DIR).glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[MAX_CACHED:]:
            old.unlink()
    except:
        pass


def _is_trusted_sender(from_addr):
    """Check if sender is trusted for auto-download of attachments."""
    trusted_domains = ["ustc.edu.cn", "mail.ustc.edu.cn", "edu.cn",
                       "agent.qq.com", "accounts.google.com", "google.com"]
    if any(from_addr.lower().endswith(d) for d in trusted_domains):
        return True
    try:
        import email_contacts as _ec
        contacts = _ec.load_contacts()
        if from_addr.lower() in contacts.get("contacts", {}):
            return True
    except:
        pass
    return False


def _extract_calendar_hints(subject, body):
    """Extract date/deadline hints from email for inline display."""
    text = f"{subject} {body[:1000]}"
    hints = []
    for pat in [r'(\d{1,2})月(\d{1,2})[日号]', r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})']:
        m = re.search(pat, text)
        if m:
            ctx = text[max(0,m.start()-20):m.end()+30].strip()[:70]
            if re.search(r'截止|deadline|请于.*前|之前|due|before', text[max(0,m.start()-40):m.end()]):
                hints.append(f"⏰ 截止: {m.group(0)}")
            elif re.search(r'时间|date|召开|举行', text[max(0,m.start()-40):m.end()]):
                hints.append(f"📅 时间: {m.group(0)}")
            break
    return "\n".join(hints) if hints else ""

def run(cmd, timeout=30):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip(), r.returncode
    except subprocess.TimeoutExpired:
        return "", 124

def load_json(path):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}

def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def load_seen():
    return load_json(SEEN_FILE)

def save_seen(data):
    save_json(SEEN_FILE, data)

def is_sleep_time():
    if SLEEP_START < 0 or SLEEP_END < 0:  # negative values = disabled
        return False
    now = datetime.now().time()
    if SLEEP_START < SLEEP_END:
        return SLEEP_START <= now.hour < SLEEP_END
    else:
        return now.hour >= SLEEP_START or now.hour < SLEEP_END


# ── Email fetching ──────────────────────────────────────────────

def list_himalaya(config_path):
    cmd = f"himalaya -c {config_path} envelope list --page-size {LOOKBACK} --output json 2>/dev/null"
    out, rc = run(cmd, timeout=30)
    if rc != 0:
        return []
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return []

def read_himalaya(config_path, msg_id):
    cmd = f"himalaya -c {config_path} message read {msg_id} --output json 2>/dev/null"
    out, rc = run(cmd, timeout=30)
    if rc != 0:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"text": out}

def list_agently():
    cmd = "agently-cli message +list --dir inbox --limit 5"
    out, rc = run(cmd, timeout=30)
    if rc != 0:
        return []
    try:
        data = json.loads(out)
        if data.get("ok") and data.get("data", {}).get("data"):
            return data["data"]["data"]
    except json.JSONDecodeError:
        pass
    return []

def read_agently(msg_id):
    cmd = f"agently-cli message +read --id {msg_id}"
    out, rc = run(cmd, timeout=30)
    if rc != 0:
        return None
    try:
        data = json.loads(out)
        if data.get("ok"):
            return data["data"]
    except json.JSONDecodeError:
        pass
    return None


# ── Attachment downloading ──────────────────────────────────────

def download_himalaya_attachments(config_path, msg_id, save_dir):
    """Download attachments from a himalaya message. Returns list of saved paths."""
    cmd = f"himalaya -c {config_path} attachment download {msg_id} --downloads-dir {save_dir}"
    out, rc = run(cmd, timeout=60)
    if rc != 0:
        return []
    # himalaya doesn't return paths in a structured way; scan the dir
    saved = []
    if os.path.isdir(save_dir):
        # Get recently created files
        for f in sorted(os.listdir(save_dir), key=lambda x: os.path.getmtime(os.path.join(save_dir, x)), reverse=True):
            fp = os.path.join(save_dir, f)
            if os.path.isfile(fp):
                saved.append(fp)
        # Return only files created in the last 5 seconds
        now = datetime.now().timestamp()
        return [p for p in saved if now - os.path.getmtime(p) < 10]
    return []

def download_agently_attachment(msg_id, att_id, save_dir):
    cmd = f"agently-cli attachment +download --msg {msg_id} --att {att_id} --output {save_dir}"
    out, rc = run(cmd, timeout=60)
    if rc != 0:
        return None
    try:
        data = json.loads(out)
        return data.get("data", {}).get("saved_to")
    except:
        return None


# ── Classification ──────────────────────────────────────────────

def classify(email_data):
    """
    Returns (emoji, category, priority, summary, action).
    
    action is one of:
      - "push": push summary via WeChat
      - "push_full": push summary + full body excerpt
      - "download_invoice": download attachments to invoice dir, push
      - "download_attach": download all attachments, push
      - "extract_code": extract verification code, push
      - "push_urgent": high-priority immediate push
      - "skip": silently ignore
    """
    subject = email_data.get("subject", "") or ""
    body = email_data.get("body", "") or ""
    from_addr = email_data.get("from_addr", "") or ""
    from_name = email_data.get("from_name", "") or ""
    from_name = from_name.strip('"\' ').strip()
    has_attachments = email_data.get("has_attachments", False)
    
    # ── Strip auto-generated footers and HTML before classification ──
    # Agently Mail footer: "此邮件由...通过Agent Mail自动发送。举报退订"
    body = re.sub(r'此邮件由.*?Agent Mail自动发送[。.]?\s*举报退订\s*', '', body)
    # Email headers in raw text (himalaya returns full MIME with headers before body)
    # Handle himalaya JSON double-escaping: literal \n → actual newline
    body = body.replace('\\n', '\n').replace('\\t', '\t')
    # Match headers at start of body: "From: xxx\nTo: xxx\nSubject: xxx\n\nbody"
    body = re.sub(r'^"?(?:From|To|Cc|Bcc|Subject|Date|Reply-To|Message-ID|MIME-Version|Content-Type|Content-Transfer-Encoding|Return-Path|Received|DKIM-Signature|X-[A-Za-z-]+):[^\n]*\n?', '', body, flags=re.MULTILINE | re.IGNORECASE)
    # Also strip leading quotes from JSON encoding
    body = re.sub(r'^"', '', body)
    body = re.sub(r'"$', '', body)
    # Strip leading blank lines
    body = re.sub(r'^\s*\n+', '', body)
    # HTML tags and CSS
    body = re.sub(r'<[^>]+>', ' ', body)
    body = re.sub(r'&[a-z]+;', ' ', body)
    body = re.sub(r'/\*.*?\*/', '', body, flags=re.DOTALL)
    body = re.sub(r'\.[a-z-]+\s*\{[^}]*\}', '', body, flags=re.DOTALL)
    # Common email client footers
    body = re.sub(r'--+\s*\nSent from.*', '', body)
    body = re.sub(r'Get Outlook for.*', '', body)
    body = re.sub(r'Sent from my iPhone.*', '', body)
    # Collapse excessive whitespace (keep paragraph breaks)
    body = re.sub(r'\n{4,}', '\n\n\n', body)
    body = body.strip()
    
    subject_lower = subject.lower()
    body_lower = body.lower()
    from_addr_lower = from_addr.lower()
    from_name_lower = from_name.lower()
    full_text = f"{subject_lower} {body_lower}"
    
    s = subject  # shorthand

    # ═══════════════════════════════════════════════════════════
    # 🔐 Verification Codes
    # ═══════════════════════════════════════════════════════════
    vc_kw = ["verification code", "security code", "验证码", "确认码",
             "auth code", "login code", "authentication code", "短信验证",
             "verify your", "验证你的", "邮箱验证", "手机验证"]
    for kw in vc_kw:
        if kw in full_text:
            # Extract code
            code = ""
            for pat in [r"(\d{4,8})\s*(?:是|is|：|:)", r"(?:code|码|验证码)[：:\s]*(\d{4,8})",
                        r"\b(\d{6})\b", r"(\d{4,8})"]:
                m = re.search(pat, full_text)
                if m:
                    code = m.group(1)
                    break
            summary = f"{s}\n码: {code}" if code else s
            return ("🔐", "验证码", "urgent", summary, "extract_code")

    # ═══════════════════════════════════════════════════════════
    # 🚨 Suspicious / Phishing — uses dynamic trust model (BEFORE security check)
    # ═══════════════════════════════════════════════════════════
    if HAS_V3:
        trust_result = email_trust.compute_trust(
            from_addr_lower, own_domains=email_trust.get_own_domains_cached(),
            contact_data=email_trust.is_known_contact(from_addr_lower) and email_store.get_contact(from_addr_lower) or None
        )
        if trust_result.get("label") in ("suspicious", "blocked"):
            return ("🚨", "⚠️ 高风险发件人", "high",
                    f"{s}\n发件人: {from_addr}\n信任: {trust_result['label']} (评分{trust_result['score']})", "push_urgent")
        
        risk_result = email_risk.compute_risk(
            {"subject": s, "from_email": from_addr_lower, "body": body, "has_attachment": has_attachments},
            trust_result
        )
        if risk_result.get("label") in ("high", "critical"):
            flags_str = ", ".join(risk_result.get("flags", [])[:3])
            return ("🚨", "⚠️ 高风险邮件", "high",
                    f"{s}\n风险: {risk_result['label']} (评分{risk_result['score']})\n原因: {flags_str}", "push_urgent")

    # ═══════════════════════════════════════════════════════════
    # ⚠️ Account Security  
    # ═══════════════════════════════════════════════════════════
    sec_pats = [(r"密码.*(?:修改|更改|重置|change)", "密码修改"),
                (r"(?:password|passwd).*(?:chang|reset|modif)", "Password Change"),
                (r"(?:异常|陌生|新).*(?:登录|设备|sign.?in)", "异常登录"),
                (r"(?:new|unusual).*(?:sign.?in|login|device)", "New Sign-in"),
                (r"security alert", "安全提醒"),
                (r"两步验证", "两步验证"),
                (r"2(?:fa|step).*(?:verif|auth)", "2FA"),
                (r"account.*recover", "账户恢复"),
                (r"login attempt", "登录尝试"),
                (r"您的.*账户", "账户提醒"),
                ]
    for pat, label in sec_pats:
        if re.search(pat, full_text):
            return ("⚠️", f"账户安全-{label}", "high", s, "push_urgent")

    # ═══════════════════════════════════════════════════════════
    # 📄 Paper / Academic Decisions
    # ═══════════════════════════════════════════════════════════
    paper_pats = [
        (r"(?:paper|manuscript|submission).*(?:accept|接收|录用|accepted)", "🎉 论文接收"),
        (r"congratulations.*(?:paper|manuscript|accept)", "🎉 论文接收"),
        (r"(?:decision|editorial decision).*(?:accept|接收|minor|major)", "📋 论文决定"),
        (r"(?:review|审稿).*(?:invitation|邀请|request)", "📬 审稿邀请"),
        (r"(?:invited|邀请).*(?:review|审稿)", "📬 审稿邀请"),
        (r"(?:paper|manuscript).*(?:reject|拒稿|declin)", "📋 论文被拒"),
        (r"(?:major|minor)\s+revision", "✏️ 修改意见"),
        (r"decision\s+on\s+(?:your|the)\s+(?:paper|manuscript|submission)", "📋 论文决定"),
        (r"(?:submission|paper)\s+(?:status|update)", "📋 论文状态"),
        (r"editorial\s+decision", "📋 编辑决定"),
        (r"under\s+review", "📋 送审通知"),
        (r"proofs?\s+(?:available|ready)", "📝 校样"),
        (r"galley\s+proof", "📝 校样"),
    ]
    for pat, label in paper_pats:
        if re.search(pat, full_text):
            excerpt = body[:500] if len(body) > 500 else body
            return ("📄", label, "high", f"{s}\n---\n{excerpt}", "push_full")

    # ═══════════════════════════════════════════════════════════
    # 🧾 Invoices & Receipts  
    # ═══════════════════════════════════════════════════════════
    invoice_kw = ["发票", "invoice", "receipt", "收据", "电子发票", "e-invoice",
                  "开票", "报账", "报销", "税号", "纳税人", "fapiao",
                  "账单", "bill", "payment receipt", "电子回单"]
    for kw in invoice_kw:
        if kw in full_text:
            action = "download_invoice" if has_attachments else "push"
            return ("🧾", "发票/收据", "high", s, action)

    # ═══════════════════════════════════════════════════════════
    # 📅 Calendar / Meeting / Events (BEFORE payment to catch "registration")
    # ═══════════════════════════════════════════════════════════
    cal_kw = ["invitation.*meeting", "calendar", "日程", "会议邀请", "meeting.*invit",
              "zoom", "teams meeting", "腾讯会议", "webinar", "seminar.*invit",
              "workshop.*invit", "conference.*invit", "讲座", "学术报告",
              "symposium", "deadline.*approaching", "abstract.*deadline", "deadline.*reminder",
              "registration.*open", "register.*conference", "early.?bird.*deadline"]
    for kw in cal_kw:
        if re.search(kw, full_text):
            dt_match = re.search(r'(\d{4}[-/]\d{1,2}[-/]\d{1,2}).*?(\d{1,2}:\d{2})', body[:500])
            dt_str = f" 时间: {dt_match.group(0)}" if dt_match else ""
            return ("📅", "会议/活动", "medium", f"{s}{dt_str}", "push")

    # ═══════════════════════════════════════════════════════════
    # 💰 Payments / Registration / Fees
    # ═══════════════════════════════════════════════════════════
    pay_kw = ["registration", "register now", "报名", "注册费", "版面费",
              "article processing", "apc", "payment due", "缴费", "汇款",
              "order confirm", "purchase", "订单", "transaction",
              "conference.*fee", "会议.*费", "会务费"]
    for kw in pay_kw:
        if re.search(kw, full_text):
            # Try to extract amount
            amount = ""
            am = re.search(r"(?:¥|￥|CNY|USD|\$)\s*([\d,.]+)", full_text)
            if am:
                amount = f" 金额: {am.group(0)}"
            # Try deadline
            deadline = ""
            dl = re.search(r"(?:deadline|截止|due|before)[：:\s]*([\d\-/]+)", full_text)
            if dl:
                deadline = f" 截止: {dl.group(1)}"
            return ("💰", "付款/缴费", "high", f"{s}{amount}{deadline}", "push_urgent")

    # ═══════════════════════════════════════════════════════════
    # 🏫 School / Institution Notices — by domain OR content
    # ═══════════════════════════════════════════════════════════
    official_kw = ["通知", "公告", "notice", "announcement", "办公", "行政",
                   "研究生院", "教务处", "图书馆", "网络中心", "信息化",
                   "保卫", "后勤", "财务处", "人事", "科研", "学位",
                   "答辩", "毕业", "奖学金", "助学金", "选课", "考试",
                   "考核", "课程", "成绩", "学籍", "注册", "报到"]
    official_content_kw = ["研究生院", "教务处", "财务处", "人事处", "学位论文",
                           "中期检查", "培养方案", "学位授予", "学籍", "注册中心",
                           "一卡通", "网络中心", "信息化", "保卫处", "后勤",
                           "答辩.*通知", "毕业.*通知", "选课.*通知", "考试.*通知",
                           "课程.*考核", "课程.*安排", "开展.*工作.*通知"]
    if from_addr_lower.endswith("@ustc.edu.cn") or "ustc" in from_addr_lower:
        for kw in official_kw:
            if kw in subject_lower:
                excerpt = body[:300] if len(body) > 300 else body
                return ("🏫", "学校通知", "medium", f"{s}\n{excerpt}", "push_full")
    
    # Also detect by content keywords (for notices forwarded from non-USTC senders)
    for kw in official_content_kw:
        if re.search(kw, full_text) and ("通知" in s or "公告" in s or "安排" in s):
            excerpt = body[:300] if len(body) > 300 else body
            return ("🏫", "学校通知", "medium", f"{s}\n{excerpt}", "push_full")

    # ═══════════════════════════════════════════════════════════
    # 📚 Academic Alerts / Weekly Briefings
    # ═══════════════════════════════════════════════════════════
    scholar_senders = ["scholaralerts", "google scholar", "google 学术",
                       "arxiv", "researchgate", "semanticscholar", "academia.edu",
                       "connected papers", "scopus", "web of science"]
    for sender in scholar_senders:
        if sender in from_addr_lower or sender in from_name_lower:
            # Body is already cleaned; extract paper titles (skip Subject/From/To lines)
            titles = re.findall(r'(?:^|\n)\s*(?!Subject:|From:|To:|Date:)(?:[\d]+\.\s*)?(.{30,200}?)(?:\n|$)', body[:2000])
            title_list = "\n".join([f"  • {t.strip()[:120]}" for t in titles[:5]]) if titles else ""
            summary = f"{s}{title_list}"
            return ("📚", "学术快讯", "low", summary, "push")
    
    # Weekly briefings from agent.qq.com
    briefing_kw = ["周报", "学术研究", "weekly.*brief", "研究.*简报", "科研.*周报"]
    for kw in briefing_kw:
        if re.search(kw, full_text):
            sender_info = from_name.strip('"\' ') if from_name else from_addr
            return ("📚", "学术周报", "low", f"[{sender_info}] {s}", "push")

    # ═══════════════════════════════════════════════════════════
    # 📝 Forms / Surveys to fill
    form_kw = ["问卷", "调查", "填写", "填报", "统计表", "信息采集",
               "请于.*前.*填写", "在线.*填", "survey", "form.*submit",
               "questionnaire", "信息确认", "数据.*上报"]
    for kw in form_kw:
        if re.search(kw, full_text):
            dl_match = re.search(r'(?:截止|deadline|请于|before)[：:\s]*(\d{1,2}[/月]\d{1,2}[日号]?)', full_text)
            dl_str = f" 截止: {dl_match.group(1)}" if dl_match else ""
            return ("📝", "表格/问卷", "medium", f"{s}{dl_str}", "push")

    # ═══════════════════════════════════════════════════════════
    # 📎 Important attachments from known senders  
    # ═══════════════════════════════════════════════════════════
    if has_attachments:
        important_attach_kw = ["合同", "协议", "contract", "agreement", "简历",
                               "cv", "resume", "证书", "certificate", "成绩单",
                               "transcript", "推荐信", "recommendation", "批文"]
        for kw in important_attach_kw:
            if kw in full_text:
                return ("📎", "重要附件", "medium", s, "download_attach")

    # ═══════════════════════════════════════════════════════════
    # 🚚 Package / Delivery
    # ═══════════════════════════════════════════════════════════
    pkg_kw = [r'\b快递\b', r'\b包裹\b', r'\b物流\b', r'\bshipment\b', r'\bdelivery\b',
              r'\btracking\b', r'\b菜鸟\b', r'\b顺丰\b', r'\b中通\b', r'\b圆通\b',
              r'\b韵达\b', r'\bems\b', r'\bdhl\b', r'\bfedex\b']
    for kw in pkg_kw:
        if re.search(kw, full_text):
            return ("📦", "快递物流", "low", s, "push")

    # ═══════════════════════════════════════════════════════════
    # 🗑️ Ads / Spam  
    # ═══════════════════════════════════════════════════════════
    spam_kw = ["unsubscribe", "退订", "discount", "折扣", "promotion",
               "促销", "sale", "newsletter", "deal", "offer", "limited time",
               "优惠", "广告", "subscribe now", "免费领取", "限时",
               "双11", "618", "大促", "满减", "秒杀", "团购", "直播"]
    for kw in spam_kw:
        if kw in full_text:
            return ("🗑️", "广告", "skip", None, "skip")

    marketing_domains = ["mailchimp", "sendgrid", "hubspot", "marketo",
                         "campaign", "litmus", "constantcontact", "mailgun",
                         "emarsys", "salesforce.com"]
    for d in marketing_domains:
        if d in from_addr_lower:
            return ("🗑️", "广告", "skip", None, "skip")

    # ═══════════════════════════════════════════════════════════
    # 🤖 Auto-generated / Noreply / Service / Receipts
    # ═══════════════════════════════════════════════════════════
    noreply_patterns = ["noreply@", "no-reply@", "donotreply@", "mailer-daemon@",
                        "bounce@", "auto-reply@", "notification@github.com"]
    for p in noreply_patterns:
        if p in from_addr_lower:
            return ("🤖", "自动通知", "skip", None, "skip")
    
    # Service welcome/confirmation/platform reminders
    service_kw = [r"welcome to", r"confirmation instructions", r"confirm your",
                  r"verify your email", r"get started", r"thank you for sign",
                  r"已读:", r"阅读回执", r"学员.*提醒", r"学时提醒",
                  r"^\w+提醒$", r"系统.*通知", r"自动.*提醒", r"你有一封.*提醒"]
    for kw in service_kw:
        if re.search(kw, full_text):
            return ("🤖", "自动通知", "skip", None, "skip")

    # ═══════════════════════════════════════════════════════════
    # 📰 Newsletters (non-spam, user might have subscribed)
    # ═══════════════════════════════════════════════════════════
    newsletter_kw = ["weekly digest", "monthly digest", "newsletter", "周报",
                     "月刊", "订阅", "substack", "medium daily"]
    for kw in newsletter_kw:
        if kw in full_text:
            return ("📰", "订阅推送", "low", s, "push")

    # ═══════════════════════════════════════════════════════════
    # 💬 Personal / Catch-all — preserve full body
    # ═══════════════════════════════════════════════════════════
    sender_info = from_name.strip('"\' ') if from_name else from_addr
    # Preserve full body for personal emails (up to 500 chars for WeChat, full text cached)
    max_preview = 500
    snippet = body[:max_preview].strip() if body else ""
    truncated = len(body) > max_preview
    summary = f"发件人: {sender_info}\n主题: {s}"
    if snippet:
        summary += f"\n{snippet}"
        if truncated:
            summary += "\n[📋 内容较长，已缓存全文，回复'查看'获取]"
    return ("💬", "个人邮件", "medium", summary, "push")


# ── Process single account ──────────────────────────────────────
def check_account(acct, pushed_count=None):
    """Check one account for new emails. Returns list of alert strings.
    If pushed_count is a list, stops when pushed_count[0] >= MAX_PER_TICK."""
    alerts = []
    acct_name = acct["name"]

    if acct["type"] == "himalaya":
        envelopes = list_himalaya(acct["config"])
    else:
        envelopes = list_agently()

    if not envelopes:
        return alerts

    seen = load_seen()
    prefix = f"{acct_name}:"
    updated = False

    for env in envelopes:
        if pushed_count and pushed_count[0] >= MAX_PER_TICK:
            break
            
        msg_id = str(env.get("id") or env.get("message_id", ""))
        key = f"{prefix}{msg_id}"

        if key in seen:
            continue

        if acct["type"] == "himalaya":
            msg = read_himalaya(acct["config"], msg_id)
        else:
            msg = read_agently(msg_id)

        if msg is None:
            seen[key] = True
            updated = True
            continue

        if acct["type"] == "himalaya":
            body = msg.get("text", "") if isinstance(msg, dict) else str(msg)
            from_addr = env.get("from", {}).get("addr", "")
            from_name = env.get("from", {}).get("name", "")
            subject = env.get("subject", "")
            has_attachments = env.get("has_attachment", False)
            to_addr = env.get("to", {}).get("addr", "") if isinstance(env.get("to"), dict) else ""
        else:
            body = msg.get("body", "") if isinstance(msg, dict) else ""
            from_addr = env.get("from", {}).get("email", "")
            from_name = env.get("from", {}).get("name", "")
            subject = env.get("subject", "")
            has_attachments = env.get("has_attachments", False)
            to_list = env.get("to") or [{}]
            to_addr = to_list[0].get("email", "") if to_list else ""

        email_data = {
            "subject": subject, "from_addr": from_addr, "from_name": from_name,
            "body": body[:5000], "to_addr": to_addr,
            "has_attachments": has_attachments, "msg_id": msg_id,
        }

        _cache_full_email(acct_name, msg_id, subject, from_addr, from_name, body, has_attachments)

        if HAS_V3:
            from_addr_lower = (from_addr or "").lower()
            from_domain = from_addr_lower.split("@")[1] if "@" in from_addr_lower else ""
            own_domains = email_trust.get_own_domains_cached()
            contact_data = email_store.get_contact(from_addr_lower)
            trust_result = email_trust.compute_trust(from_addr_lower, from_domain, own_domains, contact_data)
            risk_result = email_risk.compute_risk(
                {"subject": subject, "from_email": from_addr_lower, "body": body, "has_attachment": has_attachments}, trust_result
            ) if trust_result.get("label") != "blocked" else {"score": 100, "label": "critical", "flags": ["trust_blocked"]}
            email_store.upsert_message({
                "id": msg_id, "account": acct_name, "subject": subject, "from_name": from_name,
                "from_email": from_addr_lower, "from_domain": from_domain, "date_sent": env.get("date", ""),
                "has_attachment": 1 if has_attachments else 0,
                "trust_score": trust_result["score"], "trust_label": trust_result["label"],
                "risk_score": risk_result["score"], "risk_label": risk_result["label"], "push_status": "pending",
            })
            email_trust.learn_contact(from_addr_lower, from_name, own_domains)

        emoji, category, priority, summary, action = classify(email_data)
        seen[key] = True
        updated = True

        if HAS_MODULES and from_addr:
            email_contacts.learn_contact(from_addr, from_name)

        thread_context = ""
        if HAS_MODULES and action != "skip":
            thread_id = email_reply.update_thread_on_reply(from_addr, subject, (body or "")[:200])
            if thread_id:
                threads = email_reply.load_threads()
                t = threads.get("threads", {}).get(thread_id, {})
                if t.get("user_question"):
                    thread_context = f"\n🔗 {t.get('topic','')} — {t.get('user_question','')}"

        if action == "skip":
            continue

        attach_info = ""
        if has_attachments:
            if HAS_V3:
                skip_download = email_risk.is_suspicious_for_download(
                    {"subject": subject, "from_email": from_addr_lower, "attachments": env.get("attachments", [])}, trust_result
                )
            else:
                skip_download = False
            if skip_download:
                attach_info = "\n⚠️ 附件可疑，未下载"
            else:
                save_dir = os.path.join(INVOICE_DIR if action == "download_invoice" else ATTACHMENT_DIR, datetime.now().strftime("%Y-%m"))
                if acct["type"] == "himalaya":
                    saved = download_himalaya_attachments(acct["config"], msg_id, save_dir)
                else:
                    saved = []
                    for att in env.get("attachments", []):
                        aid = att.get("attachment_id") or att.get("id", "")
                        if aid:
                            sp = download_agently_attachment(msg_id, aid, save_dir)
                            if sp: saved.append(sp)
                if saved:
                    label = "发票" if action == "download_invoice" else "附件"
                    attach_info = f"\n📎 {label}: {' '.join(saved)}"

        priority_mark = "🔴" if priority in ("urgent", "high") else "🟡" if priority == "medium" else "🟢"
        sender_display = (from_name or "").strip('"\' ').strip() or from_addr
        if sender_display and from_addr and sender_display != from_addr:
            sender_display = f"{sender_display} <{from_addr}>"

        lines = [f"{priority_mark} {category}"]
        lines.append(f"发件人: {sender_display}")

        body_show = body
        body_show = body_show.replace('\\n', '\n').replace('\\t', ' ')
        body_show = re.sub(r'^(?:From|To|Cc|Bcc|Subject|Date|Reply-To|Message-ID|MIME-Version|Content-Type|Content-Transfer-Encoding|Return-Path|Received|X-[A-Za-z-]+):[^\n]*\n?', '', body_show, flags=re.MULTILINE | re.IGNORECASE)
        body_show = re.sub(r'此邮件由.*?举报退订\s*', '', body_show)
        body_show = re.sub(r'<[^>]+>', '', body_show)
        body_show = re.sub(r'&[a-z]+;', ' ', body_show)
        body_show = re.sub(r'\[HTML\]\s*', '', body_show)
        body_show = re.sub(r'\n{3,}', '\n\n', body_show)
        body_show = body_show.strip()

        max_preview = 300 if category in ("个人邮件","学校通知","📄 论文决定","📄 论文接收","🎉 论文接收","📅 会议/活动","会议/活动","📝 表格/问卷","💰 付款/缴费","🧾 发票/收据","⚠️ 高风险邮件") else 150
        if body_show and len(body_show) > 20:
            truncated = len(body_show) > max_preview
            preview = body_show[:max_preview].strip()
            if truncated: preview += "..."
            lines.append(f"\n{preview}")

        cal = _extract_calendar_hints(subject, body)
        if cal: lines.append(f"\n{cal}")
        if attach_info: lines.append(attach_info)
        if thread_context: lines.append(thread_context)

        alert = "\n".join(lines)
        if priority == "urgent": alerts.insert(0, alert)
        else: alerts.append(alert)

        if pushed_count is not None:
            pushed_count[0] += 1

    if updated:
        save_seen(seen)
    return alerts

def main():
    # Sleep time check
    if is_sleep_time():
        return ""

    all_alerts = []
    pushed_count = [0]  # use list for closure
    
    for acct in ACCOUNTS:
        if pushed_count[0] >= MAX_PER_TICK:
            break
        try:
            acct_alerts = check_account(acct, pushed_count)
            if acct_alerts:
                all_alerts.extend(acct_alerts)
                accounts_seen.add(acct["name"])
        except Exception as e:
            all_alerts.append(f"⚠️ {acct['name']} 检查失败: {e}")
            accounts_seen.add(acct["name"])

    if not all_alerts:
        return ""

    now = datetime.now().strftime("%m/%d %H:%M")
    total = len(all_alerts)
    
    header = f"📬 {total}封邮件 ({now}) [{'+'.join(sorted(accounts_seen))}]"
    full = header + "\n\n" + "\n\n---\n\n".join(all_alerts)
    return full


if __name__ == "__main__":
    output = main()
    if output:
        print(output)