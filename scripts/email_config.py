#!/usr/bin/env python3
"""Runtime configuration for Hermes Email Watchdog."""

import copy
import json
import os
import sys
from pathlib import Path

HERMES_HOME_RAW = os.environ.get("HERMES_HOME", "~/.hermes")
HERMES_HOME = Path(os.path.expandvars(os.path.expanduser(HERMES_HOME_RAW)))
STATE_ROOT_RAW = os.environ.get(
    "HERMES_EMAIL_WATCHDOG_STATE_ROOT",
    HERMES_HOME_RAW.rstrip("/\\") + "/plugin-data/hermes-email-watchdog",
)
STATE_ROOT = Path(
    os.path.expandvars(
        os.path.expanduser(STATE_ROOT_RAW)
    )
)
CONFIG_PATH = os.environ.get("EMAIL_WATCHDOG_CONFIG", str(STATE_ROOT / "config.json"))

_WARNED = False
_CACHE = None

DEFAULT_CONFIG = {
    "version": 3,
    "default_account": "",
    "accounts": [],
    "paths": {
        "db": STATE_ROOT_RAW.rstrip("/\\") + "/email.db",
        "seen": STATE_ROOT_RAW.rstrip("/\\") + "/seen.json",
        "cache_dir": STATE_ROOT_RAW.rstrip("/\\") + "/email_cache",
        "threads": STATE_ROOT_RAW.rstrip("/\\") + "/email_threads.json",
        "contacts": STATE_ROOT_RAW.rstrip("/\\") + "/email_contacts.json",
        "attachment_dir": "~/Documents/EmailAttachments",
    },
    "watchdog": {
        "lookback": 5,
        "sleep_start": 0,
        "sleep_end": 6,
        "max_cached": 200,
        "seen_max_entries": 5000,
    },
    "llm": {
        "enabled": False,
        "endpoint": "",
        "api_key_env": "",
        "model": "",
        "temperature": 0.1,
        "max_tokens": 2000,
        "timeout_seconds": 90,
        "max_body_chars": 12000,
    },
    # EMAIL_WATCHDOG_READABLE_GROUNDED_CORE_CONFIG_SHADOW_V1
    "semantic_engine": {
        "enabled": True,
        "mode": "shadow",
        "provider": "hermes_openai",
        "provider_name": "USTC",
        "endpoint": "",
        "api_key_env": "",
        "model": "deepseek-flash",
        "fallback_model": "qwen3.6-chat",
        "timeout_seconds": 120,
        "temperature": 0.0,
        "max_body_chars": 16000,
        "max_parallel": 1,
        "cache_by_message_hash": True,
        "protocol": "readable_grounded_core_v1v",
        "num_thread": 5,
        # EMAIL_WATCHDOG_ADAPTIVE_OUTPUT_BUDGET_CONFIG_V1
        "num_predict_mode": "adaptive",
        "num_predict": 1800,
        "num_predict_simple": 700,
        "num_predict_standard": 1200,
        "num_predict_complex": 1800,
        "num_predict_hard_cap": 2048,
    },
    # EMAIL_WATCHDOG_ADAPTIVE_RENDERER_CONFIG_SHADOW_V1
    "notification": {
        "renderer": "adaptive_v1g",
        "mode": "production",
        "production_route_enabled": True,
        "all_mail_push": False,
        "legacy_fallback_enabled": True,
        # Every production semantic decision is made by the configured model.
        # Deterministic extraction remains a grounding/safety layer, not a
        # parallel classifier that silently bypasses deepseek-flash.
        "fast_lane_enabled": False,
        "original_policy": "auto",
        "original_max_chars": 900,
        "show_priority": True,
        "show_category": True,
        "show_time": True,
        "show_debug_reason": False,
        "suppress_redundant_summary": True,
    },
    # EMAIL_WATCHDOG_SEMANTIC_MEMORY_CONFIG_SHADOW_V1
    "semantic_memory": {
        "enabled": True,
        "mode": "shadow",
        "max_examples": 5,
        "max_evidence_keys": 200,
        "learn_from_user_feedback_only": True,
        "runtime_activation": False,
    },
    "delivery": {
        "auto_download_attachments": True,
        "forward_attachments_to_weixin": True,
        "auto_forward_safe_attachments": True,
        "attachment_max_bytes": 25 * 1024 * 1024,
        "attachment_safe_extensions": [
            ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp",
            ".txt", ".csv", ".doc", ".docx", ".xls", ".xlsx",
            ".ppt", ".pptx", ".zip", ".7z",
        ],
        "create_reminders": True,
        "managed_cron": True,
        "reminder_offsets_minutes": [1440, 60],
        "calendar_path": "~/Documents/EmailAttachments/email-watchdog-calendar.ics",
        "timezone": "auto",
        "target": {
            "platform": "",
            "chat_id": "",
            "thread_id": "",
            "chat_type": "",
        },
    },
    # Immutable release safety boundary. User config cannot override these.
    "safety": {
        "mailbox_read_only": True,
        "outbound_email_enabled": False,
        "mailbox_mutation_enabled": False,
    },
}


