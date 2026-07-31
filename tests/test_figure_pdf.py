import io
import logging
import math
from collections.abc import Callable

import httpx
import pdfplumber
import pypdfium2 as pdfium
import pytest
from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from vla_wam_daily import figure_pdf
from vla_wam_daily.figure_pdf import ArxivPdfFigureExtractor
from vla_wam_daily.figure_recovery_types import TransientRecoveryError

ARXIV_ID = "2607.12345"
VERSION = 2
PDF_URL = f"https://arxiv.org/pdf/{ARXIV_ID}v{VERSION}"
PAGE_SIZE = (612, 792)
USER_AGENT = "VLA-WAM-Daily-Test/0.1"

DrawPage = Callable[[Canvas], None]


class FakeTextPage:
    def __init__(self, text: str, *, geometryless_indexes: set[int]) -> None:
        self.text = text
        self.geometryless_indexes = geometryless_indexes

    def get_text_range(
        self,
        _index: int,
        _count: int,
        *,
        errors: str,
    ) -> str:
        assert errors == "strict"
        return self.text

    def get_charbox(self, index: int) -> tuple[float, float, float, float]:
        line_index = self.text.count("\n", 0, index)
        line_start = self.text.rfind("\n", 0, index) + 1
        left = 100.0 + (index - line_start) * 6
        bottom = 450.0 - line_index * 60
        if index in self.geometryless_indexes:
            return (left, bottom, left, bottom)
        return (left, bottom, left + 5, bottom + 12)


def make_pdf(*pages: DrawPage, page_size: tuple[int, int] = PAGE_SIZE) -> bytes:
    output = io.BytesIO()
    canvas = Canvas(output, pagesize=page_size, invariant=1, pageCompression=0)
    for draw_page in pages:
        draw_page(canvas)
        canvas.showPage()
    canvas.save()
    return output.getvalue()


def draw_caption(
    canvas: Canvas,
    text: str = "Figure 1: Model architecture.",
    *,
    x: float = 100,
    y: float = 390,
) -> None:
    canvas.setFillColorRGB(0, 0, 0)
    canvas.setFont("Helvetica", 12)
    canvas.drawString(x, y, text)


def draw_rect_visual(
    canvas: Canvas,
    *,
    x: float = 90,
    y: float = 430,
    width: float = 400,
    height: float = 180,
    color: tuple[float, float, float] = (0.1, 0.3, 0.8),
) -> None:
    canvas.setFillColorRGB(*color)
    canvas.rect(x, y, width, height, stroke=0, fill=1)


def make_target_pdf(
    *,
    caption: str = "Figure 1: Model architecture.",
    visual: DrawPage = draw_rect_visual,
) -> bytes:
    def page(canvas: Canvas) -> None:
        visual(canvas)
        draw_caption(canvas, caption)

    return make_pdf(page)


def replace_page_boxes(
    content: bytes,
    *,
    media_box: tuple[int, int, int, int] = (0, 0, 612, 792),
    crop_box: tuple[int, int, int, int] | None = None,
) -> bytes:
    old = b"/MediaBox [ 0 0 612 792 ]"
    media = f"/MediaBox [ {' '.join(str(value) for value in media_box)} ]".encode()
    replacement = media
    if crop_box is not None:
        crop = f"/CropBox [ {' '.join(str(value) for value in crop_box)} ]".encode()
        replacement = crop + b" " + media
    assert content.count(old) == 1
    return content.replace(old, replacement)


def make_extractor(
    body: bytes | None = None,
    *,
    handler: httpx.BaseTransport | None = None,
    **kwargs: object,
) -> tuple[ArxivPdfFigureExtractor, httpx.Client]:
    if handler is None:
        assert body is not None
        handler = httpx.MockTransport(
            lambda _request: httpx.Response(200, content=body)
        )
    client = httpx.Client(transport=handler)
    options: dict[str, object] = {
        "user_agent": USER_AGENT,
        "client": client,
    }
    options.update(kwargs)
    return ArxivPdfFigureExtractor(**options), client


def extract(body: bytes, **kwargs: object):
    extractor, client = make_extractor(body, **kwargs)
    try:
        return extractor.extract(ARXIV_ID, VERSION)
    finally:
        client.close()


