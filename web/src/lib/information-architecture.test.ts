import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const sourceRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");

async function source(relativePath: string): Promise<string> {
  return readFile(resolve(sourceRoot, relativePath), "utf8").catch(() => "");
}

describe("topic and archive route contracts", () => {
  it("always builds all central topic routes with PaperExplorer", async () => {
    const page = await source("pages/topics/[topic].astro");

    expect(page).toContain("TOPIC_ROUTES");
    expect(page).toContain("getStaticPaths");
    expect(page).toContain("TOPIC_ROUTES.map");
    expect(page).toContain("<PaperExplorer");
    expect(page).toContain("description={route.description}");
  });

  it("builds a count index and deterministic compact monthly archives", async () => {
    const index = await source("pages/archive/index.astro");
    const month = await source("pages/archive/[month].astro");

    expect(index).toContain("groupArchiveMonths");
    expect(index).toContain("href={`${base}archive/${month.month}/`}");
    expect(month).toContain("groupArchiveMonths");
    expect(month).toContain("groupArchiveDays");
    expect(month).toMatch(/<PaperCard[^>]*compact/s);
  });
});

describe("weekly, methodology, RSS, and 404 route contracts", () => {
  it("anchors the weekly window to latest.generated_at and uses compact cards", async () => {
    const page = await source("pages/weekly.astro");

    expect(page).toContain("loadLatestDataFile");
    expect(page).toContain("new Date(latest.generated_at)");
    expect(page).toContain("formatBeijingTimestamp");
    expect(page).toContain("selectWeeklyTop");
    expect(page).toMatch(/<PaperCard[^>]*compact/s);
    expect(page).not.toContain(".slice(0, 10)");
    expect(page).not.toContain("selectWeeklyTop(await loadArchive())");
  });

  it("distinguishes observed provenance from configurable defaults", async () => {
    const page = await source("pages/methodology.astro");

    expect(page).toContain("latest.generated_at");
    expect(page).toContain("formatBeijingTimestamp");
    expect(page).toContain("paper.provenance.model");
    expect(page).toContain("暂无已发布论文，无法从当前数据确认模型或 Prompt");
    expect(page).toContain("quality profile 的默认模型为");
    expect(page).toContain("deepseek-v4-pro");
    expect(page).toContain("CLI --threshold");
    expect(page).toContain("当前构建数据不携带实际运行阈值");
    expect(page).toContain("--config-path");
    expect(page).not.toContain("当前发布阈值 6");
    expect(page).not.toContain("1–5：低于当前发布阈值");
    expect(page).not.toContain(".slice(0, 10)");
  });

  it("discloses the non-guessing pipeline methodology", async () => {
    const page = await source("pages/methodology.astro");

    expect(page).toContain("deepseek-v4-pro");
    expect(page).toContain("cs.RO");
    expect(page).toContain("两级筛选");
    expect(page).toContain("发布阈值");
    expect(page).toContain("标题与摘要");
    expect(page).toContain("不会猜测");
    expect(page).toContain("arXiv");
    expect(page).toContain("缓存");
    expect(page).toContain("北京时间");
    expect(page).toContain("工作流");
    expect(page).toContain("反馈");
  });

  it("uses the official RSS helper with absolute item URLs and language metadata", async () => {
    const page = await source("pages/rss.xml.ts");

    expect(page).toContain('from "@astrojs/rss"');
    expect(page).toContain("createRssItems");
    expect(page).toContain("new URL(base, context.site)");
    expect(page).toContain("<language>zh-CN</language>");
  });

  it("keeps the 404 home link inside the configured base", async () => {
    const page = await source("pages/404.astro");

    expect(page).toContain("import.meta.env.BASE_URL");
    expect(page).toContain("href={base}");
  });
});

describe("global navigation contract", () => {
  it("links every information route and reuses the central topic mapping", async () => {
    const header = await source("components/Header.astro");
    const layout = await source("layouts/BaseLayout.astro");
    const css = await source("styles/global.css");

    expect(header).toContain("TOPIC_ROUTES");
    for (const target of [
      "weekly/",
      "archive/",
      "search/",
      "rss.xml",
      "methodology/",
    ]) {
      expect(header).toContain(target);
    }
    expect(layout).toContain('type="application/rss+xml"');
    expect(css).toMatch(/\.site-header nav\s*\{[\s\S]*flex-wrap:\s*wrap/s);
  });

  it("describes the local Figure cache and original rights in the footer", async () => {
    const layout = await source("layouts/BaseLayout.astro");

    expect(layout).toContain("论文 Figure 优先从本站缓存加载");
    expect(layout).toContain("版权与许可仍以原论文为准");
    expect(layout).not.toContain("论文图片直接从 arXiv 加载");
  });

  it("configures Pagefind to index paper detail HTML only", async () => {
    const packageJson = JSON.parse(await source("../package.json"));
    const buildScript = await source("../scripts/build-pagefind.mjs");

    expect(packageJson.scripts.build).toContain(
      "node scripts/build-pagefind.mjs",
    );
    expect(buildScript).toContain('"papers/**/*.html"');
    expect(buildScript).toContain("pagefind-entry.json");
    expect(buildScript).toContain("results: []");
    expect(buildScript).toContain('import.meta.resolve("pagefind")');
    expect(buildScript).toMatch(/spawn\(\s*process\.execPath/);
    expect(buildScript).not.toContain('spawn("pagefind"');
  });
});
