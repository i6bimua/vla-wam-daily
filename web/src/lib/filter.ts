import type { Paper, Topic } from "./schema";

export const TOPICS: readonly Topic[] = [
  "VLA",
  "WAM",
  "World Model",
  "Dataset",
  "Benchmark",
];
export const DEFAULT_MINIMUM_SCORE = 6;
export const MAX_FILTER_QUERY_LENGTH = 200;

export interface FilterState {
  query: string;
  topics: Topic[];
  minimumScore: number;
  code: "" | "yes" | "no";
  date: string;
}

export interface FilterPaper {
  arxivId: string;
  topic: Topic;
  score: number;
  hasCode: boolean;
  date: string;
  searchText: string;
}

function validDate(value: string): boolean {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return false;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  if (year < 1 || month < 1 || month > 12) return false;
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return day >= 1 && day <= (days[month - 1] ?? 0);
}

function canonicalQuery(value: string): string {
  const query = value.trim();
  return query.length <= MAX_FILTER_QUERY_LENGTH ? query : "";
}

function canonicalTopics(values: readonly string[]): Topic[] {
  const selected = new Set(values);
  return TOPICS.filter((topic) => selected.has(topic));
}

function canonicalScore(value: string): number {
  return /^(?:[1-9]|10)$/.test(value) ? Number(value) : DEFAULT_MINIMUM_SCORE;
}

export function parseFilterState(search: string): FilterState {
  const params = new URLSearchParams(search);
  const code = params.get("code");
  const date = params.get("date") ?? "";
  return {
    query: canonicalQuery(params.get("q") ?? ""),
    topics: canonicalTopics(params.getAll("topic")),
    minimumScore: canonicalScore(params.get("score") ?? ""),
    code: code === "yes" || code === "no" ? code : "",
    date: validDate(date) ? date : "",
  };
}

export function serializeFilterState(state: FilterState): string {
  const params = new URLSearchParams();
  const query = canonicalQuery(state.query);
  if (query) params.set("q", query);
  for (const topic of canonicalTopics(state.topics)) {
    params.append("topic", topic);
  }
  const score = canonicalScore(String(state.minimumScore));
  if (score !== DEFAULT_MINIMUM_SCORE) params.set("score", String(score));
  if (state.code === "yes" || state.code === "no") {
    params.set("code", state.code);
  }
  if (validDate(state.date)) params.set("date", state.date);
  return params.toString();
}

export function createFilterPaper(paper: Paper): FilterPaper {
  return {
    arxivId: paper.arxiv_id,
    topic: paper.analysis.primary_topic,
    score: paper.analysis.relevance_score,
    hasCode: paper.resources.code_url !== null,
    date: paper.published_at.slice(0, 10),
    searchText: [
      paper.title,
      paper.title_zh,
      paper.authors.join(" "),
      paper.abstract,
      paper.analysis.tags.join(" "),
      paper.analysis.one_sentence_summary,
      paper.analysis.main_contribution,
      paper.analysis.method,
      paper.analysis.key_results,
      paper.analysis.limitations,
      paper.analysis.relation_to_vla_wam,
    ].join("\n"),
  };
}

function normalizeSearchValue(value: string): string {
  return value
    .normalize("NFKC")
    .toLocaleLowerCase("en-US")
    .replaceAll("ß", "ss")
    .replaceAll("ς", "σ")
    .replace(/\s+/gu, " ")
    .trim();
}

export function filterPapers(
  papers: readonly FilterPaper[],
  state: FilterState,
): FilterPaper[] {
  const canonical = parseFilterState(`?${serializeFilterState(state)}`);
  const query = normalizeSearchValue(canonical.query);
  return papers.filter(
    (paper) =>
      (!query || normalizeSearchValue(paper.searchText).includes(query)) &&
      (!canonical.topics.length || canonical.topics.includes(paper.topic)) &&
      paper.score >= canonical.minimumScore &&
      (!canonical.code || (paper.hasCode ? "yes" : "no") === canonical.code) &&
      (!canonical.date || paper.date === canonical.date),
  );
}

export function serializeJsonForHtml(value: unknown): string {
  const serialized = JSON.stringify(value);
  if (serialized === undefined) {
    throw new TypeError("Embedded filter data must be JSON serializable");
  }
  return serialized
    .replaceAll("<", "\\u003c")
    .replaceAll("\u2028", "\\u2028")
    .replaceAll("\u2029", "\\u2029");
}
