"""Focused RFC-0079 contract tests using only in-process HTTPX transports."""

import asyncio
import gzip
import importlib.metadata
import json
import subprocess
import sys
from collections.abc import Callable

import httpx
import pytest

from home_ai_cluster_plugin_searxng import plugin


def run(coroutine: object) -> object:
    return asyncio.run(coroutine)  # type: ignore[arg-type]


def install_transport(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> list[dict[str, object]]:
    observations: list[dict[str, object]] = []
    original_client = httpx.AsyncClient

    def client_factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        observations.append(dict(kwargs))
        return original_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(plugin.httpx, "AsyncClient", client_factory)
    return observations


def usable_result(index: int = 1, **extra: object) -> dict[str, object]:
    return {
        "title": f"title-{index}",
        "url": f"https://example.invalid/{index}",
        "content": f"content-{index}",
        **extra,
    }


def test_entry_point_metadata_exposes_exact_async_callable() -> None:
    entries = importlib.metadata.entry_points().select(
        group="home_ai_cluster.external_information_acquisition.v1", name="searxng"
    )
    assert len(entries) == 1
    entry = next(iter(entries))
    assert entry.value == "home_ai_cluster_plugin_searxng.plugin:acquire"
    assert asyncio.iscoroutinefunction(entry.load())


def test_package_import_has_no_network_side_effect() -> None:
    code = """
import socket
socket.socket.connect = lambda *args: (_ for _ in ()).throw(AssertionError())
import home_ai_cluster_plugin_searxng
"""
    completed = subprocess.run([sys.executable, "-c", code], check=False)
    assert completed.returncode == 0


def test_exact_single_form_post_preserves_query_and_client_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"results": [usable_result()]})

    observations = install_transport(monkeypatch, handler)
    query = "  !images exact +syntax  "
    assert run(plugin.acquire(query)) == [usable_result()]
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == "http://127.0.0.1:8888/search"
    assert request.headers["content-type"] == "application/x-www-form-urlencoded"
    assert request.content == b"q=++%21images+exact+%2Bsyntax++&format=json"
    assert observations == [
        {
            "timeout": plugin._TIMEOUT,
            "limits": plugin._LIMITS,
            "follow_redirects": False,
            "trust_env": False,
        }
    ]
    assert plugin._TIMEOUT.connect == 2.0
    assert plugin._TIMEOUT.read == 20.0
    assert plugin._TIMEOUT.write == 5.0
    assert plugin._TIMEOUT.pool == 2.0
    assert plugin._LIMITS.max_connections == 1
    assert plugin._LIMITS.max_keepalive_connections == 0


def test_each_operation_creates_a_fresh_client(monkeypatch: pytest.MonkeyPatch) -> None:
    observations = install_transport(
        monkeypatch,
        lambda request: httpx.Response(200, json={"results": [usable_result()]}),
    )
    run(plugin.acquire("first"))
    run(plugin.acquire("second"))
    assert len(observations) == 2


def test_redirect_is_not_followed_or_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"location": "https://example.invalid/"})

    install_transport(monkeypatch, handler)
    with pytest.raises(plugin.AcquisitionFailure):
        run(plugin.acquire("query"))
    assert len(requests) == 1


@pytest.mark.parametrize("status", [199, 201, 403, 500])
def test_only_http_200_is_accepted(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    install_transport(monkeypatch, lambda request: httpx.Response(status))
    with pytest.raises(plugin.AcquisitionFailure):
        run(plugin.acquire("query"))


def test_total_deadline_is_independent_and_test_controllable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def slow_handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.02)
        return httpx.Response(200, json={"results": [usable_result()]})

    original_client = httpx.AsyncClient
    monkeypatch.setattr(plugin, "_TOTAL_OPERATION_DEADLINE_SECONDS", 0.001)
    monkeypatch.setattr(
        plugin.httpx,
        "AsyncClient",
        lambda *args, **kwargs: original_client(
            *args, transport=httpx.MockTransport(slow_handler), **kwargs
        ),
    )
    with pytest.raises(plugin.AcquisitionFailure):
        run(plugin.acquire("query"))


class ChunkedStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    async def __aiter__(self):  # type: ignore[override]
        for chunk in self.chunks:
            yield chunk

    async def aclose(self) -> None:
        pass


def test_incremental_decoded_size_limit_precedes_json_parsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def parsing_must_not_run(_: object) -> object:
        raise AssertionError("JSON parsing must not run for an oversized response")

    monkeypatch.setattr(plugin.json, "loads", parsing_must_not_run)
    install_transport(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            stream=ChunkedStream([b"{", b"x" * plugin._MAX_DECODED_RESPONSE_BYTES]),
        ),
    )
    with pytest.raises(plugin.AcquisitionFailure):
        run(plugin.acquire("secret query"))


def test_compressed_response_limit_uses_decoded_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decoded = b"{" + (b"x" * plugin._MAX_DECODED_RESPONSE_BYTES)
    compressed = gzip.compress(decoded)
    assert len(compressed) < plugin._MAX_DECODED_RESPONSE_BYTES
    install_transport(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            headers={"content-encoding": "gzip"},
            content=compressed,
        ),
    )
    with pytest.raises(plugin.AcquisitionFailure):
        run(plugin.acquire("query"))


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        json.dumps([usable_result()]).encode(),
        b"{}",
        b'{"results": {}}',
    ],
)
def test_invalid_json_or_structure_fails(
    monkeypatch: pytest.MonkeyPatch, body: bytes
) -> None:
    install_transport(monkeypatch, lambda request: httpx.Response(200, content=body))
    with pytest.raises(plugin.AcquisitionFailure):
        run(plugin.acquire("query"))


def test_normalisation_skips_malformed_preserves_values_and_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preserved = {
        "title": " title ",
        "url": " https://example.invalid/a ",
        "content": " body ",
    }
    payload = {
        "results": [
            None,
            {"title": "", "url": "url", "content": "content"},
            {"title": "title", "url": 1, "content": "content"},
            preserved,
            usable_result(2, engine="ignored", score=100),
            usable_result(2),
        ],
        "answers": ["ignored"],
    }
    install_transport(monkeypatch, lambda request: httpx.Response(200, json=payload))
    assert run(plugin.acquire("query")) == [
        preserved,
        usable_result(2),
        usable_result(2),
    ]


def test_stops_after_first_five_usable_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results = [usable_result(index) for index in range(1, 7)]
    install_transport(
        monkeypatch, lambda request: httpx.Response(200, json={"results": results})
    )
    assert run(plugin.acquire("query")) == results[:5]


def test_large_field_is_preserved_without_url_traffic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    large_content = "x" * 100_000
    result = usable_result(content=large_content)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"results": [result]})

    install_transport(monkeypatch, handler)
    assert run(plugin.acquire("query")) == [result]
    assert len(requests) == 1
    assert requests[0].url == httpx.URL("http://127.0.0.1:8888/search")


def test_zero_usable_candidates_and_failures_do_not_expose_sensitive_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query = "private query"
    sensitive_url = "https://sensitive.invalid/path"
    sensitive_content = "private response"
    install_transport(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "",
                        "url": sensitive_url,
                        "content": sensitive_content,
                    }
                ]
            },
        ),
    )
    with pytest.raises(plugin.AcquisitionFailure) as failure:
        run(plugin.acquire(query))
    message = str(failure.value)
    assert query not in message
    assert sensitive_url not in message
    assert sensitive_content not in message
