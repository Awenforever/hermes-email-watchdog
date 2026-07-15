# Security policy

Report vulnerabilities privately to the repository owner.

## Security invariants

- Mailbox read-only behavior is mandatory.
- Credentials are never committed to the repository.
- Runtime configuration exports must be redacted.
- User data is preserved by default uninstall.
- Purge is explicit and ownership-scoped.
- `weixin.py` and WeChat Enhance are outside this repository's write boundary.
- Installation and upgrade refuse unowned drift.
