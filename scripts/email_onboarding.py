#!/usr/bin/env python3
"""Repository-owned, non-interactive onboarding for Hermes Email Watchdog.

All commands are JSON-in / JSON-out. The module never opens an interactive
terminal questionnaire, never accepts a mailbox password value, and never
performs a mailbox write. Validation is limited to a one-envelope Himalaya list
request.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import email_config
from portable_lock import acquire_file_lock, release_file_lock

HERMES_HOME = Path(
    os.path.expandvars(
        os.path.expanduser(os.environ.get("HERMES_HOME", "~/.hermes"))
    )
)
STATE_ROOT = Path(
    os.path.expandvars(
        os.path.expanduser(
            os.environ.get(
                "HERMES_EMAIL_WATCHDOG_STATE_ROOT",
                str(HERMES_HOME / "plugin-data" / "hermes-email-watchdog"),
            )
        )
    )
)
CONFIG_PATH = Path(
    os.path.expandvars(
        os.path.expanduser(
            os.environ.get(
                "EMAIL_WATCHDOG_CONFIG",
                str(STATE_ROOT / "config.json"),
            )
        )
    )
)
ENABLED_FILE = Path(
    os.path.expandvars(
        os.path.expanduser(
            os.environ.get(
                "HERMES_EMAIL_WATCHDOG_ENABLED_FILE",
                str(STATE_ROOT / "enabled"),
            )
        )
    )
)
ONBOARDING_FILE = Path(
    os.path.expandvars(
        os.path.expanduser(
            os.environ.get(
                "HERMES_EMAIL_WATCHDOG_ONBOARDING_FILE",
                str(STATE_ROOT / "onboarding.json"),
            )
        )
    )
)
BACKUP_DIR = Path(
    os.path.expandvars(
        os.path.expanduser(
            os.environ.get(
                "HERMES_EMAIL_WATCHDOG_ONBOARDING_BACKUP_DIR",
                str(STATE_ROOT / "onboarding-backups"),
            )
        )
    )
)
OWNED_HIMALAYA_DIR = Path(
    os.path.expandvars(
        os.path.expanduser(
            os.environ.get(
                "HERMES_EMAIL_WATCHDOG_OWNED_HIMALAYA_DIR",
                str(STATE_ROOT / "himalaya"),
            )
        )
    )
)

# EMAIL_WATCHDOG_ONBOARDING_TRANSACTION_LOCK_V1
TRANSACTION_LOCK_FILE = Path(
    os.path.expandvars(
        os.path.expanduser(
            os.environ.get(
                "HERMES_EMAIL_WATCHDOG_ONBOARDING_LOCK_FILE",
                str(STATE_ROOT / "onboarding.lock"),
            )
        )
    )
)


@contextlib.contextmanager
def _transaction_lock():
    """Serialize setup state changes across concurrent CLI processes."""
    TRANSACTION_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(TRANSACTION_LOCK_FILE, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.chmod(TRANSACTION_LOCK_FILE, 0o600)
        acquire_file_lock(fd)
        yield
    finally:
        try:
            release_file_lock(fd)
        finally:
            os.close(fd)


ALLOWED_PATH_KEYS = {
    "db",
    "seen",
    "cache_dir",
    "threads",
    "contacts",
    "attachment_dir",
}
ALLOWED_TOP_LEVEL = {
    "version",
    "default_account",
    "accounts",
    "paths",
    "watchdog",
    "llm",
    "semantic_engine",
    "notification",
    "semantic_memory",
    "delivery",
    "safety",
}
ALLOWED_SECRET_COMMAND_PREFIXES = {
    "pass",
    "gopass",
    "secret-tool",
    "security",
    "op",
    "bw",
    "printenv",
}
FORBIDDEN_SECRET_KEYS = {
    "password",
    "password_value",
    "secret",
    "secret_value",
    "token",
    "token_value",
    "app_password",
}


class OnboardingError(RuntimeError):
    pass


@dataclass
class PlannedHimalayaFile:
    path: Path
    content: str
    account_id: str


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _canonical_sha(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _atomic_write_text(path: Path, text: str, mode: int = 0o600) -> None:
    previous = None
    try:
        previous = path.stat()
    except FileNotFoundError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    tmp = Path(raw_tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if previous is not None and hasattr(os, "chown") and getattr(os, "geteuid", lambda: -1)() == 0:
            os.chown(tmp, previous.st_uid, previous.st_gid)
        os.chmod(tmp, (previous.st_mode & 0o777) if previous is not None else mode)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    _atomic_write_text(
        path,
        json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        0o600,
    )


def _stage_text(path: Path, text: str, mode: int = 0o600) -> Path:
    """Create a durable sibling staging file without publishing it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_tmp = tempfile.mkstemp(
        prefix=f".{path.name}.stage.",
        dir=str(path.parent),
    )
    tmp = Path(raw_tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        return tmp
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _validation_config_for_staged_himalaya(
    config: dict[str, Any],
    generated: PlannedHimalayaFile,
    staged_path: Path,
) -> dict[str, Any]:
    candidate = copy.deepcopy(config)
    final_path = str(generated.path)
    for account in candidate.get("accounts", []):
        if not isinstance(account, dict):
            continue
        for key in ("config", "himalaya_config"):
            if str(account.get(key) or "") == final_path:
                account[key] = str(staged_path)
    return candidate


def _load_json(path: Path, default: Any) -> Any:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value
    except Exception:
        return copy.deepcopy(default)


def _read_enabled() -> bool:
    try:
        return ENABLED_FILE.read_text(encoding="utf-8").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
    except Exception:
        return False


def _write_enabled(value: bool) -> None:
    _atomic_write_text(ENABLED_FILE, "true\n" if value else "false\n", 0o600)


def _deep_merge(base: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _migrate_known_v2_assistant_policy(
    data: dict[str, Any] | None,
) -> tuple[dict[str, Any], bool]:
    """Upgrade only the exact v0.2.x default policy, preserving every other key."""
    source = copy.deepcopy(data) if isinstance(data, dict) else {}
    try:
        source_version = int(source.get("version") or 0)
    except (TypeError, ValueError):
        source_version = 0
    semantic = source.get("semantic_engine") if isinstance(source.get("semantic_engine"), dict) else {}
    notification = source.get("notification") if isinstance(source.get("notification"), dict) else {}
    delivery = source.get("delivery") if isinstance(source.get("delivery"), dict) else {}
    known_v2_signature = bool(
        source_version == 2
        and str(semantic.get("model") or "") == "deepseek-flash"
        and str(semantic.get("fallback_model") or "") == "qwen3.6-chat"
        and str(semantic.get("protocol") or "") == "readable_grounded_core_v1u"
        and notification.get("fast_lane_enabled") is True
        and delivery.get("auto_download_attachments") is False
    )
    if not known_v2_signature:
        return source, False

    semantic["protocol"] = "readable_grounded_core_v1x"
    semantic["num_predict_simple"] = 700
    semantic["num_predict_standard"] = 1200
    semantic["num_predict_complex"] = 1800
    semantic["num_predict_hard_cap"] = 2048
    notification["fast_lane_enabled"] = False
    delivery["auto_download_attachments"] = True
    delivery["forward_attachments_to_weixin"] = True
    delivery.setdefault("attachment_max_bytes", 25 * 1024 * 1024)
    delivery.setdefault(
        "attachment_safe_extensions",
        copy.deepcopy(email_config.DEFAULT_CONFIG["delivery"]["attachment_safe_extensions"]),
    )
    source["semantic_engine"] = semantic
    source["notification"] = notification
    source["delivery"] = delivery
    source["version"] = 3
    return source, True


def _migrate_known_v3_assistant_policy(
    data: dict[str, Any] | None,
) -> tuple[dict[str, Any], bool]:
    """Upgrade only the shipped v0.3.x assistant policy signature."""
    source = copy.deepcopy(data) if isinstance(data, dict) else {}
    notification = source.get("notification") if isinstance(source.get("notification"), dict) else {}
    delivery = source.get("delivery") if isinstance(source.get("delivery"), dict) else {}
    known_v3_signature = bool(
        str(notification.get("renderer") or "") == "adaptive_v1f"
        and int(notification.get("original_max_chars") or 0) == 5000
        # Some v0.3.1 installations enabled reminder extraction manually while
        # still retaining the shipped plain renderer and disabled scheduler.
        # Both boolean values remain the known release family; non-booleans are
        # treated as customization and preserved.
        and isinstance(delivery.get("create_reminders"), bool)
        and delivery.get("managed_cron") is False
    )
    if not known_v3_signature:
        return source, False

    notification["renderer"] = "adaptive_v1g"
    notification["original_max_chars"] = 900
    delivery["create_reminders"] = True
    delivery["managed_cron"] = True
    delivery["auto_forward_safe_attachments"] = True
    delivery.setdefault("reminder_offsets_minutes", [1440, 60])
    delivery.setdefault(
        "calendar_path",
        email_config.DEFAULT_CONFIG["delivery"]["calendar_path"],
    )
    source["notification"] = notification
    source["delivery"] = delivery
    source["version"] = 3
    return source, True


def _migrate_known_v4_presentation_policy(
    data: dict[str, Any] | None,
) -> tuple[dict[str, Any], bool]:
    """Move the shipped v0.4 renderer to the intent-aware presentation layer."""
    source = copy.deepcopy(data) if isinstance(data, dict) else {}
    notification = source.get("notification") if isinstance(source.get("notification"), dict) else {}
    delivery = source.get("delivery") if isinstance(source.get("delivery"), dict) else {}
    if not (
        str(notification.get("renderer") or "") == "adaptive_v1g"
        and str(notification.get("mode") or "production") == "production"
        and notification.get("production_route_enabled", True) is True
    ):
        return source, False

    notification["renderer"] = "intelligent_v2"
    old_calendar = str(delivery.get("calendar_path") or "")
    if old_calendar in {
        "", "~/Documents/EmailAttachments/email-watchdog-calendar.ics",
    }:
        delivery["calendar_path"] = email_config.DEFAULT_CONFIG["delivery"]["calendar_path"]
    source["notification"] = notification
    source["delivery"] = delivery
    source["version"] = 4
    return source, True


def _migrate_known_v5_storage_policy(
    data: dict[str, Any] | None,
) -> tuple[dict[str, Any], bool]:
    """Move only shipped legacy attachment roots into plugin-owned state."""
    source = copy.deepcopy(data) if isinstance(data, dict) else {}
    paths = source.get("paths") if isinstance(source.get("paths"), dict) else {}
    notification = source.get("notification") if isinstance(source.get("notification"), dict) else {}
    if not (
        int(source.get("version") or 0) == 4
        and str(notification.get("renderer") or "") == "intelligent_v2"
    ):
        return source, False
    old_attachment = str(paths.get("attachment_dir") or "")
    if old_attachment in {
        "~/Documents/EmailAttachments",
        "$HOME/Documents/EmailAttachments",
        "/opt/data/.hermes-home/EmailAttachments",
    }:
        paths["attachment_dir"] = email_config.DEFAULT_CONFIG["paths"]["attachment_dir"]
    source["paths"] = paths
    source["version"] = 5
    return source, True


def _sanitize_existing_config(data: dict[str, Any] | None) -> dict[str, Any]:
    source = data if isinstance(data, dict) else {}
    try:
        source_version = int(source.get("version", 1))
    except (TypeError, ValueError):
        source_version = 1
    allowed = {k: copy.deepcopy(v) for k, v in source.items() if k in ALLOWED_TOP_LEVEL}
    allowed.pop("safety", None)

    # Migrate only the repository's known v1 defaults.  Explicitly customized
    # providers, endpoints, models, and notification policies remain untouched.
    if source_version < 2:
        semantic = allowed.get("semantic_engine")
        if isinstance(semantic, dict):
            provider = str(semantic.get("provider") or "ollama").strip().lower()
            endpoint = str(semantic.get("endpoint") or "http://127.0.0.1:11434").rstrip("/")
            model = str(semantic.get("model") or "qwen2.5:3b").strip().lower()
            if (
                provider == "ollama"
                and endpoint in {"http://127.0.0.1:11434", "http://localhost:11434"}
                and model == "qwen2.5:3b"
            ):
                allowed["semantic_engine"] = copy.deepcopy(
                    email_config.DEFAULT_CONFIG["semantic_engine"]
                )

        notification = allowed.get("notification")
        if isinstance(notification, dict):
            renderer = str(notification.get("renderer") or "adaptive_v1").strip().lower()
            mode = str(notification.get("mode") or "shadow").strip().lower()
            route_enabled = bool(notification.get("production_route_enabled", False))
            if renderer in {"adaptive_v1", "adaptive_v1e"} and mode == "shadow" and not route_enabled:
                migrated = copy.deepcopy(email_config.DEFAULT_CONFIG["notification"])
                for key in (
                    "all_mail_push",
                    "legacy_fallback_enabled",
                    "fast_lane_enabled",
                    "original_policy",
                    "original_max_chars",
                    "show_priority",
                    "show_category",
                    "show_time",
                    "show_debug_reason",
                    "suppress_redundant_summary",
                ):
                    if key in notification:
                        migrated[key] = copy.deepcopy(notification[key])
                allowed["notification"] = migrated

    # v0.2.x shipped this exact assistant-policy signature. Upgrade it as one
    # unit; if any of these fields was customized, preserve the whole policy.
    allowed, _ = _migrate_known_v2_assistant_policy(allowed)

    # v0.3.x shipped these exact conservative defaults.  They looked like a
    # complete assistant contract but left rich rendering and reminders off.
    # Upgrade only that exact signature; genuinely customized values remain
    # owned by the user.
    allowed, _ = _migrate_known_v3_assistant_policy(allowed)
    allowed, _ = _migrate_known_v4_presentation_policy(allowed)
    allowed, _ = _migrate_known_v5_storage_policy(allowed)

    cfg = _deep_merge(email_config.DEFAULT_CONFIG, allowed)
    cfg["version"] = 5
    cfg["paths"] = {
        key: cfg.get("paths", {}).get(key, email_config.DEFAULT_CONFIG["paths"][key])
        for key in sorted(ALLOWED_PATH_KEYS)
    }
    cfg["safety"] = copy.deepcopy(email_config.DEFAULT_CONFIG["safety"])
    delivery = cfg.setdefault("delivery", {})
    target = delivery.get("target") if isinstance(delivery.get("target"), dict) else {}
    delivery["target"] = {
        "platform": str(target.get("platform") or "").strip().lower(),
        "chat_id": str(target.get("chat_id") or "").strip(),
        "thread_id": str(target.get("thread_id") or "").strip(),
        "chat_type": str(target.get("chat_type") or "").strip(),
    }
    return cfg


def _slug(value: str, fallback: str = "account") -> str:
    text = re.sub(r"[^a-z0-9._-]+", "-", str(value or "").strip().lower()).strip("-._")
    return text[:80] or fallback


def _mask_email(value: str) -> str:
    text = str(value or "")
    if "@" not in text:
        return ""
    local, domain = text.rsplit("@", 1)
    return f"{local[:1]}***@{domain}"


def _path_summary(value: str | Path) -> dict[str, Any]:
    path = Path(str(value)).expanduser()
    return {
        "basename": path.name,
        "path_sha256": hashlib.sha256(str(path).encode("utf-8")).hexdigest(),
        "exists": path.is_file(),
    }


def _target_summary(target: dict[str, Any]) -> dict[str, Any]:
    chat_id = str(target.get("chat_id") or "")
    return {
        "platform": str(target.get("platform") or ""),
        "chat_id_present": bool(chat_id),
        "chat_id_sha256": hashlib.sha256(chat_id.encode("utf-8")).hexdigest() if chat_id else "",
        "thread_id_present": bool(target.get("thread_id")),
        "chat_type": str(target.get("chat_type") or ""),
    }


def _normalize_target(value: dict[str, Any] | None) -> dict[str, str]:
    data = value if isinstance(value, dict) else {}
    return {
        "platform": str(data.get("platform") or "").strip().lower(),
        "chat_id": str(data.get("chat_id") or "").strip(),
        "thread_id": str(data.get("thread_id") or "").strip(),
        "chat_type": str(data.get("chat_type") or "").strip(),
    }


def _session_target() -> dict[str, str]:
    return _normalize_target(
        {
            "platform": os.environ.get("HERMES_SESSION_PLATFORM", ""),
            "chat_id": os.environ.get("HERMES_SESSION_CHAT_ID", ""),
            "thread_id": os.environ.get("HERMES_SESSION_THREAD_ID", ""),
            "chat_type": os.environ.get("HERMES_SESSION_CHAT_TYPE", ""),
        }
    )


def _pending_target() -> dict[str, str]:
    state = _load_json(ONBOARDING_FILE, {})
    pending = state.get("pending_target") if isinstance(state, dict) else {}
    return _normalize_target(pending)


def _resolve_target(input_data: dict[str, Any], current: dict[str, Any]) -> tuple[dict[str, str], str]:
    explicit = _normalize_target(input_data.get("delivery_target"))
    session = _session_target()
    pending = _pending_target()
    configured = _normalize_target((current.get("delivery") or {}).get("target"))
    for source, target in (
        ("explicit", explicit),
        ("session_env", session),
        ("pending_hook_context", pending),
        ("existing_config", configured),
    ):
        if target.get("chat_id"):
            if not target.get("platform"):
                target["platform"] = "weixin"
            return target, source
    return explicit, "unresolved"


def _inspect_forbidden_secret_values(value: Any, path: str = "") -> list[str]:
    errors: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key).lower()
            child_path = f"{path}.{key}" if path else str(key)
            if key_text in FORBIDDEN_SECRET_KEYS and str(child or "").strip():
                errors.append(f"literal secret value is forbidden at {child_path}")
            errors.extend(_inspect_forbidden_secret_values(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            errors.extend(_inspect_forbidden_secret_values(child, f"{path}[{index}]"))
    return errors


def _validate_secret_command(command: str) -> str:
    text = str(command or "").strip()
    if not text:
        raise OnboardingError("secret_command is required for a generated Himalaya config")
    lowered = text.lower()
    if re.search(r"(^|[;&|]\s*)(echo|printf)\b", lowered):
        raise OnboardingError("literal echo/printf secret commands are forbidden")
    if re.search(r"(password|secret|token)\s*=", lowered):
        raise OnboardingError("inline secret assignments are forbidden")
    try:
        parts = shlex.split(text)
    except ValueError as exc:
        raise OnboardingError(f"secret_command is not valid shell syntax: {exc}") from exc
    if not parts:
        raise OnboardingError("secret_command is empty")
    command_name = Path(parts[0]).name
    if command_name not in ALLOWED_SECRET_COMMAND_PREFIXES:
        raise OnboardingError(
            "secret_command must use an approved external secret source: "
            + ", ".join(sorted(ALLOWED_SECRET_COMMAND_PREFIXES))
        )
    if len(parts) < 2:
        raise OnboardingError("secret_command must name an external secret item")
    if command_name == "printenv":
        if len(parts) != 2 or not re.fullmatch(r"[A-Z_][A-Z0-9_]*", parts[1]):
            raise OnboardingError("printenv secret_command must reference one uppercase environment variable")
    return command_name


def _toml_string(value: Any) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _infer_imap_host(email: str) -> str:
    domain = str(email or "").rsplit("@", 1)[-1].lower() if "@" in str(email or "") else ""
    mapping = {
        "gmail.com": "imap.gmail.com",
        "googlemail.com": "imap.gmail.com",
        "outlook.com": "outlook.office365.com",
        "hotmail.com": "outlook.office365.com",
        "live.com": "outlook.office365.com",
        "qq.com": "imap.qq.com",
        "foxmail.com": "imap.qq.com",
        "mail.ustc.edu.cn": "mail.ustc.edu.cn",
        "ustc.edu.cn": "mail.ustc.edu.cn",
    }
    return mapping.get(domain, "")


def _build_himalaya_toml(data: dict[str, Any], account_id: str) -> str:
    email = str(data.get("email") or "").strip()
    display_name = str(data.get("display_name") or data.get("label") or email).strip()
    host = str(data.get("imap_host") or _infer_imap_host(email)).strip()
    login = str(data.get("imap_login") or email).strip()
    encryption = str(data.get("imap_encryption") or "tls").strip().lower()
    secret_command = str(data.get("secret_command") or "").strip()
    try:
        port = int(data.get("imap_port") or 993)
    except Exception as exc:
        raise OnboardingError("imap_port must be an integer") from exc
    if not email or "@" not in email:
        raise OnboardingError("a valid email address is required")
    if not host:
        raise OnboardingError("imap_host is required")
    if port < 1 or port > 65535:
        raise OnboardingError("imap_port is out of range")
    if encryption not in {"tls", "start-tls", "none"}:
        raise OnboardingError("imap_encryption must be tls, start-tls, or none")
    _validate_secret_command(secret_command)
    section = _slug(account_id)
    rows = [
        "# Generated by Hermes Email Watchdog onboarding.",
        "# Incoming-mail only. No send backend is configured.",
        "",
        f"[accounts.{_toml_string(section)}]",
        f"email = {_toml_string(email)}",
        f"display-name = {_toml_string(display_name)}",
        "default = true",
        "",
        'backend.type = "imap"',
        f"backend.host = {_toml_string(host)}",
        f"backend.port = {port}",
        f"backend.encryption.type = {_toml_string(encryption)}",
        f"backend.login = {_toml_string(login)}",
        'backend.auth.type = "password"',
        f"backend.auth.cmd = {_toml_string(secret_command)}",
        "",
        'folder.aliases.inbox = "INBOX"',
        "",
    ]
    return "\n".join(rows)


def _normalize_account(account: dict[str, Any]) -> dict[str, Any]:
    data = dict(account)
    account_type = str(data.get("type") or "").strip().lower()
    if account_type != "himalaya":
        raise OnboardingError("each account must explicitly set type=himalaya")
    config_path = str(data.get("himalaya_config") or data.get("config") or "").strip()
    if not config_path:
        raise OnboardingError("each Himalaya account requires himalaya_config")
    email = str(data.get("email") or "").strip()
    account_id = _slug(data.get("id") or data.get("label") or email)
    label = str(data.get("label") or data.get("name") or account_id).strip()
    return {
        "id": account_id,
        "label": label,
        "name": label,
        "display_name": str(data.get("display_name") or label).strip(),
        "email": email,
        "type": "himalaya",
        "enabled": bool(data.get("enabled", True)),
        "config": os.path.expandvars(os.path.expanduser(config_path)),
        "himalaya_config": os.path.expandvars(os.path.expanduser(config_path)),
    }


def _accounts_from_himalaya(path: Path) -> list[dict[str, Any]]:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    source = data.get("accounts") if isinstance(data, dict) else {}
    if not isinstance(source, dict):
        return []
    result: list[dict[str, Any]] = []
    for name, value in source.items():
        if not isinstance(value, dict):
            continue
        backend = value.get("backend") if isinstance(value.get("backend"), dict) else {}
        if str(backend.get("type") or "").lower() != "imap":
            continue
        email = str(value.get("email") or "").strip()
        result.append(
            _normalize_account(
                {
                    "id": _slug(name),
                    "label": str(value.get("display-name") or name),
                    "display_name": str(value.get("display-name") or name),
                    "email": email,
                    "type": "himalaya",
                    "himalaya_config": str(path),
                    "enabled": True,
                }
            )
        )
    return result


def _candidate_himalaya_paths() -> list[Path]:
    candidates: list[Path] = []
    env_path = os.environ.get("HIMALAYA_CONFIG", "").strip()
    if env_path:
        candidates.append(Path(os.path.expandvars(os.path.expanduser(env_path))))
    config_dir = Path.home() / ".config" / "himalaya"
    candidates.append(config_dir / "config.toml")
    if config_dir.is_dir():
        candidates.extend(sorted(config_dir.glob("config_*.toml")))
        candidates.extend(sorted(config_dir.glob("*.toml")))
    candidates.extend(sorted(OWNED_HIMALAYA_DIR.glob("*.toml")) if OWNED_HIMALAYA_DIR.is_dir() else [])
    unique: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path.resolve()) if path.exists() else str(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _detect_himalaya_accounts() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    configs: list[dict[str, Any]] = []
    all_accounts: list[dict[str, Any]] = []
    for path in _candidate_himalaya_paths():
        if not path.is_file():
            continue
        accounts = _accounts_from_himalaya(path)
        if not accounts:
            continue
        configs.append({"path": path, "accounts": accounts})
        all_accounts.extend(accounts)
    return all_accounts, configs


def _load_input(raw: str) -> dict[str, Any]:
    value = str(raw or "").strip()
    if not value:
        return {}
    if value == "-":
        text = sys.stdin.read()
    elif value.startswith("@"):
        text = Path(value[1:]).read_text(encoding="utf-8")
    elif value.startswith("{"):
        text = value
    else:
        candidate = Path(value)
        try:
            is_file = candidate.is_file()
        except OSError:
            is_file = False
        text = candidate.read_text(encoding="utf-8") if is_file else value
    data = json.loads(text)
    if not isinstance(data, dict):
        raise OnboardingError("input JSON must be an object")
    return data


def _resolve_accounts(
    input_data: dict[str, Any], current: dict[str, Any]
) -> tuple[list[dict[str, Any]], str, PlannedHimalayaFile | None, list[str]]:
    unresolved: list[str] = []
    generated: PlannedHimalayaFile | None = None
    if input_data.get("new_himalaya"):
        spec = input_data["new_himalaya"]
        if not isinstance(spec, dict):
            raise OnboardingError("new_himalaya must be an object")
        account_id = _slug(spec.get("id") or spec.get("email"))
        raw_path = spec.get("config_path")
        path = Path(os.path.expandvars(os.path.expanduser(str(raw_path)))) if raw_path else OWNED_HIMALAYA_DIR / f"{account_id}.toml"
        content = _build_himalaya_toml(spec, account_id)
        generated = PlannedHimalayaFile(path=path, content=content, account_id=account_id)
        accounts = [
            _normalize_account(
                {
                    "id": account_id,
                    "label": spec.get("label") or spec.get("display_name") or account_id,
                    "display_name": spec.get("display_name") or spec.get("label") or account_id,
                    "email": spec.get("email") or "",
                    "type": "himalaya",
                    "himalaya_config": str(path),
                    "enabled": True,
                }
            )
        ]
        return accounts, "generated_imap_only", generated, unresolved

    explicit = input_data.get("accounts")
    if explicit is None and input_data.get("account") is not None:
        explicit = [input_data.get("account")]
    if explicit is not None:
        if not isinstance(explicit, list) or not explicit:
            raise OnboardingError("accounts must be a non-empty list")
        return [_normalize_account(a) for a in explicit if isinstance(a, dict)], "explicit", None, unresolved

    existing = current.get("accounts") if isinstance(current.get("accounts"), list) else []
    if existing:
        try:
            accounts = [_normalize_account(a) for a in existing if isinstance(a, dict)]
        except OnboardingError:
            accounts = []
        if accounts:
            return accounts, "existing_email_watchdog_config", None, unresolved

    detected, configs = _detect_himalaya_accounts()
    if len(configs) == 1 and detected:
        return detected, "auto_detected_himalaya", None, unresolved
    if len(configs) > 1:
        unresolved.append("account_selection")
    else:
        unresolved.append("account")
    return [], "unresolved", None, unresolved


def _plan_internal(input_data: dict[str, Any]) -> dict[str, Any]:
    secret_errors = _inspect_forbidden_secret_values(input_data)
    if secret_errors:
        raise OnboardingError("; ".join(secret_errors))
    current_raw = _load_json(CONFIG_PATH, {})
    current = _sanitize_existing_config(current_raw if isinstance(current_raw, dict) else {})
    accounts, account_source, generated, unresolved = _resolve_accounts(input_data, current)
    target, target_source = _resolve_target(input_data, current)
    if not target.get("chat_id"):
        unresolved.append("delivery_target")
    elif target.get("platform") != "weixin":
        raise OnboardingError("this release candidate supports a Weixin notification target only")

    cfg = _sanitize_existing_config(current)
    cfg["accounts"] = accounts
    cfg["default_account"] = str(
        input_data.get("default_account")
        or (accounts[0]["id"] if accounts else "")
    )
    cfg.setdefault("delivery", {})["target"] = target
    options = input_data.get("options") if isinstance(input_data.get("options"), dict) else {}
    if options.get("timezone"):
        cfg["delivery"]["timezone"] = str(options["timezone"])
    semantic = cfg.setdefault("semantic_engine", {})
    if "semantic_enabled" in options:
        semantic["enabled"] = bool(options["semantic_enabled"])
    elif "ollama_enabled" in options:  # compatibility with early onboarding payloads
        semantic["enabled"] = bool(options["ollama_enabled"])
    if options.get("semantic_model"):
        semantic["model"] = str(options["semantic_model"]).strip()
    if options.get("semantic_fallback_model"):
        semantic["fallback_model"] = str(options["semantic_fallback_model"]).strip()

    notification = cfg.setdefault("notification", {})
    notification["fast_lane_enabled"] = False
    policy = str(options.get("notification_policy") or "").strip().lower()
    if policy:
        if policy not in {"actionable", "all"}:
            raise OnboardingError("notification_policy must be actionable or all")
        notification["all_mail_push"] = policy == "all"

    delivery = cfg.setdefault("delivery", {})
    if "auto_download_attachments" in options:
        delivery["auto_download_attachments"] = bool(options["auto_download_attachments"])
    if "forward_attachments_to_weixin" in options:
        delivery["forward_attachments_to_weixin"] = bool(options["forward_attachments_to_weixin"])
    if options.get("attachment_max_mb") is not None:
        try:
            max_mb = int(options["attachment_max_mb"])
        except (TypeError, ValueError) as exc:
            raise OnboardingError("attachment_max_mb must be an integer") from exc
        if not 1 <= max_mb <= 100:
            raise OnboardingError("attachment_max_mb must be between 1 and 100")
        delivery["attachment_max_bytes"] = max_mb * 1024 * 1024
    cfg["safety"] = copy.deepcopy(email_config.DEFAULT_CONFIG["safety"])
    cfg["version"] = 5

    unresolved = sorted(set(unresolved))
    return {
        "ok": not unresolved,
        "unresolved": unresolved,
        "config": cfg,
        "config_sha256": _canonical_sha(cfg),
        "account_source": account_source,
        "target_source": target_source,
        "generated_himalaya": generated,
        "enable_requested": bool(input_data.get("enable", False)),
    }


def plan(input_data: dict[str, Any]) -> dict[str, Any]:
    internal = _plan_internal(input_data)
    generated = internal["generated_himalaya"]
    return {
        "ok": internal["ok"],
        "unresolved": internal["unresolved"],
        "config_sha256": internal["config_sha256"],
        "account_source": internal["account_source"],
        "target_source": internal["target_source"],
        "account_count": len(internal["config"].get("accounts", [])),
        "accounts": [
            {
                "id": a.get("id"),
                "type": a.get("type"),
                "email": _mask_email(a.get("email", "")),
                "config": _path_summary(a.get("himalaya_config", "")),
            }
            for a in internal["config"].get("accounts", [])
        ],
        "delivery_target": _target_summary(internal["config"]["delivery"]["target"]),
        "generated_himalaya": (
            {
                "path": _path_summary(generated.path),
                "content_sha256": hashlib.sha256(generated.content.encode("utf-8")).hexdigest(),
                "imap_only": "message.send" not in generated.content and "smtp" not in generated.content.lower(),
            }
            if generated
            else None
        ),
        "enable_requested": internal["enable_requested"],
        "mailbox_read_only": True,
    }


def _himalaya_binary() -> str:
    hermes_home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))).expanduser()
    candidates = [
        os.environ.get("HERMES_EMAIL_WATCHDOG_HIMALAYA_BIN", "").strip(),
        os.environ.get("HIMALAYA_BIN", "").strip(),
        str(hermes_home / "bin" / ("himalaya.exe" if os.name == "nt" else "himalaya")),
        shutil.which("himalaya") or "",
    ]
    for candidate in candidates:
        if candidate and (Path(candidate).is_file() or shutil.which(candidate)):
            return candidate
    return "himalaya"


