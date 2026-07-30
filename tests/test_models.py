import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

import vla_wam_daily.models as models
from tests.factories import make_gallery, make_record
from vla_wam_daily.models import (
    AIOutput,
    Analysis,
    CacheEntry,
    DataFile,
    FigureAsset,
    FigureCacheEntry,
    FigureGallery,
    FigureStatus,
    PaperRecord,
    Provenance,
    RawPaper,
    Resources,
    RunStats,
    Topic,
)


def valid_ai_output_payload() -> dict[str, object]:
    return {
        "title_zh": "中文标题",
        "analysis": {
            "relevance_score": 8,
            "primary_topic": "VLA",
            "tags": ["Vision-Language"],
            "one_sentence_summary": "一句话总结",
            "main_contribution": "核心贡献",
            "method": "方法",
            "key_results": "实验结果",
            "limitations": "局限性",
            "relation_to_vla_wam": "与 VLA 直接相关",
        },
    }


def load_persisted_record(
    boundary: str,
    record_payload: dict[str, object],
) -> models.AnalyzedPaperRecord:
    payload = deepcopy(record_payload)
    if boundary in {"analyzed-record", "cache-entry"}:
        payload.pop("figure_gallery")
    if boundary == "analyzed-record":
        return models.AnalyzedPaperRecord.model_validate_json(json.dumps(payload))
    if boundary == "paper-record":
        return PaperRecord.model_validate_json(json.dumps(payload))
    if boundary == "cache-entry":
        entry = CacheEntry.model_validate_json(
            json.dumps({"key": "analysis-key", "record": payload})
        )
        return entry.record
    if boundary == "data-file":
        data_file = DataFile.model_validate_json(
            json.dumps(
                {
                    "generated_at": "2026-07-27T00:00:00Z",
                    "stats": {},
                    "papers": [payload],
                }
            )
        )
        return data_file.papers[0]
    raise AssertionError(f"unsupported test boundary: {boundary}")


PERSISTED_RECORD_BOUNDARIES = [
    "analyzed-record",
    "paper-record",
    "cache-entry",
    "data-file",
]


@pytest.mark.parametrize("boundary", PERSISTED_RECORD_BOUNDARIES)
@pytest.mark.parametrize(
    "field",
    [
        "title_zh",
        "provenance.model",
        "provenance.prompt_version",
    ],
)
def test_persisted_record_boundaries_reject_blank_normalized_fields(
    boundary: str,
    field: str,
) -> None:
    payload = make_record().model_dump(mode="json")
    if field == "title_zh":
        payload["title_zh"] = " \n\t "
    else:
        provenance = payload["provenance"]
        assert isinstance(provenance, dict)
        provenance[field.rsplit(".", maxsplit=1)[1]] = " \n\t "

    with pytest.raises(ValidationError):
        load_persisted_record(boundary, payload)


@pytest.mark.parametrize("boundary", PERSISTED_RECORD_BOUNDARIES)
def test_persisted_record_boundaries_normalize_text_and_provenance(
    boundary: str,
) -> None:
    payload = make_record().model_dump(mode="json")
    payload["title_zh"] = " \n 中文标题 \t"
    provenance = payload["provenance"]
    assert isinstance(provenance, dict)
    provenance["model"] = " deepseek-v4-pro "
    provenance["prompt_version"] = "\n prompt-v1 \t"

    record = load_persisted_record(boundary, payload)

    assert record.title_zh == "中文标题"
    assert record.provenance.model == "deepseek-v4-pro"
    assert record.provenance.prompt_version == "prompt-v1"


@pytest.mark.parametrize("boundary", PERSISTED_RECORD_BOUNDARIES)
@pytest.mark.parametrize(
    "field",
    ["title", "authors", "arxiv_categories", "abstract", "matched_rules"],
)
def test_persisted_record_boundaries_reject_blank_public_text(
    boundary: str,
    field: str,
) -> None:
    payload = make_record().model_dump(mode="json")
    if field in {"authors", "arxiv_categories", "matched_rules"}:
        payload[field] = ["valid", " \n\t "]
    else:
        payload[field] = " \n\t "

    with pytest.raises(ValidationError):
        load_persisted_record(boundary, payload)


