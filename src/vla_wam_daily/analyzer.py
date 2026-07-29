import json
from datetime import datetime
from typing import Annotated, Protocol

from pydantic import Field, TypeAdapter

from vla_wam_daily.models import (
    AIOutput,
    AnalyzedPaperRecord,
    Provenance,
    RawPaper,
    TokenUsage,
)
from vla_wam_daily.resources import extract_resources

StrictRelevanceScore = Annotated[int, Field(strict=True, ge=1, le=10)]
RELEVANCE_SCORE = TypeAdapter(StrictRelevanceScore)


class AnalysisClient(Protocol):
    model: str

    def analyze(
        self,
        *,
        system_prompt: str,
        paper_json: str,
    ) -> tuple[dict[str, object], TokenUsage]: ...


def _require_nonblank_string(value: object, *, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value.strip():
        raise ValueError(f"{name} must be non-empty")
    return value


def _normalize_matched_rules(
    matched_rules: list[str] | tuple[str, ...],
) -> tuple[str, ...]:
    if not isinstance(matched_rules, (list, tuple)):
        raise TypeError("matched_rules must be a list or tuple")
    if not matched_rules:
        raise ValueError("matched_rules must be non-empty")

    normalized: list[str] = []
    seen: set[str] = set()
    for rule in matched_rules:
        if not isinstance(rule, str):
            raise TypeError("matched_rules members must be strings")
        if not rule or rule != rule.strip():
            raise ValueError("matched_rules members must be non-empty and trimmed")
        if rule not in seen:
            seen.add(rule)
            normalized.append(rule)
    return tuple(normalized)


def _validate_ai_output(payload: object) -> AIOutput:
    if isinstance(payload, dict):
        analysis = payload.get("analysis")
        if isinstance(analysis, dict) and "relevance_score" in analysis:
            RELEVANCE_SCORE.validate_python(analysis["relevance_score"])
    return AIOutput.model_validate(payload)


def _validate_usage(usage: object) -> TokenUsage:
    if not isinstance(usage, TokenUsage):
        raise TypeError("client usage must be a TokenUsage")
    if usage.total_tokens != usage.prompt_tokens + usage.completion_tokens:
        raise ValueError("token usage totals are inconsistent")
    return usage


def analyze_paper(
    *,
    paper: RawPaper,
    matched_rules: list[str] | tuple[str, ...],
    client: AnalysisClient,
    prompt: str,
    prompt_version: str,
    analyzed_at: datetime,
) -> tuple[AnalyzedPaperRecord, TokenUsage]:
    normalized_rules = _normalize_matched_rules(matched_rules)
    system_prompt = _require_nonblank_string(prompt, name="prompt")
    model = _require_nonblank_string(client.model, name="client.model")
    version = _require_nonblank_string(prompt_version, name="prompt_version")
    provenance = Provenance(
        analysis_scope="title_and_abstract",
        model=model,
        prompt_version=version,
        analyzed_at=analyzed_at,
    )

    input_payload = {
        "arxiv_id": paper.arxiv_id,
        "title": paper.title,
        "abstract": paper.abstract,
        "arxiv_categories": paper.arxiv_categories,
        "matched_rules": normalized_rules,
    }
    payload, raw_usage = client.analyze(
        system_prompt=system_prompt,
        paper_json=json.dumps(input_payload, ensure_ascii=False, sort_keys=True),
    )
    output = _validate_ai_output(payload)
    usage = _validate_usage(raw_usage)

    record = AnalyzedPaperRecord(
        arxiv_id=paper.arxiv_id,
        version=paper.version,
        published_at=paper.published_at,
        updated_at=paper.updated_at,
        title=paper.title,
        title_zh=output.title_zh,
        authors=tuple(paper.authors),
        arxiv_categories=tuple(paper.arxiv_categories),
        abstract=paper.abstract,
        matched_rules=normalized_rules,
        analysis=output.analysis,
        resources=extract_resources(paper.arxiv_id, paper.abstract, paper.comment),
        provenance=provenance,
    )
    return record, usage
