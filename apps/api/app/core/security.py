"""Secret encryption helpers.

Merchant credentials (e.g., Shopify access tokens) are encrypted at rest with
Fernet, keyed deterministically from `SECRET_KEY` so values survive restarts.
Decrypted values never leave the server process boundary.
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


def _fernet_from_secret(secret: str) -> Fernet:
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(raw: str) -> str:
    f = _fernet_from_secret(get_settings().secret_key)
    return f.encrypt(raw.encode("utf-8")).decode("utf-8")


def decrypt_secret(token: str) -> str:
    f = _fernet_from_secret(get_settings().secret_key)
    try:
        return f.decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:  # pragma: no cover - indicates misconfigured SECRET_KEY
        raise ValueError("Failed to decrypt stored credential; check SECRET_KEY") from exc
