#!/usr/bin/env python3
"""
Email Pending Queue Processor — checks for scheduled emails that are due
and sends them. Runs as no_agent cron job. Silent when nothing is due.
"""

import json, os, subprocess, sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

try:
    import email_config
except ImportError:
    email_config = None

PENDING_FILE = email_config.get_path("pending") if email_config else os.path.expanduser("~/.hermes/email_pending.json")
THREADS_FILE = email_config.get_path("threads") if email_config else os.path.expanduser("~/.hermes/email_threads.json")

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


def run(cmd, timeout=30):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip(), r.returncode
    except subprocess.TimeoutExpired:
        return "", 124


def send_via_himalaya(acct, reply_data):
    """Send via himalaya."""
    stdin = f"From: {reply_data['from_name']} <{reply_data['from_email']}>\n"
    stdin += f"To: {reply_data['to_name']} <{reply_data['to']}>\n"
    stdin += f"Subject: {reply_data['subject']}\n"
    if reply_data.get("cc"):
        stdin += f"Cc: {', '.join(reply_data['cc'])}\n"
    stdin += f"\n{reply_data['body']}\n"
    try:
        result = subprocess.run(
            ["himalaya", "-c", acct.get("himalaya_config") or acct["config"], "template", "send"],
            input=stdin, capture_output=True, text=True, timeout=30,
        )
        return (result.returncode == 0, result.stdout.strip() or result.stderr.strip())
    except subprocess.TimeoutExpired:
        return (False, "")


def send_via_agently(reply_data):
    """Send via agently-cli."""
    cmd = ["agently-cli", "message", "+send", "--to", reply_data["to"], "--subject", reply_data["subject"], "--body", reply_data["body"]]
    out, rc = run(cmd, timeout=30)
    
    if rc != 0:
        return (False, out)
    
    try:
        data = json.loads(out)
        if data.get("ok"):
            rd = data.get("data", {})
            if rd.get("confirmation_required"):
                ctk = rd.get("confirmation_token", "")
                cmd2 = cmd + ["--confirmation-token", ctk]
                out2, rc2 = run(cmd2, timeout=30)
                return (rc2 == 0, out2)
            return (True, "ok")
    except json.JSONDecodeError:
        pass
    return (False, out)


def send_email(reply_data):
    """Route to correct sender."""
    acct = reply_data.get("account")
    if not acct:
        acct_name = reply_data.get("from_account", "ustc")
        acct = ACCOUNTS.get(acct_name, ACCOUNTS["ustc"])
    
    if acct["type"] == "himalaya":
        return send_via_himalaya(acct, reply_data)
    else:
        return send_via_agently(reply_data)


def process_queue():
    """Check and send pending emails. Returns list of (task_id, status, message)."""
    pending = load_json(PENDING_FILE)
    queue = pending.get("queue", [])
    now = datetime.now()
    
    results = []
    updated = False
    
    for task in queue:
        if task.get("status") != "pending":
            continue
        
        send_at_str = task.get("send_at")
        if not send_at_str:
            continue
        
        try:
            send_at = datetime.fromisoformat(send_at_str)
        except (ValueError, TypeError):
            continue
        
        if send_at > now:
            continue
        
        # Time to send
        reply_data = task.get("reply_data", {})
        success, msg = send_email(reply_data)
        
        task["status"] = "sent" if success else "failed"
        task["result"] = msg
        task["sent_at"] = now.isoformat()
        task["error"] = msg if not success else None
        updated = True
        
        results.append({
            "id": task.get("id", "?"),
            "topic": task.get("topic", reply_data.get("subject", "?")),
            "to": reply_data.get("to_name", reply_data.get("to", "?")),
            "status": task["status"],
            "error": msg if not success else None,
        })
        
        # Track thread if successful
        if success and task.get("track_thread"):
            threads = load_json(THREADS_FILE)
            thread_id = f"thread_{task.get('topic','scheduled')}_{now.strftime('%Y%m')}"
            threads.setdefault("threads", {})[thread_id] = {
                "topic": task.get("topic", "Scheduled email"),
                "participants": [reply_data.get("to", "")],
                "user_question": task.get("user_question", ""),
                "created": now.isoformat(),
                "status": "waiting_reply",
                "messages": [],
                "sent_msg_id": task.get("id"),
                "scheduled": True,
            }
            save_json(THREADS_FILE, threads)
    
    if updated:
        pending["queue"] = queue
        save_json(PENDING_FILE, pending)
    
    return results


def cleanup_old_tasks(days=30):
    """Remove tasks older than N days."""
    pending = load_json(PENDING_FILE)
    queue = pending.get("queue", [])
    cutoff = datetime.now().isoformat()
    
    # Keep only recent tasks and pending ones
    kept = []
    for task in queue:
        if task.get("status") == "pending":
            kept.append(task)
            continue
        created = task.get("created", "")
        if created > cutoff:
            kept.append(task)
    
    if len(kept) != len(queue):
        pending["queue"] = kept
        save_json(PENDING_FILE, pending)


def main():
    results = process_queue()
    cleanup_old_tasks()
    
    if not results:
        return ""
    
    lines = ["⏰ 定时邮件已发送:"]
    for r in results:
        status = "✅" if r["status"] == "sent" else "❌"
        lines.append(f"  {status} {r['topic'][:40]} → {r['to']}")
        if r.get("error"):
            lines.append(f"     错误: {r['error'][:100]}")
    
    return "\n".join(lines)


if __name__ == "__main__":
    output = main()
    if output:
        print(output)
