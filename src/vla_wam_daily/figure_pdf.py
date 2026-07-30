import io
import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, TypeGuard, cast
from urllib.parse import urljoin, urlsplit

import httpx
import pdfplumber
from pdfplumber.page import Page

from vla_wam_daily.figure_pdf_render import PdfCrop, render_pdf_page_to_png
from vla_wam_daily.figure_recovery_types import (
    RecoveredFigure,
    TransientRecoveryError,
)
from vla_wam_daily.figures import figure_cache_key
from vla_wam_daily.models import ARXIV_FIGURE_HOSTS

_TARGET_CAPTION_RE = re.compile(
    r"^(?:(?:figure)\s+1|(?:fig\.)\s*1)(?!\d|\.\d)\s*[:.]\s*",
    re.IGNORECASE,
)
_ANY_CAPTION_RE = re.compile(
    r"^(?:figure|fig\.)\s*\d+(?!\d|\.\d)\s*[:.]",
    re.IGNORECASE,
)
_LINE_TOLERANCE = 3.0
_MAX_CAPTION_CONTINUATION_LINES = 2
_MAX_CAPTION_LINE_GAP = 10.0
_MAX_CROP_PAGE_RATIO = 0.85


@dataclass(frozen=True)
class _Box:
    left: float
    top: float
    right: float
    bottom: float

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.bottom - self.top

    @property
    def area(self) -> float:
        return self.width * self.height

    def union(self, other: "_Box") -> "_Box":
        return _Box(
            min(self.left, other.left),
            min(self.top, other.top),
            max(self.right, other.right),
            max(self.bottom, other.bottom),
        )


@dataclass(frozen=True)
class _TextLine:
    text: str
    box: _Box


@dataclass(frozen=True)
class _Caption:
    text: str
    box: _Box


def _finite_number(value: object) -> TypeGuard[int | float]:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _box(
    left: object,
    top: object,
    right: object,
    bottom: object,
) -> _Box | None:
    if not all(_finite_number(value) for value in (left, top, right, bottom)):
        return None
    result = _Box(
        float(cast(int | float, left)),
        float(cast(int | float, top)),
        float(cast(int | float, right)),
        float(cast(int | float, bottom)),
    )
    return result if result.width > 0 and result.height >= 0 else None


def _normalize_text(value: str) -> str | None:
    if any(
        character not in "\t\n\r"
        and unicodedata.category(character).startswith("C")
        for character in value
    ):
        return None
    normalized = " ".join(value.split())
    return normalized or None


def _page_lines(page: Page) -> list[_TextLine]:
    words: list[dict[str, Any]] = page.extract_words(
        x_tolerance=3,
        y_tolerance=3,
        keep_blank_chars=False,
        use_text_flow=False,
    )
    valid_words: list[tuple[str, _Box]] = []
    for word in words:
        text = word.get("text")
        word_box = _box(
            word.get("x0"),
            word.get("top"),
            word.get("x1"),
            word.get("bottom"),
        )
        if (
            not isinstance(text, str)
            or not text
            or word_box is None
            or word.get("upright", True) is not True
        ):
            continue
        valid_words.append((text, word_box))
    valid_words.sort(key=lambda item: (item[1].top, item[1].left))

    groups: list[list[tuple[str, _Box]]] = []
    for item in valid_words:
        if not groups or abs(item[1].top - groups[-1][0][1].top) > _LINE_TOLERANCE:
            groups.append([item])
        else:
            groups[-1].append(item)

    lines: list[_TextLine] = []
    for group in groups:
        group.sort(key=lambda item: item[1].left)
        line_box = group[0][1]
        for _text, word_box in group[1:]:
            line_box = line_box.union(word_box)
        normalized = _normalize_text(" ".join(text for text, _box_value in group))
        if normalized is not None:
            lines.append(_TextLine(normalized, line_box))
    return lines


