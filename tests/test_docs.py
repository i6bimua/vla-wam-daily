import os
import re
import subprocess
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
MAIN_DESIGN = (ROOT / "docs/superpowers/specs/2026-07-27-vla-wam-daily-design.md").read_text(
    encoding="utf-8"
)
FIGURE_DESIGN = (
    ROOT / "docs/superpowers/specs/2026-07-29-paper-figure-display-design.md"
).read_text(encoding="utf-8")
PAPER_IMAGE_EXTENSIONS = {
    ".avif",
    ".bmp",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".png",
    ".svg",
    ".tif",
    ".tiff",
    ".webp",
}


def tracked_files() -> tuple[Path, ...]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
    )
    return tuple(
        ROOT / os.fsdecode(raw_path) for raw_path in result.stdout.split(b"\0") if raw_path
    )


def test_readme_documents_supported_models_limits_and_information_features() -> None:
    for heading in (
        "## 功能",
        "## 模型与分析边界",
        "## 本地开发",
        "## 配置",
        "## GitHub Pages 与每日更新",
        "## 故障排查",
        "## 局限",
    ):
        assert heading in README
    for text in (
        "deepseek-v4-pro",
        "deepseek-v4-flash",
        "quality",
        "economy",
        "60",
        "30%",
        "Pagefind",
        "RSS",
        "Weekly Top 5",
    ):
        assert text in README
    assert "DataFile" in README
    assert "不保存本次实际运行阈值" in README


def test_readme_has_reproducible_python_web_and_dry_run_commands() -> None:
    for command in (
        "uv python install 3.13",
        "uv sync --frozen",
        "uv run pytest",
        "uv run ruff check src tests",
        "uv run mypy",
        "uv run vla-wam-daily daily --dry-run",
        "npm install --global pnpm@11.9.0",
        "pnpm install --frozen-lockfile",
        "pnpm test",
        "pnpm format:check",
        "pnpm build",
        "pnpm preview --host 127.0.0.1",
        "pnpm exec playwright install chromium",
        "pnpm test:e2e",
    ):
        assert command in README
    assert "Python 3.13" in README
    assert "Node.js 24" in README
    assert "read -rsp" in README
    assert "unset DEEPSEEK_API_KEY" in README


def test_readme_orders_e2e_setup_and_keeps_manual_preview_separate() -> None:
    install_browser = README.index("pnpm exec playwright install chromium")
    fixture_build = README.index(
        "BASE_PATH=/ VLA_WAM_DATA_DIR=../tests/fixtures/data "
        "VLA_WAM_PUBLIC_DIR=../tests/fixtures/public pnpm build"
    )
    strict_e2e = README.index("pnpm test:e2e")
    manual_preview = README.index("pnpm preview --host 127.0.0.1")

    assert install_browser < fixture_build < strict_e2e < manual_preview
    assert "另一个终端" in README
    assert "前台进程" in README
    assert "Ctrl-C" in README
    assert "strict E2E 会独占默认端口 `4321`" in README
    assert "不要同时运行手动 preview" in README


def test_readme_documents_actual_cache_paths() -> None:
    assert "`data/cache/analyses.json`" in README
    assert "`data/cache/figures.json`" in README
    assert "`data/cache.json`" not in README
    assert "`data/figures.json`" not in README


def test_readme_documents_base_path_derivation_and_analysis_cache_exception() -> None:
    assert "Astro 根据 `GITHUB_REPOSITORY` 推导" in README
    assert "显式 `BASE_PATH` 仍可覆盖" in README
    assert "常规每日运行" in README
    assert "未使用 `--force-arxiv-id` 强制重分析" in README


def test_readme_documents_retry_and_invalid_ai_output_behavior() -> None:
    assert "arXiv 和 DeepSeek 对超时、429 和瞬态错误执行有限指数退避" in README
    assert "无效或不符合 Schema 的 AI 输出绝不发布" in README
    assert "计入失败" in README
    assert "下次运行重试" in README


def test_readme_explains_pages_schedule_secrets_sources_and_troubleshooting() -> None:
    for text in (
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_MODEL",
        "GitHub Actions",
        "github-pages",
        "北京时间 07:00",
        "Asia/Shanghai",
        "workflow_dispatch",
        "dry-run",
        "arXiv API",
        "独立实现",
        "monologg/nlp-arxiv-daily",
        "dw-dengwei/daily-arXiv-ai-enhanced",
        "Vincentqyw/cv-arxiv-daily",
    ):
        assert text in README
    assert "Secret" in README
    assert "论文许可证" in README


