#!/usr/bin/env python3
"""
Email Batch Operations — group management, mass forward, bulk actions.
Designed to be imported and used by the agent when processing batch commands.
"""

import json, os, re, subprocess, sys
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

CONTACTS_FILE = os.path.expanduser("~/.hermes/email_contacts.json")
GROUPS_FILE = os.path.expanduser("~/.hermes/email_groups.json")
SETTINGS_FILE = os.path.expanduser("~/.hermes/email_settings.json")

ACCOUNTS = {
    "ustc": {"type": "himalaya", "config": os.path.expanduser("~/.config/himalaya/config_ustc.toml"),
             "email": "wmwen@mail.ustc.edu.cn", "name": "wmwen"},
    "gmail": {"type": "himalaya", "config": os.path.expanduser("~/.config/himalaya/config_gmail.toml"),
             "email": "wmwen1999@gmail.com", "name": "wmwen"},
    "agently": {"type": "agently", "email": "augenstern@agent.qq.com", "name": "augenstern"},
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
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip(), r.returncode
    except subprocess.TimeoutExpired:
        return "", 124


# ── Group Management ─────────────────────────────────────────────

def list_groups():
    """List all contact groups."""
    return load_json(GROUPS_FILE).get("groups", {})


def create_group(name, members):
    """Create or update a contact group. members = list of email addresses or contact names."""
    groups = load_json(GROUPS_FILE)
    groups.setdefault("groups", {})[name] = {
        "members": members,
        "created": datetime.now().isoformat(),
        "updated": datetime.now().isoformat(),
    }
    save_json(GROUPS_FILE, groups)
    return groups["groups"][name]


def delete_group(name):
    groups = load_json(GROUPS_FILE)
    if name in groups.get("groups", {}):
        del groups["groups"][name]
        save_json(GROUPS_FILE, groups)
        return True
    return False


def resolve_group_emails(group_name):
    """Resolve a group name to list of email addresses."""
    groups = load_json(GROUPS_FILE)
    contacts = load_json(CONTACTS_FILE)
    
    group = groups.get("groups", {}).get(group_name)
    if not group:
        return []
    
    emails = []
    for member in group.get("members", []):
        # Try direct email
        if "@" in member:
            emails.append(member)
            continue
        
        # Try contact name/alias
        for alias, addr in contacts.get("aliases", {}).items():
            if member.lower() in alias.lower():
                emails.append(addr)
                break
        else:
            # Try email search
            for addr, c in contacts.get("contacts", {}).items():
                if member.lower() in c.get("name", "").lower():
                    emails.append(addr)
                    break
    
    return emails


# ── Batch Sending ────────────────────────────────────────────────

def send_batch(recipients, subject, body, from_account="ustc", cc=None, attachments=None):
    """
    Send email to multiple recipients.
    Returns (success_count, fail_count, errors).
    """
    acct = ACCOUNTS.get(from_account, ACCOUNTS["ustc"])
    results = {"sent": [], "failed": []}
    
    for recipient in recipients:
        try:
            if acct["type"] == "himalaya":
                success = _send_one_himalaya(acct, recipient, subject, body, cc)
            else:
                success = _send_one_agently(recipient, subject, body)
            
            if success:
                results["sent"].append(recipient)
            else:
                results["failed"].append(recipient)
        except Exception as e:
            results["failed"].append(f"{recipient} ({e})")
    
    return results


def _send_one_himalaya(acct, to_addr, subject, body, cc=None):
    stdin = f"From: {acct['name']} <{acct['email']}>\n"
    stdin += f"To: {to_addr}\n"
    stdin += f"Subject: {subject}\n"
    if cc:
        stdin += f"Cc: {', '.join(cc)}\n"
    stdin += f"\n{body}\n"
    
    cmd = f"cat << 'ENDOFMAIL'\n{stdin}ENDOFMAIL\n | himalaya -c {acct['config']} template send"
    _, rc = run(cmd, timeout=30)
    return rc == 0


def _send_one_agently(to_addr, subject, body):
    cmd = f"agently-cli message +send --to '{to_addr}' --subject '{subject}' --body '{body}'"
    out, rc = run(cmd, timeout=30)
    if rc != 0:
        return False
    try:
        data = json.loads(out)
        if data.get("ok"):
            rd = data.get("data", {})
            if rd.get("confirmation_required"):
                ctk = rd.get("confirmation_token", "")
                cmd2 = f"{cmd} --confirmation-token {ctk}"
                _, rc2 = run(cmd2, timeout=30)
                return rc2 == 0
            return True
    except:
        pass
    return False


# ── Batch Forward ────────────────────────────────────────────────

def forward_to_multiple(msg_id, recipients, note="", from_account="ustc"):
    """Forward an email to multiple recipients."""
    results = {"sent": [], "failed": []}
    
    for recipient in recipients:
        try:
            acct = ACCOUNTS.get(from_account, ACCOUNTS["ustc"])
            if acct["type"] == "himalaya":
                # Get forward template and modify
                cmd = f"himalaya -c {acct['config']} template forward {msg_id}"
                out, rc = run(cmd, timeout=30)
                if rc != 0:
                    results["failed"].append(recipient)
                    continue
                
                # Replace To header
                modified = re.sub(r'^To:.*', f'To: {recipient}', out, flags=re.MULTILINE)
                if note:
                    modified = re.sub(r'\n\n', f'\n\n{note}\n\n', modified, count=1)
                
                send_cmd = f"echo '{modified}' | himalaya -c {acct['config']} template send"
                _, rc2 = run(send_cmd, timeout=30)
                if rc2 == 0:
                    results["sent"].append(recipient)
                else:
                    results["failed"].append(recipient)
            else:
                cmd = f"agently-cli message +forward --id {msg_id} --to '{recipient}' --body '{note}'"
                _, rc2 = run(cmd, timeout=30)
                if rc2 == 0:
                    results["sent"].append(recipient)
                else:
                    results["failed"].append(recipient)
        except Exception as e:
            results["failed"].append(f"{recipient} ({e})")
    
    return results


# ── Bulk Cleanup ─────────────────────────────────────────────────

def mark_all_read(account="ustc", folder="INBOX"):
    """Mark all emails in a folder as read."""
    acct = ACCOUNTS.get(account, ACCOUNTS["ustc"])
    if acct["type"] == "himalaya":
        cmd = f"himalaya -c {acct['config']} envelope list --page-size 100 --output json"
        out, rc = run(cmd, timeout=30)
        if rc != 0:
            return 0
        try:
            envs = json.loads(out)
            count = 0
            for env in envs:
                eid = env.get("id", "")
                if eid and "Seen" not in env.get("flags", []):
                    run(f"himalaya -c {acct['config']} flag add {eid} --flag seen", timeout=10)
                    count += 1
            return count
        except:
            return 0
    return 0


# ── Standalone ───────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: email_batch.py [groups|send|forward|cleanup] ...")
        sys.exit(1)
    
    cmd = sys.argv[1]
    
    if cmd == "groups":
        for name, group in list_groups().items():
            members = group.get("members", [])
            print(f"  {name}: {', '.join(members)}")
    
    elif cmd == "send" and len(sys.argv) > 4:
        group = sys.argv[2]
        subject = sys.argv[3]
        body = sys.argv[4]
        recipients = resolve_group_emails(group)
        if not recipients:
            print(f"Group '{group}' not found or empty")
        else:
            results = send_batch(recipients, subject, body)
            print(f"Sent: {len(results['sent'])}, Failed: {len(results['failed'])}")