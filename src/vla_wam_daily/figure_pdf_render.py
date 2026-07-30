import io
import math
import warnings
from typing import TypeGuard

import pdfplumber
from pdfplumber.page import Page
from PIL import Image, UnidentifiedImageError

type PdfCrop = tuple[float, float, float, float]


def _valid_dimension(value: object) -> TypeGuard[int | float]:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )


def _valid_png(
    content: bytes,
    *,
    max_output_dimension: int,
    max_output_pixels: int,
) -> bool:
    if not content.startswith(b"\x89PNG\r\n\x1a\n"):
        return False
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                if image.format != "PNG":
                    return False
                width, height = image.size
                if (
                    width < 1
                    or height < 1
                    or width > max_output_dimension
                    or height > max_output_dimension
                    or width * height > max_output_pixels
                ):
                    return False
                image.load()
            with Image.open(io.BytesIO(content)) as verifier:
                verifier.verify()
    except (
        EOFError,
        OSError,
        SyntaxError,
        UnidentifiedImageError,
        ValueError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        return False
    return True


def render_pdf_page_to_png(
    page: Page,
    *,
    crop: PdfCrop | None,
    resolution: int,
    max_output_dimension: int,
    max_output_pixels: int,
    max_output_bytes: int,
) -> bytes | None:
    """Render one already-open PDF page or strict in-page crop to validated PNG."""
    if (
        type(resolution) is not int
        or resolution < 1
        or type(max_output_dimension) is not int
        or max_output_dimension < 1
        or type(max_output_pixels) is not int
        or max_output_pixels < 1
        or type(max_output_bytes) is not int
        or max_output_bytes < 1
        or not _valid_dimension(page.width)
        or not _valid_dimension(page.height)
    ):
        return None

    page_width = float(page.width)
    page_height = float(page.height)
    bbox = (0.0, 0.0, page_width, page_height) if crop is None else crop
    if (
        len(bbox) != 4
        or any(not _valid_dimension(value) and value != 0 for value in bbox)
    ):
        return None
    left, top, right, bottom = (float(value) for value in bbox)
    if (
        left < 0
        or top < 0
        or right > page_width
        or bottom > page_height
        or left >= right
        or top >= bottom
    ):
        return None

    scale = resolution / 72
    predicted_width = math.ceil((right - left) * scale) + 1
    predicted_height = math.ceil((bottom - top) * scale) + 1
    if (
        predicted_width > max_output_dimension
        or predicted_height > max_output_dimension
        or predicted_width * predicted_height > max_output_pixels
    ):
        return None

    try:
        rendered_page = page if crop is None else page.crop(bbox, strict=True)
        rendered = rendered_page.to_image(
            resolution=resolution,
            antialias=True,
        ).original
        width, height = rendered.size
        if (
            width < 1
            or height < 1
            or width > max_output_dimension
            or height > max_output_dimension
            or width * height > max_output_pixels
        ):
            return None
        output = io.BytesIO()
        rendered.save(output, format="PNG")
        content = output.getvalue()
    except Exception:
        return None

    if (
        not content
        or len(content) > max_output_bytes
        or not _valid_png(
            content,
            max_output_dimension=max_output_dimension,
            max_output_pixels=max_output_pixels,
        )
    ):
        return None
    return content


def render_single_page_pdf(
    content: bytes,
    *,
    max_pdf_bytes: int,
    max_page_dimension_points: int,
    resolution: int,
    max_output_dimension: int,
    max_output_pixels: int,
    max_output_bytes: int,
) -> bytes | None:
    """Validate and render an exactly-one-page embedded PDF asset."""
    if (
        not content
        or len(content) > max_pdf_bytes
        or not content.startswith(b"%PDF-")
    ):
        return None

    try:
        with pdfplumber.open(io.BytesIO(content), repair=False) as pdf:
            if len(pdf.pages) != 1:
                return None
            page = pdf.pages[0]
            if (
                not _valid_dimension(page.width)
                or not _valid_dimension(page.height)
                or page.width > max_page_dimension_points
                or page.height > max_page_dimension_points
            ):
                return None
            return render_pdf_page_to_png(
                page,
                crop=None,
                resolution=resolution,
                max_output_dimension=max_output_dimension,
                max_output_pixels=max_output_pixels,
                max_output_bytes=max_output_bytes,
            )
    except Exception:
        return None
