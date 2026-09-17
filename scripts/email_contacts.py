#!/usr/bin/env python3
"""Email contact management — auto-learn from emails, resolve names to addresses."""

import json, os, re
from datetime import datetime
from pathlib import Path

try:
    import email_config
except ImportError:
    email_config = None

CONTACTS_FILE = email_config.get_path("contacts") if email_config else os.path.expanduser("~/.hermes/email_contacts.json")
SETTINGS_FILE = email_config.get_path("settings") if email_config else os.path.expanduser("~/.hermes/email_settings.json")


def load_contacts():
    if os.path.exists(CONTACTS_FILE):
        with open(CONTACTS_FILE) as f:
            data = json.load(f)
        # Defensive: ensure expected structure even if file was corrupted/empty
        if "contacts" not in data:
            data["contacts"] = {}
        if "aliases" not in data:
            data["aliases"] = {}
        return data
    return {"contacts": {}, "aliases": {}}


def save_contacts(data):
    os.makedirs(os.path.dirname(CONTACTS_FILE), exist_ok=True)
    with open(CONTACTS_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE) as f:
            return json.load(f)
    return {}


def normalize_email(addr):
    """Lowercase and strip."""
    return (addr or "").strip().lower()


def extract_name_from_email(addr):
    """Try to extract a name from an email address like firstname.lastname@domain."""
    addr = normalize_email(addr)
    local = addr.split("@")[0] if "@" in addr else addr
    # Try common patterns
    patterns = [
        r"^([a-z]+)\.([a-z]+)$",        # first.last
        r"^([a-z]+)([a-z]+)$",          # firstlast
        r"^([a-z]+)_([a-z]+)$",         # first_last
        r"^([a-z]+)$",                   # first
    ]
    for pat in patterns:
        m = re.match(pat, local)
        if m:
            parts = [p.capitalize() for p in m.groups()]
            return " ".join(parts)
    return local


def learn_contact(from_addr, from_name=None):
    """Auto-learn a contact from an email. Returns the contact's display name."""
    contacts = load_contacts()
    addr = normalize_email(from_addr)
    
    # Extract display name
    name = (from_name or "").strip().strip('"\'')
    if not name:
        name = extract_name_from_email(addr)
    
    now = datetime.now().isoformat()
    
    if addr in contacts["contacts"]:
        c = contacts["contacts"][addr]
        c["last_contact"] = now
        c["contact_count"] = c.get("contact_count", 0) + 1
        # Update name if we now have a better one
        if name and not name.startswith(name[0].lower() if name else ""):
            if name != c.get("name"):
                old_name = c.get("name", "")
                c["name"] = name
                # Update alias
                if old_name and old_name in contacts["aliases"]:
                    del contacts["aliases"][old_name]
                contacts["aliases"][name] = addr
    else:
        contacts["contacts"][addr] = {
            "name": name,
            "email": addr,
            "first_seen": now,
            "last_contact": now,
            "contact_count": 1,
            "affiliation": guess_affiliation(addr),
        }
        contacts["aliases"][name] = addr
    
    save_contacts(contacts)
    return name


def guess_affiliation(email):
    """Guess organization from email domain."""
    domain = email.split("@")[-1] if "@" in email else ""
    known = {
        "ustc.edu.cn": "USTC",
        "mail.ustc.edu.cn": "USTC",
        "gmail.com": "Personal",
        "qq.com": "Personal",
        "163.com": "Personal",
        "outlook.com": "Personal",
        "hotmail.com": "Personal",
        "agent.qq.com": "Agently Mail",
    }
    return known.get(domain, domain)


def resolve_recipient(query):
    """
    Resolve a user query to an email address.
    Supports: name, alias, email address, role keywords.
    Returns (email, display_name) or (None, error_message).
    """
    contacts = load_contacts()
    query_lower = query.strip().lower()
    
    # Direct email match
    if "@" in query_lower and "." in query_lower.split("@")[-1]:
        addr = normalize_email(query)
        name = contacts["contacts"].get(addr, {}).get("name", addr)
        return (addr, name)
    
    # Alias match (exact)
    for alias, addr in contacts["aliases"].items():
        if query_lower == alias.lower():
            name = contacts["contacts"].get(addr, {}).get("name", alias)
            return (addr, name)
    
    # Name match (partial)
    matches = []
    for addr, c in contacts["contacts"].items():
        name = c.get("name", "").lower()
        if query_lower in name or query_lower in addr.lower():
            matches.append((addr, c.get("name", addr), c.get("last_contact", "")))
    
    # Sort by recency
    matches.sort(key=lambda x: x[2], reverse=True)
    
    if matches:
        return (matches[0][0], matches[0][1])
    
    # Role-based search
    for addr, c in contacts["contacts"].items():
        role = c.get("role", "").lower()
        if query_lower in role:
            return (addr, c.get("name", addr))
    
    return (None, f"未找到匹配'{query}'的联系人")


def list_contacts(role_filter=None, sort_by="recent"):
    """List contacts, optionally filtered."""
    contacts = load_contacts()
    result = []
    for addr, c in contacts["contacts"].items():
        if role_filter and role_filter not in c.get("role", ""):
            continue
        result.append(c)
    
    if sort_by == "recent":
        result.sort(key=lambda x: x.get("last_contact", ""), reverse=True)
    elif sort_by == "frequent":
        result.sort(key=lambda x: x.get("contact_count", 0), reverse=True)
    else:
        result.sort(key=lambda x: x.get("name", ""))
    
    return result


def set_contact_attr(email, **kwargs):
    """Set attributes on a contact."""
    contacts = load_contacts()
    addr = normalize_email(email)
    if addr not in contacts["contacts"]:
        contacts["contacts"][addr] = {"email": addr, "first_seen": datetime.now().isoformat()}
    
    for k, v in kwargs.items():
        contacts["contacts"][addr][k] = v
    
    # Update aliases
    if "name" in kwargs:
        old_aliases = [a for a, e in contacts["aliases"].items() if e == addr]
        for a in old_aliases:
            del contacts["aliases"][a]
        contacts["aliases"][kwargs["name"]] = addr
    
    save_contacts(contacts)


def get_reply_preferences(email):
    """Get greeting/signature preferences for a contact based on role/domain."""
    settings = load_settings()
    contacts = load_contacts()
    addr = normalize_email(email)
    contact = contacts["contacts"].get(addr, {})
    
    prefs = settings.get("reply_preferences", {})
    
    # Check role-based preference
    role = contact.get("role", "")
    if role in prefs:
        return prefs[role]
    
    # Check domain-based
    domain = addr.split("@")[-1] if "@" in addr else ""
    if domain.endswith(".edu.cn") or domain.endswith(".ac.cn"):
        return prefs.get("导师", prefs["default"])
    
    # International domains → English
    if domain in ("gmail.com", "outlook.com", "hotmail.com", "yahoo.com"):
        if "期刊" in prefs:
            return prefs["期刊"]
    
    return prefs.get("default", {"greeting": "{name}您好，\n", "signature": "祝好！\nwmwen"})


# Standalone test
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "list":
            for c in list_contacts():
                print(f"  {c['name']:15} {c['email']:35} x{c.get('contact_count',1)}")
        elif cmd == "resolve" and len(sys.argv) > 2:
            addr, name = resolve_recipient(sys.argv[2])
            print(f"  {name} <{addr}>" if addr else f"  ERROR: {name}")
        elif cmd == "learn" and len(sys.argv) > 2:
            name = learn_contact(sys.argv[2])
            print(f"  Learned: {name} <{sys.argv[2]}>")
    else:
        print("email_contacts.py [list|resolve <query>|learn <email>]")