@pytest.mark.parametrize("boundary", PERSISTED_RECORD_BOUNDARIES)
def test_persisted_record_boundaries_normalize_public_text(
    boundary: str,
) -> None:
    payload = make_record().model_dump(mode="json")
    payload.update(
        title=" Original title ",
        authors=[" Ada Robot ", "\n Wei Model\t"],
        arxiv_categories=[" cs.RO ", "\ncs.CV\t"],
        abstract=" Original abstract ",
        matched_rules=[" vision language action "],
    )

    record = load_persisted_record(boundary, payload)

    assert record.title == "Original title"
    assert record.authors == ("Ada Robot", "Wei Model")
    assert record.arxiv_categories == ("cs.RO", "cs.CV")
    assert record.abstract == "Original abstract"
    assert record.matched_rules == ("vision language action",)


@pytest.mark.parametrize("boundary", ["paper-record", "data-file"])
@pytest.mark.parametrize("field", ["label", "caption"])
def test_public_figure_text_is_nonblank_and_normalized(
    boundary: str,
    field: str,
) -> None:
    payload = make_record().model_dump(mode="json")
    gallery = payload["figure_gallery"]
    assert isinstance(gallery, dict)
    figures = gallery["figures"]
    assert isinstance(figures, list)
    first_figure = figures[0]
    assert isinstance(first_figure, dict)
    first_figure[field] = f"  {field} value \n"

    record = load_persisted_record(boundary, payload)
    assert getattr(record.figure_gallery.figures[0], field) == f"{field} value"

    first_figure[field] = " \n\t "
    with pytest.raises(ValidationError):
        load_persisted_record(boundary, payload)


def test_cache_entry_key_is_nonblank_and_normalized() -> None:
    record_payload = make_record().model_dump(mode="json")
    record_payload.pop("figure_gallery")

    entry = CacheEntry.model_validate_json(
        json.dumps({"key": " analysis-key ", "record": record_payload})
    )

    assert entry.key == "analysis-key"
    with pytest.raises(ValidationError):
        CacheEntry.model_validate_json(
            json.dumps({"key": " \n\t ", "record": record_payload})
        )


def test_public_boundaries_share_explicit_unicode_whitespace_normalization() -> None:
    payload = make_record().model_dump(mode="json")
    payload["title"] = "\ufeff\u0085  Outer\u0085Inner \u3000\ufeff"
    payload["authors"] = ["\u0085 Ada Robot \ufeff"]
    gallery = payload["figure_gallery"]
    assert isinstance(gallery, dict)
    figures = gallery["figures"]
    assert isinstance(figures, list)
    first_figure = figures[0]
    assert isinstance(first_figure, dict)
    first_figure["label"] = "\ufeff\u0085 Figure\u00851 \u3000"

    data_record = load_persisted_record("data-file", payload)
    cache_record = load_persisted_record("cache-entry", payload)
    cache_payload = cache_record.model_dump(mode="json")
    cache_entry = CacheEntry.model_validate(
        {"key": "\ufeff\u0085 analysis-key \u3000", "record": cache_payload}
    )

    assert data_record.title == "Outer\u0085Inner"
    assert data_record.authors == ("Ada Robot",)
    assert data_record.figure_gallery.figures[0].label == "Figure\u00851"
    assert cache_record.title == "Outer\u0085Inner"
    assert cache_entry.key == "analysis-key"


@pytest.mark.parametrize("whitespace", ["\ufeff", "\u0085", "\ufeff\u0085"])
@pytest.mark.parametrize(
    "boundary",
    ["data-file", "cache-entry", "figure-label", "cache-key"],
)
def test_public_boundaries_reject_explicit_unicode_whitespace_only(
    whitespace: str,
    boundary: str,
) -> None:
    payload = make_record().model_dump(mode="json")
    if boundary in {"data-file", "cache-entry"}:
        payload["title"] = whitespace
        with pytest.raises(ValidationError):
            load_persisted_record(boundary, payload)
        return
    if boundary == "figure-label":
        gallery = payload["figure_gallery"]
        assert isinstance(gallery, dict)
        figures = gallery["figures"]
        assert isinstance(figures, list)
        first_figure = figures[0]
        assert isinstance(first_figure, dict)
        first_figure["label"] = whitespace
        with pytest.raises(ValidationError):
            load_persisted_record("data-file", payload)
        return

    payload.pop("figure_gallery")
    with pytest.raises(ValidationError):
        CacheEntry.model_validate({"key": whitespace, "record": payload})


