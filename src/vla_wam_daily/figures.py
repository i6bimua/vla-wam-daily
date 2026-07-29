import re
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import cast
from urllib.parse import urljoin, urlsplit

import httpx
from pydantic import HttpUrl
from selectolax.lexbor import LexborHTMLParser

from vla_wam_daily.models import (
    ARXIV_FIGURE_HOSTS,
    FigureAsset,
    FigureCacheEntry,
    FigureGallery,
    FigureNumber,
    FigureStatus,
)

ARXIV_ID_RE = re.compile(r"^\d{4}\.\d{4,5}$")
FIGURE_NUMBER_RE = re.compile(
    r"^(?:figure|fig\.)\s*([12])\s*[:.]?\s*",
    re.IGNORECASE,
)
NEGATIVE_CACHE_TTL = timedelta(hours=24)


class HtmlUnavailableError(RuntimeError):
    pass


class TransientFigureFetchError(RuntimeError):
    pass


def _validate_identity(arxiv_id: str, version: int) -> None:
    if (
        ARXIV_ID_RE.fullmatch(arxiv_id) is None
        or type(version) is not int
        or version < 1
    ):
        raise ValueError("invalid arXiv identity")


def figure_cache_key(arxiv_id: str, version: int) -> str:
    _validate_identity(arxiv_id, version)
    return f"{arxiv_id}:v{version}"


def figure_html_url(arxiv_id: str, version: int) -> str:
    _validate_identity(arxiv_id, version)
    return f"https://arxiv.org/html/{arxiv_id}v{version}"


def _normalize_caption(value: str) -> str:
    return " ".join(value.split())


def _is_current_paper_image(candidate: str, html_url: str) -> bool:
    parsed = urlsplit(candidate)
    html = urlsplit(html_url)
    try:
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname in ARXIV_FIGURE_HOSTS
        and parsed.username is None
        and parsed.password is None
        and port in (None, 443)
        and not parsed.fragment
        and parsed.path.startswith(f"{html.path}/")
    )


def parse_figure_gallery(
    html: str,
    html_url: str,
    checked_at: datetime,
) -> FigureGallery:
    tree = LexborHTMLParser(html)
    by_number: dict[int, FigureAsset] = {}

    for node in tree.css("figure"):
        caption_node = node.css_first("figcaption")
        if caption_node is None:
            continue
        raw_caption = _normalize_caption(caption_node.text(separator=" ", strip=True))
        match = FIGURE_NUMBER_RE.match(raw_caption)
        if match is None:
            continue
        caption = raw_caption[match.end() :].strip()
        fragment = (node.attributes.get("id") or "").strip()
        if not caption or not fragment:
            continue

        image_urls: list[HttpUrl] = []
        seen_image_urls: set[str] = set()
        for image in node.css("img"):
            source = (image.attributes.get("src") or "").strip()
            if not source:
                continue
            try:
                candidate = urljoin(f"{html_url}/", source)
                allowed = _is_current_paper_image(candidate, html_url)
            except ValueError:
                continue
            if (
                allowed
                and candidate not in seen_image_urls
            ):
                seen_image_urls.add(candidate)
                image_urls.append(HttpUrl(candidate))
        if not image_urls:
            continue

        number = cast(FigureNumber, int(match.group(1)))
        current = by_number.get(number)
        if current is None:
            by_number[number] = FigureAsset(
                number=number,
                label=f"Figure {number}",
                caption=caption,
                image_urls=tuple(image_urls),
                source_url=HttpUrl(f"{html_url}#{fragment}"),
            )
            continue

        merged = list(current.image_urls)
        merged_urls = {str(url) for url in merged}
        merged.extend(url for url in image_urls if str(url) not in merged_urls)
        by_number[number] = FigureAsset(
            number=current.number,
            label=current.label,
            caption=current.caption,
            image_urls=tuple(merged),
            source_url=current.source_url,
        )

    figures = tuple(by_number[number] for number in sorted(by_number))
    return FigureGallery(
        status=FigureStatus.AVAILABLE if figures else FigureStatus.NOT_FOUND,
        html_url=HttpUrl(html_url),
        figures=figures,
        checked_at=checked_at,
    )


