#!/usr/bin/env python3
"""Read-only replay of real cached emails through Hermes' USTC provider."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import email_notification_renderer
import email_semantic_engine


def load_email(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    value.setdefault("id", value.get("msg_id") or path.stem)
    value.setdefault("from_email", value.get("from_addr") or "")
    value.setdefault("date_sent", value.get("cached_at") or "")
    return value


def main() -> int:
    paths = [Path(arg) for arg in sys.argv[1:]]
    if not paths:
        raise SystemExit("usage: live_qwen36_semantic_replay.py EMAIL_JSON...")
    settings = {
        "enabled": True,
        "mode": "shadow",
        "provider": "hermes_openai",
        "provider_name": "USTC",
        "endpoint": "",
        "model": "qwen3.6-chat",
        "timeout_seconds": 120,
        "temperature": 0.0,
        "max_body_chars": 16000,
        "num_predict_mode": "adaptive",
        "num_predict_simple": 1400,
        "num_predict_standard": 2400,
        "num_predict_complex": 4000,
        "num_predict_hard_cap": 4096,
    }
    failures = []
    for path in paths:
        email = load_email(path)
        result = email_semantic_engine.analyze_email(email, {}, {}, settings_override=settings)
        decision = result["decision"]
        rendered = email_notification_renderer.render_notification(
            email, decision, {}, {"name": email.get("account") or ""},
            settings_override={"mode": "production", "renderer": "adaptive_v1f"},
        )
        category = str((decision.get("classification") or {}).get("category") or "")
        action = decision.get("action") or {}
        summary = str((decision.get("notification") or {}).get("summary") or "")
        record = {
            "id": email.get("id"),
            "subject": email.get("subject"),
            "model": result.get("model"),
            "fallback_used": result.get("fallback_used"),
            "error_code": result.get("error_code"),
            "raw_errors": result.get("raw_errors"),
            "raw_category": result.get("raw_category"),
            "raw_importance": result.get("raw_importance"),
            "raw_summary": result.get("raw_summary"),
            "normalization_repairs": result.get("normalization_repairs"),
            "model_core_keys": result.get("model_core_keys"),
            "category": category,
            "importance": (decision.get("importance") or {}).get("level"),
            "action_required": bool(action.get("required")),
            "action": action,
            "summary": summary,
            "rendered": rendered.get("text"),
        }
        print(json.dumps(record, ensure_ascii=False, indent=2))
        subject = str(email.get("subject") or "")
        if "ORCID" in subject:
            if category == "newsletter_marketing" or not action.get("required"):
                failures.append("ORCID action/category")
            if not any(word in (summary + json.dumps(action, ensure_ascii=False)) for word in ("Crossref", "ORCID", "授权", "自动更新")):
                failures.append("ORCID key action missing")
        if "党费" in subject:
            if not action.get("required"):
                failures.append("party dues action missing")
            if "缴" not in (summary + json.dumps(action, ensure_ascii=False)):
                failures.append("party dues payment missing")
    if failures:
        print(json.dumps({"ok": False, "failures": failures}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, "count": len(paths)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
