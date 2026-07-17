# Hermes Email Watchdog

A read-only multi-account email monitor for Hermes Agent. It classifies new
mail, generates grounded summaries, and sends durable notifications through the
Hermes Weixin adapter.

## Frozen functional baseline

- Semantic protocol: `readable_grounded_core_v1u`
- Renderer: `adaptive_v1e`
- Scheduler/outbox: `EMAIL_WATCHDOG_OUTBOX_NONBLOCKING_BACKOFF_V1`
- Repository onboarding: `EMAIL_WATCHDOG_ONBOARDING_V1`
- Mailbox policy: read-only
- Outbound email and mailbox mutation modules: excluded
- Weixin transport ownership: external to this repository

## Setup

After repository installation, configure through natural Hermes conversation or
through the same non-interactive engine:

```bash
bash /opt/data/skills/hermes-email-watchdog/setup.sh status --json
```

The setup engine can reuse an existing Himalaya configuration, capture the
current Weixin conversation, generate an IMAP-only Himalaya configuration using
an external secret command, validate read-only access, roll back failures, and
explicitly enable or disable the scheduler.

It never asks for or stores a mailbox password.

## Safety boundary

The skill may read explicitly configured mail accounts. It may write only its
own configuration, onboarding state, cache, seen index, learning database,
status, and notification outbox. It must not modify mailbox state or send email.

`weixin.py` and `hermes-wechat-enhance` are not owned by this repository.

## Release status

This remains an isolated repository candidate. GitHub Actions on a real remote,
stress and recovery, real isolated mailbox/Weixin E2E, software licensing, and
Weixin context-token queue hardening remain pending.

See `INSTALLATION.md`, `SECURITY.md`, `docs/ONBOARDING.md`,
`docs/OWNERSHIP.md`, and `docs/RELEASE_ACCEPTANCE.md`.
