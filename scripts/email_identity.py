#!/usr/bin/env python3
"""Canonical, lossless sender identity handling for Email Watchdog."""

from __future__ import annotations

import re
from typing import Any, Mapping


def _clean(value: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\x00", " ")).strip()[:limit]


def address_from(value: Any) -> str:
    if isinstance(value, Mapping):
        value = (
            value.get("addr")
            or value.get("email")
            or value.get("address")
            or value.get("from_addr")
            or value.get("from_email")
            or ""
        )
    return _clean(value, 320).strip("<>")


def name_from(value: Any) -> str:
    if isinstance(value, Mapping):
        value = value.get("name") or value.get("display_name") or value.get("label") or ""
    return _clean(value, 160).strip('"\' ')


def sender_address(email: Mapping[str, Any]) -> str:
    for key in ("from_addr", "from_email", "from_address", "sender_email", "sender"):
        address = address_from(email.get(key))
        if address:
            return address
    return ""


def sender_name(email: Mapping[str, Any]) -> str:
    for key in ("from_name", "sender_name"):
        name = name_from(email.get(key))
        if name:
            return name
    sender = email.get("sender")
    return name_from(sender) if isinstance(sender, Mapping) else ""


def canonical_sender(email: Mapping[str, Any], unknown: str = "未知发件人") -> str:
    """Never discard an available address merely because its local part matches the name."""
    name = sender_name(email)
    address = sender_address(email)
    if address:
        if name and name.casefold() != address.casefold():
            return f"{name} <{address}>"
        return address
    return name or unknown
