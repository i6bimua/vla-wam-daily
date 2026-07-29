# arXiv Fig. 1 / Fig. 2 Remote Gallery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show remotely hosted arXiv Figure 1 and Figure 2, their captions, and reliable open/download controls on each published paper without storing paper images in Git or the Pages artifact.

**Architecture:** Extend the strict Python public contract with a versioned figure gallery, then fetch and parse only the arXiv HTML pages of papers that pass the publication threshold. Cache URL/caption metadata separately from AI analysis, merge it into public records before persistence, and render an accessible lazy-loaded Astro gallery whose download action falls back to opening the arXiv original.

**Tech Stack:** Python 3.13, Pydantic 2, httpx, selectolax/Lexbor, pytest/respx, Astro 6, TypeScript, Zod 4, Vitest, Playwright, GitHub Pages.

---

## Execution placement

This is an approved amendment to
`docs/superpowers/plans/2026-07-27-vla-wam-daily-implementation.md`.
Execute tasks in this order:

1. Execute Figure Tasks 1–2 immediately, before original Task 5.
2. Execute Figure Task 3 immediately after original Task 9 creates `storage.py`.
3. Execute Figure Task 4 immediately after original Task 10 creates `pipeline.py` and the CLI.
4. Execute Figure Task 5 immediately after original Task 11 creates the Astro site, before search/filter work.
5. Execute Figure Task 6 after original Task 14 creates Playwright coverage and original Task 16 creates the public README.

This placement keeps each change testable and prevents temporary placeholder image files or an
unvalidated frontend schema from entering generated data.

## File responsibility map

- `src/vla_wam_daily/models.py`: strict public Figure models and Figure run counters.
- `src/vla_wam_daily/figures.py`: arXiv HTML URL construction, throttled fetching, DOM parsing,
  URL allowlisting, negative-cache freshness.
- `src/vla_wam_daily/storage.py`: load and atomically save Figure metadata cache.
- `src/vla_wam_daily/pipeline.py`: enrich only publishable records and account for Figure outcomes.
- `web/src/components/FigureGallery.astro`: gallery markup, lazy images, download/fallback behavior.
- `web/src/lib/schema.ts`: mirror the Python Figure contract at the build boundary.
- `web/tests/site.spec.ts`: user-visible gallery, download, mobile, and error fallback.

## Figure Task 1: Extend the strict public data contract

**Files:**

- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `src/vla_wam_daily/models.py`
- Modify: `tests/factories.py`
- Modify: `tests/test_models.py`

- [ ] **Step 1: Write failing Figure model tests**

Append these imports and tests to `tests/test_models.py`:

```python
from vla_wam_daily.models import (
    FigureAsset,
    FigureGallery,
    FigureStatus,
)


def test_figure_gallery_serializes_remote_arxiv_images() -> None:
    gallery = FigureGallery(
        status=FigureStatus.AVAILABLE,
        html_url="https://arxiv.org/html/2607.12345v1",
        checked_at=datetime(2026, 7, 29, tzinfo=UTC),
        figures=[
            FigureAsset(
                number=1,
                label="Figure 1",
                caption="The model architecture.",
                image_urls=[
                    "https://arxiv.org/html/2607.12345v1/x1.png",
                    "https://arxiv.org/html/2607.12345v1/x2.svg",
                ],
                source_url="https://arxiv.org/html/2607.12345v1#Sx1.F1",
            )
        ],
    )
    payload = gallery.model_dump(mode="json")
    assert payload["status"] == "available"
    assert payload["figures"][0]["number"] == 1
    assert payload["figures"][0]["source"] == "arxiv_html"
    assert payload["figures"][0]["image_urls"] == [
        "https://arxiv.org/html/2607.12345v1/x1.png",
        "https://arxiv.org/html/2607.12345v1/x2.svg",
    ]


@pytest.mark.parametrize(
    "url",
    [
        "http://arxiv.org/html/2607.12345v1/x1.png",
        "https://example.com/x1.png",
        "data:image/png;base64,AAAA",
    ],
)
def test_figure_asset_rejects_non_arxiv_https_images(url: str) -> None:
    with pytest.raises(ValidationError):
        FigureAsset(
            number=1,
            label="Figure 1",
            caption="Caption",
            image_urls=[url],
            source_url="https://arxiv.org/html/2607.12345v1#F1",
        )


def test_available_gallery_requires_a_figure() -> None:
    with pytest.raises(ValidationError):
        FigureGallery(
            status=FigureStatus.AVAILABLE,
            html_url="https://arxiv.org/html/2607.12345v1",
            checked_at=datetime(2026, 7, 29, tzinfo=UTC),
            figures=[],
        )


def test_unavailable_gallery_rejects_stale_figures() -> None:
    gallery = make_gallery()
    with pytest.raises(ValidationError):
        gallery.model_copy(update={"status": FigureStatus.NOT_FOUND}, deep=True).__class__(
            **{
                **gallery.model_dump(),
                "status": FigureStatus.NOT_FOUND,
            }
        )


def test_public_data_file_requires_checked_gallery() -> None:
    unchecked = make_record().model_copy(update={"figure_gallery": None})
    with pytest.raises(ValidationError):
        DataFile(
            generated_at=datetime(2026, 7, 29, tzinfo=UTC),
            stats=RunStats(),
            papers=[unchecked],
        )
```

Also import `make_gallery` from `tests.factories`.

- [ ] **Step 2: Run the model tests to verify RED**

Run:

```bash
uv run pytest tests/test_models.py -v
```

Expected: collection fails because the Figure models and `make_gallery` do not exist.

- [ ] **Step 3: Add the parser dependency**

Add this project dependency in `pyproject.toml`:

```toml
"selectolax>=0.4.11,<0.5",
```

Run:

```bash
uv lock
```

Expected: `uv.lock` resolves a Python 3.13-compatible selectolax wheel.

- [ ] **Step 4: Implement the Figure models and public-boundary validation**

Add `model_validator` to the existing Pydantic imports in `models.py`, then add:

```python
ARXIV_FIGURE_HOSTS = frozenset({"arxiv.org", "www.arxiv.org"})
FigureNumber = Literal[1, 2]
FigureImageList = Annotated[list[HttpUrl], Field(min_length=1)]


def validate_arxiv_https_url(value: HttpUrl) -> HttpUrl:
    if value.scheme != "https" or value.host not in ARXIV_FIGURE_HOSTS:
        raise ValueError("figure URLs must use HTTPS on arxiv.org")
    return value


class FigureStatus(StrEnum):
    AVAILABLE = "available"
    HTML_UNAVAILABLE = "html_unavailable"
    NOT_FOUND = "not_found"
    FETCH_FAILED = "fetch_failed"


class FigureAsset(StrictModel):
    number: FigureNumber
    label: NonEmptyStr
    caption: NonEmptyStr
    image_urls: FigureImageList
    source_url: HttpUrl
    source: Literal["arxiv_html"] = "arxiv_html"

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, value: HttpUrl) -> HttpUrl:
        return validate_arxiv_https_url(value)

    @field_validator("image_urls")
    @classmethod
    def validate_image_urls(cls, values: list[HttpUrl]) -> list[HttpUrl]:
        unique: list[HttpUrl] = []
        seen: set[str] = set()
        for value in values:
            validated = validate_arxiv_https_url(value)
            key = str(validated)
            if key not in seen:
                seen.add(key)
                unique.append(validated)
        return unique


class FigureGallery(StrictModel):
    status: FigureStatus
    html_url: HttpUrl
    figures: list[FigureAsset] = Field(default_factory=list, max_length=2)
    checked_at: UtcDatetime

    @field_validator("html_url")
    @classmethod
    def validate_html_url(cls, value: HttpUrl) -> HttpUrl:
        return validate_arxiv_https_url(value)

    @field_validator("figures")
    @classmethod
    def validate_figures(cls, values: list[FigureAsset]) -> list[FigureAsset]:
        numbers = [figure.number for figure in values]
        if len(numbers) != len(set(numbers)):
            raise ValueError("figure numbers must be unique")
        return sorted(values, key=lambda figure: figure.number)

    @model_validator(mode="after")
    def validate_status_content(self) -> "FigureGallery":
        if self.status is FigureStatus.AVAILABLE and not self.figures:
            raise ValueError("available gallery must contain at least one figure")
        if self.status is not FigureStatus.AVAILABLE and self.figures:
            raise ValueError("unavailable gallery cannot contain figures")
        return self


class FigureCacheEntry(StrictModel):
    key: NonEmptyStr
    gallery: FigureGallery
```

