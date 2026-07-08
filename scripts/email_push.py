#!/usr/bin/env python3
"""
Email Push Formatter — WeChat message formatting, splitting, merging, and command prompts.
"""

import re, textwrap

MAX_CHARS = 1600  # Leave room for header, footer, Hermes wrapper
SUMMARY_HOUR_WINDOW = 60  # minutes


def format_push_header(total: int, accounts: list) -> str:
    account_str = "+".join(sorted(accounts))
    from datetime import datetime
    now = datetime.now().strftime("%m/%d %H:%M")
    return f"📬 {total}封邮件 ({now}) [{account_str}]"


def format_mail_block(msg: dict, show_full_body: bool = False) -> str:
    """
    Format a single email for WeChat push.
    msg keys: id, account, from_name, from_email, subject, final_category, 
              importance, summary_short, action_summary, trust_label, risk_label,
              deadline, has_attachment, attachments_info
    """
    msg_id = msg.get("id", "?")[:20]
    category = msg.get("final_category") or msg.get("rule_category", "个人邮件")
    from_name = msg.get("from_name") or msg.get("from_email", "?")
    subject = msg.get("subject", "")
    importance = msg.get("importance", "medium")
    
    # Priority emoji
    pri_map = {"urgent": "🔴", "high": "🔴", "medium": "🟡", "low": "🟢"}
    pri_emoji = pri_map.get(importance, "🟡")
    
    # Risk/trust indicator
    risk_label = msg.get("risk_label", "")
    trust_label = msg.get("trust_label", "")
    safety = ""
    if risk_label in ("critical", "high"):
        safety = " 🚨"
    elif trust_label == "suspicious":
        safety = " ⚠️"
    
    block = f"[{msg.get('account', '?')}] {pri_emoji} {category}{safety}\n"
    block += f"发件人: {from_name}\n"
    block += f"主题: {subject}\n"
    
    # Summary
    summary = msg.get("summary_short") or msg.get("summary_long", "")
    if summary:
        block += f"\n{summary}\n"
    
    # Deadline
    if msg.get("has_deadline") and msg.get("deadline"):
        block += f"\n⏰ 截止: {msg['deadline']}\n"
    
    # Attachments
    if msg.get("has_attachment"):
        att_info = msg.get("attachments_info", "")
        if att_info:
            block += f"\n📎 {att_info}\n"
        else:
            block += "\n📎 含附件\n"
    
    # Action summary
    action = msg.get("action_summary", "")
    if action:
        block += f"\n📋 {action}\n"
    
    # Commands footer
    mid = msg.get("id", "")[:20]
    block += f"\n[回复:{mid}]"
    
    return block


def split_long_message(text: str, max_chars: int = MAX_CHARS) -> list:
    """Split a long message into WeChat-friendly chunks."""
    if len(text) <= max_chars:
        return [text]
    
    chunks = []
    paragraphs = text.split("\n\n")
    current = ""
    
    for para in paragraphs:
        if len(current) + len(para) + 2 <= max_chars:
            current += ("\n\n" + para) if current else para
        else:
            if current:
                chunks.append(current)
            # If single paragraph is too long, split it
            if len(para) > max_chars:
                sub_chunks = [para[i:i+max_chars] for i in range(0, len(para), max_chars)]
                chunks.extend(sub_chunks)
            else:
                current = para
    
    if current:
        chunks.append(current)
    
    # Add part numbers
    total = len(chunks)
    if total > 1:
        for i in range(total):
            chunks[i] = f"[{i+1}/{total}] {chunks[i]}"
    
    return chunks


def format_summary_batch(mails: list, window_minutes: int = SUMMARY_HOUR_WINDOW) -> str:
    """
    Merge low-value emails into a summary block.
    Groups by category, shows counts and key subjects.
    """
    from collections import Counter
    
    cats = Counter(m.get("final_category") or m.get("rule_category", "其他") for m in mails)
    
    lines = [f"📬 邮件摘要 (过去{window_minutes}分钟)"]
    
    # Low-value: scholar alerts, newsletters, auto, ads
    low_value_cats = {"学术快讯", "学术周报", "订阅推送", "自动通知", "广告", "快递物流"}
    
    for cat, count in cats.most_common():
        if cat in low_value_cats:
            cat_mails = [m for m in mails if (m.get("final_category") or m.get("rule_category")) == cat]
            lines.append(f"\n{cat}: {count}封")
            for m in cat_mails[:3]:
                lines.append(f"  • {m.get('subject', '')[:50]}")
            if len(cat_mails) > 3:
                lines.append(f"  ... 还有{len(cat_mails)-3}封")
    
    return "\n".join(lines)


def format_commands_footer(msg_ids: list) -> str:
    """Generate command footer for user interaction."""
    ids_str = " ".join(msg_ids[:5])
    return f"\n\n回复:\n- 全文 {ids_str}\n- 草拟回复 {ids_str}\n- 今天重要\n- 待处理"


# ── Quick test ──
if __name__ == "__main__":
    test_msg = {
        "id": "E20260701-003",
        "account": "USTC",
        "from_name": "张三",
        "from_email": "zhangsan@ustc.edu.cn",
        "subject": "关于论文修改意见",
        "final_category": "个人邮件",
        "importance": "high",
        "summary_short": "导师反馈论文需要大改，要求本周五前提交修改稿",
        "action_summary": "建议：阅读附件→制定修改计划→草拟回复",
        "has_deadline": True,
        "deadline": "2026-07-03 18:00",
        "has_attachment": True,
        "attachments_info": "revision_comments.pdf (已保存)",
        "trust_label": "trusted",
        "risk_label": "low",
    }
    print(format_mail_block(test_msg))
    print("\n--- split test ---")
    long_text = "x" * 3500
    chunks = split_long_message(long_text)
    print(f"Split into {len(chunks)} chunks, lengths: {[len(c) for c in chunks]}")