#!/usr/bin/env python3
"""
Candidate lifecycle shadow support for Hermes Email Watchdog.

Shadow-only responsibilities:
- canonicalize and fingerprint safe candidate patterns;
- deduplicate repeated candidates across distinct messages;
- keep idempotent evidence records;
- evaluate candidate matches on later messages;
- update shadow-only counters and review eligibility;
- never activate learned rules or write learned_category_rules.
"""
from __future__ import annotations

# EMAIL_WATCHDOG_CANDIDATE_LIFECYCLE_SHADOW_V1

import hashlib
import json
import sqlite3
from typing import Any, Dict, Iterable, List, Optional

try:
    import email_safe_patterns
except Exception:  # Keep decision-engine import safe.
    email_safe_patterns = None

MARKER = "EMAIL_WATCHDOG_CANDIDATE_LIFECYCLE_SHADOW_V1"

DEFAULT_POLICY = {
    "shadowing_min_support": 2,
    "shadowing_min_matches": 2,
    "review_min_support": 3,
    "review_min_matches": 3,
    "review_min_agreement_ratio": 0.80,
    "promotion_min_ground_truth_positive": 2,
    "promotion_max_ground_truth_negative": 0,
}

ACTIVE_STATUSES = {"candidate", "shadowing"}
TERMINAL_STATUSES = {"rejected", "expired", "merged_duplicate", "promoted"}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out: List[str] = []
    seen = set()
    for item in value[:50]:
        text = str(item or "").strip().lower()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return sorted(out)


