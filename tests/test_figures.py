from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx

from vla_wam_daily.figures import (
    ArxivFigureClient,
    figure_cache_key,
    figure_html_url,
    is_figure_cache_fresh,
    parse_figure_gallery,
)
from vla_wam_daily.models import FigureCacheEntry, FigureStatus

CHECKED_AT = datetime(2026, 7, 29, 2, 30, tzinfo=UTC)
HTML_URL = "https://arxiv.org/html/2607.12345v1"


def fixture_html() -> str:
    return Path("tests/fixtures/arxiv/figures.html").read_text(encoding="utf-8")


def make_client(**kwargs: object) -> ArxivFigureClient:
    options: dict[str, object] = {
        "user_agent": "VLA-WAM-Daily-Test/0.1",
        "request_delay_seconds": 0,
        "retry_wait_seconds": 0,
    }
    options.update(kwargs)
    return ArxivFigureClient(**options)


@pytest.mark.parametrize(
    ("arxiv_id", "version"),
    [
        ("2607.123", 1),
        ("2607.123456", 1),
        ("hep-th/9901001", 1),
        ("2607.12345v1", 1),
        ("2607.12345", 0),
        ("2607.12345", -1),
        ("2607.12345", True),
        ("2607.12345", 1.0),
    ],
)
def test_identity_helpers_reject_invalid_identity(
    arxiv_id: str,
    version: object,
) -> None:
    with pytest.raises(ValueError, match="invalid arXiv identity"):
        figure_cache_key(arxiv_id, version)
    with pytest.raises(ValueError, match="invalid arXiv identity"):
        figure_html_url(arxiv_id, version)


@pytest.mark.parametrize("arxiv_id", ["2607.1234", "2607.12345"])
def test_identity_helpers_build_versioned_values(arxiv_id: str) -> None:
    assert figure_cache_key(arxiv_id, 2) == f"{arxiv_id}:v2"
    assert figure_html_url(arxiv_id, 2) == f"https://arxiv.org/html/{arxiv_id}v2"


def test_parser_extracts_only_figures_one_and_two() -> None:
    gallery = parse_figure_gallery(fixture_html(), HTML_URL, CHECKED_AT)

    assert gallery.status is FigureStatus.AVAILABLE
    assert [figure.number for figure in gallery.figures] == [1, 2]
    assert gallery.figures[0].label == "Figure 1"
    assert gallery.figures[0].caption == "The model architecture."
    assert [str(url) for url in gallery.figures[0].image_urls] == [
        "https://arxiv.org/html/2607.12345v1/x1.png"
    ]
    assert gallery.figures[1].caption == "Robot evaluation environments."
    assert [str(url) for url in gallery.figures[1].image_urls] == [
        "https://arxiv.org/html/2607.12345v1/x2-a.png",
        "https://www.arxiv.org/html/2607.12345v1/x2-b.svg",
    ]
    assert str(gallery.figures[0].source_url) == f"{HTML_URL}#S1.F1"
    assert gallery.checked_at == CHECKED_AT


def test_parser_returns_not_found_for_missing_target_figures() -> None:
    gallery = parse_figure_gallery(
        "<html><body><p>No figures</p></body></html>",
        HTML_URL,
        CHECKED_AT,
    )

    assert gallery.status is FigureStatus.NOT_FOUND
    assert gallery.figures == ()


def test_parser_ignores_cross_references_and_empty_captions() -> None:
    html = """
    <html><body>
      <p>Figure 1 shows the system.</p>
      <figure id="empty">
        <img src="x1.png">
        <figcaption>Figure 1:</figcaption>
      </figure>
    </body></html>
    """

    assert parse_figure_gallery(html, HTML_URL, CHECKED_AT).status is FigureStatus.NOT_FOUND


def test_parser_skips_target_figure_without_id() -> None:
    html = """
    <figure>
      <img src="x1.png">
      <figcaption>Figure 1: No stable source anchor.</figcaption>
    </figure>
    """

    gallery = parse_figure_gallery(html, HTML_URL, CHECKED_AT)

    assert gallery.status is FigureStatus.NOT_FOUND
    assert gallery.figures == ()


def test_parser_filters_cross_paper_and_dangerous_image_urls() -> None:
    html = """
    <figure id="S1.F1">
      <img src="safe.png">
      <img src="https://arxiv.org/html/2607.12345v2/wrong-version.png">
      <img src="https://arxiv.org/html/2607.99999v1/wrong-paper.png">
      <img src="https://attacker@arxiv.org/html/2607.12345v1/userinfo.png">
      <img src="https://arxiv.org:444/html/2607.12345v1/port.png">
      <img src="https://arxiv.org/html/2607.12345v1/fragment.png#payload">
      <img src="https://[malformed">
      <figcaption>Figure 1: Only the safe panel remains.</figcaption>
    </figure>
    """

    gallery = parse_figure_gallery(html, HTML_URL, CHECKED_AT)

    assert gallery.status is FigureStatus.AVAILABLE
    assert [str(url) for url in gallery.figures[0].image_urls] == [
        "https://arxiv.org/html/2607.12345v1/safe.png"
    ]


