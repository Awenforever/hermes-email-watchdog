"""Tolerant datetime parsing shared by the email pipeline.

Python 3.11 relaxed ``datetime.fromisoformat`` (space separator, bare ``+0800``
offset, ``Z`` suffix). Python 3.10 -- the default on Ubuntu 22.04 -- rejects
those, so a date harvested from a real mail header such as
``2026-06-06 15:34 +0800`` silently failed to parse and the stale-mail guard
stopped recognising old mail. Every module now parses through this helper.
"""
from __future__ import annotations

from datetime import datetime

_EXTRA_FORMATS = (
    "%Y-%m-%d %H:%M %z",
    "%Y-%m-%d %H:%M:%S %z",
    "%Y-%m-%dT%H:%M %z",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
)


def parse_datetime(value) -> datetime:
    """Parse an ISO-ish timestamp, tolerating pre-3.11 formats.

    Raises ``ValueError`` for unparseable input, matching ``fromisoformat``.
    """
    text = str(value or "").strip()
    if not text:
        raise ValueError("empty datetime")
    candidate = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(candidate)
    except ValueError:
        pass
    for fmt in _EXTRA_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError("unrecognised datetime: %s" % text[:60])
