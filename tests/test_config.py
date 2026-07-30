import re
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from vla_wam_daily.config import ArxivConfig, load_config
from vla_wam_daily.models import ALLOWED_TAGS, Topic


@pytest.fixture
def config_payload() -> dict[str, object]:
    with Path("config/topics.yaml").open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def write_config(tmp_path: Path, payload: dict[str, object]) -> tuple[Path, Path]:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    path = config_dir / "topics.yaml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    (prompt_dir / "analysis-v1.md").write_text("prompt", encoding="utf-8")
    return path, prompt_dir


def test_default_config_uses_quality_model() -> None:
    config = load_config(Path("config/topics.yaml"))
    assert config.arxiv.max_results_per_category == 2000
    assert config.analysis.model_for("quality") == "deepseek-v4-pro"
    assert config.analysis.model_for("economy") == "deepseek-v4-flash"
    assert config.analysis.threshold == 6
    assert config.analysis.max_candidates == 60


def test_arxiv_schema_default_supports_three_day_catchup_capacity() -> None:
    config = ArxivConfig(categories=["cs.RO"])

    assert config.max_results_per_category == 2000
    assert config.timeout_seconds == 60.0
    assert config.retries == 5
    assert config.retry_wait_seconds == 5.0
    assert config.use_oai_for_recent is True


def test_standalone_vla_and_wam_are_not_exact_phrases() -> None:
    config = load_config(Path("config/topics.yaml"))
    normalized = {phrase.casefold() for phrase in config.prefilter.exact_phrases}
    assert "vla" not in normalized
    assert "wam" not in normalized


@pytest.mark.parametrize(
    ("section", "mutate"),
    [
        ("category", lambda payload: payload["arxiv"]["categories"].__setitem__(0, " ")),
        ("phrase", lambda payload: payload["prefilter"]["exact_phrases"].__setitem__(0, " ")),
        (
            "rule name",
            lambda payload: payload["prefilter"]["composite_rules"].__getitem__(0).__setitem__(
                "name", " "
            ),
        ),
        (
            "inner group",
            lambda payload: payload["prefilter"]["composite_rules"].__getitem__(0).__setitem__(
                "groups", [[]]
            ),
        ),
        ("prompt version", lambda payload: payload["analysis"].__setitem__("prompt_version", " ")),
        (
            "model name",
            lambda payload: payload["analysis"]["model_profiles"].__setitem__("quality", " "),
        ),
    ],
)
def test_rejects_blank_semantic_config_strings(
    tmp_path: Path,
    config_payload: dict[str, object],
    section: str,
    mutate: object,
) -> None:
    mutate(config_payload)
    path, prompt_dir = write_config(tmp_path, config_payload)

    with pytest.raises(ValidationError):
        load_config(path, prompt_dir=prompt_dir)


def test_rejects_empty_prefilter_rules(tmp_path: Path, config_payload: dict[str, object]) -> None:
    config_payload["prefilter"]["exact_phrases"] = []
    config_payload["prefilter"]["composite_rules"] = []
    path, prompt_dir = write_config(tmp_path, config_payload)

    with pytest.raises(ValidationError):
        load_config(path, prompt_dir=prompt_dir)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["prefilter"]["exact_phrases"].__setitem__(0, "..."),
        lambda payload: payload["prefilter"]["composite_rules"].__getitem__(0)["groups"]
        .__getitem__(0)
        .__setitem__(0, "---"),
    ],
)
def test_rejects_prefilter_phrases_without_word_characters(
    tmp_path: Path, config_payload: dict[str, object], mutate: object
) -> None:
    mutate(config_payload)
    path, prompt_dir = write_config(tmp_path, config_payload)

    with pytest.raises(ValidationError):
        load_config(path, prompt_dir=prompt_dir)


@pytest.mark.parametrize("name", ["Vision-Language", "vision-language", "1vision"])
def test_rejects_unstable_composite_rule_names(
    tmp_path: Path, config_payload: dict[str, object], name: str
) -> None:
    config_payload["prefilter"]["composite_rules"].__getitem__(0).__setitem__("name", name)
    path, prompt_dir = write_config(tmp_path, config_payload)

    with pytest.raises(ValidationError):
        load_config(path, prompt_dir=prompt_dir)


def test_rejects_duplicate_composite_rule_names(
    tmp_path: Path, config_payload: dict[str, object]
) -> None:
    duplicate = config_payload["prefilter"]["composite_rules"].__getitem__(0).copy()
    config_payload["prefilter"]["composite_rules"].append(duplicate)
    path, prompt_dir = write_config(tmp_path, config_payload)

    with pytest.raises(ValidationError):
        load_config(path, prompt_dir=prompt_dir)


@pytest.mark.parametrize("profiles", [{}, {"economy": "deepseek-v4-flash"}])
def test_requires_nonblank_quality_model_profile(
    tmp_path: Path, config_payload: dict[str, object], profiles: dict[str, str]
) -> None:
    config_payload["analysis"]["model_profiles"] = profiles
    path, prompt_dir = write_config(tmp_path, config_payload)

    with pytest.raises(ValidationError):
        load_config(path, prompt_dir=prompt_dir)


def test_rejects_quoted_numeric_values(tmp_path: Path, config_payload: dict[str, object]) -> None:
    config_payload["analysis"]["threshold"] = "6"
    path, prompt_dir = write_config(tmp_path, config_payload)

    with pytest.raises(ValidationError):
        load_config(path, prompt_dir=prompt_dir)


def test_requires_versioned_prompt_artifact(
    tmp_path: Path, config_payload: dict[str, object]
) -> None:
    path, prompt_dir = write_config(tmp_path, config_payload)
    (prompt_dir / "analysis-v1.md").unlink()

    with pytest.raises(FileNotFoundError):
        load_config(path, prompt_dir=prompt_dir)


def test_unknown_profile_error_lists_sorted_choices() -> None:
    config = load_config(Path("config/topics.yaml"))

    with pytest.raises(ValueError, match=r"choose one of: economy, quality"):
        config.analysis.model_for("fast")


def test_prompt_taxonomy_matches_models() -> None:
    prompt = Path("prompts/analysis-v1.md").read_text(encoding="utf-8")

    def values_after(heading: str) -> set[str]:
        section = prompt.split(heading, maxsplit=1)[1].split("\n\n", maxsplit=1)[0]
        return set(re.findall(r'"([^"]+)"', section))

    assert values_after("Allowed primary_topic values:") == {topic.value for topic in Topic}
    assert values_after("Allowed tags:") == ALLOWED_TAGS
