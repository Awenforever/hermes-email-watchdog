#!/usr/bin/env python3
"""
Email Watchdog shadow learning store.

Records how Email Watchdog decided to classify and push a message.
Shadow-only: no classification changes, no mailbox writes, no seen writes.
LLM output is a decision source, not user feedback.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

SCHEMA_VERSION = 1
DEFAULT_ROOT = Path("/opt/data/.hermes-home/.hermes/email_learning")
ROOT = Path(os.environ.get("EMAIL_LEARNING_ROOT", str(DEFAULT_ROOT)))
DB_PATH = Path(os.environ.get("EMAIL_LEARNING_DB", str(ROOT / "email_learning.sqlite")))

CANONICAL_CATEGORIES = {
    "verification_code", "security_alert", "school_notice", "meeting_event",
    "paper_feedback", "invoice_receipt", "delivery_logistics", "bank_finance",
    "account_notification", "personal_task", "newsletter", "marketing",
    "system_notification", "spam_suspicious", "other",
}
CATEGORY_ALIASES = {
    "email": "other", "mail": "other", "unknown": "other",
    "school": "school_notice", "notice": "school_notice",
    "meeting": "meeting_event", "event": "meeting_event",
    "paper": "paper_feedback", "review": "paper_feedback",
    "invoice": "invoice_receipt", "receipt": "invoice_receipt",
    "code": "verification_code", "verification": "verification_code",
    "security": "security_alert", "alert": "security_alert",
    "personal": "personal_task", "task": "personal_task",
    "system": "system_notification", "spam": "spam_suspicious",
}
IMPORTANCE_VALUES = {"urgent", "high", "medium", "low", "ignore", "skip"}
DISPLAY_MODES = {"compact", "evidence", "full_excerpt", "code_only", "full_body", "summary_only"}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _json_safe(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return json.dumps({"repr": repr(obj)}, ensure_ascii=False, sort_keys=True)


def _hash_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", "replace")).hexdigest()


def _domain(addr: str) -> str:
    addr = (addr or "").strip().lower()
    if "@" not in addr:
        return ""
    return addr.rsplit("@", 1)[-1]


def _norm_token(value: Any, default: str = "other") -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9_]+", "_", text).strip("_")
    return text or default


def normalize_category(value: Any) -> str:
    cat = _norm_token(value, "other")
    cat = CATEGORY_ALIASES.get(cat, cat)
    return cat if cat in CANONICAL_CATEGORIES else "other"


def normalize_importance(value: Any) -> str:
    imp = _norm_token(value, "medium")
    if imp in {"normal", "ordinary"}:
        imp = "medium"
    if imp == "important":
        imp = "high"
    return imp if imp in IMPORTANCE_VALUES else "medium"


def normalize_display_mode(value: Any, email: Optional[dict] = None, analysis: Optional[dict] = None) -> str:
    mode = _norm_token(value, "")
    if mode in DISPLAY_MODES:
        return mode
    if (analysis or {}).get("format_decision") == "code_extraction":
        return "code_only"
    if len((email or {}).get("body") or "") > 1200:
        return "evidence"
    return "compact"


def message_key(email: dict) -> str:
    account = str(email.get("account") or email.get("account_id") or "unknown")
    msg_id = str(email.get("msg_id") or email.get("id") or email.get("message_id") or "")
    if msg_id:
        return f"{account}:{msg_id}"
    seed = "|".join([
        account,
        str(email.get("from_addr") or email.get("from_email") or ""),
        str(email.get("subject") or ""),
        str(email.get("date_sent") or ""),
    ])
    return f"{account}:sha256:{_hash_text(seed)[:24]}"


def extract_features(email: dict) -> dict:
    subject = str(email.get("subject") or "")
    body = str(email.get("body") or email.get("body_plain") or "")
    from_addr = str(email.get("from_addr") or email.get("from_email") or "")
    tokens = []
    for field_text in [subject, body[:1000]]:
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", field_text):
            t = token.lower()
            if len(t) <= 64:
                tokens.append(t)
    return {
        "sender_domain": _domain(from_addr),
        "subject_hash": _hash_text(subject),
        "body_excerpt_hash": _hash_text(body[:2000]),
        "has_attachments": bool(email.get("has_attachments") or email.get("has_attachment")),
        "token_sample": tokens[:80],
        "body_chars": len(body),
    }


def _connect() -> sqlite3.Connection:
    ROOT.mkdir(parents=True, exist_ok=True)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db(conn: Optional[sqlite3.Connection] = None) -> None:
    own = conn is None
    conn = conn or _connect()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS message_decisions (
            message_key TEXT PRIMARY KEY,
            account TEXT,
            msg_id TEXT,
            subject_hash TEXT,
            sender_domain TEXT,
            sender_hash TEXT,
            base_category TEXT,
            rule_action TEXT,
            llm_category TEXT,
            final_category TEXT,
            importance TEXT,
            confidence REAL,
            display_mode TEXT,
            used_llm INTEGER NOT NULL DEFAULT 0,
            delivery_status TEXT,
            decision_json TEXT NOT NULL,
            feature_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS user_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_key TEXT NOT NULL,
            feedback_type TEXT NOT NULL,
            old_value TEXT,
            new_value TEXT,
            note TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS learned_sender_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender TEXT,
            domain TEXT,
            category TEXT NOT NULL,
            importance TEXT,
            confidence REAL NOT NULL DEFAULT 0.0,
            support INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'feedback',
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS learned_token_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT NOT NULL,
            field TEXT NOT NULL DEFAULT 'subject_body',
            category TEXT NOT NULL,
            weight REAL NOT NULL DEFAULT 0.0,
            support INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'feedback',
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS pattern_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern_type TEXT NOT NULL DEFAULT 'dsl',
            field TEXT NOT NULL,
            pattern_json TEXT NOT NULL,
            category TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 0.0,
            source TEXT NOT NULL DEFAULT 'llm_candidate',
            status TEXT NOT NULL DEFAULT 'candidate',
            hits INTEGER NOT NULL DEFAULT 0,
            correct_hits INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_message_decisions_sender_domain ON message_decisions(sender_domain);
        CREATE INDEX IF NOT EXISTS idx_message_decisions_final_category ON message_decisions(final_category);
        CREATE INDEX IF NOT EXISTS idx_message_decisions_updated_at ON message_decisions(updated_at);
        CREATE INDEX IF NOT EXISTS idx_user_feedback_message_key ON user_feedback(message_key);
        """
    )
    conn.execute(
        "INSERT OR REPLACE INTO schema_meta(key, value, updated_at) VALUES (?, ?, ?)",
        ("schema_version", str(SCHEMA_VERSION), _now()),
    )
    conn.commit()
    if own:
        conn.close()


