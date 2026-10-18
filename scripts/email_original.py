"""Full-original-email view for the ``@原文`` WeChat command.

Read-only: fetches the message via Himalaya (export preferred, preview
fallback — neither mutates the Seen flag) and renders it as WeChat-safe
Markdown. Layout contract with the transport: the WeChat adapter packs
Markdown blocks (blank-line separated) into <=2000-char bubbles and HARD
TRUNCATES any oversized block, so every emitted block must stay below
``MAX_BLOCK_CHARS`` and blocks must be separated by blank lines.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

DEFAULT_MAX_CHARS = 16000
MIN_MAX_CHARS = 2000
MAX_MAX_CHARS = 20000
# Bubbles are capped at 2000 chars by the transport, which also appends a
# small footer; 1600 keeps every block safely under both.
MAX_BLOCK_CHARS = 1600

_SENTENCE_ENDS = ("。", "！", "？", "!", "?", "；", ";", "\n", ".", " ")


def _max_chars() -> int:
    try:
        import email_config
        settings = email_config.get_notification_settings() or {}
        value = int(settings.get("original_full_max_chars") or DEFAULT_MAX_CHARS)
    except Exception:
        return DEFAULT_MAX_CHARS
    return max(MIN_MAX_CHARS, min(MAX_MAX_CHARS, value))


def _choose_cut(window: str) -> int:
    """Break index within ``window`` (len <= MAX_BLOCK_CHARS) that never splits a
    space-delimited token/URL unless a single unbroken run exceeds the block cap.

    ``_SENTENCE_ENDS`` already includes the space and newline, so a late break
    keeps the block well-filled. When the only break is early we still take it —
    leaving the long token whole in the remainder — instead of hard-cutting
    mid-token, which used to break links inside space-less runs (inline base64,
    long tracking URLs, punctuation-free CJK walls).
    """
    best = -1
    for marker in _SENTENCE_ENDS:
        candidate = window.rfind(marker)
        if candidate > best:
            best = candidate
    if best >= MAX_BLOCK_CHARS // 2:
        return best
    if best > 0:
        return best
    # A single unbroken run longer than MAX_BLOCK_CHARS: no safe boundary exists,
    # so a hard cut is unavoidable.
    return MAX_BLOCK_CHARS - 1


def _split_oversized(paragraph: str) -> list[str]:
    if len(paragraph) <= MAX_BLOCK_CHARS:
        return [paragraph]
    pieces: list[str] = []
    remaining = paragraph
    while len(remaining) > MAX_BLOCK_CHARS:
        window = remaining[:MAX_BLOCK_CHARS]
        cut = _choose_cut(window)
        pieces.append(remaining[: cut + 1].rstrip())
        remaining = remaining[cut + 1 :].lstrip()
    if remaining:
        pieces.append(remaining)
    return pieces


def prepare_blocks(text: str) -> list[str]:
    """Clean the raw body and split it into transport-safe paragraph blocks.

    Uses ``clean_body(..., join_lines=False)``: ``@原文`` means the *original*
    text, so the author's line breaks are preserved instead of being reflowed
    into walls the way the notification-card cleaner does.
    """
    from email_notification_renderer import clean_body

    cleaned = clean_body(text, join_lines=False)
    if not cleaned:
        return []
    paragraphs = [p.strip() for p in cleaned.split("\n\n")]
    blocks: list[str] = []
    for paragraph in paragraphs:
        if not paragraph:
            continue
        blocks.extend(_split_oversized(paragraph))
    return blocks


def _beijing_tz():
    """Profile timezone (Asia/Shanghai) with a fixed +08:00 fallback.

    ``zoneinfo`` needs tzdata, which is present in the Linux container but may be
    missing elsewhere; the fixed offset keeps the label correct regardless.
    """
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo("Asia/Shanghai")
    except Exception:
        return timezone(timedelta(hours=8))


def _parse_date(text: str):
    """Parse an RFC2822 (Date header) or ISO-8601 timestamp; None if unparseable."""
    try:
        return parsedate_to_datetime(text)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None


def _format_date(raw: object) -> str:
    """Render a message timestamp for the ``@原文`` header.

    An offset-bearing header (e.g. Gmail's ``+0000``) is converted to the profile
    timezone and labelled 北京时间, so a UTC ``04:36`` no longer reads as a local
    early-morning send. A naive header has no known zone, so its wall-clock is
    shown unlabelled rather than falsely stamped as Beijing time.
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    parsed = _parse_date(text)
    if parsed is None:
        return text[:40]
    if parsed.tzinfo is not None:
        local = parsed.astimezone(_beijing_tz())
        return local.strftime("%Y-%m-%d %H:%M") + "（北京时间）"
    return parsed.strftime("%Y-%m-%d %H:%M")


def _cap(value: object, limit: int) -> str:
    """Whitespace-collapse and length-cap a header field.

    The header is emitted as a single block, and the transport HARD-TRUNCATES any
    block over 2000 chars — so an uncapped subject/sender from an automated
    mailer could silently drop the whole header. Capping each field keeps it far
    under the limit.
    """
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def render_original(action: dict, fetched: dict, max_chars: int | None = None) -> str:
    """Render header + full body blocks as one WeChat reply string."""
    budget = int(max_chars or _max_chars())
    blocks = prepare_blocks(str((fetched or {}).get("text") or ""))

    subject = _cap((action or {}).get("subject"), 160) or "（无主题）"
    from_name = _cap((action or {}).get("from_name"), 80)
    from_addr = _cap((action or {}).get("from_addr"), 120)
    sender = f"{from_name} <{from_addr}>" if from_name and from_addr else (from_addr or from_name)
    date = _format_date((fetched or {}).get("date_sent"))
    account = _cap((action or {}).get("account"), 60)

    header = ["### 📄 邮件原文", f"**主题** {subject}"]
    if sender:
        header.append(f"**发件人** {sender}")
    if date:
        header.append(f"**时间** {date}")
    if account:
        header.append(f"**账户** {account}")

    if not blocks:
        attachments = (fetched or {}).get("attachments") or []
        note = "这封邮件没有可显示的文本正文（可能为纯图片或附件邮件"
        if attachments:
            note += f"，含 {len(attachments)} 个附件"
        note += "），请在邮箱客户端中查看。"
        return "\n".join(header + ["---"]) + "\n\n" + note

    total_chars = sum(len(block) for block in blocks)
    shown: list[str] = []
    used = 0
    for block in blocks:
        if used + len(block) > budget:
            break
        shown.append(block)
        used += len(block)
    body = "\n\n".join(shown)
    out = "\n".join(header + ["---"]) + "\n\n" + body
    if len(shown) < len(blocks):
        out += (
            f"\n\n＞ 全文约 {total_chars} 字，已显示前 {used} 字；剩余部分请在邮箱中查看。"
        )
    return out


def _fetch_deadline_seconds() -> int:
    try:
        import email_config
        settings = email_config.get_notification_settings() or {}
        value = int(settings.get("original_fetch_deadline_seconds") or 90)
    except Exception:
        return 90
    return max(30, min(300, value))


def _fetch_with_deadline(config_path: str, uid: str, alias: str | None, deadline: int):
    """Bound total fetch time; Himalaya variant/fallback chains can otherwise
    stack per-attempt timeouts into multi-minute interactive stalls."""
    import concurrent.futures

    import email_watch

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(email_watch.read_himalaya, config_path, uid, account=alias)
        return future.result(timeout=deadline)
    except concurrent.futures.TimeoutError:
        raise TimeoutError(f"original-text fetch exceeded {deadline}s") from None
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _load_cached_original(action: dict) -> dict | None:
    """Serve @原文 from the watchdog local full-body cache when available.

    The live IMAP export fails whenever the provider is at its simultaneous
    connection cap (Gmail "Too many simultaneous connections"), yet the full body
    was already persisted at delivery time under CACHE_DIR/{message_id}.json.
    Prefer that cache; return None only when it is missing or has no body, so the
    caller falls back to a live fetch.
    """
    storage_id = str((action or {}).get("message_id") or "").strip()
    if not storage_id:
        return None
    try:
        import json
        import os

        import email_watch
        cache_dir = getattr(email_watch, "CACHE_DIR", "")
        if not cache_dir:
            return None
        path = os.path.join(cache_dir, f"{storage_id}.json")
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    body = str(data.get("body") or "")
    if not body.strip():
        return None
    return {
        "text": body,
        "date_sent": data.get("date_sent") or data.get("date_received") or "",
        "attachments": data.get("attachments") or [],
    }

def build_original_reply(action: dict, uid: str, deadline_seconds: int | None = None) -> str:
    """Fetch and render the original email for one resolved mail action.

    Raises on transport/fetch failures so the dispatcher can reply with a
    retryable error and skip receipt caching; friendly refusals are returned
    as normal strings.
    """
    action = action if isinstance(action, dict) else {}
    if str(action.get("account_type") or "").lower() != "himalaya":
        return "该邮箱通道暂不支持查看原文，请在邮箱客户端中查看。"
    cached = _load_cached_original(action)
    if cached is not None:
        return render_original(action, cached)
    config_path = str(action.get("himalaya_config") or "").strip()
    uid = str(uid or "").strip()
    if not config_path or not uid:
        return "这条推送缺少定位邮件所需的信息，无法获取原文；请引用升级后收到的新推送重试。"
    alias = str(action.get("himalaya_account") or "").strip() or None
    fetched = _fetch_with_deadline(
        config_path, uid, alias, int(deadline_seconds or _fetch_deadline_seconds())
    )
    if not isinstance(fetched, dict):
        raise RuntimeError(f"himalaya fetch returned {type(fetched).__name__}")
    return render_original(action, fetched)
