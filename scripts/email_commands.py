#!/usr/bin/env python3
"""
Email Command Handler — parses WeChat commands and executes email operations.
Imported by Hermes agent when user sends email-related commands via WeChat.

Supports:
  全文 #N / full #N         → return full email body
  附件 #N                    → list attachments
  草拟回复 #N / draft #N     → LLM drafts a reply (agent handles this)
  发送 #N                    → send drafted reply
  标记已处理 #N / done #N    → mark as processed
  今天重要 / today           → show today's important emails
  待处理 / pending           → show emails needing action
  摘要 / summary             → show category breakdown
"""

import json, os, re, sys
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

try:
    import email_store
except ImportError:
    email_store = None

CACHE_DIR = os.path.expanduser("~/.hermes/email_cache")


# ── Command Parser ───────────────────────────────────────────────

def parse_command(text: str) -> dict:
    """
    Parse a WeChat command string.
    Returns {"cmd": str, "id": str/int, "args": dict} or None if not a command.
    """
    text = text.strip()
    
    patterns = [
        # (regex, cmd_name, id_group)
        (r'^(?:全文|full|查看)\s*#?(\d+)', '全文', 1),
        (r'^(?:附件|attachments?)\s*#?(\d+)', '附件', 1),
        (r'^(?:草拟|draft)\s*(?:回复)?\s*#?(\d+)', '草拟回复', 1),
        (r'^(?:发送|send)\s*#?(\d+)', '发送', 1),
        (r'^(?:标记|done|完成|已处理)\s*#?(\d+)', '标记已处理', 1),
        (r'^(?:今天|today)\s*(?:重要|important)?', '今天重要', 0),
        (r'^(?:待处理|pending|未处理)', '待处理', 0),
        (r'^(?:摘要|summary)', '摘要', 0),
        (r'^(?:帮助|help|命令)', '帮助', 0),
    ]
    
    for pat, cmd, group in patterns:
        m = re.match(pat, text, re.IGNORECASE)
        if m:
            return {
                "cmd": cmd,
                "id": int(m.group(group)) if group > 0 and m.lastindex >= group else None,
                "raw": text,
            }
    
    return None


# ── Command Executors ────────────────────────────────────────────

def cmd_全文(msg_id_num: int) -> str:
    """Return full email body for message #N."""
    msg = _find_message_by_number(msg_id_num)
    if not msg:
        return f"未找到邮件 #{msg_id_num}"
    
    # Load full body from cache
    body = _load_from_cache(msg["id"])
    if not body:
        body = msg.get("summary_long") or msg.get("subject", "(无内容)")
    
    from_name = msg.get("from_name") or msg.get("from_email", "?")
    subject = msg.get("subject", "")
    date = msg.get("date_sent", "")
    category = msg.get("final_category") or msg.get("rule_category", "")
    
    lines = [
        f"📧 #{msg_id_num} [{msg.get('account','?')}]",
        f"发件人: {from_name}",
        f"时间: {date}",
        f"主题: {subject}",
        f"分类: {category}",
        f"\n{body}",
    ]
    return "\n".join(lines)


def cmd_附件(msg_id_num: int) -> str:
    """List attachments for message #N."""
    msg = _find_message_by_number(msg_id_num)
    if not msg:
        return f"未找到邮件 #{msg_id_num}"
    
    if email_store:
        attachments = email_store.get_attachments(msg["id"])
        if attachments:
            lines = [f"📎 #{msg_id_num} 附件:"]
            for i, att in enumerate(attachments):
                status = att.get("download_status", "?")
                path = att.get("local_path", "")
                name = att.get("filename", "?")
                lines.append(f"  {i+1}. {name} ({status})")
                if path and os.path.exists(path):
                    lines.append(f"     位置: {path}")
            return "\n".join(lines)
    
    return f"#{msg_id_num} 无附件"


def cmd_今天重要() -> str:
    """Show today's important emails."""
    if not email_store:
        return "存储未就绪"
    
    today = datetime.now().strftime("%Y-%m-%d")
    conn = email_store._get_conn()
    rows = conn.execute(
        """SELECT * FROM messages 
           WHERE date_sent LIKE ? AND importance IN ('high','urgent')
           ORDER BY date_sent DESC LIMIT 10""",
        (f"{today}%",)
    ).fetchall()
    
    if not rows:
        return "今天没有重要邮件"
    
    lines = [f"📬 今天重要邮件 ({len(rows)}封):"]
    for i, row in enumerate(rows):
        m = dict(row)
        lines.append(f"  #{i+1} [{m.get('account','')}] {m.get('from_name','')}: {m.get('subject','')[:40]}")
    
    return "\n".join(lines)


