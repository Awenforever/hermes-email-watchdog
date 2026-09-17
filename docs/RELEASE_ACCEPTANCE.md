# Release acceptance

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
