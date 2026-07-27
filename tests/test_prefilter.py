from datetime import UTC, datetime
from pathlib import Path

from vla_wam_daily.config import load_config
from vla_wam_daily.models import RawPaper
from vla_wam_daily.prefilter import contains_phrase, match_paper, normalize


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


def test_exact_phrase_does_not_span_title_and_abstract() -> None:
    config = load_config(Path("config/topics.yaml")).prefilter
    assert (
        "exact:vision-language-action"
        not in match_paper(paper("A survey of vision", "Language action is discussed."), config)
    )


def test_composite_rule_can_be_satisfied_across_title_and_abstract() -> None:
    config = load_config(Path("config/topics.yaml")).prefilter
    matches = match_paper(paper("A multimodal model", "Robot manipulation is discussed."), config)
    assert "composite:vision_language_robotics" in matches


def test_composite_rule_does_not_match_one_group_alone() -> None:
    config = load_config(Path("config/topics.yaml")).prefilter
    matches = match_paper(paper("A multimodal model", "Image representation learning."), config)
    assert "composite:vision_language_robotics" not in matches


def test_contains_phrase_rejects_punctuation_only_phrase() -> None:
    assert contains_phrase(normalize("Robot policy learning"), "...") is False


def test_contains_phrase_rejects_embedded_words() -> None:
    assert contains_phrase(normalize("robotics"), "robot") is False


def test_matches_are_ordered_and_deduplicated() -> None:
    config = load_config(Path("config/topics.yaml")).prefilter
    matches = match_paper(paper("Vision language action", "A multimodal robot policy."), config)
    assert matches[:3] == [
        "exact:vision-language-action",
        "exact:multimodal-robot-policy",
        "composite:vision_language_robotics",
    ]
    assert matches.count("exact:vision-language-action") == 1
