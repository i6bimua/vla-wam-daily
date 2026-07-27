from datetime import UTC, datetime
from pathlib import Path

from vla_wam_daily.config import load_config
from vla_wam_daily.models import RawPaper
from vla_wam_daily.prefilter import match_paper


def paper(title: str, abstract: str) -> RawPaper:
    now = datetime(2026, 7, 27, tzinfo=UTC)
    return RawPaper(
        arxiv_id="2607.12345",
        version=1,
        published_at=now,
        updated_at=now,
        title=title,
        authors=["A. Researcher"],
        arxiv_categories=["cs.RO"],
        abstract=abstract,
    )


def test_exact_phrase_matches_hyphen_variation() -> None:
    config = load_config(Path("config/topics.yaml")).prefilter
    matches = match_paper(
        paper("A Vision–Language–Action Model", "We learn a robot policy."),
        config,
    )
    assert "exact:vision-language-action" in matches


def test_composite_rule_requires_both_groups() -> None:
    config = load_config(Path("config/topics.yaml")).prefilter
    matches = match_paper(
        paper("Multimodal policy learning", "A VLM controls robot manipulation."),
        config,
    )
    assert "composite:vision_language_robotics" in matches


def test_standalone_acronyms_do_not_match() -> None:
    config = load_config(Path("config/topics.yaml")).prefilter
    assert match_paper(paper("VLA transport protocol", "WAM compression."), config) == []