def cmd_待处理() -> str:
    """Show emails needing action."""
    if not email_store:
        return "存储未就绪"
    
    conn = email_store._get_conn()
    rows = conn.execute(
        """SELECT * FROM messages 
           WHERE (needs_reply=1 OR has_deadline=1 OR importance IN ('high','urgent'))
           AND push_status='pushed'
           ORDER BY date_sent DESC LIMIT 10"""
    ).fetchall()
    
    if not rows:
        return "没有待处理邮件"
    
    lines = [f"📋 待处理邮件 ({len(rows)}封):"]
    for i, row in enumerate(rows):
        m = dict(row)
        action = m.get("action_summary") or ""
        deadline = f" ⏰{m.get('deadline')}" if m.get("deadline") else ""
        lines.append(f"  #{i+1} [{m.get('account','')}] {m.get('summary_short') or m.get('subject','')[:50]}{deadline}")
        if action:
            lines.append(f"     📋 {action}")
    
    return "\n".join(lines)


def cmd_摘要() -> str:
    """Show email summary by category."""
    if not email_store:
        return "存储未就绪"
    
    today = datetime.now().strftime("%Y-%m-%d")
    conn = email_store._get_conn()
    rows = conn.execute(
        """SELECT final_category, COUNT(*) as cnt, 
                  SUM(CASE WHEN importance IN ('high','urgent') THEN 1 ELSE 0 END) as important
           FROM messages WHERE date_sent LIKE ?
           GROUP BY final_category ORDER BY cnt DESC""",
        (f"{today}%",)
    ).fetchall()
    
    if not rows:
        return "今天还没有邮件"
    
    lines = ["📊 今日邮件摘要:"]
    for row in rows:
        cat, cnt, imp = row["final_category"], row["cnt"], row["important"]
        imp_str = f" (重要:{imp})" if imp > 0 else ""
        lines.append(f"  {cat}: {cnt}封{imp_str}")
    
    return "\n".join(lines)


def cmd_帮助() -> str:
    return """📧 邮件命令:

全文 #N    查看邮件原文
附件 #N    查看附件列表
草拟回复 #N  AI 起草回复
发送 #N     发送草拟的回复
标记已处理 #N  归档
今天重要    查看今天的重要邮件
待处理      查看需要处理的邮件
摘要        查看今日邮件统计"""


# ── Helpers ──────────────────────────────────────────────────────

def _find_message_by_number(n: int) -> dict:
    """Find the Nth most recent message from the store."""
    if not email_store:
        return None
    
    conn = email_store._get_conn()
    rows = conn.execute(
        "SELECT * FROM messages WHERE push_status='pushed' ORDER BY date_sent DESC LIMIT ?",
        (n,)
    ).fetchall()
    
    if 0 <= (n - 1) < len(rows):
        return dict(rows[n - 1])
    return None


def _load_from_cache(msg_id: str) -> str:
    """Load full email body from cache."""
    cache_file = os.path.join(CACHE_DIR, f"{msg_id}.json")
    if os.path.exists(cache_file):
        try:
            with open(cache_file) as f:
                data = json.load(f)
                return data.get("body", "")
        except:
            pass
    return ""


# ── Main handler ─────────────────────────────────────────────────

def handle(text: str) -> str:
    """Main entry point. Parse and execute a command."""
    parsed = parse_command(text)
    if not parsed:
        return ""
    
    cmd = parsed["cmd"]
    n = parsed.get("id")
    
    handlers = {
        "全文": lambda: cmd_全文(n),
        "附件": lambda: cmd_附件(n),
        "草拟回复": lambda: f"已收到草拟回复 #{n} 的请求，我来帮你起草。",
        "发送": lambda: f"确认发送 #{n} 的回复吗？请回复 确认发送 #{n}",
        "标记已处理": lambda: _mark_done(n),
        "今天重要": cmd_今天重要,
        "待处理": cmd_待处理,
        "摘要": cmd_摘要,
        "帮助": cmd_帮助,
    }
    
    handler = handlers.get(cmd)
    if handler:
        return handler()
    
    return f"未知命令: {cmd}\n回复 帮助 查看可用命令"


def _mark_done(n: int) -> str:
    """Mark message as processed."""
    if not email_store:
        return "存储未就绪"
    
    msg = _find_message_by_number(n)
    if not msg:
        return f"未找到邮件 #{n}"
    
    email_store.upsert_message({
        "id": msg["id"],
        "push_status": "archived",
        "updated_at": datetime.now().isoformat(),
    })
    
    return f"✅ #{n} 已标记为已处理"


# ── Standalone test ──
if __name__ == "__main__":
    tests = ["全文 #1", "附件 3", "草拟回复 #2", "今天重要", "待处理", "摘要", "帮助", "hello"]
    for t in tests:
        parsed = parse_command(t)
        print(f"'{t}' → {parsed}")