def extract_all(body: bytes, **kwargs: object):
    extractor, client = make_extractor(body, **kwargs)
    try:
        return extractor.extract_all(ARXIV_ID, VERSION)
    finally:
        client.close()


def test_extracts_figure_one_and_two_from_separate_pages() -> None:
    def figure_one_page(canvas: Canvas) -> None:
        draw_rect_visual(canvas)
        draw_caption(canvas, "Figure 1: First architecture.")

    def figure_two_page(canvas: Canvas) -> None:
        draw_rect_visual(canvas, color=(0.8, 0.2, 0.1))
        draw_caption(canvas, "Figure 2: Second architecture.")

    candidates = extract_all(make_pdf(figure_one_page, figure_two_page))

    assert [candidate.number for candidate in candidates] == [1, 2]
    assert [candidate.caption for candidate in candidates] == [
        "First architecture.",
        "Second architecture.",
    ]


def test_page_lines_tolerates_pdfium_decoded_text_count_mismatch() -> None:
    text = "Body text\nFigure 1: Recover this caption."
    lines = figure_pdf._page_lines(
        FakeTextPage(text, geometryless_indexes=set()),
        char_count=len(text) + 5,
        visible=figure_pdf._Box(0, 0, *PAGE_SIZE),
    )

    assert [line.text for line in lines] == [
        "Body text",
        "Figure 1: Recover this caption.",
    ]


def test_wide_crop_fallback_uses_region_above_caption() -> None:
    visible = figure_pdf._Box(0, 0, *PAGE_SIZE)
    caption = figure_pdf._Caption(
        1,
        "Fallback diagram.",
        figure_pdf._Box(100, 100, 400, 115),
    )

    crop = figure_pdf._wide_crop_for_caption(
        caption,
        visible=visible,
        page_margin=6,
        crop_padding=6,
    )

    assert crop is not None
    assert crop[0] >= visible.left
    assert crop[1] <= caption.box.bottom
    assert crop[2] <= visible.right
    assert crop[3] > caption.box.top


@pytest.mark.parametrize(
    ("caption", "expected"),
    [
        ("Figure 1:   Model   architecture. ", "Model architecture."),
        ("Fig. 1. Compact policy diagram.", "Compact policy diagram."),
    ],
)
def test_extracts_supported_figure_one_caption_forms(
    caption: str,
    expected: str,
) -> None:
    candidate = extract(make_target_pdf(caption=caption))

    assert candidate is not None
    assert candidate.caption == expected
    assert candidate.extension == "png"
    assert candidate.source == "arxiv_pdf"
    assert candidate.source_url == PDF_URL
    assert candidate.content.startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(io.BytesIO(candidate.content)) as image:
        image.load()
        assert image.format == "PNG"
        assert 200 < image.width < 2_500
        assert 200 < image.height < 2_500


def test_geometryless_whitespace_keeps_later_caption_but_non_whitespace_rejects() -> None:
    text = "Body text\nFig. 1: See2Think architecture."
    visible = figure_pdf._Box(0, 0, *PAGE_SIZE)
    whitespace_index = text.index(" ")

    lines = figure_pdf._page_lines(
        FakeTextPage(text, geometryless_indexes={whitespace_index}),
        char_count=len(text),
        visible=visible,
    )

    assert [line.text for line in lines] == [
        "Body text",
        "Fig. 1: See2Think architecture.",
    ]
    crop = figure_pdf._crop_for_page(
        lines,
        [figure_pdf._Box(90, 430, 490, 610)],
        visible=visible,
        page_margin=6,
        max_vertical_distance=72,
        min_visual_area=900,
        max_cluster_gap=12,
        crop_padding=6,
    )
    assert crop is not None
    assert crop[0] == "See2Think architecture."
    assert (
        figure_pdf._page_lines(
            FakeTextPage(text, geometryless_indexes={0}),
            char_count=len(text),
            visible=visible,
        )
        == []
    )


def test_extracts_punctuation_free_figure_one_caption_with_unique_visual() -> None:
    candidate = extract(make_target_pdf(caption="Figure 1 See2Think architecture"))

    assert candidate is not None
    assert candidate.caption == "See2Think architecture"


