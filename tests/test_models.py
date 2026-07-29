from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from tests.factories import make_gallery, make_record
from vla_wam_daily.models import (
    Analysis,
    CacheEntry,
    DataFile,
    FigureAsset,
    FigureGallery,
    FigureStatus,
    PaperRecord,
    Provenance,
    RawPaper,
    Resources,
    RunStats,
    Topic,
)


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
                source_url="https://arxiv.org/html/2607.12345v1",
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
                "source_url": "https://arxiv.org/html/2607.12345v1",
                "source": "arxiv_html",
            }
        ],
        "checked_at": "2026-07-27T00:00:00Z",
    }


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
            source_url="https://arxiv.org/html/2607.12345v1",
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


def test_data_file_rejects_unchecked_figure_gallery() -> None:
    record_data = make_record().model_dump()
    record_data["figure_gallery"] = None
    record = PaperRecord(**record_data)

    with pytest.raises(ValidationError, match="2607.12345"):
        DataFile(
            generated_at=datetime(2026, 7, 27, tzinfo=UTC),
            stats=RunStats(),
            papers=[record],
        )


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
        source_url="https://arxiv.org/html/2607.12345v1",
    )
    figure_two = FigureAsset(
        number=2,
        label="Figure 2",
        caption="Robot evaluation environments.",
        image_urls=["https://arxiv.org/html/2607.12345v1/x3.png"],
        source_url="https://arxiv.org/html/2607.12345v1",
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

    assert analysis.tags == ["Vision-Language", "Robot Manipulation"]


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
