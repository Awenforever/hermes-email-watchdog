# Development memorandum

This document records non-negotiable engineering invariants and regression
history. It is intentionally separate from the product README.

## 2026-10-07 — Quoted-message actions are structured events

- Never infer Weixin quote order or parse a display string. The current user
  text is `text_item.text`; the quoted bubble is `ref_msg.message_item`.
- Channel integration must publish a platform-neutral `message:inbound` event
  only after refreshing the inbound Context Token and resuming durable FIFO.
- Email Watchdog consumes that optional event but channel plugins must not
  import, require, or special-case Email Watchdog.
- An email deadline is evidence, not authorization. Store an inert candidate;
  create active reminders only after exact user intent (`提醒我` or selected
  numbers). Date-only deadlines mean 09:00 in the Hermes profile timezone.
- A reply is a two-phase operation. Preserve all text after the first `回复`
  newline byte-for-byte (apart from newline normalization), append only the
  configured mailbox signature, render a draft, then require `确认发送` quoted
  against that draft.
- Reply only to the sender. Block no-reply/automated/high-risk destinations.
  Every action is paired-user gated, restart-safe, and idempotent.
- One visible notification bubble must correspond to exactly one email action.
  Polling may discover many messages, but each is stored, retried, attributed,
  and sent as a separate outbox entry with only its own attachments and model
  provenance. Never ask the user to disambiguate a batch bubble that the system
  itself created.
- Serialize action-state transitions with a cross-thread and cross-process
  lock. Duplicate or concurrent inbound delivery must produce one reminder
  activation or one confirmation transition, not repeated side effects.
- Consumers must require the channel contract's explicit `authorized: true`;
  a syntactically valid Hook event is not authorization.
- `safety.outbound_email_enabled` must describe the real configured transport
  capability. Its default is false; when confirmed replies are enabled it is
  true, while `mailbox_read_only` and `mailbox_mutation_enabled=false` continue
  to describe monitoring and mailbox-state behavior. Never publish a config
  whose safety flags contradict what runtime code can do.
- Himalaya's generated reply template may inject its own signature and quoted
  original. Keep its addressing/thread headers, but replace the entire body
  with the already reviewed Watchdog draft before `template send`; otherwise
  “verbatim body plus one configured signature” is not true in production.
- Before transport, persist a draft as `transmitting`. If a send times out or
  the process dies after handoff, fail closed as `delivery_uncertain` and never
  retry automatically; the user must inspect Sent mail. Avoiding duplicate
  external email is more important than pretending an ambiguous timeout failed.

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

## 2026-10-05 — Portable path expansion during enable

Production recovery exposed a migrated configuration whose `paths.seen` value
was `$HERMES_EMAIL_WATCHDOG_STATE_ROOT/seen.json`. Runtime loading expanded it,
but the enable-time baseline check constructed a `Path` directly and attempted
to access a literal dollar-prefixed directory. Every lifecycle entry point must
apply the same portable path expansion contract. Upgrade acceptance must include
disable → restart → enable with an existing non-empty seen index and must prove
that no historical message is replayed.

## 2026-10-08 — Receipt time and native-quote action ownership

Notification chrome has one operational timestamp: mailbox receipt time. The
priority, category, and receipt time belong on one metadata row. Do not show a
second ``Date:``/sent timestamp or move either time into a separate prose row;
when a provider lacks server receipt time, the source sent time is only a
fallback for that single slot.

Weixin native quotes are previews, not guaranteed full-message copies. Email
actions must accept an exact durable-message match or one sufficiently long,
unique normalized preview match. The same rule applies to reply-draft
confirmation. Never use “most recent email” or a short fuzzy snippet. If a
mail-shaped quote belongs to an old pre-action push or has fallen outside local
retention, consume the explicit mail command and explain that the source cannot
be safely restored; do not pass it to the conversational agent and never guess
the recipient. Multiple matches always fail closed.

The command namespace is exact: ``@回复`` starts a verbatim reply body on the
next line, and ``@转发 address@example.com`` starts a single-recipient forward
with an optional verbatim body on later lines. Both produce a durable draft and
require a separately quoted ``@确认发送``; ``@取消`` discards it. Forwarding must
use the mail client's native forward template so the original MIME/MML body and
attachments survive. Remove the client's implicit preface/signature, then add
only the user's explicit body and configured mailbox signature. With no body,
forward directly and add nothing.

Channel plugins may drop native quote metadata, so a leading ``@`` mail command
can arrive without a reference. It still belongs to Email Watchdog's namespace
and must be consumed with a safe “quote required” explanation. Never infer the
latest email. WeChat Enhance remains business-neutral: it only publishes the
generic event after Context Token refresh/FIFO handling.

## 2026-10-08 — Real transport contracts and mailbox identity

Unit mocks must not define a third-party CLI contract. Himalaya accepts simple
one-line templates as positional values, but complete multi-line MML reply and
forward templates must be supplied on standard input. Passing a whole template
as one argv item produces `cannot parse template` in the real v1.2 client even
when mocked calls appear correct. Regression tests must assert both that stdin
contains the complete template and that no user-authored body appears in argv.

Mailbox account labels are presentation values, not case-stable identifiers.
Every lookup of per-account signatures must normalize both the runtime label
and configured keys with Unicode-aware `casefold`; the signature content itself
must remain user-authored apart from newline normalization.

For production acceptance, creating a draft is insufficient. A newly created
draft must visibly contain the configured account signature, and an explicit
confirmation must traverse the real configured mail client successfully. A
failed or uncertain attempt must never be retried automatically.

## 2026-10-08 — Signature authoring is not wire formatting

Do not send a configured Markdown separator or authoring whitespace directly
as an email body. A leading dash-only line in a mailbox signature represents a
semantic divider: remove it from content, compact the signature fields below
it, and render it as a fixed-width HTML rule. The plain-text alternative uses a
Unicode rule rather than three literal dashes.

Confirmed replies are `multipart/alternative`: the text part remains usable in
plain clients, while the HTML part provides deterministic spacing and signature
layout in Gmail and Outlook. User-authored reply text remains unchanged in the
plain part and is HTML-escaped in the rich part. Because MML directives are
active inside template text, reject the reserved `<#` prefix rather than
silently rewriting it or permitting template injection. Draft previews must use
the same signature-layout semantics as the eventual wire message.

## 2026-10-08 — Terminal drafts, one signature path, and truthful recall

A draft remains an Email Watchdog object after it reaches a terminal state.
Quoting a sent or cancelled draft with ``@确认发送`` or ``@取消`` must produce an
idempotent state explanation; it must never fall through to Hermes and must
never repeat delivery. Delivery-uncertain drafts remain fail-closed. Success
notifications are durable addressable objects too, with recipient and subject,
so a quoted ``@召回`` can resolve the exact sent draft rather than a recent
message heuristic.

Reply and forward are different transports but not different identities. Any
outgoing message that has a user-authored body uses the same per-account
signature resolver and semantic signature renderer. A bodyless forward adds no
signature. Subject prefixing is idempotent: an existing ``Fwd:``/``Fw:`` or
``Re:`` must not be duplicated.

Recall is a provider capability, not an IMAP deletion. Never report success
unless the provider returns a verifiable recall result. Coremail recall is only
eligible for unread recipients in the same Coremail system and may require a
Webmail-only operation. External recipients and transports without a recall
adapter receive the truthful capability result and safe next action. Repeated
recall commands record and return their result without inventing state.

User-facing failures must describe the current action only. Do not expose
development-history wording such as “the system will not guess the latest
mail”, internal match fields, or adapter debugging advice.
