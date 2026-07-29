import json
import math

import httpx
import pytest

from vla_wam_daily.deepseek_client import (
    DeepSeekClient,
    DeepSeekResponseError,
    RetryableDeepSeekError,
)


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
        ({"max_response_bytes": 0}, "max_response_bytes"),
        ({"max_response_bytes": True}, "max_response_bytes"),
        ({"http_client": object()}, "http_client"),
        ({"sleep": object()}, "sleep"),
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
    usage: object = None,
) -> httpx.Response:
    payload: dict[str, object] = {
        "choices": [
            {
                "finish_reason": finish_reason,
                "message": {"content": content},
            }
        ],
    }
    if usage is not None:
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
        payload, usage = client.analyze(
            system_prompt="Return JSON.",
            paper_json='{"title":"x"}',
        )

    assert payload == {"title_zh": "中文标题"}
    assert usage.total_tokens == 0


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
        [],
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


@pytest.mark.parametrize("status_code", [302, 400])
def test_redirect_and_client_error_are_not_retried(status_code: int) -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            status_code,
            headers={"location": "https://example.com"} if status_code == 302 else {},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = DeepSeekClient(
            api_key="test-key",
            model="deepseek-v4-pro",
            retries=3,
            retry_wait=0,
            http_client=http_client,
        )
        with pytest.raises(httpx.HTTPStatusError):
            client.analyze(
                system_prompt="Return JSON.",
                paper_json='{"title":"x"}',
            )

    assert attempts == 1


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
        ("2.5", 2.5),
        ("invalid", 0.5),
        ("-2", 0.5),
    ],
)
def test_retry_after_numeric_value_overrides_shorter_backoff(
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