def _captions(lines: list[_TextLine]) -> tuple[list[_Caption], list[_TextLine]]:
    targets: list[_Caption] = []
    neighbor_lines = [line for line in lines if _ANY_CAPTION_RE.match(line.text)]
    for index, line in enumerate(lines):
        match = _TARGET_CAPTION_RE.match(line.text)
        if match is None:
            continue
        parts = [line.text[match.end() :].strip()]
        caption_box = line.box
        previous = line
        for continuation in lines[index + 1 : index + 1 + _MAX_CAPTION_CONTINUATION_LINES]:
            if (
                _ANY_CAPTION_RE.match(continuation.text)
                or continuation.box.top - previous.box.bottom > _MAX_CAPTION_LINE_GAP
                or abs(continuation.box.left - line.box.left) > 36
            ):
                break
            parts.append(continuation.text)
            caption_box = caption_box.union(continuation.box)
            previous = continuation
        normalized = _normalize_text(" ".join(parts))
        if normalized is not None:
            targets.append(_Caption(normalized, caption_box))
    return targets, neighbor_lines


def _horizontal_overlap(first: _Box, second: _Box) -> float:
    return max(0.0, min(first.right, second.right) - max(first.left, second.left))


def _boxes_near(first: _Box, second: _Box, gap: float) -> bool:
    return not (
        first.right + gap < second.left
        or second.right + gap < first.left
        or first.bottom + gap < second.top
        or second.bottom + gap < first.top
    )


def _merge_clusters(boxes: list[_Box], gap: float) -> list[_Box]:
    clusters = list(boxes)
    changed = True
    while changed:
        changed = False
        merged: list[_Box] = []
        while clusters:
            current = clusters.pop()
            index = 0
            while index < len(clusters):
                if _boxes_near(current, clusters[index], gap):
                    current = current.union(clusters.pop(index))
                    changed = True
                    index = 0
                else:
                    index += 1
            merged.append(current)
        clusters = merged
    return clusters


def _visual_boxes(
    page: Page,
    *,
    caption: _Caption,
    neighbors: list[_TextLine],
    page_margin: float,
    max_vertical_distance: float,
    min_visual_area: float,
) -> list[_Box]:
    page_width = float(page.width)
    page_height = float(page.height)
    page_area = page_width * page_height
    boxes: list[_Box] = []
    for collection in (page.images, page.rects, page.curves, page.lines):
        for item in collection:
            candidate = _box(
                item.get("x0"),
                item.get("top"),
                item.get("x1"),
                item.get("bottom"),
            )
            if candidate is None:
                continue
            if candidate.height == 0:
                line_width = item.get("linewidth", 1)
                thickness = (
                    float(line_width)
                    if _finite_number(line_width) and float(line_width) > 0
                    else 1.0
                )
                candidate = _Box(
                    candidate.left,
                    candidate.top,
                    candidate.right,
                    candidate.bottom + thickness,
                )
            if (
                candidate.left < page_margin
                or candidate.top < page_margin
                or candidate.right > page_width - page_margin
                or candidate.bottom > page_height - page_margin
                or candidate.area < min_visual_area
                or candidate.area >= page_area * _MAX_CROP_PAGE_RATIO
                or candidate.bottom > caption.box.top
                or caption.box.top - candidate.bottom > max_vertical_distance
                or _horizontal_overlap(candidate, caption.box) <= 0
            ):
                continue
            if any(
                neighbor is not None
                and neighbor.box.top > candidate.bottom
                and neighbor.box.top < caption.box.top
                and not _TARGET_CAPTION_RE.match(neighbor.text)
                for neighbor in neighbors
            ):
                continue
            boxes.append(candidate)
    return boxes


