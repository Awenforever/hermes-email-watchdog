# Release acceptance

A final distributable conclusion requires all of the following:

1. GitHub repository and immutable release checksum.
2. Fresh-container installation using only the repository URL or confirmed
   Hermes transport.
3. Natural conversational onboarding and non-interactive parity.
4. Complete unit, matrix and policy regression suites.
5. Stress, queue saturation and fault recovery.
6. Source/user-data persistence across container rebuild.
7. Real isolated Weixin receive/send E2E.
8. Safe uninstall, explicit purge with zero residual, and reinstall.
9. GitHub Actions passing from a clean checkout.
10. Hermes Weixin context-token queue bounding, persistence and priority
    hardening.

Production closure alone does not satisfy this list.

## Public repository publication gates

- Existing public history is preserved by normal fast-forward.
- GitHub private vulnerability reporting is enabled before the first push.
- GitHub Actions permissions are explicitly read-only and action dependencies
  are pinned to immutable commit SHAs.
- The live remote head must still equal the reviewed parent before publication.
- The deleted exposed credential is never reused; a new repository-scoped
  credential is supplied outside chat and shell history.