Add this field to `PaperRecord`:

```python
figure_gallery: FigureGallery | None = None
```

Append these counters to `RunStats`:

```python
figure_cache_hits: int = Field(default=0, ge=0)
figure_requests: int = Field(default=0, ge=0)
figure_available: int = Field(default=0, ge=0)
figure_unavailable: int = Field(default=0, ge=0)
figure_failed: int = Field(default=0, ge=0)
```

Add this validator to `DataFile`:

```python
@model_validator(mode="after")
def require_checked_figure_galleries(self) -> "DataFile":
    unchecked = [paper.arxiv_id for paper in self.papers if paper.figure_gallery is None]
    if unchecked:
        raise ValueError(f"public papers require figure galleries: {', '.join(unchecked)}")
    return self
```

The optional `PaperRecord.figure_gallery` lets the analyzer cache title/abstract analysis before
Figure enrichment. `DataFile` remains the strict public boundary and rejects unchecked records.

- [ ] **Step 5: Extend the shared factory**

Update imports in `tests/factories.py` and add:

```python
from vla_wam_daily.models import FigureAsset, FigureGallery, FigureStatus


def make_gallery(
    *,
    arxiv_id: str = "2607.12345",
    version: int = 1,
    status: FigureStatus = FigureStatus.AVAILABLE,
) -> FigureGallery:
    figures: list[FigureAsset] = []
    if status is FigureStatus.AVAILABLE:
        figures = [
            FigureAsset(
                number=1,
                label="Figure 1",
                caption="The model architecture.",
                image_urls=[f"https://arxiv.org/html/{arxiv_id}v{version}/x1.png"],
                source_url=f"https://arxiv.org/html/{arxiv_id}v{version}#S1.F1",
            ),
            FigureAsset(
                number=2,
                label="Figure 2",
                caption="Robot evaluation environments.",
                image_urls=[f"https://arxiv.org/html/{arxiv_id}v{version}/x2.png"],
                source_url=f"https://arxiv.org/html/{arxiv_id}v{version}#S2.F2",
            ),
        ]
    return FigureGallery(
        status=status,
        html_url=f"https://arxiv.org/html/{arxiv_id}v{version}",
        checked_at=datetime(2026, 7, 27, 1, 30, tzinfo=UTC),
        figures=figures,
    )
```

Pass this field when creating `PaperRecord` in `make_record`:

```python
figure_gallery=make_gallery(arxiv_id=arxiv_id, version=version),
```

Add this helper after `make_record`:

```python
def make_figure_fixture_records() -> list[PaperRecord]:
    records = [make_record()]
    statuses = [
        FigureStatus.HTML_UNAVAILABLE,
        FigureStatus.NOT_FOUND,
        FigureStatus.FETCH_FAILED,
    ]
    for offset, status in enumerate(statuses, start=1):
        arxiv_id = f"2607.2000{offset}"
        record = make_record(arxiv_id=arxiv_id, score=6)
        records.append(
            record.model_copy(
                update={
                    "title": f"Figure fallback fixture {offset}",
                    "title_zh": f"图片降级状态测试 {offset}",
                    "abstract": "A fixture paper without the primary search keyword.",
                    "analysis": record.analysis.model_copy(
                        update={
                            "one_sentence_summary": "用于验证图片不可用状态。",
                            "main_contribution": "仅用于界面测试。",
                            "method": "固定测试数据。",
                            "relation_to_vla_wam": "测试数据。",
                        }
                    ),
                    "figure_gallery": make_gallery(arxiv_id=arxiv_id, status=status),
                }
            )
        )
    return records
```

Update the complete public-shape assertion in `tests/test_models.py` to include
`"figure_gallery"`. Extend the expected `RunStats().model_dump()` dictionary with:

```python
"figure_cache_hits": 0,
"figure_requests": 0,
"figure_available": 0,
"figure_unavailable": 0,
"figure_failed": 0,
```

- [ ] **Step 6: Run all Python gates**

Run:

```bash
uv run pytest tests/test_models.py -v
uv run pytest
uv run ruff check src tests
uv run mypy
git diff --check
```

Expected: all commands exit 0.

- [ ] **Step 7: Commit the Figure contract**

```bash
git add pyproject.toml uv.lock src/vla_wam_daily/models.py tests/factories.py tests/test_models.py
git commit -m "feat: define remote figure gallery contract"
```

## Figure Task 2: Fetch and parse arXiv HTML figures

**Files:**

- Create: `src/vla_wam_daily/figures.py`
- Create: `tests/fixtures/arxiv/figures.html`
- Create: `tests/test_figures.py`

- [ ] **Step 1: Add a representative arXiv HTML fixture**

Create `tests/fixtures/arxiv/figures.html`:

```html
<!doctype html>
<html>
  <body>
    <figure id="S1.F1" class="ltx_figure">
      <img src="x1.png" alt="architecture" />
      <figcaption>Figure 1: The model architecture.</figcaption>
    </figure>
    <figure id="S2.F2" class="ltx_figure">
      <img src="./x2-a.png" alt="panel a" />
      <img src="https://arxiv.org/html/2607.12345v1/x2-b.svg" alt="panel b" />
      <img src="https://example.com/tracker.png" alt="external" />
      <figcaption>Fig. 2: Robot evaluation environments.</figcaption>
    </figure>
    <figure id="S3.F3" class="ltx_figure">
      <img src="x3.png" />
      <figcaption>Figure 3: This figure is outside the requested range.</figcaption>
    </figure>
    <figure id="S4.T1" class="ltx_table">
      <figcaption>Table 1: Results.</figcaption>
    </figure>
  </body>
</html>
```

- [ ] **Step 2: Write failing parser and client tests**

Create `tests/test_figures.py`:

