# Release acceptance

## 0.6.1 card identity and receipt-time acceptance

The `0.6.1` production candidate passed the following isolated acceptance on
2026-09-25 before deployment:

- Linux repository contract, checksum, compile, unit, policy, transport,
  concurrency, and provenance matrices: `CI_MATRIX_OK count=20`.
- Ten consecutive rounds using forty unique, previously unused historical
  mailbox messages: 40 processed, 0 errors, 37 published and 3 intentionally
  suppressed.
- The set covered account security and confirmation, verification codes,
  service suspension, overdue invoices and downloaded attachments, academic
  alerts and weekly reports, school notices, events, personal correspondence,
  calls for papers, and marketing suppression.
- Every published card had a typed mailbox title, canonical sender and subject,
  and at least one grounded message timestamp.  When the destination server's
  RFC `Received` timestamp was available it was shown separately from the
  sender's `Date`; scan or replay time was never presented as receipt time.
- A second cross-round audit found 40 unique message IDs, no presentation
  errors, no generic batch heading, forwarding/image chrome, invisible URL
  characters, raw local paths, or unverified state-changing mail UI links.
- Eleven messages exercised attachment handling.  The acceptance harness used
  read-only mailbox export and isolated database, cache, calendar, learning,
  attachment, and seen-state paths; it never enqueued or sent Weixin messages.

Evidence is retained on the production host under
`/opt/data/migration-staging/email-watchdog-v061-chrome/final2-r01` through
`final2-r10`.  These staging paths are diagnostic evidence, not runtime state.

Hermes Email Watchdog `0.1.0` is accepted as the first stable public release.

The accepted lineage has completed:

1. Public GitHub repository and preserved fast-forward history.
2. Immutable release-candidate and stable-release identities.
3. Published release assets and SHA256 checksums.
4. Fresh-container installation using the real repository/tag source.
5. Natural conversational onboarding and non-interactive parity.
6. Complete unit, matrix and policy regression suites.
7. Stress, fault and state/concurrency recovery.
8. Source/user-data persistence across container rebuild.
9. Real isolated read-only mailbox onboarding and list-only access.
10. Real isolated spare-account Weixin delivery through the external Hermes
    adapter, with exactly one delivered notification and no mailbox mutation or
    outbound email.
11. Safe uninstall, explicit purge with zero residual, and reinstall after
    purge.
12. GitHub Actions passing from the exact published commit.

## Transport ownership boundary

`weixin.py` and `hermes-wechat-enhance` are external to this repository. Email
Watchdog owns its durable business outbox and stable delivery IDs, but it does
not own or patch the Weixin transport source.

Queue bounding, transport-queue persistence, priority scheduling, or other
WeChat Enhance development is not an Email Watchdog release or production
deployment gate.

## Stable publication and deployment gates

- Existing public history is preserved by normal fast-forward.
- The live remote head must equal the accepted `0.1.0-rc.5` commit before the
  stable commit is published.
- GitHub Actions must pass before creating the stable tag/release.
- Release assets are generated from the exact tagged commit and published with
  SHA256 checksums.
- A final fresh-container installation is run from the real stable tag.
- Production deployment must begin with an exact frozen-source audit.
- Existing Email Watchdog user configuration, state, learning data and outbox
  must be preserved.
- Production deployment must create a rollback backup before replacing code.
- `weixin.py` and `hermes-wechat-enhance` must remain byte-identical.
