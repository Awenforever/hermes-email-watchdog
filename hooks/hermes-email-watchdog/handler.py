"""Hermes Email Watchdog readonly scheduler hook."""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import re
import sys
import time
import traceback
import hashlib
import contextlib
import fcntl
import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

SKILL_DIR = Path(os.getenv("HERMES_EMAIL_WATCHDOG_SKILL_DIR", "/opt/data/skills/hermes-email-watchdog"))
SCRIPTS_DIR = SKILL_DIR / "scripts"
STATE_ROOT = Path(os.getenv("HERMES_EMAIL_WATCHDOG_STATE_ROOT", "/opt/data/.hermes-home/.hermes"))
CONFIG_PATH = os.getenv("EMAIL_WATCHDOG_CONFIG", str(STATE_ROOT / "email_watchdog_config.json"))
ENABLED_FILE = Path(os.getenv("HERMES_EMAIL_WATCHDOG_ENABLED_FILE", str(STATE_ROOT / "email_watchdog_enabled")))
INTERVAL_FILE = Path(os.getenv("HERMES_EMAIL_WATCHDOG_INTERVAL_FILE", str(STATE_ROOT / "email_watchdog_interval_seconds")))
STATUS_FILE = Path(os.getenv("HERMES_EMAIL_WATCHDOG_STATUS_FILE", str(STATE_ROOT / "email_watchdog_status.json")))
SEEN_FILE = Path(os.getenv("HERMES_EMAIL_WATCHDOG_SEEN_FILE", str(STATE_ROOT / "email_watch_seen.json")))
ONBOARDING_FILE = Path(os.getenv("HERMES_EMAIL_WATCHDOG_ONBOARDING_FILE", str(STATE_ROOT / "email_watchdog_onboarding.json")))
# EMAIL_WATCHDOG_OUTBOX_V1
OUTBOX_FILE = Path(os.getenv("HERMES_EMAIL_WATCHDOG_OUTBOX_FILE", "/opt/data/.hermes-home/.hermes/email_watchdog_outbox.json"))
OUTBOX_MAX_DELIVERED = int(os.getenv("HERMES_EMAIL_WATCHDOG_OUTBOX_MAX_DELIVERED", "200") or "200")
OUTBOX_RETRY_BASE_SECONDS = int(os.getenv("HERMES_EMAIL_WATCHDOG_OUTBOX_RETRY_BASE_SECONDS", "60") or "60")
OUTBOX_RETRY_MAX_SECONDS = int(os.getenv("HERMES_EMAIL_WATCHDOG_OUTBOX_RETRY_MAX_SECONDS", "3600") or "3600")

_task: asyncio.Task | None = None
_once_lock = asyncio.Lock()

# EMAIL_WATCHDOG_STATE_TRANSACTION_LOCK_V1
_STATE_LOCKS_GUARD = threading.Lock()
_STATE_LOCKS: dict[str, threading.RLock] = {}


def _state_thread_lock(path: Path) -> threading.RLock:
    key = str(path)
    with _STATE_LOCKS_GUARD:
        lock = _STATE_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _STATE_LOCKS[key] = lock
        return lock