def _normalized_required(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    out: Dict[str, Any] = {}
    for key in sorted(value):
        key_text = str(key or "").strip()
        if not key_text:
            continue
        item = value[key]
        if isinstance(item, (str, int, float, bool)) or item is None:
            out[key_text] = item
    return out


def canonicalize_candidate(candidate: Dict[str, Any], category: Optional[str] = None) -> Dict[str, Any]:
    """Return a stable semantic identity for a validated safe candidate.

    Confidence and model-specific commentary are deliberately excluded from the
    identity so repeated evidence can aggregate onto one candidate.
    """
    if email_safe_patterns is None:
        raise RuntimeError("email_safe_patterns import failed")

    checked = email_safe_patterns.validate_candidate(candidate or {}, category)
    if not checked.get("ok"):
        raise ValueError(str(checked.get("error") or "invalid candidate"))

    source = dict(checked.get("candidate") or {})
    candidate_type = str(source.get("type") or "").strip()
    field = str(source.get("field") or "subject_body").strip()
    canonical_category = str(source.get("category") or category or "unknown_needs_llm").strip()

    identity: Dict[str, Any] = {
        "type": candidate_type,
        "field": field,
        "category": canonical_category,
    }

    negative_tokens = _list(source.get("negative_tokens"))
    if negative_tokens:
        identity["negative_tokens"] = negative_tokens

    if candidate_type in {
        "subject_contains_any",
        "subject_contains_all",
        "body_contains_any",
        "body_contains_all",
        "sender_contains",
    }:
        identity["tokens"] = _list(source.get("tokens") or source.get("contains"))
    elif candidate_type == "domain_match":
        identity["domains"] = [item.lstrip("@") for item in _list(
            source.get("domains") or source.get("tokens") or source.get("domain")
        )]
    elif candidate_type == "safe_regex":
        identity["pattern"] = str(source.get("pattern") or "").strip()
    elif candidate_type == "body_shape_match":
        identity["required"] = _normalized_required(source.get("required"))
    elif candidate_type == "attachment_type_match":
        identity["suffixes"] = [item.lstrip(".") for item in _list(
            source.get("suffixes") or source.get("tokens")
        )]

    return identity


def candidate_fingerprint(candidate: Dict[str, Any], category: Optional[str] = None) -> str:
    canonical = canonicalize_candidate(candidate, category)
    return hashlib.sha256(_json(canonical).encode("utf-8")).hexdigest()


def _columns(conn: sqlite3.Connection, table: str) -> Dict[str, sqlite3.Row]:
    return {str(row["name"]): row for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _ensure_column(conn: sqlite3.Connection, table: str, definition: str) -> None:
    name = definition.split()[0]
    if name not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def _candidate_row_by_fingerprint(conn: sqlite3.Connection, fingerprint: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM learned_pattern_candidates WHERE candidate_fingerprint=? LIMIT 1",
        (fingerprint,),
    ).fetchone()


def _refresh_candidate_state(conn: sqlite3.Connection, candidate_id: int, now_value: str) -> Dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM learned_pattern_candidates WHERE id=?",
        (candidate_id,),
    ).fetchone()
    if row is None:
        return {"ok": False, "error": "candidate not found", "candidate_id": candidate_id}

    status = str(row["status"] or "candidate")
    if status in TERMINAL_STATUSES:
        return {
            "ok": True,
            "candidate_id": candidate_id,
            "status": status,
            "review_eligible": int(row["review_eligible"] or 0),
            "promotion_eligible": int(row["promotion_eligible"] or 0),
            "eligibility_reason": row["eligibility_reason"],
        }

    support = int(row["support_count"] or 0)
    matches = int(row["shadow_match_count"] or 0)
    agreements = int(row["agreement_count"] or 0)
    disagreements = int(row["disagreement_count"] or 0)
    gt_positive = int(row["ground_truth_positive_count"] or 0)
    gt_negative = int(row["ground_truth_negative_count"] or 0)

    if status == "candidate" and (
        support >= DEFAULT_POLICY["shadowing_min_support"]
        or matches >= DEFAULT_POLICY["shadowing_min_matches"]
    ):
        status = "shadowing"

    agreement_total = agreements + disagreements
    agreement_ratio = agreements / agreement_total if agreement_total else 0.0
    review_eligible = int(
        support >= DEFAULT_POLICY["review_min_support"]
        and matches >= DEFAULT_POLICY["review_min_matches"]
        and agreement_total >= DEFAULT_POLICY["review_min_matches"]
        and agreement_ratio >= DEFAULT_POLICY["review_min_agreement_ratio"]
    )

    promotion_eligible = int(
        review_eligible
        and gt_positive >= DEFAULT_POLICY["promotion_min_ground_truth_positive"]
        and gt_negative <= DEFAULT_POLICY["promotion_max_ground_truth_negative"]
    )

    if promotion_eligible:
        reason = "ground_truth_threshold_met_review_only"
    elif review_eligible:
        reason = "shadow_consensus_review_ready_no_ground_truth"
    elif status == "shadowing":
        reason = "shadowing_collecting_evidence"
    else:
        reason = "candidate_collecting_support"

    conn.execute(
        """
        UPDATE learned_pattern_candidates
        SET status=?, review_eligible=?, promotion_eligible=?, eligibility_reason=?,
            last_evaluated_at=?
        WHERE id=?
        """,
        (status, review_eligible, promotion_eligible, reason, now_value, candidate_id),
    )

    return {
        "ok": True,
        "candidate_id": candidate_id,
        "status": status,
        "support_count": support,
        "shadow_match_count": matches,
        "agreement_count": agreements,
        "disagreement_count": disagreements,
        "agreement_ratio": round(agreement_ratio, 6),
        "ground_truth_positive_count": gt_positive,
        "ground_truth_negative_count": gt_negative,
        "review_eligible": review_eligible,
        "promotion_eligible": promotion_eligible,
        "eligibility_reason": reason,
    }


def _merge_duplicate_rows(conn: sqlite3.Connection, now_value: str) -> Dict[str, int]:
    rows = conn.execute(
        "SELECT * FROM learned_pattern_candidates ORDER BY id"
    ).fetchall()
    seen: Dict[str, int] = {}
    backfilled = 0
    merged = 0
    invalid = 0

    for row in rows:
        candidate_id = int(row["id"])
        try:
            raw_candidate = json.loads(str(row["candidate_json"] or "{}"))
            canonical = canonicalize_candidate(raw_candidate, row["category"])
            fingerprint = hashlib.sha256(_json(canonical).encode("utf-8")).hexdigest()
        except Exception:
            invalid += 1
            conn.execute(
                """
                UPDATE learned_pattern_candidates
                SET status='rejected', eligibility_reason='invalid_legacy_candidate',
                    review_eligible=0, promotion_eligible=0, last_evaluated_at=?
                WHERE id=?
                """,
                (now_value, candidate_id),
            )
            continue

        keeper_id = seen.get(fingerprint)
        if keeper_id is None:
            existing = _candidate_row_by_fingerprint(conn, fingerprint)
            if existing is not None and int(existing["id"]) != candidate_id:
                keeper_id = int(existing["id"])
            else:
                seen[fingerprint] = candidate_id
                already_current = (
                    str(row["candidate_fingerprint"] or "") == fingerprint
                    and str(row["candidate_json"] or "") == _json(canonical)
                    and str(row["category"] or "") == str(canonical.get("category") or "")
                )
                if not already_current:
                    conn.execute(
                        """
                        UPDATE learned_pattern_candidates
                        SET candidate_fingerprint=?, candidate_json=?, category=?, last_evaluated_at=?
                        WHERE id=?
                        """,
                        (fingerprint, _json(canonical), canonical.get("category"), now_value, candidate_id),
                    )
                    backfilled += 1
                continue

        keeper = conn.execute(
            "SELECT * FROM learned_pattern_candidates WHERE id=?",
            (keeper_id,),
        ).fetchone()
        if keeper is None:
            continue

        conn.execute(
            """
            UPDATE learned_pattern_candidates
            SET support_count=support_count+?,
                shadow_match_count=shadow_match_count+?,
                shadow_correct_count=shadow_correct_count+?,
                shadow_wrong_count=shadow_wrong_count+?,
                agreement_count=agreement_count+?,
                disagreement_count=disagreement_count+?,
                ground_truth_positive_count=ground_truth_positive_count+?,
                ground_truth_negative_count=ground_truth_negative_count+?,
                confidence=MAX(confidence, ?),
                last_seen_at=MAX(last_seen_at, ?),
                last_evaluated_at=?
            WHERE id=?
            """,
            (
                int(row["support_count"] or 0),
                int(row["shadow_match_count"] or 0),
                int(row["shadow_correct_count"] or 0),
                int(row["shadow_wrong_count"] or 0),
                int(row["agreement_count"] or 0),
                int(row["disagreement_count"] or 0),
                int(row["ground_truth_positive_count"] or 0),
                int(row["ground_truth_negative_count"] or 0),
                float(row["confidence"] or 0.0),
                str(row["last_seen_at"] or now_value),
                now_value,
                keeper_id,
            ),
        )
        conn.execute(
            """
            UPDATE learned_pattern_candidates
            SET candidate_fingerprint=NULL, status='merged_duplicate',
                merged_into_candidate_id=?, review_eligible=0, promotion_eligible=0,
                eligibility_reason='merged_duplicate', last_evaluated_at=?
            WHERE id=?
            """,
            (keeper_id, now_value, candidate_id),
        )
        merged += 1

    return {"backfilled": backfilled, "merged": merged, "invalid": invalid}


def ensure_schema(conn: sqlite3.Connection, now_value: str) -> Dict[str, Any]:
    """Install additive shadow lifecycle schema and backfill stable identities."""
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
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
        CREATE TABLE IF NOT EXISTS schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT NOT NULL
        );
        """
    )

    for definition in [
        "candidate_fingerprint TEXT",
        "agreement_count INTEGER NOT NULL DEFAULT 0",
        "disagreement_count INTEGER NOT NULL DEFAULT 0",
        "ground_truth_positive_count INTEGER NOT NULL DEFAULT 0",
        "ground_truth_negative_count INTEGER NOT NULL DEFAULT 0",
        "review_eligible INTEGER NOT NULL DEFAULT 0",
        "promotion_eligible INTEGER NOT NULL DEFAULT 0",
        "eligibility_reason TEXT",
        "last_evaluated_at TEXT",
        "merged_into_candidate_id INTEGER",
    ]:
        _ensure_column(conn, "learned_pattern_candidates", definition)

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS candidate_evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id INTEGER NOT NULL,
            candidate_fingerprint TEXT NOT NULL,
            message_key TEXT NOT NULL,
            evidence_type TEXT NOT NULL,
            matched INTEGER NOT NULL DEFAULT 0,
            candidate_category TEXT,
            reference_category TEXT,
            reference_source TEXT,
            reference_strength TEXT,
            agreement_status TEXT,
            is_ground_truth INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            UNIQUE(candidate_id, message_key, evidence_type)
        );
        CREATE INDEX IF NOT EXISTS idx_candidate_evidence_candidate_id
            ON candidate_evidence(candidate_id);
        CREATE INDEX IF NOT EXISTS idx_candidate_evidence_message_key
            ON candidate_evidence(message_key);
        CREATE INDEX IF NOT EXISTS idx_candidate_evidence_type
            ON candidate_evidence(evidence_type);
        """
    )

    migration = _merge_duplicate_rows(conn, now_value)

    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_learned_pattern_candidates_fingerprint
        ON learned_pattern_candidates(candidate_fingerprint)
        WHERE candidate_fingerprint IS NOT NULL
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_learned_pattern_candidates_status ON learned_pattern_candidates(status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_learned_pattern_candidates_review ON learned_pattern_candidates(review_eligible, promotion_eligible)"
    )
    conn.execute(
        "INSERT OR REPLACE INTO schema_meta(key, value, updated_at) VALUES (?, ?, ?)",
        ("candidate_lifecycle_shadow_schema", "1", now_value),
    )
    conn.execute(
        "INSERT OR REPLACE INTO schema_meta(key, value, updated_at) VALUES (?, ?, ?)",
        ("legacy_pattern_candidates_policy", "dormant_not_read_not_written", now_value),
    )

    for row in conn.execute(
        "SELECT id FROM learned_pattern_candidates WHERE candidate_fingerprint IS NOT NULL"
    ).fetchall():
        _refresh_candidate_state(conn, int(row["id"]), now_value)

    return {
        "ok": True,
        "marker": MARKER,
        "migration": migration,
        "legacy_pattern_candidates_policy": "dormant_not_read_not_written",
    }


