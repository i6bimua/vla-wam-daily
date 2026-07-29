import json
import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Protocol

from pydantic import HttpUrl

from vla_wam_daily.analyzer import AnalysisClient, analyze_paper
from vla_wam_daily.config import AppConfig
from vla_wam_daily.figures import (
    figure_cache_key,
    figure_html_url,
    is_figure_cache_fresh,
)
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
from vla_wam_daily.prefilter import match_paper
from vla_wam_daily.storage import (
    cache_key,
    load_cache,
    load_figure_cache,
    save_successful_run,
)

LOGGER = logging.getLogger(__name__)
ARXIV_SELECTOR_RE = re.compile(
    r"(?P<year>\d{2})(?P<month>0[1-9]|1[0-2])"
    r"\.(?P<number>\d{4,5})(?:v(?P<version>[1-9]\d*))?"
)


class CandidateLimitError(RuntimeError):
    pass


class QualityGateError(RuntimeError):
    pass


class Fetcher(Protocol):
    def fetch_recent(
        self,
        *,
        categories: list[str],
        since: datetime,
        until: datetime,
        max_results_per_category: int,
    ) -> list[RawPaper]: ...

    def fetch_by_ids(self, arxiv_ids: Iterable[str]) -> list[RawPaper]: ...


class FigureFetcher(Protocol):
    def fetch(
        self,
        arxiv_id: str,
        version: int,
        checked_at: datetime,
    ) -> FigureGallery: ...


@dataclass(frozen=True)
class _ForceSelector:
    raw: str
    arxiv_id: str
    version: int | None

    def matches(self, identity: tuple[str, int]) -> bool:
        arxiv_id, version = identity
        return arxiv_id == self.arxiv_id and (self.version is None or version == self.version)


@dataclass(frozen=True)
class RunReport:
    stats: RunStats
    published: tuple[PaperRecord, ...]
    dry_run: bool


@dataclass(frozen=True)
class FigureEnrichment:
    records: tuple[PaperRecord, ...]
    cache: Mapping[str, FigureCacheEntry]
    cache_hits: int
    requests: int
    available: int
    unavailable: int
    failed: int


