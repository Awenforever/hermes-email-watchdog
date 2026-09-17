#!/usr/bin/env python3
"""Synthetic, mailbox-free live Ollama probe for spam semantic behavior."""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import email_semantic_engine as engine

MODEL = os.environ.get("EMAIL_WATCHDOG_PROBE_MODEL", "qwen2.5:3b")
ENDPOINT = os.environ.get("EMAIL_WATCHDOG_PROBE_ENDPOINT", "http://127.0.0.1:11434")
OUT = Path(os.environ.get("EMAIL_WATCHDOG_PROBE_OUTPUT", "/results/live-qwen-probe.json"))

CASES = [
    {
        "name": "newton_spam_latest_issue",
        "email": {
            "id": "synthetic-newton", "msg_id": "synthetic-newton", "account": "SYNTHETIC",
            "subject": "[SPAM] Read the latest issue of Newton",
            "from_addr": "services-solutions@cellpress-cp.email.elsevier.com",
            "from_name": "Elisa De Ranieri",
            "body": "Read the latest issue of Newton. This issue covers research in cell processes, quantum communication and nanostructures. Comments from researchers are included. Manage preferences or unsubscribe.",
            "attachments": [], "has_attachment": False, "has_attachments": False,
        },
        "expect_category": "newsletter_marketing",
    },
    {
        "name": "spam_marked_manuscript_revision",
        "email": {
            "id": "synthetic-revision", "msg_id": "synthetic-revision", "account": "SYNTHETIC",
            "subject": "[SPAM] Major revision decision for manuscript",
            "from_addr": "editorial-office@journal.example",
            "from_name": "Editorial Office",
            "body": "Your manuscript requires major revision. Please submit the revised manuscript by 30 July 2026.",
            "attachments": [], "has_attachment": False, "has_attachments": False,
        },
        "expect_category": "paper_manuscript_feedback",
    },
    {
        "name": "spam_prefix_personal_collaborator",
        "email": {
            "id": "synthetic-collaborator", "msg_id": "synthetic-collaborator", "account": "SYNTHETIC",
            "subject": "[SPAM] Message from collaborator",
            "from_addr": "colleague@example.org",
            "from_name": "Research Collaborator",
            "body": "I reviewed your method and have several comments about the experimental design.",
            "attachments": [], "has_attachment": False, "has_attachments": False,
        },
        "expect_not_category": "newsletter_marketing",
    },
]

settings = {
    "enabled": True,
    "mode": "shadow",
    "provider": "ollama",
    "endpoint": ENDPOINT,
    "model": MODEL,
    "timeout_seconds": 180,
    "temperature": 0.1,
    "cache_by_message_hash": False,
    "num_predict_mode": "fixed",
    "num_predict": 1000,
    "num_predict_hard_cap": 1200,
}
rows = []
failed = False
for case in CASES:
    result = engine.analyze_email(
        case["email"],
        {"action": "needs_llm", "category": "unknown_needs_llm", "priority": "normal"},
        {"should_notify": True, "formatted_summary": ""},
        settings_override=settings,
    )
    decision = result.get("decision") or {}
    classification = decision.get("classification") or {}
    importance = decision.get("importance") or {}
    risk = decision.get("risk") or {}
    notification = decision.get("notification") or {}
    validated = classification.get("category") or ""
    ok = bool(result.get("ok")) and bool(result.get("llm_called")) and not result.get("fallback_used") and not result.get("timeout")
    if case.get("expect_category"):
        ok = ok and validated == case["expect_category"]
    if case.get("expect_not_category"):
        ok = ok and validated != case["expect_not_category"]
    row = {
        "name": case["name"],
        "ok": ok,
        "model": result.get("model"),
        "llm_called": result.get("llm_called"),
        "fallback_used": result.get("fallback_used"),
        "timeout": result.get("timeout"),
        "error_code": result.get("error_code"),
        "fallback_reason": result.get("fallback_reason"),
        "raw_category": result.get("raw_category"),
        "raw_importance": result.get("raw_importance"),
        "raw_risk_level": result.get("raw_risk_level"),
        "validated_category": validated,
        "validated_importance": importance.get("level"),
        "validated_risk": risk.get("level"),
        "summary": notification.get("summary"),
        "normalization_repairs": result.get("normalization_repairs") or [],
        "trace_signals": result.get("trace_signals") or {},
        "latency_ms": result.get("latency_ms"),
        "prompt_version": result.get("prompt_version"),
    }
    rows.append(row)
    failed = failed or not ok

newton = rows[0]
payload = {
    "status": "failed" if failed else "passed",
    "synthetic_only": True,
    "mailbox_access": False,
    "weixin_send": False,
    "model": MODEL,
    "endpoint_scope": "ollama_container_network_namespace",
    "qwen_understood_newton_raw": newton.get("raw_category") == "newsletter_marketing",
    "newton_raw_category": newton.get("raw_category"),
    "newton_validated_category": newton.get("validated_category"),
    "cases": rows,
}
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(payload, ensure_ascii=False, indent=2))
raise SystemExit(1 if failed else 0)
