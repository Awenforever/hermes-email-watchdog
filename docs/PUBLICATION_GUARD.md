# Guarded publication contract

The reviewed public repository is:

- owner: `Awenforever`
- repository: `hermes-email-watchdog`
- branch: `main`
- visibility: public
- license: MIT
- security route: GitHub private vulnerability reporting
- reviewed remote parent: `66b4ab176f894564e28992166e86b800d3c0656a`

The existing public history must be preserved. Publication must be a normal
fast-forward; force-push, branch deletion and history rewriting are forbidden.

Before any push, the publication tool must:

1. Verify the live public `main` still equals the reviewed remote parent.
2. Verify the exact candidate commit and bundle checksum.
3. Verify GitHub private vulnerability reporting is enabled.
4. Accept a newly created repository-scoped credential only through a hidden
   terminal prompt or protected file descriptor. The credential must not appear
   in chat, command-line arguments, shell history, logs or result archives.
5. Verify the credential has only the permissions required for the current
   operation.
6. Push only the reviewed commit to `main`.
7. Wait for GitHub Actions to pass before creating a tag or release.
8. Create release assets from the exact reviewed commit and publish SHA256
   checksums.
9. Perform a fresh-container installation from the real GitHub URL.

The previously exposed fine-grained token was confirmed deleted by the
repository owner on 2026-07-17 and must never be reused.
