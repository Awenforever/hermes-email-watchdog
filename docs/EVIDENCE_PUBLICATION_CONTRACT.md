# Evidence publication contract

This memorandum records non-negotiable development invariants. It is not an
installation guide and must not be copied into the public-facing README.

## The boundary

Email Watchdog may use models to interpret intent, relevance, priority, and
presentation. Models do not own source artifacts. URLs and attachments are
inventoried by the runtime, assigned stable identities, and materialized only
from the original message.

A notification must never say or imply that the user should follow a link,
open a document, use a code, or meet a deadline while silently dropping the
corresponding source evidence available to the runtime.

## Failure behavior

1. A valid semantic decision plus a successful editorial review publishes the
   reviewed Markdown and the explicitly selected source artifacts.
2. A valid semantic decision plus an unavailable editorial chain publishes the
   semantic explanation with every display-safe source link and attachment
   preserved. Unreviewed operational sections are removed and rebuilt as
   neutral evidence, so fragments cannot become user instructions. This
   degraded mode is explicit in metadata and in the card.
3. If no semantic model route is usable, the runtime publishes a transparent
   lossless source card and does not pretend to infer action, importance, or
   deadlines.
4. Unsubscribe, tracking, preference-management, and other mail chrome remain
   excluded by structural source policy in every mode.

The deterministic runtime is therefore an evidence fallback, not a substitute
semantic model. Model fallback remains Hermes' configured primary/fallback
route and is outside this plugin's credential ownership.

An inherited model route must call Hermes with provider `auto`, not merely an
empty model name on an auxiliary task. The latter can remain pinned to the
primary provider and never traverse Hermes' cross-provider fallback chain after
an authorization failure.

## Regression rule

Do not fix missing evidence by adding a phrase such as “click”, “log in”, or
“confirm” to an action-word list. New regressions must be expressed as general
publication invariants and tested across unrelated email categories.

Release acceptance requires the full automated suite plus ten consecutive
isolated rounds of real historical mail with no information-loss,
presentation, attachment, temporal, or routing errors. Any failure resets the
consecutive-round count. Replays must not mutate the mailbox or send historical
notifications.

The acceptance manifest separates plugin errors from host model-route warnings.
A host-level 403 is not counted as a plugin defect only when the publication
boundary demonstrably entered evidence-complete degraded mode and every safe
source artifact remained present. It remains an explicit environment warning
and must never be reported as a healthy editorial model.
