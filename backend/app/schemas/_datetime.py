"""Shared datetime serialization helpers for response schemas.

The Postgres tables store datetimes as ``TIMESTAMP WITHOUT TIME ZONE``
(naive UTC). When Pydantic serializes a naive datetime to JSON, the
output is ``"2026-05-21T14:31:00"`` with no timezone designator.

ECMAScript's ``new Date(<string>)`` interprets that as **local time**,
NOT UTC. In an IST browser the string ``"2026-05-21T14:31:00"`` is
read as IST 14:31 — but the value was actually stored as UTC 14:31,
which is IST 20:01. So the user sees their times 5h30m off.

``utc_iso()`` re-attaches ``tzinfo=timezone.utc`` to naive datetimes
before ``.isoformat()``, producing ``"2026-05-21T14:31:00+00:00"``.
JS parses that as UTC; the frontend's renderer
(``toLocaleString({timeZone: 'Asia/Kolkata'})`` in ``lib/design.ts``)
then converts to IST display correctly.

Apply to a response schema via ``@field_serializer`` on each datetime
field (Pydantic v2 doesn't support ``'*'`` wildcards), e.g.::

    @field_serializer('due_at', 'created_at', 'updated_at')
    def _ser_dt(self, v):
        return utc_iso(v)
"""
from datetime import datetime, timezone
from typing import Optional


def utc_iso(value: Optional[datetime]) -> Optional[str]:
    """Serialize a (possibly naive) datetime as ISO 8601 with UTC offset.

    Returns ``None`` for ``None`` input. Naive datetimes are treated as
    UTC (matching how the DB stores them). Aware datetimes are
    converted to UTC before formatting.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat()
