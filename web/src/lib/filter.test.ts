import { describe, expect, it } from "vitest";
import {
  createFilterPaper,
  filterPapers,
  parseFilterState,
  serializeFilterState,
  serializeJsonForHtml,
  type FilterPaper,
  type FilterState,
} from "./filter";
import type { Paper } from "./schema";

const defaultState: FilterState = {
  query: "",
  topics: [],
  minimumScore: 6,
  code: "",
  date: "",
};

const filterFixtures: readonly FilterPaper[] = [
  {
    arxivId: "2607.10001",
    topic: "WAM",
    score: 9,
    hasCode: true,
    date: "2026-07-27",
    searchText: "Ａction WORLD model 机器人",
  },
  {
    arxivId: "2607.10002",
    topic: "VLA",
    score: 7,
    hasCode: false,
    date: "2026-07-26",
    searchText: "Vision Language Policy 视觉语言策略",
  },
  {
    arxivId: "2607.10003",
    topic: "Dataset",
    score: 5,
    hasCode: true,
    date: "2026-07-27",
    searchText: "Robot dataset",
  },
];

describe("parseFilterState", () => {
  it("trims query and canonicalizes valid repeated state", () => {
    const state = parseFilterState(
      "?q=%20Robot%20World%20&topic=WAM&topic=VLA&topic=WAM&topic=Unknown&score=8&code=yes&date=2024-02-29",
    );

    expect(state).toEqual({
      query: "Robot World",
      topics: ["VLA", "WAM"],
      minimumScore: 8,
      code: "yes",
      date: "2024-02-29",
    });
  });

  it.each([
    ["overlong query", `?q=${"x".repeat(201)}`, "query", ""],
    ["invalid score", "?score=0", "minimumScore", 6],
    ["non-canonical score", "?score=07", "minimumScore", 6],
    ["invalid code", "?code=maybe", "code", ""],
    ["nonexistent day", "?date=2026-02-29", "date", ""],
    ["invalid month", "?date=2026-13-01", "date", ""],
    ["zero year", "?date=0000-01-01", "date", ""],
  ])("drops %s", (_label, search, key, expected) => {
    expect(parseFilterState(search)[key as keyof FilterState]).toBe(expected);
  });
});

describe("serializeFilterState", () => {
  it("uses a stable canonical order and omits defaults", () => {
    expect(
      serializeFilterState({
        query: "  机器人 world  ",
        topics: ["WAM", "VLA", "WAM"],
        minimumScore: 8,
        code: "no",
        date: "2026-07-27",
      }),
    ).toBe(
      "q=%E6%9C%BA%E5%99%A8%E4%BA%BA+world&topic=VLA&topic=WAM&score=8&code=no&date=2026-07-27",
    );
    expect(serializeFilterState(defaultState)).toBe("");
  });

  it("round-trips to canonical state", () => {
    const parsed = parseFilterState(
      "?date=nope&topic=WAM&topic=VLA&q=%20test%20&unknown=x",
    );

    expect(parseFilterState(`?${serializeFilterState(parsed)}`)).toEqual(
      parsed,
    );
  });
});

describe("filterPapers", () => {
  it("normalizes NFKC case and combines every filter with AND", () => {
    const result = filterPapers(filterFixtures, {
      query: "action world",
      topics: ["VLA", "WAM"],
      minimumScore: 8,
      code: "yes",
      date: "2026-07-27",
    });

    expect(result.map((paper) => paper.arxivId)).toEqual(["2607.10001"]);
  });

  it("matches Chinese and English text deterministically", () => {
    expect(
      filterPapers(filterFixtures, {
        ...defaultState,
        query: "VISION LANGUAGE",
      }).map((paper) => paper.arxivId),
    ).toEqual(["2607.10002"]);
    expect(
      filterPapers(filterFixtures, {
        ...defaultState,
        query: "机器人",
      }).map((paper) => paper.arxivId),
    ).toEqual(["2607.10001"]);
  });

  it("does not mutate input records or state", () => {
    const papers = filterFixtures.map((paper) =>
      Object.freeze({ ...paper }),
    ) as readonly FilterPaper[];
    const state = Object.freeze({
      ...defaultState,
      topics: Object.freeze(["WAM"]) as unknown as FilterState["topics"],
    });
    const before = JSON.stringify({ papers, state });

    const result = filterPapers(papers, state);

    expect(JSON.stringify({ papers, state })).toBe(before);
    expect(result).not.toBe(papers);
    expect(result[0]).toBe(papers[0]);
  });
});

describe("client filter projection", () => {
  it("keeps only fields required by local filtering", () => {
    const paper = {
      arxiv_id: "2607.12345",
      title: "World Action Model",
      title_zh: "世界动作模型",
      authors: ["Ada", "Wei"],
      abstract: "Robot world model.",
      published_at: "2026-07-27T01:00:00Z",
      analysis: {
        primary_topic: "WAM",
        relevance_score: 9,
        tags: ["World Modeling"],
        one_sentence_summary: "一句话总结",
        main_contribution: "主要贡献",
        method: "方法",
        key_results: "结果",
        limitations: "局限",
        relation_to_vla_wam: "关联",
      },
      resources: { code_url: "https://github.com/example/project" },
      figure_gallery: { status: "available" },
    } as unknown as Paper;

    const projected = createFilterPaper(paper);

    expect(projected).toEqual({
      arxivId: "2607.12345",
      topic: "WAM",
      score: 9,
      hasCode: true,
      date: "2026-07-27",
      searchText: expect.stringContaining("世界动作模型"),
    });
    expect(Object.keys(projected).sort()).toEqual([
      "arxivId",
      "date",
      "hasCode",
      "score",
      "searchText",
      "topic",
    ]);
    expect(JSON.stringify(projected)).not.toContain("figure_gallery");
  });

  it("escapes script terminators and JavaScript line separators", () => {
    const value = { text: "</script><script>\u2028\u2029" };
    const serialized = serializeJsonForHtml(value);

    expect(serialized).not.toContain("<");
    expect(serialized).not.toContain("\u2028");
    expect(serialized).not.toContain("\u2029");
    expect(JSON.parse(serialized)).toEqual(value);
  });
});
