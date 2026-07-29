import type { Paper } from "./schema";

export interface ArchiveMonth {
  month: string;
  papers: Paper[];
}

export interface ArchiveDay {
  day: string;
  papers: Paper[];
}

function compareArchivePapers(left: Paper, right: Paper): number {
  return (
    Date.parse(right.published_at) - Date.parse(left.published_at) ||
    right.analysis.relevance_score - left.analysis.relevance_score ||
    Date.parse(right.updated_at) - Date.parse(left.updated_at) ||
    right.version - left.version ||
    left.arxiv_id.localeCompare(right.arxiv_id)
  );
}

function groupByDatePrefix(
  papers: readonly Paper[],
  length: 7 | 10,
): Array<[string, Paper[]]> {
  const groups = new Map<string, Paper[]>();
  for (const paper of [...papers].sort(compareArchivePapers)) {
    const key = paper.published_at.slice(0, length);
    const entries = groups.get(key);
    if (entries) entries.push(paper);
    else groups.set(key, [paper]);
  }
  return [...groups.entries()].sort(([left], [right]) =>
    right.localeCompare(left),
  );
}

export function groupArchiveMonths(papers: readonly Paper[]): ArchiveMonth[] {
  return groupByDatePrefix(papers, 7).map(([month, entries]) => ({
    month,
    papers: entries,
  }));
}

export function groupArchiveDays(papers: readonly Paper[]): ArchiveDay[] {
  return groupByDatePrefix(papers, 10).map(([day, entries]) => ({
    day,
    papers: entries,
  }));
}
