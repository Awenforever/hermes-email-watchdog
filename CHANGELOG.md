# Changelog

## 0.1.0 — 2026-07-18

- Publish the first stable Email Watchdog release after the complete `rc.5`
  acceptance and public-release verification sequence.
- Keep runtime mail handling, renderer, onboarding, durable outbox, delivery,
  mailbox policy and Weixin integration byte-identical to `0.1.0-rc.5`.
- Record the stable production-deployment contract: preserve all owned user
  data, create a rollback backup, and leave `weixin.py` and
  `hermes-wechat-enhance` unchanged.
- Update stable lifecycle assertions and guarded-publication parent.

## 0.1.0-rc.5 — 2026-07-18

- Close the Email Watchdog release-acceptance record using the accepted public
  repository, real-URL lifecycle, isolated read-only mailbox, state recovery,
  and real spare-account Weixin E2E evidence.
- Remove WeChat Enhance queue bounding, persistence, and priority hardening as
  an Email Watchdog release gate; `weixin.py` remains externally owned.
- Refresh the guarded-publication parent for the next fast-forward.
- Update lifecycle version assertions for `0.1.0-rc.5`.
- Make no runtime mail, renderer, onboarding, outbox, delivery, mailbox, or
  Weixin source changes.

## 0.1.0-rc.4 — 2026-07-17

- Record the owner's decisions to preserve existing public history, use the MIT
  license, and use GitHub private vulnerability reporting.
- Add the guarded publication contract and confirm the exposed token was deleted.
- Restrict GitHub Actions to `contents: read`, disable persisted checkout
  credentials, and pin third-party actions to immutable commit SHAs.
- Keep publication blocked until the isolated hardening candidate passes and the
  live security setting is verified by guarded publication tooling.


## 0.1.0-rc.3 — 2026-07-17

- Preserve the existing public GitHub history through a two-parent integration
  commit without force-pushing or rewriting `main`.
- Make the accepted read-only candidate the current repository tree.
- Add the MIT license selected by the repository owner.
- Document intentional removal of legacy write-capable v3 modules.
- Keep publication blocked until a private security-reporting route is selected.

## 0.1.0-rc.2 — 2026-07-17

- Correct the lifecycle rollback assertion to verify restoration of `0.1.0-rc.2`.

- Serialize onboarding apply/enable/disable/context transactions with an
  OS-backed lock.
- Validate proposed mailbox configuration before publishing live config,
  enabled state, or onboarding state.
- Use unique durable staging files for generated Himalaya configuration.
- Serialize Hook context and business-outbox JSON read-modify-write operations
  across threads and processes.
- Replace fixed `.tmp` state paths with unique atomic staging files.
- Add concurrency, rollback-race, and SIGKILL regression coverage.

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
