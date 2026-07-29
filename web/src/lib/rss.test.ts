import { describe, expect, it } from "vitest";
import type { Paper } from "./schema";
import { createRssItems, paperPermalink } from "./rss";

function paper(index: number): Paper {
  const arxivId = `2607.${String(index).padStart(5, "0")}`;
  return {
    arxiv_id: arxivId,
    title: `English title ${index}`,
    title_zh: `中文标题 ${index}`,
    published_at: "2026-07-27T01:00:00Z",
    analysis: { one_sentence_summary: `摘要 ${index}` },
  } as Paper;
}

describe("RSS helpers", () => {
  it("builds absolute root and project-base paper permalinks", () => {
    const site = new URL("https://research.example/");

    expect(paperPermalink(site, "/", "2607.12345")).toBe(
      "https://research.example/papers/2607.12345/",
    );
    expect(paperPermalink(site, "/vla-wam-daily/", "2607.12345")).toBe(
      "https://research.example/vla-wam-daily/papers/2607.12345/",
    );
  });

  it("creates at most 100 ordered items with bilingual titles and summaries", () => {
    const papers = Array.from({ length: 105 }, (_, index) => paper(index + 1));

    const items = createRssItems(
      papers,
      new URL("https://research.example/"),
      "/vla-wam-daily/",
    );

    expect(items).toHaveLength(100);
    expect(items[0]).toEqual({
      title: "English title 1 / 中文标题 1",
      description: "摘要 1",
      pubDate: new Date("2026-07-27T01:00:00Z"),
      link: "https://research.example/vla-wam-daily/papers/2607.00001/",
    });
    expect(items.at(-1)?.link).toBe(
      "https://research.example/vla-wam-daily/papers/2607.00100/",
    );
    expect(papers).toHaveLength(105);
  });
});