@pytest.mark.parametrize(
    "field",
    [
        "title_zh",
        "one_sentence_summary",
        "main_contribution",
        "method",
        "key_results",
        "limitations",
        "relation_to_vla_wam",
    ],
)
def test_ai_output_rejects_whitespace_only_text_fields(field: str) -> None:
    payload = valid_ai_output_payload()
    if field == "title_zh":
        payload[field] = " \n\t "
    else:
        analysis = payload["analysis"]
        assert isinstance(analysis, dict)
        analysis[field] = " \n\t "

    with pytest.raises(ValidationError):
        AIOutput.model_validate(payload)


def test_analysis_rejects_out_of_range_score() -> None:
    with pytest.raises(ValidationError):
        Analysis(
            relevance_score=11,
            primary_topic=Topic.VLA,
            tags=["Vision-Language"],
            one_sentence_summary="总结",
            main_contribution="贡献",
            method="方法",
            key_results="摘要未说明",
            limitations="摘要未说明",
            relation_to_vla_wam="直接相关",
        )


def test_data_file_serializes_public_contract() -> None:
    record = make_record()
    data = DataFile(
        schema_version="1",
        generated_at=datetime(2026, 7, 27, tzinfo=UTC),
        stats={"fetched": 4, "prefiltered": 1, "published": 1},
        papers=[record],
    )
    payload = data.model_dump(mode="json")
    assert payload["papers"][0]["analysis"]["primary_topic"] == "VLA"
    assert payload["papers"][0]["provenance"]["analysis_scope"] == "title_and_abstract"


def test_available_figure_gallery_serializes_public_contract() -> None:
    gallery = FigureGallery(
        status=FigureStatus.AVAILABLE,
        html_url="https://arxiv.org/html/2607.12345v1",
        figures=[
            FigureAsset(
                number=1,
                label="Figure 1",
                caption="The model architecture.",
                image_urls=[
                    "https://arxiv.org/html/2607.12345v1/x1.png",
                    "https://arxiv.org/html/2607.12345v1/x2.png",
                ],
                source_url="https://arxiv.org/html/2607.12345v1#S1.F1",
            )
        ],
        checked_at=datetime(2026, 7, 27, tzinfo=UTC),
    )

    assert gallery.model_dump(mode="json") == {
        "status": "available",
        "html_url": "https://arxiv.org/html/2607.12345v1",
        "figures": [
            {
                "number": 1,
                "label": "Figure 1",
                "caption": "The model architecture.",
                "image_urls": [
                    "https://arxiv.org/html/2607.12345v1/x1.png",
                    "https://arxiv.org/html/2607.12345v1/x2.png",
                ],
                "cached_image_paths": [],
                "source_url": "https://arxiv.org/html/2607.12345v1#S1.F1",
                "source": "arxiv_html",
            }
        ],
        "checked_at": "2026-07-27T00:00:00Z",
    }


def test_figure_asset_serializes_aligned_cached_image_paths() -> None:
    asset = FigureAsset(
        number=1,
        label="Figure 1",
        caption="The model architecture.",
        image_urls=[
            "https://arxiv.org/html/2607.12345v1/x1.png",
            "https://arxiv.org/html/2607.12345v1/x2.png",
        ],
        cached_image_paths=[
            "/figures/2607.12345/v1/fig1-panel1.png",
            None,
        ],
        source_url="https://arxiv.org/html/2607.12345v1#S1.F1",
    )

    assert asset.model_dump(mode="json")["cached_image_paths"] == [
        "/figures/2607.12345/v1/fig1-panel1.png",
        None,
    ]


def test_figure_asset_accepts_historical_payload_without_cached_paths() -> None:
    payload = make_gallery().figures[0].model_dump(mode="json")
    payload.pop("cached_image_paths", None)

    asset = FigureAsset.model_validate(payload)

    assert asset.cached_image_paths == ()