```python
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import respx

from vla_wam_daily.figures import (
    ArxivFigureClient,
    figure_cache_key,
    is_figure_cache_fresh,
    parse_figure_gallery,
)
from vla_wam_daily.models import FigureCacheEntry, FigureStatus

CHECKED_AT = datetime(2026, 7, 29, 2, 30, tzinfo=UTC)
HTML_URL = "https://arxiv.org/html/2607.12345v1"


def fixture_html() -> str:
    return Path("tests/fixtures/arxiv/figures.html").read_text(encoding="utf-8")


def test_parser_extracts_only_figures_one_and_two() -> None:
    gallery = parse_figure_gallery(fixture_html(), HTML_URL, CHECKED_AT)
    assert gallery.status is FigureStatus.AVAILABLE
    assert [figure.number for figure in gallery.figures] == [1, 2]
    assert gallery.figures[0].caption == "The model architecture."
    assert [str(url) for url in gallery.figures[1].image_urls] == [
        "https://arxiv.org/html/2607.12345v1/x2-a.png",
        "https://arxiv.org/html/2607.12345v1/x2-b.svg",
    ]
    assert str(gallery.figures[0].source_url) == f"{HTML_URL}#S1.F1"


def test_parser_returns_not_found_for_missing_target_figures() -> None:
    gallery = parse_figure_gallery(
        "<html><body><p>No figures</p></body></html>",
        HTML_URL,
        CHECKED_AT,
    )
    assert gallery.status is FigureStatus.NOT_FOUND
    assert gallery.figures == []


@respx.mock
def test_client_maps_404_to_html_unavailable() -> None:
    respx.get(HTML_URL).mock(return_value=httpx.Response(404))
    client = ArxivFigureClient(
        user_agent="VLA-WAM-Daily-Test/0.1",
        request_delay_seconds=0,
        retry_wait_seconds=0,
    )
    gallery = client.fetch("2607.12345", 1, CHECKED_AT)
    assert gallery.status is FigureStatus.HTML_UNAVAILABLE
    client.close()


@respx.mock
def test_client_retries_5xx_then_returns_fetch_failed() -> None:
    route = respx.get(HTML_URL).mock(return_value=httpx.Response(503))
    client = ArxivFigureClient(
        user_agent="VLA-WAM-Daily-Test/0.1",
        request_delay_seconds=0,
        retry_wait_seconds=0,
        max_attempts=3,
    )
    gallery = client.fetch("2607.12345", 1, CHECKED_AT)
    assert gallery.status is FigureStatus.FETCH_FAILED
    assert route.call_count == 3
    client.close()


def test_negative_cache_expires_after_24_hours() -> None:
    gallery = parse_figure_gallery("<html></html>", HTML_URL, CHECKED_AT)
    entry = FigureCacheEntry(key=figure_cache_key("2607.12345", 1), gallery=gallery)
    assert is_figure_cache_fresh(entry, CHECKED_AT + timedelta(hours=23))
    assert not is_figure_cache_fresh(entry, CHECKED_AT + timedelta(hours=24))


def test_successful_cache_does_not_expire_for_same_version() -> None:
    gallery = parse_figure_gallery(fixture_html(), HTML_URL, CHECKED_AT)
    entry = FigureCacheEntry(key=figure_cache_key("2607.12345", 1), gallery=gallery)
    assert is_figure_cache_fresh(entry, CHECKED_AT + timedelta(days=365))
    assert entry.key != figure_cache_key("2607.12345", 2)
```

- [ ] **Step 3: Run tests to verify RED**

Run:

```bash
uv run pytest tests/test_figures.py -v
```

Expected: collection fails because `vla_wam_daily.figures` does not exist.

- [ ] **Step 4: Implement the parser and best-effort client**

Create `src/vla_wam_daily/figures.py`:

```python
import re
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import cast
from urllib.parse import urljoin, urlparse

import httpx
from selectolax.lexbor import LexborHTMLParser

from vla_wam_daily.models import (
    ARXIV_FIGURE_HOSTS,
    FigureAsset,
    FigureCacheEntry,
    FigureGallery,
    FigureNumber,
    FigureStatus,
)

FIGURE_NUMBER_RE = re.compile(r"^(?:figure|fig\\.)\\s*([12])\\s*[:.]?\\s*", re.IGNORECASE)
NEGATIVE_CACHE_TTL = timedelta(hours=24)


class HtmlUnavailableError(RuntimeError):
    pass


class TransientFigureFetchError(RuntimeError):
    pass


def figure_cache_key(arxiv_id: str, version: int) -> str:
    return f"{arxiv_id}:v{version}"


def figure_html_url(arxiv_id: str, version: int) -> str:
    if not re.fullmatch(r"\\d{4}\\.\\d{4,5}", arxiv_id) or version < 1:
        raise ValueError("invalid arXiv identity")
    return f"https://arxiv.org/html/{arxiv_id}v{version}"


def is_allowed_arxiv_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname in ARXIV_FIGURE_HOSTS


def normalize_caption(value: str) -> str:
    return " ".join(value.split())


def parse_figure_gallery(html: str, html_url: str, checked_at: datetime) -> FigureGallery:
    tree = LexborHTMLParser(html)
    by_number: dict[int, FigureAsset] = {}
    for node in tree.css("figure"):
        caption_node = node.css_first("figcaption")
        if caption_node is None:
            continue
        raw_caption = normalize_caption(caption_node.text(separator=" ", strip=True))
        match = FIGURE_NUMBER_RE.match(raw_caption)
        if match is None:
            continue
        number = cast(FigureNumber, int(match.group(1)))
        caption = raw_caption[match.end() :].strip()
        if not caption:
            continue
        image_urls: list[str] = []
        for image in node.css("img"):
            source = image.attributes.get("src", "").strip()
            candidate = urljoin(f"{html_url}/", source)
            if source and is_allowed_arxiv_url(candidate) and candidate not in image_urls:
                image_urls.append(candidate)
        if not image_urls:
            continue
        fragment = node.attributes.get("id", "").strip()
        source_url = f"{html_url}#{fragment}" if fragment else html_url
        current = by_number.get(number)
        if current is None:
            by_number[number] = FigureAsset(
                number=number,
                label=f"Figure {number}",
                caption=caption,
                image_urls=image_urls,
                source_url=source_url,
            )
            continue
        merged = [str(url) for url in current.image_urls]
        merged.extend(url for url in image_urls if url not in merged)
        by_number[number] = FigureAsset(
            number=current.number,
            label=current.label,
            caption=current.caption,
            image_urls=merged,
            source_url=current.source_url,
        )
    figures = [by_number[number] for number in sorted(by_number)]
    return FigureGallery(
        status=FigureStatus.AVAILABLE if figures else FigureStatus.NOT_FOUND,
        html_url=html_url,
        figures=figures,
        checked_at=checked_at,
    )


def is_figure_cache_fresh(entry: FigureCacheEntry, now: datetime) -> bool:
    if entry.gallery.status is FigureStatus.AVAILABLE:
        return True
    return now - entry.gallery.checked_at < NEGATIVE_CACHE_TTL


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
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if max_html_bytes < 1:
            raise ValueError("max_html_bytes must be positive")
        self.client = client or httpx.Client(
            headers={"User-Agent": user_agent},
            timeout=timeout_seconds,
            follow_redirects=True,
        )
        self._owns_client = client is None
        self.request_delay_seconds = request_delay_seconds
        self.retry_wait_seconds = retry_wait_seconds
        self.max_attempts = max_attempts
        self.max_html_bytes = max_html_bytes
        self.sleep = sleep
        self.clock = clock
        self._last_request_at: float | None = None

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def _throttle(self) -> None:
        if self._last_request_at is not None:
            remaining = self.request_delay_seconds - (self.clock() - self._last_request_at)
            if remaining > 0:
                self.sleep(remaining)
        self._last_request_at = self.clock()

    def _read_html(self, url: str) -> str:
        self._throttle()
        with self.client.stream("GET", url) as response:
            if response.status_code == 404:
                raise HtmlUnavailableError
            if response.status_code == 429 or response.status_code >= 500:
                raise TransientFigureFetchError(f"arXiv HTML returned {response.status_code}")
            response.raise_for_status()
            if "text/html" not in response.headers.get("content-type", "").casefold():
                raise HtmlUnavailableError
            declared = int(response.headers.get("content-length", "0") or 0)
            if declared > self.max_html_bytes:
                raise ValueError("arXiv HTML exceeds configured size limit")
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > self.max_html_bytes:
                    raise ValueError("arXiv HTML exceeds configured size limit")
            return bytes(body).decode(response.encoding or "utf-8", errors="replace")

    def fetch(self, arxiv_id: str, version: int, checked_at: datetime) -> FigureGallery:
        url = figure_html_url(arxiv_id, version)
        for attempt in range(1, self.max_attempts + 1):
            try:
                html = self._read_html(url)
            except HtmlUnavailableError:
                return FigureGallery(
                    status=FigureStatus.HTML_UNAVAILABLE,
                    html_url=url,
                    checked_at=checked_at,
                )
            except (httpx.RequestError, TransientFigureFetchError):
                if attempt < self.max_attempts:
                    self.sleep(self.retry_wait_seconds)
                    continue
                return FigureGallery(
                    status=FigureStatus.FETCH_FAILED,
                    html_url=url,
                    checked_at=checked_at,
                )
            except (httpx.HTTPStatusError, UnicodeError, ValueError):
                return FigureGallery(
                    status=FigureStatus.FETCH_FAILED,
                    html_url=url,
                    checked_at=checked_at,
                )
            try:
                return parse_figure_gallery(html, url, checked_at)
            except Exception:
                return FigureGallery(
                    status=FigureStatus.FETCH_FAILED,
                    html_url=url,
                    checked_at=checked_at,
                )
        raise AssertionError("unreachable retry state")
```

