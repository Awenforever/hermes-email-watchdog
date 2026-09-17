# Public repository history migration

This repository previously published a v3 experimental Email Watchdog tree at
commit `36617b50a6016482b4ea03e2d72e05930eb2e442`. That tree included mailbox-write and outbound-action
modules such as reply, send, pending-action, calendar and batch operations.

The accepted read-only release candidate was developed and tested independently
at commit `e5ab5b12e4a13fc36eb829e936bea8ac57dfafe7`. Its safety boundary is intentionally narrower:

- mailbox access is read-only;
- outbound email and mailbox mutation are disabled;
- installation is disabled until explicit onboarding and validation;
- `weixin.py` is outside repository ownership;
- state writes are atomic and concurrency-safe;
- uninstall preserves user data, while purge is explicit and ownership-scoped.

The integration commit preserves both complete Git histories as parents while
using the accepted read-only candidate as the current working tree. Historical
write-capable modules remain accessible in earlier commits but are deliberately
absent from the current release tree.

No force-push or remote-history rewrite is required: the integration commit is
a normal descendant of the existing public `main` commit and can be published
as a fast-forward update after final approval.