@pytest.mark.parametrize(
    "cached_path",
    [
        "/figures/2607.99999/v1/fig1-panel1.png",
        "/figures/2607.12345/v2/fig1-panel1.png",
        "/figures/2607.12345/v1/fig2-panel1.png",
        "/figures/2607.12345/v1/fig1-panel2.png",
        "/figures/../../secret.png",
        "https://example.com/image.png",
    ],
)
def test_figure_asset_rejects_cached_path_for_another_panel(
    cached_path: str,
) -> None:
    with pytest.raises(ValidationError):
        FigureAsset(
            number=1,
            label="Figure 1",
            caption="The model architecture.",
            image_urls=["https://arxiv.org/html/2607.12345v1/x1.png"],
            cached_image_paths=[cached_path],
            source_url="https://arxiv.org/html/2607.12345v1#S1.F1",
        )


def test_figure_asset_rejects_misaligned_cached_path_count() -> None:
    with pytest.raises(ValidationError):
        FigureAsset(
            number=1,
            label="Figure 1",
            caption="The model architecture.",
            image_urls=[
                "https://arxiv.org/html/2607.12345v1/x1.png",
                "https://arxiv.org/html/2607.12345v1/x2.png",
            ],
            cached_image_paths=[
                "/figures/2607.12345/v1/fig1-panel1.png",
            ],
            source_url="https://arxiv.org/html/2607.12345v1#S1.F1",
        )


def test_figure_factory_uses_anchored_source_urls() -> None:
    figures = make_gallery().figures

    assert [str(figure.source_url) for figure in figures] == [
        "https://arxiv.org/html/2607.12345v1#S1.F1",
        "https://arxiv.org/html/2607.12345v1#S2.F2",
    ]


def test_published_models_are_frozen_and_collections_are_tuples() -> None:
    gallery = make_gallery()
    record = make_record()
    data_file = DataFile(
        generated_at=datetime(2026, 7, 27, tzinfo=UTC),
        stats=RunStats(),
        papers=[record],
    )

    assert isinstance(gallery.figures, tuple)
    assert isinstance(gallery.figures[0].image_urls, tuple)
    assert isinstance(record.authors, tuple)
    assert isinstance(record.arxiv_categories, tuple)
    assert isinstance(record.matched_rules, tuple)
    assert isinstance(record.analysis.tags, tuple)
    assert isinstance(data_file.papers, tuple)
    with pytest.raises(ValidationError):
        gallery.status = FigureStatus.NOT_FOUND
    with pytest.raises(ValidationError):
        record.analysis.relevance_score = 11
    with pytest.raises(ValidationError):
        data_file.stats.fetched = -1
    with pytest.raises(AttributeError):
        gallery.figures.clear()  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        gallery.figures[0].image_urls.append("https://arxiv.org/html/2607.12345v1/x3.png")  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        record.authors.clear()  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        record.analysis.tags.append("unsupported")  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        data_file.papers.append(make_record())  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        data_file.stats.error_categories.update(network=-1)  # type: ignore[attr-defined]


def test_frozen_model_copy_revalidates_updates() -> None:
    with pytest.raises(ValidationError):
        make_gallery().model_copy(update={"status": FigureStatus.NOT_FOUND})


def test_strict_models_remain_mutable_for_configuration_workflows() -> None:
    raw_paper = RawPaper(
        arxiv_id="2607.12345",
        version=1,
        published_at=datetime(2026, 7, 27, tzinfo=UTC),
        updated_at=datetime(2026, 7, 27, tzinfo=UTC),
        title="Original title",
        authors=["Author"],
        arxiv_categories=["cs.RO"],
        abstract="Abstract",
    )

    raw_paper.title = "Updated title"

    assert raw_paper.title == "Updated title"


def test_public_record_requires_gallery_while_analyzed_record_does_not() -> None:
    record_data = make_record().model_dump()
    record_data.pop("figure_gallery")

    analyzed_record = models.AnalyzedPaperRecord(**record_data)

    assert analyzed_record.arxiv_id == "2607.12345"
    with pytest.raises(ValidationError):
        PaperRecord(**record_data)
    assert CacheEntry(key="analysis-key", record=analyzed_record).record == analyzed_record


@pytest.mark.parametrize(
    ("gallery_arxiv_id", "gallery_version"),
    [
        ("2607.99999", 1),
        ("2607.12345", 2),
    ],
)
def test_paper_record_rejects_mismatched_gallery_identity(
    gallery_arxiv_id: str, gallery_version: int
) -> None:
    record_data = make_record().model_dump()
    record_data["figure_gallery"] = make_gallery(
        arxiv_id=gallery_arxiv_id,
        version=gallery_version,
    )

    with pytest.raises(ValidationError):
        PaperRecord(**record_data)


