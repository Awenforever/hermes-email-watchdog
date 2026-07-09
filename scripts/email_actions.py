#!/usr/bin/env python3
"""
Email Actions — proactive execution: link extraction, attachment download,
schedule maintenance, draft reply workflow, and caching interface.

Runs as no_agent cron or imported by agent for WeChat commands.
"""

import json, os, re, sys, subprocess
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

try:
    import email_store
    import email_delivery
except ImportError:
    email_store = None
    email_delivery = None

try:
    import email_config
except ImportError:
    email_config = None

CACHE_DIR = email_config.get_path("cache_dir") if email_config else os.path.expanduser("~/.hermes/email_cache")


# ═══════════════════════════════════════════════════════════
# #2: Link extraction & proactive access
# ═══════════════════════════════════════════════════════════

def extract_links_from_body(body: str) -> list:
    """Extract URLs from email body."""
    urls = re.findall(r'https?://[^\s<>"\']+', body)
    return urls


def save_links_to_store(msg_id: str, urls: list):
    """Save extracted links to SQLite store."""
    if not email_store:
        return
    for i, url in enumerate(urls):
        link_id = f"{msg_id}_link_{i}"
        domain = url.split("/")[2] if "//" in url and len(url.split("/")) > 2 else ""
        email_store.add_link({
            "id": link_id,
            "message_id": msg_id,
            "url": url,
            "domain": domain,
            "extract_status": "pending",
        })


def process_pending_links(limit=5) -> list:
    """Access pending links and extract titles. Returns results for push."""
    if not email_store:
        return []
    
    links = email_store.get_pending_links(limit)
    results = []
    
    for link in links:
        url = link.get("url", "")
        if not url:
            continue
        
        try:
            # Use web_extract or curl to get page title
            import urllib.request, ssl
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            
            req = urllib.request.Request(url, headers={"User-Agent": "Hermes/1.0"})
            with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
                html = resp.read().decode("utf-8", errors="ignore")[:10000]
                title_match = re.search(r'<title[^>]*>([^<]+)</title>', html, re.IGNORECASE)
                title = title_match.group(1).strip() if title_match else url[:60]
            
            email_store.add_link({
                "id": link["id"],
                "message_id": link["message_id"],
                "extract_status": "done",
                "extracted_title": title,
            })
            results.append({"url": url[:60], "title": title[:80]})
        except Exception as e:
            email_store.add_link({
                "id": link["id"],
                "message_id": link["message_id"],
                "extract_status": "failed",
                "extracted_title": str(e)[:100],
            })
    
    return results


# ═══════════════════════════════════════════════════════════
# #3: Scholar alert batching
# ═══════════════════════════════════════════════════════════

def batch_scholar_alerts() -> str:
    """Deprecated: semantic delivery now decides whether low-value mail is skipped or summarized."""
    return ""


# ═══════════════════════════════════════════════════════════
# #4: Draft reply workflow
# ═══════════════════════════════════════════════════════════

DRAFTS_DIR = email_config.get_path("drafts_dir") if email_config else os.path.expanduser("~/.hermes/email_drafts")


def save_draft(msg_id: str, draft_body: str):
    """Save a reply draft for approval."""
    os.makedirs(DRAFTS_DIR, exist_ok=True)
    draft_file = os.path.join(DRAFTS_DIR, f"{msg_id}.draft")
    with open(draft_file, "w") as f:
        f.write(draft_body)
    
    if email_store:
        email_store.create_action({
            "message_id": msg_id,
            "action_type": "draft_reply",
            "action_status": "pending_approval",
            "requires_approval": 1,
            "plan_json": json.dumps({"draft_file": draft_file}),
        })


def load_draft(msg_id: str) -> str:
    """Load a saved draft."""
    draft_file = os.path.join(DRAFTS_DIR, f"{msg_id}.draft")
    if os.path.exists(draft_file):
        with open(draft_file) as f:
            return f.read()
    return ""