- [ ] **Step 5: Add edge-case tests**

Append tests for:

```python
def test_parser_ignores_cross_references_and_empty_captions() -> None:
    html = """
    <html><body>
      <p>Figure 1 shows the system.</p>
      <figure><img src="x1.png"><figcaption>Figure 1:</figcaption></figure>
    </body></html>
    """
    assert parse_figure_gallery(html, HTML_URL, CHECKED_AT).status is FigureStatus.NOT_FOUND


@respx.mock
def test_client_rejects_oversized_html_without_raising() -> None:
    respx.get(HTML_URL).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html", "content-length": "101"},
            content=b"<html></html>",
        )
    )
    client = ArxivFigureClient(
        user_agent="VLA-WAM-Daily-Test/0.1",
        request_delay_seconds=0,
        max_html_bytes=100,
    )
    assert client.fetch("2607.12345", 1, CHECKED_AT).status is FigureStatus.FETCH_FAILED
    client.close()
```

Add these successful 200 and timeout tests:

```python
@respx.mock
def test_client_fetches_and_parses_html() -> None:
    respx.get(HTML_URL).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text=fixture_html(),
        )
    )
    client = ArxivFigureClient(
        user_agent="VLA-WAM-Daily-Test/0.1",
        request_delay_seconds=0,
    )
    gallery = client.fetch("2607.12345", 1, CHECKED_AT)
    assert gallery.status is FigureStatus.AVAILABLE
    assert gallery.checked_at == CHECKED_AT
    client.close()


@respx.mock
def test_client_timeout_is_a_non_raising_failure() -> None:
    respx.get(HTML_URL).mock(side_effect=httpx.ConnectTimeout("simulated timeout"))
    client = ArxivFigureClient(
        user_agent="VLA-WAM-Daily-Test/0.1",
        request_delay_seconds=0,
        retry_wait_seconds=0,
        max_attempts=2,
    )
    gallery = client.fetch("2607.12345", 1, CHECKED_AT)
    assert gallery.status is FigureStatus.FETCH_FAILED
    assert gallery.checked_at == CHECKED_AT
    client.close()
```

- [ ] **Step 6: Run all Python gates**

```bash
uv run pytest tests/test_figures.py -v
uv run pytest
uv run ruff check src tests
uv run mypy
git diff --check
```

Expected: all commands exit 0.

- [ ] **Step 7: Commit the arXiv Figure client**

```bash
git add src/vla_wam_daily/figures.py tests/fixtures/arxiv/figures.html tests/test_figures.py
git commit -m "feat: parse remote arXiv figures"
```

## Figure Task 3: Persist Figure metadata cache

**Prerequisite:** Original Task 9 has created `src/vla_wam_daily/storage.py`.

**Files:**

- Modify: `src/vla_wam_daily/storage.py`
- Modify: `tests/test_storage.py`
- Create: `data/cache/figures.json`
- Create: `tests/fixtures/data/cache/figures.json`

- [ ] **Step 1: Write failing storage tests**

Add:

```python
from tests.factories import make_gallery
from vla_wam_daily.figures import figure_cache_key
from vla_wam_daily.models import FigureCacheEntry
from vla_wam_daily.storage import load_figure_cache


def test_save_and_load_figure_metadata_without_image_bytes(tmp_path: Path) -> None:
    key = figure_cache_key("2607.12345", 1)
    figure_cache = {key: FigureCacheEntry(key=key, gallery=make_gallery())}
    save_successful_run(
        tmp_path,
        [make_record()],
        {},
        RunStats(published=1, figure_available=1),
        datetime(2026, 7, 29, tzinfo=UTC),
        figure_cache=figure_cache,
    )
    loaded = load_figure_cache(tmp_path)
    assert loaded[key].gallery.figures[0].number == 1
    raw = (tmp_path / "cache/figures.json").read_text(encoding="utf-8")
    assert "https://arxiv.org/html/" in raw
    assert "data:image/" not in raw
```

- [ ] **Step 2: Run the storage test to verify RED**

```bash
uv run pytest tests/test_storage.py -v
```

Expected: import/signature failure for `load_figure_cache` or `figure_cache`.

- [ ] **Step 3: Implement cache load/save**

Add:

```python
from vla_wam_daily.models import FigureCacheEntry


def load_figure_cache(data_dir: Path) -> dict[str, FigureCacheEntry]:
    path = data_dir / "cache/figures.json"
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {key: FigureCacheEntry.model_validate(value) for key, value in raw.items()}
```

Extend `save_successful_run` with the final optional keyword parameter:

```python
figure_cache: dict[str, FigureCacheEntry] | None = None,
```

Before writing `pending`, add:

```python
if figure_cache is not None:
    pending[data_dir / "cache/figures.json"] = {
        key: value.model_dump(mode="json") for key, value in sorted(figure_cache.items())
    }
```

Keep the parameter optional until Figure Task 4 updates every call site.

- [ ] **Step 4: Seed empty caches and regenerate fixtures**

Create both files with exactly:

```json
{}
```

Files:

- `data/cache/figures.json`
- `tests/fixtures/data/cache/figures.json`

Regenerate the browser fixture after its factory includes Figure data:

```bash
uv run python -c "from datetime import UTC, datetime; from pathlib import Path; from tests.factories import make_figure_fixture_records; from vla_wam_daily.models import RunStats; from vla_wam_daily.storage import save_successful_run; records=make_figure_fixture_records(); save_successful_run(Path('tests/fixtures/data'), records, {}, RunStats(fetched=4, published=4, figure_available=1, figure_unavailable=2, figure_failed=1), datetime(2026, 7, 29, tzinfo=UTC), figure_cache={})"
```

