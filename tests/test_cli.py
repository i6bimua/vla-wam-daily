import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from typer._click.utils import strip_ansi
from typer.testing import CliRunner

import vla_wam_daily.cli as cli_module
from tests.factories import make_record
from vla_wam_daily.config import AppConfig, load_config
from vla_wam_daily.figure_sync import FigureSyncReport
from vla_wam_daily.models import RunStats
from vla_wam_daily.pipeline import RunReport

RUNNER = CliRunner()
SECRET = "test-deepseek-secret"


@pytest.fixture
def real_cli_paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    config_dir = tmp_path / "config"
    prompt_dir = tmp_path / "prompts"
    config_dir.mkdir()
    prompt_dir.mkdir()
    config_path = config_dir / "topics.yaml"
    config_path.write_text(
        Path("config/topics.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    prompt_path = prompt_dir / "analysis-v1.md"
    prompt_path.write_text("只返回一个 JSON 对象。", encoding="utf-8")
    return config_path, prompt_path, tmp_path / "data"


@pytest.fixture
def config() -> AppConfig:
    return load_config(Path("config/topics.yaml"))


@pytest.fixture
def prompt_path(tmp_path: Path) -> Path:
    path = tmp_path / "analysis-v1.md"
    path.write_text("只返回一个 JSON 对象。", encoding="utf-8")
    return path


class ManagedClient:
    def __init__(
        self,
        kind: str,
        events: list[str],
        *,
        enter_error: BaseException | None = None,
        model: str | None = None,
    ) -> None:
        self.kind = kind
        self.events = events
        self.enter_error = enter_error
        self.model = model
        self.close_calls = 0

    def __enter__(self) -> "ManagedClient":
        self.events.append(f"enter:{self.kind}")
        if self.enter_error is not None:
            raise self.enter_error
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> None:
        self.close()

    def close(self) -> None:
        self.close_calls += 1
        self.events.append(f"close:{self.kind}")


class Harness:
    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        config: AppConfig,
        *,
        failure_stage: str | None = None,
        report: RunReport | None = None,
    ) -> None:
        self.events: list[str] = []
        self.constructor_kwargs: dict[str, dict[str, object]] = {}
        self.run_kwargs: dict[str, object] = {}
        self.sync_kwargs: dict[str, object] = {}
        self.config_calls: list[tuple[Path, Path | None]] = []
        self.instances: dict[str, ManagedClient] = {}
        self.failure_stage = failure_stage
        self.report = report or RunReport(
            stats=RunStats(fetched=2, published=1),
            published=(make_record(),),
            dry_run=True,
        )

        monkeypatch.setattr(cli_module, "load_config", self.load_config, raising=False)
        monkeypatch.setattr(cli_module, "ArxivClient", self.constructor("arxiv"), raising=False)
        monkeypatch.setattr(
            cli_module,
            "DeepSeekClient",
            self.constructor("analysis"),
            raising=False,
        )
        monkeypatch.setattr(
            cli_module,
            "ArxivFigureClient",
            self.constructor("figure"),
            raising=False,
        )
        monkeypatch.setattr(
            cli_module,
            "ArxivFigureStore",
            self.constructor("asset"),
            raising=False,
        )
        monkeypatch.setattr(
            cli_module,
            "ArxivSourceFigureExtractor",
            self.constructor("source"),
            raising=False,
        )
        monkeypatch.setattr(
            cli_module,
            "ArxivPdfFigureExtractor",
            self.constructor("pdf"),
            raising=False,
        )
        monkeypatch.setattr(
            cli_module,
            "FigureRecoveryService",
            self.constructor("recovery"),
            raising=False,
        )
        monkeypatch.setattr(cli_module, "run_daily", self.run_daily, raising=False)
        monkeypatch.setattr(
            cli_module,
            "synchronize_figure_assets",
            self.synchronize_figure_assets,
            raising=False,
        )
        self.config = config

    def load_config(self, path: Path, *, prompt_dir: Path | None = None) -> AppConfig:
        self.config_calls.append((path, prompt_dir))
        return self.config

    def constructor(self, kind: str) -> Callable[..., ManagedClient]:
        def construct(**kwargs: object) -> ManagedClient:
            self.events.append(f"construct:{kind}")
            self.constructor_kwargs[kind] = kwargs
            if self.failure_stage == f"{kind}_construct":
                raise RuntimeError(f"{kind} construction failed")
            instance = ManagedClient(
                kind,
                self.events,
                enter_error=(
                    RuntimeError(f"{kind} enter failed")
                    if self.failure_stage == f"{kind}_enter"
                    else None
                ),
                model=kwargs.get("model") if isinstance(kwargs.get("model"), str) else None,
            )
            self.instances[kind] = instance
            return instance

        return construct

    def run_daily(self, **kwargs: object) -> RunReport:
        self.events.append("run")
        self.run_kwargs = kwargs
        if self.failure_stage == "run":
            raise RuntimeError("pipeline failed")
        return self.report

    def synchronize_figure_assets(
        self,
        **kwargs: object,
    ) -> FigureSyncReport:
        self.events.append("sync")
        self.sync_kwargs = kwargs
        return FigureSyncReport(
            papers_scanned=1,
            panels_reused=0,
            panels_mirrored=2,
            panels_failed=0,
        )


def invoke_daily(
    prompt_path: Path,
    *args: str,
    config_path: Path = Path("config/topics.yaml"),
    data_dir: Path = Path("data"),
) -> Any:
    return RUNNER.invoke(
        cli_module.app,
        [
            "daily",
            "--config-path",
            str(config_path),
            "--data-dir",
            str(data_dir),
            "--prompt-path",
            str(prompt_path),
            *args,
        ],
    )


def install_client_sentries(
    monkeypatch: pytest.MonkeyPatch,
    events: list[str],
) -> None:
    def unexpected_client(kind: str) -> Callable[..., None]:
        def construct(**kwargs: object) -> None:
            events.append(kind)
            raise AssertionError(f"{kind} client must not be constructed")

        return construct

    monkeypatch.setattr(cli_module, "ArxivClient", unexpected_client("arxiv"))
    monkeypatch.setattr(cli_module, "DeepSeekClient", unexpected_client("analysis"))
    monkeypatch.setattr(cli_module, "ArxivFigureClient", unexpected_client("figure"))
    monkeypatch.setattr(cli_module, "ArxivFigureStore", unexpected_client("asset"))
    monkeypatch.setattr(
        cli_module,
        "ArxivSourceFigureExtractor",
        unexpected_client("source"),
        raising=False,
    )
    monkeypatch.setattr(
        cli_module,
        "ArxivPdfFigureExtractor",
        unexpected_client("pdf"),
        raising=False,
    )
    monkeypatch.setattr(
        cli_module,
        "FigureRecoveryService",
        unexpected_client("recovery"),
        raising=False,
    )
    monkeypatch.setattr(
        cli_module,
        "run_daily",
        lambda **kwargs: pytest.fail("pipeline must not run"),
    )
    monkeypatch.setattr(
        cli_module,
        "synchronize_figure_assets",
        lambda **kwargs: pytest.fail("Figure synchronization must not run"),
    )


def plain_cli_text(value: str) -> str:
    return strip_ansi(value)


def test_plain_cli_text_removes_split_ansi_styles() -> None:
    styled = "\x1b[36m--\x1b[0m\x1b[36mprofile\x1b[0m"

    assert plain_cli_text(styled) == "--profile"


def assert_parameter_error(result: Any, option: str) -> None:
    assert result.exit_code == 2
    assert option in plain_cli_text(result.stderr)
    assert "Traceback" not in result.stdout
    assert "Traceback" not in result.stderr
    assert SECRET not in result.stdout
    assert SECRET not in result.stderr


def test_daily_help_lists_all_options_without_environment_files_or_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected(*args: object, **kwargs: object) -> None:
        raise AssertionError("help must have no side effects")

    monkeypatch.setattr(cli_module, "load_config", unexpected, raising=False)
    monkeypatch.setattr(cli_module, "ArxivClient", unexpected, raising=False)
    monkeypatch.setattr(cli_module, "DeepSeekClient", unexpected, raising=False)
    monkeypatch.setattr(cli_module, "ArxivFigureClient", unexpected, raising=False)
    monkeypatch.setattr(cli_module, "run_daily", unexpected, raising=False)

    result = RUNNER.invoke(cli_module.app, ["daily", "--help"])
    stdout = plain_cli_text(result.stdout)

    assert result.exit_code == 0
    for option in (
        "--profile",
        "--lookback-days",
        "--threshold",
        "--force-arxiv-id",
        "--dry-run",
        "--config-path",
        "--data-dir",
        "--public-dir",
        "--prompt-path",
    ):
        assert option in stdout


def test_root_help_lists_daily_without_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli_module,
        "load_config",
        lambda *args, **kwargs: pytest.fail("help loaded configuration"),
        raising=False,
    )

    result = RUNNER.invoke(cli_module.app, ["--help"])

    assert result.exit_code == 0
    assert "daily" in result.stdout
    assert "sync-figures" in result.stdout


