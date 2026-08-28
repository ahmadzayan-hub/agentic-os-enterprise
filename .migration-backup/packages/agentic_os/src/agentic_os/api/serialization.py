"""JSON projection helpers for API responses.

PostgreSQL ``numeric`` arrives as :class:`decimal.Decimal`, and Pydantic v2 —
which FastAPI uses to encode responses — renders ``Decimal`` as a *string*.
That forces every consumer to remember to coerce a cost or a score before doing
arithmetic on it, and produces a runtime error the first time someone forgets.

Money and scores are numbers in this API's contract, so rows are projected
through :func:`jsonify` on the way out. The database remains the authority for
exact decimal arithmetic; only the wire representation changes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID


def jsonify(value: Any) -> Any:
    """Recursively convert database values into JSON-friendly primitives."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): jsonify(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [jsonify(item) for item in value]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [jsonify(item) for item in value]
    return value


def rows(result: Any) -> list[dict[str, Any]]:
    """Project a ``.mappings()`` result into JSON-ready dictionaries."""
    return [jsonify(dict(row)) for row in result]


def row(record: Any) -> dict[str, Any] | None:
    """Project a single mapping row, or ``None``."""
    return None if record is None else jsonify(dict(record))
