import io
import math
import warnings
from typing import TypeGuard

import pypdfium2 as pdfium
from PIL import Image, UnidentifiedImageError
from pypdfium2 import PdfiumError

type PdfCrop = tuple[float, float, float, float]

_EXPECTED_PDF_ERRORS = (PdfiumError, OSError, ValueError, UnicodeError)


def _finite_number(value: object) -> TypeGuard[int | float]:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def visible_page_bbox(page: pdfium.PdfPage) -> PdfCrop | None:
    """Return PDFium's visible MediaBox/CropBox intersection."""
    try:
        bbox = page.get_bbox()
    except _EXPECTED_PDF_ERRORS:
        return None
    if len(bbox) != 4 or not all(_finite_number(value) for value in bbox):
        return None
    left, bottom, right, top = (float(value) for value in bbox)
    if left >= right or bottom >= top:
        return None
    return left, bottom, right, top


def _valid_png(
    content: bytes,
    *,
    expected_size: tuple[int, int],
    max_output_dimension: int,
    max_output_pixels: int,
) -> bool:
    if not content.startswith(b"\x89PNG\r\n\x1a\n"):
        return False
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                if image.format != "PNG" or image.size != expected_size:
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
    page: pdfium.PdfPage,
    *,
    visible_bbox: PdfCrop,
    crop: PdfCrop | None,
    resolution: int,
    max_output_dimension: int,
    max_output_pixels: int,
    max_output_bytes: int,
) -> bytes | None:
    """Render a true PDFium bitmap crop after validating allocation dimensions."""
    if (
        type(resolution) is not int
        or resolution < 1
        or type(max_output_dimension) is not int
        or max_output_dimension < 1
        or type(max_output_pixels) is not int
        or max_output_pixels < 1
        or type(max_output_bytes) is not int
        or max_output_bytes < 1
    ):
        return None
    left, bottom, right, top = visible_bbox
    requested = visible_bbox if crop is None else crop
    crop_left, crop_bottom, crop_right, crop_top = requested
    if (
        not all(_finite_number(value) for value in requested)
        or crop_left < left
        or crop_bottom < bottom
        or crop_right > right
        or crop_top > top
        or crop_left >= crop_right
        or crop_bottom >= crop_top
    ):
        return None

    scale = resolution / 72
    source_width = math.ceil((right - left) * scale)
    source_height = math.ceil((top - bottom) * scale)
    crop_margins = (
        crop_left - left,
        crop_bottom - bottom,
        right - crop_right,
        top - crop_top,
    )
    crop_pixels = tuple(math.ceil(value * scale) for value in crop_margins)
    output_width = source_width - crop_pixels[0] - crop_pixels[2]
    output_height = source_height - crop_pixels[1] - crop_pixels[3]
    if (
        output_width < 1
        or output_height < 1
        or output_width > max_output_dimension
        or output_height > max_output_dimension
        or output_width * output_height > max_output_pixels
    ):
        return None

    bitmap = None
    try:
        bitmap = page.render(
            scale=scale,
            crop=crop_margins,
            may_draw_forms=False,
            rev_byteorder=True,
            maybe_alpha=True,
            limit_image_cache=True,
        )
        if bitmap.width != output_width or bitmap.height != output_height:
            return None
        rendered = bitmap.to_pil()
        output = io.BytesIO()
        rendered.save(output, format="PNG")
        content = output.getvalue()
    except _EXPECTED_PDF_ERRORS:
        return None
    finally:
        if bitmap is not None:
            bitmap.close()

    if (
        not content
        or len(content) > max_output_bytes
        or not _valid_png(
            content,
            expected_size=(output_width, output_height),
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
    max_page_objects: int,
    max_text_chars: int,
    resolution: int,
    max_output_dimension: int,
    max_output_pixels: int,
    max_output_bytes: int,
) -> bytes | None:
    """Preflight and render an exactly-one-page embedded PDF asset."""
    if (
        not content
        or len(content) > max_pdf_bytes
        or not content.startswith(b"%PDF-")
    ):
        return None

    document = None
    page = None
    text_page = None
    try:
        document = pdfium.PdfDocument(content)
        if len(document) != 1:
            return None
        page = document.get_page(0)
        visible_bbox = visible_page_bbox(page)
        if visible_bbox is None:
            return None
        left, bottom, right, top = visible_bbox
        if (
            right - left > max_page_dimension_points
            or top - bottom > max_page_dimension_points
        ):
            return None

        text_page = page.get_textpage()
        if text_page.count_chars() > max_text_chars:
            return None
        for object_count, _object in enumerate(
            page.get_objects(max_depth=8, textpage=text_page),
            start=1,
        ):
            if object_count > max_page_objects:
                return None
        return render_pdf_page_to_png(
            page,
            visible_bbox=visible_bbox,
            crop=None,
            resolution=resolution,
            max_output_dimension=max_output_dimension,
            max_output_pixels=max_output_pixels,
            max_output_bytes=max_output_bytes,
        )
    except _EXPECTED_PDF_ERRORS:
        return None
    finally:
        if text_page is not None:
            text_page.close()
        if page is not None:
            page.close()
        if document is not None:
            document.close()