def _redact_command(command: list[str]) -> list[str]:
    result: list[str] = []
    for index, value in enumerate(command):
        if index > 0 and command[index - 1] in {"-c", "--config"}:
            result.append(f"***CONFIG:{Path(value).name}***")
        else:
            result.append(value)
    return result


def _validate_himalaya_account(account: dict[str, Any]) -> dict[str, Any]:
    config_path = Path(str(account.get("himalaya_config") or account.get("config") or "")).expanduser()
    if not config_path.is_file():
        return {"passed": False, "error": "himalaya config does not exist", "config": _path_summary(config_path)}
    binary = _himalaya_binary()
    args = ["envelope", "list", "--page-size", "1", "--output", "json"]
    attempts: list[dict[str, Any]] = []
    for flag in ("-c", "--config"):
        command = [binary, flag, str(config_path), *args]
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=20)
            row = {
                "command": _redact_command(command),
                "return_code": completed.returncode,
                "stdout_sha256": hashlib.sha256((completed.stdout or "").encode("utf-8")).hexdigest(),
                "stderr_excerpt": re.sub(r"\s+", " ", completed.stderr or "").strip()[:240],
            }
            attempts.append(row)
            if completed.returncode == 0:
                try:
                    parsed = json.loads((completed.stdout or "[]").strip() or "[]")
                except Exception:
                    row["error"] = "stdout was not JSON"
                    continue
                return {
                    "passed": True,
                    "account_id": account.get("id"),
                    "config": _path_summary(config_path),
                    "envelope_list_only": True,
                    "result_type": type(parsed).__name__,
                    "attempts": attempts,
                }
        except subprocess.TimeoutExpired:
            attempts.append({"command": _redact_command(command), "return_code": 124, "error": "timeout"})
        except FileNotFoundError:
            attempts.append({"command": _redact_command(command), "return_code": 127, "error": "binary not found"})
    return {
        "passed": False,
        "account_id": account.get("id"),
        "config": _path_summary(config_path),
        "envelope_list_only": True,
        "attempts": attempts,
    }


