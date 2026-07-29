import { mkdtemp, mkdir, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  DataLoadError,
  loadArchive,
  loadLatestDataFile,
  resolveDataDir,
} from "./data";
import { dataFileSchema, figureGallerySchema, paperSchema } from "./schema";

const timestamp = "2026-07-29T02:30:00Z";

function figureGallery(arxivId = "2607.12345", version = 1) {
  return {
    status: "available",
    html_url: `https://arxiv.org/html/${arxivId}v${version}`,
    figures: [
      {
        number: 1,
        label: "Figure 1",
        caption: "The model architecture.",
        image_urls: [`https://arxiv.org/html/${arxivId}v${version}/x1.png`],
        source_url: `https://arxiv.org/html/${arxivId}v${version}#S1.F1`,
        source: "arxiv_html",
      },
      {
        number: 2,
        label: "Figure 2",
        caption: "Robot evaluation environments.",
        image_urls: [
          `https://www.arxiv.org/html/${arxivId}v${version}/x2-a.svg`,
        ],
        source_url: `https://arxiv.org/html/${arxivId}v${version}#S2.F2`,
        source: "arxiv_html",
      },
    ],
    checked_at: timestamp,
  };
}

function paper(arxivId = "2607.12345", version = 1) {
  return {
    arxiv_id: arxivId,
    version,
    published_at: "2026-07-27T01:00:00Z",
    updated_at: `2026-07-${String(26 + version).padStart(2, "0")}T01:00:00Z`,
    title: "A Vision-Language-Action Policy",
    title_zh: "视觉语言动作策略",
    authors: ["Ada Robot"],
    arxiv_categories: ["cs.RO"],
    abstract: "A robot policy abstract.",
    matched_rules: ["vision language action"],
    analysis: {
      relevance_score: 8,
      primary_topic: "VLA",
      tags: ["Vision-Language"],
      one_sentence_summary: "提出视觉语言动作策略。",
      main_contribution: "统一视觉、语言与动作。",
      method: "采用多模态策略学习。",
      key_results: "摘要未说明",
      limitations: "摘要未说明",
      relation_to_vla_wam: "直接属于 VLA。",
    },
    resources: {
      arxiv_url: `https://arxiv.org/abs/${arxivId}`,
      pdf_url: `https://arxiv.org/pdf/${arxivId}`,
      project_url: null,
      code_url: null,
    },
    provenance: {
      analysis_scope: "title_and_abstract",
      model: "deepseek-v4-pro",
      prompt_version: "1",
      analyzed_at: timestamp,
    },
    figure_gallery: figureGallery(arxivId, version),
  };
}

function dataFile(papers: unknown[], overrides: Record<string, unknown> = {}) {
  return {
    schema_version: "1",
    generated_at: timestamp,
    stats: {
      fetched: papers.length,
      prefiltered: papers.length,
      cache_hits: 0,
      figure_cache_hits: 0,
      figure_requests: 0,
      figure_available: papers.length,
      figure_unavailable: 0,
      figure_failed: 0,
      model_calls: papers.length,
      published: papers.length,
      failed: 0,
      prompt_tokens: 10,
      completion_tokens: 5,
      total_tokens: 15,
      error_categories: {},
    },
    papers,
    ...overrides,
  };
}

async function makeDataRoot(): Promise<string> {
  const root = await mkdtemp(join(tmpdir(), "vla-wam-data-"));
  await mkdir(join(root, "archive"));
  return root;
}

async function writeArchive(
  root: string,
  name: string,
  payload: unknown,
): Promise<void> {
  await writeFile(join(root, "archive", name), JSON.stringify(payload), "utf8");
}

