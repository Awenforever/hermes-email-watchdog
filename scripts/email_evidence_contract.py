#!/usr/bin/env python3
"""Evidence-preserving notification contract.

This module never tries to understand the open-ended meaning of an email.  It
only inventories source evidence, resolves model-selected evidence IDs, and
renders a transparent lossless card when every model route is unavailable.
"""
from __future__ import annotations

import re
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Mapping
from urllib.parse import unquote, urlparse, urlunparse

MARKER = "EMAIL_WATCHDOG_EVIDENCE_CONTRACT_V1"


def _text(value: Any, limit: int = 4000) -> str:
    return str(value or "").replace("\x00", "").strip()[:limit]


def _clean_url(value: Any) -> str:
    url = _text(value, 5000).replace("\u200b", "").replace("\ufeff", "")
    url = url.rstrip(".,;:!?，。；：！？]>}")
    try:
        parsed = urlparse(url)
    except Exception:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, parsed.query, ""))


def _display_policy(url: str, label: str = "") -> str:
    parsed = urlparse(url)
    decoded = unquote(url).casefold()
    tracking_wrapper = bool(
        len(url) > 1200
        or re.search(r"(?i)(?:^|\.)(?:click|track|tracking|url\d+)\.", parsed.netloc)
        or re.search(r"(?i)/(?:ls/)?click(?:/|\?|$)", parsed.path)
    )
    mail_chrome_pattern = (
        r"(?i)(?:unsubscribe|opt[-_ ]?out|manage[_ -]?preferences?|"
        r"social[_ -]?share|share[_ -]?(?:email|link|post)|forward[_ -]?to|"
        r"email[_ -]?library[_ -]?add|(?:^|[?&])(?:action|op|operation)="
        r"[^&]*(?:add|save|share|subscribe|follow))"
    )
    mail_chrome = bool(
        re.search(
            mail_chrome_pattern, decoded,
        )
        or re.search(
            r"(?i)^(?:unsubscribe|opt out|manage (?:email )?preferences?|"
            r"退订|取消订阅|管理邮件偏好|停止接收)",
            label.strip(),
        )
    )
    if tracking_wrapper:
        return "tracking_wrapper"
    if mail_chrome:
        return "mail_chrome_action"
    return "eligible_destination"


