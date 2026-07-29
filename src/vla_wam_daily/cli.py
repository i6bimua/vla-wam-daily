import json
import os
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from vla_wam_daily.arxiv_client import ArxivClient
from vla_wam_daily.config import load_config
from vla_wam_daily.deepseek_client import DeepSeekClient
from vla_wam_daily.figures import ArxivFigureClient
from vla_wam_daily.pipeline import run_daily

DEFAULT_CONFIG_PATH = Path("config/topics.yaml")
DEFAULT_DATA_DIR = Path("data")
DEFAULT_PROMPT_PATH = Path("prompts/analysis-v1.md")
DEFAULT_USER_AGENT = "VLA-WAM-Daily/0.1 (https://github.com/vla-wam-daily/vla-wam-daily)"

app = typer.Typer(no_args_is_help=True)


@app.callback()
def main() -> None:
    """VLA/WAM Daily command-line interface."""


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
    prompt_path: Annotated[
        Path,
        typer.Option(help="Versioned analysis prompt path."),
    ] = DEFAULT_PROMPT_PATH,
) -> None:
    """Fetch, analyze, enrich, and publish the daily paper set."""
    config = load_config(config_path, prompt_dir=prompt_path.parent)

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

    api_key = os.getenv("DEEPSEEK_API_KEY")
    if api_key is None or not api_key.strip():
        raise typer.BadParameter(
            "DEEPSEEK_API_KEY is required",
            param_hint="DEEPSEEK_API_KEY",
        )

    expected_prompt_name = f"analysis-v{config.analysis.prompt_version}.md"
    if prompt_path.name != expected_prompt_name:
        raise typer.BadParameter(
            f"prompt path must select configured version {config.analysis.prompt_version!r}",
            param_hint="--prompt-path",
        )
    prompt = prompt_path.read_text(encoding="utf-8")

    user_agent = os.getenv("ARXIV_USER_AGENT", DEFAULT_USER_AGENT)
    resolved_lookback = config.arxiv.lookback_days if lookback_days is None else lookback_days
    resolved_threshold = config.analysis.threshold if threshold is None else threshold

    with ExitStack() as stack:
        fetcher = stack.enter_context(
            ArxivClient(
                user_agent=user_agent,
                request_delay_seconds=config.arxiv.request_delay_seconds,
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
            force_ids=force_arxiv_id or [],
            dry_run=dry_run,
            now=datetime.now(UTC),
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