describe("the public data contract", () => {
  it("loads the repository fixture with all four Figure states", async () => {
    const fixture = resolve(
      dirname(fileURLToPath(import.meta.url)),
      "../../../tests/fixtures/data",
    );
    const loaded = await loadLatestDataFile(fixture);

    expect(loaded.papers).toHaveLength(4);
    expect(
      new Set(loaded.papers.map((item) => item.figure_gallery.status)),
    ).toEqual(
      new Set(["available", "html_unavailable", "not_found", "fetch_failed"]),
    );
  });

  it("rejects unknown fields at every public boundary", () => {
    expect(() => paperSchema.parse({ ...paper(), unexpected: true })).toThrow();
    expect(() =>
      paperSchema.parse({
        ...paper(),
        analysis: { ...paper().analysis, unexpected: true },
      }),
    ).toThrow();
    expect(() =>
      dataFileSchema.parse({ ...dataFile([paper()]), unexpected: true }),
    ).toThrow();
  });

  it("requires non-empty integer and UTC-valued fields", () => {
    expect(() => paperSchema.parse({ ...paper(), authors: [] })).toThrow();
    expect(() => paperSchema.parse({ ...paper(), version: 1.5 })).toThrow();
    expect(() => paperSchema.parse({ ...paper(), title_zh: "   " })).toThrow();
    expect(() =>
      paperSchema.parse({ ...paper(), published_at: "2026-07-27T01:00:00" }),
    ).toThrow();
  });

  it("normalizes AI text and provenance like the Python model", () => {
    const raw = paper();
    const parsed = paperSchema.parse({
      ...raw,
      title_zh: " 视觉语言动作策略 ",
      analysis: {
        ...raw.analysis,
        tags: ["Vision-Language", "Vision-Language"],
        one_sentence_summary: " 总结 ",
      },
      provenance: { ...raw.provenance, model: " deepseek-v4-pro " },
    });

    expect(parsed.title_zh).toBe("视觉语言动作策略");
    expect(parsed.analysis.one_sentence_summary).toBe("总结");
    expect(parsed.analysis.tags).toEqual(["Vision-Language"]);
    expect(parsed.provenance.model).toBe("deepseek-v4-pro");
  });

  it("normalizes every Python NonEmptyStr public field", () => {
    const raw = paper();
    const parsed = paperSchema.parse({
      ...raw,
      title: " Original title ",
      authors: [" Ada Robot "],
      arxiv_categories: [" cs.RO "],
      abstract: " Original abstract ",
      matched_rules: [" vision language action "],
      figure_gallery: {
        ...raw.figure_gallery,
        figures: raw.figure_gallery.figures.map((figure) => ({
          ...figure,
          label: ` ${figure.label} `,
          caption: ` ${figure.caption} `,
        })),
      },
    });

    expect(parsed.title).toBe("Original title");
    expect(parsed.authors).toEqual(["Ada Robot"]);
    expect(parsed.arxiv_categories).toEqual(["cs.RO"]);
    expect(parsed.abstract).toBe("Original abstract");
    expect(parsed.matched_rules).toEqual(["vision language action"]);
    expect(parsed.figure_gallery.figures[0].label).toBe("Figure 1");
    expect(parsed.figure_gallery.figures[0].caption).toBe(
      "The model architecture.",
    );
  });

  it.each([
    ["title", { title: " \n " }],
    ["authors", { authors: [" \n "] }],
    ["arxiv categories", { arxiv_categories: [" \n "] }],
    ["abstract", { abstract: " \n " }],
    ["matched rules", { matched_rules: [" \n "] }],
  ])("rejects blank persisted %s", (_label, override) => {
    expect(() => paperSchema.parse({ ...paper(), ...override })).toThrow();
  });

  it("requires token totals to equal prompt plus completion tokens", () => {
    const payload = dataFile([paper()]);
    const stats = payload.stats;
    expect(() =>
      dataFileSchema.parse({
        ...payload,
        stats: { ...stats, total_tokens: stats.total_tokens + 1 },
      }),
    ).toThrow(/token/i);
  });

  it("mirrors Python RunStats defaults for omitted counters", () => {
    const payload = dataFile([]);
    const { figure_cache_hits: _omitted, ...stats } = payload.stats;
    const parsed = dataFileSchema.parse({ ...payload, stats });

    expect(parsed.stats.figure_cache_hits).toBe(0);
  });
});