def test_paper_record_copy_revalidates_gallery_identity() -> None:
    with pytest.raises(ValidationError):
        make_record().model_copy(
            update={"figure_gallery": make_gallery(version=2)}
        )


def test_public_data_file_schema_requires_non_nullable_figure_gallery() -> None:
    schema = DataFile.model_json_schema()
    paper_schema = schema["$defs"]["PaperRecord"]

    assert "figure_gallery" in paper_schema["required"]
    assert "anyOf" not in paper_schema["properties"]["figure_gallery"]


@pytest.mark.parametrize(
    "html_url",
    [
        "https://reader@arxiv.org/html/2607.12345v1",
        "https://reader:secret@arxiv.org/html/2607.12345v1",
        "https://arxiv.org:444/html/2607.12345v1",
    ],
)
def test_figure_gallery_rejects_insecure_arxiv_url_authority(html_url: str) -> None:
    with pytest.raises(ValidationError):
        FigureGallery(
            status=FigureStatus.NOT_FOUND,
            html_url=html_url,
            checked_at=datetime(2026, 7, 27, tzinfo=UTC),
        )


@pytest.mark.parametrize(
    ("field", "url"),
    [
        ("source_url", "https://reader@arxiv.org/html/2607.12345v1#S1.F1"),
        ("source_url", "https://arxiv.org:444/html/2607.12345v1#S1.F1"),
        ("image_url", "https://reader:secret@arxiv.org/html/2607.12345v1/x1.png"),
        ("image_url", "https://arxiv.org:444/html/2607.12345v1/x1.png"),
    ],
)
def test_figure_asset_rejects_insecure_arxiv_url_authority(
    field: str, url: str
) -> None:
    source_url = "https://arxiv.org/html/2607.12345v1#S1.F1"
    image_url = "https://arxiv.org/html/2607.12345v1/x1.png"
    if field == "source_url":
        source_url = url
    else:
        image_url = url

    with pytest.raises(ValidationError):
        FigureAsset(
            number=1,
            label="Figure 1",
            caption="The model architecture.",
            image_urls=[image_url],
            source_url=source_url,
        )


def test_figure_urls_allow_explicit_https_default_port() -> None:
    gallery = FigureGallery(
        status=FigureStatus.AVAILABLE,
        html_url="https://arxiv.org:443/html/2607.12345v1",
        figures=[
            FigureAsset(
                number=1,
                label="Figure 1",
                caption="The model architecture.",
                image_urls=["https://arxiv.org:443/html/2607.12345v1/x1.png"],
                source_url="https://arxiv.org:443/html/2607.12345v1#S1.F1",
            )
        ],
        checked_at=datetime(2026, 7, 27, tzinfo=UTC),
    )

    assert str(gallery.html_url) == "https://arxiv.org/html/2607.12345v1"


def test_figure_urls_require_their_expected_fragment_shapes() -> None:
    with pytest.raises(ValidationError):
        FigureGallery(
            status=FigureStatus.NOT_FOUND,
            html_url="https://arxiv.org/html/2607.12345v1#section",
            checked_at=datetime(2026, 7, 27, tzinfo=UTC),
        )
    with pytest.raises(ValidationError):
        FigureAsset(
            number=1,
            label="Figure 1",
            caption="The model architecture.",
            image_urls=["https://arxiv.org/html/2607.12345v1/x1.png"],
            source_url="https://arxiv.org/html/2607.12345v1",
        )
    with pytest.raises(ValidationError):
        FigureAsset(
            number=1,
            label="Figure 1",
            caption="The model architecture.",
            image_urls=["https://arxiv.org/html/2607.12345v1/x1.png#fragment"],
            source_url="https://arxiv.org/html/2607.12345v1#S1.F1",
        )


