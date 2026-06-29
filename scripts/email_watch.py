#!/usr/bin/env python3
"""Email watchdog — check all accounts for new mail, classify, output summary.

Runs as a cron job (no_agent=True). Stdout is delivered verbatim via WeChat/Telegram.
Silent when there's nothing new.
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

SEEN_FILE = os.path.expanduser("~/.hermes/email_watch_seen.json")
LOOKBACK = 5  # how many recent emails to check per account

# ── Configure your accounts here ────────────────────────────────

ACCOUNTS = [
    {
        "name": "USTC",
        "type": "himalaya",
        "config": os.path.expanduser("~/.config/himalaya/config_ustc.toml"),
        "email": "wmwen@mail.ustc.edu.cn",
    },
    {
        "name": "Gmail",
        "type": "himalaya",
        "config": os.path.expanduser("~/.config/himalaya/config_gmail.toml"),
        "email": "wmwen1999@gmail.com",
    },
    {
        "name": "Agently",
        "type": "agently",
        "email": "augenstern@agent.qq.com",
    },
]


def run(cmd, timeout=30):
    """Run a shell command, return (stdout, exit_code)."""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip(), r.returncode
    except subprocess.TimeoutExpired:
        return "", 124


def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE) as f:
            return json.load(f)
    return {}


def save_seen(data):
    os.makedirs(os.path.dirname(SEEN_FILE), exist_ok=True)
    with open(SEEN_FILE, "w") as f:
        json.dump(data, f, indent=2)


def list_himalaya(config_path):
    """List recent envelopes via himalaya."""
    cmd = f"himalaya -c {config_path} envelope list --page-size {LOOKBACK} --output json"
    out, rc = run(cmd, timeout=30)
    if rc != 0:
        return []
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return []


def read_himalaya(config_path, msg_id):
    """Read a full message via himalaya."""
    cmd = f"himalaya -c {config_path} message read {msg_id} --output json"
    out, rc = run(cmd, timeout=30)
    if rc != 0:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"text": out}


def list_agently():
    """List recent envelopes via agently-cli."""
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
    """Read a full message via agently-cli."""
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


# ── Classification ──────────────────────────────────────────────

def classify(email_data):
    """
    Classify an email. Returns (category_emoji, category_name, priority, summary).
    email_data is a dict with keys: subject, from_addr, from_name, body, to_addr
    """
    subject = (email_data.get("subject") or "").lower()
    body = (email_data.get("body") or "").lower()
    from_addr = (email_data.get("from_addr") or "").lower()
    from_name = (email_data.get("from_name") or "").lower()
    to_addr = (email_data.get("to_addr") or "").lower()
    full_text = subject + " " + body

    # ── 🔐 Verification codes ──
    vc_keywords = ["verification code", "security code", "验证码", "确认码",
                   "auth code", "login code", "authentication code", "短信验证"]
    for kw in vc_keywords:
        if kw in full_text:
            # Try to extract the actual code number
            code_match = re.search(r"\b(\d{4,8})\b", body) if body else None
            code_str = f" 码: {code_match.group(1)}" if code_match else ""
            return ("🔐", "验证码", "high", f"{email_data.get('subject','')}{code_str}")

    # ── ⚠️ Account security ──
    sec_keywords = ["password changed", "密码.*修改", "new sign.in", "异常登录",
                    "unusual activity", "security alert", "安全提醒", "login attempt",
                    "new device", "陌生设备", "账户恢复", "两步验证"]
    for kw in sec_keywords:
        if re.search(kw, full_text):
            return ("⚠️", "账户安全", "high", email_data.get("subject", "安全提醒"))

    # ── 📄 Paper/Review decisions ──
    paper_patterns = [
        (r"(?:paper|manuscript|submission).*(?:accept|接收|录用)", "论文接收"),
        (r"(?:decision|decision letter).*(?:accept|接收)", "论文决定"),
        (r"(?:review|审稿).*(?:invitation|邀请|request)", "审稿邀请"),
        (r"(?:paper|manuscript).*(?:reject|拒稿|decline)", "论文决定"),
        (r"(?:major|minor)\s+revision", "修改意见"),
        (r"decision\s+on\s+(?:your|the)\s+(?:paper|manuscript|submission)", "论文决定"),
        (r"editorial\s+decision", "论文决定"),
        (r"your\s+submission\s+(?:to|for)", "论文状态"),
    ]
    for pat, label in paper_patterns:
        if re.search(pat, full_text):
            return ("📄", label, "high", email_data.get("subject", "论文状态更新"))

    # ── 💰 Payments/Registration ──
    pay_keywords = ["registration", "invoice", "payment", "fee", "receipt",
                    "注册费", "版面费", "发票", "缴费", "汇款", "订单确认",
                    "order confirm", "purchase"]
    for kw in pay_keywords:
        if re.search(kw, full_text):
            return ("💰", "付款/注册", "high", email_data.get("subject", "付款相关"))

    # ── 🏫 USTC official ──
    if from_addr.endswith("@ustc.edu.cn") or "ustc" in from_addr:
        official_kw = ["通知", "公告", "notice", "announcement", "办公", "行政",
                       "研究生院", "教务处", "图书馆", "网络中心", "信息化",
                       "保卫", "后勤", "财务处", "人事"]
        for kw in official_kw:
            if kw in subject or kw in from_name:
                return ("🏫", "学校通知", "medium", email_data.get("subject", "学校通知"))

    # ── 📚 Academic alerts ──
    scholar_senders = ["scholaralerts", "google scholar", "google 学术",
                       "arxiv", "researchgate", "semanticscholar", "academia.edu"]
    for s in scholar_senders:
        if s in from_addr or s in from_name:
            return ("📚", "学术快讯", "low", email_data.get("subject", "学术快讯"))

    # ── 🗑️ Ads / Spam ──
    spam_keywords = ["unsubscribe", "退订", "discount", "折扣", "promotion",
                     "促销", "sale", "newsletter", "deal", "offer", "limited time",
                     "优惠", "广告", "subscribe now", "免费领取", "限时",
                     "双11", "618", "大促", "满减"]
    for kw in spam_keywords:
        if kw in full_text:
            return ("🗑️", "广告", "skip", None)

    # Marketing platform domains
    marketing_domains = ["mailchimp", "sendgrid", "hubspot", "marketo",
                         "campaign", "litmus", "constantcontact", "mailgun"]
    for d in marketing_domains:
        if d in from_addr:
            return ("🗑️", "广告", "skip", None)

    # Auto-generated / noreply
    noreply_patterns = ["noreply@", "no-reply@", "donotreply@", "mailer-daemon@",
                        "bounce@", "auto-reply@"]
    for p in noreply_patterns:
        if p in from_addr:
            # Only skip if it's not a security/verification already caught above
            return ("🤖", "自动邮件", "skip", None)

    # ── 💬 Personal / catch-all ──
    sender_info = from_name.strip('"') if from_name else from_addr
    return ("💬", "个人邮件", "medium", f"发件人: {sender_info} | {email_data.get('subject','')}")


# ── Main ────────────────────────────────────────────────────────


def check_account(acct):
    """Check one account for new emails. Returns list of alert strings."""
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
        msg_id = env.get("id") or env.get("message_id", "")
        key = f"{prefix}{msg_id}"

        if key in seen:
            continue

        # New email — read it
        if acct["type"] == "himalaya":
            msg = read_himalaya(acct["config"], msg_id)
        else:
            msg = read_agently(msg_id)

        if msg is None:
            seen[key] = True
            updated = True
            continue

        # Extract relevant fields
        if acct["type"] == "himalaya":
            body = msg.get("text", "") if isinstance(msg, dict) else str(msg)
            from_addr = env.get("from", {}).get("addr", "")
            from_name = env.get("from", {}).get("name", "")
            subject = env.get("subject", "")
            to_addr = env.get("to", {}).get("addr", "") if isinstance(env.get("to"), dict) else ""
        else:
            body = msg.get("body", "") if isinstance(msg, dict) else ""
            from_addr = env.get("from", {}).get("email", "")
            from_name = env.get("from", {}).get("name", "")
            subject = env.get("subject", "")
            to_list = env.get("to") or [{}]
            to_addr = to_list[0].get("email", "") if to_list else ""

        email_data = {
            "subject": subject,
            "from_addr": from_addr,
            "from_name": from_name,
            "body": body[:3000],
            "to_addr": to_addr,
        }

        emoji, category, priority, summary = classify(email_data)

        seen[key] = True
        updated = True

        if priority == "skip":
            continue

        alerts.append(f"[{acct_name}] {emoji} {category}\n{summary}")

    if updated:
        save_seen(seen)

    return alerts


def main():
    all_alerts = []

    for acct in ACCOUNTS:
        try:
            alerts = check_account(acct)
            all_alerts.extend(alerts)
        except Exception as e:
            all_alerts.append(f"[{acct['name']}] ⚠️ 检查失败: {e}")

    if not all_alerts:
        return ""

    now = datetime.now().strftime("%H:%M")
    header = f"📬 新邮件 ({now})"
    return header + "\n" + "\n\n".join(all_alerts)


if __name__ == "__main__":
    output = main()
    if output:
        print(output)