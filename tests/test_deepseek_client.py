import json
import math
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from vla_wam_daily.deepseek_client import (
    DeepSeekClient,
    DeepSeekResponseError,
    RetryableDeepSeekError,
)

OMIT_USAGE = object()
DEFAULT_USAGE = {
    "prompt_tokens": 2,
    "completion_tokens": 1,
    "total_tokens": 3,
}


def assert_exception_graph_is_secret_safe(
    error: BaseException,
    *,
    secrets: tuple[str, ...],
) -> None:
    pending: list[object] = [error]
    seen: set[int] = set()
    lowered_secrets = tuple(secret.casefold() for secret in secrets)

    while pending:
        value = pending.pop()
        if id(value) in seen:
            continue
        seen.add(id(value))

        assert not isinstance(value, (httpx.Request, httpx.Response, httpx.Headers))
        if isinstance(value, str):
            lowered = value.casefold()
            assert "authorization" not in lowered
            assert all(secret not in lowered for secret in lowered_secrets)
            continue
        if isinstance(value, bytes):
            lowered = value.decode(errors="ignore").casefold()
            assert "authorization" not in lowered
            assert all(secret not in lowered for secret in lowered_secrets)
            continue
        if isinstance(value, BaseException):
            if value.__cause__ is not None:
                pending.append(value.__cause__)
            if value.__context__ is not None:
                pending.append(value.__context__)
        if isinstance(value, dict):
            pending.extend(value.keys())
            pending.extend(value.values())
            continue
        if isinstance(value, (list, tuple, set, frozenset)):
            pending.extend(value)
            continue
        with suppress(TypeError):
            pending.extend(vars(value).values())


def test_client_requests_json_output_and_collects_usage() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": (
                                '{"title_zh":"中文标题",'
                                '"analysis":{"relevance_score":8}}'
                            )
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "total_tokens": 120,
                },
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = DeepSeekClient(
            api_key="test-key",
            model="deepseek-v4-pro",
            retries=1,
            http_client=http_client,
        )
        payload, usage = client.analyze(
            system_prompt="Return JSON.",
            paper_json='{"title":"x"}',
        )

    assert len(captured) == 1
    request = captured[0]
    assert request.url == "https://api.deepseek.com/chat/completions"
    assert request.headers["authorization"] == "Bearer test-key"
    assert json.loads(request.content) == {
        "model": "deepseek-v4-pro",
        "messages": [
            {"role": "system", "content": "Return JSON."},
            {"role": "user", "content": '{"title":"x"}'},
        ],
        "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"},
        "max_tokens": 1800,
        "stream": False,
    }
    assert payload["title_zh"] == "中文标题"
    assert usage.total_tokens == 120