@pytest.mark.parametrize(
    ("source_url", "image_url"),
    [
        (
            "https://arxiv.org/html/2607.99999v1#S1.F1",
            "https://arxiv.org/html/2607.12345v1/x1.png",
        ),
        (
            "https://arxiv.org/html/2607.12345v2#S1.F1",
            "https://arxiv.org/html/2607.12345v1/x1.png",
        ),
        (
            "https://arxiv.org/html/2607.12345v1#S1.F1",
            "https://arxiv.org/html/2607.99999v1/x1.png",
        ),
        (
            "https://arxiv.org/html/2607.12345v1#S1.F1",
            "https://arxiv.org/html/2607.12345v2/x1.png",
        ),
    ],
)
def test_figure_gallery_rejects_cross_paper_or_version_urls(
    source_url: str, image_url: str
) -> None:
    figure = FigureAsset(
        number=1,
        label="Figure 1",
        caption="The model architecture.",
        image_urls=[image_url],
        source_url=source_url,
    )

    with pytest.raises(ValidationError):
        FigureGallery(
            status=FigureStatus.AVAILABLE,
            html_url="https://arxiv.org/html/2607.12345v1",
            figures=[figure],
            checked_at=datetime(2026, 7, 27, tzinfo=UTC),
        )


@pytest.mark.parametrize(
    "invalid_key",
    [
        "2606.30552:2",
        "2606.30552:v0",
        "2606.30552:v-1",
        "2606.305:v2",
        "not-an-arxiv-key",
    ],
)
def test_figure_cache_entry_requires_arxiv_version_key(invalid_key: str) -> None:
    entry = FigureCacheEntry(key="2607.12345:v1", gallery=make_gallery())

    assert entry.key == "2607.12345:v1"
    with pytest.raises(ValidationError):
        FigureCacheEntry(key=invalid_key, gallery=make_gallery())


@pytest.mark.parametrize(
    "key",
    [
        "2607.99999:v1",
        "2607.12345:v2",
    ],
)
def test_figure_cache_entry_rejects_mismatched_gallery_identity(key: str) -> None:
    with pytest.raises(ValidationError):
        FigureCacheEntry(key=key, gallery=make_gallery())


@pytest.mark.parametrize(
    "url",
    [
        "http://arxiv.org/html/2607.12345v1/x1.png",
        "https://example.com/x1.png",
        "data:image/png;base64,AAAA",
    ],
)
def test_figure_asset_rejects_non_arxiv_https_image_urls(url: str) -> None:
    with pytest.raises(ValidationError):
        FigureAsset(
            number=1,
            label="Figure 1",
            caption="The model architecture.",
            image_urls=[url],
            source_url="https://arxiv.org/html/2607.12345v1#S1.F1",
        )


def test_available_figure_gallery_rejects_empty_figures() -> None:
    with pytest.raises(ValidationError):
        FigureGallery(
            status=FigureStatus.AVAILABLE,
            html_url="https://arxiv.org/html/2607.12345v1",
            figures=[],
            checked_at=datetime(2026, 7, 27, tzinfo=UTC),
        )


@pytest.mark.parametrize(
    "status",
    [
        FigureStatus.HTML_UNAVAILABLE,
        FigureStatus.NOT_FOUND,
        FigureStatus.FETCH_FAILED,
    ],
)
def test_unavailable_figure_gallery_rejects_figures(status: FigureStatus) -> None:
    with pytest.raises(ValidationError):
        FigureGallery(
            status=status,
            html_url="https://arxiv.org/html/2607.12345v1",
            figures=[make_gallery().figures[0]],
            checked_at=datetime(2026, 7, 27, tzinfo=UTC),
        )


def test_paper_record_rejects_unchecked_figure_gallery() -> None:
    record_data = make_record().model_dump()
    record_data["figure_gallery"] = None
    with pytest.raises(ValidationError, match="2607.12345"):
        PaperRecord(**record_data)