def validate_config(config: dict[str, Any]) -> dict[str, Any]:
    safety = config.get("safety") if isinstance(config.get("safety"), dict) else {}
    target = _normalize_target((config.get("delivery") or {}).get("target"))
    accounts = config.get("accounts") if isinstance(config.get("accounts"), list) else []
    schema_checks = {
        "mailbox_read_only": safety.get("mailbox_read_only") is True,
        "outbound_email_disabled": safety.get("outbound_email_enabled") is False,
        "mailbox_mutation_disabled": safety.get("mailbox_mutation_enabled") is False,
        "accounts_present": bool(accounts),
        "accounts_explicit_himalaya": bool(accounts) and all(
            isinstance(a, dict) and a.get("type") == "himalaya" for a in accounts
        ),
        "weixin_target_present": target.get("platform") == "weixin" and bool(target.get("chat_id")),
    }
    account_results = [
        _validate_himalaya_account(_normalize_account(a))
        for a in accounts
        if isinstance(a, dict) and a.get("type") == "himalaya"
    ]
    passed = all(schema_checks.values()) and bool(account_results) and all(r.get("passed") for r in account_results)
    return {
        "passed": passed,
        "schema_checks": schema_checks,
        "account_results": account_results,
        "mailbox_operation": "envelope list --page-size 1 only",
        "mailbox_mutation": False,
    }


