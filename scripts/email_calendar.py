#!/usr/bin/env python3
"""
Email Calendar Extractor — scan recent emails for dates, deadlines, and events.
Runs as daily cron job. Pushes upcoming events to WeChat.

Extracts:
- Paper deadlines (submission, revision, camera-ready)
- Conference dates
- Meeting/appointment times
- Administrative deadlines (forms, payments, registration)
"""

import json, os, re, subprocess, sys
from datetime import datetime, timedelta
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

try:
    import email_config
    import email_store
except ImportError:
    email_config = None
    email_store = None

CALENDAR_FILE = email_config.get_path("calendar") if email_config else os.path.expanduser("~/.hermes/email_calendar.json")
SEEN_FILE = email_config.get_path("seen") if email_config else os.path.expanduser("~/.hermes/email_watch_seen.json")

ACCOUNTS = email_config.get_accounts(True) if email_config else [
    {"name": "USTC", "type": "himalaya",
     "config": os.path.expanduser("~/.config/himalaya/config_ustc.toml")},
    {"name": "Gmail", "type": "himalaya",
     "config": os.path.expanduser("~/.config/himalaya/config_gmail.toml")},
    {"name": "Agently", "type": "agently"},
]

# ── Date Extraction Patterns ────────────────────────────────────

# Chinese date formats
CN_DATE_PATTERNS = [
    (r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日', "ymd"),
    (r'(\d{1,2})\s*月\s*(\d{1,2})\s*日', "mmdd"),
    (r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', "iso"),
    (r'(\d{1,2})[-/](\d{1,2})', "mmdd_short"),
]

# Event type indicators
EVENT_PATTERNS = {
    "🎓 毕业/答辩": [
        r'(?:答辩|defense|毕业).*(?:日期|时间|安排|通知)',
        r'(?:thesis|dissertation).*(?:deadline|defense)',
        r'(?:博士|硕士).*(?:答辩|论文)',
    ],
    "📄 论文截止": [
        r'(?:submission|paper|manuscript)\s+deadline',
        r'deadline.*(?:submission|paper)',
        r'(?:截稿|提交|投稿).*(?:日期|截止|deadline)',
        r'(?:camera.?ready|最终版).*(?:deadline|due|截止)',
        r'full\s+paper\s+(?:due|deadline|submission)',
    ],
    "🏛️ 会议/研讨会": [
        r'(?:conference|workshop|symposium|seminar|webinar)',
        r'会议.*(?:时间|日期|召开|举办|举行)',
        r'(?:将于|定于|拟定于).*(?:召开|举办|举行)',
        r'(?:conference|meeting)\s+date',
    ],
    "📅 预约/会面": [
        r'(?:meeting|appointment|schedule).*(?:confirm|set|arrange)',
        r'(?:约|定)\s*(?:在|于|时间).*(?:见面|开会|讨论)',
        r'calendar.*invit',
        r'(?:zoom|teams|腾讯会议|tencent).*(?:meeting|会议)',
    ],
    "💰 付款截止": [
        r'(?:registration|payment|fee).*(?:deadline|due|截止)',
        r'(?:注册|缴费|付款|报名).*(?:截止|deadline|日期)',
        r'early.?bird.*(?:deadline|截止)',
    ],
    "📝 表格/行政": [
        r'(?:填写|填报|提交|上报).*(?:截止|deadline|截止日期)',
        r'(?:请于|务必在|需要在).*(?:前|之前).*(?:填写|提交|完成)',
    ],
}


def run(cmd, timeout=30):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
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


def list_himalaya(config_path, limit=10):
    cmd = ["himalaya", "-c", config_path, "envelope", "list", "--page-size", str(limit), "--output", "json"]
    out, rc = run(cmd, timeout=30)
    if rc != 0: return []
    try: return json.loads(out)
    except: return []


def list_agently(limit=5):
    cmd = ["agently-cli", "message", "+list", "--dir", "inbox", "--limit", str(limit)]
    out, rc = run(cmd, timeout=30)
    if rc != 0: return []
    try:
        data = json.loads(out)
        return data.get("data", {}).get("data", [])
    except: return []


def read_himalaya(config_path, msg_id):
    cmd = ["himalaya", "-c", config_path, "message", "read", str(msg_id), "--output", "json"]
    out, rc = run(cmd, timeout=30)
    if rc != 0: return None
    try: return json.loads(out)
    except: return {"text": out}


def read_agently(msg_id):
    cmd = ["agently-cli", "message", "+read", "--id", str(msg_id)]
    out, rc = run(cmd, timeout=30)
    if rc != 0: return None
    try:
        data = json.loads(out)
        return data.get("data") if data.get("ok") else None
    except: return None


# ── Date Parsing ─────────────────────────────────────────────────

def parse_date(text):
    """Try to extract a date from text. Returns (datetime, confidence)."""
    now = datetime.now()
    
    # ISO format: 2026-07-15 or 2026/07/15
    m = re.search(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', text)
    if m:
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if abs((dt - now).days) < 730:  # within 2 years
                return (dt, 0.9)
        except ValueError:
            pass
    
    # Chinese format: 2026年7月15日
    m = re.search(r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日', text)
    if m:
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if abs((dt - now).days) < 730:
                return (dt, 0.9)
        except ValueError:
            pass
    
    # Chinese short: 7月15日 (assume current or next year)
    m = re.search(r'(\d{1,2})\s*月\s*(\d{1,2})\s*日', text)
    if m:
        try:
            month, day = int(m.group(1)), int(m.group(2))
            dt = datetime(now.year, month, day)
            if dt < now - timedelta(days=30):
                dt = datetime(now.year + 1, month, day)
            if abs((dt - now).days) < 730:
                return (dt, 0.7)
        except ValueError:
            pass
    
    # MM/DD short format
    m = re.search(r'(\d{1,2})[-/](\d{1,2})', text)
    if m:
        try:
            month, day = int(m.group(1)), int(m.group(2))
            if 1 <= month <= 12 and 1 <= day <= 31:
                dt = datetime(now.year, month, day)
                if dt < now - timedelta(days=30):
                    dt = datetime(now.year + 1, month, day)
                return (dt, 0.5)
        except ValueError:
            pass
    
    # Relative: "tomorrow", "next Monday", "in 3 days"
    relative_patterns = [
        (r'(?:明天|tomorrow)', timedelta(days=1)),
        (r'(?:后天|day after tomorrow)', timedelta(days=2)),
        (r'(?:下周|next week)', timedelta(days=7)),
        (r'(\d+)\s*(?:天|days?)\s*(?:后|later|after)', None),  # needs parsing
        (r'(\d+)\s*(?:周|weeks?)\s*(?:后|later|after)', None),
    ]
    for pat, delta in relative_patterns:
        m = re.search(pat, text)
        if m and delta:
            return (now + delta, 0.4)
    
    return (None, 0)


def classify_event(subject, body):
    """Determine event type from email content."""
    full = f"{subject} {body}".lower()
    for event_type, patterns in EVENT_PATTERNS.items():
        for pat in patterns:
            if re.search(pat, full):
                return event_type
    return "📌 其他事件"


def extract_time(text):
    """Try to extract a time from text."""
    # HH:MM format
    m = re.search(r'(\d{1,2}):(\d{2})\s*(?:[ap]m|上午|下午)?', text, re.IGNORECASE)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2))
        suffix = m.group(0).lower()
        if 'pm' in suffix or '下午' in suffix:
            if hour < 12:
                hour += 12
        if 'am' in suffix or '上午' in suffix:
            if hour == 12:
                hour = 0
        return f"{hour:02d}:{minute:02d}"
    
    # Chinese: 下午3点 / 上午9点
    m = re.search(r'(?:上午|下午|晚上)?\s*(\d{1,2})\s*[点时]', text)
    if m:
        hour = int(m.group(1))
        prefix = m.group(0)
        if '下午' in prefix or '晚上' in prefix:
            if hour < 12:
                hour += 12
        if '上午' in prefix:
            if hour == 12:
                hour = 0
        return f"{hour:02d}:00"
    
    return None


# ── Main Processing ──────────────────────────────────────────────

def scan_emails_for_dates():
    """Scan recent emails and extract calendar events."""
    calendar = load_json(CALENDAR_FILE)
    if "events" not in calendar:
        calendar["events"] = []
    if "sources" not in calendar:
        calendar["sources"] = {}
    
    existing_sources = calendar.get("sources", {})
    new_events = 0
    
    for acct in ACCOUNTS:
        if acct["type"] == "himalaya":
            envelopes = list_himalaya(acct.get("himalaya_config") or acct.get("config"), 20)
        else:
            envelopes = list_agently(10)
        
        for env in envelopes:
            msg_id = str(env.get("id") or env.get("message_id", ""))
            acct_name = acct.get("label") or acct.get("name") or acct.get("id") or "account"
            source_key = f"{acct_name}:{msg_id}"
            
            if source_key in existing_sources:
                continue
            
            subject = env.get("subject", "")
            
            # Read body
            if acct["type"] == "himalaya":
                msg = read_himalaya(acct.get("himalaya_config") or acct.get("config"), msg_id)
                body = msg.get("text", "") if isinstance(msg, dict) else str(msg)
            else:
                msg = read_agently(msg_id)
                body = msg.get("body", "") if isinstance(msg, dict) else ""
            
            if not body:
                continue
            
            # Strip footers before date extraction
            import re as _re
            body_clean = _re.sub(r'此邮件由.*?Agent Mail自动发送[。.]?\s*举报退订\s*', '', body)
            
            # Try to extract date from body ONLY (not subject)
            date_dt, confidence = parse_date(body_clean)
            if not date_dt or confidence < 0.5:
                continue
            
            # Determine event type
            event_type = classify_event(subject, body)
            time_str = extract_time(body)
            
            event = {
                "id": f"evt_{date_dt.strftime('%Y%m%d')}_{new_events}",
                "title": subject[:60] if subject else event_type,
                "date": date_dt.strftime("%Y-%m-%d"),
                "time": time_str,
                "type": event_type,
                "source": source_key,
                "source_subject": subject,
                "confidence": confidence,
                "extracted_at": datetime.now().isoformat(),
                "reminders_sent": [],
            }
            
            calendar["events"].append(event)
            calendar["sources"][source_key] = True
            new_events += 1
    
    if new_events > 0:
        save_json(CALENDAR_FILE, calendar)
    
    return new_events, calendar


def get_upcoming_events(calendar, days=7):
    """Get events in the next N days."""
    now = datetime.now()
    cutoff = now + timedelta(days=days)
    upcoming = []
    
    for event in calendar.get("events", []):
        try:
            evt_date = datetime.strptime(event["date"], "%Y-%m-%d")
        except (ValueError, KeyError):
            continue
        
        if now.date() <= evt_date.date() <= cutoff.date():
            upcoming.append(event)
    
    upcoming.sort(key=lambda e: e["date"])
    return upcoming


def check_urgent_reminders(calendar):
    """Check for events that need reminder (1 day, 3 days, 7 days before)."""
    now = datetime.now()
    reminders = []
    
    for event in calendar.get("events", []):
        try:
            evt_date = datetime.strptime(event["date"], "%Y-%m-%d")
        except (ValueError, KeyError):
            continue
        
        days_until = (evt_date.date() - now.date()).days
        
        reminder_days = [1, 3, 7]
        for rd in reminder_days:
            reminder_key = f"d{rd}"
            if days_until == rd and reminder_key not in event.get("reminders_sent", []):
                reminders.append({
                    "event": event,
                    "days_until": rd,
                    "reminder_key": reminder_key,
                })
    
    return reminders


def main():
    if email_store:
        schedules = email_store.get_schedules("active", 20)
        if schedules:
            lines = ["📅 邮件日程:"]
            for item in schedules[:10]:
                deadline = item.get("deadline") or "无截止时间"
                title = item.get("title") or item.get("action_needed") or item.get("message_id")
                lines.append(f"  {deadline} {title[:60]}")
            return "\n".join(lines)

    # Scan for new events
    new_count, calendar = scan_emails_for_dates()
    
    # Check urgent reminders
    reminders = check_urgent_reminders(calendar)
    
    # Get upcoming events (next 7 days)
    upcoming = get_upcoming_events(calendar, 7)
    
    output_parts = []
    
    if new_count > 0:
        output_parts.append(f"📅 发现 {new_count} 个新事件")
    
    if reminders:
        output_parts.append("⏰ 临近提醒:")
        for r in reminders:
            evt = r["event"]
            output_parts.append(f"  🔴 还有{r['days_until']}天: {evt['type']} {evt['title'][:40]}")
            output_parts.append(f"     日期: {evt['date']}" + (f" {evt['time']}" if evt.get('time') else ""))
            # Mark reminder as sent
            evt.setdefault("reminders_sent", []).append(r["reminder_key"])
    
    if upcoming and not reminders:
        output_parts.append("📅 未来7天:")
        for evt in upcoming[:5]:
            output_parts.append(f"  {evt['type']} {evt['date']} {evt['title'][:40]}")
    
    # Save updated reminders
    if reminders:
        save_json(CALENDAR_FILE, calendar)
    
    return "\n".join(output_parts) if output_parts else ""


if __name__ == "__main__":
    output = main()
    if output:
        print(output)