def test_figure_gallery_deduplicates_urls_sorts_figures_and_rejects_duplicates() -> None:
    figure_one = FigureAsset(
        number=1,
        label="Figure 1",
        caption="The model architecture.",
        image_urls=[
            "https://arxiv.org/html/2607.12345v1/x1.png",
            "https://arxiv.org/html/2607.12345v1/x1.png",
            "https://arxiv.org/html/2607.12345v1/x2.png",
        ],
        source_url="https://arxiv.org/html/2607.12345v1#S1.F1",
    )
    figure_two = FigureAsset(
        number=2,
        label="Figure 2",
        caption="Robot evaluation environments.",
        image_urls=["https://arxiv.org/html/2607.12345v1/x3.png"],
        source_url="https://arxiv.org/html/2607.12345v1#S2.F2",
    )

    gallery = FigureGallery(
        status=FigureStatus.AVAILABLE,
        html_url="https://arxiv.org/html/2607.12345v1",
        figures=[figure_two, figure_one],
        checked_at=datetime(2026, 7, 27, tzinfo=UTC),
    )

    assert [figure.number for figure in gallery.figures] == [1, 2]
    assert [str(url) for url in gallery.figures[0].image_urls] == [
        "https://arxiv.org/html/2607.12345v1/x1.png",
        "https://arxiv.org/html/2607.12345v1/x2.png",
    ]
    with pytest.raises(ValidationError):
        FigureGallery(
            status=FigureStatus.AVAILABLE,
            html_url="https://arxiv.org/html/2607.12345v1",
            figures=[figure_one, figure_one],
            checked_at=datetime(2026, 7, 27, tzinfo=UTC),
        )


def test_strict_models_reject_extra_fields() -> None:
    with pytest.raises(ValidationError):
        Analysis(
            relevance_score=8,
            primary_topic=Topic.VLA,
            tags=["Vision-Language"],
            one_sentence_summary="总结",
            main_contribution="贡献",
            method="方法",
            key_results="摘要未说明",
            limitations="摘要未说明",
            relation_to_vla_wam="直接相关",
            unexpected="not allowed",
        )


def test_analysis_deduplicates_tags_in_input_order() -> None:
    analysis = Analysis(
        relevance_score=8,
        primary_topic=Topic.VLA,
        tags=["Vision-Language", "Robot Manipulation", "Vision-Language"],
        one_sentence_summary="总结",
        main_contribution="贡献",
        method="方法",
        key_results="摘要未说明",
        limitations="摘要未说明",
        relation_to_vla_wam="直接相关",
    )

    assert analysis.tags == ("Vision-Language", "Robot Manipulation")


def test_analysis_rejects_unsupported_tags() -> None:
    with pytest.raises(ValidationError):
        Analysis(
            relevance_score=8,
            primary_topic=Topic.VLA,
            tags=["unsupported"],
            one_sentence_summary="总结",
            main_contribution="贡献",
            method="方法",
            key_results="摘要未说明",
            limitations="摘要未说明",
            relation_to_vla_wam="直接相关",
        )


def test_resources_reject_invalid_urls() -> None:
    with pytest.raises(ValidationError):
        Resources(arxiv_url="not a URL", pdf_url="https://arxiv.org/pdf/2607.12345")


@pytest.mark.parametrize(
    ("arxiv_id", "version"),
    [("invalid", 1), ("2607.12345", 0)],
)
def test_paper_record_rejects_invalid_identity(arxiv_id: str, version: int) -> None:
    with pytest.raises(ValidationError):
        make_record(arxiv_id=arxiv_id, version=version)


@pytest.mark.parametrize(
    "field",
    ["title", "title_zh", "authors", "arxiv_categories", "abstract", "matched_rules"],
)
def test_paper_record_rejects_empty_persisted_public_fields(field: str) -> None:
    record = make_record().model_dump()
    record[field] = [] if field in {"authors", "arxiv_categories", "matched_rules"} else ""

    with pytest.raises(ValidationError):
        PaperRecord(**record)


def test_paper_record_rejects_empty_list_members() -> None:
    record = make_record().model_dump()
    record["authors"] = [""]

    with pytest.raises(ValidationError):
        PaperRecord(**record)


def test_cache_entry_rejects_empty_key() -> None:
    with pytest.raises(ValidationError):
        CacheEntry(key="", record=make_record())


def test_persisted_models_reject_naive_datetimes() -> None:
    naive = datetime(2026, 7, 27, 1, 0)
    record = make_record().model_dump()
    record["published_at"] = naive
    record["updated_at"] = naive

    with pytest.raises(ValidationError):
        RawPaper(
            arxiv_id="2607.12345",
            version=1,
            published_at=naive,
            updated_at=naive,
            title="Paper",
            authors=["Author"],
            arxiv_categories=["cs.RO"],
            abstract="Abstract",
        )
    with pytest.raises(ValidationError):
        Provenance(
            analysis_scope="title_and_abstract",
            model="model",
            prompt_version="1",
            analyzed_at=naive,
        )
    with pytest.raises(ValidationError):
        PaperRecord(**record)
    with pytest.raises(ValidationError):
        DataFile(generated_at=naive, stats=RunStats(), papers=[])


