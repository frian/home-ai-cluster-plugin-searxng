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

    def transport_handler(request: httpx.Request) -> httpx.Response:
        response = handler(request)
        if response.is_stream_consumed:
            return httpx.Response(
                response.status_code,
                headers=response.headers,
                stream=ChunkedStream([response.content]),
            )
        return response

    def client_factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        observations.append(dict(kwargs))
        return original_client(
            *args, transport=httpx.MockTransport(transport_handler), **kwargs
        )

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
    assert request.headers["accept-encoding"] == "identity"
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


def test_incremental_raw_size_limit_precedes_json_parsing(
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


def test_exact_raw_response_size_limit_is_accepted() -> None:
    body = b"x" * plugin._MAX_DECODED_RESPONSE_BYTES
    response = httpx.Response(200, stream=ChunkedStream([body[:1], body[1:]]))
    assert run(plugin._read_bounded_raw_body(response)) == body


def test_compressed_response_is_rejected_before_body_consumption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnreadableStream(httpx.AsyncByteStream):
        async def __aiter__(self):  # type: ignore[override]
            raise AssertionError("compressed response body must not be consumed")
            yield b""

        async def aclose(self) -> None:
            pass

    install_transport(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            headers={"content-encoding": "gzip"},
            stream=UnreadableStream(),
        ),
    )
    with pytest.raises(plugin.AcquisitionFailure):
        run(plugin.acquire("query"))


def test_gzip_oversized_decoding_regression_is_rejected_without_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decoded = b"{" + (b"x" * (2 * plugin._MAX_DECODED_RESPONSE_BYTES))
    compressed = gzip.compress(decoded)
    assert len(compressed) < plugin._MAX_DECODED_RESPONSE_BYTES

    class UnreadableStream(httpx.AsyncByteStream):
        def __init__(self, body: bytes) -> None:
            self.body = body

        async def __aiter__(self):  # type: ignore[override]
            raise AssertionError("gzip response body must not be consumed")
            yield b""

        async def aclose(self) -> None:
            pass

    install_transport(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            headers={"content-encoding": "gzip"},
            stream=UnreadableStream(compressed),
        ),
    )
    with pytest.raises(plugin.AcquisitionFailure):
        run(plugin.acquire("query"))


@pytest.mark.parametrize(
    "headers",
    [
        {"content-encoding": "deflate"},
        {"content-encoding": "br"},
        {"content-encoding": "unknown"},
        {"content-encoding": "gzip "},
        {"content-encoding": "gzip, identity"},
        {"content-encoding": "identity, gzip"},
        {"content-encoding": "identity, identity"},
        {"content-encoding": "identity\t, gzip"},
        {"content-encoding": "gzip, deflate"},
        [("content-encoding", "identity"), ("content-encoding", "identity")],
    ],
)
def test_unsupported_or_ambiguous_content_encoding_is_rejected(
    monkeypatch: pytest.MonkeyPatch, headers: object
) -> None:
    install_transport(
        monkeypatch,
        lambda request: httpx.Response(200, headers=headers, content=b"{}"),  # type: ignore[arg-type]
    )
    with pytest.raises(plugin.AcquisitionFailure):
        run(plugin.acquire("query"))


@pytest.mark.parametrize(
    "encoding", [None, "identity", "IDENTITY", "identity ", " identity", "identity\t"]
)
def test_absent_or_identity_content_encoding_is_accepted(
    monkeypatch: pytest.MonkeyPatch, encoding: str | None
) -> None:
    headers = {} if encoding is None else {"content-encoding": encoding}
    install_transport(
        monkeypatch,
        lambda request: httpx.Response(
            200, headers=headers, json={"results": [usable_result()]}
        ),
    )
    assert run(plugin.acquire("query")) == [usable_result()]


def test_raw_stream_read_failure_is_a_controlled_acquisition_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingStream(httpx.AsyncByteStream):
        async def __aiter__(self):  # type: ignore[override]
            raise httpx.ReadError("private transport detail")
            yield b""

        async def aclose(self) -> None:
            pass

    install_transport(
        monkeypatch,
        lambda request: httpx.Response(200, stream=FailingStream()),
    )
    with pytest.raises(plugin.AcquisitionFailure) as failure:
        run(plugin.acquire("private query"))
    assert str(failure.value) == "external information acquisition failed"


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


