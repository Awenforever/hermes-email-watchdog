#!/usr/bin/env python3
"""Semantic-memory shadow store for Hermes Email Watchdog.

Phase 3 records bounded, privacy-conscious semantic observations and explicit user
feedback. Model observations are never ground truth and are not used by the live
semantic prompt in this phase.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from email.utils import parseaddr
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional

try:
    import email_config
except Exception:
    email_config = None

MARKER = "EMAIL_WATCHDOG_SEMANTIC_MEMORY_SHADOW_V1"
SCHEMA_VERSION = 1
DEFAULT_DB_PATH = Path(
    os.environ.get(
        "EMAIL_LEARNING_DB",
        "/opt/data/.hermes-home/.hermes/email_learning/email_learning.sqlite",
    )
)

_ALLOWED_FEEDBACK = {
    "category_correction",
    "importance_correction",
    "content_mode_preference",
    "original_policy_preference",
    "sender_preference",
    "topic_preference",
    "other",
}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _safe_error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}".replace("\n", " ").replace("\r", " ")[:500]


def _sha(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8", "replace")).hexdigest()


def _text(value: Any, limit: int = 300) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        value = _json(value)
    return re.sub(r"\s+", " ", str(value)).strip()[:limit]


def _settings(override: Mapping[str, Any] | None = None) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "enabled": True,
        "mode": "shadow",
        "max_examples": 5,
        "max_evidence_keys": 200,
        "learn_from_user_feedback_only": True,
        "runtime_activation": False,
    }
    if email_config is not None and hasattr(email_config, "get_semantic_memory_settings"):
        try:
            result.update(email_config.get_semantic_memory_settings() or {})
        except Exception:
            pass
    if override:
        result.update(dict(override))
    result["enabled"] = bool(result.get("enabled", True))
    result["mode"] = str(result.get("mode") or "shadow").strip().lower()
    result["max_examples"] = max(1, min(20, int(result.get("max_examples") or 5)))
    result["max_evidence_keys"] = max(10, min(500, int(result.get("max_evidence_keys") or 200)))
    result["learn_from_user_feedback_only"] = bool(result.get("learn_from_user_feedback_only", True))
    # Phase 3 hard boundary. Config cannot activate runtime use.
    result["runtime_activation"] = False
    return result


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def ensure_schema(db_path: Path | str = DEFAULT_DB_PATH) -> Dict[str, Any]:
    path = Path(db_path)
    conn: Optional[sqlite3.Connection] = None
    try:
        conn = _connect(path)
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS semantic_feedback (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              message_key TEXT NOT NULL,
              feedback_type TEXT NOT NULL,
              old_value TEXT,
              new_value TEXT,
              note TEXT,
              is_ground_truth INTEGER NOT NULL DEFAULT 1,
              memory_type TEXT,
              scope_key TEXT,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS semantic_memories (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              memory_type TEXT NOT NULL,
              scope_key TEXT NOT NULL,
              memory_json TEXT NOT NULL,
              confidence REAL NOT NULL DEFAULT 0,
              positive_count INTEGER NOT NULL DEFAULT 0,
              negative_count INTEGER NOT NULL DEFAULT 0,
              observation_count INTEGER NOT NULL DEFAULT 0,
              ground_truth_count INTEGER NOT NULL DEFAULT 0,
              status TEXT NOT NULL DEFAULT 'shadow',
              source TEXT NOT NULL DEFAULT 'model_observation',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE(memory_type, scope_key)
            );

            CREATE INDEX IF NOT EXISTS idx_semantic_feedback_message
              ON semantic_feedback(message_key);
            CREATE INDEX IF NOT EXISTS idx_semantic_feedback_type
              ON semantic_feedback(feedback_type);
            CREATE INDEX IF NOT EXISTS idx_semantic_memories_type_status
              ON semantic_memories(memory_type, status);
            CREATE INDEX IF NOT EXISTS idx_semantic_memories_updated
              ON semantic_memories(updated_at);
            """
        )
        existing_feedback = {row[1] for row in conn.execute("PRAGMA table_info(semantic_feedback)")}
        feedback_additive = {
            "is_ground_truth": "INTEGER NOT NULL DEFAULT 1",
            "memory_type": "TEXT",
            "scope_key": "TEXT",
        }
        for name, definition in feedback_additive.items():
            if name not in existing_feedback:
                conn.execute(f"ALTER TABLE semantic_feedback ADD COLUMN {name} {definition}")
        existing_mem = {row[1] for row in conn.execute("PRAGMA table_info(semantic_memories)")}
        memory_additive = {
            "observation_count": "INTEGER NOT NULL DEFAULT 0",
            "ground_truth_count": "INTEGER NOT NULL DEFAULT 0",
            "source": "TEXT NOT NULL DEFAULT 'model_observation'",
        }
        for name, definition in memory_additive.items():
            if name not in existing_mem:
                conn.execute(f"ALTER TABLE semantic_memories ADD COLUMN {name} {definition}")
        conn.commit()
        return {"ok": True, "db_path": str(path), "tables": ["semantic_feedback", "semantic_memories"]}
    except Exception as exc:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return {"ok": False, "db_path": str(path), "error": _safe_error(exc)}
    finally:
        if conn is not None:
            conn.close()