def record_decision(email: dict, rule_result: dict, analysis: dict, delivery: Optional[dict] = None, account: Optional[dict] = None) -> dict:
    try:
        conn = _connect()
        init_db(conn)
        features = extract_features(email or {})
        from_addr = str((email or {}).get("from_addr") or (email or {}).get("from_email") or "")
        rule_result = rule_result or {}
        analysis = analysis or {}
        delivery = delivery or {}

        base_category = normalize_category(rule_result.get("category"))
        llm_category = normalize_category(analysis.get("semantic_category") or analysis.get("final_category"))
        final_category = normalize_category(analysis.get("semantic_category") or analysis.get("final_category") or rule_result.get("category"))
        importance = normalize_importance(analysis.get("user_relevance") or rule_result.get("priority"))
        display_mode = normalize_display_mode(analysis.get("display_mode") or analysis.get("format_decision"), email, analysis)

        try:
            confidence = float(analysis.get("confidence"))
        except Exception:
            confidence = 0.95 if rule_result.get("action") == "simple_code" else 0.35

        used_llm = int(bool(analysis.get("llm_raw") or analysis.get("llm_model") or analysis.get("llm_used")))
        if str(analysis.get("llm_notes") or "").lower().startswith("rule bypass"):
            used_llm = 0
        if "llm disabled" in _json_safe(analysis).lower():
            used_llm = 0

        key = message_key(email or {})
        now = _now()
        decision = {
            "email": {
                "account": (email or {}).get("account"),
                "msg_id": (email or {}).get("msg_id") or (email or {}).get("id"),
                "subject_hash": features["subject_hash"],
                "sender_domain": features["sender_domain"],
            },
            "rule_result": rule_result,
            "analysis": analysis,
            "delivery": delivery,
            "account_label": (account or {}).get("label") if isinstance(account, dict) else None,
        }

        conn.execute(
            """
            INSERT INTO message_decisions (
                message_key, account, msg_id, subject_hash, sender_domain, sender_hash,
                base_category, rule_action, llm_category, final_category, importance,
                confidence, display_mode, used_llm, delivery_status, decision_json,
                feature_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(message_key) DO UPDATE SET
                account=excluded.account,
                msg_id=excluded.msg_id,
                subject_hash=excluded.subject_hash,
                sender_domain=excluded.sender_domain,
                sender_hash=excluded.sender_hash,
                base_category=excluded.base_category,
                rule_action=excluded.rule_action,
                llm_category=excluded.llm_category,
                final_category=excluded.final_category,
                importance=excluded.importance,
                confidence=excluded.confidence,
                display_mode=excluded.display_mode,
                used_llm=excluded.used_llm,
                delivery_status=excluded.delivery_status,
                decision_json=excluded.decision_json,
                feature_json=excluded.feature_json,
                updated_at=excluded.updated_at
            """,
            (
                key,
                (email or {}).get("account"),
                (email or {}).get("msg_id") or (email or {}).get("id"),
                features["subject_hash"],
                features["sender_domain"],
                _hash_text(from_addr.lower()) if from_addr else "",
                base_category,
                rule_result.get("action"),
                llm_category,
                final_category,
                importance,
                confidence,
                display_mode,
                used_llm,
                str(delivery.get("status") or delivery.get("delivery_status") or ""),
                _json_safe(decision),
                _json_safe(features),
                now,
                now,
            ),
        )
        conn.commit()
        conn.close()
        return {"ok": True, "message_key": key, "db_path": str(DB_PATH)}
    except Exception as exc:
        return {"ok": False, "error": repr(exc), "db_path": str(DB_PATH)}


