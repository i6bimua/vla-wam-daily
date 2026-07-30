import json
import os
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import httpx
import typer
from pydantic import ValidationError
from yaml import YAMLError

from vla_wam_daily.arxiv_client import ArxivClient
from vla_wam_daily.config import load_config
from vla_wam_daily.deepseek_client import DeepSeekClient
from vla_wam_daily.figure_pdf import ArxivPdfFigureExtractor
from vla_wam_daily.figure_recovery import FigureRecoveryService
from vla_wam_daily.figure_source import ArxivSourceFigureExtractor
from vla_wam_daily.figure_store import ArxivFigureStore
from vla_wam_daily.figure_sync import (
    FigureSyncReport,
    synchronize_figure_assets,
)
from vla_wam_daily.figures import ArxivFigureClient
from vla_wam_daily.pipeline import normalize_force_ids, run_daily

DEFAULT_CONFIG_PATH = Path("config/topics.yaml")
DEFAULT_DATA_DIR = Path("data")
DEFAULT_PUBLIC_DIR = Path("web/public")
DEFAULT_PROMPT_PATH = Path("prompts/analysis-v1.md")
DEFAULT_USER_AGENT = "VLA-WAM-Daily/0.1 (https://github.com/vla-wam-daily/vla-wam-daily)"
DEFAULT_FIGURE_REQUEST_DELAY_SECONDS = 3.0

app = typer.Typer(no_args_is_help=True)


def _require_input_file(path: Path, *, param_hint: str, label: str) -> None:
    try:
        is_file = path.is_file()
    except OSError:
        is_file = False
    if not is_file:
        raise typer.BadParameter(
            f"{label} must be an existing readable file",
            param_hint=param_hint,
        )


def _require_generated_data(data_dir: Path) -> None:
    try:
        has_latest = (data_dir / "latest.json").is_file()
    except OSError:
        has_latest = False
    if not has_latest:
        raise typer.BadParameter(
            "data directory must contain a readable latest.json",
            param_hint="--data-dir",
        )


def _run_figure_sync(
    *,
    data_dir: Path,
    public_dir: Path,
    user_agent: str,
) -> FigureSyncReport:
    with ExitStack() as stack:
        client = stack.enter_context(httpx.Client())
        store = stack.enter_context(
            ArxivFigureStore(
                public_dir=public_dir,
                user_agent=user_agent,
                client=client,
            )
        )
        html_fetcher = stack.enter_context(
            ArxivFigureClient(
                user_agent=user_agent,
                request_delay_seconds=DEFAULT_FIGURE_REQUEST_DELAY_SECONDS,
                client=client,
            )
        )
        source_extractor = stack.enter_context(
            ArxivSourceFigureExtractor(
                user_agent=user_agent,
                client=client,
            )
        )
        pdf_extractor = stack.enter_context(
            ArxivPdfFigureExtractor(
                user_agent=user_agent,
                client=client,
            )
        )
        recovery = FigureRecoveryService(
            html_fetcher=html_fetcher,
            source_extractor=source_extractor,
            pdf_extractor=pdf_extractor,
            store=store,
        )
        return synchronize_figure_assets(
            data_dir=data_dir,
            store=store,
            recovery=recovery,
            now=datetime.now(UTC),
        )


@app.callback()
def main() -> None:
    """VLA/WAM Daily command-line interface."""


@app.command("sync-figures")
def sync_figures(
    data_dir: Annotated[
        Path,
        typer.Option(help="Generated data directory."),
    ] = DEFAULT_DATA_DIR,
    public_dir: Annotated[
        Path,
        typer.Option(help="Static site public directory."),
    ] = DEFAULT_PUBLIC_DIR,
) -> None:
    """Mirror Figure 1 and Figure 2 for every archived paper."""
    _require_generated_data(data_dir)
    report = _run_figure_sync(
        data_dir=data_dir,
        public_dir=public_dir,
        user_agent=os.getenv("ARXIV_USER_AGENT", DEFAULT_USER_AGENT),
    )
    typer.echo(report.model_dump_json())


