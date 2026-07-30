import io
from collections.abc import Callable

import httpx
import pytest
from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from vla_wam_daily.figure_pdf import ArxivPdfFigureExtractor
from vla_wam_daily.figure_recovery_types import TransientRecoveryError

ARXIV_ID = "2607.12345"
VERSION = 2
PDF_URL = f"https://arxiv.org/pdf/{ARXIV_ID}v{VERSION}"
PAGE_SIZE = (612, 792)
USER_AGENT = "VLA-WAM-Daily-Test/0.1"

DrawPage = Callable[[Canvas], None]


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


def test_figure_ten_does_not_match_figure_one() -> None:
    assert extract(make_target_pdf(caption="Figure 10: Wrong figure.")) is None


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


def test_two_equally_plausible_disjoint_regions_are_ambiguous() -> None:
    def page(canvas: Canvas) -> None:
        draw_rect_visual(canvas, x=80, width=145)
        draw_rect_visual(canvas, x=250, width=145)
        draw_caption(canvas, "Figure 1: Ambiguous regions.", x=100)

    assert extract(make_pdf(page)) is None


def test_does_not_fall_back_to_largest_object_when_candidates_are_ambiguous() -> None:
    def page(canvas: Canvas) -> None:
        draw_rect_visual(canvas, x=70, width=170)
        draw_rect_visual(canvas, x=260, width=230)
        draw_caption(
            canvas,
            "Figure 1: Still ambiguous with two horizontally overlapping regions.",
            x=100,
        )

    assert extract(make_pdf(page)) is None


def test_caption_without_plausible_visual_region_returns_none() -> None:
    assert extract(make_pdf(lambda canvas: draw_caption(canvas))) is None


def test_content_below_caption_is_not_a_visual_candidate() -> None:
    def page(canvas: Canvas) -> None:
        draw_caption(canvas)
        draw_rect_visual(canvas, y=170)

    assert extract(make_pdf(page)) is None


def test_visual_outside_page_is_rejected_instead_of_clipping_crop() -> None:
    def page(canvas: Canvas) -> None:
        draw_rect_visual(canvas, x=-20, width=220)
        draw_caption(canvas, x=60)

    assert extract(make_pdf(page)) is None


def test_neighboring_figure_caption_blocks_crossing_visual_region() -> None:
    def page(canvas: Canvas) -> None:
        draw_rect_visual(canvas, y=500)
        draw_caption(canvas, "Figure 2: This owns the visual.", y=455)
        draw_caption(canvas, "Figure 1: Must not cross Figure 2.", y=390)

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


def test_near_full_page_visual_candidate_is_rejected_by_crop_coverage_bound() -> None:
    def page(canvas: Canvas) -> None:
        draw_rect_visual(
            canvas,
            x=6,
            y=35,
            width=600,
            height=745,
        )
        draw_caption(canvas, "Figure 1: Near-full-page candidate.", x=20, y=18)

    assert extract(make_pdf(page)) is None


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
    assert extract(body, max_objects_per_page=2) is None


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


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("timeout_seconds", 0),
        ("max_pdf_bytes", 0),
        ("max_redirects", -1),
        ("max_pages", 0),
        ("max_objects_per_page", 0),
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