def validate_current() -> dict[str, Any]:
    raw = _load_json(CONFIG_PATH, {})
    config = _sanitize_existing_config(raw if isinstance(raw, dict) else {})
    return validate_config(config)


def migrate_current_config() -> dict[str, Any]:
    """Atomically migrate a known release default without touching custom configs."""
    with _transaction_lock():
        raw = _load_json(CONFIG_PATH, {})
        if not isinstance(raw, dict) or not CONFIG_PATH.is_file():
            return {
                "passed": True,
                "changed": False,
                "reason": "config_missing",
                "mailbox_mutation": False,
            }
        migrated, changed_v2 = _migrate_known_v2_assistant_policy(raw)
        migrated, changed_v3 = _migrate_known_v3_assistant_policy(migrated)
        migrated, changed_v4 = _migrate_known_v4_presentation_policy(migrated)
        migrated, changed_v5 = _migrate_known_v5_storage_policy(migrated)
        changed = changed_v2 or changed_v3 or changed_v4 or changed_v5
        if not changed:
            return {
                "passed": True,
                "changed": False,
                "reason": "custom_or_current_config_preserved",
                "config_sha256": _canonical_sha(raw),
                "mailbox_mutation": False,
            }
        operation_id = datetime.now().astimezone().strftime("migration-%Y%m%d-%H%M%S-%f")
        backup = _write_backup(_snapshot([CONFIG_PATH]), operation_id)
        _atomic_write_json(CONFIG_PATH, migrated)
        return {
            "passed": True,
            "changed": True,
            "from_version": raw.get("version"),
            "to_version": migrated.get("version"),
            "backup": {
                "basename": backup.name,
                "exists": backup.is_dir(),
                "path_sha256": hashlib.sha256(str(backup).encode("utf-8")).hexdigest(),
            },
            "config_sha256": _canonical_sha(migrated),
            "mailbox_mutation": False,
        }


