import re
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite
from types import TracebackType
from typing import Any, Self
from urllib.parse import urlsplit

import feedparser
import httpx
from pydantic import ValidationError

from vla_wam_daily.models import RawPaper

ARXIV_API_URL = "https://export.arxiv.org/api/query"
CATEGORY_RE = re.compile(r"^[a-z][a-z0-9-]*(?:\.[A-Z]{2,3})?$")
ARXIV_ID_RE = re.compile(
    r"(?P<year>\d{2})(?P<month>0[1-9]|1[0-2])"
    r"\.(?P<number>\d{4,5})(?:v(?P<version>[1-9]\d*))?"
)
DEFAULT_PAGE_SIZE = 100
DEFAULT_MAX_IDS_PER_REQUEST = 100
DEFAULT_MAX_RESPONSE_BYTES = 10 * 1024 * 1024
XML_CONTENT_TYPES = frozenset({"application/atom+xml", "application/xml", "text/xml"})


@dataclass(frozen=True)
class _FeedPage:
    papers: tuple[RawPaper, ...]
    raw_entry_count: int
    total_results: int | None
    start_index: int | None
    items_per_page: int | None


class RetryableArxivError(RuntimeError):
    pass


class ArxivWindowTruncatedError(RuntimeError):
    pass


def _require_positive_int(value: int, *, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


def _require_nonnegative_number(value: float, *, name: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value < 0
    ):
        raise ValueError(f"{name} must be a finite non-negative number")


def _validate_new_style_id(arxiv_id: str) -> re.Match[str]:
    match = ARXIV_ID_RE.fullmatch(arxiv_id)
    if match is None:
        raise ValueError(f"invalid new-style arXiv ID: {arxiv_id!r}")
    year_month = int(match.group("year") + match.group("month"))
    if year_month < 704:
        raise ValueError(f"invalid new-style arXiv ID: {arxiv_id!r}")
    return match


def _parse_entry_id(value: object) -> tuple[str, int]:
    if not isinstance(value, str):
        raise ValueError("arXiv entry ID must be a string")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ValueError(f"invalid arXiv entry ID: {value!r}") from error
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname != "arxiv.org"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith("/abs/")
    ):
        raise ValueError(f"invalid arXiv entry ID: {value!r}")
    identifier = parsed.path.removeprefix("/abs/")
    if "/" in identifier:
        raise ValueError(f"invalid arXiv entry ID: {value!r}")
    try:
        match = _validate_new_style_id(identifier)
    except ValueError as error:
        raise ValueError(f"invalid arXiv entry ID: {value!r}") from error
    canonical_id = f"{match.group('year')}{match.group('month')}.{match.group('number')}"
    return canonical_id, int(match.group("version") or "1")


def _parse_entry_datetime(value: object, *, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"arXiv entry has invalid {field}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"arXiv entry has invalid {field}") from error
    if parsed.utcoffset() is None:
        raise ValueError(f"arXiv entry {field} must be timezone-aware")
    return parsed.astimezone(UTC)


def _parse_feed_integer(
    feed: Mapping[str, Any],
    key: str,
) -> int | None:
    value = feed.get(key)
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"invalid arXiv feed {key}") from error
    if parsed < 0:
        raise ValueError(f"invalid arXiv feed {key}")
    return parsed


def _paper_preference_key(paper: RawPaper) -> tuple[datetime, str]:
    return paper.updated_at, paper.model_dump_json()


def _keep_preferred_paper(
    papers: dict[tuple[str, int], RawPaper],
    paper: RawPaper,
) -> None:
    key = (paper.arxiv_id, paper.version)
    existing = papers.get(key)
    if existing is None or _paper_preference_key(paper) > _paper_preference_key(existing):
        papers[key] = paper