- [ ] **Step 5: Run storage and full verification**

```bash
uv run pytest tests/test_storage.py tests/test_models.py -v
uv run pytest
uv run ruff check src tests
uv run mypy
git diff --check
```

Expected: all commands exit 0.

- [ ] **Step 6: Commit Figure cache storage**

```bash
git add src/vla_wam_daily/storage.py tests/test_storage.py data/cache/figures.json tests/fixtures/data
git commit -m "feat: persist remote figure metadata"
```

## Figure Task 4: Enrich published papers in the daily pipeline

**Prerequisite:** Original Task 10 has created `pipeline.py`, `cli.py`, and their tests.

**Files:**

- Modify: `src/vla_wam_daily/pipeline.py`
- Modify: `src/vla_wam_daily/cli.py`
- Modify: `tests/test_pipeline.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing non-blocking enrichment tests**

Add a fake:

```python
from tests.factories import make_gallery
from vla_wam_daily.models import FigureGallery, FigureStatus


class FakeFigureFetcher:
    def __init__(self, gallery: FigureGallery | None = None) -> None:
        self.gallery = gallery or make_gallery()
        self.calls = 0

    def fetch(self, arxiv_id: str, version: int, checked_at: datetime) -> FigureGallery:
        self.calls += 1
        return self.gallery
```

Update every existing `run_daily` call to pass a `figure_fetcher`. Add:

```python
def test_only_published_papers_fetch_figures(tmp_path: Path) -> None:
    config = load_config(Path("config/topics.yaml"))
    figure_fetcher = FakeFigureFetcher()
    report = run_daily(
        config=config,
        data_dir=tmp_path,
        fetcher=FakeFetcher([raw_paper()]),
        analysis_client=CountingClient(),
        figure_fetcher=figure_fetcher,
        prompt=config.analysis.prompt_path(Path("prompts")).read_text(encoding="utf-8"),
        lookback_days=3,
        threshold=6,
        force_ids=[],
        dry_run=False,
        now=datetime(2026, 7, 29, 2, 30, tzinfo=UTC),
    )
    assert figure_fetcher.calls == 1
    assert report.published[0].figure_gallery is not None
    assert report.stats.figure_requests == 1
    assert report.stats.figure_available == 1


def test_negative_figure_result_does_not_block_publication(tmp_path: Path) -> None:
    unavailable = FigureGallery(
        status=FigureStatus.FETCH_FAILED,
        html_url="https://arxiv.org/html/2607.12345v1",
        checked_at=datetime(2026, 7, 29, 2, 30, tzinfo=UTC),
    )
    report = run_daily(
        config=load_config(Path("config/topics.yaml")),
        data_dir=tmp_path,
        fetcher=FakeFetcher([raw_paper()]),
        analysis_client=CountingClient(),
        figure_fetcher=FakeFigureFetcher(unavailable),
        prompt=Path("prompts/analysis-v1.md").read_text(encoding="utf-8"),
        lookback_days=3,
        threshold=6,
        force_ids=[],
        dry_run=False,
        now=datetime(2026, 7, 29, 2, 30, tzinfo=UTC),
    )
    assert len(report.published) == 1
    assert report.stats.figure_failed == 1
    assert report.stats.failed == 0
```

- [ ] **Step 2: Run pipeline tests to verify RED**

```bash
uv run pytest tests/test_pipeline.py -v
```

Expected: `run_daily` does not accept or use `figure_fetcher`.

- [ ] **Step 3: Implement a focused enrichment helper**

Add imports and protocol:

```python
from dataclasses import dataclass

from vla_wam_daily.figures import figure_cache_key, is_figure_cache_fresh
from vla_wam_daily.models import FigureCacheEntry, FigureGallery, FigureStatus
from vla_wam_daily.storage import load_figure_cache


class FigureFetcher(Protocol):
    def fetch(self, arxiv_id: str, version: int, checked_at: datetime) -> FigureGallery:
        pass


@dataclass(frozen=True)
class FigureEnrichment:
    records: list[PaperRecord]
    cache: dict[str, FigureCacheEntry]
    cache_hits: int
    requests: int
    available: int
    unavailable: int
    failed: int


def enrich_figures(
    records: list[PaperRecord],
    *,
    figure_fetcher: FigureFetcher,
    cache: dict[str, FigureCacheEntry],
    now: datetime,
) -> FigureEnrichment:
    enriched: list[PaperRecord] = []
    cache_hits = requests = available = unavailable = failed = 0
    for record in records:
        key = figure_cache_key(record.arxiv_id, record.version)
        entry = cache.get(key)
        if entry is not None and is_figure_cache_fresh(entry, now):
            gallery = entry.gallery
            cache_hits += 1
        else:
            gallery = figure_fetcher.fetch(record.arxiv_id, record.version, now)
            cache[key] = FigureCacheEntry(key=key, gallery=gallery)
            requests += 1
        if gallery.status is FigureStatus.AVAILABLE:
            available += 1
        elif gallery.status is FigureStatus.FETCH_FAILED:
            failed += 1
        else:
            unavailable += 1
        enriched.append(record.model_copy(update={"figure_gallery": gallery}))
    return FigureEnrichment(
        records=enriched,
        cache=cache,
        cache_hits=cache_hits,
        requests=requests,
        available=available,
        unavailable=unavailable,
        failed=failed,
    )
```

- [ ] **Step 4: Wire enrichment after the publication threshold**

Add the `figure_fetcher: FigureFetcher` keyword argument to `run_daily`.
Immediately after sorting the thresholded `published` list:

```python
figure_result = enrich_figures(
    published,
    figure_fetcher=figure_fetcher,
    cache=load_figure_cache(data_dir),
    now=now,
)
published = figure_result.records
```

Pass these fields when constructing `RunStats`:

```python
figure_cache_hits=figure_result.cache_hits,
figure_requests=figure_result.requests,
figure_available=figure_result.available,
figure_unavailable=figure_result.unavailable,
figure_failed=figure_result.failed,
```

Change the persistence call to:

```python
save_successful_run(
    data_dir,
    published,
    cache,
    stats,
    now,
    figure_cache=figure_result.cache,
)
```

Figure failures must not increment analysis `failed` or `error_categories`, and must not affect
`max_failure_ratio`.

- [ ] **Step 5: Wire the CLI with a guaranteed close**

Import `ArxivFigureClient`, construct it with the same User-Agent and request delay, and wrap
`run_daily` in:

```python
figure_client = ArxivFigureClient(
    user_agent=user_agent,
    request_delay_seconds=config.arxiv.request_delay_seconds,
)
try:
    report = run_daily(
        config=config,
        data_dir=data_dir,
        fetcher=ArxivClient(
            user_agent=user_agent,
            request_delay_seconds=config.arxiv.request_delay_seconds,
        ),
        analysis_client=DeepSeekClient(
            api_key=api_key,
            model=model,
            max_output_tokens=config.analysis.max_output_tokens,
        ),
        figure_fetcher=figure_client,
        prompt=config.analysis.prompt_path(prompt_path.parent).read_text(encoding="utf-8"),
        lookback_days=lookback_days,
        threshold=threshold,
        force_ids=force_arxiv_id or [],
        dry_run=dry_run,
        now=datetime.now(UTC),
    )
finally:
    figure_client.close()