def test_readme_documents_permanent_records_local_figures_and_fallbacks() -> None:
    assert "## Fig. 1 / Fig. 2" in README
    for text in (
        "默认回看 3 天",
        "抓取窗口，不是保留期限",
        "永久保留",
        "arXiv HTML",
        "figure",
        "figcaption",
        "caption",
        "多 panel",
        "`web/public/figures/{arxiv_id}/v{version}/`",
        "优先从本站缓存",
        "arXiv 原图",
        "后续每日运行重试",
        "Blob",
        "CORS",
        "html_unavailable",
        "not_found",
        "fetch_failed",
        "PDF",
        "版权",
        "论文许可证",
    ):
        assert text in README


def test_readme_documents_multistage_figure_recovery_and_backfill() -> None:
    for text in (
        "arXiv HTML → arXiv 源码包 → arXiv PDF 自动裁剪",
        "`web/public/figures/{arxiv_id}/v{version}/`",
        "uv run vla-wam-daily sync-figures",
        "PDF 自动裁剪可能因置信不足而明确降级",
        "Figure 2 永远不会冒充 Figure 1",
        "24 小时后重试",
        "pdfplumber",
        "pypdfium2",
    ):
        assert text in README


def test_readme_documents_inference_efficiency_topics_and_uncached_limit() -> None:
    for text in (
        "cs.CL",
        "Speculative Decoding",
        "Quantization",
        "analysis-v2.md",
        "未缓存",
    ):
        assert text in README


def test_license_is_complete_mit_text() -> None:
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert license_text.startswith("MIT License\n")
    assert "Copyright (c) 2026 VLA/WAM Daily contributors" in license_text
    assert "Permission is hereby granted, free of charge" in license_text
    assert 'THE SOFTWARE IS PROVIDED "AS IS"' in license_text


def test_dependabot_checks_uv_web_and_actions_monthly() -> None:
    payload = yaml.load(
        (ROOT / ".github/dependabot.yml").read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    assert isinstance(payload, dict)
    updates: list[dict[str, Any]] = payload["updates"]
    assert {
        (update["package-ecosystem"], update["directory"], update["schedule"]["interval"])
        for update in updates
    } == {
        ("uv", "/", "monthly"),
        ("npm", "/web", "monthly"),
        ("github-actions", "/", "monthly"),
    }


def test_designs_keep_reviewed_status_and_document_figure_pipeline_metrics() -> None:
    for design in (MAIN_DESIGN, FIGURE_DESIGN):
        assert "状态：已复核通过" in design
        assert "状态：已实现" not in design
        assert "状态：已验收" not in design
    for metric in (
        "figure_cache_hits",
        "figure_requests",
        "figure_available",
        "figure_unavailable",
        "figure_failed",
    ):
        assert metric in MAIN_DESIGN
    assert "DataFile 不持久化本次实际运行阈值" in MAIN_DESIGN
    assert "不能据此宣称当前运行阈值" in MAIN_DESIGN
    for text in (
        "相关性评分通过发布阈值后",
        "URL、caption、状态和时间",
        "不保存图片字节",
        "多面板",
        "CORS",
        "论文页面标注的许可证",
    ):
        assert text in FIGURE_DESIGN


def test_repository_tracked_files_contain_no_secret_like_bytes() -> None:
    secret_patterns = (
        re.compile(rb"sk-[A-Za-z0-9_-]{12,}"),
        re.compile(rb"Bearer [A-Za-z0-9_-]{12,}"),
    )
    offending_paths = [
        path.relative_to(ROOT).as_posix()
        for path in tracked_files()
        if any(pattern.search(path.read_bytes()) for pattern in secret_patterns)
    ]
    assert offending_paths == [], f"tracked files contain potential secret bytes: {offending_paths}"


def test_tracked_paper_images_stay_inside_mirrors_or_test_fixtures() -> None:
    assert {".gif", ".bmp", ".tif", ".tiff"}.issubset(PAPER_IMAGE_EXTENSIONS)
    images = [
        path.relative_to(ROOT).as_posix()
        for path in tracked_files()
        if path.suffix.lower() in PAPER_IMAGE_EXTENSIONS
    ]
    prefixes = ("web/public/figures/", "tests/fixtures/public/figures/")
    assert any(path.startswith(prefixes[0]) for path in images)
    assert any(path.startswith(prefixes[1]) for path in images)
    assert all(path.startswith(prefixes) for path in images)
    pattern = re.compile(
        r"^(?:web/public|tests/fixtures/public)/figures/"
        r"\d{4}\.\d{4,5}/v[1-9]\d*/"
        r"fig[12]-panel[1-9]\d*\.(?:png|jpg|webp|gif|svg)$"
    )
    assert all(pattern.fullmatch(path) for path in images)