def approve_and_send(msg_id: str, from_account: str = "ustc") -> str:
    """Send an approved draft. Called after user confirms in WeChat."""
    draft = load_draft(msg_id)
    if not draft:
        return "无待发送的草稿"
    
    msg = email_store.get_message(msg_id) if email_store else None
    if not msg:
        return f"未找到邮件 {msg_id}"
    
    to_addr = msg.get("from_email", "")
    subject = f"Re: {msg.get('subject', '')}"
    
    account_map = email_config.get_account_map() if email_config else {}
    acct = account_map.get(from_account.lower(), {}) if account_map else {}
    cfg = acct.get("himalaya_config") or acct.get("config") or os.path.expanduser("~/.config/himalaya/config_ustc.toml")
    from_email = acct.get("email") or "wmwen@mail.ustc.edu.cn"
    from_name = acct.get("display_name") or acct.get("name") or "wmwen"
    
    stdin = f"From: {from_name} <{from_email}>\nTo: {to_addr}\nSubject: {subject}\n\n{draft}\n"
    try:
        result = subprocess.run(
            ["himalaya", "-c", cfg, "template", "send"],
            input=stdin, capture_output=True, text=True, timeout=30
        )
        success = result.returncode == 0
        
        if email_store:
            email_store.update_action(
                email_store.create_action({
                    "message_id": msg_id,
                    "action_type": "draft_reply",
                    "action_status": "sent" if success else "failed",
                    "requires_approval": 0,
                    "approved_by_user": 1,
                    "approved_at": datetime.now().isoformat(),
                    "executed_at": datetime.now().isoformat(),
                }),
                {"action_status": "sent" if success else "failed"}
            )
        
        return "✅ 邮件已发送" if success else f"❌ 发送失败: {result.stderr[:200]}"
    except Exception as e:
        return f"❌ 发送异常: {e}"


# ═══════════════════════════════════════════════════════════
# #6: Cache retrieval interface
# ═══════════════════════════════════════════════════════════

def get_full_email(msg_id: str) -> dict:
    """Retrieve full email from cache or store."""
    # Try cache first
    cache_file = os.path.join(CACHE_DIR, f"{msg_id}.json")
    if os.path.exists(cache_file):
        with open(cache_file) as f:
            return json.load(f)
    
    # Fallback to store
    if email_store:
        msg = email_store.get_message(msg_id)
        if msg:
            return msg
    
    return None


def get_message_by_number(n: int) -> dict:
    """Get the Nth most recent message."""
    if not email_store:
        return None
    conn = email_store._get_conn()
    rows = conn.execute(
        "SELECT * FROM messages WHERE push_status='pushed' ORDER BY date_sent DESC LIMIT ?",
        (n,)
    ).fetchall()
    if 0 <= (n-1) < len(rows):
        return dict(rows[n-1])
    return None


def list_cached_emails(limit=20) -> list:
    """List recently cached emails."""
    if not os.path.isdir(CACHE_DIR):
        return []
    
    files = sorted(
        [f for f in os.listdir(CACHE_DIR) if f.endswith(".json")],
        key=lambda f: os.path.getmtime(os.path.join(CACHE_DIR, f)),
        reverse=True
    )[:limit]
    
    results = []
    for f in files:
        try:
            with open(os.path.join(CACHE_DIR, f)) as fp:
                data = json.load(fp)
                results.append({
                    "id": data.get("msg_id", f.replace(".json", "")),
                    "subject": data.get("subject", "")[:60],
                    "from": data.get("from_name", "") or data.get("from_addr", ""),
                    "cached_at": data.get("cached_at", ""),
                })
        except:
            pass
    
    return results


# ═══════════════════════════════════════════════════════════
# #5: Pending send approval workflow
# ═══════════════════════════════════════════════════════════

PENDING_FILE = email_config.get_path("pending") if email_config else os.path.expanduser("~/.hermes/email_pending.json")


def approve_pending_send(task_id: str) -> str:
    """Approve a pending scheduled send."""
    pending = {}
    if os.path.exists(PENDING_FILE):
        with open(PENDING_FILE) as f:
            pending = json.load(f)
    
    for task in pending.get("queue", []):
        if task.get("id") == task_id:
            task["status"] = "approved"
            with open(PENDING_FILE, "w") as f:
                json.dump(pending, f, indent=2)
            return f"✅ 定时发送 {task_id} 已批准"
    
    return f"未找到任务 {task_id}"


# ── Main cron entry ──────────────────────────────────────────────

def main():
    """Called by cron to process links and batch scholar alerts."""
    outputs = []
    
    # Process pending links
    link_results = process_pending_links(limit=3)
    if link_results:
        outputs.append("🔗 链接提取:")
        for r in link_results:
            outputs.append(f"  {r['title'][:60]}")
    
    # Batch scholar alerts
    scholar_batch = batch_scholar_alerts()
    if scholar_batch:
        outputs.append(scholar_batch)
    
    return "\n\n".join(outputs) if outputs else ""


if __name__ == "__main__":
    output = main()
    if output:
        print(output)
