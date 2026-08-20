"""RFC-0079 fixed-loopback SearXNG acquisition implementation."""

import asyncio
import json

import httpx

_ENDPOINT = "http://127.0.0.1:8888/search"
_TOTAL_OPERATION_DEADLINE_SECONDS = 30.0
_MAX_DECODED_RESPONSE_BYTES = 1 * 1024 * 1024
_TIMEOUT = httpx.Timeout(connect=2.0, read=20.0, write=5.0, pool=2.0)
_LIMITS = httpx.Limits(max_connections=1, max_keepalive_connections=0)


class AcquisitionFailure(Exception):
    """A privacy-safe provider acquisition failure."""

    def __init__(self) -> None:
        super().__init__("external information acquisition failed")


async def acquire(query: str) -> list[dict[str, str]]:
    """Acquire at most five usable SearXNG JSON results for an exact query."""
    try:
        async with asyncio.timeout(_TOTAL_OPERATION_DEADLINE_SECONDS):
            async with httpx.AsyncClient(
                timeout=_TIMEOUT,
                limits=_LIMITS,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                async with client.stream(
                    "POST",
                    _ENDPOINT,
                    data={"q": query, "format": "json"},
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                ) as response:
                    if response.status_code != httpx.codes.OK:
                        raise AcquisitionFailure()
                    response_bytes = await _read_bounded_decoded_body(response)

        return _normalise_results(json.loads(response_bytes))
    except (
        AcquisitionFailure,
        httpx.HTTPError,
        TimeoutError,
        UnicodeError,
        json.JSONDecodeError,
    ):
        raise AcquisitionFailure() from None


async def _read_bounded_decoded_body(response: httpx.Response) -> bytes:
    """Read decoded HTTPX stream data without exceeding the RFC byte bound."""
    accumulated = bytearray()
    async for chunk in response.aiter_bytes():
        if len(accumulated) + len(chunk) > _MAX_DECODED_RESPONSE_BYTES:
            raise AcquisitionFailure()
        accumulated.extend(chunk)
    return bytes(accumulated)


def _normalise_results(payload: object) -> list[dict[str, str]]:
    """Return the closed, ordered RFC-0078 candidate representation."""
    if not isinstance(payload, dict):
        raise AcquisitionFailure()

    results = payload.get("results")
    if not isinstance(results, list):
        raise AcquisitionFailure()

    candidates: list[dict[str, str]] = []
    for item in results:
        if not isinstance(item, dict):
            continue

        title = item.get("title")
        url = item.get("url")
        content = item.get("content")
        values = (title, url, content)
        if not all(isinstance(value, str) and value.strip() for value in values):
            continue

        candidates.append({"title": title, "url": url, "content": content})
        if len(candidates) == 5:
            break

    if not candidates:
        raise AcquisitionFailure()
    return candidates