def _sender_identity(email: Mapping[str, Any]) -> Dict[str, str]:
    raw = (
        email.get("from")
        or email.get("from_address")
        or email.get("sender")
        or email.get("sender_email")
        or ""
    )
    name, addr = parseaddr(str(raw))
    addr = addr.strip().lower()
    if not addr and "@" in str(raw):
        addr = str(raw).strip().lower()
    domain = addr.rsplit("@", 1)[1] if "@" in addr else ""
    return {
        "sender_hash": _sha(addr) if addr else "",
        "sender_name": _text(name or email.get("from_name") or "", 120),
        "domain": domain[:200],
    }


def _normalize_tag(value: Any) -> str:
    text = _text(value, 80).casefold()
    text = re.sub(r"[^\w\-\u4e00-\u9fff ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:80]


def _bounded_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _text(value, 500)
    if isinstance(value, Mapping):
        result: Dict[str, Any] = {}
        for key in sorted(value)[:20]:
            result[_text(key, 80)] = _bounded_value(value[key])
        return result
    if isinstance(value, (list, tuple)):
        return [_bounded_value(item) for item in list(value)[:20]]
    return _text(value, 500)


def _safe_model_candidate(value: Any) -> Dict[str, Any]:
    """Persist only a non-reconstructable review marker for untrusted model candidates."""
    bounded = _bounded_value(value)
    safe: Dict[str, Any] = {
        "candidate_hash": _sha(_json(bounded)),
        "candidate_kind": "mapping" if isinstance(value, Mapping) else "text",
        "review_required": True,
    }
    if isinstance(value, Mapping):
        allowed = {"content_mode", "original_policy", "importance", "category", "should_notify"}
        safe["structured_hints"] = {
            str(key): _bounded_value(value[key])
            for key in sorted(value)
            if str(key) in allowed
        }
    return safe


def _memory_payload(decision: Mapping[str, Any], renderer: Mapping[str, Any] | None) -> Dict[str, Any]:
    classification = decision.get("classification") if isinstance(decision.get("classification"), Mapping) else {}
    importance = decision.get("importance") if isinstance(decision.get("importance"), Mapping) else {}
    notification = decision.get("notification") if isinstance(decision.get("notification"), Mapping) else {}
    render = renderer.get("render") if isinstance(renderer, Mapping) and isinstance(renderer.get("render"), Mapping) else {}
    return {
        "category": _text(classification.get("category"), 120),
        "importance": _text(importance.get("level"), 40),
        "content_mode": _text(notification.get("content_mode") or render.get("content_mode"), 80),
        "original_policy": _text(notification.get("original_policy"), 40),
        "should_notify": bool(notification.get("should_notify", True)),
    }


def _upsert_model_observation(
    conn: sqlite3.Connection,
    *,
    memory_type: str,
    scope_key: str,
    value: Any,
    message_key: str,
    max_evidence_keys: int,
) -> bool:
    if not memory_type or not scope_key:
        return False
    now = _now()
    message_hash = _sha(message_key)
    bounded = _bounded_value(value)
    value_hash = _sha(_json(bounded))
    row = conn.execute(
        "SELECT * FROM semantic_memories WHERE memory_type=? AND scope_key=?",
        (memory_type, scope_key),
    ).fetchone()
    created = now
    doc: Dict[str, Any] = {
        "runtime_activation": False,
        "ground_truth": False,
        "latest": bounded,
        "value_counts": {},
        "evidence_message_hashes": [],
    }
    observation_count = 0
    positive_count = 0
    negative_count = 0
    ground_truth_count = 0
    if row is not None:
        # Explicit user feedback is authoritative. Model observations may never
        # downgrade or overwrite a confirmed memory.
        if str(row["status"] or "") == "confirmed" or str(row["source"] or "") == "explicit_user_feedback":
            return False
        created = str(row["created_at"] or now)
        observation_count = int(row["observation_count"] or 0)
        positive_count = int(row["positive_count"] or 0)
        negative_count = int(row["negative_count"] or 0)
        ground_truth_count = int(row["ground_truth_count"] or 0)
        try:
            loaded = json.loads(str(row["memory_json"] or "{}"))
            if isinstance(loaded, dict):
                doc.update(loaded)
        except Exception:
            pass
    evidence = [str(item) for item in (doc.get("evidence_message_hashes") or []) if item]
    duplicate = message_hash in evidence
    if not duplicate:
        evidence.append(message_hash)
        evidence = evidence[-max_evidence_keys:]
        observation_count += 1
        counts = doc.get("value_counts") if isinstance(doc.get("value_counts"), dict) else {}
        entry = counts.get(value_hash) if isinstance(counts.get(value_hash), dict) else {"value": bounded, "count": 0}
        entry["value"] = bounded
        entry["count"] = int(entry.get("count") or 0) + 1
        counts[value_hash] = entry
        if len(counts) > 10:
            ordered = sorted(counts.items(), key=lambda item: int((item[1] or {}).get("count") or 0), reverse=True)[:10]
            counts = dict(ordered)
        doc["value_counts"] = counts
    doc["latest"] = bounded
    doc["evidence_message_hashes"] = evidence
    doc["runtime_activation"] = False
    doc["ground_truth"] = False
    conn.execute(
        """
        INSERT INTO semantic_memories (
          memory_type, scope_key, memory_json, confidence, positive_count,
          negative_count, observation_count, ground_truth_count, status,
          source, created_at, updated_at
        ) VALUES (?, ?, ?, 0, ?, ?, ?, ?, 'shadow', 'model_observation', ?, ?)
        ON CONFLICT(memory_type, scope_key) DO UPDATE SET
          memory_json=excluded.memory_json,
          confidence=0,
          positive_count=excluded.positive_count,
          negative_count=excluded.negative_count,
          observation_count=excluded.observation_count,
          ground_truth_count=excluded.ground_truth_count,
          status='shadow',
          source='model_observation',
          updated_at=excluded.updated_at
        """,
        (
            memory_type,
            scope_key,
            _json(doc),
            positive_count,
            negative_count,
            observation_count,
            ground_truth_count,
            created,
            now,
        ),
    )
    return not duplicate


def shadow_observe(
    email: Mapping[str, Any],
    decision: Mapping[str, Any],
    account: Mapping[str, Any] | None = None,
    *,
    message_key: str,
    renderer: Mapping[str, Any] | None = None,
    semantic_meta: Mapping[str, Any] | None = None,
    db_path: Path | str | None = None,
    settings_override: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Record model-derived memory candidates without activating them."""
    settings = _settings(settings_override)
    if not settings["enabled"]:
        return {"ok": True, "skipped": True, "reason": "semantic memory disabled", "runtime_activation": False}
    if settings["mode"] != "shadow":
        return {"ok": True, "skipped": True, "reason": "phase3 requires shadow mode", "runtime_activation": False}
    if not isinstance(decision, Mapping) or not message_key:
        return {"ok": False, "error": "decision and message_key are required", "runtime_activation": False}
    schema = ensure_schema(db_path or DEFAULT_DB_PATH)
    if not schema.get("ok"):
        return {**schema, "runtime_activation": False}

    identity = _sender_identity(email)
    memory = decision.get("memory_observation") if isinstance(decision.get("memory_observation"), Mapping) else {}
    base = _memory_payload(decision, renderer)
    observations: list[tuple[str, str, Any]] = []
    if identity["sender_hash"]:
        observations.append(("sender_semantics", identity["sender_hash"], {**base, "sender_name": identity["sender_name"]}))
        observations.append(("display_mode_sender", identity["sender_hash"], {
            "content_mode": base.get("content_mode"),
            "original_policy": base.get("original_policy"),
        }))
    if identity["domain"]:
        observations.append(("domain_semantics", identity["domain"], base))
    for tag in memory.get("topic_tags") or []:
        normalized = _normalize_tag(tag)
        if normalized:
            observations.append(("topic_semantics", normalized, base))
    sender_candidate = memory.get("sender_preference_candidate")
    if sender_candidate not in (None, "", {}):
        observations.append(("model_sender_preference_candidate", identity["sender_hash"] or _sha(_json(sender_candidate)), _safe_model_candidate(sender_candidate)))
    user_candidate = memory.get("user_preference_candidate")
    if user_candidate not in (None, "", {}):
        observations.append(("model_user_preference_candidate", _sha(_json(user_candidate)), _safe_model_candidate(user_candidate)))

    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    conn: Optional[sqlite3.Connection] = None
    inserted = 0
    try:
        conn = _connect(path)
        conn.execute("BEGIN IMMEDIATE")
        for memory_type, scope_key, value in observations:
            if _upsert_model_observation(
                conn,
                memory_type=memory_type,
                scope_key=scope_key,
                value=value,
                message_key=message_key,
                max_evidence_keys=settings["max_evidence_keys"],
            ):
                inserted += 1
        conn.commit()
        return {
            "ok": True,
            "observation_count": len(observations),
            "new_evidence_count": inserted,
            "runtime_activation": False,
            "ground_truth_written": False,
            "learned_category_rules_written": False,
            "candidate_promotion_executed": False,
            "sender_scope_hashed": bool(identity["sender_hash"]),
        }
    except Exception as exc:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return {"ok": False, "error": _safe_error(exc), "runtime_activation": False}
    finally:
        if conn is not None:
            conn.close()


def record_feedback(
    message_key: str,
    feedback_type: str,
    *,
    old_value: Any = "",
    new_value: Any = "",
    note: str = "",
    memory_type: str = "",
    scope_key: str = "",
    explicit_user_feedback: bool = False,
    db_path: Path | str | None = None,
) -> Dict[str, Any]:
    """Store strong ground truth only for explicitly confirmed user feedback."""
    if not explicit_user_feedback:
        return {"ok": False, "error": "explicit_user_feedback=true is required", "ground_truth_written": False}
    feedback_type = str(feedback_type or "").strip().lower()
    if feedback_type not in _ALLOWED_FEEDBACK:
        return {"ok": False, "error": "unsupported feedback_type", "ground_truth_written": False}
    if not message_key:
        return {"ok": False, "error": "message_key is required", "ground_truth_written": False}
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    schema = ensure_schema(path)
    if not schema.get("ok"):
        return {**schema, "ground_truth_written": False}
    conn: Optional[sqlite3.Connection] = None
    now = _now()
    try:
        conn = _connect(path)
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            INSERT INTO semantic_feedback (
              message_key, feedback_type, old_value, new_value, note,
              is_ground_truth, memory_type, scope_key, created_at
            ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
            """,
            (
                message_key,
                feedback_type,
                _text(old_value, 1000),
                _text(new_value, 1000),
                _text(note, 1000),
                _text(memory_type, 80),
                _text(scope_key, 240),
                now,
            ),
        )
        memory_updated = False
        if memory_type and scope_key and new_value not in (None, ""):
            row = conn.execute(
                "SELECT * FROM semantic_memories WHERE memory_type=? AND scope_key=?",
                (memory_type, scope_key),
            ).fetchone()
            created = str(row["created_at"] or now) if row else now
            positive = int(row["positive_count"] or 0) + 1 if row else 1
            negative = int(row["negative_count"] or 0) if row else 0
            observations = int(row["observation_count"] or 0) if row else 0
            ground_truth = int(row["ground_truth_count"] or 0) + 1 if row else 1
            doc = {
                "runtime_activation": False,
                "ground_truth": True,
                "confirmed_value": _bounded_value(new_value),
                "feedback_type": feedback_type,
                "last_message_hash": _sha(message_key),
            }
            conn.execute(
                """
                INSERT INTO semantic_memories (
                  memory_type, scope_key, memory_json, confidence, positive_count,
                  negative_count, observation_count, ground_truth_count, status,
                  source, created_at, updated_at
                ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, 'confirmed', 'explicit_user_feedback', ?, ?)
                ON CONFLICT(memory_type, scope_key) DO UPDATE SET
                  memory_json=excluded.memory_json,
                  confidence=1,
                  positive_count=excluded.positive_count,
                  negative_count=excluded.negative_count,
                  observation_count=excluded.observation_count,
                  ground_truth_count=excluded.ground_truth_count,
                  status='confirmed',
                  source='explicit_user_feedback',
                  updated_at=excluded.updated_at
                """,
                (memory_type, scope_key, _json(doc), positive, negative, observations, ground_truth, created, now),
            )
            memory_updated = True
        conn.commit()
        return {
            "ok": True,
            "ground_truth_written": True,
            "memory_updated": memory_updated,
            "runtime_activation": False,
        }
    except Exception as exc:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return {"ok": False, "error": _safe_error(exc), "ground_truth_written": False}
    finally:
        if conn is not None:
            conn.close()


def retrieve_confirmed(
    *,
    memory_type: str = "",
    scope_keys: Iterable[str] | None = None,
    limit: int = 5,
    db_path: Path | str | None = None,
) -> Dict[str, Any]:
    """Bounded preview retrieval. It is intentionally not wired into Ollama prompts."""
    limit = max(1, min(20, int(limit or 5)))
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    if not path.exists():
        return {"ok": True, "items": [], "runtime_activation": False}
    keys = [str(item) for item in (scope_keys or []) if str(item)]
    conn: Optional[sqlite3.Connection] = None
    try:
        conn = sqlite3.connect(str(path), timeout=5)
        conn.row_factory = sqlite3.Row
        sql = "SELECT memory_type, scope_key, memory_json, confidence, status, source, updated_at FROM semantic_memories WHERE status='confirmed' AND source='explicit_user_feedback'"
        params: list[Any] = []
        if memory_type:
            sql += " AND memory_type=?"
            params.append(memory_type)
        if keys:
            sql += " AND scope_key IN (" + ",".join("?" for _ in keys) + ")"
            params.extend(keys)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return {
            "ok": True,
            "items": [
                {
                    "memory_type": row["memory_type"],
                    "scope_key": row["scope_key"],
                    "memory": json.loads(row["memory_json"]),
                    "confidence": row["confidence"],
                    "status": row["status"],
                    "source": row["source"],
                }
                for row in rows
            ],
            "runtime_activation": False,
        }
    except Exception as exc:
        return {"ok": False, "items": [], "error": _safe_error(exc), "runtime_activation": False}
    finally:
        if conn is not None:
            conn.close()


def prompt_examples(*args: Any, **kwargs: Any) -> list[Any]:
    """Phase 3 hard stop: semantic memory is not injected into prompts."""
    return []


def status(db_path: Path | str | None = None) -> Dict[str, Any]:
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    result: Dict[str, Any] = {
        "marker": MARKER,
        "schema_version": SCHEMA_VERSION,
        "db_path": str(path),
        "runtime_activation": False,
        "candidate_promotion_executed": False,
        "learned_category_rules_written": False,
        "tables": {},
    }
    if not path.exists():
        return result
    conn: Optional[sqlite3.Connection] = None
    try:
        conn = sqlite3.connect(str(path), timeout=5)
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for name in ("semantic_feedback", "semantic_memories"):
            result["tables"][name] = int(conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]) if name in tables else None
        if "semantic_memories" in tables:
            result["memory_status_counts"] = {
                row[0]: int(row[1])
                for row in conn.execute("SELECT status, COUNT(*) FROM semantic_memories GROUP BY status")
            }
        return result
    except Exception as exc:
        result["error"] = _safe_error(exc)
        return result
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    print(json.dumps(status(), ensure_ascii=False, indent=2, sort_keys=True))