def test_sync_figures_does_not_require_deepseek_and_prints_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    data_dir = tmp_path / "data"
    public_dir = tmp_path / "public"
    data_dir.mkdir()
    (data_dir / "latest.json").write_text("{}\n", encoding="utf-8")
    events: list[str] = []
    instances = {
        kind: ManagedClient(kind, events)
        for kind in ("asset", "html", "source", "pdf")
    }
    constructor_kwargs: dict[str, dict[str, object]] = {}
    sync_kwargs: dict[str, object] = {}

    def constructor(kind: str) -> Callable[..., ManagedClient]:
        def construct(**kwargs: object) -> ManagedClient:
            events.append(f"construct:{kind}")
            constructor_kwargs[kind] = kwargs
            return instances[kind]

        return construct

    def construct_recovery(**kwargs: object) -> object:
        events.append("construct:recovery")
        constructor_kwargs["recovery"] = kwargs
        return object()

    def synchronize(**kwargs: object) -> FigureSyncReport:
        sync_kwargs.update(kwargs)
        return FigureSyncReport(
            papers_scanned=3,
            panels_reused=2,
            panels_mirrored=4,
            panels_failed=1,
        )

    monkeypatch.setattr(cli_module, "ArxivFigureStore", constructor("asset"))
    monkeypatch.setattr(cli_module, "ArxivFigureClient", constructor("html"))
    monkeypatch.setattr(
        cli_module,
        "ArxivSourceFigureExtractor",
        constructor("source"),
        raising=False,
    )
    monkeypatch.setattr(
        cli_module,
        "ArxivPdfFigureExtractor",
        constructor("pdf"),
        raising=False,
    )
    monkeypatch.setattr(
        cli_module,
        "FigureRecoveryService",
        construct_recovery,
        raising=False,
    )
    monkeypatch.setattr(cli_module, "synchronize_figure_assets", synchronize)
    before = datetime.now(UTC)

    result = RUNNER.invoke(
        cli_module.app,
        [
            "sync-figures",
            "--data-dir",
            str(data_dir),
            "--public-dir",
            str(public_dir),
        ],
    )
    after = datetime.now(UTC)

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "papers_scanned": 3,
        "panels_reused": 2,
        "panels_mirrored": 4,
        "panels_failed": 1,
        "html_recovered": 0,
        "source_recovered": 0,
        "pdf_recovered": 0,
        "recovery_not_found": 0,
        "recovery_failed": 0,
    }
    assert constructor_kwargs["asset"]["public_dir"] == public_dir
    assert constructor_kwargs["asset"]["user_agent"] == cli_module.DEFAULT_USER_AGENT
    shared_client = constructor_kwargs["asset"]["client"]
    assert constructor_kwargs["html"] == {
        "user_agent": cli_module.DEFAULT_USER_AGENT,
        "request_delay_seconds": 3.0,
        "client": shared_client,
    }
    assert constructor_kwargs["source"] == {
        "user_agent": cli_module.DEFAULT_USER_AGENT,
        "client": shared_client,
    }
    assert constructor_kwargs["pdf"] == {
        "user_agent": cli_module.DEFAULT_USER_AGENT,
        "client": shared_client,
    }
    assert constructor_kwargs["recovery"] == {
        "html_fetcher": instances["html"],
        "source_extractor": instances["source"],
        "pdf_extractor": instances["pdf"],
        "store": instances["asset"],
    }
    assert sync_kwargs["data_dir"] == data_dir
    assert sync_kwargs["store"] is instances["asset"]
    assert sync_kwargs["recovery"] is not None
    assert before <= sync_kwargs["now"] <= after
    assert events == [
        "construct:asset",
        "enter:asset",
        "construct:html",
        "enter:html",
        "construct:source",
        "enter:source",
        "construct:pdf",
        "enter:pdf",
        "construct:recovery",
        "close:pdf",
        "close:source",
        "close:html",
        "close:asset",
    ]


