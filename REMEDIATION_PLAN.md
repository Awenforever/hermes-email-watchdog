# Hermes Email Watchdog Remediation Plan

Audit date: 2026-07-09  
Scope read in full: existing `REMEDIATION_PLAN.md` and all 15 Python files under `scripts/`.

This rewrite replaces the previous mechanical plan. The corrected target is an intelligent delivery pipeline where rules cheaply filter noise and simple cases, while LLM semantic analysis drives delivery decisions, reminders, attachment handling, and the user's ability to understand the mail from chat alone.

## Target Architecture

```text
Email arrives
  -> email_watch.fetch/read
  -> email_watch.classify as rule pre-processor
       - skip ads/spam/unsafe auto mail -> mark seen, no push
       - high-confidence simple case, e.g. verification code -> deliver without LLM
       - everything else -> call email_llm.analyze_email()
  -> email_llm semantic analysis
       - meaning to user
       - action needed
       - urgency and deadline
       - delivery format decision
       - attachment policy
       - reminder schedule
  -> email_delivery.deliver_email()
       - format code_extraction | summary | full_body
       - separate header/body/signature when full body is shown
       - preserve structured blocks as code fences
       - download allowed attachments and report saved paths
       - create/update schedule entries
       - install cron reminder entries or emit entries for a managed cron file
       - persist delivery metadata and mark pushed
  -> printed notification text for the chat gateway
```

The core design rule is that the LLM does not merely enrich a pushed message. It decides what the user needs to see and what side effects should happen. The rule classifier remains valuable, but only as a fast gate.

## Issue 1: Replace `email_push.py` With Intelligent Delivery

### Before

- [scripts/email_push.py](/tmp/hermes-email-watchdog-new/scripts/email_push.py:1) is a WeChat formatter: chunking, summaries, command footers, and static `show_full_body`.
- [scripts/email_watch.py](/tmp/hermes-email-watchdog-new/scripts/email_watch.py:24) imports `email_push`, but the active runtime builds alert strings inline at lines 681-713.
- [scripts/email_watch.py](/tmp/hermes-email-watchdog-new/scripts/email_watch.py:637) calls `classify()` and immediately formats/downloads/pushes from the returned action.
- [scripts/email_watch.py](/tmp/hermes-email-watchdog-new/scripts/email_watch.py:653) skips messages, then lines 656-679 download attachments directly, and lines 681-713 format fixed previews.
- [scripts/email_watch.py](/tmp/hermes-email-watchdog-new/scripts/email_watch.py:728) uses `pushed_count`/`MAX_PER_TICK`; [scripts/email_watch.py](/tmp/hermes-email-watchdog-new/scripts/email_watch.py:737) references `accounts_seen` without defining it.
- [scripts/email_store.py](/tmp/hermes-email-watchdog-new/scripts/email_store.py:28) has no durable `deadline`, `format_decision`, or delivery-analysis JSON fields.
- [scripts/email_calendar.py](/tmp/hermes-email-watchdog-new/scripts/email_calendar.py:248) separately rescans mail with regexes for dates; [scripts/email_followup.py](/tmp/hermes-email-watchdog-new/scripts/email_followup.py:80) reads calendar JSON for reminders.

### After

Delete [scripts/email_push.py](/tmp/hermes-email-watchdog-new/scripts/email_push.py:1). Add a new `scripts/email_delivery.py` with these public functions:

```python
def deliver_email(email: dict, rule_result: dict, analysis: dict, account: dict) -> dict:
    """Apply LLM/rule delivery decision and side effects. Returns notification payload."""

def format_notification(email: dict, analysis: dict, attachments: list, schedule: list) -> str:
    """Return chat-ready text, not channel-specific chunks."""

def download_attachments(email: dict, analysis: dict, account: dict) -> list:
    """Download allowed attachments and persist paths in email_store.attachments."""

def upsert_schedule(email: dict, analysis: dict) -> list:
    """Persist deadlines/reminders in schedule store and email_store actions."""

def install_reminder_cron(schedule_items: list) -> list:
    """Create/update managed reminder cron entries or return planned entries if disabled."""
```

`deliver_email()` consumes the LLM analysis result, not just category/action strings. It must support:

- `format_decision="code_extraction"`: show platform/service name, code, expiry, sender, and subject only.
- `format_decision="summary"`: show concise summary, action, deadline, amount/payment details if relevant, and attachment paths.
- `format_decision="full_body"`: show summary, required action, deadline, then readable full body with logical sections.
- `attachment_handling.policy`: `download_all`, `download_safe`, `download_invoices_only`, `list_only`, or `none`.
- `reminder_schedule`: schedule data that becomes queryable and reminder-capable.

