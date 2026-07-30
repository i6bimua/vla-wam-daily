import re
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_serializer,
    field_validator,
    model_validator,
)

NonNegativeInt = Annotated[int, Field(ge=0)]
ARXIV_FIGURE_HOSTS = frozenset({"arxiv.org", "www.arxiv.org"})
ARXIV_HTML_PATH_PATTERN = re.compile(
    r"^/html/(?P<arxiv_id>\d{4}\.\d{4,5})v(?P<version>[1-9]\d*)$"
)
ARXIV_SOURCE_PATH_PATTERN = re.compile(
    r"^/e-print/(?P<arxiv_id>\d{4}\.\d{4,5})v(?P<version>[1-9]\d*)$"
)
ARXIV_PDF_PATH_PATTERN = re.compile(
    r"^/pdf/(?P<arxiv_id>\d{4}\.\d{4,5})v(?P<version>[1-9]\d*)$"
)
ARXIV_IMAGE_PATH_PATTERN = re.compile(
    r"^/html/\d{4}\.\d{4,5}v[1-9]\d*/.+$"
)
CACHED_FIGURE_PATH_PATTERN = re.compile(
    r"^/figures/(?P<arxiv_id>\d{4}\.\d{4,5})/"
    r"v(?P<version>[1-9]\d*)/"
    r"fig(?P<figure>[12])-panel(?P<panel>[1-9]\d*)"
    r"\.(?:png|jpg|webp|gif|svg)$"
)
FigureNumber = Literal[1, 2]
FigureSource = Literal["arxiv_html", "arxiv_source", "arxiv_pdf"]
FigureImageTuple = Annotated[tuple[HttpUrl | None, ...], Field(min_length=1)]
FigureCachedImageTuple = tuple[str | None, ...]
FigureCacheKey = Annotated[
    str,
    Field(pattern=r"^\d{4}\.\d{4,5}:v[1-9]\d*$"),
]


def normalize_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


UtcDatetime = Annotated[AwareDatetime, AfterValidator(normalize_utc)]

PERSISTED_BOUNDARY_WHITESPACE_PATTERN = re.compile(
    r"^[\u0009-\u000D\u001C-\u001F\u0020\u0085\u00A0\u1680"
    r"\u2000-\u200A\u2028\u2029\u202F\u205F\u3000\uFEFF]+|"
    r"[\u0009-\u000D\u001C-\u001F\u0020\u0085\u00A0\u1680"
    r"\u2000-\u200A\u2028\u2029\u202F\u205F\u3000\uFEFF]+$"
)


def normalize_nonblank_text(value: str) -> str:
    normalized = PERSISTED_BOUNDARY_WHITESPACE_PATTERN.sub("", value)
    if not normalized:
        raise ValueError("text must contain non-whitespace characters")
    return normalized


NormalizedNonBlankStr = Annotated[
    str,
    Field(min_length=1),
    AfterValidator(normalize_nonblank_text),
]
NonEmptyStr = NormalizedNonBlankStr
NonEmptyStrList = Annotated[list[NonEmptyStr], Field(min_length=1)]
NonEmptyStrTuple = Annotated[tuple[NonEmptyStr, ...], Field(min_length=1)]


def validate_arxiv_url_authority(url: HttpUrl) -> HttpUrl:
    if url.scheme != "https" or url.host not in ARXIV_FIGURE_HOSTS:
        raise ValueError("URL must use https and an allowed arXiv host")
    if url.username is not None or url.password is not None:
        raise ValueError("arXiv URL must not contain credentials")
    if url.port != 443:
        raise ValueError("arXiv URL must use the default https port")
    return url


def parse_arxiv_html_identity(url: HttpUrl) -> tuple[str, int]:
    path = url.path
    match = ARXIV_HTML_PATH_PATTERN.fullmatch(path) if path is not None else None
    if match is None:
        raise ValueError("arXiv HTML URL must identify a versioned paper")
    return match.group("arxiv_id"), int(match.group("version"))


def validate_arxiv_html_url(url: HttpUrl) -> HttpUrl:
    validate_arxiv_url_authority(url)
    if url.query is not None:
        raise ValueError("arXiv HTML URL must not contain a query")
    if url.fragment is not None:
        raise ValueError("arXiv HTML URL must not contain a fragment")
    parse_arxiv_html_identity(url)
    return url


def validate_arxiv_image_url(url: HttpUrl) -> HttpUrl:
    validate_arxiv_url_authority(url)
    if url.fragment is not None:
        raise ValueError("arXiv image URL must not contain a fragment")
    if url.path is None or ARXIV_IMAGE_PATH_PATTERN.fullmatch(url.path) is None:
        raise ValueError("arXiv image URL must belong to a versioned HTML paper")
    return url


