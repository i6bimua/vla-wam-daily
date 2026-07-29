import json
import threading
import time
from collections.abc import Iterable
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import vla_wam_daily.pipeline as pipeline_module
from tests.factories import make_gallery, make_record
from tests.test_analyzer import VALID_AI_PAYLOAD
from vla_wam_daily.config import AppConfig, load_config
from vla_wam_daily.figures import figure_cache_key
from vla_wam_daily.models import (
    AnalyzedPaperRecord,
    CacheEntry,
    FigureCacheEntry,
    FigureGallery,
    FigureStatus,
    PaperRecord,
    RawPaper,
    RunStats,
    TokenUsage,
)
from vla_wam_daily.pipeline import CandidateLimitError, QualityGateError, run_daily
from vla_wam_daily.storage import cache_key, load_cache, load_data_file, load_figure_cache

NOW = datetime(2026, 7, 30, 2, 30, tzinfo=UTC)
PROMPT = "Return one valid JSON object."


def raw_paper(
    arxiv_id: str = "2607.12345",
    *,
    version: int = 1,
    published_at: datetime | None = None,
    title: str | None = None,
    matched: bool = True,
) -> RawPaper:
    published = published_at or datetime(2026, 7, 29, tzinfo=UTC)
    keyword = "vision-language-action policy" if matched else "spectral graph theorem"
    return RawPaper(
        arxiv_id=arxiv_id,
        version=version,
        published_at=published,
        updated_at=published + timedelta(hours=1),
        title=title or f"{keyword.title()} for {arxiv_id}",
        authors=["Ada Robot"],
        arxiv_categories=["cs.RO"],
        abstract=f"We study a {keyword} for robot manipulation.",
    )


def analyzed_record(
    arxiv_id: str = "2607.12345",
    *,
    version: int = 1,
    score: int = 8,
) -> AnalyzedPaperRecord:
    payload = make_record(
        arxiv_id=arxiv_id,
        version=version,
        score=score,
    ).model_dump(mode="json")
    payload.pop("figure_gallery")
    return AnalyzedPaperRecord.model_validate(payload)


def analysis_entry(record: AnalyzedPaperRecord) -> tuple[str, CacheEntry]:
    key = cache_key(
        record.arxiv_id,
        record.version,
        record.provenance.model,
        record.provenance.prompt_version,
    )
    return key, CacheEntry(key=key, record=record)


def figure_entry(gallery: FigureGallery) -> tuple[str, FigureCacheEntry]:
    path = gallery.html_url.path
    assert path is not None
    identity = path.removeprefix("/html/")
    arxiv_id, version_text = identity.rsplit("v", maxsplit=1)
    key = figure_cache_key(arxiv_id, int(version_text))
    return key, FigureCacheEntry(key=key, gallery=gallery)


def configured(
    *,
    max_candidates: int = 60,
    max_concurrency: int = 3,
    max_failure_ratio: float = 0.30,
) -> AppConfig:
    config = load_config(Path("config/topics.yaml"))
    config.analysis.max_candidates = max_candidates
    config.analysis.max_concurrency = max_concurrency
    config.analysis.max_failure_ratio = max_failure_ratio
    return config


class FakeFetcher:
    def __init__(
        self,
        recent: list[RawPaper] | None = None,
        forced: list[RawPaper] | None = None,
    ) -> None:
        self.recent = recent or []
        self.forced = forced or []
        self.recent_calls: list[dict[str, object]] = []
        self.forced_calls: list[tuple[str, ...]] = []

    def fetch_recent(self, **kwargs: object) -> list[RawPaper]:
        self.recent_calls.append(kwargs)
        return list(self.recent)

    def fetch_by_ids(self, arxiv_ids: Iterable[str]) -> list[RawPaper]:
        ids = tuple(arxiv_ids)
        self.forced_calls.append(ids)
        return [paper for paper in self.forced if paper.arxiv_id in ids]


