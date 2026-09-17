#!/usr/bin/env python3
"""
Hermes Email Watchdog decision engine shadow v1.
Shadow-only: observes delivery boundary, records decisions, optionally calls local Ollama.
It must never change notification text, mailbox state, seen state, or original Watchdog LLM config.
"""
from __future__ import annotations

# EMAIL_WATCHDOG_SEMANTIC_CONFLICT_POLICY_V1
# EMAIL_WATCHDOG_CANDIDATE_LIFECYCLE_SHADOW_V1

import hashlib
import json
import os
import random
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

try:
    import email_feature_extractor
    import email_llm_decision
    import email_safe_patterns
except Exception:  # Keep import-time safe for production.
    email_feature_extractor = None
    email_llm_decision = None
    email_safe_patterns = None

try:
    import email_candidate_lifecycle
except Exception:  # Candidate lifecycle must never break delivery observation.
    email_candidate_lifecycle = None

CONFIG_PATH = Path(os.environ.get("EMAIL_DECISION_ENGINE_CONFIG", "/opt/data/.hermes-home/.hermes/email_decision_engine_config.json"))
LEARNING_ROOT = Path(os.environ.get("EMAIL_LEARNING_ROOT", "/opt/data/.hermes-home/.hermes/email_learning"))
SHADOW_JSONL = LEARNING_ROOT / "decision_engine_shadow.jsonl"
LLM_JSONL = LEARNING_ROOT / "llm_observations.jsonl"
CANDIDATE_JSONL = LEARNING_ROOT / "pattern_candidates.jsonl"
DB_PATH = Path(os.environ.get("EMAIL_LEARNING_DB", str(LEARNING_ROOT / "email_learning.sqlite")))

MARKER = "EMAIL_WATCHDOG_DECISION_ENGINE_SHADOW_V1"
_RUN_LLM_CALLS = 0

CANONICAL_CATEGORIES = {
    "verification_code",
    "account_security",
    "account_status_notice",
    "invoice_receipt",
    "school_notice",
    "health_check_notice",
    "meeting_event",
    "task_deadline",
    "paper_manuscript_feedback",
    "academic_opportunity_call",
    "academic_report_digest",
    "academic_alert_digest",
    "research_feedback_thread",
    "data_download_order_notice",
    "system_automation_notice",
    "newsletter_marketing",
    "delivery_logistics",
    "personal_or_general",
    "unknown_needs_llm",
}

DEFAULT_CONFIG = {
    "enabled": True,
    "mode": "shadow",
    "version": 1,
    "llm": {
        "enabled": True,
        "provider": "ollama",
        "url": "http://127.0.0.1:11434",
        "model": "qwen2.5:3b",
        "timeout_seconds": 20,
        "strict_json": True,
        "temperature": 0.1,
        "num_predict": 900
    },
    "policy": {
        "cold_start_llm_until_decisions": 100,
        "high_confidence_skip_threshold": 0.86,
        "low_confidence_llm_threshold": 0.55,
        "audit_sample_rate": 0.05,
        "max_llm_calls_per_run": 5
    },
    "safety": {
        "mailbox_write_ops": False,
        "send_email": False,
        "mark_read": False,
        "delete_or_move": False,
        "change_notification_text": False,
        "change_original_watchdog_llm_config": False
    }
}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)


