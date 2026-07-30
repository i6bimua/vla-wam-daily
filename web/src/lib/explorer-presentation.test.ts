import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const sourceRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");

async function source(relativePath: string): Promise<string> {
  return readFile(resolve(sourceRoot, relativePath), "utf8").catch(() => "");
}

describe("PaperExplorer presentation contract", () => {
  it("is a semantic GET form with reset, live count, and a no-script notice", async () => {
    const component = await source("components/PaperExplorer.astro");

    expect(component).toMatch(/<form[^>]*data-filter-form[^>]*method="get"/s);
    expect(component).toContain('type="reset"');
    expect(component).toMatch(/data-result-count[^>]*aria-live="polite"/s);
    expect(component).toMatch(/<noscript>[\s\S]*筛选功能需要启用 JavaScript/s);
    expect(component).toContain("serializeFilterState");
    expect(component).toContain("history.replaceState");
    expect(component).toContain("location.hash");
    expect(component).toContain("event.preventDefault()");
  });

  it("embeds only a safely serialized minimal filter projection", async () => {
    const component = await source("components/PaperExplorer.astro");

    expect(component).toContain("createFilterPaper");
    expect(component).toContain("serializeJsonForHtml");
    expect(component).toContain('type="application/json"');
    expect(component).not.toContain("JSON.stringify(papers)");
    expect(component).not.toContain("figure_gallery");
  });
});

describe("home paper Figure preview contract", () => {
  it("renders Figure 1 before the expandable research notes", async () => {
    const card = await source("components/PaperCard.astro");
    const preview = await source("components/FigurePreview.astro");

    expect(card).toContain(
      'import FigurePreview from "./FigurePreview.astro";',
    );
    expect(card).toContain("<FigurePreview paper={paper} />");
    expect(card.indexOf("<FigurePreview")).toBeLessThan(
      card.indexOf('<details class="analysis"'),
    );
    expect(preview).toContain("figure.number === 1");
    expect(preview).toContain("figure.image_urls[0]");
    expect(preview).toContain("figure.cached_image_paths[0]");
    expect(preview).toContain("resolveFigurePanelSource");
    expect(preview).toContain("data-figure-preview");
    expect(preview).toContain("figure.caption");
    expect(preview).toContain("Fig. 1 暂不可用");
  });

  it("uses the approved smaller title and responsive lead layout", async () => {
    const css = await source("styles/global.css");

    expect(css).toMatch(
      /\.paper-card h2\s*\{[^}]*font-size:\s*clamp\(1\.35rem,\s*2\.4vw,\s*2rem\)/s,
    );
    expect(css).toMatch(
      /\.paper-card__lead\s*\{[^}]*display:\s*grid[^}]*grid-template-columns:\s*minmax\(0,\s*1\.15fr\)\s+minmax\(16rem,\s*0\.85fr\)/s,
    );
    expect(css).toMatch(
      /@media\s*\(max-width:\s*44rem\)[\s\S]*\.paper-card__lead\s*\{[^}]*grid-template-columns:\s*1fr/s,
    );
    expect(css).toMatch(
      /\.paper-card__figure-preview img\s*\{[^}]*object-fit:\s*contain/s,
    );
    expect(css).toMatch(
      /\.paper-card--compact\s+\.paper-card__lead\s*\{[^}]*grid-template-columns:\s*1fr/s,
    );
  });
});

describe("Pagefind search presentation contract", () => {
  it("loads the base-scoped index in guarded batches without unsafe rendering", async () => {
    const component = await source("components/SearchPanel.astro");

    expect(component).toContain("`${base}pagefind/pagefind.js`");
    expect(component).toContain("requestGeneration");
    expect(component).toMatch(/generation\s*!==\s*requestGeneration/);
    expect(component).toContain("loadPagefindResultBatch");
    expect(component).not.toContain("Promise.all(");
    expect(component).toContain("data-load-more");
    expect(component).toMatch(
      /data-load-more[\s\S]*type="button"[\s\S]*hidden[\s\S]*disabled/s,
    );
    expect(component).toContain("resolvePagefindResultHref");
    expect(component).toContain(".textContent");
    expect(component).not.toContain(".innerHTML");
  });

  it("has labelled controls and live loading, progress, empty, error, and no-script status", async () => {
    const component = await source("components/SearchPanel.astro");

    expect(component).toContain("data-search-form");
    expect(component).toMatch(/data-search-status[^>]*aria-live="polite"/s);
    expect(component).toContain("正在加载搜索索引");
    expect(component).toContain("已展示");
    expect(component).toContain("失败");
    expect(component).toContain("没有找到符合条件的论文");
    expect(component).toContain("搜索暂时不可用");
    expect(component).toMatch(/<noscript>[\s\S]*全文搜索需要启用 JavaScript/s);
    expect(component).toContain("location.hash");
  });

  it("exposes a discoverable search route", async () => {
    const page = await source("pages/search.astro");
    const header = await source("components/Header.astro");

    expect(page).toContain(
      'import SearchPanel from "../components/SearchPanel.astro"',
    );
    expect(page).toContain("<SearchPanel />");
    expect(header).toContain('{ label: "搜索", path: "search/" }');
    expect(header).toContain("href={`${base}${link.path}`}");
  });
});

describe("Pagefind document metadata contract", () => {
  it("uses attribute capture syntax for all paper metadata and filters", async () => {
    const detail = await source("pages/papers/[id].astro");

    for (const key of ["title", "title_zh", "summary"]) {
      expect(detail).toContain(`data-pagefind-meta="${key}[content]"`);
    }
    for (const key of ["topic", "score", "code", "date"]) {
      expect(detail).toContain(`data-pagefind-filter="${key}[content]"`);
    }
  });

  it("gives every rendered card a stable data identity", async () => {
    const card = await source("components/PaperCard.astro");

    expect(card).toContain("data-id={paper.arxiv_id}");
  });
});

describe("explorer responsive interaction styles", () => {
  it("keeps controls touch-sized, focused, and single-column on narrow screens", async () => {
    const css = await source("styles/global.css");

    expect(css).toMatch(/\.explorer-controls[\s\S]*min-height:\s*2\.75rem/);
    expect(css).toMatch(/\.explorer-controls[\s\S]*:focus-visible/);
    expect(css).toMatch(
      /@media\s*\(max-width:\s*44rem\)[\s\S]*\.explorer-controls/s,
    );
  });
});