def test_parser_skips_target_when_all_images_are_invalid() -> None:
    html = """
    <figure id="S1.F1">
      <img src="https://example.com/external.png">
      <img src="https://arxiv.org/html/2607.12345v2/wrong-version.png">
      <figcaption>Figure 1: No usable image.</figcaption>
    </figure>
    """

    assert parse_figure_gallery(html, HTML_URL, CHECKED_AT).status is FigureStatus.NOT_FOUND


def test_parser_stably_merges_repeated_figure_panels() -> None:
    html = """
    <figure id="S2.F2-a">
      <img src="panel-a.png">
      <figcaption>Fig. 2. First caption wins.</figcaption>
    </figure>
    <figure id="S2.F2-b">
      <img src="panel-a.png">
      <img src="panel-b.png">
      <figcaption>FIGURE 2: A later caption.</figcaption>
    </figure>
    """

    gallery = parse_figure_gallery(html, HTML_URL, CHECKED_AT)

    assert len(gallery.figures) == 1
    assert gallery.figures[0].number == 2
    assert gallery.figures[0].caption == "First caption wins."
    assert str(gallery.figures[0].source_url) == f"{HTML_URL}#S2.F2-a"
    assert [str(url) for url in gallery.figures[0].image_urls] == [
        "https://arxiv.org/html/2607.12345v1/panel-a.png",
        "https://arxiv.org/html/2607.12345v1/panel-b.png",
    ]


def test_parser_requires_caption_to_start_with_target_label() -> None:
    html = """
    <figure id="S1.F1">
      <img src="x1.png">
      <figcaption>Architecture shown in Figure 1.</figcaption>
    </figure>
    """

    assert parse_figure_gallery(html, HTML_URL, CHECKED_AT).status is FigureStatus.NOT_FOUND


@respx.mock
def test_client_maps_404_to_html_unavailable() -> None:
    route = respx.get(HTML_URL).mock(return_value=httpx.Response(404))

    with make_client() as client:
        gallery = client.fetch("2607.12345", 1, CHECKED_AT)

    assert gallery.status is FigureStatus.HTML_UNAVAILABLE
    assert route.call_count == 1


@respx.mock
def test_client_maps_non_html_to_html_unavailable() -> None:
    route = respx.get(HTML_URL).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "application/pdf"},
            content=b"%PDF",
        )
    )

    with make_client() as client:
        gallery = client.fetch("2607.12345", 1, CHECKED_AT)

    assert gallery.status is FigureStatus.HTML_UNAVAILABLE
    assert route.call_count == 1


@respx.mock
def test_client_retries_5xx_then_returns_fetch_failed() -> None:
    route = respx.get(HTML_URL).mock(return_value=httpx.Response(503))

    with make_client(max_attempts=3) as client:
        gallery = client.fetch("2607.12345", 1, CHECKED_AT)

    assert gallery.status is FigureStatus.FETCH_FAILED
    assert route.call_count == 3


@respx.mock
def test_client_retries_429_then_succeeds() -> None:
    route = respx.get(HTML_URL).mock(
        side_effect=[
            httpx.Response(429),
            httpx.Response(
                200,
                headers={"content-type": "text/html; charset=utf-8"},
                text=fixture_html(),
            ),
        ]
    )

    with make_client(max_attempts=3) as client:
        gallery = client.fetch("2607.12345", 1, CHECKED_AT)

    assert gallery.status is FigureStatus.AVAILABLE
    assert route.call_count == 2


@respx.mock
def test_client_fetches_and_parses_html() -> None:
    respx.get(HTML_URL).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text=fixture_html(),
        )
    )

    with make_client() as client:
        gallery = client.fetch("2607.12345", 1, CHECKED_AT)

    assert gallery.status is FigureStatus.AVAILABLE
    assert gallery.checked_at == CHECKED_AT


@respx.mock
def test_client_timeout_is_a_non_raising_failure() -> None:
    route = respx.get(HTML_URL).mock(side_effect=httpx.ConnectTimeout("simulated timeout"))

    with make_client(max_attempts=2) as client:
        gallery = client.fetch("2607.12345", 1, CHECKED_AT)

    assert gallery.status is FigureStatus.FETCH_FAILED
    assert gallery.checked_at == CHECKED_AT
    assert route.call_count == 2


@respx.mock
def test_client_rejects_declared_oversized_html_without_raising() -> None:
    respx.get(HTML_URL).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html", "content-length": "101"},
            content=b"<html></html>",
        )
    )

    with make_client(max_html_bytes=100) as client:
        gallery = client.fetch("2607.12345", 1, CHECKED_AT)

    assert gallery.status is FigureStatus.FETCH_FAILED