def _hash(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8", "replace")).hexdigest()


def _load_config() -> Dict[str, Any]:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        if CONFIG_PATH.exists():
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            _deep_update(cfg, data)
    except Exception:
        pass
    return cfg


def _deep_update(base: Dict[str, Any], patch: Dict[str, Any]) -> None:
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_update(base[k], v)
        else:
            base[k] = v


def ensure_config() -> Dict[str, Any]:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return _load_config()


def _safe_append_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(_json(obj) + "\n")


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _decision_count() -> int:
    try:
        conn = _connect()
        row = conn.execute("SELECT COUNT(*) AS n FROM message_decisions").fetchone()
        conn.close()
        return int(row["n"] if row else 0)
    except Exception:
        return 0


def _contains_any(text: str, words: List[str]) -> bool:
    t = (text or "").lower()
    return any(w.lower() in t for w in words)


def _contains_all(text: str, words: List[str]) -> bool:
    t = (text or "").lower()
    return all(w.lower() in t for w in words)


def _rule(category: str, confidence: float, reason: str, **kw: Any) -> Dict[str, Any]:
    out = {
        "category": category if category in CANONICAL_CATEGORIES else "unknown_needs_llm",
        "confidence": max(0.0, min(1.0, float(confidence))),
        "reason": reason,
        "decision_source": "base_rules",
        "conflict_detected": False,
        "negative_context_hit": False,
        "matched_signals": [],
    }
    out.update(kw)
    return out


def classify_base(features: Dict[str, Any], rule_result: Dict[str, Any] | None = None, analysis: Dict[str, Any] | None = None) -> Dict[str, Any]:
    payload = features.get("llm_payload") or {}
    subject = payload.get("subject") or features.get("subject_preview") or ""
    body = payload.get("body_excerpt") or ""
    text = f"{subject}\n{body}"
    domain = features.get("sender_domain") or ""
    tokens = set(features.get("tokens") or [])
    codes = features.get("code_candidates") or []
    shape = features.get("body_shape") or {}

    # Negative contexts first.
    if _contains_any(text, ["反诈", "不要泄露", "安全教育", "诈骗", "防骗"]) and _contains_any(text, ["验证码", "verification code", "code"]):
        return _rule("unknown_needs_llm", 0.32, "verification keyword with anti-fraud/security-education negative context", negative_context_hit=True, matched_signals=["negative_verification_context"])

    if codes and _contains_any(text, ["验证码", "verification code", "login code", "security code", "一次性", "动态码", "校验码", "登录"]):
        return _rule("verification_code", 0.90, "code candidate with login/security context", matched_signals=["code_candidate", "verification_context"])

    if _contains_any(text, ["异常登录", "password reset", "reset your password", "security alert", "unusual sign-in", "账户安全", "密码重置"]):
        return _rule("account_security", 0.84, "account security keywords", matched_signals=["security_keywords"])

    if _contains_any(text, ["账户过期", "账号过期", "account expired", "account status", "账号状态", "注册成功", "subscription expired"]):
        return _rule("account_status_notice", 0.78, "account status keywords", matched_signals=["account_status_keywords"])

    if _contains_any(text, ["电子发票", "发票", "invoice", "receipt", "账单", "付款", "payment", "开票", "金额"]) and not _contains_any(text, ["call for papers", "会议征稿"]):
        return _rule("invoice_receipt", 0.82, "invoice/receipt keywords", matched_signals=["invoice_keywords"])

    if _contains_any(text, ["健康打卡", "健康填报", "每日健康", "health check"]):
        return _rule("health_check_notice", 0.86, "health check keywords", matched_signals=["health_check_keywords"])

    if domain.endswith(".edu.cn") and _contains_any(text, ["通知", "学院", "学校", "教务", "研究生院", "ustc", "中国科学技术大学"]):
        return _rule("school_notice", 0.78, "edu.cn sender with school notice keywords", matched_signals=["edu_domain", "school_keywords"])

    if _contains_any(text, ["组会", "会议", "seminar", "webinar", "讲座", "答辩", "meeting", "conference"]) and _contains_any(text, ["时间", "地点", "zoom", "腾讯会议", "date", "time", "room"]):
        return _rule("meeting_event", 0.76, "meeting/event keywords", matched_signals=["meeting_keywords"])

    if shape.get("deadline_hint") or _contains_any(text, ["deadline", "due by", "截止", "请于", "不晚于", "提交", "确认", "报名"]):
        # Let more specific paper/event/data rules override below only if they appear earlier. Here broad task.
        broad = _rule("task_deadline", 0.66, "deadline/action hint", matched_signals=["deadline_hint"])
    else:
        broad = None

    if _contains_any(text, ["major revision", "minor revision", "reviewer comments", "decision letter", "manuscript", "审稿意见", "返修", "修回", "编辑决定"]):
        if _contains_any(text, ["call for papers", "征稿", "best poster", "submit abstract", "special issue invitation"]):
            return _rule("academic_opportunity_call", 0.76, "paper words but opportunity/call context", matched_signals=["paper_terms", "opportunity_context"], conflict_detected=True)
        return _rule("paper_manuscript_feedback", 0.82, "manuscript feedback keywords", matched_signals=["manuscript_feedback_keywords"])

    if _contains_any(text, ["call for papers", "征稿", "submit abstract", "best poster", "special issue", "invited submission", "workshop invitation"]):
        return _rule("academic_opportunity_call", 0.78, "academic opportunity/call keywords", matched_signals=["opportunity_keywords"])

    if _contains_any(text, ["学术周报", "研究周报", "weekly report", "weekly briefing", "briefing generated", "digest generated"]):
        return _rule("academic_report_digest", 0.82, "academic report/digest keywords", matched_signals=["report_digest_keywords"])

    if _contains_any(text, ["google scholar", "scholar alert", "citation alert", "文献快讯", "new articles", "research alert"]):
        return _rule("academic_alert_digest", 0.83, "academic alert/digest keywords", matched_signals=["academic_alert_keywords"])

    if _contains_any(text, ["修改意见", "论文修改", "实验结果", "导师", "合作者", "manuscript draft", "draft comments"]):
        return _rule("research_feedback_thread", 0.68, "research feedback thread keywords", matched_signals=["research_thread_keywords"])

    if _contains_any(text, ["firms", "nasa", "download order", "data order", "订单状态", "全文传递", "nstl", "download ready"]):
        return _rule("data_download_order_notice", 0.78, "data/download/order keywords", matched_signals=["data_order_keywords"])

    if _contains_any(text, ["api key", "cron", "任务完成", "自动化", "generated successfully", "build completed", "workflow", "system notification"]):
        return _rule("system_automation_notice", 0.78, "system automation keywords", matched_signals=["system_automation_keywords"])

    if shape.get("newsletter_hint") or _contains_any(text, ["unsubscribe", "退订", "newsletter", "促销", "marketing", "推广"]):
        return _rule("newsletter_marketing", 0.72, "newsletter/marketing signals", matched_signals=["newsletter_hint"])

    if _contains_any(text, ["快递", "取件", "物流", "已签收", "delivery", "shipment", "tracking number", "package"]):
        return _rule("delivery_logistics", 0.80, "delivery/logistics keywords", matched_signals=["delivery_keywords"])

    if broad:
        return broad

    return _rule("personal_or_general", 0.35, "default catch-all", decision_source="catch_all", matched_signals=["default"])


def apply_learned_sender_rules(features: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Any]:
    """Use existing learned_sender_rules only when very conservative."""
    try:
        domain = features.get("sender_domain") or ""
        if not domain:
            return current
        conn = _connect()
        rows = conn.execute(
            """
            SELECT category, confidence, support, source
            FROM learned_sender_rules
            WHERE status='active' AND domain=?
            ORDER BY confidence DESC, support DESC
            LIMIT 1
            """,
            (domain,),
        ).fetchall()
        conn.close()
        if not rows:
            return current
        row = rows[0]
        cat = str(row["category"] or "")
        if cat in CANONICAL_CATEGORIES and float(row["confidence"] or 0) >= 0.86 and int(row["support"] or 0) >= 3:
            learned = dict(current)
            learned.update({
                "category": cat,
                "confidence": max(float(current.get("confidence", 0)), float(row["confidence"])),
                "decision_source": "learned_sender_rules",
                "matched_signals": list(current.get("matched_signals") or []) + ["learned_sender_rule"],
            })
            return learned
    except Exception:
        return current
    return current


def should_call_llm(decision: Dict[str, Any], config: Dict[str, Any], decisions_seen: int, features: Dict[str, Any]) -> Dict[str, Any]:
    global _RUN_LLM_CALLS
    policy = (config or {}).get("policy") or {}
    high = float(policy.get("high_confidence_skip_threshold", 0.86))
    low = float(policy.get("low_confidence_llm_threshold", 0.55))
    cold_until = int(policy.get("cold_start_llm_until_decisions", 100))
    sample_rate = float(policy.get("audit_sample_rate", 0.05))
    max_calls = int(policy.get("max_llm_calls_per_run", 5))
    reason = []
    confidence = float(decision.get("confidence", 0.0))
    if decisions_seen < cold_until:
        reason.append("cold_start")
    if confidence < low:
        reason.append("low_confidence")
    elif confidence < high:
        reason.append("medium_confidence")
    if decision.get("conflict_detected"):
        reason.append("conflict_detected")
    if decision.get("negative_context_hit"):
        reason.append("negative_context_hit")
    if decision.get("category") in {"personal_or_general", "unknown_needs_llm"}:
        reason.append("catch_all_or_unknown")
    # Deterministic audit sampling based on message key.
    digest = int(_hash(features.get("message_key") or "")[:8], 16)
    if confidence >= high and (digest % 10000) < int(sample_rate * 10000):
        reason.append("audit_sample")
    would_call = bool(reason) and ((config.get("llm") or {}).get("enabled", False))
    capped = _RUN_LLM_CALLS >= max_calls
    return {
        "needs_llm": bool(reason),
        "llm_reason": reason,
        "llm_call_allowed": would_call and not capped,
        "llm_call_capped": bool(would_call and capped),
        "run_llm_calls": _RUN_LLM_CALLS,
        "max_llm_calls_per_run": max_calls,
    }


def apply_semantic_conflict_policy(
    rule_decision: Dict[str, Any],
    llm_result: Optional[Dict[str, Any]],
    config: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Annotate rule/LLM disagreement and quarantine unsafe pattern candidates."""
    if not isinstance(llm_result, dict):
        return llm_result

    result = dict(llm_result)
    if not result.get("ok") or not result.get("schema_valid"):
        return result

    policy = (config or {}).get("policy") or {}
    high = float(policy.get("high_confidence_skip_threshold", 0.86))
    rule_category = str((rule_decision or {}).get("category") or "unknown_needs_llm")
    llm_category = str(result.get("semantic_category") or "unknown_needs_llm")
    rule_confidence = float((rule_decision or {}).get("confidence") or 0.0)
    disagreement = rule_category != llm_category
    negative_context = bool((rule_decision or {}).get("negative_context_hit"))
    rule_conflict = bool((rule_decision or {}).get("conflict_detected"))
    high_conflict = disagreement and rule_confidence >= high and rule_category not in {
        "personal_or_general",
        "unknown_needs_llm",
    }

    quarantine_reasons: List[str] = []
    if high_conflict:
        quarantine_reasons.append("high_confidence_rule_disagreement")
    if negative_context:
        quarantine_reasons.append("negative_context_hit")
    if rule_conflict:
        quarantine_reasons.append("rule_conflict_detected")
    if result.get("negative_context_detected"):
        quarantine_reasons.append("llm_negative_context_detected")

    result["category_disagreement"] = disagreement
    result["rule_category_observed"] = rule_category
    result["rule_confidence_observed"] = rule_confidence
    result["candidate_quarantined"] = False
    result["candidate_quarantine_reasons"] = []

    candidate = result.get("learned_pattern_candidate")
    if candidate and quarantine_reasons:
        result["quarantined_pattern_candidate"] = candidate
        result["learned_pattern_candidate"] = None
        result["candidate_quarantined"] = True
        result["candidate_quarantine_reasons"] = quarantine_reasons
        result["candidate_status"] = {
            "ok": False,
            "error": "candidate quarantined: " + ",".join(quarantine_reasons),
            "quarantined": True,
        }

    return result

def compose_final_decision(
    rule_decision: Dict[str, Any],
    llm_result: Optional[Dict[str, Any]],
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Compose a conservative shadow decision without letting one LLM call erase strong rule evidence."""
    final = dict(rule_decision or {})
    rule_category = str(final.get("category") or "unknown_needs_llm")
    rule_confidence = float(final.get("confidence") or 0.0)
    final["final_category"] = rule_category
    final["final_confidence"] = rule_confidence
    final["final_source"] = final.get("decision_source", "base_rules")
    final["llm_override_blocked"] = False
    final["category_disagreement"] = False

    if not (llm_result and llm_result.get("ok") and llm_result.get("schema_valid")):
        return final

    policy = (config or {}).get("policy") or {}
    high = float(policy.get("high_confidence_skip_threshold", 0.86))
    llm_category = str(llm_result.get("semantic_category") or "unknown_needs_llm")
    llm_confidence = float(llm_result.get("confidence") or 0.0)
    agreement = llm_category == rule_category
    catch_all = rule_category in {"personal_or_general", "unknown_needs_llm"}
    negative_context = bool(final.get("negative_context_hit"))
    rule_conflict = bool(final.get("conflict_detected"))

    final["llm_category"] = llm_category
    final["llm_confidence"] = llm_confidence
    final["category_disagreement"] = not agreement

    if agreement:
        final["final_category"] = rule_category
        final["final_confidence"] = max(rule_confidence, llm_confidence)
        final["final_source"] = "rule_llm_agreement"
    elif rule_confidence >= high and not catch_all:
        final["final_source"] = "rule_preferred_high_conflict"
        final["llm_override_blocked"] = True
        final["conflict_detected"] = True
    elif negative_context or rule_conflict:
        final["final_source"] = "rule_preferred_conflict_context"
        final["llm_override_blocked"] = True
        final["conflict_detected"] = True
    elif catch_all and llm_confidence >= 0.55:
        final["final_category"] = llm_category
        final["final_confidence"] = llm_confidence
        final["final_source"] = "llm_shadow_low_rule_confidence"
    elif llm_confidence >= max(0.75, rule_confidence + 0.10):
        final["final_category"] = llm_category
        final["final_confidence"] = llm_confidence
        final["final_source"] = "llm_shadow_strong_margin"
    else:
        final["final_source"] = "rule_preferred_insufficient_llm_margin"
        final["llm_override_blocked"] = True
        final["conflict_detected"] = True

    final["importance"] = llm_result.get("importance")
    final["needs_action"] = llm_result.get("needs_action")
    final["deadline"] = llm_result.get("deadline")
    final["summary"] = llm_result.get("summary")
    final["recommended_render_mode"] = llm_result.get("recommended_render_mode")
    final["candidate_quarantined"] = bool(llm_result.get("candidate_quarantined"))
    final["candidate_quarantine_reasons"] = list(llm_result.get("candidate_quarantine_reasons") or [])
    return final



# EMAIL_WATCHDOG_DECISION_ENGINE_SHADOW_DB_FIX_V1C
def _record_db(record: Dict[str, Any]) -> Dict[str, Any]:
    """Persist shadow decisions and candidate-lifecycle evidence in one transaction."""
    conn = None
    try:
        record = record or {}
        features = record.get("features") or {}
        rule = record.get("rule_decision") or {}
        gate = record.get("gate") or {}
        llm = record.get("llm_result") or {}
        final = record.get("final_decision") or {}
        body_shape = features.get("body_shape") or {}
        message_key_value = str(record.get("message_key") or features.get("message_key") or "")
        now_value = str(record.get("created_at") or _now())
        category = str(final.get("final_category") or rule.get("category") or "unknown_needs_llm")
        confidence = float(final.get("final_confidence") or rule.get("confidence") or 0.0)

        conn = _connect()
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS email_feature_cache (
            message_key TEXT PRIMARY KEY,
            account_id TEXT,
            subject_hash TEXT,
            sender_hash TEXT,
            sender_domain TEXT,
            subject_tokens_json TEXT,
            body_tokens_json TEXT,
            body_shape_json TEXT,
            attachment_profile_json TEXT,
            thread_state TEXT,
            has_code_context INTEGER NOT NULL DEFAULT 0,
            has_deadline INTEGER NOT NULL DEFAULT 0,
            feature_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS decision_engine_shadow (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_key TEXT NOT NULL,
            account_id TEXT,
            rule_category TEXT,
            rule_confidence REAL,
            learned_category TEXT,
            learned_confidence REAL,
            llm_category TEXT,
            llm_confidence REAL,
            final_category TEXT,
            final_confidence REAL,
            render_mode TEXT,
            needs_action INTEGER NOT NULL DEFAULT 0,
            importance TEXT,
            deadline TEXT,
            decision_source TEXT,
            needs_llm INTEGER NOT NULL DEFAULT 0,
            llm_called INTEGER NOT NULL DEFAULT 0,
            audit_sampled INTEGER NOT NULL DEFAULT 0,
            record_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS llm_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_key TEXT NOT NULL,
            model TEXT,
            prompt_version TEXT,
            raw_output_json TEXT,
            normalized_output_json TEXT,
            schema_valid INTEGER NOT NULL DEFAULT 0,
            semantic_category TEXT,
            confidence REAL,
            summary TEXT,
            evidence_json TEXT,
            recommended_render_mode TEXT,
            learned_pattern_candidate_json TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS learned_pattern_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_message_key TEXT,
            source_llm_observation_id INTEGER,
            candidate_json TEXT NOT NULL,
            category TEXT,
            confidence REAL NOT NULL DEFAULT 0.0,
            support_count INTEGER NOT NULL DEFAULT 0,
            shadow_match_count INTEGER NOT NULL DEFAULT 0,
            shadow_correct_count INTEGER NOT NULL DEFAULT 0,
            shadow_wrong_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'candidate',
            created_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS category_stats (
            category TEXT PRIMARY KEY,
            total_seen INTEGER NOT NULL DEFAULT 0,
            llm_called INTEGER NOT NULL DEFAULT 0,
            llm_correction_count INTEGER NOT NULL DEFAULT 0,
            user_correction_count INTEGER NOT NULL DEFAULT 0,
            avg_confidence REAL NOT NULL DEFAULT 0.0,
            last_seen_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_decision_engine_shadow_message_key ON decision_engine_shadow(message_key);
        CREATE INDEX IF NOT EXISTS idx_decision_engine_shadow_created_at ON decision_engine_shadow(created_at);
        CREATE INDEX IF NOT EXISTS idx_decision_engine_shadow_final_category ON decision_engine_shadow(final_category);
        """)

        lifecycle_schema = {
            "ok": False,
            "error": "email_candidate_lifecycle import failed",
            "runtime_activation": False,
        }
        lifecycle_match = {
            "ok": False,
            "error": "email_candidate_lifecycle import failed",
            "runtime_decision_changed": False,
        }
        if email_candidate_lifecycle is not None:
            lifecycle_schema = email_candidate_lifecycle.ensure_schema(conn, now_value)

        conn.execute(
            """
            INSERT INTO email_feature_cache (
                message_key, account_id, subject_hash, sender_hash, sender_domain,
                subject_tokens_json, body_tokens_json, body_shape_json,
                attachment_profile_json, thread_state, has_code_context, has_deadline,
                feature_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(message_key) DO UPDATE SET
                account_id=excluded.account_id,
                subject_hash=excluded.subject_hash,
                sender_hash=excluded.sender_hash,
                sender_domain=excluded.sender_domain,
                subject_tokens_json=excluded.subject_tokens_json,
                body_tokens_json=excluded.body_tokens_json,
                body_shape_json=excluded.body_shape_json,
                attachment_profile_json=excluded.attachment_profile_json,
                thread_state=excluded.thread_state,
                has_code_context=excluded.has_code_context,
                has_deadline=excluded.has_deadline,
                feature_json=excluded.feature_json,
                updated_at=excluded.updated_at
            """,
            (
                message_key_value,
                features.get("account"),
                features.get("subject_hash"),
                features.get("sender_hash"),
                features.get("sender_domain"),
                _json(features.get("subject_tokens") or []),
                _json(features.get("body_tokens") or []),
                _json(body_shape),
                _json(features.get("attachment_profile") or {}),
                "reply_forward" if body_shape.get("reply_forward_hint") else "single",
                int(bool(features.get("code_candidates"))),
                int(bool(body_shape.get("deadline_hint"))),
                _json(features),
                now_value,
                now_value,
            ),
        )

        if email_candidate_lifecycle is not None:
            lifecycle_match = email_candidate_lifecycle.evaluate_existing_candidates(
                conn,
                features,
                message_key_value,
                rule,
                llm,
                final,
                now_value,
            )

        llm_observation_id = None
        if llm:
            cur = conn.execute(
                """
                INSERT INTO llm_observations (
                    message_key, model, prompt_version, raw_output_json,
                    normalized_output_json, schema_valid, semantic_category,
                    confidence, summary, evidence_json, recommended_render_mode,
                    learned_pattern_candidate_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_key_value,
                    llm.get("model"),
                    "decision_engine_shadow_v1",
                    _json({"raw_output_excerpt": llm.get("raw_output_excerpt"), "error": llm.get("error")}),
                    _json(llm),
                    int(bool(llm.get("schema_valid"))),
                    llm.get("semantic_category"),
                    float(llm.get("confidence") or 0.0),
                    llm.get("summary"),
                    _json(llm.get("evidence") or []),
                    llm.get("recommended_render_mode"),
                    _json(llm.get("learned_pattern_candidate") or {}),
                    now_value,
                ),
            )
            llm_observation_id = cur.lastrowid

        lifecycle_generated = {
            "ok": True,
            "candidate_recorded": False,
            "reason": "no candidate",
            "runtime_activation": False,
        }
        candidate = llm.get("learned_pattern_candidate") if isinstance(llm, dict) else None
        if isinstance(candidate, dict) and candidate and email_candidate_lifecycle is not None:
            lifecycle_generated = email_candidate_lifecycle.record_generated_candidate(
                conn,
                candidate,
                message_key_value,
                llm_observation_id,
                str(candidate.get("category") or llm.get("semantic_category") or category),
                float(candidate.get("confidence") or llm.get("confidence") or 0.0),
                now_value,
            )
        elif isinstance(candidate, dict) and candidate:
            lifecycle_generated = {
                "ok": False,
                "candidate_recorded": False,
                "error": "email_candidate_lifecycle import failed",
                "runtime_activation": False,
            }

        lifecycle_status = {}
        if email_candidate_lifecycle is not None:
            lifecycle_status = email_candidate_lifecycle.status(conn)

        lifecycle_summary = {
            "schema": lifecycle_schema,
            "future_shadow_matches": lifecycle_match,
            "generated_candidate": lifecycle_generated,
            "status": lifecycle_status,
            "runtime_activation": False,
            "learned_category_rules_written": False,
            "correct_wrong_counters_updated_without_ground_truth": False,
        }

        record_for_storage = dict(record)
        record_for_storage["candidate_lifecycle"] = lifecycle_summary

        conn.execute(
            """
            INSERT INTO decision_engine_shadow (
                message_key, account_id, rule_category, rule_confidence,
                learned_category, learned_confidence, llm_category, llm_confidence,
                final_category, final_confidence, render_mode, needs_action,
                importance, deadline, decision_source, needs_llm, llm_called,
                audit_sampled, record_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message_key_value,
                features.get("account"),
                rule.get("category"),
                float(rule.get("confidence") or 0.0),
                final.get("learned_category"),
                float(final.get("learned_confidence") or 0.0),
                llm.get("semantic_category"),
                float(llm.get("confidence") or 0.0),
                category,
                confidence,
                final.get("recommended_render_mode"),
                int(bool(final.get("needs_action"))),
                final.get("importance"),
                final.get("deadline"),
                final.get("final_source") or rule.get("decision_source"),
                int(bool(gate.get("needs_llm"))),
                int(bool(llm.get("llm_called"))),
                int("audit_sample" in (gate.get("llm_reason") or [])),
                _json(record_for_storage),
                now_value,
            ),
        )

        old = conn.execute(
            "SELECT total_seen, avg_confidence FROM category_stats WHERE category=?",
            (category,),
        ).fetchone()
        if old:
            old_total = int(old["total_seen"])
            total = old_total + 1
            avg = ((float(old["avg_confidence"]) * old_total) + confidence) / max(total, 1)
            conn.execute(
                "UPDATE category_stats SET total_seen=?, llm_called=llm_called+?, avg_confidence=?, last_seen_at=? WHERE category=?",
                (total, int(bool(llm.get("llm_called"))), avg, now_value, category),
            )
        else:
            conn.execute(
                "INSERT INTO category_stats(category, total_seen, llm_called, avg_confidence, last_seen_at) VALUES (?, 1, ?, ?, ?)",
                (category, int(bool(llm.get("llm_called"))), confidence, now_value),
            )

        conn.commit()
        return {
            "ok": True,
            "writer": "local_sqlite_db_fix_v1c_candidate_lifecycle_shadow_v1",
            "db_path": str(DB_PATH),
            "message_key": message_key_value,
            "llm_observation_id": llm_observation_id,
            "candidate_lifecycle": lifecycle_summary,
        }
    except Exception as exc:
        try:
            if conn is not None:
                conn.rollback()
        except Exception:
            pass
        return {
            "ok": False,
            "writer": "local_sqlite_db_fix_v1c_candidate_lifecycle_shadow_v1",
            "db_path": str(DB_PATH),
            "error": repr(exc),
        }
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass



def _record_candidate(
    record: Dict[str, Any],
    candidate: Dict[str, Any],
    lifecycle_result: Optional[Dict[str, Any]] = None,
) -> None:
    if not candidate:
        return
    payload = {
        "created_at": _now(),
        "message_key": record.get("message_key"),
        "candidate": candidate,
        "category": candidate.get("category") or record.get("final_decision", {}).get("final_category"),
        "source": "llm_candidate_shadow_v1",
        "candidate_lifecycle": lifecycle_result or {},
        "runtime_activation": False,
    }
    _safe_append_jsonl(CANDIDATE_JSONL, payload)


def shadow_observe(email: Dict[str, Any], rule_result: Dict[str, Any], analysis: Dict[str, Any], delivery: Dict[str, Any], account: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Observe one already-delivered email. All exceptions are contained."""
    global _RUN_LLM_CALLS
    try:
        # Regression tests create isolated /tmp/email_regression_* configs; do not call LLM there.
        watchdog_cfg = os.environ.get("EMAIL_WATCHDOG_CONFIG", "")
        if "/tmp/email_regression_" in watchdog_cfg:
            return {"ok": True, "skipped": True, "reason": "isolated regression test"}
        cfg = ensure_config()
        if not cfg.get("enabled", True) or cfg.get("mode") != "shadow":
            return {"ok": True, "skipped": True, "reason": "decision engine disabled or not shadow"}
        if email_feature_extractor is None:
            return {"ok": False, "error": "email_feature_extractor import failed"}
        features = email_feature_extractor.extract_features(email or {})
        decisions_seen = _decision_count()
        rule_decision = classify_base(features, rule_result, analysis)
        rule_decision = apply_learned_sender_rules(features, rule_decision)
        gate = should_call_llm(rule_decision, cfg, decisions_seen, features)
        llm_result = None
        if gate.get("llm_call_allowed") and email_llm_decision is not None:
            _RUN_LLM_CALLS += 1
            llm_result = email_llm_decision.analyze(features, rule_decision, cfg)
            llm_result = apply_semantic_conflict_policy(rule_decision, llm_result, cfg)
            _safe_append_jsonl(LLM_JSONL, {
                "created_at": _now(),
                "message_key": features.get("message_key"),
                "result": llm_result,
            })
        final_decision = compose_final_decision(rule_decision, llm_result, cfg)
        record = {
            "schema": MARKER,
            "created_at": _now(),
            "message_key": features.get("message_key"),
            "account": features.get("account"),
            "msg_id_hash": _hash(features.get("msg_id") or ""),
            "features": email_feature_extractor.compact_features_for_storage(features),
            "existing_rule_result": rule_result or {},
            "existing_analysis_keys": sorted((analysis or {}).keys()),
            "delivery_status": (delivery or {}).get("status") or (delivery or {}).get("delivery_status") or "",
            "rule_decision": rule_decision,
            "gate": gate,
            "llm_result": llm_result,
            "final_decision": final_decision,
            "safety": cfg.get("safety") or {},
        }
        db_result = _record_db(record)
        record["db_result"] = db_result
        _safe_append_jsonl(SHADOW_JSONL, record)
        candidate = (llm_result or {}).get("learned_pattern_candidate") if isinstance(llm_result, dict) else None
        if candidate:
            lifecycle_result = (
                ((db_result or {}).get("candidate_lifecycle") or {}).get("generated_candidate")
                if isinstance(db_result, dict)
                else None
            )
            _record_candidate(record, candidate, lifecycle_result)
        return {
            "ok": True,
            "message_key": features.get("message_key"),
            "rule_category": rule_decision.get("category"),
            "final_category": final_decision.get("final_category"),
            "needs_llm": gate.get("needs_llm"),
            "llm_called": bool(llm_result and llm_result.get("llm_called")),
            "db": db_result,
        }
    except Exception as exc:
        try:
            _safe_append_jsonl(SHADOW_JSONL, {"schema": MARKER, "created_at": _now(), "ok": False, "error": repr(exc)})
        except Exception:
            pass
        return {"ok": False, "error": repr(exc)}


def status() -> Dict[str, Any]:
    cfg = ensure_config()
    return {
        "ok": True,
        "marker": MARKER,
        "config_path": str(CONFIG_PATH),
        "shadow_jsonl": str(SHADOW_JSONL),
        "llm_jsonl": str(LLM_JSONL),
        "candidate_jsonl": str(CANDIDATE_JSONL),
        "db_path": str(DB_PATH),
        "enabled": bool(cfg.get("enabled")),
        "mode": cfg.get("mode"),
        "llm_enabled": bool((cfg.get("llm") or {}).get("enabled")),
        "model": (cfg.get("llm") or {}).get("model"),
        "decisions_seen": _decision_count(),
        "candidate_lifecycle_marker": (
            getattr(email_candidate_lifecycle, "MARKER", None)
            if email_candidate_lifecycle is not None
            else None
        ),
        "candidate_runtime_activation": False,
    }


if __name__ == "__main__":
    print(json.dumps(status(), ensure_ascii=False, indent=2, sort_keys=True))