class ProgrammableAnalysisClient:
    model = "deepseek-v4-pro"

    def __init__(
        self,
        *,
        scores: dict[str, int] | None = None,
        failures: dict[str, Exception] | None = None,
        delays: dict[str, float] | None = None,
        usage: TokenUsage | None = None,
    ) -> None:
        self.scores = scores or {}
        self.failures = failures or {}
        self.delays = delays or {}
        self.usage = usage or TokenUsage(
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
        )
        self.calls: list[dict[str, object]] = []
        self._lock = threading.Lock()

    def analyze(
        self,
        *,
        system_prompt: str,
        paper_json: str,
    ) -> tuple[dict[str, object], TokenUsage]:
        paper = json.loads(paper_json)
        arxiv_id = paper["arxiv_id"]
        with self._lock:
            self.calls.append(paper)
        time.sleep(self.delays.get(arxiv_id, 0))
        failure = self.failures.get(arxiv_id)
        if failure is not None:
            raise failure
        payload = deepcopy(VALID_AI_PAYLOAD)
        analysis = payload["analysis"]
        assert isinstance(analysis, dict)
        analysis["relevance_score"] = self.scores.get(arxiv_id, 8)
        payload["title_zh"] = f"论文 {arxiv_id}"
        return payload, self.usage


class StopPipeline(BaseException):
    pass


class FakeFigureFetcher:
    def __init__(
        self,
        results: dict[tuple[str, int], FigureGallery | object | BaseException] | None = None,
    ) -> None:
        self.results = results or {}
        self.calls: list[tuple[str, int, datetime]] = []

    def fetch(
        self,
        arxiv_id: str,
        version: int,
        checked_at: datetime,
    ) -> FigureGallery:
        self.calls.append((arxiv_id, version, checked_at))
        result = self.results.get(
            (arxiv_id, version),
            make_gallery(arxiv_id=arxiv_id, version=version),
        )
        if isinstance(result, BaseException):
            raise result
        return result  # type: ignore[return-value]


def run(
    tmp_path: Path,
    *,
    fetcher: FakeFetcher,
    analysis_client: ProgrammableAnalysisClient | None = None,
    figure_fetcher: FakeFigureFetcher | None = None,
    config: AppConfig | None = None,
    threshold: int = 6,
    force_ids: list[str] | None = None,
    dry_run: bool = True,
):
    return run_daily(
        config=config or configured(),
        data_dir=tmp_path,
        fetcher=fetcher,
        analysis_client=analysis_client or ProgrammableAnalysisClient(),
        figure_fetcher=figure_fetcher or FakeFigureFetcher(),
        prompt=PROMPT,
        lookback_days=3,
        threshold=threshold,
        force_ids=force_ids or [],
        dry_run=dry_run,
        now=NOW,
    )


def test_recent_and_forced_results_are_deduplicated_by_version_and_stably_ordered(
    tmp_path: Path,
) -> None:
    duplicate_recent = raw_paper("2607.10002", title="Recent duplicate vision-language-action")
    duplicate_forced = raw_paper("2607.10002", title="Forced duplicate vision-language-action")
    fetcher = FakeFetcher(
        recent=[
            duplicate_recent,
            raw_paper("2607.10003"),
            raw_paper("2607.10003", version=2),
        ],
        forced=[raw_paper("2607.10001"), duplicate_forced],
    )
    client = ProgrammableAnalysisClient(
        delays={"2607.10001": 0.03, "2607.10002": 0.01},
    )

    report = run(
        tmp_path,
        fetcher=fetcher,
        analysis_client=client,
        force_ids=["2607.10001", "2607.10002"],
    )

    assert report.stats.fetched == 4
    assert report.stats.prefiltered == 4
    assert [(paper.arxiv_id, paper.version) for paper in report.published] == [
        ("2607.10001", 1),
        ("2607.10002", 1),
        ("2607.10003", 2),
        ("2607.10003", 1),
    ]
    duplicate_call = next(call for call in client.calls if call["arxiv_id"] == "2607.10002")
    assert duplicate_call["title"] == "Forced duplicate vision-language-action"


def test_candidate_limit_is_checked_before_any_model_or_figure_request(
    tmp_path: Path,
) -> None:
    client = ProgrammableAnalysisClient()
    figure_fetcher = FakeFigureFetcher()

    with pytest.raises(CandidateLimitError, match="2 candidates exceeds limit 1"):
        run(
            tmp_path,
            fetcher=FakeFetcher([raw_paper("2607.10001"), raw_paper("2607.10002")]),
            analysis_client=client,
            figure_fetcher=figure_fetcher,
            config=configured(max_candidates=1),
            dry_run=False,
        )

    assert client.calls == []
    assert figure_fetcher.calls == []
    assert not any(tmp_path.iterdir())


