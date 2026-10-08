"""Authorized quote-driven reminder and reply actions for Email Watchdog."""

from __future__ import annotations

import hashlib
import json
import ntpath
import os
import re
import shutil
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import email_config
import email_delivery
import email_store
from portable_lock import acquire_file_lock, release_file_lock


HERMES_HOME = Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes"))).expanduser()
STATE_ROOT = Path(os.getenv("HERMES_EMAIL_WATCHDOG_STATE_ROOT", str(HERMES_HOME / "plugin-data" / "hermes-email-watchdog")))
OUTBOX_FILE = Path(os.getenv("HERMES_EMAIL_WATCHDOG_OUTBOX_FILE", str(STATE_ROOT / "outbox.json")))
ACTION_FILE = STATE_ROOT / "mail-actions.json"
_FOOTER_RE = re.compile(r"\n\s*---\s*\n\s*`\d+`\s+`[^`]+`\s*$", re.S)
_NO_REPLY_RE = re.compile(r"(?i)(?:^|[._+-])(no-?reply|do-?not-?reply|mailer-daemon|bounce)(?:@|[._+-])")
_THREAD_LOCKS = {}
_THREAD_LOCKS_GUARD = threading.Lock()


def _thread_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(key, threading.RLock())


@contextmanager
def _file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with _thread_lock(path):
        lock_path = path.with_name(f".{path.name}.lock")
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            os.chmod(lock_path, 0o600)
            acquire_file_lock(fd)
            yield
        finally:
            release_file_lock(fd)
            os.close(fd)


def _atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp = Path(raw)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _load(path: Path, default: dict) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else dict(default)
    except Exception:
        return dict(default)


def _normalized(text: object) -> str:
    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    value = _FOOTER_RE.sub("", value).strip()
    return re.sub(r"[ \t]+\n", "\n", value)


def _fingerprint(text: object) -> str:
    return hashlib.sha256(_normalized(text).encode("utf-8", errors="replace")).hexdigest()


def _outbox_actions(reference_text: str, reference_message_id: str = "") -> list[dict]:
    wanted = _fingerprint(reference_text)
    wanted_id = str(reference_message_id or "").strip()
    with _file_lock(OUTBOX_FILE):
        data = _load(OUTBOX_FILE, {"entries": {}})
    matches = []
    id_matches = []
    for entry in (data.get("entries") or {}).values():
        if not isinstance(entry, dict):
            continue
        metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
        actions = [item for item in (metadata.get("mail_actions") or []) if isinstance(item, dict)]
        if not actions:
            continue
        entry_match = _fingerprint(entry.get("text")) == wanted
        text_part = (entry.get("parts") or {}).get("text") if isinstance(entry.get("parts"), dict) else {}
        adapter_message_id = str((text_part or {}).get("adapter_message_id") or entry.get("adapter_message_id") or "").strip()
        for action in actions:
            if wanted_id and adapter_message_id and adapter_message_id == wanted_id:
                id_matches.append(dict(action))
                continue
            if entry_match or _fingerprint(action.get("notification_text")) == wanted:
                matches.append(dict(action))
    return id_matches or matches


def _state() -> dict:
    value = _load(ACTION_FILE, {"version": 1, "receipts": {}, "drafts": {}})
    value.setdefault("version", 1)
    value.setdefault("receipts", {})
    value.setdefault("drafts", {})
    return value


def _handled(message: str, key: str) -> dict:
    return {"decision": "handled", "message": message, "source": "hermes-email-watchdog", "delivery_id": key}


def _receipt_key(context: dict, action: str) -> str:
    raw = "|".join((str(context.get("platform") or ""), str(context.get("user_id") or ""), str(context.get("message_id") or ""), action))
    return hashlib.sha256(raw.encode()).hexdigest()


def _signature(action: dict) -> str:
    cfg = email_config.load_config()
    reply = cfg.get("reply") if isinstance(cfg.get("reply"), dict) else {}
    signatures = reply.get("signatures") if isinstance(reply.get("signatures"), dict) else {}
    account = str(action.get("account") or "")
    value = signatures.get(account, reply.get("default_signature", ""))
    return str(value or "").replace("\r\n", "\n").strip()


def _reply_block_reason(action: dict) -> str:
    sender = str(action.get("from_addr") or "").strip()
    if not sender or _NO_REPLY_RE.search(sender):
        return "该邮件来自不可回复或自动发送地址，已阻止创建回复。"
    if str(action.get("risk_label") or "").lower() in {"high", "critical", "高", "严重"}:
        return "该邮件被标记为高风险，已阻止从微信发起回复。"
    if str(action.get("category") or "").lower() in {
        "verification_code", "security_alert", "newsletter_marketing",
        "system_notification", "receipt_invoice", "automated_notification",
    }:
        return "该邮件属于自动通知，默认不允许直接回复。"
    if str(action.get("account_type") or "").lower() != "himalaya":
        return "该邮箱当前没有可验证的回复发送通道；请先在 Email Watchdog 中配置该账户的发信能力。"
    return ""


