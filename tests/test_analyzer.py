import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from vla_wam_daily.analyzer import analyze_paper
from vla_wam_daily.models import (
    AIOutput,
    AnalyzedPaperRecord,
    CacheEntry,
    RawPaper,
    TokenUsage,
)

VALID_AI_PAYLOAD: dict[str, object] = {
    "title_zh": "用于机器人操作的视觉语言动作策略",
    "analysis": {
        "relevance_score": 8,
        "primary_topic": "VLA",
        "tags": ["Vision-Language", "Robot Manipulation"],
        "one_sentence_summary": "提出一种机器人多模态策略。",
        "main_contribution": "统一视觉、语言与动作。",
        "method": "多模态策略学习。",
        "key_results": "摘要未说明",
        "limitations": "摘要未说明",
        "relation_to_vla_wam": "直接属于 VLA。",
    },
}
VALID_USAGE = TokenUsage(
    prompt_tokens=100,
    completion_tokens=30,
    total_tokens=130,
)


class FakeClient:
    def __init__(
        self,
        *,
        payload: object | None = None,
        usage: object = VALID_USAGE,
        model: object = "deepseek-v4-pro",
    ) -> None:
        self.model = model
        self.payload = deepcopy(VALID_AI_PAYLOAD) if payload is None else payload
        self.usage = usage
        self.calls: list[tuple[str, str]] = []

    def analyze(
        self,
        *,
        system_prompt: str,
        paper_json: str,
    ) -> tuple[Any, Any]:
        self.calls.append((system_prompt, paper_json))
        return self.payload, self.usage


def raw_paper(
    *,
    abstract: str = (
        "我们提出 vision-language-action policy。 Code: https://github.com/example/vla-policy."
    ),
    comment: str | None = "Project page https://example.github.io/vla-policy/",
) -> RawPaper:
    now = datetime(2026, 7, 27, tzinfo=UTC)
    return RawPaper(
        arxiv_id="2607.12345",
        version=2,
        published_at=now,
        updated_at=now + timedelta(hours=1),
        title="A Vision-Language-Action Policy 机器人",
        authors=["Ada Robot", "Wei Model"],
        arxiv_categories=["cs.RO", "cs.CV"],
        abstract=abstract,
        comment=comment,
    )


def analyze_with(
    client: FakeClient,
    *,
    paper: RawPaper | None = None,
    matched_rules: list[str] | tuple[str, ...] = (
        "exact:vision-language-action",
        "semantic:robot-policy",
    ),
    prompt: str = "Return one valid JSON object.",
    prompt_version: str = "1",
    analyzed_at: datetime = datetime(2026, 7, 27, 10, 0, tzinfo=timezone(timedelta(hours=8))),
) -> tuple[AnalyzedPaperRecord, TokenUsage]:
    return analyze_paper(
        paper=paper or raw_paper(),
        matched_rules=matched_rules,
        client=client,
        prompt=prompt,
        prompt_version=prompt_version,
        analyzed_at=analyzed_at,
    )


def test_analyzer_builds_traceable_immutable_record() -> None:
    record, usage = analyze_with(FakeClient())

    assert isinstance(record, AnalyzedPaperRecord)
    assert record.arxiv_id == "2607.12345"
    assert record.version == 2
    assert record.title == "A Vision-Language-Action Policy 机器人"
    assert record.title_zh == "用于机器人操作的视觉语言动作策略"
    assert record.authors == ("Ada Robot", "Wei Model")
    assert record.arxiv_categories == ("cs.RO", "cs.CV")
    assert record.analysis.relevance_score == 8
    assert record.provenance.analysis_scope == "title_and_abstract"
    assert record.provenance.model == "deepseek-v4-pro"
    assert record.provenance.prompt_version == "1"
    assert record.provenance.analyzed_at == datetime(2026, 7, 27, 2, 0, tzinfo=UTC)
    assert usage is VALID_USAGE
    assert not hasattr(record, "figure_gallery")
    with pytest.raises(ValidationError):
        record.title = "mutated"
    assert CacheEntry(key="2607.12345:v2:prompt-1", record=record).record is record


def test_analyzer_sends_only_allowed_stable_unicode_ai_input() -> None:
    client = FakeClient()

    first_record, _ = analyze_with(
        client,
        matched_rules=[
            "semantic:robot-policy",
            "exact:vision-language-action",
            "semantic:robot-policy",
        ],
    )
    second_record, _ = analyze_with(
        client,
        matched_rules=(
            "semantic:robot-policy",
            "exact:vision-language-action",
            "semantic:robot-policy",
        ),
    )

    first_prompt, first_json = client.calls[0]
    second_prompt, second_json = client.calls[1]
    payload = json.loads(first_json)
    assert first_prompt == "Return one valid JSON object."
    assert set(payload) == {
        "arxiv_id",
        "title",
        "abstract",
        "arxiv_categories",
        "matched_rules",
    }
    assert payload["title"] == "A Vision-Language-Action Policy 机器人"
    assert payload["abstract"].startswith("我们提出")
    assert "\\u" not in first_json
    assert first_json == second_json
    assert json.dumps(payload, ensure_ascii=False, sort_keys=True) == first_json
    assert payload["matched_rules"] == [
        "semantic:robot-policy",
        "exact:vision-language-action",
    ]
    assert (
        first_record.matched_rules
        == second_record.matched_rules
        == (
            "semantic:robot-policy",
            "exact:vision-language-action",
        )
    )
    assert "authors" not in payload
    assert "comment" not in payload
    assert "published_at" not in payload
    assert "updated_at" not in payload


