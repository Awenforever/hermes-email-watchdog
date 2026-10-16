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
from email.utils import parsedate_to_datetime

DEFAULT_MAX_CHARS = 12000
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


def _split_oversized(paragraph: str) -> list[str]:
    if len(paragraph) <= MAX_BLOCK_CHARS:
        return [paragraph]
    pieces: list[str] = []
    remaining = paragraph
    while len(remaining) > MAX_BLOCK_CHARS:
        window = remaining[:MAX_BLOCK_CHARS]
        cut = -1
        for marker in _SENTENCE_ENDS:
            candidate = window.rfind(marker)
            if candidate > cut:
                cut = candidate
        if cut < MAX_BLOCK_CHARS // 2:
            cut = MAX_BLOCK_CHARS - 1
        pieces.append(remaining[: cut + 1].rstrip())
        remaining = remaining[cut + 1 :].lstrip()
    if remaining:
        pieces.append(remaining)
    return pieces


def prepare_blocks(text: str) -> list[str]:
    """Clean the raw body and split it into transport-safe paragraph blocks."""
    from email_notification_renderer import clean_body

    cleaned = clean_body(text)
    if not cleaned:
        return []
    paragraphs = [p.strip() for p in cleaned.split("\n\n")]
    blocks: list[str] = []
    for paragraph in paragraphs:
        if not paragraph:
            continue
        blocks.extend(_split_oversized(paragraph))
    return blocks


def _format_date(raw: object) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    try:
        return parsedate_to_datetime(text).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return text[:40]


def render_original(action: dict, fetched: dict, max_chars: int | None = None) -> str:
    """Render header + full body blocks as one WeChat reply string."""
    budget = int(max_chars or _max_chars())
    blocks = prepare_blocks(str((fetched or {}).get("text") or ""))

    subject = " ".join(str((action or {}).get("subject") or "").split()) or "（无主题）"
    from_name = " ".join(str((action or {}).get("from_name") or "").split())
    from_addr = " ".join(str((action or {}).get("from_addr") or "").split())
    sender = f"{from_name} <{from_addr}>" if from_name and from_addr else (from_addr or from_name)
    date = _format_date((fetched or {}).get("date_sent"))
    account = " ".join(str((action or {}).get("account") or "").split())

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


def build_original_reply(action: dict, uid: str, deadline_seconds: int | None = None) -> str:
    """Fetch and render the original email for one resolved mail action.

    Raises on transport/fetch failures so the dispatcher can reply with a
    retryable error and skip receipt caching; friendly refusals are returned
    as normal strings.
    """
    action = action if isinstance(action, dict) else {}
    if str(action.get("account_type") or "").lower() != "himalaya":
        return "该邮箱通道暂不支持查看原文，请在邮箱客户端中查看。"
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
