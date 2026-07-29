import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
WEB_PACKAGE = yaml.safe_load((ROOT / "web" / "package.json").read_text(encoding="utf-8"))

PINNED_ACTIONS = {
    "actions/checkout": (
        "3d3c42e5aac5ba805825da76410c181273ba90b1",
        "v7.0.1",
    ),
    "actions/deploy-pages": (
        "cd2ce8fcbc39b97be8ca5fce6e763baed58fa128",
        "v5.0.0",
    ),
    "actions/setup-node": (
        "820762786026740c76f36085b0efc47a31fe5020",
        "v7.0.0",
    ),
    "astral-sh/setup-uv": (
        "c771a70e6277c0a99b617c7a806ffedaca235ff9",
        "v9.0.0",
    ),
    "pnpm/action-setup": (
        "0ebf47130e4866e96fce0953f49152a61190b271",
        "v6.0.9",
    ),
    "withastro/action": (
        "e84f40bd8d2caa9e768ec82ad30dd81f0b280853",
        "v6.1.2",
    ),
}


def workflow_source(name: str) -> str:
    return (WORKFLOW_DIR / name).read_text(encoding="utf-8")


def workflow(name: str) -> dict[str, Any]:
    loaded = yaml.load(workflow_source(name), Loader=yaml.BaseLoader)
    assert isinstance(loaded, dict)
    return loaded


def steps_for(payload: dict[str, Any], job_name: str) -> list[dict[str, Any]]:
    steps = payload["jobs"][job_name]["steps"]
    assert isinstance(steps, list)
    return steps


def step_named(payload: dict[str, Any], job_name: str, name: str) -> dict[str, Any]:
    return next(step for step in steps_for(payload, job_name) if step.get("name") == name)


def test_all_workflow_actions_are_full_sha_pinned_with_release_comments() -> None:
    seen: set[str] = set()
    for path in sorted(WORKFLOW_DIR.glob("*.yml")):
        source = path.read_text(encoding="utf-8")
        uses_lines = re.findall(r"^\s*uses:\s*([^\s#]+)(?:\s+#\s*(\S+))?\s*$", source, re.MULTILINE)
        assert uses_lines, f"{path.name} has no actions"
        for action_ref, comment in uses_lines:
            action, separator, sha = action_ref.partition("@")
            assert separator == "@"
            assert re.fullmatch(r"[0-9a-f]{40}", sha), action_ref
            assert action in PINNED_ACTIONS, action
            expected_sha, expected_tag = PINNED_ACTIONS[action]
            assert (sha, comment) == (expected_sha, expected_tag)
            seen.add(action)
    assert seen == set(PINNED_ACTIONS)


def test_ci_runs_frozen_python_and_complete_fixture_web_gates() -> None:
    payload = workflow("ci.yml")
    source = workflow_source("ci.yml")
    assert payload["on"]["push"]["branches"] == ["main"]
    assert "pull_request" in payload["on"]
    assert payload["permissions"] == {"contents": "read"}
    assert set(payload["jobs"]) == {"python", "web"}
    assert "uv sync --frozen" in source
    assert "uv run ruff check src tests" in source
    assert "uv run mypy" in source
    assert "uv run pytest --cov=vla_wam_daily --cov-report=term-missing" in source
    assert "pnpm install --frozen-lockfile" in source
    assert "pnpm exec playwright install --with-deps chromium" in source
    assert "pnpm test:e2e" in source
    assert "VLA_WAM_DATA_DIR: ../tests/fixtures/data" in source
    for verifier in ("figure", "information", "search"):
        assert f"pnpm verify:{verifier}-build" in source

    web_steps = steps_for(payload, "web")
    pnpm_step = next(step for step in web_steps if str(step.get("uses", "")).startswith("pnpm/"))
    node_step = next(
        step
        for step in web_steps
        if str(step.get("uses", "")).startswith("actions/setup-node")
    )
    package_manager = WEB_PACKAGE["packageManager"]
    assert package_manager == "pnpm@11.9.0"
    assert pnpm_step["with"]["version"] == package_manager.removeprefix("pnpm@")
    assert pnpm_step["with"]["package_json_file"] == "web/package.json"
    assert node_step["with"]["node-version"] == "24"


def test_pages_builds_existing_data_only_and_deploys_with_minimal_permissions() -> None:
    payload = workflow("pages.yml")
    source = workflow_source("pages.yml")
    assert payload["on"]["push"]["branches"] == ["main"]
    assert "workflow_dispatch" in payload["on"]
    assert payload["permissions"] == {}
    assert payload["concurrency"] == {
        "group": "pages",
        "cancel-in-progress": "false",
    }
    assert "DEEPSEEK" not in source
    assert "vla-wam-daily daily" not in source

    build = payload["jobs"]["build"]
    deploy = payload["jobs"]["deploy"]
    assert build["permissions"] == {"contents": "read"}
    assert deploy["permissions"] == {"pages": "write", "id-token": "write"}
    assert deploy["environment"]["name"] == "github-pages"
    assert deploy["environment"]["url"] == "${{ steps.deployment.outputs.page_url }}"
    astro_step = next(
        step
        for step in build["steps"]
        if str(step.get("uses", "")).startswith("withastro/action")
    )
    assert astro_step["with"] == {
        "path": "web",
        "node-version": "24",
        "package-manager": WEB_PACKAGE["packageManager"],
    }