def _require_bounded_integer(
    value: object,
    *,
    name: str,
    minimum: int,
    maximum: int,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer from {minimum} through {maximum}")
    return value


def _parse_force_selector(value: object) -> _ForceSelector:
    if type(value) is not str:
        raise TypeError("force_ids members must be strings")
    if value != value.strip():
        raise ValueError("force_ids members must be trimmed")
    match = ARXIV_SELECTOR_RE.fullmatch(value)
    if match is None or int(match.group("year") + match.group("month")) < 704:
        raise ValueError(f"invalid modern arXiv selector: {value!r}")
    arxiv_id = f"{match.group('year')}{match.group('month')}.{match.group('number')}"
    version_text = match.group("version")
    return _ForceSelector(
        raw=value,
        arxiv_id=arxiv_id,
        version=int(version_text) if version_text is not None else None,
    )


def normalize_force_ids(force_ids: object) -> list[str]:
    if type(force_ids) is not list:
        raise TypeError("force_ids must be a list")

    normalized: list[str] = []
    seen: set[str] = set()
    for value in force_ids:
        selector = _parse_force_selector(value)
        if selector.raw not in seen:
            seen.add(selector.raw)
            normalized.append(selector.raw)
    return normalized


def _validate_run_inputs(
    *,
    data_dir: object,
    prompt: object,
    lookback_days: object,
    threshold: object,
    force_ids: object,
    dry_run: object,
    now: object,
) -> tuple[Path, str, int, int, list[str], tuple[_ForceSelector, ...], bool, datetime]:
    if not isinstance(data_dir, Path):
        raise TypeError("data_dir must be a Path")
    if not isinstance(now, datetime):
        raise TypeError("now must be a datetime")
    if now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    normalized_now = now.astimezone(UTC)
    normalized_lookback = _require_bounded_integer(
        lookback_days,
        name="lookback_days",
        minimum=1,
        maximum=31,
    )
    normalized_threshold = _require_bounded_integer(
        threshold,
        name="threshold",
        minimum=1,
        maximum=10,
    )
    if type(dry_run) is not bool:
        raise TypeError("dry_run must be a bool")
    if not isinstance(prompt, str):
        raise TypeError("prompt must be a string")
    if not prompt.strip():
        raise ValueError("prompt must not be blank")
    normalized_force_ids = normalize_force_ids(force_ids)
    selectors = tuple(_parse_force_selector(value) for value in normalized_force_ids)
    return (
        data_dir,
        prompt,
        normalized_lookback,
        normalized_threshold,
        normalized_force_ids,
        selectors,
        dry_run,
        normalized_now,
    )


def _paper_preference_key(paper: RawPaper) -> str:
    return json.dumps(
        paper.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _deduplicate_papers(papers: Sequence[RawPaper]) -> dict[tuple[str, int], RawPaper]:
    deduplicated: dict[tuple[str, int], RawPaper] = {}
    for paper in papers:
        snapshot = RawPaper.model_validate(paper.model_dump(mode="python", round_trip=True))
        identity = snapshot.arxiv_id, snapshot.version
        current = deduplicated.get(identity)
        if current is None or _paper_preference_key(snapshot) > _paper_preference_key(current):
            deduplicated[identity] = snapshot
    return deduplicated


def _collect_papers(
    recent: Sequence[RawPaper],
    forced: Sequence[RawPaper],
) -> list[RawPaper]:
    papers_by_identity = _deduplicate_papers(recent)
    papers_by_identity.update(_deduplicate_papers(forced))
    return [papers_by_identity[identity] for identity in sorted(papers_by_identity)]


def _publication_order(record: AnalyzedPaperRecord) -> tuple[float, int, str, int]:
    return (
        -record.published_at.timestamp(),
        -record.analysis.relevance_score,
        record.arxiv_id,
        -record.version,
    )


def _sum_usage(total: TokenUsage, item: TokenUsage) -> TokenUsage:
    return TokenUsage(
        prompt_tokens=total.prompt_tokens + item.prompt_tokens,
        completion_tokens=total.completion_tokens + item.completion_tokens,
        total_tokens=total.total_tokens + item.total_tokens,
    )


def _public_record(
    record: AnalyzedPaperRecord,
    gallery: FigureGallery,
) -> PaperRecord:
    return PaperRecord.model_validate(
        {
            **record.model_dump(mode="python", round_trip=True),
            "figure_gallery": gallery.model_dump(mode="python", round_trip=True),
        }
    )


def _fetch_failed_gallery(
    record: AnalyzedPaperRecord,
    now: datetime,
) -> FigureGallery:
    return FigureGallery(
        status=FigureStatus.FETCH_FAILED,
        html_url=HttpUrl(figure_html_url(record.arxiv_id, record.version)),
        checked_at=now,
    )


def _validated_fetched_gallery(value: object) -> FigureGallery:
    if isinstance(value, FigureGallery):
        value = value.model_dump(mode="python", round_trip=True)
    return FigureGallery.model_validate(value)


def enrich_figures(
    records: Sequence[AnalyzedPaperRecord],
    *,
    figure_fetcher: FigureFetcher,
    cache: Mapping[str, FigureCacheEntry],
    now: datetime,
) -> FigureEnrichment:
    updated_cache = dict(cache)
    enriched: list[PaperRecord] = []
    cache_hits = 0
    requests = 0
    available = 0
    unavailable = 0
    failed = 0

    for record in records:
        key = figure_cache_key(record.arxiv_id, record.version)
        entry = updated_cache.get(key)
        if entry is not None and is_figure_cache_fresh(entry, now):
            gallery = entry.gallery
            public_record = _public_record(record, gallery)
            cache_hits += 1
        else:
            requests += 1
            try:
                gallery = _validated_fetched_gallery(
                    figure_fetcher.fetch(record.arxiv_id, record.version, now)
                )
                public_record = _public_record(record, gallery)
                entry = FigureCacheEntry(key=key, gallery=gallery)
            except Exception:
                LOGGER.warning(
                    "figure enrichment failed for %sv%s; using fetch_failed",
                    record.arxiv_id,
                    record.version,
                    exc_info=True,
                )
                gallery = _fetch_failed_gallery(record, now)
                public_record = _public_record(record, gallery)
                entry = FigureCacheEntry(key=key, gallery=gallery)
            updated_cache[key] = entry

        if gallery.status is FigureStatus.AVAILABLE:
            available += 1
        elif gallery.status is FigureStatus.FETCH_FAILED:
            failed += 1
        else:
            unavailable += 1
        enriched.append(public_record)

    return FigureEnrichment(
        records=tuple(enriched),
        cache=MappingProxyType(dict(updated_cache)),
        cache_hits=cache_hits,
        requests=requests,
        available=available,
        unavailable=unavailable,
        failed=failed,
    )


def run_daily(
    *,
    config: AppConfig,
    data_dir: Path,
    fetcher: Fetcher,
    analysis_client: AnalysisClient,
    figure_fetcher: FigureFetcher,
    prompt: str,
    lookback_days: int,
    threshold: int,
    force_ids: list[str],
    dry_run: bool,
    now: datetime,
) -> RunReport:
    (
        data_dir,
        prompt,
        lookback_days,
        threshold,
        normalized_force_ids,
        force_selectors,
        dry_run,
        now,
    ) = _validate_run_inputs(
        data_dir=data_dir,
        prompt=prompt,
        lookback_days=lookback_days,
        threshold=threshold,
        force_ids=force_ids,
        dry_run=dry_run,
        now=now,
    )
    recent = fetcher.fetch_recent(
        categories=list(config.arxiv.categories),
        since=now - timedelta(days=lookback_days),
        until=now,
        max_results_per_category=config.arxiv.max_results_per_category,
    )
    forced = fetcher.fetch_by_ids(normalized_force_ids)
    forced_by_identity = _deduplicate_papers(forced)
    forced_rules_by_identity: dict[tuple[str, int], list[str]] = {}
    for identity in sorted(forced_by_identity):
        matching_selectors = [
            selector for selector in force_selectors if selector.matches(identity)
        ]
        if not matching_selectors:
            arxiv_id, version = identity
            raise ValueError(
                f"forced arXiv result {arxiv_id}v{version} does not match any requested selector"
            )
        forced_rules_by_identity[identity] = [
            f"forced:{selector.raw}" for selector in matching_selectors
        ]
    forced_identities = frozenset(forced_by_identity)
    papers = _collect_papers(recent, list(forced_by_identity.values()))

    candidates: list[tuple[RawPaper, list[str]]] = []
    for paper in papers:
        identity = paper.arxiv_id, paper.version
        rules = match_paper(paper, config.prefilter)
        if rules:
            candidates.append((paper, rules))
        elif identity in forced_identities:
            candidates.append((paper, forced_rules_by_identity[identity]))

    if len(candidates) > config.analysis.max_candidates:
        raise CandidateLimitError(
            f"{len(candidates)} candidates exceeds limit {config.analysis.max_candidates}"
        )

    analysis_cache = load_cache(data_dir)
    records: list[AnalyzedPaperRecord] = []
    pending: list[tuple[RawPaper, list[str], str]] = []
    cache_hits = 0
    for paper, rules in candidates:
        key = cache_key(
            paper.arxiv_id,
            paper.version,
            analysis_client.model,
            config.analysis.prompt_version,
        )
        entry = analysis_cache.get(key)
        if entry is not None and (paper.arxiv_id, paper.version) not in forced_identities:
            records.append(entry.record)
            cache_hits += 1
        else:
            pending.append((paper, rules, key))

    failures = 0
    error_categories: dict[str, int] = {}
    usage = TokenUsage()
    with ThreadPoolExecutor(max_workers=config.analysis.max_concurrency) as executor:
        futures: dict[
            Future[tuple[AnalyzedPaperRecord, TokenUsage]],
            str,
        ] = {
            executor.submit(
                analyze_paper,
                paper=paper,
                matched_rules=rules,
                client=analysis_client,
                prompt=prompt,
                prompt_version=config.analysis.prompt_version,
                analyzed_at=now,
            ): key
            for paper, rules, key in pending
        }
        for future in as_completed(futures):
            key = futures[future]
            try:
                record, item_usage = future.result()
            except Exception as error:
                failures += 1
                category = type(error).__name__
                error_categories[category] = error_categories.get(category, 0) + 1
                continue
            records.append(record)
            analysis_cache[key] = CacheEntry(key=key, record=record)
            usage = _sum_usage(usage, item_usage)

    attempted = len(pending)
    failure_ratio = failures / attempted if attempted else 0.0
    if failure_ratio > config.analysis.max_failure_ratio:
        raise QualityGateError(
            f"analysis failure ratio {failure_ratio:.1%} exceeds "
            f"{config.analysis.max_failure_ratio:.1%}"
        )

    thresholded = sorted(
        (record for record in records if record.analysis.relevance_score >= threshold),
        key=_publication_order,
    )
    figure_result = enrich_figures(
        thresholded,
        figure_fetcher=figure_fetcher,
        cache=load_figure_cache(data_dir),
        now=now,
    )
    published = figure_result.records
    stats = RunStats(
        fetched=len(papers),
        prefiltered=len(candidates),
        cache_hits=cache_hits,
        figure_cache_hits=figure_result.cache_hits,
        figure_requests=figure_result.requests,
        figure_available=figure_result.available,
        figure_unavailable=figure_result.unavailable,
        figure_failed=figure_result.failed,
        model_calls=attempted,
        published=len(published),
        failed=failures,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
        error_categories=dict(sorted(error_categories.items())),
    )
    if not dry_run:
        save_successful_run(
            data_dir,
            published,
            analysis_cache,
            stats,
            now,
            figure_cache=figure_result.cache,
        )
    return RunReport(
        stats=stats,
        published=published,
        dry_run=dry_run,
    )
