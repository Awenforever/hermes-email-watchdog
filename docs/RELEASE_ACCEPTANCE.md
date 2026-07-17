# Release acceptance

A final distributable conclusion requires all of the following:

1. A public GitHub repository, immutable tagged commit, release asset and
   published SHA256 checksum.
2. Fresh-container installation using only the real repository URL.
3. Natural conversational onboarding and non-interactive parity.
4. Complete unit, matrix and policy regression suites.
5. Stress, fault and state/concurrency recovery.
6. Source/user-data persistence across container rebuild.
7. Real isolated read-only mailbox onboarding and list-only access.
8. Real isolated spare-account Weixin delivery through the external Hermes
   adapter, with exactly one delivered notification and no mailbox mutation or
   outbound email.
9. Safe uninstall, explicit purge with zero residual, and reinstall after purge.
10. GitHub Actions passing from the exact published commit.

The accepted release evidence has completed items 2–9 for the reviewed
candidate lineage. Item 1 and the published-commit instance of item 10 are the
remaining guarded write operations.

## Transport ownership boundary

`weixin.py` and `hermes-wechat-enhance` are external to this repository.
Email Watchdog owns its durable business outbox and stable delivery IDs, but it
does not own or patch the Weixin transport source.

Queue bounding, transport-queue persistence, priority scheduling, or other
WeChat Enhance development is not an Email Watchdog release gate. The accepted
real Weixin E2E proves the integration required by this skill.

## Public repository publication gates

- Existing public history is preserved by normal fast-forward.
- GitHub private vulnerability reporting is enabled before push.
- GitHub Actions permissions remain explicitly read-only and action
  dependencies remain pinned to immutable commit SHAs.
- The live remote head must still equal the reviewed parent before publication.
- Credentials are supplied outside chat, command arguments, shell history,
  logs, and result archives.
- The exact reviewed candidate is pushed; force-push, branch deletion, and
  history rewriting are forbidden.
- GitHub Actions must pass before creating the tag or release.
- Release assets are created from the exact tagged commit with SHA256.
- A final fresh-container installation is run from the real tagged source.
