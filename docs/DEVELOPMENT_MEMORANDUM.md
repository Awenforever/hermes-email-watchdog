# Development memorandum

This document records non-negotiable engineering invariants and regression
history. It is intentionally separate from the product README.

## 2026-10-05 — Evidence-bound actions and real model fallback

### Incident

A Springer Nature review invitation contained a valid accept/decline URL in the
source cache and link table, but the delivered notification mentioned the action
without exposing the URL. The same card contained `please decli`, a word cut in
the middle.

The failure had three independent causes:

1. The configured semantic model completed, but both editorial model aliases
   returned HTTP 403. The pipeline then treated deterministic composition as if
   it were an equivalent semantic model fallback.
2. The deterministic link selector used an incomplete list of action verbs.
   Because accept/decline was absent, it discarded valid source evidence.
3. Action-quote extraction truncated the source to 4,000 characters before
   inspecting its last line, making the artificial boundary look like prose.

### Required architecture

- Models own open-ended meaning: classification, usefulness, action intent,
  importance, deadline interpretation, and presentation judgment.
- Runtime code owns evidence, safety, and execution: source identity, stable
  link IDs, attachment identity, credential boundaries, idempotency, and actual
  delivery/reminder receipts.
- Source URLs are never copied from model output. Every source URL receives a
  stable `link_N` ID. A model may select only display-safe IDs; the runtime
  resolves those IDs back to the original URL.
- Link safety is structural. Tracking wrappers, unsubscribe/preferences, and
  mail-chrome actions may be hidden. Business usefulness must never depend on a
  finite verb/category allowlist.
- Explicit plugin model aliases are preferences, not ownership of provider
  routing. Attempt the explicit primary, then explicit fallback, then an empty
  model request that delegates to Hermes' current live route. Never scrape a
  provider's error response to invent another alias.
- Deterministic rendering is not a semantic model fallback. If every model route
  fails, emit a visibly degraded evidence-preservation card containing sender,
  subject, source time, bounded source excerpt, all structurally safe source
  links, and attachment status. State that action, deadline, and importance were
  not inferred.
- Editorial rewriting may improve prose but may not remove verified evidence or
  weaken source provenance.
- Input truncation must not promote a synthetic cut boundary into user-visible
  content. Extraction operates on the bounded full semantic source, and tests
  must cover words crossing the former 4,000-character boundary.

### Mandatory regression coverage

- A review invitation preserves its source accept/decline link without a
  hard-coded accept/decline rule.
- Unsafe opt-out/tracking links cannot be selected by model-provided IDs.
- Missing model link selection on an actionable message retains all structurally
  safe source links under neutral labels.
- Semantic and editorial paths both attempt explicit primary, explicit fallback,
  then Hermes default routing.
- Complete model-route failure produces the transparent evidence card and never
  enters the legacy semantic formatter.
- The historical incident message can be replayed locally without network
  delivery, preserving the invitation URL and never emitting a truncated word.

### Release and production rules

- Do not deploy from a dirty or untested tree.
- Invalidate cached semantic decisions whenever the evidence contract changes.
- Before production replacement, record the current status and hashes and create
  a rollback copy of plugin, Skill, Hook, configuration, outbox, seen state, and
  databases (including SQLite WAL/SHM state or consistent database backups).
- Never replay a historical email over a real notification channel during an
  upgrade unless the user explicitly authorizes it.
- Verify behavior, not merely file presence: scheduler running, Hook loaded,
  configuration preserved, historical dry replay correct, and no change to
  mailbox authentication or message-adapter code.
