#!/usr/bin/env python3
"""
Email Reply Processor — format replies, manage threads, send via himalaya/agently-cli.

Designed to be imported and used by the Hermes agent when processing
email-related commands from WeChat or CLI.
"""

import json, os, re, subprocess
from datetime import datetime
from pathlib import Path

try:
    import email_config
except ImportError:
    email_config = None

CONTACTS_FILE = email_config.get_path("contacts") if email_config else os.path.expanduser("~/.hermes/email_contacts.json")
SETTINGS_FILE = email_config.get_path("settings") if email_config else os.path.expanduser("~/.hermes/email_settings.json")
THREADS_FILE = email_config.get_path("threads") if email_config else os.path.expanduser("~/.hermes/email_threads.json")
SEEN_FILE = email_config.get_path("seen") if email_config else os.path.expanduser("~/.hermes/email_watch_seen.json")

# Account configs for sending
ACCOUNTS = email_config.get_account_map() if email_config else {
    "ustc": {
        "type": "himalaya",
        "config": os.path.expanduser("~/.config/himalaya/config_ustc.toml"),
        "email": "wmwen@mail.ustc.edu.cn",
        "name": "wmwen",
    },
    "gmail": {
        "type": "himalaya",
        "config": os.path.expanduser("~/.config/himalaya/config_gmail.toml"),
        "email": "wmwen1999@gmail.com",
        "name": "wmwen",
    },
    "agently": {
        "type": "agently",
        "email": "augenstern@agent.qq.com",
        "name": "augenstern",
    },
}


def load_json(path):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_settings():
    return load_json(SETTINGS_FILE)


def load_contacts():
    return load_json(CONTACTS_FILE)


def load_threads():
    return load_json(THREADS_FILE)


def save_threads(data):
    save_json(THREADS_FILE, data)


def run(cmd, timeout=30):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip(), r.returncode
    except subprocess.TimeoutExpired:
        return "", 124


# ── Email Lookup ────────────────────────────────────────────────

def get_recent_emails(account, limit=10):
    """Get recent emails from an account. Returns list of envelope dicts."""
    acct = ACCOUNTS.get(account)
    if not acct:
        return []
    
    if acct["type"] == "himalaya":
        cmd = ["himalaya", "-c", acct.get("himalaya_config") or acct["config"], "envelope", "list", "--page-size", str(limit), "--output", "json"]
        out, rc = run(cmd, timeout=30)
        if rc != 0:
            return []
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return []
    else:
        cmd = ["agently-cli", "message", "+list", "--dir", "inbox", "--limit", str(limit)]
        out, rc = run(cmd, timeout=30)
        if rc != 0:
            return []
        try:
            data = json.loads(out)
            return data.get("data", {}).get("data", [])
        except json.JSONDecodeError:
            return []


def find_latest_email(match_phrase=None):
    """Find the latest email across all accounts, optionally matching a phrase."""
    all_emails = []
    for acct_name in ACCOUNTS:
        envs = get_recent_emails(acct_name, 10)
        for env in envs:
            env["_account"] = acct_name
            all_emails.append(env)
    
    # Sort by date (newest first)
    all_emails.sort(key=lambda e: e.get("date", ""), reverse=True)
    
    if match_phrase:
        phrase_lower = match_phrase.lower()
        filtered = []
        for env in all_emails:
            subject = (env.get("subject") or "").lower()
            from_name = (env.get("from", {}).get("name") or "").lower()
            from_addr = (env.get("from", {}).get("addr") or env.get("from", {}).get("email", "")).lower()
            if phrase_lower in subject or phrase_lower in from_name or phrase_lower in from_addr:
                filtered.append(env)
        return filtered[0] if filtered else None
    
    return all_emails[0] if all_emails else None


def find_email_by_index(index_str):
    """Find email by index (1-based, from recent list)."""
    try:
        idx = int(index_str) - 1
    except ValueError:
        return None
    
    all_emails = []
    for acct_name in ACCOUNTS:
        envs = get_recent_emails(acct_name, 10)
        for env in envs:
            env["_account"] = acct_name
            all_emails.append(env)
    
    all_emails.sort(key=lambda e: e.get("date", ""), reverse=True)
    
    if 0 <= idx < len(all_emails):
        return all_emails[idx]
    return None


def read_full_email(env):
    """Read the full content of an email."""
    acct_name = env.get("_account", "ustc")
    acct = ACCOUNTS.get(acct_name)
    msg_id = str(env.get("id") or env.get("message_id", ""))
    
    if not acct or not msg_id:
        return None
    
    if acct["type"] == "himalaya":
        cmd = ["himalaya", "-c", acct.get("himalaya_config") or acct["config"], "message", "read", str(msg_id), "--output", "json"]
        out, rc = run(cmd, timeout=30)
        if rc != 0:
            return None
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return {"text": out}
    else:
        cmd = ["agently-cli", "message", "+read", "--id", str(msg_id)]
        out, rc = run(cmd, timeout=30)
        if rc != 0:
            return None
        try:
            data = json.loads(out)
            return data.get("data")
        except json.JSONDecodeError:
            return None