def test_caption_continuation_skips_an_interleaved_right_column_line() -> None:
    lines = [
        figure_pdf._TextLine(
            "Fig. 1. Methods for transferring text-conditioned models to speech. a) uses",
            figure_pdf._Box(54.10, 173.94, 298.47, 181.11),
        ),
        figure_pdf._TextLine(
            "an Automatic Speech Recognition (ASR) model to transcribe the speech,",
            figure_pdf._Box(54.29, 164.97, 298.36, 172.14),
        ),
        figure_pdf._TextLine(
            "but it mistranscribes, causing complete failure of the downstream task. b) is",
            figure_pdf._Box(54.02, 156.00, 298.47, 163.18),
        ),
        figure_pdf._TextLine(
            "speaker variations. Motivated by these findings, we introduce",
            figure_pdf._Box(313.71, 153.95, 557.80, 162.92),
        ),
        figure_pdf._TextLine(
            "trained directly on speech, bypassing the discrete mistranscription problem.",
            figure_pdf._Box(54.10, 147.04, 298.25, 154.21),
        ),
        figure_pdf._TextLine(
            "This close fourth continuation must not be consumed.",
            figure_pdf._Box(54.10, 138.07, 298.25, 145.24),
        ),
    ]

    captions, _neighbors = figure_pdf._captions(lines)

    assert [caption.text for caption in captions] == [
        "Methods for transferring text-conditioned models to speech. "
        "a) uses an Automatic Speech Recognition (ASR) model to transcribe "
        "the speech, but it mistranscribes, causing complete failure of the "
        "downstream task. b) is trained directly on speech, bypassing the "
        "discrete mistranscription problem."
    ]


def test_caption_continuation_stops_at_a_shifted_same_column_line() -> None:
    lines = [
        figure_pdf._TextLine(
            "Figure 1: Target caption.",
            figure_pdf._Box(54, 174, 298, 181),
        ),
        figure_pdf._TextLine(
            "Indented body text.",
            figure_pdf._Box(100, 165, 298, 172),
        ),
        figure_pdf._TextLine(
            "A later aligned line must not be reached.",
            figure_pdf._Box(54, 156, 298, 163),
        ),
    ]

    captions, _neighbors = figure_pdf._captions(lines)

    assert [caption.text for caption in captions] == ["Target caption."]


def test_prose_reference_without_punctuation_is_not_a_caption() -> None:
    assert (
        extract(make_target_pdf(caption="Figure 1 shows how the method works."))
        is None
    )


@pytest.mark.parametrize(
    "caption",
    [
        "Figure 10: Wrong figure.",
        "Figure 1.1: Wrong subsection.",
    ],
)
def test_numbered_variants_do_not_match_figure_one(caption: str) -> None:
    assert extract(make_target_pdf(caption=caption)) is None


def test_default_render_resolution_is_approximately_300_dpi() -> None:
    candidate = extract(make_target_pdf())

    assert candidate is not None
    with Image.open(io.BytesIO(candidate.content)) as image:
        width, height = image.size

    crop_width_points = 400 + 2 * 6
    helvetica_12_descent_points = 2.484
    crop_height_points = 180 + (430 - 390) + helvetica_12_descent_points + 2 * 6
    expected_width = crop_width_points * 300 / 72
    expected_height = crop_height_points * 300 / 72
    assert width == pytest.approx(expected_width, abs=2)
    assert height == pytest.approx(expected_height, abs=2)


def draw_image_visual(canvas: Canvas) -> None:
    image = Image.new("RGB", (80, 40), (30, 100, 210))
    canvas.drawImage(
        ImageReader(image),
        90,
        430,
        width=400,
        height=180,
        preserveAspectRatio=False,
        mask="auto",
    )


def draw_curve_visual(canvas: Canvas) -> None:
    canvas.setLineWidth(5)
    canvas.bezier(90, 430, 190, 610, 390, 430, 490, 600)


def draw_line_visual(canvas: Canvas) -> None:
    canvas.setLineWidth(5)
    canvas.line(90, 430, 490, 600)


@pytest.mark.parametrize(
    "visual",
    [draw_rect_visual, draw_image_visual, draw_curve_visual, draw_line_visual],
)
def test_accepts_unique_plausible_visual_object_above_overlapping_caption(
    visual: DrawPage,
) -> None:
    assert extract(make_target_pdf(visual=visual)) is not None