def test_empty_content_is_an_error() -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": ""},
                    }
                ]
            },
        )
    )
    with httpx.Client(transport=transport) as http_client:
        client = DeepSeekClient(
            api_key="test-key",
            model="deepseek-v4-pro",
            retries=1,
            http_client=http_client,
        )
        with pytest.raises(DeepSeekResponseError, match="empty"):
            client.analyze(
                system_prompt="Return JSON.",
                paper_json='{"title":"x"}',
            )


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"api_key": 123}, "api_key"),
        ({"api_key": ""}, "api_key"),
        ({"api_key": " secret"}, "api_key"),
        ({"api_key": "secret\nleak"}, "api_key"),
        ({"model": 123}, "model"),
        ({"model": ""}, "model"),
        ({"model": " deepseek-v4-pro"}, "model"),
        ({"max_output_tokens": 0}, "max_output_tokens"),
        ({"max_output_tokens": True}, "max_output_tokens"),
        ({"retries": 0}, "retries"),
        ({"retries": True}, "retries"),
        ({"timeout": 0}, "timeout"),
        ({"timeout": True}, "timeout"),
        ({"timeout": math.inf}, "timeout"),
        ({"retry_wait": -1}, "retry_wait"),
        ({"retry_wait": True}, "retry_wait"),
        ({"retry_wait": math.nan}, "retry_wait"),
        ({"max_retry_delay": 0}, "max_retry_delay"),
        ({"max_retry_delay": True}, "max_retry_delay"),
        ({"max_retry_delay": math.inf}, "max_retry_delay"),
        ({"max_response_bytes": 0}, "max_response_bytes"),
        ({"max_response_bytes": True}, "max_response_bytes"),
        ({"http_client": object()}, "http_client"),
        ({"sleep": object()}, "sleep"),
        ({"wall_clock": object()}, "wall_clock"),
    ],
)
def test_constructor_rejects_invalid_parameters(
    overrides: dict[str, object],
    match: str,
) -> None:
    arguments: dict[str, object] = {
        "api_key": "test-key",
        "model": "deepseek-v4-pro",
    }
    arguments.update(overrides)

    with pytest.raises((TypeError, ValueError), match=match):
        DeepSeekClient(**arguments)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("system_prompt", "paper_json", "match"),
    [
        ("", '{"title":"x"}', "system_prompt"),
        ("  ", '{"title":"x"}', "system_prompt"),
        ("Return JSON.", "", "paper_json"),
        ("Return JSON.", "not-json", "valid JSON"),
        ("Return JSON.", "[]", "object"),
        ("Return JSON.", "null", "object"),
    ],
)
def test_analyze_rejects_invalid_inputs_before_request(
    system_prompt: str,
    paper_json: str,
    match: str,
) -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = DeepSeekClient(
            api_key="test-key",
            model="deepseek-v4-pro",
            http_client=http_client,
        )
        with pytest.raises(ValueError, match=match):
            client.analyze(system_prompt=system_prompt, paper_json=paper_json)

    assert not called


def completion_response(
    *,
    content: object = '{"title_zh":"中文标题"}',
    finish_reason: object = "stop",
    usage: object = DEFAULT_USAGE,
) -> httpx.Response:
    payload: dict[str, object] = {
        "choices": [
            {
                "finish_reason": finish_reason,
                "message": {"content": content},
            }
        ],
    }
    if usage is not OMIT_USAGE:
        payload["usage"] = usage
    return httpx.Response(
        200,
        headers={"content-type": "application/json; charset=utf-8"},
        json=payload,
    )


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        ([], "root"),
        ({}, "choices"),
        ({"choices": "wrong"}, "choices"),
        ({"choices": []}, "choices"),
        ({"choices": [None]}, "choice"),
        ({"choices": [{}]}, "message"),
        (
            {"choices": [{"finish_reason": "stop", "message": None}]},
            "message",
        ),
        (
            {"choices": [{"finish_reason": "stop", "message": {}}]},
            "content",
        ),
    ],
)
def test_malformed_response_shape_is_rejected(
    payload: object,
    match: str,
) -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json=payload,
        )
    )
    with httpx.Client(transport=transport) as http_client:
        client = DeepSeekClient(
            api_key="test-key",
            model="deepseek-v4-pro",
            retries=1,
            http_client=http_client,
        )
        with pytest.raises(DeepSeekResponseError, match=match):
            client.analyze(
                system_prompt="Return JSON.",
                paper_json='{"title":"x"}',
            )


@pytest.mark.parametrize(
    ("content", "match"),
    [
        ("not-json", "invalid JSON"),
        ("[]", "root must be an object"),
        (None, "content"),
    ],
)
def test_invalid_message_content_is_rejected(content: object, match: str) -> None:
    transport = httpx.MockTransport(lambda _request: completion_response(content=content))
    with httpx.Client(transport=transport) as http_client:
        client = DeepSeekClient(
            api_key="test-key",
            model="deepseek-v4-pro",
            retries=1,
            http_client=http_client,
        )
        with pytest.raises(DeepSeekResponseError, match=match):
            client.analyze(
                system_prompt="Return JSON.",
                paper_json='{"title":"x"}',
            )


def test_missing_finish_reason_is_accepted() -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"title_zh":"中文标题"}',
                        }
                    }
                ],
                "usage": DEFAULT_USAGE,
            },
        )
    )
    with httpx.Client(transport=transport) as http_client:
        client = DeepSeekClient(
            api_key="test-key",
            model="deepseek-v4-pro",
            retries=1,
            http_client=http_client,
        )
        payload, usage = client.analyze(
            system_prompt="Return JSON.",
            paper_json='{"title":"x"}',
        )

    assert payload == {"title_zh": "中文标题"}
    assert usage.total_tokens == 3