def _reference_strength(
    rule_decision: Dict[str, Any],
    llm_result: Dict[str, Any],
    final_decision: Dict[str, Any],
) -> Dict[str, Any]:
    source = str(final_decision.get("final_source") or rule_decision.get("decision_source") or "unknown")
    rule_confidence = float(rule_decision.get("confidence") or 0.0)
    rule_category = str(rule_decision.get("category") or "")
    llm_category = str(llm_result.get("semantic_category") or "")

    if source.startswith("user_feedback"):
        return {"strength": "user_ground_truth", "is_ground_truth": True}
    if source == "rule_llm_agreement" and rule_category and rule_category == llm_category:
        return {"strength": "rule_llm_consensus", "is_ground_truth": False}
    if source.startswith("rule_") and rule_confidence >= 0.86:
        return {"strength": "high_confidence_rule", "is_ground_truth": False}
    if source.startswith("llm_"):
        return {"strength": "llm_only", "is_ground_truth": False}
    return {"strength": "weak_shadow_reference", "is_ground_truth": False}


def evaluate_existing_candidates(
    conn: sqlite3.Connection,
    features: Dict[str, Any],
    message_key: str,
    rule_decision: Dict[str, Any],
    llm_result: Dict[str, Any],
    final_decision: Dict[str, Any],
    now_value: str,
) -> Dict[str, Any]:
    """Match active candidates against a later message without affecting final decisions."""
    if email_safe_patterns is None:
        return {"ok": False, "error": "email_safe_patterns import failed", "matched": 0}

    rows = conn.execute(
        """
        SELECT * FROM learned_pattern_candidates
        WHERE status IN ('candidate', 'shadowing')
          AND candidate_fingerprint IS NOT NULL
        ORDER BY id
        """
    ).fetchall()

    reference_category = str(final_decision.get("final_category") or rule_decision.get("category") or "unknown_needs_llm")
    reference_source = str(final_decision.get("final_source") or rule_decision.get("decision_source") or "unknown")
    reference = _reference_strength(rule_decision, llm_result, final_decision)

    matched_ids: List[int] = []
    inserted_evidence = 0
    agreements = 0
    disagreements = 0

    for row in rows:
        candidate_id = int(row["id"])
        try:
            candidate = json.loads(str(row["candidate_json"] or "{}"))
        except Exception:
            continue
        if not email_safe_patterns.match_candidate(candidate, features):
            continue

        candidate_category = str(row["category"] or candidate.get("category") or "unknown_needs_llm")
        agreement_status = "agree" if candidate_category == reference_category else "disagree"

        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO candidate_evidence (
                candidate_id, candidate_fingerprint, message_key, evidence_type,
                matched, candidate_category, reference_category, reference_source,
                reference_strength, agreement_status, is_ground_truth, created_at
            )
            VALUES (?, ?, ?, 'shadow_match', 1, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate_id,
                row["candidate_fingerprint"],
                message_key,
                candidate_category,
                reference_category,
                reference_source,
                reference["strength"],
                agreement_status,
                int(reference["is_ground_truth"]),
                now_value,
            ),
        )

        if cursor.rowcount != 1:
            continue

        inserted_evidence += 1
        matched_ids.append(candidate_id)
        if agreement_status == "agree":
            agreements += 1
        else:
            disagreements += 1

        conn.execute(
            """
            UPDATE learned_pattern_candidates
            SET shadow_match_count=shadow_match_count+1,
                agreement_count=agreement_count+?,
                disagreement_count=disagreement_count+?,
                ground_truth_positive_count=ground_truth_positive_count+?,
                ground_truth_negative_count=ground_truth_negative_count+?,
                last_seen_at=?, last_evaluated_at=?
            WHERE id=?
            """,
            (
                int(agreement_status == "agree"),
                int(agreement_status == "disagree"),
                int(reference["is_ground_truth"] and agreement_status == "agree"),
                int(reference["is_ground_truth"] and agreement_status == "disagree"),
                now_value,
                now_value,
                candidate_id,
            ),
        )
        _refresh_candidate_state(conn, candidate_id, now_value)

    return {
        "ok": True,
        "evaluated": len(rows),
        "matched": len(matched_ids),
        "matched_candidate_ids": matched_ids,
        "inserted_evidence": inserted_evidence,
        "agreement_observations": agreements,
        "disagreement_observations": disagreements,
        "correct_wrong_counters_updated": False,
        "ground_truth_used": bool(reference["is_ground_truth"]),
        "reference_strength": reference["strength"],
        "runtime_decision_changed": False,
    }


