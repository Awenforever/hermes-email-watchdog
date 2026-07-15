# Installation

This repository candidate supports direct installation into an isolated Hermes
data root. It does not restart the gateway automatically.

```bash
bash install.sh
bash verify.sh
```

Environment overrides:

```text
HERMES_EMAIL_WATCHDOG_DATA_ROOT
HERMES_EMAIL_WATCHDOG_SKILL_DIR
HERMES_EMAIL_WATCHDOG_ACTIVE_HOOK_DIR
HERMES_EMAIL_WATCHDOG_INSTALL_STATE_DIR
```

The initial install is disabled until onboarding creates an account
configuration and explicitly enables the scheduler.

Upgrade:

```bash
bash upgrade.sh
```

Default uninstall removes the active hook and installed canonical source while
preserving all user data:

```bash
bash uninstall.sh
```

Destructive purge requires:

```bash
bash purge.sh --confirm-purge-user-data
```
