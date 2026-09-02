"""Browser Use Cloud client (Fleet PRD B6).

Wraps the task-based V4 REST API: create a run, poll its status until a terminal
state, then fetch the full result. Used as a LAST-RESORT extractor when a static
httpx fetch of a product page yields no machine-readable offer (anti-bot walls,
JS-only rendering, CAPTCHA) — the managed stealth browser solves those while the
static path stays the cheap default.

API contract (llms-full.txt):
    POST   /api/v4/runs               -> {"id": "run_...", "status": "running"}
    GET    /api/v4/runs/{id}/status   -> {"id": "...", "status": "running|completed|failed|cancelled"}
    GET    /api/v4/runs/{id}          -> {"id": "...", "status": "...", "result": "<string>", ...}
    Auth   header: X-Browser-Use-API-Key: bu_...
    V4 returns run.result as a STRING; we ask for JSON and validate client-side.
"""

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable

import httpx

from app.core.config import get_settings

logger = logging.getLogger("acg.tools.browser_use")

_TRANSIENT_STATUS = {408, 425, 429, 500, 502, 503, 504}
_TERMINAL = {"completed", "failed", "cancelled"}
_MAX_ATTEMPTS = 3


class BrowserUseError(Exception):
    pass


class BrowserUseCloud:
    def __init__(self) -> None:
        settings = get_settings()
        self._base = settings.browser_use_base_url.rstrip("/")
        self._api_key = settings.browser_use_api_key
        self._model = settings.browser_use_model
        self._timeout = settings.browser_use_timeout_seconds
        self._client = httpx.AsyncClient(
            base_url=self._base,
            headers={
                "X-Browser-Use-API-Key": self._api_key,
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(60.0, connect=10.0),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def extract_offer(
        self,
        url: str,
        prompt: str | None = None,
        on_started: Callable[[str, str | None], Awaitable[None]] | None = None,
    ) -> tuple[dict | None, str | None]:
        """Render+extract ONE product page.

        Returns (offer_dict | None, run_id | None). The run_id is the Browser
        Use V4 task id — surfaced to callers so they can build a live preview
        URL for the frontend (Fleet PRD B6 live-preview feature).

        `on_started(run_id, preview_url)` fires RIGHT AFTER the run is created
        (before the polling phase) so the caller can surface the live browser
        viewer while the task is still executing — that is what makes it live.
        """
        task = (
            "You are extracting a buyable product offer from ONE product page. "
            f"URL: {url}\n"
            "Read the page (handle login walls, CAPTCHA, or JS-only rendering — "
            "wait for content to load).\n"
            "Return ONLY a single JSON object with exactly these keys:\n"
            '{"title": string, "price_minor": int (price in PAISE, 1 INR = 100 paise), '
            '"currency": string (default "INR"), "available_for_sale": boolean}\n'
            "If no price is available, return exactly: {\"error\": \"no price\"}\n"
            + (f"Additional context: {prompt}\n" if prompt else "")
        )
        run = await self._create_run(task)
        run_id = str(run["id"])
        # The live preview URL is only useful WHILE the run executes, so grab it
        # from the run detail immediately and hand it over before polling.
        preview_url: str | None = None
        try:
            detail = await self._get_run(run_id)
            raw_preview = (
                detail.get("live_preview_url") or detail.get("public_share_url") or run.get("live_preview_url")
            )
            preview_url = str(raw_preview) if raw_preview else None
        except Exception:
            logger.info("browser use: no initial run detail for %s", run_id)
        if on_started is not None:
            try:
                await on_started(run_id, preview_url)
            except Exception:
                logger.info("browser use on_started callback failed", exc_info=True)
        await self._wait_terminal(run_id)
        data = await self._get_run(run_id)
        return self._parse_result(data, url), run_id

    # --- low-level ------------------------------------------------------------

    async def _create_run(self, task: str) -> dict:
        body = {"task": task, "model": self._model}
        last_error = "unknown"
        for attempt in range(_MAX_ATTEMPTS):
            try:
                resp = await self._client.post("/runs", json=body)
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code in _TRANSIENT_STATUS and attempt < _MAX_ATTEMPTS - 1:
                    await asyncio.sleep(_backoff(attempt))
                    continue
                last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
                break
            except httpx.HTTPError as exc:
                last_error = f"network {type(exc).__name__}: {exc}"
                if attempt < _MAX_ATTEMPTS - 1:
                    await asyncio.sleep(_backoff(attempt))
                    continue
                break
        raise BrowserUseError(f"create run failed: {last_error}")

    async def _wait_terminal(self, run_id: str) -> None:
        """Poll cheap /runs/{id}/status until a terminal state or timeout."""
        started = time.monotonic()
        while True:
            if time.monotonic() - started > self._timeout:
                raise BrowserUseError(f"run {run_id} timed out after {self._timeout}s")
            try:
                resp = await self._client.get(f"/runs/{run_id}/status")
                if resp.status_code == 200:
                    status = str(resp.json().get("status") or "running").lower()
                elif resp.status_code in _TRANSIENT_STATUS:
                    await asyncio.sleep(2.0)  # transient: back off, keep polling
                    continue
                else:
                    raise BrowserUseError(f"status HTTP {resp.status_code}: {resp.text[:200]}")
            except httpx.HTTPError as exc:
                logger.info("browser use status poll transient: %s", exc)
                await asyncio.sleep(2.0)
                continue
            if status in _TERMINAL:
                return
            await asyncio.sleep(2.0)

    async def _get_run(self, run_id: str) -> dict:
        resp = await self._client.get(f"/runs/{run_id}")
        if resp.status_code == 200:
            return resp.json()
        raise BrowserUseError(f"get run HTTP {resp.status_code}: {resp.text[:300]}")

    def _parse_result(self, data: dict, url: str) -> dict | None:
        """V4 returns result as a string; parse JSON, tolerating code fences."""
        status = str(data.get("status") or "").lower()
        result = data.get("result")
        if isinstance(result, str) and result.strip():
            raw = result.strip()
            if raw.startswith("```"):  # tolerate markdown code fences
                raw = raw.strip("`")
                if raw.startswith("json"):
                    raw = raw[4:]
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                # Model returned prose: try to salvage the first {...} block.
                start, end = raw.find("{"), raw.rfind("}")
                if start >= 0 and end > start:
                    try:
                        obj = json.loads(raw[start : end + 1])
                    except json.JSONDecodeError:
                        obj = None
                else:
                    obj = None
            if isinstance(obj, dict):
                if obj.get("error") == "no price":
                    logger.info("browser extract: no price on %s", url[:120])
                    return None
                return obj
        if status == "failed":
            logger.warning("browser use run failed: %s", str(data.get("error") or "")[:200])
            return None
        logger.info("browser extract produced no usable result for %s", url[:120])
        return None


def _backoff(attempt: int) -> float:
    return min(1.0 * (2**attempt), 8.0)