#!/usr/bin/env python3
"""Local-only thread state tracking for received replies.

This module never sends, archives, deletes, moves, flags, or marks mailbox
messages. It only updates the skill-owned local JSON thread index.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import email_config

THREADS_FILE = Path(email_config.get_path("threads") or os.path.expanduser("~/.hermes/email_threads.json"))


def _load() -> dict:
    try:
        data = json.loads(THREADS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"threads": {}}
    except Exception:
        return {"threads": {}}


def _save(data: dict) -> None:
    THREADS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = THREADS_FILE.with_suffix(THREADS_FILE.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, THREADS_FILE)
    try:
        os.chmod(THREADS_FILE, 0o600)
    except OSError:
        pass


def update_thread_on_reply(from_email: str, reply_subject: str, reply_summary: str):
    """Mark a matching local waiting thread as replied; return its id or None."""
    data = _load()
    threads = data.setdefault("threads", {})
    addr = str(from_email or "").lower().strip()
    for thread_id, row in threads.items():
        if not isinstance(row, dict):
            continue
        participants = [str(v).lower() for v in row.get("participants", [])]
        if addr in participants and row.get("status") == "waiting_reply":
            row["status"] = "replied"
            row["last_reply"] = datetime.now().astimezone().isoformat(timespec="seconds")
            row["reply_summary"] = str(reply_summary or "")
            row["reply_subject"] = str(reply_subject or "")
            _save(data)
            return thread_id
    return None