# ── Reply Formatting ────────────────────────────────────────────

def format_reply(env, user_message, cc=None, attachments=None):
    """
    Format a reply email. Returns a dict with all fields needed for sending.
    
    Args:
        env: envelope dict from get_recent_emails or find_latest_email
        user_message: the user's raw message body
        cc: optional CC recipients
        attachments: optional list of file paths
    
    Returns:
        dict with: to, to_name, from_account, from_email, subject, body, cc, attachments
    """
    settings = load_settings()
    contacts = load_contacts()
    
    # Determine recipient
    from_info = env.get("from", {})
    to_addr = from_info.get("addr") or from_info.get("email", "")
    to_name = (from_info.get("name") or "").strip().strip('"\'')
    
    if not to_name:
        to_name = to_addr
    
    # Determine which account to send from (reply from same account that received)
    acct_name = env.get("_account", "ustc")
    acct = ACCOUNTS.get(acct_name, ACCOUNTS["ustc"])
    
    # Get reply preferences based on contact
    reply_prefs = get_reply_preferences(to_addr)
    greeting_template = reply_prefs.get("greeting", "{name}您好，\n")
    signature = reply_prefs.get("signature", settings.get("signature", "祝好！\nwmwen"))
    
    # Format greeting
    greeting = greeting_template.replace("{name}", to_name)
    
    # Subject (with Re: prefix)
    original_subject = env.get("subject", "")
    if not original_subject.lower().startswith("re:"):
        subject = f"Re: {original_subject}"
    else:
        subject = original_subject
    
    # Body
    body = f"{greeting}\n{user_message.strip()}\n\n{signature}"
    
    result = {
        "to": to_addr,
        "to_name": to_name,
        "from_account": acct_name,
        "from_email": acct["email"],
        "from_name": acct["name"],
        "subject": subject,
        "body": body,
        "cc": cc or [],
        "attachments": attachments or [],
        "account": acct,
    }
    
    return result


def get_reply_preferences(email):
    """Get greeting/signature preferences for a contact."""
    settings = load_settings()
    contacts = load_contacts()
    addr = email.lower().strip()
    contact = contacts.get("contacts", {}).get(addr, {})
    
    prefs = settings.get("reply_preferences", {})
    role = contact.get("role", "")
    if role in prefs:
        return prefs[role]
    
    domain = addr.split("@")[-1] if "@" in addr else ""
    if domain.endswith(".edu.cn") or domain.endswith(".ac.cn"):
        return prefs.get("导师", prefs.get("default", {}))
    
    return prefs.get("default", {"greeting": "{name}您好，\n", "signature": "祝好！\nwmwen"})


# ── Sending ─────────────────────────────────────────────────────

def send_email(reply_data, confirmation_token=None):
    """
    Send an email. For Agently, uses two-phase confirmation.
    Returns (success: bool, message: str).
    """
    acct = reply_data["account"]
    
    if acct["type"] == "himalaya":
        return send_via_himalaya(reply_data)
    else:
        return send_via_agently(reply_data, confirmation_token)


def send_via_himalaya(reply_data):
    """Send via himalaya (no two-phase)."""
    stdin = f"From: {reply_data['from_name']} <{reply_data['from_email']}>\n"
    stdin += f"To: {reply_data['to_name']} <{reply_data['to']}>\n"
    stdin += f"Subject: {reply_data['subject']}\n"
    if reply_data["cc"]:
        stdin += f"Cc: {', '.join(reply_data['cc'])}\n"
    stdin += f"\n{reply_data['body']}\n"
    try:
        result = subprocess.run(
            ["himalaya", "-c", reply_data["account"].get("himalaya_config") or reply_data["account"]["config"], "template", "send"],
            input=stdin, capture_output=True, text=True, timeout=30,
        )
        out, rc = result.stdout.strip() or result.stderr.strip(), result.returncode
    except subprocess.TimeoutExpired:
        out, rc = "", 124
    
    if rc == 0:
        return (True, "邮件已发送")
    else:
        return (False, f"发送失败: {out}")


