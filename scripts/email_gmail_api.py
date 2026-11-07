#!/usr/bin/env python3
"""Gmail REST API mailbox provider.

Gmail's IMAP interface can expose a delivered message minutes late (measured
309s on 2026-11-07), while the REST API is not subject to that indexing delay.
This module is the optional non-IMAP provider: it returns the same envelope and
message shapes the rest of the watchdog already consumes, so the analysis,
delivery and dedupe paths stay untouched.

Credentials are per-install and never shipped: an OAuth client JSON plus a
refresh token, both kept in the plugin state directory with 0600.
"""
from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
_METADATA_HEADERS = ["From", "To", "Subject", "Date"]


def _state_root() -> Path:
    raw = os.environ.get("HERMES_EMAIL_WATCHDOG_STATE_ROOT") or ""
    if raw:
        return Path(os.path.expanduser(os.path.expandvars(raw)))
    herm = os.environ.get("HERMES_HOME") or "/opt/data"
    return Path(herm) / "plugin-data" / "hermes-email-watchdog"


def credentials_dir() -> Path:
    return _state_root() / "gmail-api"


def client_secret_path() -> Path:
    return Path(os.environ.get("EMAIL_GMAIL_CLIENT_SECRET") or credentials_dir() / "client_secret.json")


def token_path() -> Path:
    return Path(os.environ.get("EMAIL_GMAIL_TOKEN") or credentials_dir() / "token.json")


def is_configured() -> bool:
    return client_secret_path().is_file() and token_path().is_file()


def apply_proxy_env(proxy: Optional[str] = None) -> None:
    """googleapis.com is unreachable directly from the NAS; route through the
    configured proxy exactly like the other egress paths do."""
    value = str(proxy or os.environ.get("EMAIL_GMAIL_API_PROXY") or "").strip()
    if not value:
        return
    for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        os.environ.setdefault(key, value)


def build_service(proxy: Optional[str] = None):
    """Return an authorized Gmail v1 service, or raise RuntimeError."""
    if not is_configured():
        raise RuntimeError("gmail api not configured: missing client_secret.json or token.json")
    apply_proxy_env(proxy)
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_file(str(token_path()), SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            _write_private(token_path(), creds.to_json())
        else:
            raise RuntimeError("gmail api credentials invalid; re-run gmail_oauth_setup")
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _header(message: Dict[str, Any], name: str) -> str:
    for item in ((message.get("payload") or {}).get("headers") or []):
        if str(item.get("name") or "").lower() == name.lower():
            return str(item.get("value") or "")
    return ""


def _split_address(raw: str) -> Dict[str, str]:
    text = str(raw or "").strip()
    if "<" in text and text.endswith(">"):
        name, _, addr = text.partition("<")
        return {"name": name.strip().strip('"'), "addr": addr.rstrip(">").strip()}
    return {"name": "", "addr": text}


def _walk_parts(part: Dict[str, Any]):
    yield part
    for child in (part.get("parts") or []):
        if isinstance(child, dict):
            yield from _walk_parts(child)


def _decode(data: str) -> str:
    try:
        return base64.urlsafe_b64decode(data.encode("utf-8")).decode("utf-8", errors="replace")
    except Exception:
        return ""


def extract_body(message: Dict[str, Any]) -> str:
    payload = message.get("payload") or {}
    plain = html = ""
    for part in _walk_parts(payload):
        mime = str(part.get("mimeType") or "")
        body = part.get("body") or {}
        data = body.get("data")
        if not data:
            continue
        if mime == "text/plain" and not plain:
            plain = _decode(data)
        elif mime == "text/html" and not html:
            html = _decode(data)
    if plain:
        return plain
    if html:
        import re
        text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
        text = re.sub(r"(?i)<br\s*/?>", "\n", text)
        text = re.sub(r"(?i)</p\s*>", "\n", text)
        text = re.sub(r"<[^>]+>", " ", text)
        import html as html_mod
        return html_mod.unescape(text).strip()
    return ""


def _attachments(message: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for part in _walk_parts(message.get("payload") or {}):
        filename = str(part.get("filename") or "").strip()
        if not filename:
            continue
        out.append({
            "filename": filename,
            "size_bytes": int((part.get("body") or {}).get("size") or 0),
            "attachment_id": str((part.get("body") or {}).get("attachmentId") or ""),
            "mime_type": str(part.get("mimeType") or ""),
        })
    return out


def envelope_from_message(message: Dict[str, Any]) -> Dict[str, Any]:
    """Map a Gmail message (metadata or full) to the himalaya envelope shape."""
    attachments = _attachments(message)
    return {
        "id": str(message.get("id") or ""),
        "message_id": str(message.get("id") or ""),
        "thread_id": str(message.get("threadId") or ""),
        "subject": _header(message, "Subject"),
        "from": _split_address(_header(message, "From")),
        "to": _split_address(_header(message, "To")),
        "date": _header(message, "Date"),
        "internal_date": str(message.get("internalDate") or ""),
        "has_attachment": bool(attachments),
        "attachments": attachments,
        "snippet": str(message.get("snippet") or ""),
    }


def list_messages(service, query: str = "", max_results: int = 5) -> List[Dict[str, Any]]:
    """Newest-first envelopes, metadata only (no body fetch)."""
    listing = service.users().messages().list(
        userId="me", q=query, maxResults=int(max_results)
    ).execute() or {}
    ids = [str(item.get("id")) for item in (listing.get("messages") or []) if item.get("id")]
    envelopes = []
    for mid in ids:
        meta = service.users().messages().get(
            userId="me", id=mid, format="metadata", metadataHeaders=_METADATA_HEADERS
        ).execute() or {}
        envelopes.append(envelope_from_message(meta))
    return envelopes


def read_message(service, msg_id: str) -> Optional[Dict[str, Any]]:
    """Full message in the shape read_himalaya returns."""
    try:
        message = service.users().messages().get(
            userId="me", id=str(msg_id), format="full"
        ).execute()
    except Exception:
        return None
    if not message:
        return None
    envelope = envelope_from_message(message)
    envelope["text"] = extract_body(message)
    envelope["body"] = envelope["text"]
    internal = str(message.get("internalDate") or "")
    if internal.isdigit():
        envelope["date_received"] = datetime.fromtimestamp(
            int(internal) / 1000, tz=timezone.utc
        ).isoformat()
    return envelope