```

Keep the existing CLI options and JSON report. The report automatically includes the new counters
through `stats.model_dump(mode="json")`.

- [ ] **Step 6: Add cache reuse and below-threshold tests**

Add:

```python
def test_second_run_reuses_figure_cache(tmp_path: Path) -> None:
    config = load_config(Path("config/topics.yaml"))
    figure_fetcher = FakeFigureFetcher()
    kwargs = {
        "config": config,
        "data_dir": tmp_path,
        "fetcher": FakeFetcher([raw_paper()]),
        "analysis_client": CountingClient(),
        "figure_fetcher": figure_fetcher,
        "prompt": Path("prompts/analysis-v1.md").read_text(encoding="utf-8"),
        "lookback_days": 3,
        "threshold": 6,
        "force_ids": [],
        "dry_run": False,
        "now": datetime(2026, 7, 29, 2, 30, tzinfo=UTC),
    }
    first = run_daily(**kwargs)
    second = run_daily(**kwargs)
    assert first.stats.figure_requests == 1
    assert second.stats.figure_cache_hits == 1
    assert figure_fetcher.calls == 1


def test_below_threshold_paper_does_not_fetch_figures(tmp_path: Path) -> None:
    figure_fetcher = FakeFigureFetcher()
    report = run_daily(
        config=load_config(Path("config/topics.yaml")),
        data_dir=tmp_path,
        fetcher=FakeFetcher([raw_paper()]),
        analysis_client=FakeClient(score=5),
        figure_fetcher=figure_fetcher,
        prompt=Path("prompts/analysis-v1.md").read_text(encoding="utf-8"),
        lookback_days=3,
        threshold=6,
        force_ids=[],
        dry_run=True,
        now=datetime(2026, 7, 29, 2, 30, tzinfo=UTC),
    )
    assert report.published == []
    assert figure_fetcher.calls == 0


def test_new_paper_version_uses_new_figure_cache_key(tmp_path: Path) -> None:
    config = load_config(Path("config/topics.yaml"))
    figure_fetcher = FakeFigureFetcher()
    base_kwargs = {
        "config": config,
        "data_dir": tmp_path,
        "analysis_client": CountingClient(),
        "figure_fetcher": figure_fetcher,
        "prompt": Path("prompts/analysis-v1.md").read_text(encoding="utf-8"),
        "lookback_days": 3,
        "threshold": 6,
        "force_ids": [],
        "dry_run": False,
        "now": datetime(2026, 7, 29, 2, 30, tzinfo=UTC),
    }
    run_daily(fetcher=FakeFetcher([raw_paper()]), **base_kwargs)
    version_two = raw_paper().model_copy(update={"version": 2})
    run_daily(fetcher=FakeFetcher([version_two]), **base_kwargs)
    assert figure_fetcher.calls == 2


def test_dry_run_does_not_write_figure_cache(tmp_path: Path) -> None:
    run_daily(
        config=load_config(Path("config/topics.yaml")),
        data_dir=tmp_path,
        fetcher=FakeFetcher([raw_paper()]),
        analysis_client=CountingClient(),
        figure_fetcher=FakeFigureFetcher(),
        prompt=Path("prompts/analysis-v1.md").read_text(encoding="utf-8"),
        lookback_days=3,
        threshold=6,
        force_ids=[],
        dry_run=True,
        now=datetime(2026, 7, 29, 2, 30, tzinfo=UTC),
    )
    assert not (tmp_path / "cache/figures.json").exists()
```

- [ ] **Step 7: Run pipeline and CLI gates**

```bash
uv run pytest tests/test_pipeline.py tests/test_cli.py tests/test_figures.py tests/test_storage.py -v
uv run pytest
uv run ruff check src tests
uv run mypy
git diff --check
```

Expected: all commands exit 0.

- [ ] **Step 8: Commit pipeline enrichment**

```bash
git add src/vla_wam_daily/pipeline.py src/vla_wam_daily/cli.py tests/test_pipeline.py tests/test_cli.py
git commit -m "feat: enrich published papers with figures"
```

## Figure Task 5: Render the remote gallery and download fallback

**Prerequisite:** Original Task 11 has created the Astro site and fixture build.

**Files:**

- Modify: `web/src/lib/schema.ts`
- Modify: `web/src/lib/data.test.ts`
- Create: `web/src/components/FigureGallery.astro`
- Modify: `web/src/components/PaperCard.astro`
- Modify: `web/src/pages/papers/[id].astro`
- Modify: `web/src/layouts/BaseLayout.astro`
- Modify: `web/src/styles/global.css`

- [ ] **Step 1: Write failing TypeScript schema tests**

Extend the paper fixture in `web/src/lib/data.test.ts` with:

```typescript
figure_gallery: {
  status: "available",
  html_url: "https://arxiv.org/html/2607.12345v1",
  checked_at: "2026-07-29T02:30:00Z",
  figures: [
    {
      number: 1,
      label: "Figure 1",
      caption: "The model architecture.",
      image_urls: ["https://arxiv.org/html/2607.12345v1/x1.png"],
      source_url: "https://arxiv.org/html/2607.12345v1#S1.F1",
      source: "arxiv_html"
    },
    {
      number: 2,
      label: "Figure 2",
      caption: "Robot evaluation environments.",
      image_urls: ["https://arxiv.org/html/2607.12345v1/x2.png"],
      source_url: "https://arxiv.org/html/2607.12345v1#S2.F2",
      source: "arxiv_html"
    }
  ]
}
```

Import `figureGallerySchema` and add:

```typescript
it("rejects inconsistent Figure status and external image hosts", () => {
  const base = {
    status: "available",
    html_url: "https://arxiv.org/html/2607.12345v1",
    checked_at: "2026-07-29T02:30:00Z",
    figures: []
  };
  expect(() => figureGallerySchema.parse(base)).toThrow();
  expect(() =>
    figureGallerySchema.parse({
      ...base,
      figures: [
        {
          number: 1,
          label: "Figure 1",
          caption: "Caption",
          image_urls: ["https://example.com/x1.png"],
          source_url: "https://arxiv.org/html/2607.12345v1#F1",
          source: "arxiv_html"
        }
      ]
    })
  ).toThrow();
});
```

- [ ] **Step 2: Run Vitest to verify RED**

```bash
cd web
pnpm test -- src/lib/data.test.ts
```

Expected: the inferred `Paper` type/schema does not expose or validate `figure_gallery`.

- [ ] **Step 3: Mirror the strict Figure schema in Zod**

Add to `web/src/lib/schema.ts`:

```typescript
const arxivHttpsUrl = z
  .url()
  .refine((value) => {
    const url = new URL(value);
    return (
      url.protocol === "https:" &&
      (url.hostname === "arxiv.org" || url.hostname === "www.arxiv.org")
    );
  }, "Figure URL must use HTTPS on arxiv.org");

export const figureAssetSchema = z.object({
  number: z.union([z.literal(1), z.literal(2)]),
  label: z.string().min(1),
  caption: z.string().min(1),
  image_urls: z.array(arxivHttpsUrl).min(1),
  source_url: arxivHttpsUrl,
  source: z.literal("arxiv_html")
});