@respx.mock
def test_client_stops_streaming_oversized_html_without_raising() -> None:
    respx.get(HTML_URL).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html", "content-length": "0"},
            content=b"x" * 101,
        )
    )

    with make_client(max_html_bytes=100) as client:
        gallery = client.fetch("2607.12345", 1, CHECKED_AT)

    assert gallery.status is FigureStatus.FETCH_FAILED


@respx.mock
def test_client_maps_decode_failure_to_fetch_failed() -> None:
    respx.get(HTML_URL).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=b"\xff\xfe",
        )
    )

    with make_client() as client:
        gallery = client.fetch("2607.12345", 1, CHECKED_AT)

    assert gallery.status is FigureStatus.FETCH_FAILED


def test_client_applies_throttle_using_injected_clock_and_sleep() -> None:
    now = 10.0
    sleeps: list[float] = []

    def clock() -> float:
        return now

    def sleep(seconds: float) -> None:
        nonlocal now
        sleeps.append(seconds)
        now += seconds

    transport = httpx.MockTransport(lambda _request: httpx.Response(404))
    external_client = httpx.Client(transport=transport)
    client = make_client(
        client=external_client,
        request_delay_seconds=2.5,
        clock=clock,
        sleep=sleep,
    )

    client.fetch("2607.12345", 1, CHECKED_AT)
    client.fetch("2607.12345", 1, CHECKED_AT)
    client.close()
    external_client.close()

    assert sleeps == [2.5]


def test_client_sets_request_options_and_does_not_close_external_client() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/redirect":
            return httpx.Response(302, headers={"location": HTML_URL})
        return httpx.Response(404)

    external_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = make_client(
        client=external_client,
        timeout_seconds=12.5,
    )

    gallery = client.fetch("2607.12345", 1, CHECKED_AT)
    client.close()

    assert gallery.status is FigureStatus.HTML_UNAVAILABLE
    assert requests[0].headers["user-agent"] == "VLA-WAM-Daily-Test/0.1"
    assert requests[0].extensions["timeout"] == {
        "connect": 12.5,
        "read": 12.5,
        "write": 12.5,
        "pool": 12.5,
    }
    assert external_client.is_closed is False
    external_client.close()


def test_client_follows_redirects_with_external_client() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if len(paths) == 1:
            return httpx.Response(302, headers={"location": HTML_URL})
        return httpx.Response(404)

    external_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = make_client(client=external_client)

    gallery = client.fetch("2607.12345", 1, CHECKED_AT)
    client.close()

    assert gallery.status is FigureStatus.HTML_UNAVAILABLE
    assert paths == ["/html/2607.12345v1", "/html/2607.12345v1"]
    external_client.close()


def test_client_contains_unexpected_transport_errors() -> None:
    def fail(_request: httpx.Request) -> httpx.Response:
        raise RuntimeError("unexpected transport failure")

    external_client = httpx.Client(transport=httpx.MockTransport(fail))
    client = make_client(client=external_client)

    gallery = client.fetch("2607.12345", 1, CHECKED_AT)
    client.close()
    external_client.close()

    assert gallery.status is FigureStatus.FETCH_FAILED


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("request_delay_seconds", -0.1),
        ("retry_wait_seconds", -0.1),
        ("timeout_seconds", -0.1),
        ("max_attempts", 0),
        ("max_html_bytes", 0),
    ],
)
def test_client_rejects_invalid_limits(name: str, value: float) -> None:
    with pytest.raises(ValueError):
        make_client(**{name: value})


def test_negative_cache_expires_after_24_hours() -> None:
    gallery = parse_figure_gallery("<html></html>", HTML_URL, CHECKED_AT)
    entry = FigureCacheEntry(key=figure_cache_key("2607.12345", 1), gallery=gallery)

    assert is_figure_cache_fresh(entry, CHECKED_AT + timedelta(hours=23))
    assert not is_figure_cache_fresh(entry, CHECKED_AT + timedelta(hours=24))


def test_negative_cache_handles_clock_skew_without_long_future_freshness() -> None:
    gallery = parse_figure_gallery("<html></html>", HTML_URL, CHECKED_AT)
    entry = FigureCacheEntry(key=figure_cache_key("2607.12345", 1), gallery=gallery)

    assert is_figure_cache_fresh(entry, CHECKED_AT - timedelta(minutes=5))
    assert not is_figure_cache_fresh(entry, CHECKED_AT - timedelta(hours=25))


def test_successful_cache_does_not_expire_for_same_version() -> None:
    gallery = parse_figure_gallery(fixture_html(), HTML_URL, CHECKED_AT)
    entry = FigureCacheEntry(key=figure_cache_key("2607.12345", 1), gallery=gallery)

    assert is_figure_cache_fresh(entry, CHECKED_AT + timedelta(days=365))
    assert entry.key != figure_cache_key("2607.12345", 2)
