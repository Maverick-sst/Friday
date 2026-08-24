"""Shared DB helpers."""

import uuid as uuidlib
from datetime import UTC, datetime


def new_uuid() -> str:
    return str(uuidlib.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(dt: datetime) -> datetime:
    """Normalize DB-loaded datetimes (SQLite returns naive values) to aware UTC."""
    if dt is None:
        raise ValueError("cannot normalize None datetime")
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)