@contextlib.contextmanager
def _state_file_lock(path: Path):
    """Serialize JSON read-modify-write across threads and processes."""
    with _state_thread_lock(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.with_name(f".{path.name}.lock")
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            os.chmod(lock_path, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)


def _atomic_write_json_file(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_tmp = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp = Path(raw_tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


async def handle(event_type: str, context: dict):
    global _task
    if event_type == "agent:start":
        _capture_onboarding_target(context or {})
        return
    if event_type != "gateway:startup":
        return

    if not _enabled():
        logger.warning("Hermes Email Watchdog: disabled; startup hook loaded but loop not started")
        _write_status({"state": "disabled", "updated_at": _now()})
        return

    if _task is not None and not _task.done():
        logger.warning("Hermes Email Watchdog: loop already running; skip duplicate startup")
        return

    _task = asyncio.create_task(_loop(), name="hermes-email-watchdog-loop")
    _task.add_done_callback(_task_done)
    logger.warning("Hermes Email Watchdog: readonly scheduler task created")


# EMAIL_WATCHDOG_ONBOARDING_CONTEXT_CAPTURE_V1
def _setup_intent(message: object) -> bool:
    text = str(message or "").strip().lower()
    if not text:
        return False
    patterns = [
        r"(?:安装|配置|设置|启用|接入|开启).{0,12}(?:邮件|邮箱|email|watchdog)",
        r"(?:邮件|邮箱|email).{0,12}(?:监控|提醒|通知|看门狗|watchdog)",
        r"hermes[ -]?email[ -]?watchdog",
        r"email[ -]?watchdog",
        r"(?:install|configure|setup|enable).{0,20}(?:email|mail|watchdog)",
        r"(?:monitor|watch).{0,12}(?:email|mailbox)",
    ]
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def _capture_onboarding_target(context: dict) -> None:
    try:
        if not _setup_intent(context.get("message")):
            return
        platform = str(context.get("platform") or "").strip().lower()
        chat_id = str(context.get("chat_id") or "").strip()
        if not platform or not chat_id:
            return
        with _state_file_lock(ONBOARDING_FILE):
            state: dict = {}
            if ONBOARDING_FILE.exists():
                try:
                    loaded = json.loads(ONBOARDING_FILE.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        state = loaded
                except Exception:
                    state = {}
            message_hash = hashlib.sha256(
                str(context.get("message") or "").encode("utf-8", errors="replace")
            ).hexdigest()
            state.update(
                {
                    "schema_version": 1,
                    "pending_target": {
                        "platform": platform,
                        "chat_id": chat_id,
                        "thread_id": str(context.get("thread_id") or "").strip(),
                        "chat_type": str(context.get("chat_type") or "").strip(),
                    },
                    "capture_source": "agent_start_hook",
                    "captured_at": _now(),
                    "session_id": str(context.get("session_id") or ""),
                    "user_id_sha256": hashlib.sha256(
                        str(context.get("user_id") or "").encode("utf-8", errors="replace")
                    ).hexdigest(),
                    "message_sha256": message_hash,
                }
            )
            state.pop("message", None)
            _atomic_write_json_file(ONBOARDING_FILE, state)
    except Exception:
        logger.exception("Hermes Email Watchdog: failed to capture onboarding target")


def _task_done(task: asyncio.Task):
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        logger.warning("Hermes Email Watchdog: scheduler task cancelled")
        return
    if exc:
        logger.exception("Hermes Email Watchdog: scheduler task died", exc_info=exc)
    else:
        logger.warning("Hermes Email Watchdog: scheduler task exited")


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _enabled() -> bool:
    env = os.getenv("HERMES_EMAIL_WATCHDOG_ENABLED", "").strip().lower()
    if env in {"1", "true", "yes", "on"}:
        return True
    if env in {"0", "false", "no", "off"}:
        return False
    try:
        return ENABLED_FILE.read_text(encoding="utf-8").strip().lower() in {"1", "true", "yes", "on"}
    except Exception:
        return False


def _interval_seconds() -> int:
    raw = os.getenv("HERMES_EMAIL_WATCHDOG_INTERVAL_SECONDS", "").strip()
    if not raw:
        try:
            raw = INTERVAL_FILE.read_text(encoding="utf-8").strip()
        except Exception:
            raw = ""
    try:
        value = int(raw or "60")
    except Exception:
        value = 60
    return max(30, min(value, 3600))


def _chat_id() -> str:
    try:
        data = json.loads(Path(CONFIG_PATH).read_text(encoding="utf-8"))
        delivery = data.get("delivery") if isinstance(data, dict) else {}
        target = delivery.get("target") if isinstance(delivery, dict) else {}
        if isinstance(target, dict):
            platform = str(target.get("platform") or "").strip().lower()
            chat_id = str(target.get("chat_id") or "").strip()
            if platform == "weixin" and chat_id:
                return chat_id
    except Exception:
        pass
    return (
        os.getenv("HERMES_EMAIL_WATCHDOG_WEIXIN_CHAT_ID", "").strip()
        or os.getenv("HERMES_PROACTIVE_WEIXIN_CHAT_ID", "").strip()
    )


def _write_status(data: dict):
    try:
        STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        old = {}
        if STATUS_FILE.exists():
            try:
                old = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
            except Exception:
                old = {}
        merged = {**old, **data, "updated_at": _now()}
        tmp = STATUS_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, STATUS_FILE)
        try:
            os.chmod(STATUS_FILE, 0o600)
        except Exception:
            pass
    except Exception:
        logger.exception("Hermes Email Watchdog: failed to write status")



# EMAIL_WATCHDOG_SEEN_ACK_GUARD_V1
def _read_seen_snapshot() -> bytes | None:
    try:
        if SEEN_FILE.exists():
            return SEEN_FILE.read_bytes()
    except Exception:
        logger.exception("Hermes Email Watchdog: failed to snapshot seen state")
    return None


def _restore_seen_snapshot(snapshot: bytes | None):
    if snapshot is None:
        return
    try:
        SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = SEEN_FILE.with_suffix(".json.restore-tmp")
        tmp.write_bytes(snapshot)
        os.replace(tmp, SEEN_FILE)
        try:
            os.chmod(SEEN_FILE, 0o600)
        except Exception:
            pass
    except Exception:
        logger.exception("Hermes Email Watchdog: failed to restore seen state after send failure")


# EMAIL_WATCHDOG_NOTIFICATION_OUTBOX_V1
def _safe_error_text(exc: object) -> str:
    try:
        text = str(exc)
    except Exception:
        text = type(exc).__name__
    return text[:800]


def _outbox_now() -> str:
    return _now()


def _outbox_load() -> dict:
    try:
        if OUTBOX_FILE.exists():
            data = json.loads(OUTBOX_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("version", 1)
                data.setdefault("entries", {})
                return data
    except Exception:
        logger.exception("Hermes Email Watchdog: failed to load notification outbox")
    return {"version": 1, "entries": {}}


def _outbox_save(data: dict) -> None:
    data.setdefault("version", 1)
    data.setdefault("entries", {})
    delivered = [
        (k, v) for k, v in data["entries"].items()
        if isinstance(v, dict) and v.get("status") == "delivered"
    ]
    if len(delivered) > OUTBOX_MAX_DELIVERED:
        delivered.sort(key=lambda kv: str(kv[1].get("delivered_at") or kv[1].get("updated_at") or ""))
        for k, _ in delivered[: max(0, len(delivered) - OUTBOX_MAX_DELIVERED)]:
            data["entries"].pop(k, None)
    _atomic_write_json_file(OUTBOX_FILE, data)


def _outbox_text_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", errors="replace")).hexdigest()


def _outbox_delivery_id(text_hash: str) -> str:
    return f"hermes-email-watchdog-{text_hash[:32]}"


def _outbox_prepare(text: str) -> dict:
    # Persist before calling adapter.send so success=false/exception can be retried safely.
    text = text or ""
    text_hash = _outbox_text_hash(text)
    delivery_id = _outbox_delivery_id(text_hash)
    with _state_file_lock(OUTBOX_FILE):
        data = _outbox_load()
        entries = data.setdefault("entries", {})
        entry = entries.get(delivery_id)
        now = _outbox_now()
        if not isinstance(entry, dict):
            entry = {
                "id": delivery_id,
                "delivery_id": delivery_id,
                "text_hash": text_hash,
                "text": text,
                "status": "pending",
                "attempts": 0,
                "created_at": now,
                "updated_at": now,
                "source": "hermes-email-watchdog",
            }
            entries[delivery_id] = entry
        elif entry.get("status") != "delivered":
            entry["text"] = text
            entry["text_hash"] = text_hash
            entry["status"] = "pending"
            entry["updated_at"] = now
        _outbox_save(data)
        return dict(entry)



# EMAIL_WATCHDOG_OUTBOX_NONBLOCKING_BACKOFF_V1

def _outbox_retry_delay_seconds(attempts: int) -> int:
    base = max(0, int(OUTBOX_RETRY_BASE_SECONDS))
    cap = max(base, int(OUTBOX_RETRY_MAX_SECONDS))
    if base == 0:
        return 0
    exponent = min(max(int(attempts or 1) - 1, 0), 20)
    return min(cap, base * (2 ** exponent))


def _outbox_parse_time(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed.astimezone()


def _outbox_due_at(entry: dict) -> datetime | None:
    explicit = _outbox_parse_time(entry.get("next_attempt_at"))
    if explicit is not None:
        return explicit
    attempts = int(entry.get("attempts") or 0)
    if attempts <= 0:
        return None
    anchor = (
        _outbox_parse_time(entry.get("last_attempt_at"))
        or _outbox_parse_time(entry.get("last_error_at"))
        or _outbox_parse_time(entry.get("updated_at"))
        or _outbox_parse_time(entry.get("created_at"))
    )
    if anchor is None:
        return None
    return anchor + timedelta(seconds=_outbox_retry_delay_seconds(attempts))


def _outbox_is_due(entry: dict, now: datetime | None = None) -> bool:
    if entry.get("status") != "pending" or not entry.get("text"):
        return False
    due_at = _outbox_due_at(entry)
    if due_at is None:
        return True
    current = now or datetime.now().astimezone()
    if current.tzinfo is None:
        current = current.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return current.astimezone() >= due_at.astimezone()


def _outbox_pending_entries(limit: int = 10, due_only: bool = True) -> list[dict]:
    data = _outbox_load()
    entries = [
        dict(v) for v in data.get("entries", {}).values()
        if isinstance(v, dict) and v.get("status") == "pending" and v.get("text")
    ]
    entries.sort(key=lambda e: str(e.get("created_at") or ""))
    if due_only:
        entries = [entry for entry in entries if _outbox_is_due(entry)]
    return entries[:limit]


def _outbox_pending_state() -> dict:
    pending = _outbox_pending_entries(limit=100000, due_only=False)
    due = [entry for entry in pending if _outbox_is_due(entry)]
    due_times = [value for value in (_outbox_due_at(entry) for entry in pending) if value is not None]
    return {
        "pending_total": len(pending),
        "pending_due": len(due),
        "pending_deferred": max(0, len(pending) - len(due)),
        "next_due_at": min(due_times).isoformat(timespec="seconds") if due_times else None,
    }

def _outbox_mark_attempt(entry: dict) -> None:
    with _state_file_lock(OUTBOX_FILE):
        data = _outbox_load()
        entries = data.setdefault("entries", {})
        item = entries.get(entry.get("delivery_id") or entry.get("id"))
        if not isinstance(item, dict):
            item = dict(entry)
            entries[item.get("delivery_id") or item.get("id")] = item
        item["status"] = "pending"
        item["attempts"] = int(item.get("attempts") or 0) + 1
        item["last_attempt_at"] = _outbox_now()
        item["updated_at"] = item["last_attempt_at"]
        _outbox_save(data)



def _outbox_mark_failed(entry: dict, exc: object) -> None:
    with _state_file_lock(OUTBOX_FILE):
        data = _outbox_load()
        entries = data.setdefault("entries", {})
        key = entry.get("delivery_id") or entry.get("id")
        item = entries.get(key)
        if not isinstance(item, dict):
            item = dict(entry)
            entries[key] = item
        now = _outbox_now()
        attempts = int(item.get("attempts") or 0)
        delay = _outbox_retry_delay_seconds(attempts)
        now_dt = _outbox_parse_time(now) or datetime.now().astimezone()
        item["status"] = "pending"
        item["last_error"] = _safe_error_text(exc)
        item["last_error_at"] = now
        item["retry_delay_seconds"] = delay
        item["next_attempt_at"] = (now_dt + timedelta(seconds=delay)).isoformat(timespec="seconds")
        item["updated_at"] = now
        _outbox_save(data)


def _outbox_mark_delivered(entry: dict, result: object) -> None:
    with _state_file_lock(OUTBOX_FILE):
        data = _outbox_load()
        entries = data.setdefault("entries", {})
        key = entry.get("delivery_id") or entry.get("id")
        item = entries.get(key)
        if not isinstance(item, dict):
            item = dict(entry)
            entries[key] = item
        now = _outbox_now()
        item["status"] = "delivered"
        item["delivered_at"] = now
        item["updated_at"] = now
        item["last_error"] = ""
        item.pop("next_attempt_at", None)
        item.pop("retry_delay_seconds", None)
        message_id = getattr(result, "message_id", None)
        if message_id:
            item["message_id_present"] = True
        _outbox_save(data)


async def _flush_outbox(limit: int = 3) -> dict:
    summary = {
        "attempted": 0,
        "sent": 0,
        "failed": 0,
        "errors": [],
    }
    for entry in _outbox_pending_entries(limit=limit, due_only=True):
        summary["attempted"] += 1
        _outbox_mark_attempt(entry)
        try:
            result = await _send_weixin(entry.get("text", ""), delivery_id=entry.get("delivery_id") or entry.get("id"))
        except Exception as exc:
            _outbox_mark_failed(entry, exc)
            summary["failed"] += 1
            summary["errors"].append({
                "delivery_id": entry.get("delivery_id") or entry.get("id"),
                "error": _safe_error_text(exc),
            })
            logger.warning(
                "Hermes Email Watchdog: pending outbox delivery deferred delivery_id=%s error=%s",
                entry.get("delivery_id") or entry.get("id"),
                _safe_error_text(exc),
            )
            continue
        _outbox_mark_delivered(entry, result)
        summary["sent"] += 1
        logger.warning(
            "Hermes Email Watchdog: pending outbox delivered delivery_id=%s chars=%s",
            entry.get("delivery_id") or entry.get("id"),
            len(entry.get("text") or ""),
        )
    summary.update(_outbox_pending_state())
    return summary

async def _loop():
    startup_delay = int(os.getenv("HERMES_EMAIL_WATCHDOG_STARTUP_DELAY_SECONDS", "15") or "15")
    await asyncio.sleep(max(0, startup_delay))

    while _enabled():
        started = time.time()
        try:
            await _run_once()
        except Exception as exc:
            logger.exception("Hermes Email Watchdog: run_once failed")
            _write_status({
                "state": "error",
                "last_error_at": _now(),
                "last_error": f"{type(exc).__name__}: {str(exc)[:800]}",
                "traceback_tail": traceback.format_exc()[-1600:],
            })

        elapsed = time.time() - started
        await asyncio.sleep(max(5, _interval_seconds() - int(elapsed)))

    _write_status({"state": "disabled", "disabled_at": _now()})
    logger.warning("Hermes Email Watchdog: disabled; loop exited")



async def _run_once():
    if _once_lock.locked():
        logger.warning("Hermes Email Watchdog: previous run still active; skip overlap")
        return

    async with _once_lock:
        flush = await _flush_outbox()
        if flush.get("sent"):
            logger.warning("Hermes Email Watchdog: flushed pending outbox count=%s", flush.get("sent"))
        if flush.get("failed"):
            logger.warning(
                "Hermes Email Watchdog: pending outbox failures deferred count=%s; mailbox polling continues",
                flush.get("failed"),
            )

        seen_snapshot = _read_seen_snapshot()
        output = await asyncio.to_thread(_call_watchdog)
        status = {
            "state": "running",
            "last_run_at": _now(),
            "last_output_chars": len(output or ""),
            "interval_seconds": _interval_seconds(),
            "outbox_file": str(OUTBOX_FILE),
            "outbox_flush": flush,
        }
        delivery_error = ""

        if output:
            try:
                entry = _outbox_prepare(output)
            except Exception:
                _restore_seen_snapshot(seen_snapshot)
                raise

            try:
                _outbox_mark_attempt(entry)
                result = await _send_weixin(output, delivery_id=entry["delivery_id"])
            except Exception as exc:
                # Mailbox processing has completed and the exact notification is durable in the outbox.
                # Defer delivery with bounded backoff; do not suppress future mailbox polling.
                _outbox_mark_failed(entry, exc)
                delivery_error = _safe_error_text(exc)
                status["state"] = "degraded"
                status["last_delivery_id"] = entry["delivery_id"]
                status["last_delivery_pending_at"] = _now()
                status["last_delivery_error"] = delivery_error
            else:
                _outbox_mark_delivered(entry, result)
                status["last_sent_at"] = _now()
                status["last_sent_chars"] = len(output)
                status["last_delivery_id"] = entry["delivery_id"]
                status["last_delivery_error"] = ""

        pending_state = _outbox_pending_state()
        status.update(pending_state)
        status["last_ok_at"] = _now()
        if flush.get("failed") or delivery_error:
            status["state"] = "degraded"
            status["last_error"] = delivery_error or (flush.get("errors") or [{}])[0].get("error", "outbox delivery deferred")
        else:
            status["last_error"] = ""
        _write_status(status)

def _call_watchdog() -> str:
    # EMAIL_WATCHDOG_RELOAD_MODULES_EACH_RUN_V1
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    os.environ["EMAIL_WATCHDOG_CONFIG"] = CONFIG_PATH

    import email_config
    import email_watch

    email_config.reset_cache()
    importlib.reload(email_config)
    email_config.reset_cache()
    email_watch = importlib.reload(email_watch)

    result = email_watch.main()
    return result or ""
def _runner_ref():
    try:
        from gateway.run import _gateway_runner_ref
        return _gateway_runner_ref()
    except Exception:
        logger.exception("Hermes Email Watchdog: failed to resolve gateway runner")
        return None


async def _send_weixin(text: str, delivery_id: str | None = None):
    chat_id = _chat_id()
    if not chat_id:
        raise RuntimeError("configured Weixin delivery target is empty")

    runner = _runner_ref()
    if runner is None:
        raise RuntimeError("gateway runner unavailable")

    delivery_id = delivery_id or _outbox_delivery_id(_outbox_text_hash(text or ""))

    adapters = getattr(runner, "adapters", {}) or {}
    for key, adapter in adapters.items():
        key_value = getattr(key, "value", key)
        if key_value == "weixin":
            result = await adapter.send(
                chat_id,
                text,
                metadata={
                    "is_system": True,
                    "model_name": "hermes",
                    "model": "hermes",
                    "resolved_model": "hermes",
                    "routed_model": "hermes",
                    "source": "hermes-email-watchdog",
                    "_delivery_id": delivery_id,
                },
            )
            if not getattr(result, "success", False):
                error = getattr(result, "error", "") or "adapter.send returned success=false"
                raise RuntimeError(f"Weixin delivery not acknowledged: {str(error)[:800]}")
            logger.warning(
                "Hermes Email Watchdog: sent notification chars=%s delivery_id=%s message_id_present=%s",
                len(text or ""),
                delivery_id,
                bool(getattr(result, "message_id", None)),
            )
            return result

    raise RuntimeError("weixin adapter unavailable")
