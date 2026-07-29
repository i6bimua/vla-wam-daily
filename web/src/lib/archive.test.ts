import { describe, expect, it } from "vitest";
import type { Paper } from "./schema";
import { groupArchiveDays, groupArchiveMonths } from "./archive";

function paper(arxivId: string, publishedAt: string, score: number): Paper {
  return {
    arxiv_id: arxivId,
    version: 1,
    published_at: publishedAt,
    updated_at: publishedAt,
    analysis: { relevance_score: score, primary_topic: "VLA" },
  } as Paper;
}

describe("archive grouping", () => {
  it("groups current papers by descending publication month and day", () => {
    const papers = [
      paper("2606.00001", "2026-06-30T12:00:00Z", 9),
      paper("2607.00002", "2026-07-27T01:00:00Z", 7),
      paper("2607.00001", "2026-07-28T01:00:00Z", 8),
      paper("2607.00003", "2026-07-27T02:00:00Z", 6),
    ];

    const months = groupArchiveMonths(papers);
    const days = groupArchiveDays(months[0]?.papers ?? []);

    expect(
      months.map(({ month, papers: entries }) => [month, entries.length]),
    ).toEqual([
      ["2026-07", 3],
      ["2026-06", 1],
    ]);
    expect(days.map(({ day }) => day)).toEqual(["2026-07-28", "2026-07-27"]);
    expect(days[1]?.papers.map((item) => item.arxiv_id)).toEqual([
      "2607.00003",
      "2607.00002",
    ]);
  });

  it("is deterministic, handles an empty archive, and does not mutate input", () => {
    const papers = [
      paper("2607.00002", "2026-07-27T01:00:00Z", 7),
      paper("2607.00001", "2026-07-27T01:00:00Z", 8),
    ];
    const before = JSON.stringify(papers);

    expect(
      groupArchiveMonths(papers)[0]?.papers.map((item) => item.arxiv_id),
    ).toEqual(["2607.00001", "2607.00002"]);
    expect(groupArchiveMonths([])).toEqual([]);
    expect(groupArchiveDays([])).toEqual([]);
    expect(JSON.stringify(papers)).toBe(before);
  });
});
