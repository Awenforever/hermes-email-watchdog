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

Default uninstall removes code and the active Hook while preserving all owned
configuration, onboarding, state, and learning data:

```bash
bash uninstall.sh
```

Destructive purge requires:

```bash
bash purge.sh --confirm-purge-user-data
```
