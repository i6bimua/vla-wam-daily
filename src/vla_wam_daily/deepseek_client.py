import json
import math
import time
from collections.abc import Callable
from types import TracebackType
from typing import Self, cast

import httpx
from pydantic import ValidationError

from vla_wam_daily.models import TokenUsage

DEEPSEEK_CHAT_COMPLETIONS_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_TIMEOUT = 90.0
DEFAULT_RETRY_WAIT = 1.0
DEFAULT_MAX_RESPONSE_BYTES = 2_000_000


class DeepSeekResponseError(RuntimeError):
    pass


class RetryableDeepSeekError(RuntimeError):
    pass


def _require_nonempty_string(value: object, *, name: str, safe_header: bool = False) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or (safe_header and ("\r" in value or "\n" in value))
    ):
        raise ValueError(f"{name} must be a non-empty safe string")
    return value


def _require_positive_int(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _require_finite_number(
    value: object,
    *,
    name: str,
    positive: bool,
) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or (value <= 0 if positive else value < 0)
    ):
        qualifier = "positive" if positive else "non-negative"
        raise ValueError(f"{name} must be a finite {qualifier} number")
    return float(value)


class DeepSeekClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        max_output_tokens: int = 1800,
        retries: int = 3,
        timeout: float = DEFAULT_TIMEOUT,
        retry_wait: float = DEFAULT_RETRY_WAIT,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        http_client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        api_key = _require_nonempty_string(api_key, name="api_key", safe_header=True)
        self.model = _require_nonempty_string(model, name="model")
        self.max_output_tokens = _require_positive_int(
            max_output_tokens,
            name="max_output_tokens",
        )
        self.retries = _require_positive_int(retries, name="retries")
        self.timeout = _require_finite_number(timeout, name="timeout", positive=True)
        self.retry_wait = _require_finite_number(
            retry_wait,
            name="retry_wait",
            positive=False,
        )
        self.max_response_bytes = _require_positive_int(
            max_response_bytes,
            name="max_response_bytes",
        )
        if not callable(sleep):
            raise TypeError("sleep must be callable")
        if http_client is not None and not isinstance(http_client, httpx.Client):
            raise TypeError("http_client must be an httpx.Client or None")
        self._sleep = sleep
        self.http = http_client or httpx.Client(
            timeout=self.timeout,
            follow_redirects=False,
        )
        self._owns_client = http_client is None
        self._authorization = f"Bearer {api_key}"

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self.http.close()

    def _request_once(self, body: dict[str, object]) -> bytes:
        with self.http.stream(
            "POST",
            DEEPSEEK_CHAT_COMPLETIONS_URL,
            headers={
                "Authorization": self._authorization,
                "Content-Type": "application/json",
            },
            json=body,
            timeout=self.timeout,
            follow_redirects=False,
        ) as response:
            response.raise_for_status()

            content_length = response.headers.get("content-length")
            if content_length is not None:
                try:
                    declared_size = int(content_length)
                except ValueError:
                    raise DeepSeekResponseError(
                        "DeepSeek returned an invalid content length"
                    ) from None
                if declared_size < 0:
                    raise DeepSeekResponseError("DeepSeek returned an invalid content length")
                if declared_size > self.max_response_bytes:
                    raise DeepSeekResponseError("DeepSeek response is too large")

            media_type = (
                response.headers.get("content-type", "").partition(";")[0].strip().casefold()
            )
            if media_type != "application/json":
                raise DeepSeekResponseError("DeepSeek returned an unsupported content type")

            content = bytearray()
            for chunk in response.iter_bytes():
                if len(content) + len(chunk) > self.max_response_bytes:
                    raise DeepSeekResponseError("DeepSeek response is too large")
                content.extend(chunk)
            return bytes(content)

    @staticmethod
    def _retry_after(response: httpx.Response) -> float | None:
        value = response.headers.get("retry-after")
        if value is None:
            return None
        try:
            delay = float(value)
        except ValueError:
            return None
        if not math.isfinite(delay) or delay < 0:
            return None
        return delay

    def _request(self, body: dict[str, object]) -> bytes:
        last_error: httpx.HTTPStatusError | httpx.TransportError | None = None
        for attempt in range(1, self.retries + 1):
            try:
                return self._request_once(body)
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                if status_code != 429 and not 500 <= status_code < 600:
                    raise
                last_error = exc
            except httpx.TransportError as exc:
                last_error = exc

            if attempt == self.retries:
                if isinstance(last_error, httpx.HTTPStatusError):
                    detail = f"HTTP {last_error.response.status_code}"
                else:
                    detail = type(last_error).__name__
                raise RetryableDeepSeekError(
                    f"DeepSeek request failed after {self.retries} attempts: {detail}"
                ) from None

            delay = self.retry_wait * 2 ** (attempt - 1)
            if isinstance(last_error, httpx.HTTPStatusError):
                retry_after = self._retry_after(last_error.response)
                if retry_after is not None:
                    delay = max(delay, retry_after)
            if delay > 0:
                self._sleep(delay)

        raise AssertionError("retry loop ended without returning")

    @staticmethod
    def _parse_response(content_bytes: bytes) -> tuple[dict[str, object], TokenUsage]:
        try:
            payload = json.loads(content_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise DeepSeekResponseError("DeepSeek returned invalid response JSON") from None
        if not isinstance(payload, dict):
            raise DeepSeekResponseError("DeepSeek response JSON root must be an object")

        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise DeepSeekResponseError("DeepSeek response choices must be a non-empty list")
        choice = choices[0]
        if not isinstance(choice, dict):
            raise DeepSeekResponseError("DeepSeek response choice must be an object")

        if choice.get("finish_reason") != "stop":
            raise DeepSeekResponseError("DeepSeek response finish_reason must be stop")

        message = choice.get("message")
        if not isinstance(message, dict):
            raise DeepSeekResponseError("DeepSeek response message must be an object")
        content = message.get("content")
        if not isinstance(content, str):
            raise DeepSeekResponseError("DeepSeek response content must be a string")
        if not content.strip():
            raise DeepSeekResponseError("DeepSeek returned empty content")

        try:
            decoded = json.loads(content)
        except json.JSONDecodeError:
            raise DeepSeekResponseError("DeepSeek returned invalid JSON content") from None
        if not isinstance(decoded, dict):
            raise DeepSeekResponseError("DeepSeek JSON root must be an object")

        raw_usage = payload.get("usage", {})
        if not isinstance(raw_usage, dict):
            raise DeepSeekResponseError("DeepSeek response usage must be an object")
        try:
            usage = TokenUsage.model_validate(raw_usage, strict=True)
        except ValidationError:
            raise DeepSeekResponseError("DeepSeek response usage is invalid") from None

        return cast(dict[str, object], decoded), usage

    def analyze(
        self,
        *,
        system_prompt: str,
        paper_json: str,
    ) -> tuple[dict[str, object], TokenUsage]:
        if not isinstance(system_prompt, str) or not system_prompt.strip():
            raise ValueError("system_prompt must be non-empty")
        if not isinstance(paper_json, str) or not paper_json.strip():
            raise ValueError("paper_json must be non-empty")
        try:
            paper_payload = json.loads(paper_json)
        except json.JSONDecodeError as exc:
            raise ValueError("paper_json must contain valid JSON") from exc
        if not isinstance(paper_payload, dict):
            raise ValueError("paper_json root must be an object")

        body: dict[str, object] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": paper_json},
            ],
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
            "max_tokens": self.max_output_tokens,
            "stream": False,
        }
        return self._parse_response(self._request(body))