def record_feedback(message_key: str, feedback_type: str, old_value: str = "", new_value: str = "", note: str = "") -> dict:
    try:
        conn = _connect()
        init_db(conn)
        conn.execute(
            "INSERT INTO user_feedback(message_key, feedback_type, old_value, new_value, note, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (message_key, feedback_type, old_value, new_value, note, _now()),
        )
        conn.commit()
        conn.close()
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": repr(exc)}


def status() -> dict:
    try:
        conn = _connect()
        init_db(conn)
        decisions = int(conn.execute("SELECT COUNT(*) AS n FROM message_decisions").fetchone()["n"])
        feedback = int(conn.execute("SELECT COUNT(*) AS n FROM user_feedback").fetchone()["n"])
        conn.close()
        return {"ok": True, "db_path": str(DB_PATH), "decisions": decisions, "feedback": feedback, "schema_version": SCHEMA_VERSION}
    except Exception as exc:
        return {"ok": False, "error": repr(exc), "db_path": str(DB_PATH)}


if __name__ == "__main__":
    print(json.dumps(status(), ensure_ascii=False, indent=2))



# EMAIL_WATCHDOG_DECISION_ENGINE_LEARNING_SCHEMA_V1
# Shadow-only schema extension for decision-engine observations.
try:
    CANONICAL_CATEGORIES.update({
        "account_security",
        "account_status_notice",
        "health_check_notice",
        "task_deadline",
        "paper_manuscript_feedback",
        "academic_opportunity_call",
        "academic_report_digest",
        "academic_alert_digest",
        "research_feedback_thread",
        "data_download_order_notice",
        "system_automation_notice",
        "newsletter_marketing",
        "personal_or_general",
        "unknown_needs_llm",
    })
    CATEGORY_ALIASES.update({
        "security_alert": "account_security",
        "account_notification": "account_status_notice",
        "paper_feedback": "paper_manuscript_feedback",
        "newsletter": "newsletter_marketing",
        "marketing": "newsletter_marketing",
        "system_notification": "system_automation_notice",
        "personal_task": "personal_or_general",
        "other": "personal_or_general",
        "unknown": "unknown_needs_llm",
    })
except Exception:
    pass


def init_decision_engine_db(conn: Optional[sqlite3.Connection] = None) -> None:
    """Create additive decision-engine shadow tables. Does not alter mailbox state."""
    own = conn is None
    conn = conn or _connect()
    conn.executescript(
        """
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
        CREATE TABLE IF NOT EXISTS learned_category_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_type TEXT NOT NULL,
            field TEXT,
            pattern_json TEXT NOT NULL,
            category TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 0.0,
            support_count INTEGER NOT NULL DEFAULT 0,
            positive_count INTEGER NOT NULL DEFAULT 0,
            negative_count INTEGER NOT NULL DEFAULT 0,
            precision_estimate REAL NOT NULL DEFAULT 0.0,
            created_from_candidate_id INTEGER,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            expires_at TEXT,
            enabled INTEGER NOT NULL DEFAULT 0
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
        CREATE TABLE IF NOT EXISTS rule_corrections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            original_rule_id TEXT,
            original_category TEXT,
            corrected_category TEXT,
            context_signature TEXT,
            count INTEGER NOT NULL DEFAULT 0,
            confidence_penalty REAL NOT NULL DEFAULT 0.0,
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
        CREATE INDEX IF NOT EXISTS idx_llm_observations_message_key ON llm_observations(message_key);
        CREATE INDEX IF NOT EXISTS idx_learned_pattern_candidates_status ON learned_pattern_candidates(status);
        """
    )
    conn.execute(
        "INSERT OR REPLACE INTO schema_meta(key, value, updated_at) VALUES (?, ?, ?)",
        ("decision_engine_shadow_schema", "1", _now()),
    )
    conn.commit()
    if own:
        conn.close()