@pytest.mark.parametrize(
    "finish_reason",
    [
        "length",
        "content_filter",
        "tool_calls",
        "insufficient_system_resource",
        None,
    ],
)
def test_non_stop_finish_reason_is_rejected(finish_reason: object) -> None:
    transport = httpx.MockTransport(
        lambda _request: completion_response(finish_reason=finish_reason)
    )
    with httpx.Client(transport=transport) as http_client:
        client = DeepSeekClient(
            api_key="test-key",
            model="deepseek-v4-pro",
            retries=1,
            http_client=http_client,
        )
        with pytest.raises(DeepSeekResponseError, match="finish_reason"):
            client.analyze(
                system_prompt="Return JSON.",
                paper_json='{"title":"x"}',
            )


@pytest.mark.parametrize(
    "usage",
    [
        None,
        [],
        "wrong",
        {},
        {"prompt_tokens": 1, "completion_tokens": 2},
        {"prompt_tokens": 1, "total_tokens": 3},
        {"completion_tokens": 2, "total_tokens": 3},
        {"prompt_tokens": -1},
        {"completion_tokens": "20"},
        {"total_tokens": True},
        {"unexpected": 1},
    ],
)
def test_invalid_usage_is_rejected(usage: object) -> None:
    transport = httpx.MockTransport(lambda _request: completion_response(usage=usage))
    with httpx.Client(transport=transport) as http_client:
        client = DeepSeekClient(
            api_key="test-key",
            model="deepseek-v4-pro",
            retries=1,
            http_client=http_client,
        )
        with pytest.raises(DeepSeekResponseError, match="usage"):
            client.analyze(
                system_prompt="Return JSON.",
                paper_json='{"title":"x"}',
            )


def test_missing_usage_is_rejected() -> None:
    transport = httpx.MockTransport(
        lambda _request: completion_response(usage=OMIT_USAGE)
    )
    with httpx.Client(transport=transport) as http_client:
        client = DeepSeekClient(
            api_key="test-key",
            model="deepseek-v4-pro",
            retries=1,
            http_client=http_client,
        )
        with pytest.raises(DeepSeekResponseError, match="usage"):
            client.analyze(
                system_prompt="Return JSON.",
                paper_json='{"title":"x"}',
            )


def test_usage_total_must_equal_prompt_plus_completion() -> None:
    transport = httpx.MockTransport(
        lambda _request: completion_response(
            usage={
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 999,
            }
        )
    )
    with httpx.Client(transport=transport) as http_client:
        client = DeepSeekClient(
            api_key="test-key",
            model="deepseek-v4-pro",
            retries=1,
            http_client=http_client,
        )
        with pytest.raises(DeepSeekResponseError, match="usage"):
            client.analyze(
                system_prompt="Return JSON.",
                paper_json='{"title":"x"}',
            )


@pytest.mark.parametrize(
    ("cache_hit", "cache_miss"),
    [
        (-1, 101),
        ("80", 20),
        (80, True),
        (80, 19),
    ],
)
def test_usage_cache_counts_must_be_strict_nonnegative_and_sum_to_prompt(
    cache_hit: object,
    cache_miss: object,
) -> None:
    transport = httpx.MockTransport(
        lambda _request: completion_response(
            usage={
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
                "prompt_cache_hit_tokens": cache_hit,
                "prompt_cache_miss_tokens": cache_miss,
            }
        )
    )
    with httpx.Client(transport=transport) as http_client:
        client = DeepSeekClient(
            api_key="test-key",
            model="deepseek-v4-pro",
            retries=1,
            http_client=http_client,
        )
        with pytest.raises(DeepSeekResponseError, match="usage"):
            client.analyze(
                system_prompt="Return JSON.",
                paper_json='{"title":"x"}',
            )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("prompt_cache_hit_tokens", 100),
        ("prompt_cache_miss_tokens", 100),
    ],
)
def test_usage_cache_count_fields_must_appear_together(field: str, value: int) -> None:
    usage = {
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "total_tokens": 120,
        field: value,
    }
    transport = httpx.MockTransport(
        lambda _request: completion_response(usage=usage)
    )
    with httpx.Client(transport=transport) as http_client:
        client = DeepSeekClient(
            api_key="test-key",
            model="deepseek-v4-pro",
            retries=1,
            http_client=http_client,
        )
        with pytest.raises(DeepSeekResponseError, match="usage"):
            client.analyze(
                system_prompt="Return JSON.",
                paper_json='{"title":"x"}',
            )


