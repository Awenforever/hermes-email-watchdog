# Ownership boundary

Owned by this repository:

- Canonical `hermes-email-watchdog` skill source
- Active `hermes-email-watchdog` hook
- Email Watchdog configuration, seen index, cache, status, learning database
  and business outbox

Not owned:

- Hermes core source
- `/opt/hermes/gateway/platforms/weixin.py`
- `hermes-wechat-enhance`
- Other skills, hooks, accounts or user files

Cross-project coordination facts, the append-only development log and the
residual list live in the private `hermes-collab-center` repository
(`projects/plugins/email-watchdog/PROJECT.md` Part I and `LOG.md` Part II).
As of 2026-10-26 that center is the sole owner of cross-agent project facts;
the product documents in this repository remain authoritative for plugin
behavior.

Default uninstall preserves user data. Purge may remove only the explicitly
listed Email Watchdog-owned paths.