export const figureGallerySchema = z
  .object({
    status: z.enum(["available", "html_unavailable", "not_found", "fetch_failed"]),
    html_url: arxivHttpsUrl,
    figures: z.array(figureAssetSchema).max(2),
    checked_at: z.iso.datetime()
  })
  .superRefine((gallery, context) => {
    const numbers = gallery.figures.map((figure) => figure.number);
    if (new Set(numbers).size !== numbers.length) {
      context.addIssue({ code: "custom", message: "Figure numbers must be unique" });
    }
    if (gallery.status === "available" && gallery.figures.length === 0) {
      context.addIssue({ code: "custom", message: "Available gallery must contain figures" });
    }
    if (gallery.status !== "available" && gallery.figures.length > 0) {
      context.addIssue({ code: "custom", message: "Unavailable gallery cannot contain figures" });
    }
  });
```

Add `figure_gallery: figureGallerySchema` to `paperSchema`. Add the five nonnegative Figure counters
to the stats schema.

- [ ] **Step 4: Create the gallery component**

Create `web/src/components/FigureGallery.astro`:

```astro
---
import type { Paper } from "../lib/schema";

interface Props {
  gallery: Paper["figure_gallery"];
  arxivId: string;
  pdfUrl: string;
}

const { gallery, arxivId, pdfUrl } = Astro.props;

function extensionFor(url: string): string {
  const extension = new URL(url).pathname.split(".").pop()?.toLowerCase() ?? "png";
  return /^[a-z0-9]{2,5}$/.test(extension) ? extension : "png";
}
---

<section class="figure-gallery" aria-labelledby={`figures-${arxivId}`}>
  <div class="figure-gallery__heading">
    <h3 id={`figures-${arxivId}`}>Fig. 1 &amp; Fig. 2</h3>
    <a href={gallery.html_url} target="_blank" rel="noopener noreferrer">arXiv HTML</a>
  </div>

  {gallery.status === "available" ? (
    <div class="figure-gallery__grid">
      {gallery.figures.map((figure) => (
        <figure class="remote-figure">
          <div class="remote-figure__panels">
            {figure.image_urls.map((imageUrl, index) => (
              <div class="remote-figure__panel">
                <img
                  src={imageUrl}
                  alt={`${figure.label}: ${figure.caption}`}
                  loading="lazy"
                  decoding="async"
                  referrerpolicy="no-referrer"
                  data-figure-image
                />
                <p class="figure-load-error" data-figure-error hidden>
                  图片暂时无法加载。<a href={pdfUrl}>查看 PDF</a>
                </p>
                <div class="remote-figure__actions">
                  <a href={imageUrl} target="_blank" rel="noopener noreferrer">查看原图</a>
                  <button
                    type="button"
                    data-figure-download
                    data-download-url={imageUrl}
                    data-download-name={`${arxivId}-figure-${figure.number}-panel-${index + 1}.${extensionFor(imageUrl)}`}
                  >
                    下载原图
                  </button>
                </div>
              </div>
            ))}
          </div>
          <figcaption>
            <strong>{figure.label}.</strong> {figure.caption}
            <a href={figure.source_url} target="_blank" rel="noopener noreferrer">来源</a>
          </figcaption>
        </figure>
      ))}
    </div>
  ) : (
    <div class="figure-gallery__fallback" role="status">
      <p>
        {gallery.status === "html_unavailable"
          ? "arXiv 暂无 HTML 版本。"
          : gallery.status === "not_found"
            ? "未能在 arXiv HTML 中识别 Fig. 1 / Fig. 2。"
            : "图片信息暂时获取失败；下一次每日运行将重试。"}
      </p>
      <a href={pdfUrl}>查看 PDF</a>
    </div>
  )}
  <p class="figure-gallery__notice">
    图片由浏览器直接从 arXiv 加载，版权归原作者或权利人所有。
  </p>
</section>

<script>
  function openOriginal(url: string): void {
    const opened = window.open(url, "_blank", "noopener,noreferrer");
    if (opened) opened.opener = null;
    else window.location.assign(url);
  }

  document.querySelectorAll<HTMLElement>("[data-figure-image]").forEach((image) => {
    image.addEventListener("error", () => {
      image.hidden = true;
      image.parentElement?.querySelector<HTMLElement>("[data-figure-error]")?.removeAttribute("hidden");
    });
  });

  document.querySelectorAll<HTMLButtonElement>("[data-figure-download]").forEach((button) => {
    button.addEventListener("click", async () => {
      const url = button.dataset.downloadUrl;
      const filename = button.dataset.downloadName;
      if (!url || !filename) return;
      button.disabled = true;
      try {
        const response = await fetch(url, { mode: "cors" });
        if (!response.ok) throw new Error(`Image request returned ${response.status}`);
        const objectUrl = URL.createObjectURL(await response.blob());
        const link = document.createElement("a");
        link.href = objectUrl;
        link.download = filename;
        link.click();
        setTimeout(() => URL.revokeObjectURL(objectUrl), 1_000);
      } catch {
        openOriginal(url);
      } finally {
        button.disabled = false;
      }
    });
  });
</script>
```

- [ ] **Step 5: Place the gallery in card details and open it on paper pages**

In `PaperCard.astro`, import `FigureGallery`, extend props, and destructure:

```astro
interface Props { paper: Paper; compact?: boolean; expanded?: boolean }
const { paper, compact = false, expanded = false } = Astro.props;
```

Replace the existing non-compact `<details>` with:

```astro
{!compact && (
  <details open={expanded}>
    <summary>查看详情与 Fig. 1/2</summary>
    <dl>
      <dt>核心贡献</dt><dd>{paper.analysis.main_contribution}</dd>
      <dt>方法</dt><dd>{paper.analysis.method}</dd>
      <dt>实验结果</dt><dd>{paper.analysis.key_results}</dd>
      <dt>局限</dt><dd>{paper.analysis.limitations}</dd>
      <dt>与 VLA/WAM 的关系</dt><dd>{paper.analysis.relation_to_vla_wam}</dd>
    </dl>
    <FigureGallery
      gallery={paper.figure_gallery}
      arxivId={paper.arxiv_id}
      pdfUrl={paper.resources.pdf_url}
    />
  </details>
)}
```

In `papers/[id].astro`, render:

```astro
<PaperCard paper={paper} expanded />
```

This makes the Figure gallery visible immediately after a reader follows a paper title, while the
home page keeps remote images lazy and collapsed until requested.

- [ ] **Step 6: Add responsive and failure-state styles**

Append to `global.css`:

```css
.figure-gallery {
  margin-top: 1.25rem;
  padding-top: 1rem;
  border-top: 1px solid var(--line);
}
.figure-gallery__heading,
.remote-figure__actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 0.6rem;
}
.figure-gallery__grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 1rem;
}
.remote-figure {
  min-width: 0;
  margin: 0;
  border: 1px solid var(--line);
  border-radius: 0.8rem;
  overflow: hidden;
}
.remote-figure__panels {
  display: grid;
  gap: 0.5rem;
  background: white;
}
.remote-figure__panel img {
  display: block;
  width: 100%;
  height: auto;
  max-height: 34rem;
  object-fit: contain;
}
.remote-figure__actions,
.remote-figure figcaption,
.figure-gallery__fallback {
  padding: 0.75rem;
}
.remote-figure__actions button {
  border: 1px solid var(--line);
  border-radius: 0.5rem;
  color: var(--ink);
  background: var(--paper);
  padding: 0.4rem 0.65rem;
  cursor: pointer;
}
.remote-figure__actions button:disabled { opacity: 0.55; cursor: wait; }
.figure-load-error { color: var(--accent); padding: 1rem; }
.figure-gallery__notice { color: var(--muted); font-size: 0.85rem; }
@media (max-width: 48rem) {
  .figure-gallery__grid { grid-template-columns: 1fr; }
}
```

Update the footer in `BaseLayout.astro`:

```astro
<p>
  Paper metadata from arXiv · AI analysis is based on titles and abstracts only.
  Figure images load directly from arXiv and remain subject to each paper's license.
