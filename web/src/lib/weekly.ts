import type { Paper, Topic } from "./schema";

const WEEK_IN_MILLISECONDS = 7 * 24 * 60 * 60 * 1000;

function requireValidDate(value: Date): number {
  if (!(value instanceof Date) || !Number.isFinite(value.getTime())) {
    throw new TypeError("Weekly selection requires a valid Date");
  }
  return value.getTime();
}

function compareWeeklyPapers(left: Paper, right: Paper): number {
  return (
    right.analysis.relevance_score - left.analysis.relevance_score ||
    Date.parse(right.published_at) - Date.parse(left.published_at) ||
    Date.parse(right.updated_at) - Date.parse(left.updated_at) ||
    right.version - left.version ||
    left.arxiv_id.localeCompare(right.arxiv_id)
  );
}

export function selectWeeklyTop(papers: readonly Paper[], now: Date): Paper[] {
  const end = requireValidDate(now);
  const start = end - WEEK_IN_MILLISECONDS;
  const eligible = papers
    .filter((paper) => {
      const published = Date.parse(paper.published_at);
      return published >= start && published <= end;
    })
    .sort(compareWeeklyPapers);

  const topicCounts = new Map<Topic, number>();
  const selected: Paper[] = [];
  for (const paper of eligible) {
    const topic = paper.analysis.primary_topic;
    if ((topicCounts.get(topic) ?? 0) >= 2) continue;
    selected.push(paper);
    topicCounts.set(topic, (topicCounts.get(topic) ?? 0) + 1);
    if (selected.length === 5) break;
  }
  return selected;
}