def test_large_page_tiny_crop_uses_bounded_pdfium_render(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def page(canvas: Canvas) -> None:
        draw_rect_visual(canvas, x=100, y=1_200, width=120, height=80)
        draw_caption(canvas, "Figure 1: Tiny crop.", x=110, y=1_175)

    body = make_pdf(page, page_size=(1_440, 1_440))
    calls: list[tuple[int, int]] = []
    original_render = pdfium.PdfPage.render

    def bounded_render(
        pdf_page,
        *,
        scale=1,
        crop=(0, 0, 0, 0),
        **kwargs,
    ):
        source_width = math.ceil(pdf_page.get_width() * scale)
        source_height = math.ceil(pdf_page.get_height() * scale)
        crop_pixels = [math.ceil(value * scale) for value in crop]
        output_width = source_width - crop_pixels[0] - crop_pixels[2]
        output_height = source_height - crop_pixels[1] - crop_pixels[3]
        assert source_width > 5_000
        assert source_height > 5_000
        assert output_width < 1_000
        assert output_height < 1_000
        calls.append((output_width, output_height))
        return original_render(
            pdf_page,
            scale=scale,
            crop=crop,
            **kwargs,
        )

    def reject_root_page_render(*_args, **_kwargs):
        pytest.fail("pdfplumber attempted to rasterize the full root page")

    monkeypatch.setattr(pdfium.PdfPage, "render", bounded_render)
    monkeypatch.setattr(pdfplumber.page.Page, "to_image", reject_root_page_render)

    candidate = extract(body, max_page_dimension_points=2_000)

    assert candidate is not None
    assert len(calls) == 1


def test_crop_excludes_header_footer_content_below_and_neighboring_figure() -> None:
    def page(canvas: Canvas) -> None:
        canvas.setFillColorRGB(1, 0, 0)
        canvas.rect(40, 750, 530, 20, stroke=0, fill=1)
        draw_rect_visual(canvas)
        draw_caption(canvas, "Figure 1: Target diagram.")
        canvas.setFillColorRGB(0, 1, 0)
        canvas.rect(90, 320, 400, 30, stroke=0, fill=1)
        draw_caption(canvas, "Figure 2: Neighboring diagram.", y=285)
        canvas.setFillColorRGB(1, 0, 1)
        canvas.rect(90, 80, 400, 180, stroke=0, fill=1)
        canvas.setFillColorRGB(1, 0.5, 0)
        canvas.rect(40, 15, 530, 20, stroke=0, fill=1)

    candidate = extract(make_pdf(page))

    assert candidate is not None
    with Image.open(io.BytesIO(candidate.content)).convert("RGB") as image:
        palette = image.getcolors(maxcolors=image.width * image.height)
    assert palette is not None
    colors = {color for _count, color in palette}
    assert any(blue > 150 and blue > red * 1.5 for red, _green, blue in colors)
    assert not any(red > 220 and green < 40 and blue < 40 for red, green, blue in colors)
    assert not any(green > 220 and red < 40 and blue < 40 for red, green, blue in colors)
    assert not any(red > 220 and blue > 220 and green < 40 for red, green, blue in colors)


def test_wide_fallback_captures_ambiguous_disjoint_regions() -> None:
    def page(canvas: Canvas) -> None:
        draw_rect_visual(canvas, x=80, width=145)
        draw_rect_visual(canvas, x=250, width=145)
        draw_caption(canvas, "Figure 1: Ambiguous regions.", x=100)

    assert extract(make_pdf(page)) is not None


def test_wide_fallback_does_not_guess_largest_ambiguous_object() -> None:
    def page(canvas: Canvas) -> None:
        draw_rect_visual(canvas, x=70, width=170)
        draw_rect_visual(canvas, x=260, width=230)
        draw_caption(
            canvas,
            "Figure 1: Still ambiguous with two horizontally overlapping regions.",
            x=100,
        )

    assert extract(make_pdf(page)) is not None


def test_wide_fallback_keeps_disjoint_panels_with_short_caption() -> None:
    def page(canvas: Canvas) -> None:
        draw_rect_visual(canvas, x=80, width=145)
        draw_rect_visual(canvas, x=280, width=145)
        draw_caption(canvas, "Figure 1: A.", x=100)

    assert extract(make_pdf(page)) is not None


def test_caption_without_precise_visual_region_uses_wide_fallback() -> None:
    candidate = extract(make_pdf(lambda canvas: draw_caption(canvas)))

    assert candidate is not None
    assert candidate.caption == "Model architecture."


def test_content_below_caption_still_allows_caption_anchored_fallback() -> None:
    def page(canvas: Canvas) -> None:
        draw_caption(canvas)
        draw_rect_visual(canvas, y=170)

    assert extract(make_pdf(page)) is not None


def test_outside_visual_uses_page_bounded_wide_fallback() -> None:
    def page(canvas: Canvas) -> None:
        draw_rect_visual(canvas, x=-20, width=220)
        draw_caption(canvas, x=60)

    assert extract(make_pdf(page)) is not None


def test_neighboring_figure_caption_blocks_crossing_visual_region() -> None:
    def page(canvas: Canvas) -> None:
        draw_rect_visual(canvas, y=500)
        draw_caption(canvas, "Figure 2: This owns the visual.", y=455)
        draw_caption(canvas, "Figure 1: Must not cross Figure 2.", y=390)

    assert extract(make_pdf(page), max_vertical_distance=160) is None


def test_punctuation_free_neighboring_caption_blocks_crossing_visual_region() -> None:
    def page(canvas: Canvas) -> None:
        draw_rect_visual(canvas, y=500)
        draw_caption(canvas, "Figure 2 Neighboring diagram", y=455)
        draw_caption(canvas, "Figure 1 Target diagram", y=390)

    assert extract(make_pdf(page), max_vertical_distance=160) is None


def test_scanned_page_without_machine_readable_caption_is_not_ocrd() -> None:
    page_image = Image.new("RGB", PAGE_SIZE, "white")

    def page(canvas: Canvas) -> None:
        canvas.drawImage(
            ImageReader(page_image),
            0,
            0,
            width=PAGE_SIZE[0],
            height=PAGE_SIZE[1],
            preserveAspectRatio=False,
        )

    assert extract(make_pdf(page)) is None


def test_near_full_page_visual_uses_bounded_wide_fallback() -> None:
    def page(canvas: Canvas) -> None:
        draw_rect_visual(
            canvas,
            x=6,
            y=35,
            width=600,
            height=745,
        )
        draw_caption(canvas, "Figure 1: Near-full-page candidate.", x=20, y=18)

    candidate = extract(make_pdf(page))

    assert candidate is not None
    with Image.open(io.BytesIO(candidate.content)) as image:
        assert image.width <= 10_000
        assert image.height <= 10_000


def test_nonzero_media_box_origin_uses_visible_page_coordinates() -> None:
    def page(canvas: Canvas) -> None:
        canvas.translate(100, 200)
        draw_rect_visual(canvas)
        draw_caption(canvas)

    body = make_pdf(page)
    body = replace_page_boxes(body, media_box=(100, 200, 712, 992))

    candidate = extract(body)

    assert candidate is not None
    assert candidate.caption == "Model architecture."


def test_crop_box_is_used_for_both_detection_and_rendering() -> None:
    body = replace_page_boxes(
        make_target_pdf(),
        media_box=(0, 0, 612, 792),
        crop_box=(50, 100, 562, 692),
    )

    candidate = extract(body)

    assert candidate is not None
    with Image.open(io.BytesIO(candidate.content)) as image:
        assert image.width < 2_000
        assert image.height < 1_200


def test_rejects_pdf_over_byte_limit_before_parsing() -> None:
    body = make_target_pdf()
    assert extract(body, max_pdf_bytes=len(body) - 1) is None


def test_rejects_pdf_over_page_limit() -> None:
    body = make_pdf(
        lambda canvas: (draw_rect_visual(canvas), draw_caption(canvas)),
        lambda canvas: canvas.drawString(100, 700, "second page"),
    )
    assert extract(body, max_pages=1) is None


def test_rejects_page_over_object_limit() -> None:
    body = make_target_pdf()
    assert extract(body, max_objects_per_page=1) is None


def test_rejects_pdf_over_cumulative_object_limit() -> None:
    body = make_pdf(
        lambda canvas: (draw_rect_visual(canvas), draw_caption(canvas)),
        lambda canvas: canvas.line(100, 100, 200, 200),
    )

    assert extract(
        body,
        max_objects_per_page=10,
        max_total_objects=2,
    ) is None


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("max_text_chars_per_page", 5),
        ("max_total_text_chars", 5),
    ],
)
def test_rejects_pdf_over_text_character_limit(name: str, value: int) -> None:
    assert extract(make_target_pdf(), **{name: value}) is None


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("max_crop_pixels", 1_000),
        ("max_output_dimension", 100),
        ("max_output_bytes", 100),
    ],
)
def test_rejects_crop_render_over_output_limit(name: str, value: int) -> None:
    assert extract(make_target_pdf(), **{name: value}) is None