### Delivery formatting rules

Full-body rendering should be implemented in `email_delivery.py`, not in `email_watch.py`.

```text
[USTC] high | 学校通知
From: Graduate School <grad@ustc.edu.cn>
Subject: 关于提交中期检查材料的通知

Summary:
需要在 2026-07-15 17:00 前提交中期检查材料。

Action:
准备表格、导师签字后提交到系统。

Deadline:
2026-07-15T17:00:00+08:00

Body:
<clean paragraphs with sensible line breaks>

Structured content:
    Materials/checklist block:
材料清单:
1. ...
2. ...

Signature:
研究生院

Attachments:
- /home/.../EmailAttachments/2026-07/report.docx

Reminders:
- 2026-07-12T09:00:00+08:00 progress_check
- 2026-07-15T09:00:00+08:00 due_today
```

### Exact file changes

| File | Lines | Before | After |
|---|---:|---|---|
| `scripts/email_push.py` | 1-167 | WeChat push formatter, splitter, batch summary, command footer. | Delete. Replaced by `scripts/email_delivery.py`. |
| `scripts/email_watch.py` | 17-27 | Imports `email_push` in v3 bundle. | Import `email_delivery` and `email_llm`; `HAS_V3` should cover store/trust/risk only, while LLM/delivery have separate availability handling. |
| `scripts/email_watch.py` | 39-64 | Hardcoded paths/accounts plus `MAX_PER_TICK`. | Use `email_config`; remove `MAX_PER_TICK` and per-tick delivery throttling from this script. |
| `scripts/email_watch.py` | 236-248 | `classify()` returns action strings like `push_full`, `download_invoice`, `extract_code`. | Keep return compatibility initially, but document it as `RuleResult`: `skip`, `simple_code`, `high_confidence_simple`, or `needs_llm`. |
| `scripts/email_watch.py` | 290-307 | Verification code regex returns `extract_code`. | Treat as high-confidence simple case. Build a minimal analysis object locally and call `email_delivery.deliver_email()`, skipping LLM. |
| `scripts/email_watch.py` | 371-483 | Rule categories imply full body/download behavior. | Rules only set rough category, priority, and skip/simple hints. LLM decides full body vs summary and attachment policy for non-simple mail. |
| `scripts/email_watch.py` | 557-720 | `check_account()` formats alerts and downloads attachments inline. | Build normalized email dict, store/cache it, call `classify()`, optionally call `email_llm.analyze_email()`, then call `email_delivery.deliver_email()`. Append returned `notification_text`. |
| `scripts/email_watch.py` | 628-634 | Stores message but not `rule_category`, `final_category`, `summary`, `cache_path`, or delivery decision. | Include rule category after classification; after delivery, persist `llm_category`, `final_category`, `importance`, `deadline`, `summary_*`, `action_summary`, `format_decision`, `analysis_json`, and `push_status='pushed'`. |
| `scripts/email_watch.py` | 681-713 | Inline preview truncation and date hints. | Delete. Formatting belongs to `email_delivery.format_notification()`. |
| `scripts/email_watch.py` | 722-750 | `main()` uses `pushed_count`, undefined `accounts_seen`, and global header. | Iterate enabled accounts from config, collect notification texts, join with `\n\n---\n\n`; initialize `accounts_seen` only if a header remains. |
| `scripts/email_actions.py` | 15-20 | Imports `email_push`. | Remove. Import `email_delivery` only if helper functions are reused. |
| `scripts/email_actions.py` | 95-139, 321-324 | Scholar batching mutates `push_status='summarized'`. | Remove batching from delivery path. Low-value mail is skipped or summarized by semantic analysis, not channel batching. |
| `scripts/email_calendar.py` | 248-318 | Rescans inbox to infer events by regex. | Keep as fallback only or replace with reading `email_delivery` schedule store. New deadlines should be created during delivery from LLM output. |
| `scripts/email_followup.py` | 80-115 | Reads old calendar JSON and emits fixed 24/48h alerts. | Read the new schedule store and `reminders_sent`; support custom reminder entries from LLM. |
| `scripts/email_commands.py` | 141-166 | `待处理` expects `deadline` column that does not exist. | Add DB fields and query schedule store; add a schedule query command if approved. |
| `scripts/email_store.py` | 28-68 | Message schema lacks delivery/analysis fields. | Add migration-safe columns: `deadline`, `deadline_timezone`, `format_decision`, `semantic_category`, `analysis_json`, `attachment_policy`, `delivered_text_hash`. Add `schedules` table. |

### New schedule storage