@pytest.mark.parametrize("initial_bom", [b"", b"\xef\xbb\xbf"])
def test_utf8_json_with_or_without_initial_bom_is_accepted(
    monkeypatch: pytest.MonkeyPatch, initial_bom: bytes
) -> None:
    result = usable_result(title="Caf\u00e9", content="na\u00efve \u2615")
    body = (
        initial_bom
        + json.dumps(
            {
                "results": [result],
                "metadata": {"null": None, "flag": True, "score": 1.5},
            },
            ensure_ascii=False,
        ).encode()
    )
    install_transport(monkeypatch, lambda request: httpx.Response(200, content=body))
    assert run(plugin.acquire("query")) == [result]


@pytest.mark.parametrize(
    "body",
    [
        b"\xff\xfe" + json.dumps({"results": [usable_result()]}).encode("utf-16-le"),
        b"\xfe\xff" + json.dumps({"results": [usable_result()]}).encode("utf-16-be"),
        json.dumps({"results": [usable_result()]}).encode("utf-16-le"),
        b"\xff\xfe\x00\x00"
        + json.dumps({"results": [usable_result()]}).encode("utf-32-le"),
        json.dumps({"results": [usable_result()]}).encode("utf-32-le"),
        b'{"results":[{"title":"\xff","url":"https://example.invalid/",'
        b'"content":"content"}]}',
    ],
)
def test_unsupported_or_malformed_response_encodings_fail(
    monkeypatch: pytest.MonkeyPatch, body: bytes
) -> None:
    install_transport(monkeypatch, lambda request: httpx.Response(200, content=body))
    with pytest.raises(plugin.AcquisitionFailure):
        run(plugin.acquire("query"))


def test_response_decoding_failure_is_privacy_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query = "private query"
    sensitive_url = "https://sensitive.invalid/path"
    sensitive_content = "private response"
    body = (
        b'{"results":[{"title":"\xff","url":"'
        + sensitive_url.encode()
        + b'","content":"'
        + sensitive_content.encode()
        + b'","metadata":{"private":"value"}}]}'
    )
    install_transport(monkeypatch, lambda request: httpx.Response(200, content=body))
    with pytest.raises(plugin.AcquisitionFailure) as failure:
        run(plugin.acquire(query))
    message = str(failure.value)
    assert message == "external information acquisition failed"
    assert query not in message
    assert sensitive_url not in message
    assert sensitive_content not in message
    assert "UnicodeDecodeError" not in message


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
@pytest.mark.parametrize(
    ("location", "body_prefix", "body_suffix"),
    [
        (
            "additional result attribute",
            '{"results":[{"title":"title","url":"https://example.invalid/",'
            '"content":"content","score":',
            "}]}",
        ),
        (
            "top-level metadata",
            '{"results":[{"title":"title","url":"https://example.invalid/",'
            '"content":"content"}],"metadata":{"score":',
            "}}",
        ),
        (
            "nested ignored metadata",
            '{"results":[{"title":"title","url":"https://example.invalid/",'
            '"content":"content"}],"metadata":{"nested":{"score":',
            "}}}",
        ),
    ],
)
def test_non_standard_json_constants_in_ignored_content_fail(
    monkeypatch: pytest.MonkeyPatch,
    constant: str,
    location: str,
    body_prefix: str,
    body_suffix: str,
) -> None:
    body = (body_prefix + constant + body_suffix).encode()
    install_transport(monkeypatch, lambda request: httpx.Response(200, content=body))
    with pytest.raises(plugin.AcquisitionFailure):
        run(plugin.acquire("query"))


def test_valid_json_literals_and_values_are_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = b"""{
        "results": [{
            "title": "NaN",
            "url": "https://example.invalid/Infinity",
            "content": "-Infinity",
            "score": 1.5
        }],
        "metadata": {"null": null, "true": true, "false": false, "number": 2}
    }"""
    install_transport(monkeypatch, lambda request: httpx.Response(200, content=body))
    assert run(plugin.acquire("query")) == [
        {
            "title": "NaN",
            "url": "https://example.invalid/Infinity",
            "content": "-Infinity",
        }
    ]


def test_non_standard_json_constant_failure_is_privacy_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query = "private query"
    sensitive_url = "https://sensitive.invalid/path"
    sensitive_content = "private response"
    body = (
        '{"results":[{"title":"title","url":"'
        + sensitive_url
        + '","content":"'
        + sensitive_content
        + '","score":NaN}]}'
    ).encode()
    install_transport(monkeypatch, lambda request: httpx.Response(200, content=body))
    with pytest.raises(plugin.AcquisitionFailure) as failure:
        run(plugin.acquire(query))
    message = str(failure.value)
    assert message == "external information acquisition failed"
    assert query not in message
    assert sensitive_url not in message
    assert sensitive_content not in message


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