def test_requests_exact_versioned_endpoint_and_preserves_external_client() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(404)

    extractor, client = make_extractor(handler=httpx.MockTransport(handler))
    with extractor:
        assert extractor.extract(ARXIV_ID, VERSION) is None

    assert [str(request.url) for request in requests] == [PDF_URL]
    assert requests[0].headers["user-agent"] == USER_AGENT
    assert client.is_closed is False
    client.close()


def test_context_manager_closes_owned_client() -> None:
    with ArxivPdfFigureExtractor(user_agent=USER_AGENT) as extractor:
        client = extractor.client
        assert client.is_closed is False
    assert client.is_closed is True


def test_follows_only_safe_redirect_for_same_exact_pdf_identity() -> None:
    requests: list[httpx.Request] = []
    body = make_target_pdf()

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "arxiv.org":
            return httpx.Response(
                302,
                headers={"location": f"https://www.arxiv.org/pdf/{ARXIV_ID}v{VERSION}"},
            )
        return httpx.Response(200, content=body)

    extractor, client = make_extractor(handler=httpx.MockTransport(handler))
    try:
        candidate = extractor.extract(ARXIV_ID, VERSION)
    finally:
        client.close()

    assert candidate is not None
    assert [request.url.host for request in requests] == ["arxiv.org", "www.arxiv.org"]


