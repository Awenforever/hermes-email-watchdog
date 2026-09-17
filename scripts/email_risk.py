#!/usr/bin/env python3
"""
Email Risk Assessment — phishing detection, suspicious patterns, attachment/link risks.
No hardcoded institutional domains. All rules are based on behavioral signals.
"""

import re
from urllib.parse import urlparse

# ── High-risk attachment extensions ──
HIGH_RISK_EXTENSIONS = {".exe", ".scr", ".bat", ".cmd", ".js", ".vbs", ".ps1",
                        ".hta", ".msi", ".com", ".pif", ".reg", ".wsf", ".cpl"}
MACRO_EXTENSIONS = {".docm", ".xlsm", ".pptm", ".dotm"}

# ── Phishing keyword patterns ──
PHISHING_KEYWORDS = [
    "奖金发放", "年终奖", "工资条", "退税", "补贴", "补助",
    "人事部通知", "财务部通知", "社保", "公积金",
    "密码过期", "账号异常", "账户冻结", "重新激活",
    "转账", "汇款", "付款通知",
    "中奖", "领取奖励", "免费领取",
]

# ── Suspicious domain patterns (won't match legitimate .edu.cn etc) ──
SUSPICIOUS_TLD_PATTERNS = [r"\.(?:tk|ml|ga|cf|gq|xyz|top|club|work|date|loan|win)$"]


def compute_risk(mail_data: dict, trust_result: dict = None) -> dict:
    """
    Compute risk score and label for an email.
    mail_data should have: subject, from_email, from_domain, body (first 3000 chars),
                           has_attachment, attachment_filenames, has_links, links.
    Returns {"score": float, "label": str, "flags": list}.
    """
    score = 0.0
    flags = []
    
    from_email = (mail_data.get("from_email") or "").lower()
    from_domain = mail_data.get("from_domain") or ""
    if not from_domain and "@" in from_email:
        from_domain = from_email.split("@")[1]
    
    subject = (mail_data.get("subject") or "").lower()
    body = (mail_data.get("body") or "")[:3000].lower()
    full_text = f"{subject} {body}"
    
    trust_label = trust_result.get("label", "unknown") if trust_result else "unknown"
    
    # ── Content-based phishing signals ──
    for kw in PHISHING_KEYWORDS:
        if kw in full_text:
            flags.append(f"phishing_keyword:{kw}")
            score += 15
    
    # Money / account request patterns
    money_patterns = [
        r"(?:转账|汇款|付款|支付)\s*(?:到|至|给|给到)",
        r"(?:请|需要|务必)\s*(?:转账|汇款|付款)",
        r"(?:提供|输入|确认)\s*(?:密码|验证码|身份证|银行卡)",
    ]
    for pat in money_patterns:
        if re.search(pat, full_text):
            flags.append(f"money_pattern:{pat[:30]}")
            score += 20
    
    # Urgency / pressure
    urgency_patterns = [
        r"(?:立即|马上|立刻|尽快|速)\s*(?:处理|回复|确认|操作|登录)",
        r"(?:24小时|24h|24小时)\s*(?:内|之内)",
    ]
    for pat in urgency_patterns:
        if re.search(pat, full_text):
            flags.append(f"urgency:{pat[:30]}")
            score += 5
    
    # ── Domain impersonation ──
    # Check if sender domain looks like it's impersonating a known org
    # Only flag if the org keyword appears in a fake position
    # e.g., "ustc-admin.com" → suspicious; "ustc.edu.cn" → legitimate
    known_org_keywords = ["ustc", "tsinghua", "pku", "coursera", "scholar", "research",
                          "university", "college", "institute", "hospital", "bank", "gov"]
    for org_kw in known_org_keywords:
        if org_kw in from_domain:
            # Skip: exact match or dot-separated legitimate (ustc.edu.cn, mail.ustc.edu.cn)
            if from_domain == f"{org_kw}.edu.cn" or from_domain.endswith(f".{org_kw}.edu.cn"):
                continue
            if from_domain == f"{org_kw}.com" or from_domain.endswith(f".{org_kw}.com"):
                continue
            if from_domain == f"{org_kw}.org" or from_domain.endswith(f".{org_kw}.org"):
                continue
            if from_domain.endswith(f".{org_kw}.ac.cn") or from_domain == f"{org_kw}.ac.cn":
                continue
            
            # Check if it looks like a knockoff: org_kw followed by dash/dot then something
            if re.search(rf"\b{org_kw}[.-][a-z]", from_domain) and not re.search(rf"(?:\.|^){org_kw}\.(?:edu|ac|gov|org|com)", from_domain):
                flags.append(f"domain_impersonation:{org_kw}")
                score += 25
                break
    
    # ── Suspicious TLD ──
    for pat in SUSPICIOUS_TLD_PATTERNS:
        if re.search(pat, from_domain):
            flags.append(f"suspicious_tld:{from_domain}")
            score += 10
            break
    
    # ── Attachment risks ──
    attachments = mail_data.get("attachments") or []
    has_attachment = mail_data.get("has_attachment") or bool(attachments)
    
    if has_attachment:
        for att in (attachments if isinstance(attachments, list) else []):
            fname = (att.get("filename") or "").lower()
            ext = "." + fname.rsplit(".", 1)[-1] if "." in fname else ""
            
            if ext in HIGH_RISK_EXTENSIONS:
                flags.append(f"high_risk_attachment:{fname}")
                score += 40
            elif ext in MACRO_EXTENSIONS:
                flags.append(f"macro_attachment:{fname}")
                score += 20
    
    # ── Link risks ──
    links = mail_data.get("links") or []
    for link in (links if isinstance(links, list) else []):
        url = link.get("url") or link if isinstance(link, str) else ""
        if url:
            parsed = urlparse(url)
            # Short URL services
            short_domains = {"bit.ly", "t.co", "tinyurl.com", "ow.ly", "is.gd", "buff.ly",
                             "goo.gl", "shorte.st", "bc.vc", "adf.ly"}
            if parsed.netloc.lower() in short_domains:
                flags.append(f"short_url:{url[:60]}")
                score += 10
            # Display text mismatch
            display = link.get("display_text", "") if isinstance(link, dict) else ""
            if display and parsed.netloc.lower() not in display.lower():
                flags.append(f"url_display_mismatch:{url[:60]}")
                score += 15
    
    # ── Trust-adjusted risk ──
    if trust_label == "trusted":
        score = max(0, score - 30)
    elif trust_label == "suspicious":
        score += 15
    elif trust_label == "blocked":
        score += 40
    
    # ── Assign label ──
    if score >= 50:
        label = "critical"
    elif score >= 25:
        label = "high"
    elif score >= 10:
        label = "medium"
    else:
        label = "low"
    
    return {"score": round(score, 1), "label": label, "flags": flags}


def is_suspicious_for_download(mail_data: dict, trust_result: dict = None, risk_result: dict = None) -> bool:
    """Should we skip auto-download for safety?"""
    if risk_result and risk_result.get("label") in ("critical", "high"):
        return True
    if trust_result and trust_result.get("label") in ("suspicious", "blocked"):
        return True
    
    attachments = mail_data.get("attachments") or []
    for att in (attachments if isinstance(attachments, list) else []):
        fname = (att.get("filename") or "").lower()
        ext = "." + fname.rsplit(".", 1)[-1] if "." in fname else ""
        if ext in HIGH_RISK_EXTENSIONS:
            return True
    
    return False