Use SQLite as the primary data store instead of adding another JSON file:

```sql
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
```

`email_config` should still define a `schedule_file` or `cron_dir` only if the final implementation uses a managed cron file. Otherwise, `email_followup.py` can remain the single cron entry that wakes up and checks due reminder rows.

## Issue 2: Extract Config

### Required runtime path

The runtime file is:

```text
~/.hermes/email_watchdog_config.json
```

Add a committed, gitignored user template path:

```text
references/email_watchdog_config.template.json
```

Also add `.gitignore` entries for accidental local copies:

```text
email_watchdog_config.json
*.local.json
```

### New loader

Add `scripts/email_config.py`:

```python
CONFIG_PATH = "~/.hermes/email_watchdog_config.json"

def load_config() -> dict: ...
def get_accounts(enabled_only=True) -> list[dict]: ...
def get_account_map() -> dict[str, dict]: ...
def get_default_account() -> dict: ...
def get_path(name: str) -> str: ...
def get_watchdog_settings() -> dict: ...
def get_llm_settings() -> dict: ...
def get_delivery_settings() -> dict: ...
def get_account_emails() -> list[str]: ...
```

Missing config must fall back to current literals so existing cron users do not break. Emit at most one stderr warning per process.

### Proposed schema

```json
{
  "version": 1,
  "default_account": "ustc",
  "accounts": [
    {
      "id": "ustc",
      "label": "USTC",
      "type": "himalaya",
      "email": "user@mail.ustc.edu.cn",
      "display_name": "User Name",
      "himalaya_config": "~/.config/himalaya/config_ustc.toml",
      "enabled": true
    },
    {
      "id": "gmail",
      "label": "Gmail",
      "type": "himalaya",
      "email": "user@gmail.com",
      "display_name": "User Name",
      "himalaya_config": "~/.config/himalaya/config_gmail.toml",
      "enabled": true
    },
    {
      "id": "agently",
      "label": "Agently",
      "type": "agently",
      "email": "user@agent.qq.com",
      "display_name": "User Name",
      "enabled": true
    }
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
    "invoice_dir": "~/Documents/Invoices"
  },
  "watchdog": {
    "lookback": 5,
    "sleep_start": 0,
    "sleep_end": 6,
    "max_cached": 200
  },
  "llm": {
    "enabled": true,
    "max_body_chars": 12000,
    "timeout_seconds": 90,
    "temperature": 0.1,
    "mode": "hermes_aux"
  },
  "delivery": {
    "auto_download_attachments": true,
    "create_reminders": true,
    "managed_cron": false,
    "timezone": "Asia/Shanghai"
  }
}
```

### Exact file changes

| File | Lines | Before | After |
|---|---:|---|---|
| `scripts/email_watch.py` | 41-58, 63-64 | Hardcoded paths/accounts/settings. | Replace with `email_config` lookups. Convert account field reads from `name/config` to normalized `label/himalaya_config`, or normalize in loader for compatibility. |
| `scripts/email_store.py` | 11, 366 | Hardcoded DB and seen path. | Use `email_config.get_path("db")` and `get_path("seen")`. |
| `scripts/email_llm.py` | 21, 26 | Hardcoded batch size and cache path. | Use `email_config.get_llm_settings()` and `get_path("cache_dir")`. |
| `scripts/email_delivery.py` | new | None. | Use `get_path("attachment_dir")`, `get_path("invoice_dir")`, `get_delivery_settings()`. |
| `scripts/email_reply.py` | 13-37, 397 | Hardcoded JSON paths and accounts. | Use loader paths and account map. |
| `scripts/email_batch.py` | 13-23 | Hardcoded JSON paths/accounts. | Use loader paths and account map. |
| `scripts/email_pending_processor.py` | 14-35 | Hardcoded pending/thread paths/accounts. | Use loader paths and account map. |
| `scripts/email_calendar.py` | 20-29 | Hardcoded calendar/seen paths/accounts. | Use loader; later read SQLite schedule instead of rescanning. |
| `scripts/email_followup.py` | 13-16 | Hardcoded thread/calendar/seen/pending paths. | Use loader; read schedule rows from store. |
| `scripts/email_contacts.py` | 8-9 | Hardcoded contacts/settings. | Use loader. |
| `scripts/email_commands.py` | 28 | Hardcoded cache dir. | Use loader; add schedule query support. |
| `scripts/email_actions.py` | 22, 146, 188-194, 288 | Hardcoded cache/drafts/pending/sender config. | Use loader; choose sender from account map. |
| `scripts/email_trust.py` | 232-239 | Hardcoded own account emails. | Use `email_config.get_account_emails()`. |
| `scripts/test_email_matrix.py` | 49-74, 86-89 | Patches module globals individually. | Build a temp config and patch `email_config.CONFIG_PATH` or inject `EMAIL_WATCHDOG_CONFIG` env var if supported. |
| `SKILL.md` | module/config sections | Current docs reference old module count and fixed paths. | Document runtime config and template. |

