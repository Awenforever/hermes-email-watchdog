# Hermes Email Watchdog

A read-only multi-account email monitor for Hermes Agent. It classifies new
mail, generates grounded summaries, and sends durable notifications through the
Hermes messaging adapter.

## Frozen functional baseline

- Semantic protocol: `readable_grounded_core_v1u`
- Renderer: `adaptive_v1e`
- Scheduler/outbox: `EMAIL_WATCHDOG_OUTBOX_NONBLOCKING_BACKOFF_V1`
- Mailbox policy: read-only
- Outbound email, reply, archive, delete, move and mark operations: disabled and
  excluded from this release candidate
- Weixin transport ownership: external to this repository

## Safety boundary

The skill may read envelopes, message content and attachments configured by the
user. It may write only its own local configuration, cache, seen index,
learning database, status and notification outbox. It must not modify mailbox
state or send email.

`weixin.py` and `hermes-wechat-enhance` are not owned by this repository.

## Installation status

This is an isolated repository candidate, not a final release. The lifecycle
scripts are included for fresh-container acceptance, but GitHub transport,
stress, persistence rebuild, real isolated Weixin E2E and purge-zero-residual
acceptance remain pending.

See `INSTALLATION.md`, `SECURITY.md`, `docs/OWNERSHIP.md` and
`docs/RELEASE_ACCEPTANCE.md`.