describe("the Figure contract", () => {
  it.each([
    ["http transport", "http://arxiv.org/html/2607.12345v1/x1.png"],
    ["external host", "https://example.com/html/2607.12345v1/x1.png"],
    ["credentials", "https://user:pass@arxiv.org/html/2607.12345v1/x1.png"],
    ["non-default port", "https://arxiv.org:444/html/2607.12345v1/x1.png"],
    ["fragment", "https://arxiv.org/html/2607.12345v1/x1.png#tracking"],
  ])("rejects an image using %s", (_label, imageUrl) => {
    const gallery = figureGallery();
    gallery.figures[0].image_urls = [imageUrl];
    expect(() => figureGallerySchema.parse(gallery)).toThrow();
  });

  it("requires available galleries to have figures and unavailable galleries to be empty", () => {
    expect(() =>
      figureGallerySchema.parse({ ...figureGallery(), figures: [] }),
    ).toThrow(/available/i);
    expect(() =>
      figureGallerySchema.parse({ ...figureGallery(), status: "not_found" }),
    ).toThrow(/unavailable/i);
  });

  it("requires unique Figure 1/Figure 2 numbers", () => {
    const gallery = figureGallery();
    gallery.figures[1].number = 1;
    expect(() => figureGallerySchema.parse(gallery)).toThrow(/unique/i);
  });

  it("binds gallery, source and image paths to the paper id and version", () => {
    const gallery = figureGallery();
    gallery.figures[0].image_urls = [
      "https://arxiv.org/html/2607.12345v2/x1.png",
    ];
    expect(() => figureGallerySchema.parse(gallery)).toThrow(/version/i);
    expect(() =>
      paperSchema.parse({
        ...paper(),
        figure_gallery: figureGallery("2607.54321", 1),
      }),
    ).toThrow(/identity/i);
  });

  it("requires a non-empty source fragment", () => {
    const gallery = figureGallery();
    gallery.figures[0].source_url = "https://arxiv.org/html/2607.12345v1";
    expect(() => figureGallerySchema.parse(gallery)).toThrow(/fragment/i);
  });

  it("deduplicates repeated remote image URLs", () => {
    const gallery = figureGallery();
    gallery.figures[0].image_urls.push(gallery.figures[0].image_urls[0]);
    const parsed = figureGallerySchema.parse(gallery);

    expect(parsed.figures[0].image_urls).toHaveLength(1);
  });
});

describe("data loading", () => {
  it("resolves the default repository data directory and safe relative overrides", () => {
    const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
    expect(resolveDataDir(undefined, webRoot)).toBe(
      resolve(webRoot, "../data"),
    );
    expect(resolveDataDir("../tests/fixtures/data", webRoot)).toBe(
      resolve(webRoot, "../tests/fixtures/data"),
    );
    expect(() => resolveDataDir(" \u0000 ", webRoot)).toThrow(
      /data directory/i,
    );
  });

  it("keeps only the highest version per id and sorts deterministically", async () => {
    const root = await makeDataRoot();
    await writeArchive(
      root,
      "2026-06.json",
      dataFile([paper("2607.12345", 1)]),
    );
    await writeArchive(
      root,
      "2026-07.json",
      dataFile([
        paper("2607.12345", 2),
        { ...paper("2607.54321", 1), published_at: "2026-07-28T01:00:00Z" },
        { ...paper("2607.11111", 1), published_at: "2026-07-28T01:00:00Z" },
      ]),
    );

    const papers = await loadArchive(root);

    expect(papers.map((item) => [item.arxiv_id, item.version])).toEqual([
      ["2607.11111", 1],
      ["2607.54321", 1],
      ["2607.12345", 2],
    ]);
  });

  it("reports a missing archive directory as a build data error", async () => {
    const root = await mkdtemp(join(tmpdir(), "vla-wam-missing-"));
    await expect(loadArchive(root)).rejects.toBeInstanceOf(DataLoadError);
    await expect(loadArchive(root)).rejects.toThrow(
      /archive directory.*missing/i,
    );
  });

  it("reports malformed JSON with the archive file name", async () => {
    const root = await makeDataRoot();
    await writeFile(join(root, "archive", "2026-07.json"), "{broken", "utf8");
    await expect(loadArchive(root)).rejects.toThrow(/2026-07\.json.*JSON/i);
  });

  it("reports schema violations with the archive file name", async () => {
    const root = await makeDataRoot();
    await writeArchive(root, "2026-07.json", {
      ...dataFile([paper()]),
      schema_version: "2",
    });
    await expect(loadArchive(root)).rejects.toThrow(/2026-07\.json.*schema/i);
  });
});