def is_figure_cache_fresh(entry: FigureCacheEntry, now: datetime) -> bool:
    if entry.gallery.status is FigureStatus.AVAILABLE:
        return True
    return abs(now - entry.gallery.checked_at) < NEGATIVE_CACHE_TTL


class ArxivFigureClient:
    def __init__(
        self,
        *,
        user_agent: str,
        request_delay_seconds: float,
        timeout_seconds: float = 30,
        retry_wait_seconds: float = 1,
        max_attempts: int = 3,
        max_html_bytes: int = 5_000_000,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if request_delay_seconds < 0:
            raise ValueError("request_delay_seconds must not be negative")
        if retry_wait_seconds < 0:
            raise ValueError("retry_wait_seconds must not be negative")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if max_html_bytes < 1:
            raise ValueError("max_html_bytes must be positive")

        self.client = client or httpx.Client()
        self._owns_client = client is None
        self.user_agent = user_agent
        self.request_delay_seconds = request_delay_seconds
        self.timeout_seconds = timeout_seconds
        self.retry_wait_seconds = retry_wait_seconds
        self.max_attempts = max_attempts
        self.max_html_bytes = max_html_bytes
        self.sleep = sleep
        self.clock = clock
        self._last_request_at: float | None = None

    def __enter__(self) -> "ArxivFigureClient":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def _throttle(self) -> None:
        if self._last_request_at is not None:
            elapsed = self.clock() - self._last_request_at
            remaining = self.request_delay_seconds - elapsed
            if remaining > 0:
                self.sleep(remaining)
        self._last_request_at = self.clock()

    def _read_html(self, url: str) -> str:
        self._throttle()
        with self.client.stream(
            "GET",
            url,
            headers={"User-Agent": self.user_agent},
            timeout=self.timeout_seconds,
            follow_redirects=True,
        ) as response:
            if response.status_code == 404:
                raise HtmlUnavailableError
            if response.status_code == 429 or response.status_code >= 500:
                raise TransientFigureFetchError(
                    f"arXiv HTML returned {response.status_code}"
                )
            response.raise_for_status()
            if "text/html" not in response.headers.get("content-type", "").casefold():
                raise HtmlUnavailableError

            declared_size = int(response.headers.get("content-length", "0") or 0)
            if declared_size > self.max_html_bytes:
                raise ValueError("arXiv HTML exceeds configured size limit")

            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > self.max_html_bytes:
                    raise ValueError("arXiv HTML exceeds configured size limit")
            return bytes(body).decode(response.encoding or "utf-8")

    def _failure_gallery(
        self,
        status: FigureStatus,
        url: str,
        checked_at: datetime,
    ) -> FigureGallery:
        return FigureGallery(
            status=status,
            html_url=HttpUrl(url),
            checked_at=checked_at,
        )

    def fetch(
        self,
        arxiv_id: str,
        version: int,
        checked_at: datetime,
    ) -> FigureGallery:
        url = figure_html_url(arxiv_id, version)
        for attempt in range(1, self.max_attempts + 1):
            try:
                html = self._read_html(url)
            except HtmlUnavailableError:
                return self._failure_gallery(
                    FigureStatus.HTML_UNAVAILABLE,
                    url,
                    checked_at,
                )
            except (httpx.RequestError, TransientFigureFetchError):
                if attempt < self.max_attempts:
                    self.sleep(self.retry_wait_seconds)
                    continue
                return self._failure_gallery(
                    FigureStatus.FETCH_FAILED,
                    url,
                    checked_at,
                )
            except Exception:
                return self._failure_gallery(
                    FigureStatus.FETCH_FAILED,
                    url,
                    checked_at,
                )

            try:
                return parse_figure_gallery(html, url, checked_at)
            except Exception:
                return self._failure_gallery(
                    FigureStatus.FETCH_FAILED,
                    url,
                    checked_at,
                )

        return self._failure_gallery(FigureStatus.FETCH_FAILED, url, checked_at)
