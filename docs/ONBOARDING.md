# Onboarding contract

Email Watchdog has one setup engine: `scripts/email_onboarding.py`. Natural
conversation and non-interactive automation are wrappers around the same
`status`, `plan`, `apply`, `validate`, `enable`, and `disable` functions.

## Data flow

```text
current Hermes session / agent:start Hook
                ↓
      pending Weixin target only
                ↓
status → plan → atomic apply → read-only validation → explicit enable
```

The pending Hook state stores identifiers and SHA256 values only. It never
stores the inbound message body.

## Secrets

Mailbox secrets are not accepted in input JSON and are not stored by the skill.
A generated Himalaya file may contain only a command that retrieves a secret
from an external source. Literal `echo` and `printf` commands are rejected.

## Rollback

`apply` snapshots all files it may change, writes through temporary files, runs
read-only validation, and restores the exact snapshots when validation fails.
The scheduler remains disabled during the transaction.
