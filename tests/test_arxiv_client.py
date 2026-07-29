from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from vla_wam_daily.arxiv_client import (
    ArxivClient,
    ArxivWindowTruncatedError,
    RetryableArxivError,
)

ARXIV_API_URL = "https://export.arxiv.org/api/query"
FIXTURE = Path(__file__).parent / "fixtures" / "arxiv_feed.xml"
USER_AGENT = "VLA-WAM-Daily/0.1 (https://github.com/example/vla-wam-daily)"


def atom_entry(
    arxiv_id: str,
    *,
    version: int | None = 1,
    updated: str = "2026-07-27T01:00:00Z",
    published: str = "2026-07-26T12:00:00Z",
    title: str = "Paper title",
    summary: str = "Paper abstract",
    authors: tuple[str, ...] = ("Ada Robot",),
    categories: tuple[str, ...] = ("cs.RO",),
    id_url: str | None = None,
    omit: frozenset[str] = frozenset(),
) -> str:
    suffix = "" if version is None else f"v{version}"
    entry_id = id_url or f"http://arxiv.org/abs/{arxiv_id}{suffix}"
    parts = ["<entry>"]
    if "id" not in omit:
        parts.append(f"<id>{escape(entry_id)}</id>")
    if "updated" not in omit:
        parts.append(f"<updated>{escape(updated)}</updated>")
    if "published" not in omit:
        parts.append(f"<published>{escape(published)}</published>")
    if "title" not in omit:
        parts.append(f"<title>{escape(title)}</title>")
    if "summary" not in omit:
        parts.append(f"<summary>{escape(summary)}</summary>")
    if "authors" not in omit:
        parts.extend(f"<author><name>{escape(author)}</name></author>" for author in authors)
    if "categories" not in omit:
        parts.extend(f'<category term="{escape(category)}"/>' for category in categories)
    parts.append("</entry>")
    return "".join(parts)


def atom_feed(
    *entries: str,
    total_results: int | None = None,
    start_index: int = 0,
    items_per_page: int | None = None,
) -> bytes:
    total = len(entries) if total_results is None else total_results
    items = len(entries) if items_per_page is None else items_per_page
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom" '
        'xmlns:arxiv="http://arxiv.org/schemas/atom" '
        'xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">'
        "<title>ArXiv Query</title>"
        f"<opensearch:totalResults>{total}</opensearch:totalResults>"
        f"<opensearch:startIndex>{start_index}</opensearch:startIndex>"
        f"<opensearch:itemsPerPage>{items}</opensearch:itemsPerPage>"
        f"{''.join(entries)}"
        "</feed>"
    ).encode()


def atom_response(content: bytes, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code,
        content=content,
        headers={"content-type": "application/atom+xml; charset=utf-8"},
    )


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@respx.mock
def test_fetch_recent_parses_normalized_versioned_paper() -> None:
    route = respx.get(ARXIV_API_URL).mock(
        return_value=httpx.Response(
            200,
            content=FIXTURE.read_bytes(),
            headers={"content-type": "application/atom+xml; charset=utf-8"},
        )
    )

    with ArxivClient(
        user_agent=USER_AGENT,
        request_delay_seconds=0,
        retries=1,
    ) as client:
        papers = client.fetch_recent(
            categories=["cs.RO"],
            since=datetime(2026, 7, 24, tzinfo=UTC),
            until=datetime(2026, 7, 28, tzinfo=UTC),
            max_results_per_category=500,
        )

    assert route.called
    assert len(papers) == 1
    paper = papers[0]
    assert paper.arxiv_id == "2607.12345"
    assert paper.version == 2
    assert paper.title == "A Vision-Language-Action Policy for Robot Manipulation"
    assert paper.abstract == (
        "We introduce a vision-language-action policy for robot manipulation."
    )
    assert paper.authors == ["Ada Robot", "Wei Model"]
    assert paper.arxiv_categories == ["cs.RO", "cs.CV"]
    assert paper.comment == "Code: https://github.com/example/vla-policy"
    assert paper.published_at == datetime(2026, 7, 26, 12, tzinfo=UTC)
    assert paper.updated_at == datetime(2026, 7, 27, 1, tzinfo=UTC)


