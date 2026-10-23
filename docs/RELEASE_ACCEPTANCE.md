# Release acceptance

## 0.14.1 Windows-native compatibility acceptance

The `0.14.1` production candidate completed four-platform acceptance on
2026-10-24 before publication. The release delta over `0.14.0` is
Windows-compatibility code plus tests and version artifacts; Linux/Docker
runtime behaviour is unchanged, so this is a cross-platform-parity release
rather than a behaviour change on the production platform.

Accepted source: public repository commit `1535e94`
("fix(email): Windows-native timezone/path compatibility + v0.14.1 release",
parent `2b7db42` = `0.14.0`), tree
`09e0ea2d8405a80c74a048cc5d9c53bb4f58d95f`. `VERSION` = `0.14.1`,
`PACKAGE_VERSION` = `EMAIL_WATCHDOG_STABLE_RELEASE_V0P14P1`, `plugin.yaml`
version `0.14.1`, published checksums cover 126 files.

The candidate fixed two genuine cross-platform defects found during `0.14.0`
Windows-native acceptance:

- Zoneinfo on Windows. Four modules (`email_assistant_composer.py`,
  `email_delivery.py`, `email_editorial_review.py`,
  `email_notification_renderer.py`) resolved `Asia/Shanghai` through the bare
  stdlib `zoneinfo`, which has no system tz database on Windows and crashed or
  silently fell back to raw RFC-2822 dates. Each now uses the fixed `+08:00`
  offset fallback pattern already proven in `email_original.py`, so date
  rendering is correct with or without a tz database.
- State-path separator. `email_config.py` joined state paths with a hardcoded
  forward slash, yielding a mixed-separator path on Windows. It now uses
  `os.path.join`.

Verification across the four supported targets:

- Docker/NAS (production platform): fresh throwaway container from production
  image `hermes-agent:v0.21.3-weekly-20260918` on host `ugreen-nas`
  (`DXP4800PLUS-RIN`, `vive@100.105.76.45`), `--rm --network none --tmpfs
  /opt/data`, source mounted read-only, `python3 tests/run_suite.py` =
  `EMAIL_TEST_FILES_PASS=51`, `SUITE_RC=0`. The production database was
  untouched: a read-only (`mode=ro`) snapshot before and after showed 287
  messages, `integrity=ok`.
- Linux native: NAS clone host, `python3.11.2`. `tests/run_suite.py` =
  `EMAIL_TEST_FILES_PASS=51`, rc0. `scripts/ci.sh` = `CI_MATRIX_OK count=20`,
  `PROVENANCE_SOFTWRAP_REGRESSION 5/5`, rc0.
  `tests/test_repository_lifecycle.sh` = `LIFECYCLE_MATRIX_OK`,
  `INSTALL_OK version=0.14.1`, `CHECKSUMS_OK`, `REPOSITORY_CONTRACT_OK`,
  `VERIFY_OK`. The `Europe/Berlin` deadline case asserts `+01:00` and is not
  skipped (a system tz database is present).
- Windows via WSL2: covered by the `0.14.0` thin environment smoke
  (`desktop-kfscv8c-1`, Ubuntu-24.04) plus the Linux-native full-suite green
  above; the `0.14.1` delta is equivalent on Linux and was not separately
  re-run.
- Windows-native: `win11-test` (`DESKTOP-GB7NLMT`, `king@100.108.63.25`),
  Python 3.12.7. On the bare host with `tzdata` absent, `tests/run_suite.py` =
  `EMAIL_TEST_FILES_PASS=51` (the onboarding matrix prints its Windows-native
  skip marker; the `Europe/Berlin` quote-action case skips). With
  `tzdata 2026.5` present, `test_email_quote_actions` = 29/29 including
  `Europe/Berlin` -> `+01:00`. The host was restored to its bare state after.

`plugin.yaml` declares `tzdata>=2024.1; sys_platform == 'win32'` as a
`python_dependencies` entry. Under the Hermes standalone-plugin dependency
framework this is validated and surfaced with a pip-install hint but is not
auto-installed; the code's fixed-offset fallback keeps date rendering correct
even when the dependency is absent, and the CI Windows matrix exercises both
states.

GitHub Actions passed from the exact published commit before the tag was
created: pull request #5 (head `fix/qoder-email-20261016` -> base `main`),
6/6 check-runs `completed=success`, 0 failures — `static-and-matrix` (ubuntu)
x2, `windows-suite` (`tzdata` absent) x2, `windows-suite` (`tzdata` present)
x2. The `windows-suite` job (`runs-on: windows-latest`) is the native-Windows
leg added by this release and required by the multi-platform contract; both
`tzdata` states pass on real GitHub Windows runners. Merged via merge commit
`8b36846cdc3bade047094359ecfd8e1fa9fb846a`; annotated tag `v0.14.1` created at
`1535e94` and pushed; the tag-push CI event was green.

Production deployment of `0.14.1` is a separate, explicitly gated window and
is recorded when it occurs. At the time of this acceptance the production
gateway still ran `0.14.0`.

