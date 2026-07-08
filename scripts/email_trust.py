#!/usr/bin/env python3
"""
Email Trust Model — dynamic, portable, no hardcoded domains.
Auto-learns own_domains from user's email accounts and interaction history.
"""

import os, sys
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

try:
    import email_store
except ImportError:
    email_store = None

# ── Own Domain Detection ────────────────────────────────────────

def detect_own_domains(account_emails: list = None):
    """
    Auto-detect user's own email domains.
    Priority: 1) explicitly configured accounts  2) most frequent from_domain in sent mail
    """
    domains = set()
    
    if account_emails:
        for email in account_emails:
            if "@" in email:
                domains.add(email.split("@")[1])
    
    if email_store:
        try:
            conn = email_store._get_conn()
            # Check contacts marked as own_domain
            rows = conn.execute("SELECT email FROM contacts WHERE own_domain=1").fetchall()
            for r in rows:
                if "@" in r[0]:
                    domains.add(r[0].split("@")[1])
            # Also check account emails from messages
            rows = conn.execute(
                "SELECT from_domain, COUNT(*) as cnt FROM messages "
                "WHERE from_domain IS NOT NULL GROUP BY from_domain ORDER BY cnt DESC"
            ).fetchall()
            if rows and not domains:
                domains.add(rows[0][0])
        except:
            pass
    
    return sorted(domains)


# ── Trust Scoring ───────────────────────────────────────────────

def compute_trust(from_email: str, from_domain: str = None,
                  own_domains: list = None, contact_data: dict = None,
                  spf_pass: bool = None, dkim_pass: bool = None,
                  dmarc_pass: bool = None) -> dict:
    """
    Compute trust score and label for a sender.
    Returns {"score": float, "label": str, "reasons": list}.
    """
    score = 0.0
    reasons = []
    
    if not from_domain and "@" in from_email:
        from_domain = from_email.split("@")[1]
    
    own_domains = own_domains or []
    
    # ── Identity signals ──
    if spf_pass is True:
        score += 10
    elif spf_pass is False:
        score -= 20
        reasons.append("SPF failed")
    
    if dkim_pass is True:
        score += 15
    elif dkim_pass is False:
        score -= 15
        reasons.append("DKIM failed")
    
    if dmarc_pass is True:
        score += 20
    elif dmarc_pass is False:
        score -= 25
        reasons.append("DMARC failed")
    
    # ── Organization signals ──
    if from_domain in own_domains:
        score += 15
        reasons.append("same organization domain")
    
    # ── Educational / Government domain bonus ──
    if from_domain and (from_domain.endswith(".edu.cn") or from_domain.endswith(".ac.cn")
                        or from_domain.endswith(".gov.cn")):
        score += 5
        reasons.append("edu/gov domain")
    
    # ── Contact relationship signals ──
    if contact_data:
        if contact_data.get("user_replied"):
            score += 35
            reasons.append("user has replied before")
        
        if contact_data.get("user_sent"):
            score += 30
            reasons.append("user has sent to this contact")
        
        interaction_count = contact_data.get("interaction_count", 0)
        if interaction_count >= 10:
            score += 20
        elif interaction_count >= 5:
            score += 10
        elif interaction_count >= 3:
            score += 5
        
        if contact_data.get("trust_level") == "trusted":
            score += 40
    
    # ── Assign label ──
    if score >= 60:
        label = "trusted"
    elif score >= 30:
        label = "known"
    elif score >= 0:
        label = "unknown"
    elif score >= -30:
        label = "suspicious"
    else:
        label = "blocked"
    
    return {"score": round(score, 1), "label": label, "reasons": reasons}


# ── Contact Auto-Learning ───────────────────────────────────────

def learn_contact(from_email: str, from_name: str = None,
                  own_domains: list = None) -> dict:
    """Auto-learn or update a contact. Returns contact dict."""
    if not from_email:
        return None
    
    email = from_email.strip().lower()
    domain = email.split("@")[1] if "@" in email else ""
    name = (from_name or "").strip().strip('"\'')
    
    if email_store:
        existing = email_store.get_contact(email)
        if existing:
            data = {
                "interaction_count": existing.get("interaction_count", 0) + 1,
                "last_seen": datetime.now().isoformat(),
            }
            if name and name != existing.get("name"):
                data["name"] = name
            email_store.upsert_contact(email, data)
        else:
            email_store.upsert_contact(email, {
                "name": name or email.split("@")[0],
                "affiliation": _guess_affiliation(domain),
                "interaction_count": 1,
                "first_seen": datetime.now().isoformat(),
                "last_seen": datetime.now().isoformat(),
                "own_domain": 1 if domain in (own_domains or []) else 0,
            })
        return email_store.get_contact(email)
    
    return None


def mark_user_replied(email: str):
    """Mark that the user has replied to this contact."""
    if email_store:
        existing = email_store.get_contact(email)
        if existing:
            email_store.upsert_contact(email, {
                "user_replied": 1,
                "interaction_count": existing.get("interaction_count", 0) + 1,
                "last_seen": datetime.now().isoformat(),
            })


def mark_user_sent(email: str):
    """Mark that the user has sent to this contact."""
    if email_store:
        existing = email_store.get_contact(email)
        if existing:
            email_store.upsert_contact(email, {
                "user_sent": 1,
                "interaction_count": existing.get("interaction_count", 0) + 1,
                "last_seen": datetime.now().isoformat(),
            })
        else:
            email_store.upsert_contact(email, {
                "name": email.split("@")[0],
                "user_sent": 1,
                "interaction_count": 1,
                "first_seen": datetime.now().isoformat(),
                "last_seen": datetime.now().isoformat(),
            })


def _guess_affiliation(domain: str) -> str:
    known = {
        "agent.qq.com": "Agently Mail",
        "gmail.com": "Personal",
        "qq.com": "Personal",
        "163.com": "Personal",
        "outlook.com": "Personal",
    }
    return known.get(domain, domain)


# ── Quick Helpers ───────────────────────────────────────────────

def is_known_contact(email: str) -> bool:
    if email_store:
        c = email_store.get_contact(email)
        return c is not None and c.get("interaction_count", 0) > 0
    return False


def is_trusted(email: str) -> bool:
    if email_store:
        c = email_store.get_contact(email)
        return c is not None and c.get("trust_level") == "trusted"
    return False


def get_own_domains_cached() -> list:
    """Get own domains, using account emails as hint."""
    account_emails = [
        "wmwen@mail.ustc.edu.cn",
        "wmwen1999@gmail.com",
        "augenstern@agent.qq.com",
    ]
    return detect_own_domains(account_emails)