def test_daily_schedule_dispatch_defaults_and_permissions_are_bounded() -> None:
    payload = workflow("daily.yml")
    dispatch = payload["on"]["workflow_dispatch"]["inputs"]
    assert payload["on"]["schedule"] == [{"cron": "30 2 * * *"}]
    assert dispatch["lookback_days"]["default"] == "3"
    assert dispatch["profile"]["default"] == "quality"
    assert dispatch["profile"]["options"] == ["quality", "economy"]
    assert dispatch["threshold"]["default"] == "6"
    assert dispatch["dry_run"]["default"] == "false"
    assert dispatch["force_arxiv_id"]["required"] == "false"
    assert payload["permissions"] == {}
    assert payload["jobs"]["update"]["permissions"] == {"contents": "write"}
    assert payload["jobs"]["build"]["permissions"] == {"contents": "read"}
    assert payload["jobs"]["deploy"]["permissions"] == {
        "pages": "write",
        "id-token": "write",
    }


def test_daily_inputs_are_validated_via_env_and_never_interpolated_in_shell() -> None:
    payload = workflow("daily.yml")
    source = workflow_source("daily.yml")
    for job in payload["jobs"].values():
        for step in job.get("steps", []):
            run = step.get("run", "")
            assert "${{ inputs." not in run
            assert "${{ github.event.inputs." not in run

    options = step_named(payload, "update", "Validate options")
    assert options["env"] == {
        "INPUT_DRY_RUN": "${{ inputs.dry_run }}",
        "INPUT_FORCE_ARXIV_ID": "${{ inputs.force_arxiv_id }}",
        "INPUT_LOOKBACK_DAYS": "${{ inputs.lookback_days }}",
        "INPUT_PROFILE": "${{ inputs.profile }}",
        "INPUT_THRESHOLD": "${{ inputs.threshold }}",
    }
    assert "force_arxiv_id=$force_arxiv_id" in options["run"]
    assert "lookback_days=$lookback_days" in options["run"]
    assert "threshold=$threshold" in options["run"]
    assert "quality|economy" in options["run"]
    assert "2607.12345v2" in options["run"]

    pipeline = step_named(payload, "update", "Run pipeline and validate report")
    assert pipeline["env"]["DEEPSEEK_API_KEY"] == "${{ secrets.DEEPSEEK_API_KEY }}"
    assert pipeline["env"]["DEEPSEEK_MODEL"] == "${{ vars.DEEPSEEK_MODEL }}"
    assert pipeline["env"]["RUN_FORCE_ARXIV_ID"] == "${{ steps.options.outputs.force_arxiv_id }}"
    assert 'args=(' in pipeline["run"]
    assert 'args+=(--force-arxiv-id "$RUN_FORCE_ARXIV_ID")' in pipeline["run"]
    assert 'uv run vla-wam-daily "${args[@]}"' in pipeline["run"]
    assert source.count("${{ secrets.DEEPSEEK_API_KEY }}") == 1


def test_daily_dry_run_cannot_commit_or_deploy_and_non_dry_run_is_data_only() -> None:
    payload = workflow("daily.yml")
    source = workflow_source("daily.yml")
    update = payload["jobs"]["update"]
    assert update["outputs"]["dry_run"] == "${{ steps.options.outputs.dry_run }}"
    for name in ("Validate persisted data", "Commit and push generated data"):
        assert step_named(payload, "update", name)["if"] == (
            "steps.options.outputs.dry_run == 'false'"
        )
    assert payload["jobs"]["build"]["if"] == "needs.update.outputs.dry_run == 'false'"
    assert payload["jobs"]["deploy"]["if"] == "needs.update.outputs.dry_run == 'false'"
    assert "git add -- data" in source
    assert "git diff --cached --name-only" in source
    assert "git add ." not in source
    assert "git add -A" not in source
    assert "git rebase origin/main" in source
    assert "git push origin HEAD:main" in source


def test_daily_non_dry_run_builds_and_deploys_latest_main_in_the_same_run() -> None:
    payload = workflow("daily.yml")
    build = payload["jobs"]["build"]
    deploy = payload["jobs"]["deploy"]
    assert build["needs"] == "update"
    checkout = next(
        step
        for step in build["steps"]
        if str(step.get("uses", "")).startswith("actions/checkout")
    )
    assert checkout["with"]["ref"] == "main"
    assert any(
        str(step.get("uses", "")).startswith("withastro/action")
        for step in build["steps"]
    )
    assert deploy["needs"] == ["update", "build"]
    assert deploy["concurrency"] == {
        "group": "pages",
        "cancel-in-progress": "false",
    }
    assert any(
        str(step.get("uses", "")).startswith("actions/deploy-pages")
        for step in deploy["steps"]
    )