def test_official_usage_extensions_are_ignored() -> None:
    transport = httpx.MockTransport(
        lambda _request: completion_response(
            usage={
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
                "prompt_cache_hit_tokens": 80,
                "prompt_cache_miss_tokens": 20,
                "completion_tokens_details": {"reasoning_tokens": 5},
            }
        )
    )
    with httpx.Client(transport=transport) as http_client:
        client = DeepSeekClient(
            api_key="test-key",
            model="deepseek-v4-pro",
            retries=1,
            http_client=http_client,
        )
        _payload, usage = client.analyze(
            system_prompt="Return JSON.",
            paper_json='{"title":"x"}',
        )

    assert usage.prompt_tokens == 100
    assert usage.completion_tokens == 20
    assert usage.total_tokens == 120


@pytest.mark.parametrize("content_type", ["text/html", "application/problem+json", ""])
def test_non_json_response_content_type_is_rejected(content_type: str) -> None:
    headers = {"content-type": content_type} if content_type else {}
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            headers=headers,
            content=b'{"choices":[]}',
        )
    )
    with httpx.Client(transport=transport) as http_client:
        client = DeepSeekClient(
            api_key="test-key",
            model="deepseek-v4-pro",
            retries=1,
            http_client=http_client,
        )
        with pytest.raises(DeepSeekResponseError, match="content type"):
            client.analyze(
                system_prompt="Return JSON.",
                paper_json='{"title":"x"}',
            )


class ChunkedStream(httpx.SyncByteStream):
    def __iter__(self) -> object:
        yield b'{"choices":'
        yield b'"this chunk makes the response too large"}'


@pytest.mark.parametrize("declared_length", [True, False])
def test_oversized_response_is_rejected(declared_length: bool) -> None:
    body = b'{"choices":"too large"}'

    def handler(_request: httpx.Request) -> httpx.Response:
        if declared_length:
            return httpx.Response(
                200,
                headers={
                    "content-type": "application/json",
                    "content-length": str(len(body)),
                },
                content=body,
            )
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            stream=ChunkedStream(),
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = DeepSeekClient(
            api_key="test-key",
            model="deepseek-v4-pro",
            retries=1,
            max_response_bytes=16,
            http_client=http_client,
        )
        with pytest.raises(DeepSeekResponseError, match="too large"):
            client.analyze(
                system_prompt="Return JSON.",
                paper_json='{"title":"x"}',
            )


def test_invalid_response_body_is_not_exposed_in_exception() -> None:
    secret_body = "server-secret-marker"
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=secret_body.encode(),
        )
    )
    with httpx.Client(transport=transport) as http_client:
        client = DeepSeekClient(
            api_key="test-key",
            model="deepseek-v4-pro",
            retries=1,
            http_client=http_client,
        )
        with pytest.raises(DeepSeekResponseError) as exc_info:
            client.analyze(
                system_prompt="Return JSON.",
                paper_json='{"title":"x"}',
            )

    assert secret_body not in str(exc_info.value)


@pytest.mark.parametrize("status_code", [429, 500, 503])
def test_retryable_http_status_is_retried(status_code: int) -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(status_code, content=b"private server details")
        return completion_response()

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = DeepSeekClient(
            api_key="test-key",
            model="deepseek-v4-pro",
            retries=2,
            retry_wait=0,
            http_client=http_client,
        )
        payload, _usage = client.analyze(
            system_prompt="Return JSON.",
            paper_json='{"title":"x"}',
        )

    assert attempts == 2
    assert payload["title_zh"] == "中文标题"