def test_sync_figures_rejects_missing_latest_before_constructing_store(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        cli_module,
        "ArxivFigureStore",
        lambda **kwargs: events.append("constructed"),
    )

    result = RUNNER.invoke(
        cli_module.app,
        [
            "sync-figures",
            "--data-dir",
            str(tmp_path / "missing"),
            "--public-dir",
            str(tmp_path / "public"),
        ],
    )

    assert_parameter_error(result, "--data-dir")
    assert events == []


@pytest.mark.parametrize(
    "selector",
    [
        "not-an-arxiv-id",
        "2607.12345v0",
        "2607.12345 ",
        "0601.12345",
    ],
)
def test_invalid_force_id_is_a_parameter_error_before_any_client_construction(
    monkeypatch: pytest.MonkeyPatch,
    real_cli_paths: tuple[Path, Path, Path],
    selector: str,
) -> None:
    config_path, prompt_path, data_dir = real_cli_paths
    monkeypatch.setenv("DEEPSEEK_API_KEY", SECRET)
    events: list[str] = []
    install_client_sentries(monkeypatch, events)

    result = invoke_daily(
        prompt_path,
        "--force-arxiv-id",
        selector,
        config_path=config_path,
        data_dir=data_dir,
    )

    assert_parameter_error(result, "--force-arxiv-id")
    assert events == []