@respx.mock
def test_fetch_by_ids_uses_stably_deduplicated_id_list() -> None:
    route = respx.get(ARXIV_API_URL).mock(
        return_value=httpx.Response(200, content=FIXTURE.read_bytes())
    )

    with ArxivClient(
        user_agent=USER_AGENT,
        request_delay_seconds=0,
        retries=1,
    ) as client:
        papers = client.fetch_by_ids(["2607.12345", "2607.12345"])

    assert route.calls.last.request.url.params["id_list"] == "2607.12345"
    assert route.calls.last.request.url.params["max_results"] == "1"
    assert len(papers) == 1


@respx.mock
def test_fetch_recent_rejects_a_truncated_inclusive_time_window() -> None:
    respx.get(ARXIV_API_URL).mock(return_value=httpx.Response(200, content=FIXTURE.read_bytes()))
    client = ArxivClient(
        user_agent=USER_AGENT,
        request_delay_seconds=0,
        retries=1,
    )

    with client, pytest.raises(ArxivWindowTruncatedError, match="cs.RO"):
        client.fetch_recent(
            categories=["cs.RO"],
            since=datetime(2026, 7, 27, 1, tzinfo=UTC),
            until=datetime(2026, 7, 28, tzinfo=UTC),
            max_results_per_category=1,
        )