def test_analysis_cache_is_reused_but_forced_id_bypasses_it(tmp_path: Path) -> None:
    cached = analyzed_record()
    key, entry = analysis_entry(cached)
    gallery = make_gallery()
    figure_key, cached_figure = figure_entry(gallery)
    pipeline_module.save_successful_run(
        tmp_path,
        [],
        {key: entry},
        RunStats(),
        NOW - timedelta(hours=1),
        figure_cache={figure_key: cached_figure},
    )
    client = ProgrammableAnalysisClient()

    cached_report = run(
        tmp_path,
        fetcher=FakeFetcher([raw_paper()]),
        analysis_client=client,
    )
    forced_report = run(
        tmp_path,
        fetcher=FakeFetcher(forced=[raw_paper()]),
        analysis_client=client,
        force_ids=["2607.12345"],
    )

    assert cached_report.stats.cache_hits == 1
    assert cached_report.stats.model_calls == 0
    assert forced_report.stats.cache_hits == 0
    assert forced_report.stats.model_calls == 1
    assert len(client.calls) == 1


def test_unmatched_forced_paper_gets_a_traceable_synthetic_rule(tmp_path: Path) -> None:
    client = ProgrammableAnalysisClient()

    report = run(
        tmp_path,
        fetcher=FakeFetcher(forced=[raw_paper(matched=False)]),
        analysis_client=client,
        force_ids=["2607.12345"],
    )

    assert report.stats.prefiltered == 1
    assert report.published[0].matched_rules == ("forced:2607.12345",)


def test_out_of_order_analysis_completion_produces_deterministic_public_order(
    tmp_path: Path,
) -> None:
    papers = [
        raw_paper("2607.10003"),
        raw_paper("2607.10001"),
        raw_paper("2607.10002"),
    ]
    first = run(
        tmp_path,
        fetcher=FakeFetcher(papers),
        analysis_client=ProgrammableAnalysisClient(
            delays={"2607.10001": 0.03, "2607.10003": 0.01},
        ),
    )
    second = run(
        tmp_path,
        fetcher=FakeFetcher(list(reversed(papers))),
        analysis_client=ProgrammableAnalysisClient(
            delays={"2607.10002": 0.03, "2607.10001": 0.01},
        ),
    )

    expected = ["2607.10001", "2607.10002", "2607.10003"]
    assert [paper.arxiv_id for paper in first.published] == expected
    assert [paper.arxiv_id for paper in second.published] == expected


def test_failure_ratio_equal_to_limit_is_allowed_and_categories_are_counted(
    tmp_path: Path,
) -> None:
    client = ProgrammableAnalysisClient(
        failures={"2607.10002": RuntimeError("model down")},
    )

    report = run(
        tmp_path,
        fetcher=FakeFetcher([raw_paper("2607.10001"), raw_paper("2607.10002")]),
        analysis_client=client,
        config=configured(max_failure_ratio=0.5),
        dry_run=False,
    )

    assert report.stats.failed == 1
    assert report.stats.error_categories == {"RuntimeError": 1}
    assert report.stats.model_calls == 2
    assert (tmp_path / "latest.json").is_file()


def test_failure_ratio_above_limit_rejects_run_without_any_write(tmp_path: Path) -> None:
    client = ProgrammableAnalysisClient(
        failures={"2607.10002": RuntimeError("model down")},
    )

    with pytest.raises(QualityGateError, match="50.0% exceeds 49.0%"):
        run(
            tmp_path,
            fetcher=FakeFetcher([raw_paper("2607.10001"), raw_paper("2607.10002")]),
            analysis_client=client,
            config=configured(max_failure_ratio=0.49),
            dry_run=False,
        )

    assert not any(tmp_path.iterdir())


def test_multiple_analysis_failure_categories_and_token_usage_are_aggregated(
    tmp_path: Path,
) -> None:
    usage = TokenUsage(prompt_tokens=11, completion_tokens=7, total_tokens=18)
    client = ProgrammableAnalysisClient(
        failures={
            "2607.10003": RuntimeError("down"),
            "2607.10004": ValueError("bad payload"),
        },
        usage=usage,
    )
    papers = [raw_paper(f"2607.1000{index}") for index in range(1, 5)]

    report = run(
        tmp_path,
        fetcher=FakeFetcher(papers),
        analysis_client=client,
        config=configured(max_failure_ratio=0.5),
    )

    assert report.stats.model_calls == 4
    assert report.stats.failed == 2
    assert report.stats.error_categories == {"RuntimeError": 1, "ValueError": 1}
    assert report.stats.prompt_tokens == 22
    assert report.stats.completion_tokens == 14
    assert report.stats.total_tokens == 36