def record_generated_candidate(
    conn: sqlite3.Connection,
    candidate: Dict[str, Any],
    message_key: str,
    llm_observation_id: Optional[int],
    fallback_category: str,
    fallback_confidence: float,
    now_value: str,
) -> Dict[str, Any]:
    """Upsert one candidate and count at most one generation evidence per message."""
    try:
        canonical = canonicalize_candidate(candidate, fallback_category)
    except Exception as exc:
        return {"ok": False, "error": repr(exc), "candidate_recorded": False}

    fingerprint = hashlib.sha256(_json(canonical).encode("utf-8")).hexdigest()
    category = str(canonical.get("category") or fallback_category or "unknown_needs_llm")
    confidence = max(0.0, min(1.0, float(candidate.get("confidence") or fallback_confidence or 0.0)))

    row = _candidate_row_by_fingerprint(conn, fingerprint)
    created = False
    if row is None:
        cursor = conn.execute(
            """
            INSERT INTO learned_pattern_candidates (
                source_message_key, source_llm_observation_id, candidate_json,
                candidate_fingerprint, category, confidence, support_count,
                shadow_match_count, shadow_correct_count, shadow_wrong_count,
                agreement_count, disagreement_count,
                ground_truth_positive_count, ground_truth_negative_count,
                review_eligible, promotion_eligible, eligibility_reason,
                status, created_at, last_seen_at, last_evaluated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                    'candidate_collecting_support', 'candidate', ?, ?, ?)
            """,
            (
                message_key,
                llm_observation_id,
                _json(canonical),
                fingerprint,
                category,
                confidence,
                now_value,
                now_value,
                now_value,
            ),
        )
        candidate_id = int(cursor.lastrowid)
        created = True
        row = conn.execute(
            "SELECT * FROM learned_pattern_candidates WHERE id=?",
            (candidate_id,),
        ).fetchone()
    else:
        candidate_id = int(row["id"])

    status = str(row["status"] or "candidate") if row is not None else "candidate"
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO candidate_evidence (
            candidate_id, candidate_fingerprint, message_key, evidence_type,
            matched, candidate_category, reference_category, reference_source,
            reference_strength, agreement_status, is_ground_truth, created_at
        )
        VALUES (?, ?, ?, 'generated', 1, ?, ?, 'llm_candidate_generation',
                'generation_evidence', 'self_generated', 0, ?)
        """,
        (
            candidate_id,
            fingerprint,
            message_key,
            category,
            category,
            now_value,
        ),
    )
    evidence_inserted = cursor.rowcount == 1

    support_incremented = False
    if evidence_inserted and status not in TERMINAL_STATUSES:
        conn.execute(
            """
            UPDATE learned_pattern_candidates
            SET support_count=support_count+1,
                confidence=MAX(confidence, ?),
                last_seen_at=?, last_evaluated_at=?
            WHERE id=?
            """,
            (confidence, now_value, now_value, candidate_id),
        )
        support_incremented = True

    state = _refresh_candidate_state(conn, candidate_id, now_value)
    final_row = conn.execute(
        "SELECT * FROM learned_pattern_candidates WHERE id=?",
        (candidate_id,),
    ).fetchone()

    return {
        "ok": True,
        "candidate_recorded": True,
        "candidate_id": candidate_id,
        "candidate_fingerprint": fingerprint,
        "created": created,
        "existing_status_before": status,
        "evidence_inserted": evidence_inserted,
        "support_incremented": support_incremented,
        "support_count": int(final_row["support_count"] or 0),
        "status": final_row["status"],
        "review_eligible": int(final_row["review_eligible"] or 0),
        "promotion_eligible": int(final_row["promotion_eligible"] or 0),
        "eligibility_reason": final_row["eligibility_reason"],
        "runtime_activation": False,
        "learned_category_rules_written": False,
        "state": state,
    }


def status(conn: sqlite3.Connection) -> Dict[str, Any]:
    conn.row_factory = sqlite3.Row
    tables = {
        str(row["name"])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    result: Dict[str, Any] = {
        "ok": True,
        "marker": MARKER,
        "legacy_pattern_candidates_policy": "dormant_not_read_not_written",
        "runtime_activation": False,
    }
    if "learned_pattern_candidates" in tables:
        result["candidate_status_counts"] = {
            str(row["status"]): int(row["n"])
            for row in conn.execute(
                "SELECT status, COUNT(*) AS n FROM learned_pattern_candidates GROUP BY status"
            ).fetchall()
        }
        result["review_eligible_count"] = int(conn.execute(
            "SELECT COUNT(*) FROM learned_pattern_candidates WHERE review_eligible=1"
        ).fetchone()[0])
        result["promotion_eligible_count"] = int(conn.execute(
            "SELECT COUNT(*) FROM learned_pattern_candidates WHERE promotion_eligible=1"
        ).fetchone()[0])
    if "candidate_evidence" in tables:
        result["evidence_count"] = int(conn.execute(
            "SELECT COUNT(*) FROM candidate_evidence"
        ).fetchone()[0])
    if "learned_category_rules" in tables:
        result["learned_category_rules_count"] = int(conn.execute(
            "SELECT COUNT(*) FROM learned_category_rules"
        ).fetchone()[0])
    if "pattern_candidates" in tables:
        result["legacy_pattern_candidates_count"] = int(conn.execute(
            "SELECT COUNT(*) FROM pattern_candidates"
        ).fetchone()[0])
    return result
