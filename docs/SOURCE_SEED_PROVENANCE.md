# Source seed provenance

- Freeze package SHA256: `bbd7f8628431e77761d9e80bc144ed60305a42d4035ecc0a99960a9ed236146b`
- Frozen protocol: `readable_grounded_core_v1u`
- Frozen renderer: `adaptive_v1e`
- Frozen handler: `EMAIL_WATCHDOG_OUTBOX_NONBLOCKING_BACKOFF_V1`
- Mobile visual E2E: passed
- Candidate construction: isolated; production not modified

The repository candidate intentionally removes dormant mailbox-write modules and
replaces the sole active `email_reply` dependency with local-only thread state
tracking.