def test_zero_candidates_and_all_cached_candidates_make_no_model_calls(
    tmp_path: Path,
) -> None:
    empty_client = ProgrammableAnalysisClient()
    empty = run(
        tmp_path / "empty",
        fetcher=FakeFetcher([raw_paper(matched=False)]),
        analysis_client=empty_client,
    )
    assert empty.published == []
    assert empty.stats.prefiltered == 0
    assert empty.stats.model_calls == 0

    records = [analyzed_record("2607.10001"), analyzed_record("2607.10002")]
    analysis_cache = dict(analysis_entry(record) for record in records)
    figure_cache = dict(
        figure_entry(make_gallery(arxiv_id=record.arxiv_id, version=record.version))
        for record in records
    )
    cached_dir = tmp_path / "cached"
    pipeline_module.save_successful_run(
        cached_dir,
        [],
        analysis_cache,
        RunStats(),
        NOW - timedelta(hours=1),
        figure_cache=figure_cache,
    )
    cached_client = ProgrammableAnalysisClient()
    cached_figure_fetcher = FakeFigureFetcher()

    cached = run(
        cached_dir,
        fetcher=FakeFetcher([raw_paper(record.arxiv_id) for record in records]),
        analysis_client=cached_client,
        figure_fetcher=cached_figure_fetcher,
    )

    assert cached.stats.cache_hits == 2
    assert cached.stats.figure_cache_hits == 2
    assert cached.stats.model_calls == 0
    assert cached.stats.total_tokens == 0
    assert cached_client.calls == []
    assert cached_figure_fetcher.calls == []


def test_threshold_is_inclusive_and_figures_are_only_requested_after_it(
    tmp_path: Path,
) -> None:
    figure_fetcher = FakeFigureFetcher()

    report = run(
        tmp_path,
        fetcher=FakeFetcher([raw_paper("2607.10005"), raw_paper("2607.10006")]),
        analysis_client=ProgrammableAnalysisClient(
            scores={"2607.10005": 5, "2607.10006": 6},
        ),
        figure_fetcher=figure_fetcher,
        threshold=6,
    )

    assert [paper.arxiv_id for paper in report.published] == ["2607.10006"]
    assert [(arxiv_id, version) for arxiv_id, version, _ in figure_fetcher.calls] == [
        ("2607.10006", 1)
    ]


def test_dry_run_returns_checked_public_records_without_writing(tmp_path: Path) -> None:
    report = run(
        tmp_path,
        fetcher=FakeFetcher([raw_paper()]),
        dry_run=True,
    )

    assert report.dry_run is True
    assert len(report.published) == 1
    assert isinstance(report.published[0], PaperRecord)
    assert report.published[0].figure_gallery.status is FigureStatus.AVAILABLE
    assert not any(tmp_path.iterdir())


@pytest.mark.parametrize(
    ("age", "expected_requests", "expected_cache_hits"),
    [
        (timedelta(hours=23, minutes=59), 0, 1),
        (timedelta(hours=24), 1, 0),
    ],
)
def test_negative_figure_cache_obeys_its_ttl(
    tmp_path: Path,
    age: timedelta,
    expected_requests: int,
    expected_cache_hits: int,
) -> None:
    record = analyzed_record()
    analysis_key, cached_analysis = analysis_entry(record)
    gallery = make_gallery(status=FigureStatus.NOT_FOUND).model_copy(
        update={"checked_at": NOW - age}
    )
    figure_key, cached_figure = figure_entry(gallery)
    pipeline_module.save_successful_run(
        tmp_path,
        [],
        {analysis_key: cached_analysis},
        RunStats(),
        NOW - timedelta(days=2),
        figure_cache={figure_key: cached_figure},
    )
    fetcher = FakeFigureFetcher()

    report = run(
        tmp_path,
        fetcher=FakeFetcher([raw_paper()]),
        figure_fetcher=fetcher,
    )

    assert report.stats.figure_requests == expected_requests
    assert report.stats.figure_cache_hits == expected_cache_hits
    assert len(fetcher.calls) == expected_requests


def test_new_paper_version_does_not_reuse_old_figure_cache_entry(tmp_path: Path) -> None:
    record = analyzed_record(version=2)
    analysis_key, cached_analysis = analysis_entry(record)
    old_key, old_figure = figure_entry(make_gallery(version=1))
    pipeline_module.save_successful_run(
        tmp_path,
        [],
        {analysis_key: cached_analysis},
        RunStats(),
        NOW - timedelta(hours=1),
        figure_cache={old_key: old_figure},
    )
    fetcher = FakeFigureFetcher()

    report = run(
        tmp_path,
        fetcher=FakeFetcher([raw_paper(version=2)]),
        figure_fetcher=fetcher,
    )

    assert report.stats.figure_requests == 1
    assert [(arxiv_id, version) for arxiv_id, version, _ in fetcher.calls] == [("2607.12345", 2)]