def test_analyzer_extracts_resources_only_from_original_metadata() -> None:
    record, _ = analyze_with(FakeClient())

    assert str(record.resources.code_url) == "https://github.com/example/vla-policy"
    assert str(record.resources.project_url) == "https://example.github.io/vla-policy/"
    assert str(record.resources.arxiv_url) == "https://arxiv.org/abs/2607.12345"
    assert str(record.resources.pdf_url) == "https://arxiv.org/pdf/2607.12345"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update(extra_field="unexpected"),
        lambda payload: payload.pop("title_zh"),
        lambda payload: payload["analysis"].update(primary_topic="Other"),  # type: ignore[union-attr]
        lambda payload: payload["analysis"].update(tags=["Unknown Tag"]),  # type: ignore[union-attr]
        lambda payload: payload["analysis"].update(relevance_score="8"),  # type: ignore[union-attr]
        lambda payload: payload["analysis"].update(relevance_score=True),  # type: ignore[union-attr]
        lambda payload: payload["analysis"].pop("method"),  # type: ignore[union-attr]
        lambda payload: payload["analysis"].update(extra_field="unexpected"),  # type: ignore[union-attr]
    ],
    ids=[
        "top-level-extra",
        "missing-title",
        "wrong-topic",
        "wrong-tag",
        "string-score",
        "boolean-score",
        "missing-analysis-field",
        "analysis-extra",
    ],
)
def test_analyzer_rejects_ai_output_outside_contract(mutate: Any) -> None:
    payload = deepcopy(VALID_AI_PAYLOAD)
    mutate(payload)

    with pytest.raises(ValidationError):
        analyze_with(FakeClient(payload=payload))


@pytest.mark.parametrize("payload", [[], "not-an-object", None])
def test_analyzer_rejects_non_object_ai_payload(payload: object) -> None:
    client = FakeClient(payload=[])
    client.payload = payload

    with pytest.raises(ValidationError):
        analyze_with(client)


@pytest.mark.parametrize("score", [0, 11, -1])
def test_analyzer_rejects_out_of_range_score(score: int) -> None:
    payload = deepcopy(VALID_AI_PAYLOAD)
    analysis = payload["analysis"]
    assert isinstance(analysis, dict)
    analysis["relevance_score"] = score

    with pytest.raises(ValidationError):
        analyze_with(FakeClient(payload=payload))


@pytest.mark.parametrize(
    "matched_rules",
    [
        [],
        (),
        [""],
        ["   "],
        [" exact:vision-language-action"],
        ["exact:vision-language-action "],
        ["exact:vision-language-action", 1],
        "exact:vision-language-action",
    ],
)
def test_analyzer_rejects_invalid_matched_rules(matched_rules: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        analyze_with(FakeClient(), matched_rules=matched_rules)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("prompt", ""),
        ("prompt", "   \n"),
        ("prompt_version", ""),
        ("prompt_version", "  "),
    ],
)
def test_analyzer_rejects_blank_prompt_metadata(field: str, value: str) -> None:
    arguments = {field: value}

    with pytest.raises(ValueError):
        analyze_with(FakeClient(), **arguments)  # type: ignore[arg-type]


@pytest.mark.parametrize("model", ["", "  ", None, 123])
def test_analyzer_rejects_invalid_client_model(model: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        analyze_with(FakeClient(model=model))


def test_analyzer_rejects_naive_analyzed_at() -> None:
    with pytest.raises(ValidationError):
        analyze_with(
            FakeClient(),
            analyzed_at=datetime(2026, 7, 27, 2, 0),
        )


def test_analyzer_rejects_inconsistent_token_usage() -> None:
    usage = TokenUsage(prompt_tokens=100, completion_tokens=30, total_tokens=999)

    with pytest.raises(ValueError, match="inconsistent"):
        analyze_with(FakeClient(usage=usage))


@pytest.mark.parametrize("usage", [None, {}, object()])
def test_analyzer_rejects_non_token_usage(usage: object) -> None:
    with pytest.raises(TypeError, match="TokenUsage"):
        analyze_with(FakeClient(usage=usage))


def test_analyzer_does_not_mutate_input_paper_or_ai_payload() -> None:
    paper = raw_paper()
    paper_before = paper.model_dump(mode="json")
    payload = deepcopy(VALID_AI_PAYLOAD)
    payload_before = deepcopy(payload)

    analyze_with(FakeClient(payload=payload), paper=paper)

    assert paper.model_dump(mode="json") == paper_before
    assert payload == payload_before


def test_analysis_client_output_is_compatible_with_ai_output_contract() -> None:
    record, _ = analyze_with(FakeClient())

    validated = AIOutput(
        title_zh=record.title_zh,
        analysis=record.analysis,
    )
    assert validated.analysis.primary_topic.value == "VLA"