def test_transport_error_is_retried() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("network unavailable", request=request)
        return completion_response()

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = DeepSeekClient(
            api_key="test-key",
            model="deepseek-v4-pro",
            retries=2,
            retry_wait=0,
            http_client=http_client,
        )
        client.analyze(
            system_prompt="Return JSON.",
            paper_json='{"title":"x"}',
        )

    assert attempts == 2


@pytest.mark.parametrize("status_code", [302, 400, 401])
def test_redirect_and_client_error_are_not_retried(status_code: int) -> None:
    attempts = 0
    api_key = "super-secret-status-key"
    response_body = "private-status-body"

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            status_code,
            headers={"location": "https://example.com"} if status_code == 302 else {},
            content=response_body.encode(),
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = DeepSeekClient(
            api_key=api_key,
            model="deepseek-v4-pro",
            retries=3,
            retry_wait=0,
            http_client=http_client,
        )
        with pytest.raises(
            DeepSeekResponseError,
            match=rf"^DeepSeek returned HTTP {status_code}$",
        ) as exc_info:
            client.analyze(
                system_prompt="Return JSON.",
                paper_json='{"title":"x"}',
            )

    assert attempts == 1
    assert_exception_graph_is_secret_safe(
        exc_info.value,
        secrets=(api_key, response_body),
    )


def test_retry_uses_exponential_backoff() -> None:
    attempts = 0
    sleeps: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(503)
        return completion_response()

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = DeepSeekClient(
            api_key="test-key",
            model="deepseek-v4-pro",
            retries=3,
            retry_wait=0.5,
            http_client=http_client,
            sleep=sleeps.append,
        )
        client.analyze(
            system_prompt="Return JSON.",
            paper_json='{"title":"x"}',
        )

    assert sleeps == [0.5, 1.0]


@pytest.mark.parametrize(
    ("retry_after", "expected_delay"),
    [
        ("3", 3.0),
        ("120", 120.0),
        ("400", 300.0),
        ("2.5", 0.5),
        ("1e308", 0.5),
        ("9" * 1000, 0.5),
        ("invalid", 0.5),
        ("-2", 0.5),
    ],
)
def test_retry_after_delta_seconds_or_fallback(
    retry_after: str,
    expected_delay: float,
) -> None:
    attempts = 0
    sleeps: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"retry-after": retry_after})
        return completion_response()

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = DeepSeekClient(
            api_key="test-key",
            model="deepseek-v4-pro",
            retries=2,
            retry_wait=0.5,
            http_client=http_client,
            sleep=sleeps.append,
        )
        client.analyze(
            system_prompt="Return JSON.",
            paper_json='{"title":"x"}',
        )

    assert sleeps == [expected_delay]


@pytest.mark.parametrize(
    ("offset_seconds", "expected_delay"),
    [
        (120, 120.0),
        (600, 300.0),
        (-10, 0.5),
    ],
)
def test_retry_after_http_date_uses_wall_clock_and_cap(
    offset_seconds: int,
    expected_delay: float,
) -> None:
    now = datetime(2026, 7, 30, 2, 0, tzinfo=UTC)
    retry_after = format_datetime(
        now + timedelta(seconds=offset_seconds),
        usegmt=True,
    )
    attempts = 0
    sleeps: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"retry-after": retry_after})
        return completion_response()

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = DeepSeekClient(
            api_key="test-key",
            model="deepseek-v4-pro",
            retries=2,
            retry_wait=0.5,
            http_client=http_client,
            sleep=sleeps.append,
            wall_clock=lambda: now,
        )
        client.analyze(
            system_prompt="Return JSON.",
            paper_json='{"title":"x"}',
        )

    assert sleeps == [expected_delay]


def test_exponential_backoff_is_clamped() -> None:
    attempts = 0
    sleeps: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(503)
        return completion_response()

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = DeepSeekClient(
            api_key="test-key",
            model="deepseek-v4-pro",
            retries=3,
            retry_wait=250,
            max_retry_delay=300,
            http_client=http_client,
            sleep=sleeps.append,
        )
        client.analyze(
            system_prompt="Return JSON.",
            paper_json='{"title":"x"}',
        )

    assert sleeps == [250.0, 300.0]


