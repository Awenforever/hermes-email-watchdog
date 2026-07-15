---
name: hermes-email-watchdog
description: Read-only multi-account email monitoring, grounded classification and durable Hermes notifications.
version: 0.1.0-rc.1
tags: [email, watchdog, notification, read-only, hermes]
---

# Hermes Email Watchdog

The skill runs a gateway-startup scheduler that polls explicitly configured
mail accounts, classifies new messages and writes notifications to its durable
business outbox.

## Required guarantees

- Mailbox access is read-only.
- No email reply, send, archive, delete, move, flag or mark operation is
  exposed by this release candidate.
- Missing configuration leaves the scheduler disabled.
- Installation never edits Hermes core `weixin.py`.
- Default uninstall preserves user data.
- Purge requires an explicit confirmation flag and removes only
  Email Watchdog-owned data.

## Runtime ownership

Canonical skill source:

```text
/opt/data/skills/hermes-email-watchdog
```

Active hook:

```text
/opt/data/hooks/hermes-email-watchdog
```

User data:

```text
/opt/data/.hermes-home/.hermes/
```

The Weixin transport and `hermes-wechat-enhance` are separate components.