def _snapshot(paths: list[Path]) -> dict[str, tuple[bool, bytes, int]]:
    result: dict[str, tuple[bool, bytes, int]] = {}
    for path in paths:
        if path.is_file():
            result[str(path)] = (True, path.read_bytes(), path.stat().st_mode & 0o777)
        else:
            result[str(path)] = (False, b"", 0o600)
    return result


def _restore(snapshot: dict[str, tuple[bool, bytes, int]]) -> None:
    for raw_path, (existed, data, mode) in snapshot.items():
        path = Path(raw_path)
        if existed:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, raw_tmp = tempfile.mkstemp(prefix=f".{path.name}.restore.", dir=str(path.parent))
            tmp = Path(raw_tmp)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(tmp, mode)
                os.replace(tmp, path)
            finally:
                tmp.unlink(missing_ok=True)
        else:
            path.unlink(missing_ok=True)


def _write_backup(snapshot: dict[str, tuple[bool, bytes, int]], operation_id: str) -> Path:
    target = BACKUP_DIR / operation_id
    target.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {"created_at": _now(), "files": []}
    for raw_path, (existed, data, mode) in snapshot.items():
        path = Path(raw_path)
        row = {
            "path_sha256": hashlib.sha256(raw_path.encode("utf-8")).hexdigest(),
            "basename": path.name,
            "existed": existed,
            "mode": oct(mode),
            "sha256": hashlib.sha256(data).hexdigest() if existed else "",
        }
        if existed:
            (target / path.name).write_bytes(data)
            os.chmod(target / path.name, 0o600)
        manifest["files"].append(row)
    _atomic_write_json(target / "manifest.json", manifest)
    return target