def test_retry_exhaustion_does_not_expose_body_or_api_key() -> None:
    secret_body = "private-upstream-body"
    api_key = "super-secret-api-key"
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(503, content=secret_body.encode())
    )
    with httpx.Client(transport=transport) as http_client:
        client = DeepSeekClient(
            api_key=api_key,
            model="deepseek-v4-pro",
            retries=2,
            retry_wait=0,
            http_client=http_client,
        )
        with pytest.raises(RetryableDeepSeekError) as exc_info:
            client.analyze(
                system_prompt="Return JSON.",
                paper_json='{"title":"x"}',
            )

    error_text = str(exc_info.value)
    assert secret_body not in error_text
    assert api_key not in error_text
    assert_exception_graph_is_secret_safe(
        exc_info.value,
        secrets=(api_key, secret_body),
    )


def test_transport_exhaustion_has_no_secret_bearing_exception_chain() -> None:
    api_key = "super-secret-transport-key"
    transport_detail = "private-transport-detail"

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(transport_detail, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = DeepSeekClient(
            api_key=api_key,
            model="deepseek-v4-pro",
            retries=1,
            retry_wait=0,
            http_client=http_client,
        )
        with pytest.raises(RetryableDeepSeekError) as exc_info:
            client.analyze(
                system_prompt="Return JSON.",
                paper_json='{"title":"x"}',
            )

    assert "ConnectError" in str(exc_info.value)
    assert_exception_graph_is_secret_safe(
        exc_info.value,
        secrets=(api_key, transport_detail),
    )


def test_decoding_error_is_retried_without_secret_bearing_exception_chain() -> None:
    api_key = "super-secret-decoding-key"
    response_body = "private-corrupt-gzip-body"
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "content-encoding": "gzip",
            },
            content=response_body.encode(),
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = DeepSeekClient(
            api_key=api_key,
            model="deepseek-v4-pro",
            retries=2,
            retry_wait=0,
            http_client=http_client,
        )
        with pytest.raises(RetryableDeepSeekError, match="DecodingError") as exc_info:
            client.analyze(
                system_prompt="Return JSON.",
                paper_json='{"title":"x"}',
            )

    assert attempts == 2
    assert_exception_graph_is_secret_safe(
        exc_info.value,
        secrets=(api_key, response_body),
    )


def test_response_validation_error_is_not_retried_as_request_error() -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=b"not-json",
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = DeepSeekClient(
            api_key="test-key",
            model="deepseek-v4-pro",
            retries=3,
            retry_wait=0,
            http_client=http_client,
        )
        with pytest.raises(DeepSeekResponseError, match="content type"):
            client.analyze(
                system_prompt="Return JSON.",
                paper_json='{"title":"x"}',
            )

    assert attempts == 1


@pytest.mark.parametrize("content_length", ["invalid", "-1"])
def test_invalid_content_length_is_rejected(content_length: str) -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "content-length": content_length,
            },
            content=b"{}",
        )
    )
    with httpx.Client(transport=transport) as http_client:
        client = DeepSeekClient(
            api_key="test-key",
            model="deepseek-v4-pro",
            retries=1,
            http_client=http_client,
        )
        with pytest.raises(DeepSeekResponseError, match="content length"):
            client.analyze(
                system_prompt="Return JSON.",
                paper_json='{"title":"x"}',
            )


def test_injected_http_client_is_not_closed() -> None:
    external = httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(400))
    )
    client = DeepSeekClient(
        api_key="test-key",
        model="deepseek-v4-pro",
        http_client=external,
    )

    client.close()

    assert not external.is_closed
    external.close()


def test_owned_http_client_is_closed_explicitly_and_by_context_manager() -> None:
    explicit = DeepSeekClient(api_key="test-key", model="deepseek-v4-pro")
    explicit_http = explicit.http
    explicit.close()
    assert explicit_http.is_closed

    with DeepSeekClient(api_key="test-key", model="deepseek-v4-pro") as managed:
        managed_http = managed.http
        assert not managed_http.is_closed
    assert managed_http.is_closed
