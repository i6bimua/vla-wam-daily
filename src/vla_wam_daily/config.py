import re
import unicodedata
from pathlib import Path
from typing import Annotated

import yaml
from pydantic import AfterValidator, Field, model_validator

from vla_wam_daily.models import StrictModel


def strip_nonempty(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("must not be blank")
    return stripped


SEARCH_SEPARATOR_RE = re.compile(r"[\s\-_–—/]+")
SEARCH_PUNCTUATION_RE = re.compile(r"[^\w\s]")


def require_searchable_phrase(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = SEARCH_SEPARATOR_RE.sub(" ", normalized)
    normalized = SEARCH_PUNCTUATION_RE.sub(" ", normalized)
    if not re.search(r"\w", normalized):
        raise ValueError("must contain a searchable word character")
    return value


ConfigString = Annotated[str, AfterValidator(strip_nonempty)]
ConfigStringList = Annotated[list[ConfigString], Field(min_length=1)]
SearchPhrase = Annotated[
    str, AfterValidator(strip_nonempty), AfterValidator(require_searchable_phrase)
]
SearchPhraseList = Annotated[list[SearchPhrase], Field(min_length=1)]
CompositeRuleName = Annotated[
    str, AfterValidator(strip_nonempty), Field(pattern=r"^[a-z][a-z0-9_]*$")
]


class ArxivConfig(StrictModel):
    categories: ConfigStringList
    lookback_days: int = Field(default=3, ge=1, le=31)
    max_results_per_category: int = Field(default=500, ge=1, le=2000)
    request_delay_seconds: float = Field(default=3.0, ge=0)


class CompositeRule(StrictModel):
    name: CompositeRuleName
    groups: list[SearchPhraseList] = Field(min_length=2)


class PrefilterConfig(StrictModel):
    exact_phrases: list[SearchPhrase]
    composite_rules: list[CompositeRule]

    @model_validator(mode="after")
    def has_usable_rule(self) -> "PrefilterConfig":
        if not self.exact_phrases and not self.composite_rules:
            raise ValueError("at least one prefilter rule is required")
        if len({rule.name for rule in self.composite_rules}) != len(self.composite_rules):
            raise ValueError("composite rule names must be unique")
        return self


class AnalysisConfig(StrictModel):
    threshold: int = Field(default=6, ge=1, le=10)
    max_candidates: int = Field(default=60, ge=1)
    max_concurrency: int = Field(default=3, ge=1, le=8)
    max_failure_ratio: float = Field(default=0.30, ge=0, le=1)
    prompt_version: ConfigString = "1"
    max_output_tokens: int = Field(default=1800, ge=512)
    model_profiles: dict[ConfigString, ConfigString] = Field(min_length=1)

    @model_validator(mode="after")
    def has_quality_profile(self) -> "AnalysisConfig":
        if "quality" not in self.model_profiles:
            raise ValueError("model_profiles must contain a quality profile")
        return self

    def model_for(self, profile: str) -> str:
        try:
            return self.model_profiles[profile]
        except KeyError as exc:
            choices = ", ".join(sorted(self.model_profiles))
            raise ValueError(f"unknown profile {profile!r}; choose one of: {choices}") from exc

    def prompt_path(self, prompt_dir: Path) -> Path:
        path = prompt_dir / f"analysis-v{self.prompt_version}.md"
        if not path.is_file():
            raise FileNotFoundError(
                f"analysis prompt version {self.prompt_version!r} not found: {path}"
            )
        return path


class AppConfig(StrictModel):
    arxiv: ArxivConfig
    prefilter: PrefilterConfig
    analysis: AnalysisConfig


def load_config(path: Path, *, prompt_dir: Path | None = None) -> AppConfig:
    with path.open(encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    config = AppConfig.model_validate(payload, strict=True)
    config.analysis.prompt_path(prompt_dir or path.parent.parent / "prompts")
    return config