def apply(input_data: dict[str, Any]) -> dict[str, Any]:
    with _transaction_lock():
        internal = _plan_internal(input_data)
        if not internal["ok"]:
            raise OnboardingError("unresolved fields: " + ", ".join(internal["unresolved"]))
        config = internal["config"]
        generated: PlannedHimalayaFile | None = internal["generated_himalaya"]
        paths = [CONFIG_PATH, ENABLED_FILE, ONBOARDING_FILE]
        if generated:
            paths.append(generated.path)
        snapshot = _snapshot(paths)
        operation_id = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
        backup = _write_backup(snapshot, operation_id)
        staged_generated: Path | None = None
        live_writes_started = False
        try:
            validation_config = config
            if generated:
                staged_generated = _stage_text(generated.path, generated.content, 0o600)
                validation_config = _validation_config_for_staged_himalaya(
                    config,
                    generated,
                    staged_generated,
                )

            # Validate the proposed configuration before publishing any live
            # Email Watchdog config/state files. A SIGKILL during validation
            # therefore leaves the previously committed transaction intact.
            validation = validate_config(validation_config)
            if not validation.get("passed"):
                raise OnboardingError("read-only account validation failed")

            live_writes_started = True
            if generated and staged_generated is not None:
                generated.path.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staged_generated, generated.path)
                staged_generated = None
                os.chmod(generated.path, 0o600)

            _atomic_write_json(CONFIG_PATH, config)
            _write_enabled(bool(internal["enable_requested"]))

            target = config["delivery"]["target"]
            state = _load_json(ONBOARDING_FILE, {})
            if not isinstance(state, dict):
                state = {}
            state.update(
                {
                    "schema_version": 1,
                    "configured": True,
                    "configured_at": _now(),
                    "last_config_sha256": internal["config_sha256"],
                    "last_account_source": internal["account_source"],
                    "last_target_source": internal["target_source"],
                    "configured_target": _target_summary(target),
                    "enabled": _read_enabled(),
                    "last_backup": str(backup),
                    "last_operation_id": operation_id,
                    "transaction_state": "committed",
                }
            )
            state.pop("pending_target", None)
            _atomic_write_json(ONBOARDING_FILE, state)
            return {
                "passed": True,
                "config_sha256": internal["config_sha256"],
                "enabled": _read_enabled(),
                "validation": validation,
                "account_source": internal["account_source"],
                "target_source": internal["target_source"],
                "backup": _path_summary(backup),
                "mailbox_mutation": False,
            }
        except Exception:
            if live_writes_started:
                _restore(snapshot)
            raise
        finally:
            if staged_generated is not None:
                staged_generated.unlink(missing_ok=True)


