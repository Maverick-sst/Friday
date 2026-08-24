"""Shared DB helpers."""

import uuid as uuidlib
from datetime import UTC, datetime


def new_uuid() -> str:
    return str(uuidlib.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)