def _activate_reminders(action: dict, selected: list[int]) -> str:
    candidates = [item for item in (action.get("deadlines") or []) if isinstance(item, dict) and item.get("deadline")]
    if not candidates:
        return "这封邮件没有可创建的有效截止时间。"
    chosen = [candidates[index - 1] for index in selected if 1 <= index <= len(candidates)]
    if not chosen:
        return "没有识别到有效编号，请按提示重新选择。"
    created = []
    for item in chosen:
        active = dict(item)
        active["status"] = "active"
        active["reminders"] = active.get("reminders") or email_delivery._default_reminders(
            active.get("deadline"), active.get("title"), email_config.get_delivery_settings()
        )
        active["reminder_json"] = json.dumps(active.get("reminders") or [], ensure_ascii=False)
        email_store.upsert_schedule(active)
        created.append(active)
    email_delivery._sync_calendar_ics()
    details = [f"{index + 1}. `{item.get('deadline')}`（提前 24 小时、1 小时）" for index, item in enumerate(created)]
    return "✅ 已创建邮件提醒\n\n" + "\n".join(details) + "\n\n如已完成可忽略后续提醒。"


def _himalaya_binary() -> str:
    candidates = [os.getenv("HERMES_EMAIL_WATCHDOG_HIMALAYA_BIN", "").strip(), str(HERMES_HOME / "bin" / ("himalaya.exe" if os.name == "nt" else "himalaya")), shutil.which("himalaya") or ""]
    return next((value for value in candidates if value and (Path(value).is_file() or shutil.which(value))), "himalaya")


def _replace_template_body(template: str, body: str) -> str:
    """Retain reply/thread headers while removing implicit quotes/signatures."""
    value = str(template or "").replace("\r\n", "\n").replace("\r", "\n")
    if "\n\n" not in value:
        raise RuntimeError("Himalaya 回复模板缺少正文分隔符")
    headers, _implicit_body = value.split("\n\n", 1)
    if not re.search(r"(?im)^to:\s*\S", headers) or not re.search(r"(?im)^subject:\s*\S", headers):
        raise RuntimeError("Himalaya 回复模板缺少必要收件人或主题")
    return headers.rstrip() + "\n\n" + str(body).replace("\r\n", "\n").replace("\r", "\n") + "\n"


def _send_himalaya_reply(draft: dict) -> None:
    reply_settings = email_config.load_config().get("reply") or {}
    if not isinstance(reply_settings, dict) or not reply_settings.get("outbound_enabled", False):
        raise RuntimeError("该邮箱尚未启用经确认的邮件回复功能")
    action = draft["mail"]
    config = str(action.get("himalaya_config") or "").strip()
    if not config or not Path(config).expanduser().is_file():
        raise RuntimeError("Himalaya 配置文件不可用")
    binary = _himalaya_binary()
    expanded = str(Path(config).expanduser())
    if os.name == "nt":
        config_arg, cwd = ntpath.basename(expanded), ntpath.dirname(expanded) or "."
    else:
        config_arg, cwd = expanded, None
    common = [binary, "-c", config_arg]
    account = str(action.get("himalaya_account") or "").strip()
    # Do not expose user-authored body text in the process argument list. The
    # generated template is used only for reply/thread headers; its body is
    # replaced below before being passed through stdin-like template content.
    reply_cmd = common + ["template", "reply"] + (["-a", account] if account else []) + [str(action.get("message_id"))]
    generated = subprocess.run(reply_cmd, capture_output=True, text=True, timeout=45, cwd=cwd)
    if generated.returncode != 0 or not generated.stdout.strip():
        raise RuntimeError((generated.stderr or "无法生成回复模板").strip()[:500])
    final_template = _replace_template_body(generated.stdout, draft["body"])
    sent = subprocess.run(common + ["template", "send", final_template], capture_output=True, text=True, timeout=60, cwd=cwd)
    if sent.returncode != 0:
        raise RuntimeError((sent.stderr or "邮件发送失败").strip()[:500])


def _find_draft(reference_text: str, state: dict) -> tuple[str, dict] | tuple[None, None]:
    wanted = _fingerprint(reference_text)
    for key, draft in state.get("drafts", {}).items():
        if isinstance(draft, dict) and draft.get("status") in {"pending", "transmitting", "delivery_uncertain"} and draft.get("preview_fingerprint") == wanted:
            return key, draft
    return None, None