def capture_context() -> dict[str, Any]:
    with _transaction_lock():
        target = _session_target()
        if not target.get("platform") or not target.get("chat_id"):
            raise OnboardingError("current session platform/chat id is unavailable")
        state = _load_json(ONBOARDING_FILE, {})
        if not isinstance(state, dict):
            state = {}
        state.update(
            {
                "schema_version": 1,
                "pending_target": target,
                "captured_at": _now(),
                "capture_source": "terminal_session_env",
            }
        )
        _atomic_write_json(ONBOARDING_FILE, state)
        return {"passed": True, "target": _target_summary(target)}


def status() -> dict[str, Any]:
    current_raw = _load_json(CONFIG_PATH, {})
    current = _sanitize_existing_config(current_raw if isinstance(current_raw, dict) else {})
    detected, detected_configs = _detect_himalaya_accounts()
    accounts = current.get("accounts") if isinstance(current.get("accounts"), list) else []
    valid_accounts: list[dict[str, Any]] = []
    for account in accounts:
        if not isinstance(account, dict):
            continue
        try:
            valid_accounts.append(_normalize_account(account))
        except OnboardingError:
            pass
    target = _normalize_target((current.get("delivery") or {}).get("target"))
    pending = _pending_target()
    configured = bool(valid_accounts) and target.get("platform") == "weixin" and bool(target.get("chat_id"))
    return {
        "configured": configured,
        "enabled": _read_enabled(),
        "config_exists": CONFIG_PATH.is_file(),
        "config_sha256": _canonical_sha(current) if CONFIG_PATH.is_file() else "",
        "account_count": len(valid_accounts),
        "accounts": [
            {
                "id": a.get("id"),
                "type": a.get("type"),
                "email": _mask_email(a.get("email", "")),
                "config": _path_summary(a.get("himalaya_config", "")),
            }
            for a in valid_accounts
        ],
        "delivery_target": _target_summary(target),
        "pending_target": _target_summary(pending),
        "detected_himalaya_config_count": len(detected_configs),
        "detected_himalaya_account_count": len(detected),
        "unresolved": [
            *([] if valid_accounts else ["account"]),
            *([] if target.get("chat_id") else ["delivery_target"]),
        ],
        "mailbox_access": False,
        "values_redacted": True,
    }


