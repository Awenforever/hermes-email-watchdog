# Changelog

## 0.5.0 - 2026-09-23

- Replace the generic summary/body template with an intent-aware Markdown
  composer. Sender and subject use inline code; invoices, account actions,
  security alerts, events, deadlines, digests, and personal mail receive
  purpose-specific layouts instead of universal "要点 / 原文摘录" blocks.
- Add an editorial quality gate so forwarding separators, mail headers,
  greetings, signatures, unsubscribe text, image placeholders, and negative
  inventory can never become highlights.
- Keep a validated semantic decision in control when attachment, calendar,
  persistence, or rendering side effects fail. Optional phase failures are
  recorded independently and never silently fall back to the mechanical legacy
  formatter.
- Move the portable attachment/calendar defaults under plugin-owned state,
  eliminating home-directory ambiguity and preserving reminders across
  Windows, WSL, Linux, and NAS-Docker installations.
- Add exact regression coverage for the production overdue-invoice failure:
  invoice number, amount, due date, payment method, action link, and PDF are
  retained while forwarding chrome and raw-body walls are absent.

## 0.4.0 - 2026-09-23

- Read mail through non-mutating raw MIME export, preserving real HTML links,
  attachment names, media types, and sizes without setting the Seen flag.
- Render compact Markdown cards with clean sections, actionable links, useful
  attachment names, and aggressive removal of image placeholders, template
  chrome, footer noise, broken soft wraps, and redundant original-body dumps.
- Automatically download and forward safe bounded attachments through the
  existing per-part acknowledged Weixin outbox.
- Persist actionable deadlines, maintain a local iCalendar view, and deliver
  restart-safe reminders 24 hours and 1 hour before the event or deadline.
- Repair Beijing clock times incorrectly labeled as UTC by model output and
  reject incomplete action fragments in favor of clear grounded instructions.
- Feed extracted links and real attachment metadata into risk assessment and
  persistent audit tables.

## 0.3.1 - 2026-09-23

- Replace closed-whitelist rejection of descriptive model fields with tolerant
  semantic-key recovery and grounding-first normalization.
- Ignore harmless future fields while retaining hard rejection for forbidden
  side effects, ungrounded facts, malformed roots, and unsafe attachment actions.
- Treat grounded account/service suspension and deactivation notices as
  actionable even when the model format is unusable, and never suppress the
  `account_status_notice` category under actionable notification policy.
- Invalidate old semantic cache entries with the tolerant-core prompt version.

## 0.3.0 - 2026-09-23

- Make USTC `deepseek-flash` the owner of every production semantic decision;
  retain `qwen3.6-chat` as bounded transport and schema-validation fallback.
- Add context-aware attachment policy, safe automatic download, per-message
  storage, and real Weixin image/document forwarding through the reliable outbox.
- Keep marketing and low-value bulk mail silent while always surfacing grounded
  verification codes, account-security notices, invoices, and useful attachments.
- Repair common model deadline/action shapes without weakening grounding, and
  expose accurate model-fallback attribution.
- Add config v3 migration and customizable model, notification, attachment,
  forwarding, size, and timezone choices for independent installations.
- Apply the v3 policy migration only to the exact v0.2.x release-default
  signature, preserve custom/identity fields, and restore the prior config on
  plugin rollback.
- Preserve the existing config file owner and mode across root-run atomic
  upgrades and rollbacks, so the unprivileged Gateway can always read it.

## 0.2.3 — 2026-09-21

- Make the portable CI contract independent of profile paths baked into a
  production image, so clean-container installation checks exercise the
  repository defaults deterministically.
- Synchronize the release version recorded by `VERSION` and `plugin.yaml`.
- Use USTC `deepseek-flash` for semantic analysis and retry once with
  `qwen3.6-chat` when the primary model is unavailable or returns unusable JSON.

## 0.2.0 — 2026-09-20

- Use the Hermes USTC OpenAI-compatible provider with `qwen3.6-chat` for the
  production semantic path; retain user-selected custom providers unchanged.
- Promote the compact mobile notification renderer to `adaptive_v1f` and keep
  explicit action clauses even when they overlap with the summary.
- Unify runtime state under the active Hermes profile at
  `plugin-data/hermes-email-watchdog/`; migrate the known legacy layout only
  when the canonical destination is absent.
- Make the profile enable marker authoritative over stale image environment
  defaults, and clear stale disabled/error fields when the scheduler starts.
- Repair rollback metadata and cross-platform line endings; validate install,
  upgrade, rollback, uninstall, purge, and reinstall in an isolated Linux
  container.

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