def test_persisted_models_normalize_aware_datetimes_to_utc() -> None:
    offset_time = datetime(2026, 7, 27, 8, 0, tzinfo=timezone(timedelta(hours=8)))
    expected = datetime(2026, 7, 27, tzinfo=UTC)
    record_data = make_record().model_dump()
    record_data["published_at"] = offset_time
    record_data["updated_at"] = offset_time
    record_data["provenance"]["analyzed_at"] = offset_time

    raw_paper = RawPaper(
        arxiv_id="2607.12345",
        version=1,
        published_at=offset_time,
        updated_at=offset_time,
        title="Paper",
        authors=["Author"],
        arxiv_categories=["cs.RO"],
        abstract="Abstract",
    )
    provenance = Provenance(
        analysis_scope="title_and_abstract",
        model="model",
        prompt_version="1",
        analyzed_at=offset_time,
    )
    record = PaperRecord(**record_data)
    data_file = DataFile(
        generated_at=offset_time,
        stats=RunStats(),
        papers=[record],
    )

    assert raw_paper.published_at == expected
    assert raw_paper.updated_at == expected
    assert provenance.analyzed_at == expected
    assert record.published_at == expected
    assert record.updated_at == expected
    assert record.provenance.analyzed_at == expected
    assert data_file.generated_at == expected
    payload = data_file.model_dump(mode="json")
    assert payload["generated_at"] == "2026-07-27T00:00:00Z"
    assert payload["papers"][0]["published_at"] == "2026-07-27T00:00:00Z"
    assert payload["papers"][0]["updated_at"] == "2026-07-27T00:00:00Z"
    assert payload["papers"][0]["provenance"]["analyzed_at"] == "2026-07-27T00:00:00Z"


def test_data_file_rejects_unknown_schema_version() -> None:
    with pytest.raises(ValidationError):
        DataFile(
            schema_version="2",
            generated_at=datetime(2026, 7, 27, tzinfo=UTC),
            stats=RunStats(),
            papers=[],
        )


def test_run_stats_rejects_negative_error_category_counts() -> None:
    with pytest.raises(ValidationError):
        RunStats(error_categories={"network": -1})


@pytest.mark.parametrize("boundary", ["direct", "json"])
def test_run_stats_rejects_inconsistent_token_totals(boundary: str) -> None:
    payload = {
        "prompt_tokens": 7,
        "completion_tokens": 5,
        "total_tokens": 13,
    }

    with pytest.raises(ValidationError, match="total_tokens"):
        if boundary == "direct":
            RunStats.model_validate(payload)
        else:
            RunStats.model_validate_json(json.dumps(payload))


def test_run_stats_defaults_to_zero_counts() -> None:
    assert RunStats().model_dump() == {
        "fetched": 0,
        "prefiltered": 0,
        "cache_hits": 0,
        "figure_cache_hits": 0,
        "figure_requests": 0,
        "figure_available": 0,
        "figure_unavailable": 0,
        "figure_failed": 0,
        "model_calls": 0,
        "published": 0,
        "failed": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "error_categories": {},
    }


def test_data_file_serializes_complete_public_json_shape() -> None:
    record = make_record()
    data = DataFile(
        generated_at=datetime(2026, 7, 27, tzinfo=UTC),
        stats=RunStats(fetched=1, published=1),
        papers=[record],
    )

    payload = data.model_dump(mode="json")

    assert set(payload) == {"schema_version", "generated_at", "stats", "papers"}
    assert payload["schema_version"] == "1"
    assert payload["stats"]["fetched"] == 1
    assert set(payload["papers"][0]) == {
        "arxiv_id",
        "version",
        "published_at",
        "updated_at",
        "title",
        "title_zh",
        "authors",
        "arxiv_categories",
        "abstract",
        "matched_rules",
        "analysis",
        "resources",
        "provenance",
        "figure_gallery",
    }
    assert payload["papers"][0]["resources"] == {
        "arxiv_url": "https://arxiv.org/abs/2607.12345",
        "pdf_url": "https://arxiv.org/pdf/2607.12345",
        "project_url": None,
        "code_url": None,
    }
