---
name: hermes-email-watchdog
description: Install, configure, and run read-only multi-account email monitoring with grounded Hermes notifications.
version: 0.2.0-rc.1
tags: [email, watchdog, notification, read-only, hermes, onboarding]
---

# Hermes Email Watchdog

A repository-owned, read-only email monitor. The skill classifies new mail and
writes durable notifications to the configured Weixin conversation.

## Natural conversation onboarding

When the user asks to install, configure, set up, or enable Email Watchdog:

1. Run:

   ```bash
   bash /opt/data/skills/hermes-email-watchdog/setup.sh status --json
   ```

2. Use the current gateway conversation as the notification target. The setup
   engine reads `HERMES_SESSION_PLATFORM` and `HERMES_SESSION_CHAT_ID`; the
   `agent:start` Hook also stores a pending target for fallback.
3. Reuse an existing valid Himalaya configuration automatically when exactly
   one is found. Do not ask the user for paths already detected.
4. Ask only for unresolved user-level information. Never ask for a mailbox
   password, app password, token, or secret value in chat.
5. For a new account, use an external secret command such as `pass`,
   `secret-tool`, `security`, `op`, `bw`, `gopass`, or `printenv`. Never use
   `echo` or `printf` with a literal secret.
6. Build a dry plan with:

   ```bash
   bash /opt/data/skills/hermes-email-watchdog/setup.sh plan --input-json '<JSON>'
   ```

7. Apply through the same engine:

   ```bash
   bash /opt/data/skills/hermes-email-watchdog/setup.sh apply --input-json '<JSON>'
   ```

   `apply` writes atomically, validates with a one-envelope read-only Himalaya
   list request, and rolls back on failure. It remains disabled unless the JSON
   explicitly contains `"enable": true`.
8. Treat a clear request to “安装并启用” or “启用邮件监控” as explicit
   enablement. A plain install request installs and validates but stays disabled.
9. Report the redacted result only. Never expose chat IDs, mailbox addresses,
   configuration paths, or secret commands in the response.

The natural and non-interactive paths must call the same `plan` and `apply`
implementation and produce the same normalized configuration hash.

## Non-interactive commands

```text
setup.sh status --json
setup.sh plan --input-json <JSON|@file|path|->
setup.sh apply --input-json <JSON|@file|path|->
setup.sh validate --json
setup.sh enable --json
setup.sh disable --json
setup.sh capture-context --json
setup.sh export-redacted --json
```

No command opens a terminal questionnaire.

## Required guarantees

- Mailbox access is read-only.
- Validation uses only `envelope list --page-size 1`.
- No email reply, send, archive, delete, move, flag, or mark operation is
  exposed by this release candidate.
- Missing or invalid configuration leaves the scheduler disabled.
- Installation never edits Hermes core `weixin.py`.
- Default uninstall preserves configuration and onboarding state.
- Purge requires an explicit confirmation flag and removes only owned data.

## Runtime ownership

Canonical skill source:

```text
/opt/data/skills/hermes-email-watchdog
```

Active Hook:

```text
/opt/data/hooks/hermes-email-watchdog
```

User data:

```text
/opt/data/.hermes-home/.hermes/
```

The Weixin transport and `hermes-wechat-enhance` are separate components.