## Issue 3: Rewrite `email_llm.py` as Core Intelligence

### Before

- [scripts/email_llm.py](/tmp/hermes-email-watchdog-new/scripts/email_llm.py:3) says it uses `deepseek-v4-flash`.
- [scripts/email_llm.py](/tmp/hermes-email-watchdog-new/scripts/email_llm.py:33) treats LLM as optional post-push processing.
- [scripts/email_llm.py](/tmp/hermes-email-watchdog-new/scripts/email_llm.py:63) builds a category prompt, not a delivery-decision prompt.
- [scripts/email_llm.py](/tmp/hermes-email-watchdog-new/scripts/email_llm.py:101) hardcodes model, API endpoint, temp file, and auth file.
- [scripts/email_llm.py](/tmp/hermes-email-watchdog-new/scripts/email_llm.py:160) processes only `push_status='pushed'`, meaning it runs after delivery rather than before.
- [scripts/email_llm.py](/tmp/hermes-email-watchdog-new/scripts/email_llm.py:193) maps batch results by list position.

### After

Replace the module around a synchronous API:

```python
def should_use_llm(rule_result: dict, email: dict) -> bool:
    """False only for skip and high-confidence simple cases."""

def analyze_email(email: dict, rule_result: dict) -> dict:
    """Return structured semantic analysis for delivery."""

def call_hermes_aux(prompt: str, settings: dict) -> dict:
    """Use Hermes auxiliary model routing, no hardcoded provider/model/auth."""

def validate_analysis(raw: dict, email: dict, rule_result: dict) -> dict:
    """Normalize missing fields and enforce enum values."""
```

`process_pending()` can remain for backfill/migration, but the main path must call `analyze_email()` before delivery from [scripts/email_watch.py](/tmp/hermes-email-watchdog-new/scripts/email_watch.py:637).

### Structured return contract

```json
{
  "id": "message-id",
  "semantic_category": "school_notice | verification_code | invoice_receipt | payment | paper_feedback | meeting_event | personal_task | newsletter | automated_notice | other",
  "user_relevance": "ignore | low | medium | high | urgent",
  "confidence": 0.0,
  "should_notify": true,
  "should_show_full_body": true,
  "format_decision": "full_body | summary | code_extraction",
  "formatted_summary": "One to three sentences with the information the user needs.",
  "action_needed": {
    "required": true,
    "description": "What the user should do next.",
    "type": "reply | read | submit_form | pay | attend | revise_document | download | monitor | none",
    "next_step": "Concrete next step, if any."
  },
  "deadline": {
    "has_deadline": true,
    "datetime": "2026-07-15T17:00:00+08:00",
    "date_text": "7月15日17:00前",
    "timezone": "Asia/Shanghai",
    "confidence": 0.9
  },
  "reminder_schedule": [
    {
      "time": "2026-07-12T09:00:00+08:00",
      "kind": "progress_check",
      "message": "检查中期材料准备进度"
    }
  ],
  "attachment_handling": {
    "policy": "download_safe",
    "wanted_types": ["forms", "feedback", "invoice"],
    "reason": "Attachments contain required materials."
  },
  "body_rendering": {
    "header_lines": ["Optional important metadata extracted from body"],
    "body_sections": [
      {"title": "Main Notice", "content": "...", "format": "paragraph"},
      {"title": "Checklist", "content": "1. ...\n2. ...", "format": "code"}
    ],
    "signature": "..."
  },
  "risk_notes": [],
  "llm_notes": "Short internal note"
}
```

### Prompt template

Use this prompt in `build_prompt(email, rule_result, now, user_context)`:

```text
You are Hermes Email Watchdog's semantic delivery planner.

Your job is not to classify into a fixed taxonomy. Your job is to understand what this email means to the user and decide what the chat notification must contain so the user does not need to open email again.

Current time: {now_iso}
User timezone: {timezone}
Rule pre-classifier result:
{rule_result_json}

Email:
{email_json}

Principles:
1. Preserve enough information for action. If the email is a school notice, paper/editor feedback, task request, meeting logistics, form request, or anything requiring decisions, use full_body and structure it for reading.
2. Do not show full body when it adds no value. Invoices, receipts, payment confirmations, shipment notices, and routine account notices usually need a summary plus key fields and saved attachments.
3. Verification/security codes should use code_extraction: service/platform, code, expiry if present, sender, subject.
4. Generalize from meaning, not from category names. The rule category is only a hint and may be wrong.
5. Extract deadlines and action items precisely. Resolve relative dates using current time and timezone. If uncertain, keep the original date text and lower confidence.
6. Design reminders only when they help progress: e.g. deadline minus 7d/3d/1d, same-day reminder, or custom progress checkpoints for revisions and multi-step tasks. Do not create reminders for passive FYI mail.
7. Attachment handling should be explicit. Choose download_safe/download_all/download_invoices_only/list_only/none and explain why.
8. If sender/content appears risky, do not recommend opening links or downloading suspicious attachments.
9. Output must be valid JSON matching the schema. No markdown outside JSON.

Return exactly this JSON object:
{
  "id": string,
  "semantic_category": string,
  "user_relevance": "ignore" | "low" | "medium" | "high" | "urgent",
  "confidence": number,
  "should_notify": boolean,
  "should_show_full_body": boolean,
  "format_decision": "full_body" | "summary" | "code_extraction",
  "formatted_summary": string,
  "action_needed": {
    "required": boolean,
    "description": string | null,
    "type": "reply" | "read" | "submit_form" | "pay" | "attend" | "revise_document" | "download" | "monitor" | "none",
    "next_step": string | null
  },
  "deadline": {
    "has_deadline": boolean,
    "datetime": string | null,
    "date_text": string | null,
    "timezone": string,
    "confidence": number
  },
  "reminder_schedule": [
    {"time": string, "kind": string, "message": string}
  ],
  "attachment_handling": {
    "policy": "download_all" | "download_safe" | "download_invoices_only" | "list_only" | "none",
    "wanted_types": [string],
    "reason": string
  },
  "body_rendering": {
    "header_lines": [string],
    "body_sections": [
      {"title": string, "content": string, "format": "paragraph" | "code"}
    ],
    "signature": string | null
  },
  "risk_notes": [string],
  "llm_notes": string
}
```

### Hermes aux model call

The implementation must use Hermes auxiliary routing rather than hardcoded provider calls. Recommended adapter:

- Read `llm.mode`, `llm.timeout_seconds`, and optional CLI path from config.
- Call the Hermes CLI with `subprocess.run([...], input=prompt, text=True, capture_output=True, timeout=...)`.
- Do not use `shell=True`.
- Do not name a provider or model unless config explicitly supplies an override.
- Parse strict JSON; if parsing fails, use a conservative fallback analysis: `summary`, no attachment download beyond safe rule policy, no reminder unless a rule-extracted deadline exists.

Exact CLI syntax is an implementation approval item because it depends on the installed Hermes CLI. The plan should not reintroduce `https://api.llm.ustc.edu.cn` or `/home/augenstern/.hermes/auth.json`.

## Pipeline Migration

1. Add `scripts/email_config.py` and template config. Convert path/account reads first with fallback defaults.
2. Add DB migrations in `email_store.py` for delivery-analysis columns and `schedules`.
3. Add `scripts/email_delivery.py` with side effects and formatting. Move attachment download logic out of `email_watch.py`.
4. Rewrite `scripts/email_llm.py` around `analyze_email()` and Hermes aux routing.
5. Change `email_watch.check_account()` so the active path is: fetch -> cache/store -> classify -> skip/simple/LLM -> deliver -> mark seen.
6. Keep `email_calendar.py` and `email_followup.py` working during migration, but shift new schedule creation to delivery and make reminder cron read the schedule store.
7. Delete `scripts/email_push.py` and remove imports/docs/tests that mention it.
8. Update `scripts/test_email_matrix.py` with unit tests for:
   - verification code bypasses LLM and uses `code_extraction`
   - invoice uses summary and downloads invoice attachments
   - school notice uses full body and schedule creation
   - paper revision feedback uses full body and progress reminders
   - LLM failure produces conservative summary fallback
   - unsafe sender blocks attachment download

## Key Decisions Needing Approval

1. Hermes CLI invocation form for aux model routing: exact command and whether JSON mode is available.
2. Reminder implementation: managed cron entries per schedule item, or keep one cron that polls SQLite schedules.
3. Whether `email_calendar.py` should be deprecated after delivery creates schedules, or retained as a fallback scanner.
4. Attachment policy default for unknown senders: recommended `list_only` unless trust/risk says safe.
5. Whether to expose a new chat command for schedule queries, e.g. `日程`, `deadline`, `schedule`.
6. Whether the config template should be committed as `references/email_watchdog_config.template.json` only, or also documented inline in `SKILL.md`.