def _crop_for_page(
    page: Page,
    *,
    page_margin: float,
    max_vertical_distance: float,
    min_visual_area: float,
    max_cluster_gap: float,
    crop_padding: float,
) -> tuple[str, PdfCrop] | None:
    lines = _page_lines(page)
    captions, neighbors = _captions(lines)
    matches: list[tuple[str, PdfCrop]] = []
    for caption in captions:
        clusters = _merge_clusters(
            _visual_boxes(
                page,
                caption=caption,
                neighbors=neighbors,
                page_margin=page_margin,
                max_vertical_distance=max_vertical_distance,
                min_visual_area=min_visual_area,
            ),
            max_cluster_gap,
        )
        if len(clusters) != 1:
            continue
        crop_box = clusters[0].union(caption.box)
        crop = (
            max(0.0, crop_box.left - crop_padding),
            max(0.0, crop_box.top - crop_padding),
            min(float(page.width), crop_box.right + crop_padding),
            min(float(page.height), crop_box.bottom + crop_padding),
        )
        crop_area = (crop[2] - crop[0]) * (crop[3] - crop[1])
        if (
            crop[0] >= crop[2]
            or crop[1] >= crop[3]
            or crop_area >= float(page.width) * float(page.height) * _MAX_CROP_PAGE_RATIO
        ):
            continue
        matches.append((caption.text, crop))
    return matches[0] if len(matches) == 1 else None


def _safe_pdf_redirect(
    current_url: str,
    location: str,
    *,
    expected_path: str,
) -> str | None:
    if not location.strip():
        return None
    candidate = urljoin(current_url, location)
    parsed = urlsplit(candidate)
    try:
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or parsed.hostname not in ARXIV_FIGURE_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.query
        or parsed.fragment
        or parsed.path != expected_path
    ):
        return None
    return candidate