def _handle_inbound_locked(context: dict):
    if context.get("authorized") is not True:
        return None
    reference = context.get("reference") if isinstance(context.get("reference"), dict) else {}
    if not reference.get("present"):
        return None
    raw_typed = str(context.get("message") or "").replace("\r\n", "\n").replace("\r", "\n")
    typed = raw_typed.strip()
    reference_text = str(reference.get("text") or "")
    state = _state()

    draft_key, draft = _find_draft(reference_text, state)
    if draft is not None and typed in {"确认发送", "取消"}:
        receipt = _receipt_key(context, f"draft:{draft_key}:{typed}")
        if receipt in state["receipts"]:
            return _handled(state["receipts"][receipt], receipt)
        if draft.get("status") in {"transmitting", "delivery_uncertain"}:
            response = "该草稿已进入发送流程，但尚无法安全确认最终投递结果。为避免重复发信，系统不会自动重试；请先检查邮箱“已发送”目录。"
        elif typed == "取消":
            draft["status"] = "cancelled"
            response = "已取消，本次邮件草稿不会发送。"
        else:
            draft["status"] = "transmitting"
            draft["transmitting_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
            _atomic(ACTION_FILE, state)
            try:
                _send_himalaya_reply(draft)
                draft["status"] = "sent"
                draft["sent_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
                response = f"✅ 邮件已回复给 `{draft['mail'].get('from_addr')}`。"
            except subprocess.TimeoutExpired:
                draft["status"] = "delivery_uncertain"
                draft["last_error"] = "transport timeout"
                response = "⚠️ 发信通道超时，投递结果不确定。为避免重复发信，系统不会自动重试；请先检查邮箱“已发送”目录。"
            except Exception as exc:
                draft["status"] = "pending"
                draft["last_error"] = str(exc)[:300]
                response = f"❌ 邮件未发送：{str(exc)[:300]}"
        state["receipts"][receipt] = response
        _atomic(ACTION_FILE, state)
        return _handled(response, receipt)

    actions = _outbox_actions(reference_text, str(reference.get("message_id") or ""))
    if len(actions) != 1:
        return None if not actions else _handled("该气泡包含多封邮件，无法安全确定操作对象；请引用单封邮件的推送。", _receipt_key(context, "ambiguous"))
    action = actions[0]

    if typed == "提醒我" or re.fullmatch(r"\d+(?:\s*[,，、]\s*\d+)*", typed):
        candidates = [item for item in (action.get("deadlines") or []) if isinstance(item, dict) and item.get("deadline")]
        if typed == "提醒我":
            if len(candidates) != 1:
                return _handled("检测到多个截止时间，请回复需要提醒的编号，例如 `1,3`。", _receipt_key(context, "reminder-select"))
            selected = [1]
        else:
            selected = sorted({int(value) for value in re.findall(r"\d+", typed)})
        receipt = _receipt_key(context, "reminder:" + ",".join(map(str, selected)))
        if receipt in state["receipts"]:
            return _handled(state["receipts"][receipt], receipt)
        response = _activate_reminders(action, selected)
        state["receipts"][receipt] = response
        _atomic(ACTION_FILE, state)
        return _handled(response, receipt)

    if typed == "回复" or raw_typed.startswith("回复\n"):
        body = raw_typed.split("\n", 1)[1] if raw_typed.startswith("回复\n") else ""
        if not body.strip():
            return _handled("请在 `回复` 后换行输入邮件正文。", _receipt_key(context, "reply-empty"))
        reason = _reply_block_reason(action)
        if reason:
            return _handled(reason, _receipt_key(context, "reply-blocked"))
        signature = _signature(action)
        final_body = body + (("\n\n" + signature) if signature else "")
        draft_key = hashlib.sha256((str(context.get("message_id")) + _fingerprint(reference_text)).encode()).hexdigest()[:24]
        preview = f"### ✉️ 回复草稿\n\n**收件人** `{action.get('from_addr')}`\n\n**主题** `Re: {action.get('subject')}`\n\n**正文**\n\n{final_body}\n\n---\n\n确认无误后，请引用本草稿回复 `确认发送`；回复 `取消` 可放弃。"
        state["drafts"][draft_key] = {"status": "pending", "created_at": datetime.now().astimezone().isoformat(timespec="seconds"), "body": final_body, "mail": action, "preview_fingerprint": _fingerprint(preview)}
        _atomic(ACTION_FILE, state)
        return _handled(preview, f"email-draft-{draft_key}")
    return None


def handle_inbound(context: dict):
    """Serialize state transitions across hook threads and gateway processes."""
    with _file_lock(ACTION_FILE):
        return _handle_inbound_locked(context)