@pytest.mark.parametrize(
    "failure",
    ["missing", "directory", "invalid-yaml", "invalid-schema"],
)
def test_invalid_real_config_path_is_a_clean_parameter_error(
    monkeypatch: pytest.MonkeyPatch,
    real_cli_paths: tuple[Path, Path, Path],
    failure: str,
) -> None:
    config_path, prompt_path, data_dir = real_cli_paths
    if failure == "missing":
        config_path.unlink()
    elif failure == "directory":
        config_path.unlink()
        config_path.mkdir()
    elif failure == "invalid-yaml":
        config_path.write_text("analysis: [", encoding="utf-8")
    else:
        config_path.write_text("arxiv: {}\n", encoding="utf-8")
    monkeypatch.setenv("DEEPSEEK_API_KEY", SECRET)
    events: list[str] = []
    install_client_sentries(monkeypatch, events)

    result = invoke_daily(
        prompt_path,
        config_path=config_path,
        data_dir=data_dir,
    )

    assert_parameter_error(result, "--config-path")
    assert str(config_path.parent.parent) not in result.stdout
    assert str(config_path.parent.parent) not in result.stderr
    assert events == []


@pytest.mark.parametrize("failure", ["missing", "directory", "invalid-utf8"])
def test_invalid_real_prompt_path_is_a_clean_parameter_error(
    monkeypatch: pytest.MonkeyPatch,
    real_cli_paths: tuple[Path, Path, Path],
    failure: str,
) -> None:
    config_path, prompt_path, data_dir = real_cli_paths
    if failure == "missing":
        prompt_path.unlink()
    elif failure == "directory":
        prompt_path.unlink()
        prompt_path.mkdir()
    else:
        prompt_path.write_bytes(b"\xff")
    monkeypatch.setenv("DEEPSEEK_API_KEY", SECRET)
    events: list[str] = []
    install_client_sentries(monkeypatch, events)

    result = invoke_daily(
        prompt_path,
        config_path=config_path,
        data_dir=data_dir,
    )

    assert_parameter_error(result, "--prompt-path")
    assert str(prompt_path.parent.parent) not in result.stdout
    assert str(prompt_path.parent.parent) not in result.stderr
    assert events == []


def test_missing_configured_prompt_version_is_a_prompt_parameter_error(
    monkeypatch: pytest.MonkeyPatch,
    real_cli_paths: tuple[Path, Path, Path],
) -> None:
    config_path, prompt_path, data_dir = real_cli_paths
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            'prompt_version: "1"',
            'prompt_version: "2"',
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", SECRET)
    events: list[str] = []
    install_client_sentries(monkeypatch, events)

    result = invoke_daily(
        prompt_path,
        config_path=config_path,
        data_dir=data_dir,
    )

    assert_parameter_error(result, "--prompt-path")
    assert str(prompt_path.parent.parent) not in result.stdout
    assert str(prompt_path.parent.parent) not in result.stderr
    assert events == []