def export_redacted() -> dict[str, Any]:
    current_raw = _load_json(CONFIG_PATH, {})
    current = _sanitize_existing_config(current_raw if isinstance(current_raw, dict) else {})
    redacted = copy.deepcopy(current)
    for account in redacted.get("accounts", []):
        if not isinstance(account, dict):
            continue
        account["email"] = _mask_email(account.get("email", ""))
        for key in ("config", "himalaya_config"):
            if account.get(key):
                account[key] = _path_summary(account[key])
    target = (redacted.get("delivery") or {}).get("target")
    redacted.setdefault("delivery", {})["target"] = _target_summary(target or {})
    return {
        "config": redacted,
        "enabled": _read_enabled(),
        "values_redacted": True,
        "mailbox_access": False,
    }


def enable() -> dict[str, Any]:
    with _transaction_lock():
        validation = validate_current()
        if not validation.get("passed"):
            raise OnboardingError("cannot enable before read-only validation passes")
        _write_enabled(True)
        state = _load_json(ONBOARDING_FILE, {})
        if isinstance(state, dict):
            state["enabled"] = True
            state["updated_at"] = _now()
            _atomic_write_json(ONBOARDING_FILE, state)
        return {"passed": True, "enabled": True, "validation": validation}


def disable() -> dict[str, Any]:
    with _transaction_lock():
        _write_enabled(False)
        state = _load_json(ONBOARDING_FILE, {})
        if isinstance(state, dict):
            state["enabled"] = False
            state["updated_at"] = _now()
            _atomic_write_json(ONBOARDING_FILE, state)
        return {"passed": True, "enabled": False}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status").add_argument("--json", action="store_true")
    for name in ("plan", "apply"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--input-json", required=True)
    sub.add_parser("validate").add_argument("--json", action="store_true")
    sub.add_parser("migrate-current").add_argument("--json", action="store_true")
    sub.add_parser("enable").add_argument("--json", action="store_true")
    sub.add_parser("disable").add_argument("--json", action="store_true")
    sub.add_parser("capture-context").add_argument("--json", action="store_true")
    sub.add_parser("export-redacted").add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "status":
            result = status()
        elif args.command == "plan":
            result = plan(_load_input(args.input_json))
        elif args.command == "apply":
            result = apply(_load_input(args.input_json))
        elif args.command == "validate":
            result = validate_current()
        elif args.command == "migrate-current":
            result = migrate_current_config()
        elif args.command == "enable":
            result = enable()
        elif args.command == "disable":
            result = disable()
        elif args.command == "capture-context":
            result = capture_context()
        elif args.command == "export-redacted":
            result = export_redacted()
        else:
            raise OnboardingError(f"unsupported command: {args.command}")
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0 if result.get("passed", result.get("ok", True)) else 1
    except (OnboardingError, json.JSONDecodeError, OSError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "passed": False,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "mailbox_mutation": False,
                },
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