@pytest.mark.parametrize(
    "location",
    [
        f"http://arxiv.org/pdf/{ARXIV_ID}v{VERSION}",
        f"https://example.com/pdf/{ARXIV_ID}v{VERSION}",
        f"https://reader:secret@arxiv.org/pdf/{ARXIV_ID}v{VERSION}",
        f"https://arxiv.org:444/pdf/{ARXIV_ID}v{VERSION}",
        f"https://arxiv.org/pdf/{ARXIV_ID}v{VERSION}?download=1",
        f"https://arxiv.org/pdf/{ARXIV_ID}v{VERSION}#page=1",
        f"https://arxiv.org/pdf/{ARXIV_ID}v1",
        "https://arxiv.org/pdf/2607.99999v2",
        f"https://arxiv.org/abs/{ARXIV_ID}v{VERSION}",
    ],
)
def test_rejects_unsafe_or_identity_changing_redirect(location: str) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"location": location})

    extractor, client = make_extractor(handler=httpx.MockTransport(handler))
    try:
        assert extractor.extract(ARXIV_ID, VERSION) is None
    finally:
        client.close()
    assert len(requests) == 1


def test_rejects_redirects_over_bound() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"location": PDF_URL})

    extractor, client = make_extractor(
        handler=httpx.MockTransport(handler),
        max_redirects=0,
    )
    try:
        assert extractor.extract(ARXIV_ID, VERSION) is None
    finally:
        client.close()
    assert len(requests) == 1


def test_pdf_404_is_not_found() -> None:
    extractor, client = make_extractor(
        handler=httpx.MockTransport(lambda _request: httpx.Response(404))
    )
    try:
        assert extractor.extract(ARXIV_ID, VERSION) is None
    finally:
        client.close()


@pytest.mark.parametrize("status_code", [408, 425, 429, 500, 503])
def test_retryable_pdf_http_status_raises_transient_error(status_code: int) -> None:
    extractor, client = make_extractor(
        handler=httpx.MockTransport(
            lambda _request: httpx.Response(status_code)
        )
    )
    try:
        with pytest.raises(TransientRecoveryError):
            extractor.extract(ARXIV_ID, VERSION)
    finally:
        client.close()


