#!/usr/bin/env python3
"""Runtime configuration for Hermes Email Watchdog."""

import copy
import json
import os
import sys
from pathlib import Path

CONFIG_PATH = os.environ.get("EMAIL_WATCHDOG_CONFIG", "~/.hermes/email_watchdog_config.json")

_WARNED = False
_CACHE = None

DEFAULT_CONFIG = {
    "version": 1,
    "default_account": "ustc",
    "accounts": [
        {
            "id": "ustc",
            "label": "USTC",
            "name": "USTC",
            "type": "himalaya",
            "email": "wmwen@mail.ustc.edu.cn",
            "display_name": "wmwen",
            "himalaya_config": "~/.config/himalaya/config_ustc.toml",
            "config": "~/.config/himalaya/config_ustc.toml",
            "enabled": True,
        },
        {
            "id": "gmail",
            "label": "Gmail",
            "name": "Gmail",
            "type": "himalaya",
            "email": "wmwen1999@gmail.com",
            "display_name": "wmwen",
            "himalaya_config": "~/.config/himalaya/config_gmail.toml",
            "config": "~/.config/himalaya/config_gmail.toml",
            "enabled": True,
        },
        {
            "id": "agently",
            "label": "Agently",
            "name": "Agently",
            "type": "agently",
            "email": "augenstern@agent.qq.com",
            "display_name": "augenstern",
            "enabled": True,
        },
    ],
    "paths": {
        "db": "~/.hermes/email.db",
        "seen": "~/.hermes/email_watch_seen.json",
        "cache_dir": "~/.hermes/email_cache",
        "drafts_dir": "~/.hermes/email_drafts",
        "pending": "~/.hermes/email_pending.json",
        "threads": "~/.hermes/email_threads.json",
        "calendar": "~/.hermes/email_calendar.json",
        "contacts": "~/.hermes/email_contacts.json",
        "groups": "~/.hermes/email_groups.json",
        "settings": "~/.hermes/email_settings.json",
        "attachment_dir": "~/Documents/EmailAttachments",
        "invoice_dir": "~/Documents/Invoices",
    },
    "watchdog": {
        "lookback": 5,
        "sleep_start": 0,
        "sleep_end": 6,
        "max_cached": 200,
    },
    "llm": {
        "enabled": True,
        "endpoint": "https://api.llm.ustc.edu.cn/v1/chat/completions",
        "api_key_env": "USTC_LLM_API_KEY",
        "model": "deepseek-v4-flash",
        "temperature": 0.1,
        "max_tokens": 2000,
        "timeout_seconds": 90,
        "max_body_chars": 12000,
    },
    "delivery": {
        "auto_download_attachments": True,
        "create_reminders": True,
        "managed_cron": False,
        "timezone": "Asia/Shanghai",
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


def get_delivery_settings() -> dict:
    return load_config().get("delivery", {})


def get_account_emails() -> list:
    return [a.get("email", "") for a in get_accounts(False) if a.get("email")]
