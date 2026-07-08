#!/usr/bin/env python3
"""
Email Follow-up Reminder — check for threads awaiting reply and deadlines approaching.
Runs as daily cron job. Pushes reminders to WeChat.
"""

import json, os, re, subprocess, sys
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

THREADS_FILE = os.path.expanduser("~/.hermes/email_threads.json")
CALENDAR_FILE = os.path.expanduser("~/.hermes/email_calendar.json")
SEEN_FILE = os.path.expanduser("~/.hermes/email_watch_seen.json")
PENDING_FILE = os.path.expanduser("~/.hermes/email_pending.json")


def load_json(path):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def check_stale_threads():
    """Find threads that haven't been replied to in 3+ days."""
    threads = load_json(THREADS_FILE)
    now = datetime.now()
    alerts = []
    
    for tid, t in threads.get("threads", {}).items():
        if t.get("status") != "waiting_reply":
            continue
        
        # Check if it's been > 3 days
        created_str = t.get("created", "")
        if not created_str:
            continue
        
        try:
            created = datetime.fromisoformat(created_str)
        except (ValueError, TypeError):
            continue
        
        days_waiting = (now - created).days
        
        if days_waiting >= 3:
            # Check if already reminded
            last_reminder = t.get("last_reminder_at", "")
            if last_reminder:
                try:
                    last = datetime.fromisoformat(last_reminder)
                    if (now - last).days < 3:  # Don't remind more than every 3 days
                        continue
                except (ValueError, TypeError):
                    pass
            
            alerts.append({
                "topic": t.get("topic", "?"),
                "participant": t.get("participants", ["?"])[0],
                "days_waiting": days_waiting,
                "user_question": t.get("user_question", ""),
            })
            
            t["last_reminder_at"] = now.isoformat()
    
    if alerts:
        save_json(THREADS_FILE, threads)
    
    return alerts


def check_imminent_deadlines():
    """Check calendar for deadlines within 24/48 hours."""
    calendar = load_json(CALENDAR_FILE)
    now = datetime.now()
    alerts = []
    
    for event in calendar.get("events", []):
        try:
            evt_date = datetime.strptime(event["date"], "%Y-%m-%d")
        except (ValueError, KeyError):
            continue
        
        hours_until = (evt_date - now).total_seconds() / 3600
        
        # Deadlines within 24h or 48h
        if 0 < hours_until <= 48:
            reminder_key = f"critical_{evt_date.strftime('%Y%m%d')}"
            if reminder_key in event.get("reminders_sent", []):
                continue
            
            emoji = "🔴" if hours_until <= 24 else "🟡"
            alerts.append({
                "title": event.get("title", "?"),
                "type": event.get("type", "?"),
                "date": event["date"],
                "time": event.get("time", ""),
                "hours_until": int(hours_until),
                "emoji": emoji,
            })
            
            event.setdefault("reminders_sent", []).append(reminder_key)
    
    if alerts:
        save_json(CALENDAR_FILE, calendar)
    
    return alerts


def check_pending_actions():
    """Check for emails that need user action (forms, surveys with deadlines)."""
    seen = load_json(SEEN_FILE)
    # This is harder without full body access - skip for now, use calendar + threads
    return []


def main():
    output_parts = []
    
    # 1. Stale thread reminders
    stale = check_stale_threads()
    if stale:
        output_parts.append("📬 等待回复提醒:")
        for s in stale:
            output_parts.append(f"  ⏳ {s['days_waiting']}天未回复: 「{s['topic']}」")
            output_parts.append(f"     发给: {s['participant']}")
            if s['user_question']:
                output_parts.append(f"     原问题: {s['user_question'][:60]}")
    
    # 2. Imminent deadline alerts
    deadlines = check_imminent_deadlines()
    if deadlines:
        output_parts.append("⏰ 截止日期提醒:")
        for d in deadlines:
            output_parts.append(f"  {d['emoji']} {d['hours_until']}小时后: {d['type']}")
            output_parts.append(f"     {d['title'][:50]}")
            output_parts.append(f"     日期: {d['date']}" + (f" {d['time']}" if d.get('time') else ""))
    
    return "\n".join(output_parts) if output_parts else ""


if __name__ == "__main__":
    output = main()
    if output:
        print(output)