def parse_arxiv_source_identity(
    url: HttpUrl,
    source: FigureSource,
) -> tuple[str, int]:
    validate_arxiv_url_authority(url)
    if url.query is not None:
        raise ValueError("arXiv figure source URL must not contain a query")

    if source == "arxiv_html":
        if not url.fragment:
            raise ValueError(
                "arXiv HTML figure source URL must contain a nonempty fragment"
            )
        return parse_arxiv_html_identity(url)

    if url.fragment is not None:
        raise ValueError("arXiv recovered figure source URL must not contain a fragment")
    if source == "arxiv_source":
        pattern = ARXIV_SOURCE_PATH_PATTERN
    elif source == "arxiv_pdf":
        pattern = ARXIV_PDF_PATH_PATTERN
    else:
        raise ValueError("unsupported Figure source")
    path = url.path
    match = pattern.fullmatch(path) if path is not None else None
    if match is None:
        raise ValueError(f"{source} URL must identify a versioned paper")
    return match.group("arxiv_id"), int(match.group("version"))


def validate_arxiv_source_url(
    url: HttpUrl,
    source: FigureSource = "arxiv_html",
) -> HttpUrl:
    parse_arxiv_source_identity(url, source)
    return url


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FrozenStrictModel(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        if update is None:
            return super().model_copy(deep=deep)
        data = self.model_dump(mode="python", round_trip=True)
        data.update(update)
        return type(self).model_validate(data)


class Topic(StrEnum):
    VLA = "VLA"
    WAM = "WAM"
    WORLD_MODEL = "World Model"
    DATASET = "Dataset"
    BENCHMARK = "Benchmark"


ALLOWED_TAGS = frozenset(
    {
        "Action Prediction",
        "Data",
        "Evaluation",
        "Generalist Robotics",
        "Policy Learning",
        "Robot Learning",
        "Robot Manipulation",
        "Simulation",
        "Video Generation",
        "Vision-Language",
        "World Modeling",
    }
)


class RawPaper(StrictModel):
    arxiv_id: str = Field(pattern=r"^\d{4}\.\d{4,5}$")
    version: int = Field(ge=1)
    published_at: UtcDatetime
    updated_at: UtcDatetime
    title: str = Field(min_length=1)
    authors: list[str] = Field(min_length=1)
    arxiv_categories: list[str] = Field(min_length=1)
    abstract: str = Field(min_length=1)
    comment: str | None = None


class Analysis(FrozenStrictModel):
    relevance_score: int = Field(ge=1, le=10)
    primary_topic: Topic
    tags: tuple[str, ...]
    one_sentence_summary: NormalizedNonBlankStr
    main_contribution: NormalizedNonBlankStr
    method: NormalizedNonBlankStr
    key_results: NormalizedNonBlankStr
    limitations: NormalizedNonBlankStr
    relation_to_vla_wam: NormalizedNonBlankStr

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, tags: tuple[str, ...]) -> tuple[str, ...]:
        unknown = sorted(set(tags) - ALLOWED_TAGS)
        if unknown:
            raise ValueError(f"unsupported tags: {', '.join(unknown)}")
        return tuple(dict.fromkeys(tags))


class AIOutput(StrictModel):
    title_zh: NormalizedNonBlankStr
    analysis: Analysis


class Resources(FrozenStrictModel):
    arxiv_url: HttpUrl
    pdf_url: HttpUrl
    project_url: HttpUrl | None = None
    code_url: HttpUrl | None = None


class FigureStatus(StrEnum):
    AVAILABLE = "available"
    HTML_UNAVAILABLE = "html_unavailable"
    NOT_FOUND = "not_found"
    FETCH_FAILED = "fetch_failed"


class FigureRecoveryStatus(StrEnum):
    NOT_ATTEMPTED = "not_attempted"
    AVAILABLE = "available"
    NOT_FOUND = "not_found"
    FETCH_FAILED = "fetch_failed"