</p>
```

- [ ] **Step 7: Run web unit/build/format gates**

```bash
cd web
pnpm test
VLA_WAM_DATA_DIR=../tests/fixtures/data pnpm build
pnpm format:check
rg -n "Fig\\. 1 &amp; Fig\\. 2|figure-gallery" dist/papers
```

Expected: all gates pass and the built paper page contains the gallery markup.

- [ ] **Step 8: Commit the gallery UI**

```bash
git add web/src
git commit -m "feat: show remote arXiv figure gallery"
```

## Figure Task 6: Verify downloads, degradation, docs, and no image storage

**Prerequisites:** Original Task 14 has created Playwright tests and original Task 16 has created
the public README.

**Files:**

- Modify: `web/tests/site.spec.ts`
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-07-27-vla-wam-daily-design.md`
- Modify: `docs/superpowers/specs/2026-07-29-paper-figure-display-design.md`

- [ ] **Step 1: Add Playwright gallery tests**

Append:

```typescript
const onePixelPng = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
  "base64"
);

test("paper detail immediately shows remote Figure 1 and Figure 2", async ({ page }) => {
  await page.route("https://arxiv.org/html/**", async (route) => {
    await route.fulfill({ status: 200, contentType: "image/png", body: onePixelPng });
  });
  await page.goto("/papers/2607.12345/");
  await expect(page.getByRole("heading", { name: "Fig. 1 & Fig. 2" })).toBeVisible();
  await expect(page.getByText("The model architecture.")).toBeVisible();
  await expect(page.getByText("Robot evaluation environments.")).toBeVisible();
  await expect(page.locator("[data-figure-image]")).toHaveCount(2);
});

test("download button saves the arXiv image with a stable name", async ({ page }) => {
  await page.route("https://arxiv.org/html/**", async (route) => {
    await route.fulfill({ status: 200, contentType: "image/png", body: onePixelPng });
  });
  await page.goto("/papers/2607.12345/");
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载原图" }).first().click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("2607.12345-figure-1-panel-1.png");
});

test("broken remote images show the PDF fallback", async ({ page }) => {
  await page.route("https://arxiv.org/html/**", async (route) => {
    await route.abort("failed");
  });
  await page.goto("/papers/2607.12345/");
  await expect(page.getByText("图片暂时无法加载。").first()).toBeVisible();
  await expect(page.getByRole("link", { name: "查看 PDF" }).first()).toBeVisible();
});

test("figure gallery is one column on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route("https://arxiv.org/html/**", async (route) => {
    await route.fulfill({ status: 200, contentType: "image/png", body: onePixelPng });
  });
  await page.goto("/papers/2607.12345/");
  await expect(page.locator(".figure-gallery__grid")).toHaveCSS("grid-template-columns", /.+/);
  const boxes = await page.locator(".remote-figure").evaluateAll((nodes) =>
    nodes.map((node) => node.getBoundingClientRect().left)
  );
  expect(new Set(boxes.map(Math.round)).size).toBe(1);
});
```

Add this parameterized browser test for the three unavailable states generated by
`make_figure_fixture_records`:

```typescript
for (const [id, message] of [
  ["2607.20001", "arXiv 暂无 HTML 版本。"],
  ["2607.20002", "未能在 arXiv HTML 中识别 Fig. 1 / Fig. 2。"],
  ["2607.20003", "图片信息暂时获取失败；下一次每日运行将重试。"]
] as const) {
  test(`Figure fallback for ${id}`, async ({ page }) => {
    await page.goto(`/papers/${id}/`);
    await expect(page.getByText(message)).toBeVisible();
    await expect(page.getByRole("link", { name: "查看 PDF" })).toBeVisible();
  });
}
```

- [ ] **Step 2: Document behavior and licensing**

Add this README section:

```markdown
## Figure 1 and Figure 2

For papers with an arXiv HTML version, the detail view parses and displays Figure 1 and
Figure 2 with their original captions. Images are loaded directly from `arxiv.org`; the
repository and GitHub Pages artifact contain URL/caption metadata only.

The download control fetches the original arXiv image in the browser. If cross-origin or
network policy blocks that request, it opens the original image in a new tab instead.
When arXiv HTML or a target Figure is unavailable, the paper remains published and the site
falls back to its HTML/PDF links.

Figure copyrights remain with their authors or other rightsholders. Viewing, downloading,
and reuse are subject to the license shown on each arXiv paper.
```

Add this paragraph to the main design document's pipeline section:

```markdown
论文通过发布阈值后，流水线按 arXiv ID 和版本抓取 arXiv HTML，只解析 Figure 1/2
的远程 URL 与 caption。图片字节不进入仓库；HTML/图片失败不会阻塞论文发布。
```

Add these metric names to its operational metrics list:

```text
figure_cache_hits, figure_requests, figure_available, figure_unavailable, figure_failed
```

Do not change either design status to “implemented” until final acceptance.

- [ ] **Step 3: Verify the repository and Pages artifact contain no paper images**

Run:

```bash
find data web -type f \\( -iname '*.png' -o -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.webp' -o -iname '*.svg' \\) -print
```

Expected: no paper Figure asset is listed. Repository-owned icons are allowed only if their paths
are explicitly documented; this plan adds none.

Build and scan remote references:

```bash
cd web
VLA_WAM_DATA_DIR=../tests/fixtures/data pnpm build
pnpm test:e2e
rg -n "https://arxiv.org/html/.+\\.(png|svg)" dist/papers
cd ..
```

Expected: Playwright passes and built pages reference arXiv images remotely.

- [ ] **Step 4: Run final cross-stack gates**

```bash
uv run pytest
uv run ruff check src tests
uv run mypy
cd web
pnpm test
VLA_WAM_DATA_DIR=../tests/fixtures/data pnpm build
pnpm format:check
pnpm test:e2e
cd ..
git diff --check
```

Expected: all commands exit 0.

- [ ] **Step 5: Commit Figure acceptance coverage**

```bash
git add web/tests/site.spec.ts README.md docs/superpowers/specs
git commit -m "test: verify remote figure reading flow"
```

## Final amendment verification

Before original Task 17 publishes the repository:

```bash
uv run vla-wam-daily daily --force-arxiv-id 2606.30552 --dry-run
```

With `DEEPSEEK_API_KEY` set, expected report properties are:

- `published_ids` contains `2606.30552`.
- `stats.figure_requests` is 1 on a cold cache.
- `stats.figure_available` is 1.
- The dry run does not modify tracked `data/`.

Confirm the live parser result independently:

```bash
uv run python -c "from datetime import UTC, datetime; from vla_wam_daily.figures import ArxivFigureClient; c=ArxivFigureClient(user_agent='VLA-WAM-Daily/0.1 (+https://github.com/)', request_delay_seconds=0); g=c.fetch('2606.30552',2,datetime.now(UTC)); print(g.model_dump_json(indent=2)); c.close(); assert [str(f.image_urls[0]) for f in g.figures] == ['https://arxiv.org/html/2606.30552v2/x1.png','https://arxiv.org/html/2606.30552v2/x2.png']"
```

Expected: Figure 1 and Figure 2 resolve to those current arXiv resources. Do not hardcode the
verified URLs in production code.
