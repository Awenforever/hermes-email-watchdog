"""Authorized quote-driven reminder and reply actions for Email Watchdog."""

from __future__ import annotations

import hashlib
import html
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
from email.utils import parseaddr
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


def _matchable_preview(text: object) -> str:
    """Canonical text for an iLink quote preview, which may be truncated."""
    value = _normalized(text)
    heading = value.find("###")
    if heading >= 0:
        value = value[heading:]
    value = re.sub(r"[`*_#\\]", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value.rstrip(" .…")


def _preview_matches(reference: object, full_text: object) -> bool:
    reference_normal = _matchable_preview(reference)
    full_normal = _matchable_preview(full_text)
    if not reference_normal or not full_normal:
        return False
    if reference_normal == full_normal:
        return True
    # iLink's title is a display preview, not a stable full-message field.  A
    # sufficiently long unique prefix is safe; short snippets are not.
    return len(reference_normal) >= 32 and (
        full_normal.startswith(reference_normal) or reference_normal.startswith(full_normal)
    )


def _reference_values(reference_text: str, reference_title: str = "") -> list[str]:
    values = []
    for value in (reference_text, reference_title):
        value = str(value or "").strip()
        if value and value not in values:
            values.append(value)
    return values


def _outbox_actions(reference_text: str, reference_message_id: str = "", reference_title: str = "") -> list[dict]:
    wanted_values = _reference_values(reference_text, reference_title)
    wanted = {_fingerprint(value) for value in wanted_values}
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
        entry_text = entry.get("text")
        entry_match = _fingerprint(entry_text) in wanted or any(
            _preview_matches(value, entry_text) for value in wanted_values
        )
        text_part = (entry.get("parts") or {}).get("text") if isinstance(entry.get("parts"), dict) else {}
        adapter_message_id = str((text_part or {}).get("adapter_message_id") or entry.get("adapter_message_id") or "").strip()
        for action in actions:
            if wanted_id and adapter_message_id and adapter_message_id == wanted_id:
                id_matches.append(dict(action))
                continue
            action_text = action.get("notification_text")
            if entry_match or _fingerprint(action_text) in wanted or any(
                _preview_matches(value, action_text) for value in wanted_values
            ):
                matches.append(dict(action))
    return id_matches or matches


def _looks_like_watchdog_reference(reference_text: str, reference_title: str = "") -> bool:
    for value in _reference_values(reference_text, reference_title):
        normalized = _matchable_preview(value)
        if re.search(r"(?:新邮件|账户确认|账户状态|研究简报|学术快讯|订阅更新|活动与日程|待办与截止时间|学校通知).*｜", normalized):
            return True
    return False


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
    account = str(action.get("account") or "").strip().casefold()
    # Account labels originate in mail backends and are presentation-facing;
    # their casing is not a stable identifier (for example ``USTC`` versus
    # ``ustc``).  Signature selection must therefore be case-insensitive while
    # preserving the configured signature text verbatim.
    normalized_signatures = {
        str(key).strip().casefold(): value for key, value in signatures.items()
    }
    value = normalized_signatures.get(account, reply.get("default_signature", ""))
    return str(value or "").replace("\r\n", "\n").strip()


def _signature_layout(value: str) -> tuple[bool, str]:
    """Interpret a signature separator without leaking Markdown into email.

    A leading line made only of dashes is authoring metadata: chat Markdown may
    render it as a rule, but a plain email client prints three literal dashes.
    Treat it as a semantic divider and compact the deliberately line-separated
    signature fields beneath it. Signatures without that marker retain a
    single intentional blank line between groups.
    """
    lines = str(value or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    divider = bool(lines and re.fullmatch(r"[-—─_]{3,}", lines[0].strip()))
    if divider:
        lines = [line for line in lines[1:] if line.strip()]
    else:
        compact = []
        blank = False
        for line in lines:
            if not line.strip():
                if compact and not blank:
                    compact.append("")
                blank = True
            else:
                compact.append(line)
                blank = False
        while compact and compact[-1] == "":
            compact.pop()
        lines = compact
    return divider, "\n".join(lines)


def _render_signature_preview(body: str, signature: str) -> str:
    divider, content = _signature_layout(signature)
    if not content:
        return body
    separator = "\n\n---\n\n" if divider else "\n\n"
    return body + separator + content


def _render_reply_mml(body: str, signature: str) -> str:
    """Build safe text/plain + HTML alternatives for a confirmed reply."""
    normalized_body = str(body or "").replace("\r\n", "\n").replace("\r", "\n")
    divider, signature_text = _signature_layout(signature)
    # MML directives are active even inside the text alternative. Refuse the
    # directive prefix instead of altering user-authored reply text silently.
    if "<#" in normalized_body or "<#" in signature_text:
        raise RuntimeError("邮件正文或签名包含保留的 MML 指令前缀 `<#`，已阻止发送")
    plain = normalized_body
    if signature_text:
        plain += ("\n\n──────────────\n" if divider else "\n\n") + signature_text

    body_html = html.escape(normalized_body, quote=False).replace("\n", "<br>\n")
    signature_lines = [
        f'<div style="margin:0;">{html.escape(line, quote=False)}</div>'
        for line in signature_text.split("\n") if line
    ]
    signature_html = ""
    if signature_lines:
        rule = (
            '<div style="border-top:1px solid #9ca3af;width:140px;'
            'margin:22px 0 8px 0;"></div>'
            if divider else '<div style="height:18px;"></div>'
        )
        signature_html = rule + '<div style="line-height:1.2;font-weight:600;">' + "".join(signature_lines) + "</div>"
    rich = (
        '<!doctype html><html><body style="margin:0;font-family:Arial,\'Microsoft YaHei\',sans-serif;'
        'font-size:14px;line-height:1.6;color:#111827;">'
        f'<div style="white-space:normal;">{body_html}</div>{signature_html}</body></html>'
    )
    return (
        "<#multipart type=alternative>\n"
        + plain
        + "\n<#part type=text/html>\n"
        + rich
        + "\n<#/multipart>"
    )


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


def _valid_single_recipient(value: str) -> str:
    candidate = str(value or "").strip()
    if not candidate or any(char in candidate for char in ",;\r\n"):
        return ""
    display, address = parseaddr(candidate)
    if display or address != candidate or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", address):
        return ""
    return address


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


def _replace_forward_template_body(template: str, body: str, recipient: str) -> str:
    """Keep Himalaya's original-message/MML block and add only explicit text."""
    value = str(template or "").replace("\r\n", "\n").replace("\r", "\n")
    if "\n\n" not in value:
        raise RuntimeError("Himalaya 转发模板缺少正文分隔符")
    headers, implicit_body = value.split("\n\n", 1)
    if not re.search(r"(?im)^subject:\s*\S", headers):
        raise RuntimeError("Himalaya 转发模板缺少主题")
    if re.search(r"(?im)^to:", headers):
        headers = re.sub(r"(?im)^to:.*$", f"To: {recipient}", headers, count=1)
    else:
        headers += f"\nTo: {recipient}"
    marker = re.search(r"(?im)^-{2,}\s*Forwarded Message\s*-{2,}\s*$", implicit_body)
    if marker is None:
        raise RuntimeError("Himalaya 转发模板缺少原邮件边界")
    forwarded = implicit_body[marker.start():].lstrip()
    prefix = str(body or "").replace("\r\n", "\n").replace("\r", "\n")
    content = (prefix.rstrip() + "\n\n" if prefix else "") + forwarded.rstrip() + "\n"
    return headers.rstrip() + "\n\n" + content


def _himalaya_context(action: dict) -> tuple[list[str], str | None, str]:
    config = str(action.get("himalaya_config") or "").strip()
    if not config or not Path(config).expanduser().is_file():
        raise RuntimeError("Himalaya 配置文件不可用")
    expanded = str(Path(config).expanduser())
    if os.name == "nt":
        config_arg, cwd = ntpath.basename(expanded), ntpath.dirname(expanded) or "."
    else:
        config_arg, cwd = expanded, None
    return [_himalaya_binary(), "-c", config_arg], cwd, str(action.get("himalaya_account") or "").strip()


def _send_himalaya_reply(draft: dict) -> None:
    reply_settings = email_config.load_config().get("reply") or {}
    if not isinstance(reply_settings, dict) or not reply_settings.get("outbound_enabled", False):
        raise RuntimeError("该邮箱尚未启用经确认的邮件回复功能")
    action = draft["mail"]
    common, cwd, account = _himalaya_context(action)
    # Do not expose user-authored body text in the process argument list. The
    # generated template is used only for reply/thread headers; its body is
    # replaced below before being passed through stdin-like template content.
    reply_cmd = common + ["template", "reply"] + (["-a", account] if account else []) + [str(action.get("message_id"))]
    generated = subprocess.run(reply_cmd, capture_output=True, text=True, timeout=45, cwd=cwd)
    if generated.returncode != 0 or not generated.stdout.strip():
        raise RuntimeError((generated.stderr or "无法生成回复模板").strip()[:500])
    if "user_body" in draft or "signature" in draft:
        rendered_body = _render_reply_mml(draft.get("user_body", ""), draft.get("signature", ""))
    else:
        rendered_body = draft["body"]
    final_template = _replace_template_body(generated.stdout, rendered_body)
    # Himalaya accepts a short one-line template as an argv value, but its MML
    # parser cannot reliably parse a complete multi-line template that way.
    # Its non-interactive contract is to read the template from stdin.  This
    # also keeps user-authored mail bodies out of process listings.
    sent = subprocess.run(
        common + ["template", "send"], input=final_template,
        capture_output=True, text=True, timeout=60, cwd=cwd,
    )
    if sent.returncode != 0:
        raise RuntimeError((sent.stderr or "邮件发送失败").strip()[:500])


def _send_himalaya_forward(draft: dict) -> None:
    reply_settings = email_config.load_config().get("reply") or {}
    if not isinstance(reply_settings, dict) or not reply_settings.get("outbound_enabled", False):
        raise RuntimeError("该邮箱尚未启用经确认的邮件发送功能")
    action = draft["mail"]
    recipient = _valid_single_recipient(draft.get("recipient", ""))
    if not recipient:
        raise RuntimeError("转发收件邮箱无效")
    common, cwd, account = _himalaya_context(action)
    command = common + ["template", "forward", "-H", f"To:{recipient}"]
    if account:
        command += ["-a", account]
    command += [str(action.get("message_id"))]
    generated = subprocess.run(command, capture_output=True, text=True, timeout=45, cwd=cwd)
    if generated.returncode != 0 or not generated.stdout.strip():
        raise RuntimeError((generated.stderr or "无法生成转发模板").strip()[:500])
    final_template = _replace_forward_template_body(generated.stdout, draft.get("body", ""), recipient)
    sent = subprocess.run(
        common + ["template", "send"], input=final_template,
        capture_output=True, text=True, timeout=60, cwd=cwd,
    )
    if sent.returncode != 0:
        raise RuntimeError((sent.stderr or "邮件发送失败").strip()[:500])


def _send_himalaya_draft(draft: dict) -> None:
    if draft.get("kind") == "forward":
        _send_himalaya_forward(draft)
    else:
        _send_himalaya_reply(draft)


def _find_draft(reference_text: str, reference_title: str, state: dict) -> tuple[str, dict] | tuple[None, None]:
    wanted_values = _reference_values(reference_text, reference_title)
    wanted = {_fingerprint(value) for value in wanted_values}
    matches = []
    for key, draft in state.get("drafts", {}).items():
        if not isinstance(draft, dict) or draft.get("status") not in {"pending", "transmitting", "delivery_uncertain"}:
            continue
        if draft.get("preview_fingerprint") in wanted or any(
            _preview_matches(value, draft.get("preview_text")) for value in wanted_values
        ):
            matches.append((key, draft))
    if len(matches) == 1:
        return matches[0]
    return None, None


def _explicit_mail_command(raw_typed: str) -> bool:
    typed = str(raw_typed or "").strip()
    return bool(
        typed in {"@回复", "@确认发送", "@取消"}
        or raw_typed.startswith("@回复\n")
        or re.match(r"^@转发(?:\s|$)", raw_typed)
    )


def _handle_inbound_locked(context: dict):
    if context.get("authorized") is not True:
        return None
    raw_typed = str(context.get("message") or "").replace("\r\n", "\n").replace("\r", "\n")
    typed = raw_typed.strip()
    reference = context.get("reference") if isinstance(context.get("reference"), dict) else {}
    if not reference.get("present"):
        if _explicit_mail_command(raw_typed):
            return _handled(
                "没有收到可验证的微信引用，无法安全确定原邮件。请长按目标邮件推送选择“引用”，再发送该命令；系统不会猜测最近一封邮件。",
                _receipt_key(context, "missing-mail-reference"),
            )
        return None
    reference_text = str(reference.get("text") or "")
    reference_title = str(reference.get("title") or "")
    state = _state()

    draft_key, draft = _find_draft(reference_text, reference_title, state)
    if draft is not None and typed in {"确认发送", "取消", "@确认发送", "@取消"}:
        receipt = _receipt_key(context, f"draft:{draft_key}:{typed}")
        if receipt in state["receipts"]:
            return _handled(state["receipts"][receipt], receipt)
        if draft.get("status") in {"transmitting", "delivery_uncertain"}:
            response = "该草稿已进入发送流程，但尚无法安全确认最终投递结果。为避免重复发信，系统不会自动重试；请先检查邮箱“已发送”目录。"
        elif typed in {"取消", "@取消"}:
            draft["status"] = "cancelled"
            response = "已取消，本次邮件草稿不会发送。"
        else:
            draft["status"] = "transmitting"
            draft["transmitting_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
            _atomic(ACTION_FILE, state)
            try:
                _send_himalaya_draft(draft)
                draft["status"] = "sent"
                draft["sent_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
                if draft.get("kind") == "forward":
                    response = f"✅ 邮件已转发给 `{draft.get('recipient')}`。"
                else:
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

    actions = _outbox_actions(
        reference_text,
        str(reference.get("message_id") or ""),
        reference_title,
    )
    if len(actions) != 1:
        if actions:
            return _handled("该气泡包含多封邮件，无法安全确定操作对象；请引用单封邮件的推送。", _receipt_key(context, "ambiguous"))
        mail_command = (
            typed in {"提醒我", "确认发送", "取消", "@确认发送", "@取消"}
            or _explicit_mail_command(raw_typed)
            or bool(re.fullmatch(r"\d+(?:\s*[,，、]\s*\d+)*", typed))
        )
        if mail_command and _looks_like_watchdog_reference(reference_text, reference_title):
            return _handled(
                "这是一条旧版或已超出本地保留范围的邮件推送，当前无法安全还原收件人和原邮件，已阻止误发。请引用升级后新收到的邮件推送重试。",
                _receipt_key(context, "unresolved-mail-reference"),
            )
        return None
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

    if typed == "@回复" or raw_typed.startswith("@回复\n"):
        body = raw_typed.split("\n", 1)[1] if raw_typed.startswith("@回复\n") else ""
        if not body.strip():
            return _handled("请在 `@回复` 后换行输入邮件正文。", _receipt_key(context, "reply-empty"))
        reason = _reply_block_reason(action)
        if reason:
            return _handled(reason, _receipt_key(context, "reply-blocked"))
        signature = _signature(action)
        final_body = _render_signature_preview(body, signature)
        draft_key = hashlib.sha256((str(context.get("message_id")) + _fingerprint(reference_text)).encode()).hexdigest()[:24]
        preview = f"### ✉️ 回复草稿\n\n**收件人** `{action.get('from_addr')}`\n\n**主题** `Re: {action.get('subject')}`\n\n**正文**\n\n{final_body}\n\n> 确认无误后，请引用本草稿回复 `@确认发送`；回复 `@取消` 可放弃。"
        state["drafts"][draft_key] = {"kind": "reply", "status": "pending", "created_at": datetime.now().astimezone().isoformat(timespec="seconds"), "body": final_body, "user_body": body, "signature": signature, "mail": action, "preview_text": preview, "preview_fingerprint": _fingerprint(preview)}
        _atomic(ACTION_FILE, state)
        return _handled(preview, f"email-draft-{draft_key}")

    forward = re.match(r"^@转发[ \t]+([^\s]+)[ \t]*(?:\n([\s\S]*))?$", raw_typed)
    if forward:
        recipient = _valid_single_recipient(forward.group(1))
        if not recipient:
            return _handled("转发收件邮箱格式无效；一次只能填写一个完整邮箱地址。", _receipt_key(context, "forward-recipient-invalid"))
        if str(action.get("account_type") or "").lower() != "himalaya":
            return _handled("该邮箱当前没有可验证的转发通道。", _receipt_key(context, "forward-unavailable"))
        body = forward.group(2) or ""
        signature = _signature(action) if body.strip() else ""
        final_body = body + (("\n\n" + signature) if signature else "")
        draft_key = hashlib.sha256((str(context.get("message_id")) + recipient + _fingerprint(reference_text)).encode()).hexdigest()[:24]
        body_preview = final_body if final_body else "_无附言，将直接转发原邮件及其附件。_"
        subject = str(action.get("subject") or "")
        preview = f"### ✉️ 转发草稿\n\n**收件人** `{recipient}`\n\n**主题** `Fwd: {subject}`\n\n**附言**\n\n{body_preview}\n\n> 确认无误后，请引用本草稿回复 `@确认发送`；回复 `@取消` 可放弃。"
        state["drafts"][draft_key] = {"kind": "forward", "status": "pending", "created_at": datetime.now().astimezone().isoformat(timespec="seconds"), "recipient": recipient, "body": final_body, "mail": action, "preview_text": preview, "preview_fingerprint": _fingerprint(preview)}
        _atomic(ACTION_FILE, state)
        return _handled(preview, f"email-forward-draft-{draft_key}")

    if typed.startswith("@回复") or typed.startswith("@转发"):
        return _handled(
            "命令格式无效。回复邮件请使用 `@回复` 后换行输入正文；转发请使用 `@转发 收件邮箱`，下一行可选填附言。",
            _receipt_key(context, "mail-command-invalid"),
        )
    return None


def handle_inbound(context: dict):
    """Serialize state transitions across hook threads and gateway processes."""
    with _file_lock(ACTION_FILE):
        return _handle_inbound_locked(context)
