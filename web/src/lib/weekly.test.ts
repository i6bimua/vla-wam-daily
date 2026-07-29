import { describe, expect, it } from "vitest";
import type { Paper, Topic } from "./schema";
import { selectWeeklyTop } from "./weekly";

const NOW = new Date("2026-07-29T02:30:00Z");

function paper(
  arxivId: string,
  topic: Topic,
  score: number,
  overrides: Partial<Paper> = {},
): Paper {
  return {
    arxiv_id: arxivId,
    version: 1,
    published_at: "2026-07-27T01:00:00Z",
    updated_at: "2026-07-27T01:00:00Z",
    analysis: {
      primary_topic: topic,
      relevance_score: score,
    },
    ...overrides,
  } as Paper;
}

describe("selectWeeklyTop", () => {
  it("requires a valid Date so builds cannot silently use an invalid clock", () => {
    expect(() => selectWeeklyTop([], new Date(Number.NaN))).toThrow(TypeError);
    expect(() => selectWeeklyTop([], "2026-07-29T02:30:00Z" as never)).toThrow(
      TypeError,
    );
  });

  it("includes both seven-day boundaries and excludes older or future papers", () => {
    const selected = selectWeeklyTop(
      [
        paper("2607.00001", "VLA", 10, {
          published_at: "2026-07-22T02:30:00Z",
        }),
        paper("2607.00002", "WAM", 9, {
          published_at: "2026-07-29T02:30:00Z",
        }),
        paper("2607.00003", "Dataset", 10, {
          published_at: "2026-07-22T02:29:59.999Z",
        }),
        paper("2607.00004", "Benchmark", 10, {
          published_at: "2026-07-29T02:30:00.001Z",
        }),
      ],
      NOW,
    );

    expect(selected.map((item) => item.arxiv_id)).toEqual([
      "2607.00001",
      "2607.00002",
    ]);
  });

  it.each([
    [
      "score descending",
      paper("2607.10001", "VLA", 8),
      paper("2607.10002", "VLA", 9),
      "2607.10002",
    ],
    [
      "publication descending",
      paper("2607.10001", "VLA", 9, {
        published_at: "2026-07-26T01:00:00Z",
      }),
      paper("2607.10002", "VLA", 9, {
        published_at: "2026-07-27T01:00:00Z",
      }),
      "2607.10002",
    ],
    [
      "update descending",
      paper("2607.10001", "VLA", 9, {
        updated_at: "2026-07-27T01:00:00Z",
      }),
      paper("2607.10002", "VLA", 9, {
        updated_at: "2026-07-28T01:00:00Z",
      }),
      "2607.10002",
    ],
    [
      "version descending",
      paper("2607.10001", "VLA", 9, { version: 1 }),
      paper("2607.10002", "VLA", 9, { version: 2 }),
      "2607.10002",
    ],
    [
      "arXiv id ascending",
      paper("2607.10002", "VLA", 9),
      paper("2607.10001", "VLA", 9),
      "2607.10001",
    ],
  ])(
    "uses %s as a deterministic ordering key",
    (_label, left, right, first) => {
      expect(selectWeeklyTop([left, right], NOW)[0]?.arxiv_id).toBe(first);
    },
  );

  it("returns at most five with at most two per topic without mutating input", () => {
    const papers = [
      paper("2607.20001", "VLA", 10),
      paper("2607.20002", "VLA", 9),
      paper("2607.20003", "VLA", 8),
      paper("2607.20004", "WAM", 9),
      paper("2607.20005", "World Model", 8),
      paper("2607.20006", "Dataset", 8),
      paper("2607.20007", "Benchmark", 7),
    ];
    const before = JSON.stringify(papers);
    const nowBefore = NOW.toISOString();

    const selected = selectWeeklyTop(papers, NOW);

    expect(selected).toHaveLength(5);
    expect(
      selected.filter((item) => item.analysis.primary_topic === "VLA"),
    ).toHaveLength(2);
    expect(selected.map((item) => item.arxiv_id)).not.toContain("2607.20003");
    expect(JSON.stringify(papers)).toBe(before);
    expect(NOW.toISOString()).toBe(nowBefore);
  });

  it("returns every eligible paper when fewer than five qualify", () => {
    const papers = [
      paper("2607.30001", "VLA", 8),
      paper("2607.30002", "WAM", 7),
    ];

    expect(selectWeeklyTop(papers, NOW)).toEqual(papers);
  });
});