def test_defaults_use_config_values_and_quality_model(
    monkeypatch: pytest.MonkeyPatch,
    config: AppConfig,
    prompt_path: Path,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", SECRET)
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    config.arxiv.lookback_days = 4
    config.analysis.threshold = 7
    harness = Harness(monkeypatch, config)

    result = invoke_daily(prompt_path, data_dir=tmp_path)

    assert result.exit_code == 0
    assert harness.constructor_kwargs["analysis"]["model"] == "deepseek-v4-pro"
    assert harness.run_kwargs["lookback_days"] == 4
    assert harness.run_kwargs["threshold"] == 7
    assert harness.config_calls == [(Path("config/topics.yaml"), prompt_path.parent)]


def test_nonblank_environment_model_override_is_trimmed(
    monkeypatch: pytest.MonkeyPatch,
    config: AppConfig,
    prompt_path: Path,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", SECRET)
    monkeypatch.setenv("DEEPSEEK_MODEL", "  deepseek-custom  ")
    harness = Harness(monkeypatch, config)

    result = invoke_daily(prompt_path)

    assert result.exit_code == 0
    assert harness.constructor_kwargs["analysis"]["model"] == "deepseek-custom"


def test_blank_environment_model_does_not_override_profile(
    monkeypatch: pytest.MonkeyPatch,
    config: AppConfig,
    prompt_path: Path,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", SECRET)
    monkeypatch.setenv("DEEPSEEK_MODEL", " \t ")
    harness = Harness(monkeypatch, config)

    result = invoke_daily(prompt_path, "--profile", "economy")

    assert result.exit_code == 0
    assert harness.constructor_kwargs["analysis"]["model"] == "deepseek-v4-flash"


def test_unknown_profile_is_rejected_before_environment_model_override(
    monkeypatch: pytest.MonkeyPatch,
    config: AppConfig,
    prompt_path: Path,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", SECRET)
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-custom")
    harness = Harness(monkeypatch, config)

    result = invoke_daily(prompt_path, "--profile", "missing")

    assert result.exit_code != 0
    assert harness.events == []
    assert SECRET not in result.stdout
    assert SECRET not in result.stderr
    assert SECRET not in str(result.exception)


@pytest.mark.parametrize("api_key", [None, "", "   "])
def test_missing_or_blank_api_key_is_rejected_without_secret_disclosure(
    monkeypatch: pytest.MonkeyPatch,
    config: AppConfig,
    prompt_path: Path,
    api_key: str | None,
) -> None:
    if api_key is None:
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    else:
        monkeypatch.setenv("DEEPSEEK_API_KEY", api_key)
    harness = Harness(monkeypatch, config)

    result = invoke_daily(prompt_path)

    assert result.exit_code != 0
    assert harness.events == []
    assert "DEEPSEEK_API_KEY is required" in result.stderr
    if api_key and api_key.strip():
        assert api_key not in result.stdout
        assert api_key not in result.stderr
        assert api_key not in str(result.exception)


def test_prompt_filename_must_match_configured_version(
    monkeypatch: pytest.MonkeyPatch,
    config: AppConfig,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", SECRET)
    config.analysis.prompt_version = "2"
    wrong_prompt = tmp_path / "analysis-v1.md"
    wrong_prompt.write_text("wrong prompt", encoding="utf-8")
    (tmp_path / "analysis-v2.md").write_text("right prompt", encoding="utf-8")
    harness = Harness(monkeypatch, config)

    result = invoke_daily(wrong_prompt)

    assert result.exit_code != 0
    assert harness.events == []
    assert "prompt" in result.stderr.casefold()
    assert SECRET not in result.stdout
    assert SECRET not in result.stderr


def test_daily_passes_options_clients_utf8_prompt_and_utc_now(
    monkeypatch: pytest.MonkeyPatch,
    config: AppConfig,
    prompt_path: Path,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", SECRET)
    monkeypatch.setenv("ARXIV_USER_AGENT", "Research Bot/1.0 (research@example.test)")
    harness = Harness(monkeypatch, config)
    before = datetime.now(UTC)

    result = invoke_daily(
        prompt_path,
        "--profile",
        "economy",
        "--lookback-days",
        "5",
        "--threshold",
        "9",
        "--force-arxiv-id",
        "2607.12345",
        "--force-arxiv-id",
        "2607.54321v2",
        "--dry-run",
        data_dir=tmp_path,
    )
    after = datetime.now(UTC)

    assert result.exit_code == 0
    assert harness.constructor_kwargs["arxiv"] == {
        "user_agent": "Research Bot/1.0 (research@example.test)",
        "request_delay_seconds": config.arxiv.request_delay_seconds,
        "timeout_seconds": config.arxiv.timeout_seconds,
        "retries": config.arxiv.retries,
        "retry_wait_seconds": config.arxiv.retry_wait_seconds,
        "use_oai_for_recent": config.arxiv.use_oai_for_recent,
    }
    assert harness.constructor_kwargs["analysis"] == {
        "api_key": SECRET,
        "model": "deepseek-v4-flash",
        "max_output_tokens": config.analysis.max_output_tokens,
    }
    assert harness.constructor_kwargs["figure"] == {
        "user_agent": "Research Bot/1.0 (research@example.test)",
        "request_delay_seconds": config.arxiv.request_delay_seconds,
    }
    assert harness.run_kwargs["config"] is config
    assert harness.run_kwargs["data_dir"] == tmp_path
    assert harness.run_kwargs["fetcher"] is harness.instances["arxiv"]
    assert harness.run_kwargs["analysis_client"] is harness.instances["analysis"]
    assert harness.run_kwargs["figure_fetcher"] is harness.instances["figure"]
    assert harness.run_kwargs["prompt"] == "只返回一个 JSON 对象。"
    assert harness.run_kwargs["lookback_days"] == 5
    assert harness.run_kwargs["threshold"] == 9
    assert harness.run_kwargs["force_ids"] == ["2607.12345", "2607.54321v2"]
    assert harness.run_kwargs["dry_run"] is True
    assert before <= harness.run_kwargs["now"] <= after
    assert harness.run_kwargs["now"].tzinfo is UTC


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--lookback-days", "0"),
        ("--lookback-days", "32"),
        ("--threshold", "0"),
        ("--threshold", "11"),
    ],
)
def test_numeric_options_are_bounded_before_side_effects(
    monkeypatch: pytest.MonkeyPatch,
    config: AppConfig,
    prompt_path: Path,
    option: str,
    value: str,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", SECRET)
    harness = Harness(monkeypatch, config)

    result = invoke_daily(prompt_path, option, value)

    assert result.exit_code != 0
    assert harness.config_calls == []
    assert harness.events == []


def test_success_writes_one_stable_unicode_json_line(
    monkeypatch: pytest.MonkeyPatch,
    config: AppConfig,
    prompt_path: Path,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", SECRET)
    report = RunReport(
        stats=RunStats(fetched=3, published=1, error_categories={"中文错误": 1}),
        published=(make_record(),),
        dry_run=False,
    )
    Harness(monkeypatch, config, report=report)

    result = invoke_daily(prompt_path)

    assert result.exit_code == 0
    assert result.stdout.count("\n") == 1
    assert "\\u" not in result.stdout
    expected = {
        "dry_run": False,
        "stats": {
            "fetched": 3,
            "prefiltered": 0,
            "cache_hits": 0,
            "figure_cache_hits": 0,
            "figure_requests": 0,
            "figure_available": 0,
            "figure_unavailable": 0,
            "figure_failed": 0,
            "model_calls": 0,
            "published": 1,
            "failed": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "error_categories": {"中文错误": 1},
        },
        "published_ids": ["2607.12345"],
    }
    assert json.loads(result.stdout) == expected
    assert result.stdout == (
        json.dumps(
            expected,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    assert SECRET not in result.stdout
    assert SECRET not in result.stderr


def test_persisted_daily_run_synchronizes_figures_after_clients_close(
    monkeypatch: pytest.MonkeyPatch,
    config: AppConfig,
    prompt_path: Path,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", SECRET)
    report = RunReport(
        stats=RunStats(published=1),
        published=(make_record(),),
        dry_run=False,
    )
    harness = Harness(monkeypatch, config, report=report)
    public_dir = tmp_path / "public"

    result = invoke_daily(
        prompt_path,
        "--public-dir",
        str(public_dir),
        data_dir=tmp_path,
    )

    assert result.exit_code == 0
    assert harness.constructor_kwargs["asset"]["public_dir"] == public_dir
    assert (
        harness.constructor_kwargs["asset"]["user_agent"]
        == cli_module.DEFAULT_USER_AGENT
    )
    shared_client = harness.constructor_kwargs["asset"]["client"]
    assert harness.constructor_kwargs["source"]["client"] is shared_client
    assert harness.constructor_kwargs["pdf"]["client"] is shared_client
    assert harness.sync_kwargs == {
        "data_dir": tmp_path,
        "store": harness.instances["asset"],
        "recovery": harness.instances["recovery"],
        "now": harness.sync_kwargs["now"],
    }
    assert harness.events.index("close:figure") < harness.events.index(
        "construct:asset"
    )
    assert harness.sync_kwargs["now"].tzinfo is UTC
    assert harness.events[-14:] == [
        "construct:asset",
        "enter:asset",
        "construct:figure",
        "enter:figure",
        "construct:source",
        "enter:source",
        "construct:pdf",
        "enter:pdf",
        "construct:recovery",
        "sync",
        "close:pdf",
        "close:source",
        "close:figure",
        "close:asset",
    ]


def test_dry_run_never_constructs_or_synchronizes_figure_store(
    monkeypatch: pytest.MonkeyPatch,
    config: AppConfig,
    prompt_path: Path,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", SECRET)
    harness = Harness(monkeypatch, config)

    result = invoke_daily(prompt_path, "--dry-run")

    assert result.exit_code == 0
    assert "asset" not in harness.constructor_kwargs
    assert harness.sync_kwargs == {}
    assert "sync" not in harness.events


@pytest.mark.parametrize(
    ("failure_stage", "expected_closes"),
    [
        ("analysis_construct", ["close:arxiv"]),
        ("analysis_enter", ["close:arxiv"]),
        ("figure_construct", ["close:analysis", "close:arxiv"]),
        ("figure_enter", ["close:analysis", "close:arxiv"]),
        ("run", ["close:figure", "close:analysis", "close:arxiv"]),
    ],
)
def test_failures_close_every_previously_opened_client_in_reverse_order(
    monkeypatch: pytest.MonkeyPatch,
    config: AppConfig,
    prompt_path: Path,
    failure_stage: str,
    expected_closes: list[str],
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", SECRET)
    harness = Harness(monkeypatch, config, failure_stage=failure_stage)

    result = invoke_daily(prompt_path)

    assert result.exit_code != 0
    assert [event for event in harness.events if event.startswith("close:")] == expected_closes
    assert all(
        instance.close_calls == 1 for instance in harness.instances.values() if instance.close_calls
    )
    assert '"dry_run"' not in result.stdout
    assert SECRET not in result.stdout
    assert SECRET not in result.stderr
    assert SECRET not in str(result.exception)
    if failure_stage == "run":
        assert type(result.exception) is RuntimeError
        assert str(result.exception) == "pipeline failed"


def test_success_closes_all_clients_once_in_reverse_order(
    monkeypatch: pytest.MonkeyPatch,
    config: AppConfig,
    prompt_path: Path,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", SECRET)
    harness = Harness(monkeypatch, config)

    result = invoke_daily(prompt_path)

    assert result.exit_code == 0
    assert harness.events == [
        "construct:arxiv",
        "enter:arxiv",
        "construct:analysis",
        "enter:analysis",
        "construct:figure",
        "enter:figure",
        "run",
        "close:figure",
        "close:analysis",
        "close:arxiv",
    ]
    assert [event for event in harness.events if event.startswith("close:")] == [
        "close:figure",
        "close:analysis",
        "close:arxiv",
    ]
    assert all(instance.close_calls == 1 for instance in harness.instances.values())


def test_unsafe_user_agent_is_forwarded_for_client_fail_fast(
    monkeypatch: pytest.MonkeyPatch,
    config: AppConfig,
    prompt_path: Path,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", SECRET)
    monkeypatch.setenv("ARXIV_USER_AGENT", " unsafe\r\nagent ")
    harness = Harness(monkeypatch, config)

    result = invoke_daily(prompt_path)

    assert result.exit_code == 0
    assert harness.constructor_kwargs["arxiv"]["user_agent"] == " unsafe\r\nagent "
    assert harness.constructor_kwargs["figure"]["user_agent"] == " unsafe\r\nagent "
