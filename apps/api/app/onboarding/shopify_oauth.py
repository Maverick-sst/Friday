"""Shopify OAuth helpers (PRD §17.2).

Stateless, HMAC-signed OAuth state tokens; authorize-URL construction; and
shared-secret verification utilities used by the install callback.
"""

import hashlib
import hmac
import json
import secrets
from base64 import urlsafe_b64decode, urlsafe_b64encode
from time import time
from urllib.parse import urlencode, urlparse

from app.core.config import get_settings
from app.core.errors import GatewayError

_STATE_TTL_SECONDS = 600
_MYSHOPIFY_PATTERN = r"^[a-z0-9][a-z0-9\-]*\.myshopify\.com$"


def normalize_store_host(store_url: str) -> str:
    """Accept `store.myshopify.com`, https://store.myshopify.com/admin etc."""
    import re

    raw = store_url.strip()
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    parsed = urlparse(raw)
    host = (parsed.netloc or parsed.path.split("/")[0]).lower().strip("/")
    if not host.endswith(".myshopify.com") or not re.match(_MYSHOPIFY_PATTERN, host):
        raise GatewayError(
            "INVALID_SHOP_DOMAIN",
            "Expected a *.myshopify.com store domain",
        )
    return host


def _sign(payload: bytes) -> str:
    secret = get_settings().shopify_api_secret or get_settings().secret_key
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def make_state(shop_host: str) -> str:
    payload = json.dumps(
        {"shop": shop_host, "nonce": secrets.token_hex(12), "ts": int(time())},
        separators=(",", ":"),
    ).encode()
    body = urlsafe_b64encode(payload).decode().rstrip("=")
    return f"{body}.{_sign(body.encode())}"


def read_state(token: str) -> dict | None:
    try:
        body, sig = token.rsplit(".", 1)
        if not hmac.compare_digest(sig, _sign(body.encode())):
            return None
        padded = body + "=" * (-len(body) % 4)
        data = json.loads(urlsafe_b64decode(padded.encode()))
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
    if int(data.get("ts", 0)) < time() - _STATE_TTL_SECONDS:
        return None
    return data


def build_authorize_url(shop_host: str) -> tuple[str, str]:
    settings = get_settings()
    state = make_state(shop_host)
    params = {
        "client_id": settings.shopify_api_key,
        "scope": ",".join(settings.shopify_scope_list),
        "redirect_uri": settings.shopify_redirect_uri,
        "state": state,
    }
    url = f"https://{shop_host}/admin/oauth/authorize?{urlencode(params)}"
    return url, state


def verify_callback_hmac(query_params: dict[str, str]) -> bool:
    """Verify the HMAC on Shopify's OAuth callback per current docs.

    Every query param except `hmac` and `signature` is sorted, joined with &,
    and MAC'd with the app secret.
    """
    provided = query_params.get("hmac", "")
    if not provided:
        return False
    message = "&".join(
        f"{k}={v}" for k, v in sorted(query_params.items()) if k not in ("hmac", "signature")
    )
    expected = _sign(message.encode())
    return hmac.compare_digest(provided, expected)


def exchange_code_for_token(shop_host: str, code: str) -> str:
    """POST the authorization code to Shopify to obtain a permanent access token."""
    import httpx

    settings = get_settings()
    response = httpx.post(
        f"https://{shop_host}/admin/oauth/access_token",
        json={
            "client_id": settings.shopify_api_key,
            "client_secret": settings.shopify_api_secret,
            "code": code,
        },
        timeout=20,
    )
    response.raise_for_status()
    token = response.json().get("access_token")
    if not token:
        raise GatewayError("SHOPIFY_TOKEN_MISSING", "Shopify did not return an access token")
    return token
