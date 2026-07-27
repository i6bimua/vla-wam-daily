from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from tests.factories import make_record
from vla_wam_daily.models import Analysis, DataFile, Topic


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
