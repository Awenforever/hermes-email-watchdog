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

Default uninstall preserves user data. Purge may remove only the explicitly
listed Email Watchdog-owned paths.