def link_inventory(email: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Return stable IDs for source URLs without assigning semantic intent."""
    output: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in list(email.get("links") or [])[:50]:
        if isinstance(item, Mapping):
            raw_url = item.get("url")
            label = " ".join(_text(item.get("display_text"), 240).split())
        else:
            raw_url, label = item, ""
        url = _clean_url(raw_url)
        if not url or url in seen:
            continue
        seen.add(url)
        parsed = urlparse(url)
        policy = _display_policy(url, label)
        output.append({
            "id": f"link_{len(output)}",
            "label": label or f"打开 {parsed.netloc}",
            "url": url,
            "domain": parsed.netloc.casefold(),
            "display_safe": policy == "eligible_destination",
            "display_policy": policy,
        })
        if len(output) >= 24:
            break
    return output


def prompt_inventory(email: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Expose IDs and human context to the model, never authority to open URLs."""
    return [
        {
            "id": item["id"],
            "label": item["label"],
            "domain": item["domain"],
            "display_safe": item["display_safe"],
        }
        for item in link_inventory(email)
    ]


def resolve_link_ids(
    email: Mapping[str, Any], link_ids: Any, *, safe_only: bool = True
) -> List[Dict[str, Any]]:
    inventory = link_inventory(email)
    by_id = {item["id"]: item for item in inventory}
    output: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for value in list(link_ids or [])[:24]:
        identity = _text(value, 80)
        item = by_id.get(identity)
        if not item or identity in seen or (safe_only and not item["display_safe"]):
            continue
        seen.add(identity)
        output.append(item)
    return output


def validate_link_ids(email: Mapping[str, Any], link_ids: Any) -> List[str]:
    inventory = {item["id"]: item for item in link_inventory(email)}
    errors: List[str] = []
    seen: set[str] = set()
    if not isinstance(link_ids, list):
        return ["action.link_ids must be an array"]
    for raw in link_ids[:24]:
        identity = _text(raw, 80)
        if identity in seen:
            continue
        seen.add(identity)
        item = inventory.get(identity)
        if item is None:
            errors.append(f"unknown source link id: {identity}")
        elif not item["display_safe"]:
            errors.append(f"source link is not display-safe: {identity}")
    return errors


def safe_source_links(email: Mapping[str, Any]) -> List[Dict[str, Any]]:
    return [item for item in link_inventory(email) if item["display_safe"]]


def attachment_inventory(
    email: Mapping[str, Any], delivered_attachments: Any = None
) -> List[Dict[str, str]]:
    """Inventory attachment evidence without deciding whether it is useful."""
    source = list(delivered_attachments or []) or list(email.get("attachments") or [])
    output: List[Dict[str, str]] = []
    seen: set[str] = set()
    for item in source[:20]:
        if isinstance(item, Mapping):
            name = _text(item.get("filename") or item.get("name"), 240)
            status = _text(item.get("download_status") or item.get("status"), 40)
            path = _text(item.get("path") or item.get("local_path") or item.get("saved_path"), 1000)
        else:
            name, status, path = _text(item, 240), "", ""
        identity = name.casefold()
        if not name or identity in seen:
            continue
        seen.add(identity)
        output.append({"name": name, "status": status, "path": path})
    return output


def has_publishable_evidence(
    email: Mapping[str, Any], delivered_attachments: Any = None
) -> bool:
    """Whether final publication must preserve source artifacts explicitly."""
    return bool(safe_source_links(email) or attachment_inventory(email, delivered_attachments))


def render_evidence_complete_draft(
    email: Mapping[str, Any], draft: str, *, reason: str = "",
    delivered_attachments: Any = None,
) -> Dict[str, Any]:
    """Preserve a grounded semantic draft while closing its artifact inventory.

    This is used only when the final editorial model chain is unavailable.  It
    does not infer which artifact matters: every display-safe source link and
    every attachment is retained, so a model outage cannot turn actionable
    prose into an unusable notification.
    """
    lines = [str(draft or "").strip()]
    links = safe_source_links(email)
    present = str(draft or "")
    missing_links = [item for item in links if str(item["url"]) not in present]
    if missing_links:
        lines.extend(["", "**邮件中的链接**"])
        for item in missing_links:
            label = str(item["label"]).replace("[", "").replace("]", "")
            url = str(item["url"]).replace(" ", "%20").replace("(", "%28").replace(")", "%29")
            lines.append(f"- [{label}]({url})")

    attachments = attachment_inventory(email, delivered_attachments)
    missing_attachments = [item for item in attachments if item["name"] not in present]
    if missing_attachments:
        lines.extend(["", "**附件**"])
        for item in missing_attachments:
            name = item["name"].replace("*", "")
            suffix = " · 已附上" if item["status"] == "downloaded" else " · 请在邮箱查看"
            lines.append(f"- **{name}**{suffix}")

    lines.extend(["", "> 编辑审校暂不可用；为避免信息丢失，已保全邮件中的安全链接与附件。"])
    return {
        "ok": True,
        "marker": MARKER,
        "renderer_version": "evidence_complete_draft_v1",
        "text": "\n".join(lines).strip(),
        "blocks": ["semantic_draft", "source_links", "source_attachments"],
        "degraded": True,
        "degraded_reason": _text(reason, 400),
        "source_link_count": len(links),
        "source_attachment_count": len(attachments),
    }


def _sender(email: Mapping[str, Any]) -> str:
    name = _text(email.get("from_name") or email.get("sender_name"), 160).strip('" ')
    address = _text(email.get("from_addr") or email.get("from_email") or email.get("sender"), 240)
    if name and address and name.casefold() != address.casefold():
        return f"{name} <{address}>"
    return address or name or "未知发件人"


def _received(email: Mapping[str, Any]) -> str:
    raw = _text(email.get("date_received") or email.get("received_at") or email.get("date"), 160)
    if not raw:
        return ""
    try:
        return parsedate_to_datetime(raw).astimezone().strftime("%Y-%m-%d %H:%M")
    except Exception:
        return raw[:32]


def _excerpt(email: Mapping[str, Any]) -> str:
    body = _text(email.get("body") or email.get("body_plain") or email.get("text"), 12000)
    kept: List[str] = []
    for raw in body.splitlines():
        line = " ".join(raw.split()).strip()
        if not line or re.fullmatch(r"[-_=*]{5,}", line):
            continue
        if re.fullmatch(r"https?://\S+", line):
            continue
        if re.search(r"(?i)unsubscribe|manage preferences|privacy policy", line):
            continue
        kept.append(line)
        if sum(len(item) for item in kept) >= 900:
            break
    text = " ".join(kept).strip()
    return text[:900] + ("…" if len(text) > 900 else "")


def render_lossless_fallback(
    email: Mapping[str, Any], account: Mapping[str, Any] | None = None, *,
    reason: str = "", delivered_attachments: Any = None,
) -> Dict[str, Any]:
    """Render only source evidence when no semantic model route is usable."""
    account = account or {}
    label = _text(account.get("label") or account.get("name") or email.get("account"), 80) or "Email"
    subject = _text(email.get("subject"), 400).replace("`", "′") or "无主题"
    sender = _sender(email).replace("`", "′")
    lines = [
        f"### 📬 新邮件｜{label}",
        "",
        "`智能分析暂不可用` · `原始信息保全`",
        "",
    ]
    received = _received(email)
    if received:
        lines.extend([f"**收到** `{received.replace('`', '′')}`", ""])
    lines.extend([f"**发件人** `{sender}`", "", f"**主题** `{subject}`"])
    excerpt = _excerpt(email)
    if excerpt:
        lines.extend(["", "**原文摘录**", f"> {excerpt}"])
    links = safe_source_links(email)
    if links:
        lines.extend(["", "**邮件中的链接**"])
        for item in links:
            safe_label = str(item["label"]).replace("[", "").replace("]", "")
            url = str(item["url"]).replace(" ", "%20").replace("(", "%28").replace(")", "%29")
            lines.append(f"- [{safe_label}]({url})")
    attachments = []
    for item in attachment_inventory(email, delivered_attachments):
        suffix = " · 已附上" if item["status"] == "downloaded" else " · 请在邮箱查看"
        attachments.append((item["name"].replace("*", ""), suffix))
    if attachments:
        lines.extend(["", "**附件**"] + [f"- **{name}**{suffix}" for name, suffix in attachments])
    lines.extend(["", "> 模型暂时不可用，本卡片未推断操作、截止时间或重要性；请以原邮件为准。"])
    return {
        "ok": True,
        "marker": MARKER,
        "renderer_version": "evidence_lossless_v1",
        "text": "\n".join(lines).strip(),
        "blocks": ["identity", "source_excerpt", "source_links", "source_attachments"],
        "degraded": True,
        "degraded_reason": _text(reason, 400),
        "source_link_count": len(links),
    }