def test_fetch_recent_requests_a_second_page_and_stops_on_a_short_page() -> None:
    requests: list[httpx.Request] = []
    responses = iter(
        [
            atom_response(
                atom_feed(
                    atom_entry("2607.00002", updated="2026-07-27T02:00:00Z"),
                    atom_entry("2607.00001", updated="2026-07-27T01:00:00Z"),
                    total_results=3,
                    items_per_page=2,
                )
            ),
            atom_response(
                atom_feed(
                    atom_entry("2607.00003", updated="2026-07-26T01:00:00Z"),
                    total_results=3,
                    start_index=2,
                    items_per_page=1,
                )
            ),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return next(responses)

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as http_client,
        ArxivClient(
            user_agent=USER_AGENT,
            request_delay_seconds=0,
            retries=1,
            page_size=2,
            http_client=http_client,
        ) as client,
    ):
        papers = client.fetch_recent(
            categories=["cs.RO"],
            since=datetime(2026, 7, 25, tzinfo=UTC),
            until=datetime(2026, 7, 28, tzinfo=UTC),
            max_results_per_category=10,
        )

    assert [request.url.params["start"] for request in requests] == ["0", "2"]
    assert [paper.arxiv_id for paper in papers] == [
        "2607.00002",
        "2607.00001",
        "2607.00003",
    ]


def test_fetch_recent_deduplicates_categories_and_papers_with_deterministic_sort() -> None:
    requests: list[httpx.Request] = []
    responses = iter(
        [
            atom_response(
                atom_feed(
                    atom_entry("2607.00002", updated="2026-07-27T01:00:00Z"),
                    atom_entry("2607.00003", updated="2026-07-28T01:00:00Z"),
                )
            ),
            atom_response(
                atom_feed(
                    atom_entry("2607.00001", updated="2026-07-27T01:00:00Z"),
                    atom_entry("2607.00002", updated="2026-07-27T01:00:00Z"),
                )
            ),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return next(responses)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = ArxivClient(
            user_agent=USER_AGENT,
            request_delay_seconds=0,
            retries=1,
            http_client=http_client,
        )
        papers = client.fetch_recent(
            categories=["cs.RO", "cs.RO", "cs.CV"],
            since=datetime(2026, 7, 25, tzinfo=UTC),
            until=datetime(2026, 7, 29, tzinfo=UTC),
            max_results_per_category=100,
        )

    assert [request.url.params["search_query"] for request in requests] == [
        "cat:cs.RO",
        "cat:cs.CV",
    ]
    assert [paper.arxiv_id for paper in papers] == [
        "2607.00003",
        "2607.00001",
        "2607.00002",
    ]


@respx.mock
def test_fetch_recent_keeps_both_inclusive_time_boundaries() -> None:
    respx.get(ARXIV_API_URL).mock(
        return_value=atom_response(
            atom_feed(
                atom_entry("2607.00001", updated="2026-07-25T00:00:00Z"),
                atom_entry("2607.00002", updated="2026-07-27T00:00:00Z"),
                atom_entry("2607.00003", updated="2026-07-24T23:59:59Z"),
                atom_entry("2607.00004", updated="2026-07-27T00:00:01Z"),
            )
        )
    )
    client = ArxivClient(user_agent=USER_AGENT, request_delay_seconds=0, retries=1)

    with client:
        papers = client.fetch_recent(
            categories=["cs.RO"],
            since=datetime(2026, 7, 25, tzinfo=UTC),
            until=datetime(2026, 7, 27, tzinfo=UTC),
            max_results_per_category=100,
        )

    assert {paper.arxiv_id for paper in papers} == {"2607.00001", "2607.00002"}


def test_fetch_recent_stops_when_a_full_page_crosses_since() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return atom_response(
            atom_feed(
                atom_entry("2607.00001", updated="2026-07-26T00:00:00Z"),
                atom_entry("2607.00002", updated="2026-07-24T23:59:59Z"),
                total_results=10,
                items_per_page=2,
            )
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = ArxivClient(
            user_agent=USER_AGENT,
            request_delay_seconds=0,
            retries=1,
            page_size=2,
            http_client=http_client,
        )
        papers = client.fetch_recent(
            categories=["cs.RO"],
            since=datetime(2026, 7, 25, tzinfo=UTC),
            until=datetime(2026, 7, 27, tzinfo=UTC),
            max_results_per_category=10,
        )

    assert len(requests) == 1
    assert [paper.arxiv_id for paper in papers] == ["2607.00001"]


@respx.mock
def test_fetch_by_ids_empty_iterable_does_not_request() -> None:
    route = respx.get(ARXIV_API_URL)
    client = ArxivClient(user_agent=USER_AGENT, request_delay_seconds=0, retries=1)

    with client:
        assert client.fetch_by_ids([]) == []

    assert not route.called


def test_fetch_by_ids_batches_and_preserves_requested_order() -> None:
    requests: list[httpx.Request] = []
    responses = iter(
        [
            atom_response(
                atom_feed(
                    atom_entry("2607.00002"),
                    atom_entry("2607.00003"),
                )
            ),
            atom_response(atom_feed(atom_entry("2607.00001"))),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return next(responses)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = ArxivClient(
            user_agent=USER_AGENT,
            request_delay_seconds=0,
            retries=1,
            max_ids_per_request=2,
            http_client=http_client,
        )
        papers = client.fetch_by_ids(["2607.00003", "2607.00002", "2607.00001"])

    assert [request.url.params["id_list"] for request in requests] == [
        "2607.00003,2607.00002",
        "2607.00001",
    ]
    assert [paper.arxiv_id for paper in papers] == [
        "2607.00003",
        "2607.00002",
        "2607.00001",
    ]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"user_agent": ""}, "user_agent"),
        ({"user_agent": " \t"}, "user_agent"),
        ({"request_delay_seconds": -0.01}, "request_delay_seconds"),
        ({"timeout_seconds": 0}, "timeout_seconds"),
        ({"retries": 0}, "retries"),
    ],
)
def test_constructor_rejects_invalid_config(
    overrides: dict[str, Any],
    message: str,
) -> None:
    kwargs: dict[str, Any] = {
        "user_agent": USER_AGENT,
        "request_delay_seconds": 0,
        "retries": 1,
    }
    kwargs.update(overrides)

    with pytest.raises(ValueError, match=message):
        ArxivClient(**kwargs)


@pytest.mark.parametrize("categories", [[], ["cs.RO OR all:*"], ["cs.ro"], [" cs.RO"]])
def test_fetch_recent_rejects_invalid_categories(categories: list[str]) -> None:
    client = ArxivClient(user_agent=USER_AGENT, request_delay_seconds=0, retries=1)

    with client, pytest.raises(ValueError, match="categor"):
        client.fetch_recent(
            categories=categories,
            since=datetime(2026, 7, 25, tzinfo=UTC),
            until=datetime(2026, 7, 27, tzinfo=UTC),
            max_results_per_category=100,
        )


@pytest.mark.parametrize(
    ("since", "until", "maximum", "message"),
    [
        (
            datetime(2026, 7, 25),
            datetime(2026, 7, 27, tzinfo=UTC),
            100,
            "timezone-aware",
        ),
        (
            datetime(2026, 7, 25, tzinfo=UTC),
            datetime(2026, 7, 27),
            100,
            "timezone-aware",
        ),
        (
            datetime(2026, 7, 28, tzinfo=UTC),
            datetime(2026, 7, 27, tzinfo=UTC),
            100,
            "since",
        ),
        (
            datetime(2026, 7, 25, tzinfo=UTC),
            datetime(2026, 7, 27, tzinfo=UTC),
            0,
            "max_results_per_category",
        ),
    ],
)
def test_fetch_recent_rejects_invalid_window(
    since: datetime,
    until: datetime,
    maximum: int,
    message: str,
) -> None:
    client = ArxivClient(user_agent=USER_AGENT, request_delay_seconds=0, retries=1)

    with client, pytest.raises(ValueError, match=message):
        client.fetch_recent(
            categories=["cs.RO"],
            since=since,
            until=until,
            max_results_per_category=maximum,
        )


@pytest.mark.parametrize(
    "arxiv_id",
    [
        "0703.1234",
        "2600.12345",
        "2613.12345",
        "2607.123",
        "2607.123456",
        "2607.12345v0",
        "hep-th/9901001",
        "2607.12345,all:*",
    ],
)
def test_fetch_by_ids_rejects_invalid_new_style_ids(arxiv_id: str) -> None:
    client = ArxivClient(user_agent=USER_AGENT, request_delay_seconds=0, retries=1)

    with client, pytest.raises(ValueError, match="arXiv ID"):
        client.fetch_by_ids([arxiv_id])


@respx.mock
def test_feed_entry_without_version_defaults_to_version_one() -> None:
    respx.get(ARXIV_API_URL).mock(
        return_value=atom_response(atom_feed(atom_entry("2607.12345", version=None)))
    )
    client = ArxivClient(user_agent=USER_AGENT, request_delay_seconds=0, retries=1)

    with client:
        papers = client.fetch_by_ids(["2607.12345"])

    assert papers[0].version == 1


@pytest.mark.parametrize(
    "entry_id",
    [
        "http://arxiv.org/abs/0703.1234v1",
        "http://arxiv.org/abs/2613.12345v1",
        "https://evil.example/abs/2607.12345v1",
        "http://user@arxiv.org/abs/2607.12345v1",
        "http://arxiv.org/abs/2607.12345v1?download=1",
    ],
)
@respx.mock
def test_feed_rejects_noncanonical_entry_ids(entry_id: str) -> None:
    respx.get(ARXIV_API_URL).mock(
        return_value=atom_response(atom_feed(atom_entry("2607.12345", id_url=entry_id)))
    )
    client = ArxivClient(user_agent=USER_AGENT, request_delay_seconds=0, retries=1)

    with client, pytest.raises(ValueError, match="entry ID"):
        client.fetch_by_ids(["2607.12345"])


@respx.mock
def test_malformed_feed_is_rejected_even_if_feedparser_recovers_an_entry() -> None:
    malformed = (
        b'<feed xmlns="http://www.w3.org/2005/Atom">'
        + atom_entry("2607.12345").encode()
        + b"</broken>"
    )
    respx.get(ARXIV_API_URL).mock(return_value=atom_response(malformed))
    client = ArxivClient(user_agent=USER_AGENT, request_delay_seconds=0, retries=1)

    with client, pytest.raises(ValueError, match="invalid arXiv feed"):
        client.fetch_by_ids(["2607.12345"])


@pytest.mark.parametrize(
    "missing",
    ["id", "updated", "published", "title", "summary", "authors", "categories"],
)
@respx.mock
def test_feed_rejects_entries_missing_critical_fields(missing: str) -> None:
    respx.get(ARXIV_API_URL).mock(
        return_value=atom_response(atom_feed(atom_entry("2607.12345", omit=frozenset({missing}))))
    )
    client = ArxivClient(user_agent=USER_AGENT, request_delay_seconds=0, retries=1)

    with client, pytest.raises(ValueError, match="entry"):
        client.fetch_by_ids(["2607.12345"])


@respx.mock
def test_empty_feed_that_claims_results_is_rejected() -> None:
    respx.get(ARXIV_API_URL).mock(
        return_value=atom_response(atom_feed(total_results=1, items_per_page=0))
    )
    client = ArxivClient(user_agent=USER_AGENT, request_delay_seconds=0, retries=1)

    with client, pytest.raises(ValueError, match="claims"):
        client.fetch_recent(
            categories=["cs.RO"],
            since=datetime(2026, 7, 25, tzinfo=UTC),
            until=datetime(2026, 7, 27, tzinfo=UTC),
            max_results_per_category=100,
        )


@pytest.mark.parametrize("status_code", [429, 503])
@respx.mock
def test_retryable_status_is_retried(status_code: int) -> None:
    route = respx.get(ARXIV_API_URL).mock(
        side_effect=[
            httpx.Response(status_code),
            atom_response(atom_feed(atom_entry("2607.12345"))),
        ]
    )
    client = ArxivClient(
        user_agent=USER_AGENT,
        request_delay_seconds=0,
        retry_wait_seconds=0,
        retries=2,
    )

    with client:
        papers = client.fetch_by_ids(["2607.12345"])

    assert len(route.calls) == 2
    assert len(papers) == 1


@respx.mock
def test_network_failure_is_retried() -> None:
    route = respx.get(ARXIV_API_URL).mock(
        side_effect=[
            httpx.ConnectTimeout("simulated network timeout"),
            atom_response(atom_feed(atom_entry("2607.12345"))),
        ]
    )
    client = ArxivClient(
        user_agent=USER_AGENT,
        request_delay_seconds=0,
        retry_wait_seconds=0,
        retries=2,
    )

    with client:
        papers = client.fetch_by_ids(["2607.12345"])

    assert len(route.calls) == 2
    assert len(papers) == 1


@respx.mock
def test_retryable_status_exhaustion_raises_clear_error() -> None:
    route = respx.get(ARXIV_API_URL).mock(return_value=httpx.Response(503))
    client = ArxivClient(
        user_agent=USER_AGENT,
        request_delay_seconds=0,
        retry_wait_seconds=0,
        retries=2,
    )

    with client, pytest.raises(RetryableArxivError, match="503"):
        client.fetch_by_ids(["2607.12345"])

    assert len(route.calls) == 2


@respx.mock
def test_nonretryable_400_is_not_retried() -> None:
    route = respx.get(ARXIV_API_URL).mock(return_value=httpx.Response(400))
    client = ArxivClient(
        user_agent=USER_AGENT,
        request_delay_seconds=0,
        retry_wait_seconds=0,
        retries=3,
    )

    with client, pytest.raises(httpx.HTTPStatusError):
        client.fetch_by_ids(["2607.12345"])

    assert len(route.calls) == 1


@respx.mock
def test_redirect_is_rejected_without_following_location() -> None:
    route = respx.get(ARXIV_API_URL).mock(
        return_value=httpx.Response(302, headers={"location": ARXIV_API_URL})
    )
    client = ArxivClient(
        user_agent=USER_AGENT,
        request_delay_seconds=0,
        retries=3,
    )

    with client, pytest.raises(httpx.HTTPStatusError, match="redirect"):
        client.fetch_by_ids(["2607.12345"])

    assert len(route.calls) == 1


@respx.mock
def test_global_throttle_covers_categories_and_followup_id_requests() -> None:
    fake_time = FakeTime()
    respx.get(ARXIV_API_URL).mock(return_value=atom_response(atom_feed(atom_entry("2607.12345"))))
    client = ArxivClient(
        user_agent=USER_AGENT,
        request_delay_seconds=2.5,
        retries=1,
        sleep=fake_time.sleep,
        clock=fake_time.clock,
    )

    with client:
        client.fetch_recent(
            categories=["cs.RO", "cs.CV"],
            since=datetime(2026, 7, 25, tzinfo=UTC),
            until=datetime(2026, 7, 27, 2, tzinfo=UTC),
            max_results_per_category=100,
        )
        client.fetch_by_ids(["2607.12345"])

    assert fake_time.sleeps == [2.5, 2.5]


@respx.mock
def test_global_throttle_also_covers_retry_attempts() -> None:
    fake_time = FakeTime()
    respx.get(ARXIV_API_URL).mock(
        side_effect=[
            httpx.Response(503),
            atom_response(atom_feed(atom_entry("2607.12345"))),
        ]
    )
    client = ArxivClient(
        user_agent=USER_AGENT,
        request_delay_seconds=2.0,
        retry_wait_seconds=0,
        retries=2,
        sleep=fake_time.sleep,
        clock=fake_time.clock,
    )

    with client:
        client.fetch_by_ids(["2607.12345"])

    assert fake_time.sleeps == [2.0]


def test_client_only_closes_owned_http_client() -> None:
    external = httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(400)))
    wrapper = ArxivClient(
        user_agent=USER_AGENT,
        request_delay_seconds=0,
        retries=1,
        http_client=external,
    )

    wrapper.close()
    assert not external.is_closed
    external.close()

    owned = ArxivClient(
        user_agent=USER_AGENT,
        request_delay_seconds=0,
        retries=1,
    )
    owned_http = owned.http
    owned.close()
    assert owned_http.is_closed


