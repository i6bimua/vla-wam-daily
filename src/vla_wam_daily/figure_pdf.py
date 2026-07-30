import logging
import math
import re
import unicodedata
from dataclasses import dataclass
from typing import TypeGuard
from urllib.parse import urljoin, urlsplit

import httpx
import pypdfium2 as pdfium
from pypdfium2 import PdfiumError

from vla_wam_daily.figure_pdf_render import (
    PdfCrop,
    render_pdf_page_to_png,
    visible_page_bbox,
)
from vla_wam_daily.figure_recovery_types import (
    RecoveredFigure,
    TransientRecoveryError,
)
from vla_wam_daily.figures import figure_cache_key
from vla_wam_daily.models import ARXIV_FIGURE_HOSTS

LOGGER = logging.getLogger(__name__)
_TARGET_CAPTION_RE = re.compile(
    r"^(?:(?:figure)\s+1|(?:fig\.)\s*1)(?!\d|\.\d)\s*[:.]\s*",
    re.IGNORECASE,
)
_ANY_CAPTION_RE = re.compile(
    r"^(?:figure|fig\.)\s*\d+(?!\d|\.\d)\s*[:.]",
    re.IGNORECASE,
)
_MAX_CAPTION_CONTINUATION_LINES = 2
_MAX_CAPTION_LINE_GAP = 10.0
_MAX_CROP_PAGE_RATIO = 0.85
_EXPECTED_PDF_ERRORS = (PdfiumError, OSError, ValueError, UnicodeError)
_PROGRAMMING_OR_RESOURCE_ERRORS = (
    AssertionError,
    AttributeError,
    IndexError,
    KeyError,
    MemoryError,
    NameError,
    NotImplementedError,
    RecursionError,
    TypeError,
)


