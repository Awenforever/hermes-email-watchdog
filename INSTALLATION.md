# Installation

Clone the complete repository, verify the expected immutable commit, then run:

```bash
bash install.sh
bash verify.sh
```

Installation never opens a questionnaire and leaves the scheduler disabled.
Configure it through the current Hermes conversation or non-interactively:

```bash
bash /opt/data/skills/hermes-email-watchdog/setup.sh status --json
```

Environment overrides:

```text
HERMES_EMAIL_WATCHDOG_DATA_ROOT
HERMES_EMAIL_WATCHDOG_SKILL_DIR
HERMES_EMAIL_WATCHDOG_ACTIVE_HOOK_DIR
HERMES_EMAIL_WATCHDOG_INSTALL_STATE_DIR
HERMES_EMAIL_WATCHDOG_STATE_ROOT
EMAIL_WATCHDOG_CONFIG
HERMES_EMAIL_WATCHDOG_HIMALAYA_BIN
```

Upgrade:

```bash
bash upgrade.sh
```

Default uninstall first removes only the runtime Hook owned by this plugin,
then removes the plugin package. Configuration, mailbox authentication,
onboarding, state, and learning data remain in the Hermes profile:

```bash
hermes email-watchdog uninstall-runtime
hermes plugins remove hermes-email-watchdog
```

`uninstall-runtime` works on Windows, Linux, WSL2, and Docker. It verifies the
installed files before removal, refuses to touch externally changed files, and
restores a pre-existing Hook when one was backed up. `bash uninstall.sh`
remains available for legacy POSIX installations.

Destructive purge requires:

```bash
bash purge.sh --confirm-purge-user-data
```