def test_pdf_network_error_raises_transient_error() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("simulated timeout", request=request)

    extractor, client = make_extractor(handler=httpx.MockTransport(fail))
    try:
        with pytest.raises(TransientRecoveryError):
            extractor.extract(ARXIV_ID, VERSION)
    finally:
        client.close()


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b""),
        httpx.Response(200, content=b"not a PDF"),
        httpx.Response(200, content=b"%PDF-malformed"),
        httpx.Response(400, content=b"bad request"),
        httpx.Response(200, headers={"content-length": "invalid"}, content=b"%PDF"),
        httpx.Response(200, headers={"content-length": "-1"}, content=b"%PDF"),
        httpx.Response(200, headers={"content-length": "101"}, content=b"%PDF"),
        httpx.Response(200, content=b"x" * 101),
    ],
)
def test_malformed_non_pdf_or_oversized_body_is_deterministic_failure(
    response: httpx.Response,
) -> None:
    extractor, client = make_extractor(
        handler=httpx.MockTransport(lambda _request: response),
        max_pdf_bytes=100,
    )
    try:
        assert extractor.extract(ARXIV_ID, VERSION) is None
    finally:
        client.close()


class BrokenStream(httpx.SyncByteStream):
    def __iter__(self):
        yield b"%PDF-partial"
        raise httpx.ReadError("simulated interrupted stream")


def test_interrupted_pdf_stream_raises_transient_error() -> None:
    extractor, client = make_extractor(
        handler=httpx.MockTransport(
            lambda _request: httpx.Response(200, stream=BrokenStream())
        )
    )
    try:
        with pytest.raises(TransientRecoveryError):
            extractor.extract(ARXIV_ID, VERSION)
    finally:
        client.close()


def test_memory_error_from_pdfium_render_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_render(*_args, **_kwargs):
        raise MemoryError("simulated allocation failure")

    monkeypatch.setattr(pdfium.PdfPage, "render", fail_render)

    with pytest.raises(MemoryError):
        extract(make_target_pdf())


def test_unexpected_pdfium_render_error_is_logged_at_public_boundary(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fail_render(*_args, **_kwargs):
        raise RuntimeError("simulated unexpected renderer failure")

    monkeypatch.setattr(pdfium.PdfPage, "render", fail_render)

    with caplog.at_level(logging.ERROR):
        assert extract(make_target_pdf()) is None
    assert "unexpected" in caplog.text.casefold()
    assert "simulated unexpected renderer failure" in caplog.text


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("timeout_seconds", 0),
        ("max_pdf_bytes", 0),
        ("max_redirects", -1),
        ("max_pages", 0),
        ("max_objects_per_page", 0),
        ("max_total_objects", 0),
        ("max_text_chars_per_page", 0),
        ("max_total_text_chars", 0),
        ("max_page_dimension_points", 0),
        ("max_vertical_distance", 0),
        ("min_visual_area", 0),
        ("max_cluster_gap", -1),
        ("page_margin", -1),
        ("crop_padding", -1),
        ("resolution", 0),
        ("max_crop_pixels", 0),
        ("max_output_dimension", 0),
        ("max_output_bytes", 0),
    ],
)
def test_rejects_invalid_constructor_bounds(name: str, value: int) -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(404))
    )
    with pytest.raises(ValueError):
        ArxivPdfFigureExtractor(
            user_agent=USER_AGENT,
            client=client,
            **{name: value},
        )
    client.close()


def test_rejects_blank_user_agent() -> None:
    with pytest.raises(ValueError):
        ArxivPdfFigureExtractor(user_agent=" \t")


@pytest.mark.parametrize(
    ("arxiv_id", "version"),
    [
        ("2607.123", 1),
        ("2607.123456", 1),
        ("../2607.12345", 1),
        ("2607.12345", 0),
        ("2607.12345", True),
    ],
)
def test_reuses_strict_arxiv_identity_validation(
    arxiv_id: str,
    version: int,
) -> None:
    extractor, client = make_extractor(
        handler=httpx.MockTransport(lambda _request: httpx.Response(404))
    )
    try:
        with pytest.raises(ValueError):
            extractor.extract(arxiv_id, version)
    finally:
        client.close()