@app.command()
def daily(
    profile: Annotated[
        str,
        typer.Option(help="Configured DeepSeek model profile."),
    ] = "quality",
    lookback_days: Annotated[
        int | None,
        typer.Option(min=1, max=31, help="Days of recent arXiv papers to fetch."),
    ] = None,
    threshold: Annotated[
        int | None,
        typer.Option(min=1, max=10, help="Minimum relevance score to publish."),
    ] = None,
    force_arxiv_id: Annotated[
        list[str] | None,
        typer.Option(help="Force one arXiv ID; repeat for multiple papers."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option(help="Run without writing generated data."),
    ] = False,
    config_path: Annotated[
        Path,
        typer.Option(help="Topic configuration YAML path."),
    ] = DEFAULT_CONFIG_PATH,
    data_dir: Annotated[
        Path,
        typer.Option(help="Generated data directory."),
    ] = DEFAULT_DATA_DIR,
    public_dir: Annotated[
        Path,
        typer.Option(help="Static site public directory."),
    ] = DEFAULT_PUBLIC_DIR,
    prompt_path: Annotated[
        Path,
        typer.Option(help="Versioned analysis prompt path."),
    ] = DEFAULT_PROMPT_PATH,
) -> None:
    """Fetch, analyze, enrich, and publish the daily paper set."""
    _require_input_file(
        config_path,
        param_hint="--config-path",
        label="configuration",
    )
    _require_input_file(
        prompt_path,
        param_hint="--prompt-path",
        label="prompt",
    )
    try:
        config = load_config(config_path, prompt_dir=prompt_path.parent)
    except FileNotFoundError:
        try:
            config_still_exists = config_path.is_file()
        except OSError:
            config_still_exists = False
        if not config_still_exists:
            raise typer.BadParameter(
                "configuration must remain an existing readable file",
                param_hint="--config-path",
            ) from None
        raise typer.BadParameter(
            "the configured prompt version is unavailable",
            param_hint="--prompt-path",
        ) from None
    except (OSError, UnicodeError, YAMLError, ValidationError):
        raise typer.BadParameter(
            "configuration must be valid UTF-8 YAML matching the expected schema",
            param_hint="--config-path",
        ) from None

    expected_prompt_name = f"analysis-v{config.analysis.prompt_version}.md"
    if prompt_path.name != expected_prompt_name:
        raise typer.BadParameter(
            f"prompt path must select configured version {config.analysis.prompt_version!r}",
            param_hint="--prompt-path",
        )
    try:
        prompt = prompt_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise typer.BadParameter(
            "prompt must be a readable UTF-8 file",
            param_hint="--prompt-path",
        ) from None

    try:
        configured_model = config.analysis.model_for(profile)
    except ValueError as error:
        raise typer.BadParameter(str(error), param_hint="--profile") from None

    model_override = os.getenv("DEEPSEEK_MODEL")
    model = (
        model_override.strip()
        if model_override is not None and model_override.strip()
        else configured_model
    )

    try:
        normalized_force_ids = normalize_force_ids(force_arxiv_id or [])
    except (TypeError, ValueError):
        raise typer.BadParameter(
            "must contain modern arXiv IDs with optional positive version suffixes",
            param_hint="--force-arxiv-id",
        ) from None

    api_key = os.getenv("DEEPSEEK_API_KEY")
    if api_key is None or not api_key.strip():
        raise typer.BadParameter(
            "DEEPSEEK_API_KEY is required",
            param_hint="DEEPSEEK_API_KEY",
        )

    user_agent = os.getenv("ARXIV_USER_AGENT", DEFAULT_USER_AGENT)
    resolved_lookback = config.arxiv.lookback_days if lookback_days is None else lookback_days
    resolved_threshold = config.analysis.threshold if threshold is None else threshold

    with ExitStack() as stack:
        fetcher = stack.enter_context(
            ArxivClient(
                user_agent=user_agent,
                request_delay_seconds=config.arxiv.request_delay_seconds,
                timeout_seconds=config.arxiv.timeout_seconds,
                retries=config.arxiv.retries,
                retry_wait_seconds=config.arxiv.retry_wait_seconds,
                use_oai_for_recent=config.arxiv.use_oai_for_recent,
            )
        )
        analysis_client = stack.enter_context(
            DeepSeekClient(
                api_key=api_key,
                model=model,
                max_output_tokens=config.analysis.max_output_tokens,
            )
        )
        figure_fetcher = stack.enter_context(
            ArxivFigureClient(
                user_agent=user_agent,
                request_delay_seconds=config.arxiv.request_delay_seconds,
            )
        )
        report = run_daily(
            config=config,
            data_dir=data_dir,
            fetcher=fetcher,
            analysis_client=analysis_client,
            figure_fetcher=figure_fetcher,
            prompt=prompt,
            lookback_days=resolved_lookback,
            threshold=resolved_threshold,
            force_ids=normalized_force_ids,
            dry_run=dry_run,
            now=datetime.now(UTC),
        )

    if not report.dry_run:
        _run_figure_sync(
            data_dir=data_dir,
            public_dir=public_dir,
            user_agent=user_agent,
        )

    typer.echo(
        json.dumps(
            {
                "dry_run": report.dry_run,
                "stats": report.stats.model_dump(mode="json"),
                "published_ids": [paper.arxiv_id for paper in report.published],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
