from pathlib import Path

import yaml
from pydantic import Field

from vla_wam_daily.models import StrictModel


class ArxivConfig(StrictModel):
    categories: list[str] = Field(min_length=1)
    lookback_days: int = Field(default=3, ge=1, le=31)
    max_results_per_category: int = Field(default=500, ge=1, le=2000)
    request_delay_seconds: float = Field(default=3.0, ge=0)


class CompositeRule(StrictModel):
    name: str
    groups: list[list[str]] = Field(min_length=2)


class PrefilterConfig(StrictModel):
    exact_phrases: list[str]
    composite_rules: list[CompositeRule]


class AnalysisConfig(StrictModel):
    threshold: int = Field(default=6, ge=1, le=10)
    max_candidates: int = Field(default=60, ge=1)
    max_concurrency: int = Field(default=3, ge=1, le=8)
    max_failure_ratio: float = Field(default=0.30, ge=0, le=1)
    prompt_version: str = "1"
    max_output_tokens: int = Field(default=1800, ge=512)
    model_profiles: dict[str, str]

    def model_for(self, profile: str) -> str:
        try:
            return self.model_profiles[profile]
        except KeyError as exc:
            choices = ", ".join(sorted(self.model_profiles))
            raise ValueError(f"unknown profile {profile!r}; choose one of: {choices}") from exc


class AppConfig(StrictModel):
    arxiv: ArxivConfig
    prefilter: PrefilterConfig
    analysis: AnalysisConfig


def load_config(path: Path) -> AppConfig:
    with path.open(encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    return AppConfig.model_validate(payload)
