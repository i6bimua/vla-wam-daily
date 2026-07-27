from pathlib import Path

from vla_wam_daily.config import load_config


def test_default_config_uses_quality_model() -> None:
    config = load_config(Path("config/topics.yaml"))
    assert config.analysis.model_for("quality") == "deepseek-v4-pro"
    assert config.analysis.model_for("economy") == "deepseek-v4-flash"
    assert config.analysis.threshold == 6
    assert config.analysis.max_candidates == 60


def test_standalone_vla_and_wam_are_not_exact_phrases() -> None:
    config = load_config(Path("config/topics.yaml"))
    normalized = {phrase.casefold() for phrase in config.prefilter.exact_phrases}
    assert "vla" not in normalized
    assert "wam" not in normalized
