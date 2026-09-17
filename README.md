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

Hermes Email Watchdog `0.1.0` is the first stable public release. The exact
runtime implementation is unchanged from the accepted `0.1.0-rc.5` candidate.

The release lineage has passed guarded fast-forward publication, GitHub
Actions, real-tag fresh-container lifecycle acceptance, isolated read-only
mailbox onboarding, state/concurrency recovery, and one isolated spare-account
real Weixin delivery E2E.

`weixin.py` and `hermes-wechat-enhance` remain external transport ownership.
They are not modified by this release and are not production-deployment
prerequisites for Email Watchdog.

The stable package may be installed or upgraded with the repository lifecycle
scripts. Installation preserves owned configuration, onboarding state, seen
state, learning data and durable notification outbox.

See `INSTALLATION.md`, `SECURITY.md`, `docs/ONBOARDING.md`,
`docs/OWNERSHIP.md`, and `docs/RELEASE_ACCEPTANCE.md`.

## Public repository migration

This release preserves the earlier public v3 history while replacing its current
write-capable tree with the accepted read-only candidate. See
[`docs/REMOTE_HISTORY_MIGRATION.md`](docs/REMOTE_HISTORY_MIGRATION.md).
