# Security policy

Do not disclose vulnerabilities in a public issue.

This repository uses **GitHub private vulnerability reporting** as its security
contact route. After publication, open the repository's **Security → Advisories**
page and select **Report a vulnerability** to submit a private report.

The guarded publication process must verify that private vulnerability reporting
is enabled on `Awenforever/hermes-email-watchdog` before pushing the reviewed
release candidate or creating a release.

## Security invariants

- Mailbox read-only behavior is mandatory.
- Credentials are never committed to the repository.
- Runtime configuration exports must be redacted.
- User data is preserved by default uninstall.
- Purge is explicit and ownership-scoped.
- `weixin.py` and WeChat Enhance are outside this repository's write boundary.
- Installation and upgrade refuse unowned drift.

## Onboarding security

- Mailbox passwords or secret values are never accepted by onboarding.
- Generated Himalaya configuration is IMAP-only and references an external secret source.
- Setup writes atomically, validates read-only access, and rolls back failures.
- Session targets are redacted in command output and the Hook stores no message body.
- State-changing onboarding operations are serialized and validate before publish.
