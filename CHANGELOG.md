# Changelog

## 0.2.0-rc.1 — 2026-07-17

- Add repository-owned JSON onboarding engine and non-interactive wrapper.
- Add natural Hermes conversation protocol with current-session target capture.
- Add `agent:start` pending-target Hook without storing message bodies.
- Add atomic apply, read-only validation, rollback, explicit enable/disable, and
  redacted status/export.
- Add IMAP-only Himalaya generation with external secret-command enforcement.
- Make runtime and template safety/delivery defaults identical and remove legacy
  mailbox-write configuration paths.
- Extend uninstall/purge/persistence contracts for onboarding-owned data.

## 0.1.0-rc.1 — 2026-07-15

- Freeze `readable_grounded_core_v1u`.
- Freeze `adaptive_v1e`.
- Add nonblocking durable outbox with bounded exponential backoff.
- Remove mailbox-write and outbound-email modules from the distributable
  candidate.
- Add isolated install, verify, upgrade, rollback, uninstall, purge and
  diagnosis lifecycle.
