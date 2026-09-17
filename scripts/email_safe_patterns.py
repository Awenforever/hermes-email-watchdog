#!/usr/bin/env python3
"""
Safe pattern DSL for Hermes Email Watchdog decision-engine shadow.
No arbitrary production regex promotion here: candidates are only validated and recorded.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, Tuple

ALLOWED_TYPES = {
    "domain_match",
    "sender_contains",
    "subject_contains_any",
    "subject_contains_all",
    "body_contains_any",
    "body_contains_all",
    "safe_regex",
    "body_shape_match",
    "attachment_type_match",
}

ALLOWED_FIELDS = {"sender", "sender_domain", "subject", "body", "subject_body", "body_shape", "attachment"}


def _list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out = []
    for item in value[:20]:
        s = str(item or "").strip()
        if 1 <= len(s) <= 80:
            out.append(s)
    return out


def _lower_text(value: Any) -> str:
    return str(value or "").lower()


def _has_any(text: str, tokens: Iterable[str]) -> bool:
    text_l = _lower_text(text)
    return any(_lower_text(t) in text_l for t in tokens if t)


def _has_all(text: str, tokens: Iterable[str]) -> bool:
    toks = [t for t in tokens if t]
    if not toks:
        return False
    text_l = _lower_text(text)
    return all(_lower_text(t) in text_l for t in toks)


def validate_safe_regex(pattern: str) -> Tuple[bool, str]:
    if not isinstance(pattern, str) or not pattern:
        return False, "empty regex"
    if len(pattern) > 120:
        return False, "regex too long"
    forbidden = ["(?=", "(?!", "(?<=", "(?<!", "(?:.*){", ".*.*", "\\C"]
    if any(x in pattern for x in forbidden):
        return False, "unsafe construct"
    if re.search(r"(\([^)]*[+*][^)]*\))[+*{]", pattern):
        return False, "nested quantifier risk"
    try:
        re.compile(pattern, re.I)
    except re.error as exc:
        return False, f"invalid regex: {exc}"
    return True, "ok"


def validate_candidate(candidate: Dict[str, Any], category: str | None = None) -> Dict[str, Any]:
    c = dict(candidate or {})
    typ = str(c.get("type") or c.get("pattern_type") or "").strip()
    field = str(c.get("field") or "").strip() or "subject_body"
    if typ not in ALLOWED_TYPES:
        return {"ok": False, "error": "unsupported pattern type", "candidate": c}
    if field not in ALLOWED_FIELDS:
        return {"ok": False, "error": "unsupported field", "candidate": c}
    c["type"] = typ
    c["field"] = field
    if category and not c.get("category"):
        c["category"] = category
    if "tokens" in c:
        c["tokens"] = _list(c.get("tokens"))
    if "negative_tokens" in c:
        c["negative_tokens"] = _list(c.get("negative_tokens"))
    if typ == "safe_regex":
        ok, reason = validate_safe_regex(str(c.get("pattern") or ""))
        if not ok:
            return {"ok": False, "error": reason, "candidate": c}
    if typ in {"subject_contains_any", "subject_contains_all", "body_contains_any", "body_contains_all"} and not c.get("tokens"):
        return {"ok": False, "error": "missing tokens", "candidate": c}
    try:
        c["confidence"] = max(0.0, min(1.0, float(c.get("confidence", 0.0))))
    except Exception:
        c["confidence"] = 0.0
    return {"ok": True, "candidate": c}


def _field_text(features: Dict[str, Any], field: str) -> str:
    payload = features.get("llm_payload") or {}
    if field == "sender_domain":
        return features.get("sender_domain") or payload.get("from_domain") or ""
    if field == "sender":
        return " ".join([features.get("sender_domain") or "", payload.get("from_name") or ""])
    if field == "subject":
        return payload.get("subject") or features.get("subject_preview") or ""
    if field == "body":
        return payload.get("body_excerpt") or ""
    if field == "subject_body":
        return (payload.get("subject") or features.get("subject_preview") or "") + "\n" + (payload.get("body_excerpt") or "")
    return ""


def match_candidate(candidate: Dict[str, Any], features: Dict[str, Any]) -> bool:
    checked = validate_candidate(candidate)
    if not checked.get("ok"):
        return False
    c = checked["candidate"]
    neg = c.get("negative_tokens") or []
    text = _field_text(features, c.get("field") or "subject_body")
    if neg and _has_any(text, neg):
        return False
    typ = c["type"]
    if typ == "domain_match":
        domains = _list(c.get("domains") or c.get("tokens") or c.get("domain"))
        domain = _lower_text(features.get("sender_domain"))
        return any(domain == _lower_text(d).lstrip("@") or domain.endswith("." + _lower_text(d).lstrip("@")) for d in domains)
    if typ == "sender_contains":
        return _has_any(_field_text(features, "sender"), _list(c.get("tokens") or c.get("contains")))
    if typ == "subject_contains_any":
        return _has_any(_field_text(features, "subject"), c.get("tokens") or [])
    if typ == "subject_contains_all":
        return _has_all(_field_text(features, "subject"), c.get("tokens") or [])
    if typ == "body_contains_any":
        return _has_any(_field_text(features, "body"), c.get("tokens") or [])
    if typ == "body_contains_all":
        return _has_all(_field_text(features, "body"), c.get("tokens") or [])
    if typ == "safe_regex":
        return bool(re.search(str(c.get("pattern") or ""), text, re.I))
    if typ == "body_shape_match":
        shape = features.get("body_shape") or {}
        required = c.get("required") or {}
        if not isinstance(required, dict):
            return False
        for k, v in required.items():
            if shape.get(k) != v:
                return False
        return True
    if typ == "attachment_type_match":
        suffixes = set((features.get("attachment_profile") or {}).get("suffixes") or [])
        required = {s.lower().lstrip(".") for s in _list(c.get("suffixes") or c.get("tokens"))}
        return bool(suffixes & required)
    return False


def candidate_json(candidate: Dict[str, Any]) -> str:
    return json.dumps(candidate or {}, ensure_ascii=False, sort_keys=True, default=str)