def record_decision_engine_shadow(record: dict) -> dict:
    """Persist decision-engine shadow record and optional LLM observation/candidate."""
    try:
        conn = _connect()
        init_db(conn)
        init_decision_engine_db(conn)
        record = record or {}
        features = record.get("features") or {}
        rule = record.get("rule_decision") or {}
        gate = record.get("gate") or {}
        llm = record.get("llm_result") or {}
        final = record.get("final_decision") or {}
        message_key_value = str(record.get("message_key") or features.get("message_key") or "")
        now_value = str(record.get("created_at") or _now())

        body_shape = features.get("body_shape") or {}
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
                _json_safe(features.get("subject_tokens") or []),
                _json_safe(features.get("body_tokens") or []),
                _json_safe(body_shape),
                _json_safe(features.get("attachment_profile") or {}),
                "reply_forward" if body_shape.get("reply_forward_hint") else "single",
                int(bool(features.get("code_candidates"))),
                int(bool(body_shape.get("deadline_hint"))),
                _json_safe(features),
                now_value,
                now_value,
            ),
        )

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
                final.get("final_category"),
                float(final.get("final_confidence") or 0.0),
                final.get("recommended_render_mode"),
                int(bool(final.get("needs_action"))),
                final.get("importance"),
                final.get("deadline"),
                final.get("final_source") or rule.get("decision_source"),
                int(bool(gate.get("needs_llm"))),
                int(bool(llm.get("llm_called"))),
                int("audit_sample" in (gate.get("llm_reason") or [])),
                _json_safe(record),
                now_value,
            ),
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
                    _json_safe({"raw_output_excerpt": llm.get("raw_output_excerpt"), "error": llm.get("error")}),
                    _json_safe(llm),
                    int(bool(llm.get("schema_valid"))),
                    llm.get("semantic_category"),
                    float(llm.get("confidence") or 0.0),
                    llm.get("summary"),
                    _json_safe(llm.get("evidence") or []),
                    llm.get("recommended_render_mode"),
                    _json_safe(llm.get("learned_pattern_candidate") or {}),
                    now_value,
                ),
            )
            llm_observation_id = cur.lastrowid
            candidate = llm.get("learned_pattern_candidate")
            if isinstance(candidate, dict) and candidate:
                conn.execute(
                    """
                    INSERT INTO learned_pattern_candidates (
                        source_message_key, source_llm_observation_id, candidate_json,
                        category, confidence, support_count, status, created_at, last_seen_at
                    )
                    VALUES (?, ?, ?, ?, ?, 1, 'candidate', ?, ?)
                    """,
                    (
                        message_key_value,
                        llm_observation_id,
                        _json_safe(candidate),
                        candidate.get("category") or llm.get("semantic_category"),
                        float(candidate.get("confidence") or llm.get("confidence") or 0.0),
                        now_value,
                        now_value,
                    ),
                )

        category = str(final.get("final_category") or rule.get("category") or "unknown_needs_llm")
        confidence = float(final.get("final_confidence") or rule.get("confidence") or 0.0)
        old = conn.execute("SELECT total_seen, avg_confidence FROM category_stats WHERE category=?", (category,)).fetchone()
        if old:
            total = int(old["total_seen"]) + 1
            avg = ((float(old["avg_confidence"]) * int(old["total_seen"])) + confidence) / max(total, 1)
            conn.execute(
                """
                UPDATE category_stats
                SET total_seen=?, llm_called=llm_called+?, avg_confidence=?, last_seen_at=?
                WHERE category=?
                """,
                (total, int(bool(llm.get("llm_called"))), avg, now_value, category),
            )
        else:
            conn.execute(
                """
                INSERT INTO category_stats(category, total_seen, llm_called, avg_confidence, last_seen_at)
                VALUES (?, 1, ?, ?, ?)
                """,
                (category, int(bool(llm.get("llm_called"))), confidence, now_value),
            )

        conn.commit()
        conn.close()
        return {"ok": True, "message_key": message_key_value, "llm_observation_id": llm_observation_id, "db_path": str(DB_PATH)}
    except Exception as exc:
        return {"ok": False, "error": repr(exc), "db_path": str(DB_PATH)}