class ArxivPdfFigureExtractor:
    def __init__(
        self,
        *,
        user_agent: str,
        timeout_seconds: float = 30,
        max_pdf_bytes: int = 50_000_000,
        max_redirects: int = 3,
        max_pages: int = 100,
        max_objects_per_page: int = 100_000,
        max_page_dimension_points: int = 20_000,
        max_vertical_distance: float = 72,
        min_visual_area: float = 900,
        max_cluster_gap: float = 12,
        page_margin: float = 6,
        crop_padding: float = 6,
        resolution: int = 300,
        max_crop_pixels: int = 40_000_000,
        max_output_dimension: int = 10_000,
        max_output_bytes: int = 15_000_000,
        client: httpx.Client | None = None,
    ) -> None:
        if not isinstance(user_agent, str) or not user_agent.strip():
            raise ValueError("user_agent must not be blank")
        if (
            type(timeout_seconds) not in (int, float)
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
            or timeout_seconds > 300
        ):
            raise ValueError("timeout_seconds must be in (0, 300]")
        integer_bounds = {
            "max_pdf_bytes": (max_pdf_bytes, 250_000_000),
            "max_redirects": (max_redirects, 10),
            "max_pages": (max_pages, 10_000),
            "max_objects_per_page": (max_objects_per_page, 1_000_000),
            "max_page_dimension_points": (max_page_dimension_points, 100_000),
            "resolution": (resolution, 600),
            "max_crop_pixels": (max_crop_pixels, 100_000_000),
            "max_output_dimension": (max_output_dimension, 30_000),
            "max_output_bytes": (max_output_bytes, 100_000_000),
        }
        if any(
            type(value) is not int
            or value < (0 if name == "max_redirects" else 1)
            or value > maximum
            for name, (value, maximum) in integer_bounds.items()
        ):
            raise ValueError("PDF extraction integer limits are outside safe bounds")
        float_bounds = {
            "max_vertical_distance": (max_vertical_distance, False),
            "min_visual_area": (min_visual_area, False),
            "max_cluster_gap": (max_cluster_gap, True),
            "page_margin": (page_margin, True),
            "crop_padding": (crop_padding, True),
        }
        if any(
            type(value) not in (int, float)
            or not math.isfinite(value)
            or value < 0
            or (not allow_zero and value == 0)
            or value > 100_000
            for value, allow_zero in float_bounds.values()
        ):
            raise ValueError("PDF geometry limits are outside safe bounds")

        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self.max_pdf_bytes = max_pdf_bytes
        self.max_redirects = max_redirects
        self.max_pages = max_pages
        self.max_objects_per_page = max_objects_per_page
        self.max_page_dimension_points = max_page_dimension_points
        self.max_vertical_distance = float(max_vertical_distance)
        self.min_visual_area = float(min_visual_area)
        self.max_cluster_gap = float(max_cluster_gap)
        self.page_margin = float(page_margin)
        self.crop_padding = float(crop_padding)
        self.resolution = resolution
        self.max_crop_pixels = max_crop_pixels
        self.max_output_dimension = max_output_dimension
        self.max_output_bytes = max_output_bytes
        self.client = client or httpx.Client()
        self._owns_client = client is None

    def __enter__(self) -> "ArxivPdfFigureExtractor":
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

    def _download(self, source_url: str) -> bytes | None:
        expected_path = urlsplit(source_url).path
        current_url = source_url
        redirects_followed = 0
        try:
            while True:
                with self.client.stream(
                    "GET",
                    current_url,
                    headers={"User-Agent": self.user_agent},
                    timeout=self.timeout_seconds,
                    follow_redirects=False,
                ) as response:
                    if 300 <= response.status_code < 400:
                        if redirects_followed >= self.max_redirects:
                            return None
                        redirect = _safe_pdf_redirect(
                            current_url,
                            response.headers.get("location", ""),
                            expected_path=expected_path,
                        )
                        if redirect is None:
                            return None
                        current_url = redirect
                        redirects_followed += 1
                        continue
                    if response.status_code == 404:
                        return None
                    if response.status_code in (408, 425, 429) or response.status_code >= 500:
                        raise TransientRecoveryError(
                            f"arXiv PDF returned {response.status_code}"
                        )
                    if response.status_code >= 400:
                        return None
                    declared_text = response.headers.get("content-length")
                    if declared_text is not None:
                        try:
                            declared = int(declared_text)
                        except ValueError:
                            return None
                        if declared < 0 or declared > self.max_pdf_bytes:
                            return None
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        if len(chunk) > self.max_pdf_bytes - len(body):
                            return None
                        body.extend(chunk)
                    return bytes(body) or None
        except httpx.RequestError as error:
            raise TransientRecoveryError("arXiv PDF request failed") from error

    def _extract_pdf(self, body: bytes) -> tuple[str, bytes] | None:
        if not body.startswith(b"%PDF-"):
            return None
        try:
            with pdfplumber.open(io.BytesIO(body), repair=False) as pdf:
                if not pdf.pages or len(pdf.pages) > self.max_pages:
                    return None
                detected: list[tuple[Page, str, PdfCrop]] = []
                for page in pdf.pages:
                    if (
                        not _finite_number(page.width)
                        or not _finite_number(page.height)
                        or page.width <= 0
                        or page.height <= 0
                        or page.width > self.max_page_dimension_points
                        or page.height > self.max_page_dimension_points
                    ):
                        return None
                    object_count = sum(len(objects) for objects in page.objects.values())
                    if object_count > self.max_objects_per_page:
                        return None
                    page_crop = _crop_for_page(
                        page,
                        page_margin=self.page_margin,
                        max_vertical_distance=self.max_vertical_distance,
                        min_visual_area=self.min_visual_area,
                        max_cluster_gap=self.max_cluster_gap,
                        crop_padding=self.crop_padding,
                    )
                    if page_crop is not None:
                        detected.append((page, page_crop[0], page_crop[1]))
                if len(detected) != 1:
                    return None
                page, caption, render_crop = detected[0]
                content = render_pdf_page_to_png(
                    page,
                    crop=render_crop,
                    resolution=self.resolution,
                    max_output_dimension=self.max_output_dimension,
                    max_output_pixels=self.max_crop_pixels,
                    max_output_bytes=self.max_output_bytes,
                )
                return None if content is None else (caption, content)
        except Exception:
            return None

    def extract(self, arxiv_id: str, version: int) -> RecoveredFigure | None:
        figure_cache_key(arxiv_id, version)
        source_url = f"https://arxiv.org/pdf/{arxiv_id}v{version}"
        body = self._download(source_url)
        if body is None:
            return None
        extracted = self._extract_pdf(body)
        if extracted is None:
            return None
        caption, content = extracted
        return RecoveredFigure(
            caption=caption,
            extension="png",
            content=content,
            source_url=source_url,
            source="arxiv_pdf",
        )