## 0.14.0 all-model triage acceptance

The `0.14.0` production candidate completed acceptance and was deployed on
2026-10-23. Accepted source: public repository commit `2b7db42`
("chore(release): v0.14.0"), five commits over `f2eec00` (`0.13.5`),
`VERSION` = `0.14.0`, `PACKAGE_VERSION` =
`EMAIL_WATCHDOG_STABLE_RELEASE_V0P14P0`.

- The deterministic fast lane and its production router short-circuit were
  removed: every triage decision now goes through the model, so no hard-coded
  category can misfile mail the classifier cannot enumerate. The removal is
  production-behaviour-neutral because the fast lane was already disabled in
  the production configuration.
- An editorial-review gate rejects a degenerate summary (for example a bare
  ellipsis) and falls back to a grounded draft instead of pushing an empty
  card.
- Re-submitting an already-delivered notification from the durable outbox is
  an idempotent no-op, ending a chronic re-send of a delivered card.
- Linux repository contract, checksum, compile, unit, policy, transport,
  concurrency and provenance matrices: `CI_MATRIX_OK count=20`; full suite
  `EMAIL_TEST_FILES_PASS=51`, rc0.
- Fresh-container installation from the pushed `v0.14.0` tag tree on the
  production platform (`docker run --rm --network none --tmpfs /opt/data`,
  `install.sh`): `CHECKSUMS_OK`, `INSTALL_OK version=0.14.0`, `VERIFY_OK`,
  `REPOSITORY_CONTRACT_OK`, correct directory/file modes, active Hook
  materialized, handler byte-stable against the tag.
- Production deployment on host `ugreen-nas` (`DXP4800PLUS-RIN`,
  `vive@100.105.76.45`) into gateway container `hermes-v0213-gateway`: a
  WAL-safe online backup of the plugin database and a plugin source/config
  backup were taken first; `upgrade.sh` ran with `HERMES_HOME=/opt/data` and
  `HERMES_EMAIL_WATCHDOG_SKILL_DIR` pinned to
  `/opt/data/plugins/hermes-email-watchdog` (the curated `skills/` library was
  left untouched). File owners were restored after the root-run upgrade
  (`config.json` to `10000:10000` mode 600, plugin-data to `10000:10000`).
- Restart was proven by three signals: the worker PID changed, a fresh
  hook-load line appeared in `/opt/data/logs/gateways/default/current`, and
  `status.json` advanced its poll heartbeat with `state=running` and
  `last_error=''`. Post-restart the live plugin reported `VERSION=0.14.0` and
  the fast-lane functions were absent from the live scripts, with no
  error/traceback since restart.

Windows-native acceptance for `0.14.0` was executed on `win11-test`
(`DESKTOP-GB7NLMT`, `king@100.108.63.25`, Python 3.12.7) against a pristine
`v0.14.0` tree and surfaced the two cross-platform defects (Windows zoneinfo,
state-path separator) that `0.14.1` then fixed. Those defects are recorded
under the `0.14.1` acceptance above.

## 0.10.1 evidence-complete publication acceptance

The production candidate completed final acceptance on 2026-10-05:

- Windows regression suite: 400 tests passed, with 4 POSIX-only concurrency
  cases skipped by platform.
- Linux/NAS regression suite: 400 tests passed, including the POSIX runtime
  paths.
- Ten consecutive isolated rounds used 20 unique real historical mailbox
  messages with zero plugin errors. The sample covered academic reports and
  attachments, account login links, bills and electronic invoices, spam,
  service-disconnection notices, conference correspondence, school platform
  messages, and manuscript review requests.
- Eighteen messages completed semantic and final editorial review. Two
  deliberately observed editorial failures completed the evidence-preserving
  degraded path with zero missing safe artifacts, malformed Markdown, mailbox
  chrome, or attachment loss.
- Replays used read-only raw export, isolated databases/state/attachments, and
  no message delivery. They did not alter mailbox flags or enqueue historical
  notifications.

Evidence is stored on the production host under
`/opt/data/migration-staging/email-watchdog-v0101-acceptance/streak-r01`
through `streak-r10`. The accepted source lineage ends at the public commit
recorded by the installed package checksums.

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
- Multi-platform verification is recorded for the release. The four supported
  targets are Docker/NAS, Linux native, Windows via WSL2, and Windows-native
  Hermes desktop. GitHub Actions must include both an Ubuntu leg (`ci.sh` +
  lifecycle + full suite) and a Windows leg (Python suite on `windows-latest`,
  POSIX-only concurrency/signal cases skipped, Windows-native unit tests really
  executed). Docker/NAS is covered by the fresh-container install from the real
  tag plus the runtime-uid readability gate; WSL2 by a thin environment smoke on
  top of Linux; Windows-native Hermes-desktop install and onboarding by a real
  Windows host (CI-automated or manual-with-evidence). A target must not be
  claimed accepted on the basis of cross-OS parameter-simulated unit tests
  alone. See the multi-platform support and verification contract in
  `DEVELOPMENT_MEMORANDUM.md`.
