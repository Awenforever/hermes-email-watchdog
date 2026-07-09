#!/usr/bin/env python3
"""
Email Store — unified SQLite storage for messages, threads, attachments, links, actions.
Replaces scattered JSON files. Thread-safe with WAL mode.
"""

import json, os, sqlite3, threading, time
from datetime import datetime
from pathlib import Path

try:
    import email_config
except ImportError:
    email_config = None

DB_PATH = email_config.get_path("db") if email_config else os.path.expanduser("~/.hermes/email.db")

_local = threading.local()

def _get_conn():
    if not hasattr(_local, "conn") or _local.conn is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        _local.conn = sqlite3.connect(DB_PATH)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


def init_db():
    conn = _get_conn()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS messages (
        id TEXT PRIMARY KEY,
        account TEXT NOT NULL,
        mailbox TEXT DEFAULT 'INBOX',
        uid TEXT,
        rfc_message_id TEXT,
        in_reply_to TEXT,
        thread_id TEXT,
        subject TEXT,
        subject_norm TEXT,
        from_name TEXT,
        from_email TEXT,
        from_domain TEXT,
        reply_to TEXT,
        to_emails TEXT,
        cc_emails TEXT,
        date_sent TEXT,
        date_seen TEXT,
        body_hash TEXT,
        cache_path TEXT,
        rule_category TEXT,
        llm_category TEXT,
        final_category TEXT,
        importance TEXT DEFAULT 'medium',
        urgency TEXT DEFAULT 'none',
        needs_reply INTEGER DEFAULT 0,
        has_deadline INTEGER DEFAULT 0,
        has_attachment INTEGER DEFAULT 0,
        has_links INTEGER DEFAULT 0,
        trust_score REAL DEFAULT 0,
        risk_score REAL DEFAULT 0,
        trust_label TEXT DEFAULT 'unknown',
        risk_label TEXT DEFAULT 'low',
        summary_short TEXT,
        summary_long TEXT,
        action_summary TEXT,
        deadline TEXT,
        deadline_timezone TEXT,
        format_decision TEXT,
        semantic_category TEXT,
        analysis_json TEXT,
        attachment_policy TEXT,
        delivered_text_hash TEXT,
        push_status TEXT DEFAULT 'pending',
        pushed_at TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS threads (
        thread_id TEXT PRIMARY KEY,
        root_message_id TEXT,
        subject_norm TEXT,
        participants TEXT,
        first_seen_at TEXT,
        last_seen_at TEXT,
        thread_summary TEXT,
        last_user_action TEXT,
        status TEXT DEFAULT 'active',
        needs_followup INTEGER DEFAULT 0,
        followup_due_at TEXT,
        deadline_at TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS attachments (
        id TEXT PRIMARY KEY,
        message_id TEXT NOT NULL,
        filename TEXT,
        content_type TEXT,
        size_bytes INTEGER,
        sha256 TEXT,
        source TEXT,
        local_path TEXT,
        download_status TEXT DEFAULT 'pending',
        risk_label TEXT DEFAULT 'low',
        risk_reason TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (message_id) REFERENCES messages(id)
    );

    CREATE TABLE IF NOT EXISTS links (
        id TEXT PRIMARY KEY,
        message_id TEXT NOT NULL,
        url TEXT,
        display_text TEXT,
        domain TEXT,
        final_url TEXT,
        link_type TEXT DEFAULT 'unknown',
        risk_label TEXT DEFAULT 'low',
        extract_status TEXT DEFAULT 'pending',
        extracted_title TEXT,
        extracted_summary TEXT,
        downloaded_file_path TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (message_id) REFERENCES messages(id)
    );

    CREATE TABLE IF NOT EXISTS actions (
        id TEXT PRIMARY KEY,
        message_id TEXT,
        thread_id TEXT,
        action_type TEXT NOT NULL,
        action_status TEXT DEFAULT 'pending',
        risk_level TEXT DEFAULT 'low',
        requires_approval INTEGER DEFAULT 0,
        plan_json TEXT,
        result_json TEXT,
        approval_prompt TEXT,
        approved_by_user INTEGER DEFAULT 0,
        approved_at TEXT,
        executed_at TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (message_id) REFERENCES messages(id)
    );

    CREATE TABLE IF NOT EXISTS schedules (
        id TEXT PRIMARY KEY,
        message_id TEXT NOT NULL,
        title TEXT,
        action_needed TEXT,
        deadline TEXT,
        timezone TEXT,
        status TEXT DEFAULT 'active',
        reminder_json TEXT,
        reminders_sent_json TEXT DEFAULT '[]',
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (message_id) REFERENCES messages(id)
    );

    CREATE TABLE IF NOT EXISTS contacts (
        email TEXT PRIMARY KEY,
        name TEXT,
        aliases TEXT,
        affiliation TEXT,
        role TEXT,
        own_domain INTEGER DEFAULT 0,
        trust_level TEXT DEFAULT 'unknown',
        trust_score REAL DEFAULT 0,
        interaction_count INTEGER DEFAULT 0,
        user_replied INTEGER DEFAULT 0,
        user_sent INTEGER DEFAULT 0,
        first_seen TEXT,
        last_seen TEXT,
        notes TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    );

    CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(thread_id);
    CREATE INDEX IF NOT EXISTS idx_messages_account ON messages(account);
    CREATE INDEX IF NOT EXISTS idx_messages_date ON messages(date_sent);
    CREATE INDEX IF NOT EXISTS idx_messages_push ON messages(push_status);
    CREATE INDEX IF NOT EXISTS idx_attachments_msg ON attachments(message_id);
    CREATE INDEX IF NOT EXISTS idx_links_msg ON links(message_id);
    CREATE INDEX IF NOT EXISTS idx_actions_msg ON actions(message_id);
    CREATE INDEX IF NOT EXISTS idx_contacts_email ON contacts(email);
    CREATE INDEX IF NOT EXISTS idx_schedules_msg ON schedules(message_id);
    CREATE INDEX IF NOT EXISTS idx_schedules_deadline ON schedules(deadline);
    """)
    _migrate_columns(conn)
    conn.commit()


def _migrate_columns(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(messages)").fetchall()}
    wanted = {
        "deadline": "TEXT",
        "deadline_timezone": "TEXT",
        "format_decision": "TEXT",
        "semantic_category": "TEXT",
        "analysis_json": "TEXT",
        "attachment_policy": "TEXT",
        "delivered_text_hash": "TEXT",
    }
    for name, sql_type in wanted.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE messages ADD COLUMN {name} {sql_type}")


# ── Message CRUD ────────────────────────────────────────────────

def upsert_message(data: dict):
    """Insert or update a message. Returns message id."""
    conn = _get_conn()
    msg_id = data["id"]
    existing = conn.execute("SELECT id FROM messages WHERE id=?", (msg_id,)).fetchone()
    
    if existing:
        cols = ", ".join(f"{k}=?" for k in data if k != "id")
        vals = [data[k] for k in data if k != "id"] + [msg_id]
        conn.execute(f"UPDATE messages SET {cols}, updated_at=datetime('now') WHERE id=?", vals)
    else:
        cols = ", ".join(data.keys())
        placeholders = ", ".join("?" for _ in data)
        conn.execute(f"INSERT INTO messages ({cols}) VALUES ({placeholders})", list(data.values()))
    
    conn.commit()
    return msg_id


def get_message(msg_id: str) -> dict:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
    return dict(row) if row else None


def get_messages_by_status(status: str, limit=50) -> list:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM messages WHERE push_status=? ORDER BY date_sent DESC LIMIT ?",
        (status, limit)
    ).fetchall()
    return [dict(r) for r in rows]


def get_unpushed_messages(limit=50) -> list:
    return get_messages_by_status("pending", limit)


def mark_pushed(msg_id: str):
    conn = _get_conn()
    conn.execute(
        "UPDATE messages SET push_status='pushed', pushed_at=datetime('now'), updated_at=datetime('now') WHERE id=?",
        (msg_id,)
    )
    conn.commit()


def update_message_fields(msg_id: str, data: dict):
    if not data:
        return
    conn = _get_conn()
    cols = ", ".join(f"{k}=?" for k in data)
    vals = list(data.values()) + [msg_id]
    conn.execute(f"UPDATE messages SET {cols}, updated_at=datetime('now') WHERE id=?", vals)
    conn.commit()


# ── Thread CRUD ─────────────────────────────────────────────────

def upsert_thread(thread_id: str, data: dict):
    conn = _get_conn()
    existing = conn.execute("SELECT thread_id FROM threads WHERE thread_id=?", (thread_id,)).fetchone()
    if existing:
        cols = ", ".join(f"{k}=?" for k in data)
        vals = list(data.values()) + [thread_id]
        conn.execute(f"UPDATE threads SET {cols}, updated_at=datetime('now') WHERE thread_id=?", vals)
    else:
        data["thread_id"] = thread_id
        cols = ", ".join(data.keys())
        placeholders = ", ".join("?" for _ in data)
        conn.execute(f"INSERT INTO threads ({cols}) VALUES ({placeholders})", list(data.values()))
    conn.commit()


def get_thread(thread_id: str) -> dict:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM threads WHERE thread_id=?", (thread_id,)).fetchone()
    return dict(row) if row else None


# ── Attachment CRUD ─────────────────────────────────────────────

def add_attachment(data: dict):
    conn = _get_conn()
    cols = ", ".join(data.keys())
    placeholders = ", ".join("?" for _ in data)
    conn.execute(f"INSERT OR REPLACE INTO attachments ({cols}) VALUES ({placeholders})", list(data.values()))
    conn.commit()


def get_attachments(message_id: str) -> list:
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM attachments WHERE message_id=?", (message_id,)).fetchall()
    return [dict(r) for r in rows]


# ── Schedule CRUD ───────────────────────────────────────────────

def upsert_schedule(data: dict):
    conn = _get_conn()
    sched_id = data["id"]
    existing = conn.execute("SELECT id FROM schedules WHERE id=?", (sched_id,)).fetchone()
    if existing:
        cols = ", ".join(f"{k}=?" for k in data if k != "id")
        vals = [data[k] for k in data if k != "id"] + [sched_id]
        conn.execute(f"UPDATE schedules SET {cols}, updated_at=datetime('now') WHERE id=?", vals)
    else:
        cols = ", ".join(data.keys())
        placeholders = ", ".join("?" for _ in data)
        conn.execute(f"INSERT INTO schedules ({cols}) VALUES ({placeholders})", list(data.values()))
    conn.commit()
    return sched_id


def get_schedules(status: str = "active", limit: int = 50) -> list:
    conn = _get_conn()
    if status:
        rows = conn.execute(
            "SELECT * FROM schedules WHERE status=? ORDER BY COALESCE(deadline, updated_at) ASC LIMIT ?",
            (status, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM schedules ORDER BY COALESCE(deadline, updated_at) ASC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def update_schedule(schedule_id: str, data: dict):
    if not data:
        return
    conn = _get_conn()
    cols = ", ".join(f"{k}=?" for k in data)
    vals = list(data.values()) + [schedule_id]
    conn.execute(f"UPDATE schedules SET {cols}, updated_at=datetime('now') WHERE id=?", vals)
    conn.commit()


# ── Link CRUD ───────────────────────────────────────────────────

def add_link(data: dict):
    conn = _get_conn()
    cols = ", ".join(data.keys())
    placeholders = ", ".join("?" for _ in data)
    conn.execute(f"INSERT OR REPLACE INTO links ({cols}) VALUES ({placeholders})", list(data.values()))
    conn.commit()


def get_links(message_id: str) -> list:
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM links WHERE message_id=?", (message_id,)).fetchall()
    return [dict(r) for r in rows]


def get_pending_links(limit=10) -> list:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM links WHERE extract_status='pending' ORDER BY created_at ASC LIMIT ?",
        (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


# ── Action CRUD ─────────────────────────────────────────────────

def create_action(data: dict):
    conn = _get_conn()
    action_id = data.get("id") or f"act_{int(time.time())}_{os.urandom(4).hex()}"
    data["id"] = action_id
    cols = ", ".join(data.keys())
    placeholders = ", ".join("?" for _ in data)
    conn.execute(f"INSERT INTO actions ({cols}) VALUES ({placeholders})", list(data.values()))
    conn.commit()
    return action_id


def get_pending_actions(limit=10) -> list:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM actions WHERE action_status='pending' ORDER BY created_at ASC LIMIT ?",
        (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def update_action(action_id: str, data: dict):
    conn = _get_conn()
    cols = ", ".join(f"{k}=?" for k in data)
    vals = list(data.values()) + [action_id]
    conn.execute(f"UPDATE actions SET {cols}, updated_at=datetime('now') WHERE id=?", vals)
    conn.commit()


# ── Contact CRUD (SQLite version) ───────────────────────────────

def upsert_contact(email: str, data: dict):
    conn = _get_conn()
    data["email"] = email
    existing = conn.execute("SELECT email FROM contacts WHERE email=?", (email,)).fetchone()
    if existing:
        cols = ", ".join(f"{k}=?" for k in data if k != "email")
        vals = [data[k] for k in data if k != "email"] + [email]
        conn.execute(f"UPDATE contacts SET {cols}, updated_at=datetime('now') WHERE email=?", vals)
    else:
        data.setdefault("first_seen", datetime.now().isoformat())
        cols = ", ".join(data.keys())
        placeholders = ", ".join("?" for _ in data)
        conn.execute(f"INSERT INTO contacts ({cols}) VALUES ({placeholders})", list(data.values()))
    conn.commit()


def get_contact(email: str) -> dict:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM contacts WHERE email=?", (email,)).fetchone()
    return dict(row) if row else None


def get_contacts_by_trust(level: str = None) -> list:
    conn = _get_conn()
    if level:
        rows = conn.execute("SELECT * FROM contacts WHERE trust_level=? ORDER BY interaction_count DESC", (level,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM contacts ORDER BY interaction_count DESC").fetchall()
    return [dict(r) for r in rows]


def get_own_domains() -> list:
    """Get domains that belong to the user (from configured email accounts)."""
    conn = _get_conn()
    rows = conn.execute("SELECT DISTINCT from_domain FROM messages WHERE from_domain IS NOT NULL AND account IN (SELECT account FROM messages GROUP BY account)").fetchall()
    domains = set()
    for r in rows:
        d = r[0]
        if d and '.' in d:
            domains.add(d)
    # Also from contacts marked as own_domain
    rows = conn.execute("SELECT email FROM contacts WHERE own_domain=1").fetchall()
    for r in rows:
        email = r[0]
        if '@' in email:
            domains.add(email.split('@')[1])
    return sorted(domains)


# ── Seen tracking (still JSON for simplicity, but backed by DB) ─

SEEN_FILE = email_config.get_path("seen") if email_config else os.path.expanduser("~/.hermes/email_watch_seen.json")

def is_seen(msg_id: str, account: str) -> bool:
    seen = _load_seen()
    key = f"{account}:{msg_id}"
    return key in seen.get("entries", {})


def mark_seen(msg_id: str, account: str):
    seen = _load_seen()
    seen.setdefault("entries", {})[f"{account}:{msg_id}"] = {
        "first_seen_at": datetime.now().isoformat(),
        "message_id": msg_id,
    }
    with open(SEEN_FILE, "w") as f:
        json.dump(seen, f, indent=2)


def _load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE) as f:
            return json.load(f)
    return {"entries": {}}


# ── Init ────────────────────────────────────────────────────────

init_db()