def _expand(value):
    if isinstance(value, str):
        return os.path.expandvars(os.path.expanduser(value))
    return value


def _deep_merge(base, override):
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _normalize_account(account):
    acct = dict(account)
    acct.setdefault("id", str(acct.get("label") or acct.get("name") or acct.get("email") or "account").lower())
    acct.setdefault("label", acct.get("name") or acct["id"])
    acct.setdefault("name", acct.get("label") or acct["id"])
    acct.setdefault("enabled", True)
    if acct.get("himalaya_config") and not acct.get("config"):
        acct["config"] = acct["himalaya_config"]
    if acct.get("config") and not acct.get("himalaya_config"):
        acct["himalaya_config"] = acct["config"]
    for key in ("config", "himalaya_config"):
        if acct.get(key):
            acct[key] = _expand(acct[key])
    acct.setdefault("display_name", acct.get("name") or acct.get("label") or acct.get("email", ""))
    return acct


def load_config() -> dict:
    global _CACHE, _WARNED
    if _CACHE is not None:
        return copy.deepcopy(_CACHE)

    path = _expand(CONFIG_PATH)
    data = {}
    if os.path.exists(path):
        try:
            with open(path) as f:
                data = json.load(f)
        except Exception as exc:
            if not _WARNED:
                print(f"email_config: failed to read {path}: {exc}; using defaults", file=sys.stderr)
                _WARNED = True
    elif not _WARNED:
        print(f"email_config: {path} not found; using built-in defaults", file=sys.stderr)
        _WARNED = True

    cfg = _deep_merge(DEFAULT_CONFIG, data)
    # Safety settings are immutable for the read-only release candidate.
    cfg["safety"] = copy.deepcopy(DEFAULT_CONFIG["safety"])
    cfg["accounts"] = [_normalize_account(a) for a in cfg.get("accounts", [])]
    cfg["paths"] = {k: _expand(v) for k, v in cfg.get("paths", {}).items()}
    _CACHE = cfg
    return copy.deepcopy(_CACHE)


def reset_cache():
    global _CACHE
    _CACHE = None


def get_accounts(enabled_only=True) -> list:
    accounts = load_config().get("accounts", [])
    return [a for a in accounts if a.get("enabled", True)] if enabled_only else accounts


def get_account_map() -> dict:
    result = {}
    for acct in get_accounts(False):
        for key in {acct.get("id"), acct.get("label"), acct.get("name")}:
            if key:
                result[str(key).lower()] = acct
    return result


def get_default_account() -> dict:
    cfg = load_config()
    default_id = str(cfg.get("default_account") or "").lower()
    amap = get_account_map()
    return amap.get(default_id) or (get_accounts(True)[0] if get_accounts(True) else {})


def get_path(name: str) -> str:
    return load_config().get("paths", {}).get(name, "")


def get_watchdog_settings() -> dict:
    return load_config().get("watchdog", {})


def get_llm_settings() -> dict:
    return load_config().get("llm", {})


def get_semantic_engine_settings() -> dict:
    """Return additive LLM-first semantic shadow settings."""
    return load_config().get("semantic_engine", {})


def get_notification_settings() -> dict:
    """Return additive Adaptive Renderer shadow settings."""
    return load_config().get("notification", {})


def get_semantic_memory_settings() -> dict:
    """Return additive semantic-memory shadow settings."""
    return load_config().get("semantic_memory", {})


def get_delivery_settings() -> dict:
    return load_config().get("delivery", {})


def get_safety_settings() -> dict:
    return load_config().get("safety", {})


def get_account_emails() -> list:
    return [a.get("email", "") for a in get_accounts(False) if a.get("email")]