class FigureAsset(FrozenStrictModel):
    number: FigureNumber
    label: NonEmptyStr
    caption: NonEmptyStr
    image_urls: FigureImageTuple
    cached_image_paths: FigureCachedImageTuple = Field(default_factory=tuple)
    source_url: HttpUrl
    source: FigureSource = "arxiv_html"

    @model_validator(mode="before")
    @classmethod
    def normalize_historical_cached_paths(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        image_urls = value.get("image_urls")
        cached_paths = value.get("cached_image_paths")
        if (
            isinstance(image_urls, (list, tuple))
            and image_urls
            and (
                "cached_image_paths" not in value
                or cached_paths == []
                or cached_paths == ()
            )
        ):
            normalized = dict(value)
            normalized["cached_image_paths"] = (None,) * len(image_urls)
            return normalized
        return value

    @field_validator("image_urls")
    @classmethod
    def validate_image_urls(
        cls, urls: tuple[HttpUrl | None, ...]
    ) -> tuple[HttpUrl | None, ...]:
        for url in urls:
            if url is None:
                continue
            validate_arxiv_image_url(url)
        return urls

    @model_validator(mode="after")
    def validate_panel_contract(self) -> Self:
        arxiv_id, version = parse_arxiv_source_identity(
            self.source_url,
            self.source,
        )
        if not self.cached_image_paths:
            raise ValueError("cached Figure paths must contain at least one panel")
        if len(self.cached_image_paths) != len(self.image_urls):
            raise ValueError("cached Figure paths must align with image URLs")

        deduplicated_urls: list[HttpUrl | None] = []
        deduplicated_paths: list[str | None] = []
        seen: set[str] = set()
        for image_url, path in zip(
            self.image_urls,
            self.cached_image_paths,
            strict=True,
        ):
            if image_url is not None:
                key = str(image_url)
                if key in seen:
                    continue
                seen.add(key)
            deduplicated_urls.append(image_url)
            deduplicated_paths.append(path)

        match self.source:
            case "arxiv_html":
                if any(image_url is None for image_url in deduplicated_urls):
                    raise ValueError(
                        "arXiv HTML Figure panels require remote image URLs"
                    )
            case "arxiv_source" | "arxiv_pdf":
                if any(image_url is not None for image_url in deduplicated_urls):
                    raise ValueError("recovered Figure panels must be local-only")
            case _:
                raise ValueError("unsupported Figure source")

        for panel, (image_url, path) in enumerate(
            zip(deduplicated_urls, deduplicated_paths, strict=True),
            start=1,
        ):
            if image_url is None and path is None:
                raise ValueError(
                    "each Figure panel requires a remote URL or cached path"
                )
            if path is None:
                continue
            match = CACHED_FIGURE_PATH_PATTERN.fullmatch(path)
            if (
                match is None
                or match.group("arxiv_id") != arxiv_id
                or int(match.group("version")) != version
                or int(match.group("figure")) != self.number
                or int(match.group("panel")) != panel
            ):
                raise ValueError("cached Figure path does not match its panel")

        # Frozen model: normalize both aligned tuples only after every contract check.
        object.__setattr__(self, "image_urls", tuple(deduplicated_urls))
        object.__setattr__(self, "cached_image_paths", tuple(deduplicated_paths))
        return self


class FigureGallery(FrozenStrictModel):
    status: FigureStatus
    html_url: HttpUrl
    figures: Annotated[tuple[FigureAsset, ...], Field(max_length=2)] = Field(
        default_factory=tuple
    )
    checked_at: UtcDatetime
    recovery_status: FigureRecoveryStatus = FigureRecoveryStatus.NOT_ATTEMPTED
    recovery_checked_at: UtcDatetime | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_historical_recovery_status(cls, value: object) -> object:
        if not isinstance(value, Mapping) or "recovery_status" in value:
            return value
        figures = value.get("figures", ())
        has_figure_one = isinstance(figures, (list, tuple)) and any(
            (
                figure.number
                if isinstance(figure, FigureAsset)
                else figure.get("number")
                if isinstance(figure, Mapping)
                else None
            )
            == 1
            for figure in figures
        )
        normalized = dict(value)
        normalized["recovery_status"] = (
            FigureRecoveryStatus.AVAILABLE
            if has_figure_one
            else FigureRecoveryStatus.NOT_ATTEMPTED
        )
        return normalized

    @field_validator("html_url")
    @classmethod
    def validate_html_url(cls, url: HttpUrl) -> HttpUrl:
        return validate_arxiv_html_url(url)

    @field_validator("figures")
    @classmethod
    def validate_and_sort_figures(
        cls, figures: tuple[FigureAsset, ...]
    ) -> tuple[FigureAsset, ...]:
        numbers = [figure.number for figure in figures]
        if len(numbers) != len(set(numbers)):
            raise ValueError("duplicate figure numbers")
        return tuple(sorted(figures, key=lambda figure: figure.number))

    @model_validator(mode="after")
    def validate_gallery_contract(self) -> Self:
        if self.status is FigureStatus.AVAILABLE and not self.figures:
            raise ValueError("available figure gallery requires at least one figure")
        if self.status is not FigureStatus.AVAILABLE and self.figures:
            raise ValueError("unavailable figure gallery must not contain figures")
        has_figure_one = any(figure.number == 1 for figure in self.figures)
        if (
            self.recovery_status is FigureRecoveryStatus.AVAILABLE
            and not has_figure_one
        ):
            raise ValueError("available Figure recovery requires Figure 1")
        if (
            self.recovery_status
            in {
                FigureRecoveryStatus.NOT_FOUND,
                FigureRecoveryStatus.FETCH_FAILED,
            }
            and self.recovery_checked_at is None
        ):
            raise ValueError("terminal Figure recovery requires a checked timestamp")
        html_path = self.html_url.path
        if html_path is None:
            raise ValueError("arXiv HTML URL must contain a path")
        gallery_identity = parse_arxiv_html_identity(self.html_url)
        for figure in self.figures:
            source_identity = parse_arxiv_source_identity(
                figure.source_url,
                figure.source,
            )
            if source_identity != gallery_identity:
                raise ValueError("figure source URL must match gallery paper and version")
            image_prefix = f"{html_path}/"
            if any(
                image_url is not None
                and (
                    image_url.path is None
                    or not image_url.path.startswith(image_prefix)
                )
                for image_url in figure.image_urls
            ):
                raise ValueError("figure image URL must match gallery paper and version")
        return self


class Provenance(FrozenStrictModel):
    analysis_scope: Literal["title_and_abstract"]
    model: NormalizedNonBlankStr
    prompt_version: NormalizedNonBlankStr
    analyzed_at: UtcDatetime


class AnalyzedPaperRecord(FrozenStrictModel):
    arxiv_id: str = Field(pattern=r"^\d{4}\.\d{4,5}$")
    version: int = Field(ge=1)
    published_at: UtcDatetime
    updated_at: UtcDatetime
    title: NonEmptyStr
    title_zh: NormalizedNonBlankStr
    authors: NonEmptyStrTuple
    arxiv_categories: NonEmptyStrTuple
    abstract: NonEmptyStr
    matched_rules: NonEmptyStrTuple
    analysis: Analysis
    resources: Resources
    provenance: Provenance


class PaperRecord(AnalyzedPaperRecord):
    figure_gallery: FigureGallery

    @model_validator(mode="before")
    @classmethod
    def reject_null_figure_gallery(cls, value: object) -> object:
        if isinstance(value, Mapping) and value.get("figure_gallery") is None:
            arxiv_id = value.get("arxiv_id", "unknown")
            raise ValueError(f"paper {arxiv_id} requires a checked figure gallery")
        return value

    @model_validator(mode="after")
    def validate_figure_gallery_identity(self) -> Self:
        gallery_identity = parse_arxiv_html_identity(self.figure_gallery.html_url)
        if gallery_identity != (self.arxiv_id, self.version):
            raise ValueError("paper record and figure gallery identities must match")
        return self


class TokenUsage(StrictModel):
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


class RunStats(FrozenStrictModel):
    fetched: int = Field(default=0, ge=0)
    prefiltered: int = Field(default=0, ge=0)
    cache_hits: int = Field(default=0, ge=0)
    figure_cache_hits: int = Field(default=0, ge=0)
    figure_requests: int = Field(default=0, ge=0)
    figure_available: int = Field(default=0, ge=0)
    figure_unavailable: int = Field(default=0, ge=0)
    figure_failed: int = Field(default=0, ge=0)
    model_calls: int = Field(default=0, ge=0)
    published: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    error_categories: Mapping[str, NonNegativeInt] = Field(
        default_factory=dict,
        validate_default=True,
    )

    @model_validator(mode="after")
    def validate_token_total(self) -> Self:
        if self.total_tokens != self.prompt_tokens + self.completion_tokens:
            raise ValueError(
                "total_tokens must equal prompt_tokens plus completion_tokens"
            )
        return self

    @field_validator("error_categories")
    @classmethod
    def freeze_error_categories(
        cls, value: Mapping[str, int]
    ) -> Mapping[str, int]:
        return MappingProxyType(dict(value))

    @field_serializer("error_categories")
    def serialize_error_categories(self, value: Mapping[str, int]) -> dict[str, int]:
        return dict(value)


class DataFile(FrozenStrictModel):
    schema_version: Literal["1"] = "1"
    generated_at: UtcDatetime
    stats: RunStats
    papers: tuple[PaperRecord, ...]


class CacheEntry(FrozenStrictModel):
    key: NonEmptyStr
    record: AnalyzedPaperRecord


class FigureCacheEntry(FrozenStrictModel):
    key: FigureCacheKey
    gallery: FigureGallery

    @model_validator(mode="after")
    def validate_gallery_identity(self) -> Self:
        arxiv_id, version_text = self.key.rsplit(":v", maxsplit=1)
        gallery_identity = parse_arxiv_html_identity(self.gallery.html_url)
        if gallery_identity != (arxiv_id, int(version_text)):
            raise ValueError("figure cache key and gallery identities must match")
        return self