@pytest.mark.parametrize(
    "bad_result",
    [
        RuntimeError("network broke"),
        make_gallery(arxiv_id="2607.99999"),
        object(),
    ],
    ids=["fetch-exception", "identity-mismatch", "invalid-gallery"],
)
def test_figure_exceptions_and_invalid_results_degrade_to_correct_fetch_failed_gallery(
    tmp_path: Path,
    bad_result: object,
) -> None:
    figure_fetcher = FakeFigureFetcher({("2607.12345", 1): bad_result})

    report = run(
        tmp_path,
        fetcher=FakeFetcher([raw_paper()]),
        figure_fetcher=figure_fetcher,
        dry_run=False,
    )

    gallery = report.published[0].figure_gallery
    assert gallery.status is FigureStatus.FETCH_FAILED
    assert str(gallery.html_url) == "https://arxiv.org/html/2607.12345v1"
    assert gallery.checked_at == NOW
    assert report.stats.figure_failed == 1
    assert report.stats.failed == 0
    assert report.stats.error_categories == {}
    persisted = load_data_file(tmp_path / "latest.json")
    assert persisted is not None
    assert persisted.papers[0].figure_gallery == gallery


def test_figure_fetch_does_not_swallow_base_exception(tmp_path: Path) -> None:
    figure_fetcher = FakeFigureFetcher(
        {("2607.12345", 1): StopPipeline("cancelled")},
    )

    with pytest.raises(StopPipeline, match="cancelled"):
        run(
            tmp_path,
            fetcher=FakeFetcher([raw_paper()]),
            figure_fetcher=figure_fetcher,
            dry_run=False,
        )

    assert not any(tmp_path.iterdir())


def test_figure_status_counters_are_separate_from_analysis_failures(
    tmp_path: Path,
) -> None:
    papers = [raw_paper(f"2607.1000{index}") for index in range(1, 5)]
    statuses = [
        FigureStatus.AVAILABLE,
        FigureStatus.NOT_FOUND,
        FigureStatus.HTML_UNAVAILABLE,
        FigureStatus.FETCH_FAILED,
    ]
    results = {
        (paper.arxiv_id, paper.version): make_gallery(
            arxiv_id=paper.arxiv_id,
            version=paper.version,
            status=status,
        )
        for paper, status in zip(papers, statuses, strict=True)
    }

    report = run(
        tmp_path,
        fetcher=FakeFetcher(papers),
        figure_fetcher=FakeFigureFetcher(results),
    )

    assert report.stats.figure_requests == 4
    assert report.stats.figure_available == 1
    assert report.stats.figure_unavailable == 2
    assert report.stats.figure_failed == 1
    assert report.stats.failed == 0


def test_successful_non_dry_run_saves_analysis_and_figure_caches_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def save_spy(*args: Any, **kwargs: Any) -> None:
        calls.append((args, kwargs))

    monkeypatch.setattr(pipeline_module, "save_successful_run", save_spy)

    report = run(
        tmp_path,
        fetcher=FakeFetcher([raw_paper("2607.10001"), raw_paper("2607.10002")]),
        dry_run=False,
    )

    assert len(calls) == 1
    args, kwargs = calls[0]
    saved_published = args[1]
    saved_analysis_cache = args[2]
    saved_figure_cache = kwargs["figure_cache"]
    assert len(saved_published) == 2
    assert all(isinstance(record, PaperRecord) for record in saved_published)
    assert all(record.figure_gallery is not None for record in saved_published)
    assert len(saved_analysis_cache) == 2
    assert all(type(entry.record) is AnalyzedPaperRecord for entry in saved_analysis_cache.values())
    assert len(saved_figure_cache) == 2
    assert report.stats.published == 2


def test_persisted_success_contains_both_caches_and_only_checked_public_records(
    tmp_path: Path,
) -> None:
    report = run(
        tmp_path,
        fetcher=FakeFetcher([raw_paper()]),
        dry_run=False,
    )

    assert len(load_cache(tmp_path)) == 1
    assert len(load_figure_cache(tmp_path)) == 1
    latest = load_data_file(tmp_path / "latest.json")
    assert latest is not None
    assert latest.papers == tuple(report.published)
    assert all(isinstance(paper.figure_gallery, FigureGallery) for paper in latest.papers)