@respx.mock
def test_oversized_response_is_rejected() -> None:
    route = respx.get(ARXIV_API_URL).mock(
        return_value=atom_response(atom_feed(atom_entry("2607.12345")))
    )
    client = ArxivClient(
        user_agent=USER_AGENT,
        request_delay_seconds=0,
        retries=1,
        max_response_bytes=32,
    )

    with client, pytest.raises(ValueError, match="too large"):
        client.fetch_by_ids(["2607.12345"])

    assert len(route.calls) == 1


@respx.mock
def test_non_xml_response_content_type_is_rejected() -> None:
    respx.get(ARXIV_API_URL).mock(
        return_value=httpx.Response(
            200,
            content=atom_feed(atom_entry("2607.12345")),
            headers={"content-type": "text/html"},
        )
    )
    client = ArxivClient(user_agent=USER_AGENT, request_delay_seconds=0, retries=1)

    with client, pytest.raises(ValueError, match="content type"):
        client.fetch_by_ids(["2607.12345"])


def test_request_uses_fixed_endpoint_user_agent_and_sorting() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return atom_response(atom_feed())

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = ArxivClient(
            user_agent=USER_AGENT,
            request_delay_seconds=0,
            retries=1,
            http_client=http_client,
        )
        client.fetch_recent(
            categories=["cs.LG"],
            since=datetime(2026, 7, 25, tzinfo=UTC),
            until=datetime(2026, 7, 27, tzinfo=UTC),
            max_results_per_category=100,
        )

    assert len(requests) == 1
    request = requests[0]
    assert request.url.copy_with(query=None) == httpx.URL(ARXIV_API_URL)
    assert request.headers["user-agent"] == USER_AGENT
    assert request.url.params["sortBy"] == "lastUpdatedDate"
    assert request.url.params["sortOrder"] == "descending"