class ArxivClient:
    def __init__(
        self,
        *,
        user_agent: str,
        request_delay_seconds: float = 3.0,
        timeout_seconds: float = 30.0,
        retries: int = 3,
        retry_wait_seconds: float = 1.0,
        page_size: int = DEFAULT_PAGE_SIZE,
        max_ids_per_request: int = DEFAULT_MAX_IDS_PER_REQUEST,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        http_client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if (
            not isinstance(user_agent, str)
            or not user_agent.strip()
            or user_agent != user_agent.strip()
            or "\r" in user_agent
            or "\n" in user_agent
        ):
            raise ValueError("user_agent must be a non-empty safe header value")
        _require_nonnegative_number(request_delay_seconds, name="request_delay_seconds")
        _require_nonnegative_number(retry_wait_seconds, name="retry_wait_seconds")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be a finite positive number")
        _require_positive_int(retries, name="retries")
        _require_positive_int(page_size, name="page_size")
        _require_positive_int(max_ids_per_request, name="max_ids_per_request")
        _require_positive_int(max_response_bytes, name="max_response_bytes")

        self.user_agent = user_agent
        self.request_delay_seconds = float(request_delay_seconds)
        self.timeout_seconds = float(timeout_seconds)
        self.retries = retries
        self.retry_wait_seconds = float(retry_wait_seconds)
        self.page_size = page_size
        self.max_ids_per_request = max_ids_per_request
        self.max_response_bytes = max_response_bytes
        self._sleep = sleep
        self._clock = clock
        self._last_request_at: float | None = None
        self.http = http_client or httpx.Client(
            timeout=self.timeout_seconds,
            follow_redirects=False,
        )
        self._owns_client = http_client is None

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

    def _throttle(self) -> None:
        if self._last_request_at is not None:
            elapsed = self._clock() - self._last_request_at
            remaining = self.request_delay_seconds - elapsed
            if remaining > 0:
                self._sleep(remaining)
        self._last_request_at = self._clock()

    def _request_once(self, params: dict[str, str | int]) -> bytes:
        self._throttle()
        with self.http.stream(
            "GET",
            ARXIV_API_URL,
            params=params,
            headers={"User-Agent": self.user_agent},
            timeout=self.timeout_seconds,
            follow_redirects=False,
        ) as response:
            if response.is_redirect:
                raise httpx.HTTPStatusError(
                    f"arXiv redirect response {response.status_code} was rejected",
                    request=response.request,
                    response=response,
                )
            response.raise_for_status()

            content_length = response.headers.get("content-length")
            if content_length is not None:
                try:
                    declared_size = int(content_length)
                except ValueError as error:
                    raise ValueError("arXiv response has an invalid content length") from error
                if declared_size < 0:
                    raise ValueError("arXiv response has an invalid content length")
                if declared_size > self.max_response_bytes:
                    raise ValueError("arXiv response is too large")

            media_type = response.headers.get("content-type", "")
            if media_type:
                media_type = media_type.partition(";")[0].strip().casefold()
                if media_type not in XML_CONTENT_TYPES and not media_type.endswith("+xml"):
                    raise ValueError(f"arXiv response has unsupported content type {media_type!r}")

            content = bytearray()
            for chunk in response.iter_bytes():
                if len(content) + len(chunk) > self.max_response_bytes:
                    raise ValueError("arXiv response is too large")
                content.extend(chunk)
            return bytes(content)

    def _request(self, params: dict[str, str | int]) -> bytes:
        last_error: httpx.HTTPStatusError | httpx.TransportError | None = None
        for attempt in range(1, self.retries + 1):
            try:
                return self._request_once(params)
            except httpx.HTTPStatusError as error:
                status_code = error.response.status_code
                if status_code != 429 and not 500 <= status_code < 600:
                    raise
                last_error = error
            except httpx.TransportError as error:
                last_error = error

            if attempt == self.retries:
                if isinstance(last_error, httpx.HTTPStatusError):
                    detail = f"HTTP {last_error.response.status_code}"
                else:
                    detail = type(last_error).__name__
                raise RetryableArxivError(
                    f"arXiv request failed after {self.retries} attempts: {detail}"
                ) from last_error
            if self.retry_wait_seconds > 0:
                self._sleep(self.retry_wait_seconds)

        raise AssertionError("retry loop ended without returning")

    @staticmethod
    def _parse_feed(xml: bytes) -> _FeedPage:
        parsed = feedparser.parse(xml)
        if getattr(parsed, "bozo", False):
            detail = getattr(parsed, "bozo_exception", "unknown parse error")
            raise ValueError(f"invalid arXiv feed: {detail}")

        raw_entries: list[Mapping[str, Any]] = list(parsed.entries)
        raw_entry_count = len(raw_entries)
        feed: Mapping[str, Any] = parsed.feed
        total_results = _parse_feed_integer(
            feed,
            "opensearch_totalresults",
        )
        start_index = _parse_feed_integer(
            feed,
            "opensearch_startindex",
        )
        items_per_page = _parse_feed_integer(
            feed,
            "opensearch_itemsperpage",
        )
        if items_per_page is not None and items_per_page < raw_entry_count:
            raise ValueError("arXiv feed itemsPerPage is smaller than its entry count")
        if (
            raw_entry_count == 0
            and total_results is not None
            and start_index is not None
            and total_results > start_index
        ):
            raise ValueError("arXiv feed claims results remain but contains no entries")

        papers: list[RawPaper] = []
        critical_fields = (
            "id",
            "updated",
            "published",
            "title",
            "summary",
            "authors",
            "tags",
        )
        for entry in raw_entries:
            missing = [field for field in critical_fields if field not in entry]
            if missing:
                raise ValueError(f"arXiv entry is missing critical fields: {', '.join(missing)}")
            try:
                arxiv_id, version = _parse_entry_id(entry["id"])
                title = " ".join(str(entry["title"]).split())
                abstract = " ".join(str(entry["summary"]).split())
                authors = [
                    " ".join(str(author.get("name", "")).split()) for author in entry["authors"]
                ]
                categories = [str(tag.get("term", "")).strip() for tag in entry["tags"]]
                authors = list(dict.fromkeys(name for name in authors if name))
                categories = list(dict.fromkeys(category for category in categories if category))
                comment_value = entry.get("arxiv_comment")
                comment = (
                    " ".join(str(comment_value).split()) if comment_value is not None else None
                )
                papers.append(
                    RawPaper(
                        arxiv_id=arxiv_id,
                        version=version,
                        published_at=_parse_entry_datetime(entry["published"], field="published"),
                        updated_at=_parse_entry_datetime(entry["updated"], field="updated"),
                        title=title,
                        authors=authors,
                        arxiv_categories=categories,
                        abstract=abstract,
                        comment=comment or None,
                    )
                )
            except (KeyError, TypeError, ValidationError) as error:
                raise ValueError("invalid arXiv entry") from error
        return _FeedPage(
            papers=tuple(papers),
            raw_entry_count=raw_entry_count,
            total_results=total_results,
            start_index=start_index,
            items_per_page=items_per_page,
        )

    def fetch_recent(
        self,
        *,
        categories: list[str],
        since: datetime,
        until: datetime,
        max_results_per_category: int,
    ) -> list[RawPaper]:
        if not categories:
            raise ValueError("categories must not be empty")
        invalid_categories = [
            category
            for category in categories
            if not isinstance(category, str) or CATEGORY_RE.fullmatch(category) is None
        ]
        if invalid_categories:
            raise ValueError(f"invalid arXiv categories: {invalid_categories!r}")
        if since.utcoffset() is None or until.utcoffset() is None:
            raise ValueError("since and until must be timezone-aware")
        if since > until:
            raise ValueError("since must be earlier than or equal to until")
        _require_positive_int(
            max_results_per_category,
            name="max_results_per_category",
        )

        papers: dict[tuple[str, int], RawPaper] = {}
        since_utc = since.astimezone(UTC)
        until_utc = until.astimezone(UTC)
        for category in dict.fromkeys(categories):
            start = 0
            while start < max_results_per_category:
                requested = min(
                    self.page_size,
                    max_results_per_category - start,
                )
                page = self._parse_feed(
                    self._request(
                        {
                            "search_query": f"cat:{category}",
                            "start": start,
                            "max_results": requested,
                            "sortBy": "lastUpdatedDate",
                            "sortOrder": "descending",
                        }
                    )
                )
                if page.start_index is not None and page.start_index != start:
                    raise ValueError(
                        f"arXiv feed startIndex {page.start_index} "
                        f"does not match requested start {start}"
                    )
                for paper in page.papers:
                    if since_utc <= paper.updated_at <= until_utc:
                        _keep_preferred_paper(papers, paper)

                oldest_update = min(
                    (paper.updated_at for paper in page.papers),
                    default=since_utc,
                )
                next_start = start + page.raw_entry_count
                total_results = page.total_results
                if total_results is not None and next_start > total_results:
                    raise ValueError("arXiv feed pagination progressed beyond totalResults")
                if (
                    page.raw_entry_count == 0
                    and total_results is not None
                    and start < total_results
                ):
                    raise ValueError("arXiv feed claims results remain but contains no entries")
                crossed_window = oldest_update < since_utc
                page_is_full = page.raw_entry_count == requested
                if total_results is None:
                    exhausted_results = False
                    more_results_possible = page_is_full
                else:
                    exhausted_results = next_start >= total_results
                    more_results_possible = next_start < total_results

                if (
                    next_start >= max_results_per_category
                    and oldest_update >= since_utc
                    and more_results_possible
                ):
                    raise ArxivWindowTruncatedError(
                        f"{category} exceeded {max_results_per_category} results in the time window"
                    )
                if (
                    page.raw_entry_count == 0
                    or exhausted_results
                    or crossed_window
                    or (total_results is None and not page_is_full)
                ):
                    break
                start = next_start
        return sorted(
            papers.values(),
            key=lambda paper: (-paper.updated_at.timestamp(), paper.arxiv_id, -paper.version),
        )

    def fetch_by_ids(self, arxiv_ids: Iterable[str]) -> list[RawPaper]:
        ids = list(dict.fromkeys(arxiv_ids))
        if not ids:
            return []
        requested: list[tuple[str, int | None]] = []
        for arxiv_id in ids:
            if not isinstance(arxiv_id, str):
                raise ValueError(f"invalid new-style arXiv ID: {arxiv_id!r}")
            match = _validate_new_style_id(arxiv_id)
            canonical_id = f"{match.group('year')}{match.group('month')}.{match.group('number')}"
            requested.append(
                (
                    canonical_id,
                    int(match.group("version")) if match.group("version") else None,
                )
            )

        papers: dict[tuple[str, int], RawPaper] = {}
        for batch_start in range(0, len(ids), self.max_ids_per_request):
            batch = ids[batch_start : batch_start + self.max_ids_per_request]
            batch_requested = requested[batch_start : batch_start + self.max_ids_per_request]
            page = self._parse_feed(
                self._request(
                    {
                        "id_list": ",".join(batch),
                        "max_results": len(batch),
                    }
                )
            )
            for paper in page.papers:
                belongs_to_batch = any(
                    paper.arxiv_id == requested_id
                    and (requested_version is None or paper.version == requested_version)
                    for requested_id, requested_version in batch_requested
                )
                if not belongs_to_batch:
                    raise ValueError("arXiv feed returned an entry outside the current ID batch")
                _keep_preferred_paper(papers, paper)

        papers_by_id: dict[str, list[RawPaper]] = {}
        for paper in papers.values():
            papers_by_id.setdefault(paper.arxiv_id, []).append(paper)

        ordered: list[RawPaper] = []
        emitted: set[tuple[str, int]] = set()
        for arxiv_id, requested_version in requested:
            candidates = papers_by_id.get(arxiv_id, [])
            selected: RawPaper | None
            if requested_version is None:
                selected = max(
                    candidates,
                    key=lambda candidate: (
                        candidate.version,
                        _paper_preference_key(candidate),
                    ),
                    default=None,
                )
            else:
                selected = next(
                    (
                        candidate
                        for candidate in candidates
                        if candidate.version == requested_version
                    ),
                    None,
                )
            if selected is None:
                continue
            key = (selected.arxiv_id, selected.version)
            if key not in emitted:
                emitted.add(key)
                ordered.append(selected)
        return ordered