def send_via_agently(reply_data, confirmation_token=None):
    """Send via agently-cli with two-phase confirmation."""
    cmd = ["agently-cli", "message", "+send", "--to", reply_data["to"], "--subject", reply_data["subject"], "--body", reply_data["body"]]
    for c in (reply_data.get("cc") or []):
        cmd.extend(["--cc", c])
    for a in (reply_data.get("attachments") or []):
        cmd.extend(["--attachment", a])
    if confirmation_token:
        cmd.extend(["--confirmation-token", confirmation_token])
    out, rc = run(cmd, timeout=30)
    
    if rc != 0:
        return (False, f"发送失败: {out}")
    
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return (False, f"无法解析响应: {out}")
    
    if not data.get("ok"):
        return (False, data.get("error", {}).get("message", "未知错误"))
    
    rd = data.get("data", {})
    
    # Check if confirmation needed
    if rd.get("confirmation_required") and not confirmation_token:
        ctk = rd.get("confirmation_token", "")
        summary = rd.get("summary", {})
        return ("confirm_needed", {"token": ctk, "summary": summary})
    
    return (True, "邮件已发送")


# ── Thread Management ───────────────────────────────────────────

def track_thread(topic, recipient_addr, user_question, sent_msg_id=None):
    """Create or update a thread for tracking replies."""
    threads = load_threads()
    
    thread_id = f"thread_{topic.replace(' ', '_')}_{datetime.now().strftime('%Y%m')}"
    
    threads["threads"][thread_id] = {
        "topic": topic,
        "participants": [recipient_addr],
        "user_question": user_question,
        "created": datetime.now().isoformat(),
        "status": "waiting_reply",
        "messages": [],
        "sent_msg_id": sent_msg_id,
    }
    
    save_threads(threads)
    return thread_id


def find_thread_by_participant(email):
    """Find active threads with a given participant."""
    threads = load_threads()
    addr = email.lower().strip()
    
    matches = []
    for tid, t in threads.get("threads", {}).items():
        if addr in [p.lower() for p in t.get("participants", [])]:
            matches.append((tid, t))
    
    # Sort by recency
    matches.sort(key=lambda x: x[1].get("created", ""), reverse=True)
    return matches


def update_thread_on_reply(from_email, reply_subject, reply_summary):
    """When a reply is received, update the thread."""
    threads = load_threads()
    addr = from_email.lower().strip()
    
    for tid, t in threads.get("threads", {}).items():
        if addr in [p.lower() for p in t.get("participants", [])] and t.get("status") == "waiting_reply":
            t["status"] = "replied"
            t["last_reply"] = datetime.now().isoformat()
            t["reply_summary"] = reply_summary
            t["reply_subject"] = reply_subject
            save_threads(threads)
            return tid
    
    return None


# ── Pending Send Queue ──────────────────────────────────────────

PENDING_FILE = email_config.get_path("pending") if email_config else os.path.expanduser("~/.hermes/email_pending.json")


def queue_email(reply_data, send_at=None):
    """Queue an email for later sending."""
    pending = load_json(PENDING_FILE)
    if "queue" not in pending:
        pending["queue"] = []
    
    task = {
        "id": f"pending_{len(pending['queue'])+1}_{datetime.now().strftime('%Y%m%d%H%M%S')}",
        "reply_data": reply_data,
        "send_at": send_at,
        "created": datetime.now().isoformat(),
        "status": "pending",
    }
    pending["queue"].append(task)
    save_json(PENDING_FILE, pending)
    return task["id"]


def process_pending_queue():
    """Check and send any emails that are due. Called by cron."""
    pending = load_json(PENDING_FILE)
    queue = pending.get("queue", [])
    now = datetime.now().isoformat()
    
    sent = []
    remaining = []
    
    for task in queue:
        if task["status"] != "pending":
            remaining.append(task)
            continue
        
        send_at = task.get("send_at")
        if send_at and send_at > now:
            remaining.append(task)
            continue
        
        # Time to send
        reply_data = task["reply_data"]
        success, msg = send_email(reply_data)
        task["status"] = "sent" if success else "failed"
        task["result"] = msg
        task["sent_at"] = datetime.now().isoformat()
        sent.append(task)
        remaining.append(task)
    
    pending["queue"] = remaining
    save_json(PENDING_FILE, pending)
    
    return sent


# ── Standalone test ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: email_reply.py [latest|index <n>|format <msg>|send]")
        sys.exit(1)
    
    cmd = sys.argv[1]
    
    if cmd == "latest":
        env = find_latest_email()
        if env:
            subj = env.get("subject", "")
            from_info = env.get("from", {})
            print(f"Latest: {from_info.get('name','?')} <{from_info.get('addr', from_info.get('email','?'))}> — {subj}")
        else:
            print("No emails found")
    
    elif cmd == "format" and len(sys.argv) > 2:
        env = find_latest_email()
        if env:
            reply = format_reply(env, sys.argv[2])
            print(json.dumps(reply, indent=2, ensure_ascii=False))
    
    elif cmd == "send":
        env = find_latest_email()
        if env and len(sys.argv) > 2:
            reply = format_reply(env, sys.argv[2])
            success, msg = send_email(reply)
            print(f"Result: {success} — {msg}")