@dataclass(frozen=True)
class _Box:
    left: float
    bottom: float
    right: float
    top: float

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.top - self.bottom

    @property
    def area(self) -> float:
        return self.width * self.height

    def union(self, other: "_Box") -> "_Box":
        return _Box(
            min(self.left, other.left),
            min(self.bottom, other.bottom),
            max(self.right, other.right),
            max(self.top, other.top),
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


def _box(bounds: object) -> _Box | None:
    if not isinstance(bounds, tuple) or len(bounds) != 4:
        return None
    if not all(_finite_number(value) for value in bounds):
        return None
    left, bottom, right, top = (float(value) for value in bounds)
    result = _Box(left, bottom, right, top)
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


def _page_lines(
    text_page: pdfium.PdfTextPage,
    *,
    char_count: int,
    visible: _Box,
) -> list[_TextLine]:
    text = text_page.get_text_range(0, char_count, errors="strict")
    if len(text) != char_count:
        return []

    lines: list[_TextLine] = []
    characters: list[str] = []
    line_box: _Box | None = None

    def finish_line() -> None:
        nonlocal characters, line_box
        if line_box is not None:
            normalized = _normalize_text("".join(characters))
            if normalized is not None:
                lines.append(_TextLine(normalized, line_box))
        characters = []
        line_box = None

    for index, character in enumerate(text):
        if character in "\r\n":
            finish_line()
            continue
        char_box = _box(text_page.get_charbox(index))
        if char_box is None:
            return []
        if (
            char_box.left < visible.left
            or char_box.bottom < visible.bottom
            or char_box.right > visible.right
            or char_box.top > visible.top
        ):
            continue
        characters.append(character)
        line_box = char_box if line_box is None else line_box.union(char_box)
    finish_line()
    lines.sort(key=lambda line: (-line.box.top, line.box.left))
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
                or previous.box.bottom - continuation.box.top > _MAX_CAPTION_LINE_GAP
                or continuation.box.top > previous.box.bottom
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
        or first.top + gap < second.bottom
        or second.top + gap < first.bottom
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


def _candidate_visuals(
    visual_objects: list[_Box],
    *,
    caption: _Caption,
    neighbors: list[_TextLine],
    visible: _Box,
    page_margin: float,
    max_vertical_distance: float,
    min_visual_area: float,
) -> list[_Box]:
    page_area = visible.area
    candidates: list[_Box] = []
    for candidate in visual_objects:
        if candidate.height == 0:
            candidate = _Box(
                candidate.left,
                candidate.bottom,
                candidate.right,
                candidate.top + 1,
            )
        if (
            candidate.left < visible.left + page_margin
            or candidate.bottom < visible.bottom + page_margin
            or candidate.right > visible.right - page_margin
            or candidate.top > visible.top - page_margin
            or candidate.area < min_visual_area
            or candidate.area >= page_area * _MAX_CROP_PAGE_RATIO
            or candidate.bottom < caption.box.top
            or candidate.bottom - caption.box.top > max_vertical_distance
        ):
            continue
        if any(
            neighbor.box.top < candidate.bottom
            and neighbor.box.top > caption.box.top
            and not _TARGET_CAPTION_RE.match(neighbor.text)
            for neighbor in neighbors
        ):
            continue
        candidates.append(candidate)
    return candidates


def _crop_for_page(
    lines: list[_TextLine],
    visual_objects: list[_Box],
    *,
    visible: _Box,
    page_margin: float,
    max_vertical_distance: float,
    min_visual_area: float,
    max_cluster_gap: float,
    crop_padding: float,
) -> tuple[str, PdfCrop] | None:
    captions, neighbors = _captions(lines)
    matches: list[tuple[str, PdfCrop]] = []
    for caption in captions:
        clusters = _merge_clusters(
            _candidate_visuals(
                visual_objects,
                caption=caption,
                neighbors=neighbors,
                visible=visible,
                page_margin=page_margin,
                max_vertical_distance=max_vertical_distance,
                min_visual_area=min_visual_area,
            ),
            max_cluster_gap,
        )
        if len(clusters) != 1 or _horizontal_overlap(clusters[0], caption.box) <= 0:
            continue
        crop_box = clusters[0].union(caption.box)
        crop = (
            max(visible.left, crop_box.left - crop_padding),
            max(visible.bottom, crop_box.bottom - crop_padding),
            min(visible.right, crop_box.right + crop_padding),
            min(visible.top, crop_box.top + crop_padding),
        )
        crop_area = (crop[2] - crop[0]) * (crop[3] - crop[1])
        if (
            crop[0] >= crop[2]
            or crop[1] >= crop[3]
            or crop_area >= visible.area * _MAX_CROP_PAGE_RATIO
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
        max_objects_per_page: int = 20_000,
        max_total_objects: int = 100_000,
        max_text_chars_per_page: int = 100_000,
        max_total_text_chars: int = 500_000,
        max_page_dimension_points: int = 2_000,
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
            "max_pages": (max_pages, 1_000),
            "max_objects_per_page": (max_objects_per_page, 100_000),
            "max_total_objects": (max_total_objects, 1_000_000),
            "max_text_chars_per_page": (max_text_chars_per_page, 500_000),
            "max_total_text_chars": (max_total_text_chars, 2_000_000),
            "max_page_dimension_points": (max_page_dimension_points, 10_000),
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
        self.max_total_objects = max_total_objects
        self.max_text_chars_per_page = max_text_chars_per_page
        self.max_total_text_chars = max_total_text_chars
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

    def _extract_pdf_impl(self, body: bytes) -> tuple[str, bytes] | None:
        document = pdfium.PdfDocument(body)
        try:
            page_count = len(document)
            if page_count < 1 or page_count > self.max_pages:
                return None
            total_objects = 0
            total_text_chars = 0
            detected: list[tuple[int, str, PdfCrop, PdfCrop]] = []

            for page_index in range(page_count):
                page = document.get_page(page_index)
                text_page = None
                try:
                    visible_bbox = visible_page_bbox(page)
                    if visible_bbox is None:
                        return None
                    visible = _Box(*visible_bbox)
                    if (
                        visible.width > self.max_page_dimension_points
                        or visible.height > self.max_page_dimension_points
                    ):
                        return None

                    text_page = page.get_textpage()
                    char_count = text_page.count_chars()
                    total_text_chars += char_count
                    if (
                        char_count > self.max_text_chars_per_page
                        or total_text_chars > self.max_total_text_chars
                    ):
                        return None
                    lines = _page_lines(
                        text_page,
                        char_count=char_count,
                        visible=visible,
                    )

                    page_objects = 0
                    visual_objects: list[_Box] = []
                    for page_object in page.get_objects(
                        max_depth=8,
                        textpage=text_page,
                    ):
                        page_objects += 1
                        total_objects += 1
                        if (
                            page_objects > self.max_objects_per_page
                            or total_objects > self.max_total_objects
                        ):
                            return None
                        if page_object.type not in (
                            pdfium.raw.FPDF_PAGEOBJ_IMAGE,
                            pdfium.raw.FPDF_PAGEOBJ_PATH,
                        ):
                            continue
                        object_box = _box(page_object.get_bounds())
                        if object_box is not None:
                            visual_objects.append(object_box)

                    page_crop = _crop_for_page(
                        lines,
                        visual_objects,
                        visible=visible,
                        page_margin=self.page_margin,
                        max_vertical_distance=self.max_vertical_distance,
                        min_visual_area=self.min_visual_area,
                        max_cluster_gap=self.max_cluster_gap,
                        crop_padding=self.crop_padding,
                    )
                    if page_crop is not None:
                        detected.append(
                            (page_index, page_crop[0], page_crop[1], visible_bbox)
                        )
                finally:
                    if text_page is not None:
                        text_page.close()
                    page.close()

            if len(detected) != 1:
                return None
            page_index, caption, crop, visible_bbox = detected[0]
            page = document.get_page(page_index)
            try:
                content = render_pdf_page_to_png(
                    page,
                    visible_bbox=visible_bbox,
                    crop=crop,
                    resolution=self.resolution,
                    max_output_dimension=self.max_output_dimension,
                    max_output_pixels=self.max_crop_pixels,
                    max_output_bytes=self.max_output_bytes,
                )
            finally:
                page.close()
            return None if content is None else (caption, content)
        finally:
            document.close()

    def _extract_pdf(self, body: bytes) -> tuple[str, bytes] | None:
        if not body.startswith(b"%PDF-"):
            return None
        try:
            return self._extract_pdf_impl(body)
        except _PROGRAMMING_OR_RESOURCE_ERRORS:
            raise
        except _EXPECTED_PDF_ERRORS:
            return None
        except Exception:
            LOGGER.exception("Unexpected arXiv PDF extraction failure")
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
