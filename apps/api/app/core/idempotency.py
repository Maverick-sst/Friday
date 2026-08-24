"""Idempotency guard for payment-critical endpoints (PRD §28).

A retried request with the same key returns the stored first response instead
of executing again - retries can never create duplicate payments.
"""

import json
from collections.abc import Callable
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import IdempotencyKey


def scoped_key(endpoint: str, scope: str, key: str) -> str:
    raw = f"{endpoint}:{scope}:{key}"
    return raw[:160]


def with_idempotency(
    db: Session,
    *,
    endpoint: str,
    scope: str,
    key: str,
    producer: Callable[[], dict[str, Any]],
) -> tuple[dict[str, Any], bool]:
    """Run `producer` once per (endpoint, scope, key); replays the snapshot after."""
    full_key = scoped_key(endpoint, scope, key)

    existing = db.get(IdempotencyKey, full_key)
    if existing is not None and existing.response_snapshot:
        snapshot = dict(existing.response_snapshot)
        snapshot["idempotent_replay"] = True
        return snapshot, True

    payload = producer()

    db.add(
        IdempotencyKey(
            key=full_key,
            endpoint=endpoint,
            response_snapshot=json.loads(json.dumps(payload, default=str)),
            status="completed",
        )
    )
    try:
        db.commit()
    except IntegrityError:
        # Concurrent first attempt won; replay its snapshot.
        db.rollback()
        existing = db.get(IdempotencyKey, full_key)
        snapshot = dict(existing.response_snapshot) if existing else payload  # pragma: no cover
        snapshot["idempotent_replay"] = True
        return snapshot, True
    return payload, False
