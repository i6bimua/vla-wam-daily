import type { RSSFeedItem } from "@astrojs/rss";
import type { Paper } from "./schema";

const arxivIdPattern = /^\d{4}\.\d{4,5}$/;

function requireBasePath(base: string): string {
  if (
    !base.startsWith("/") ||
    !base.endsWith("/") ||
    base.includes("//") ||
    base.includes("\\") ||
    base.includes("?") ||
    base.includes("#") ||
    base.split("/").some((segment) => segment === "." || segment === "..")
  ) {
    throw new TypeError("RSS base must be a normalized absolute path");
  }
  return base;
}

export function paperPermalink(
  site: URL,
  base: string,
  arxivId: string,
): string {
  if (
    !["http:", "https:"].includes(site.protocol) ||
    site.username ||
    site.password ||
    !arxivIdPattern.test(arxivId)
  ) {
    throw new TypeError("RSS paper permalink inputs are invalid");
  }
  return new URL(`${requireBasePath(base)}papers/${arxivId}/`, site.origin)
    .href;
}

export function createRssItems(
  papers: readonly Paper[],
  site: URL,
  base: string,
): RSSFeedItem[] {
  return papers.slice(0, 100).map((paper) => ({
    title: `${paper.title} / ${paper.title_zh}`,
    description: paper.analysis.one_sentence_summary,
    pubDate: new Date(paper.published_at),
    link: paperPermalink(site, base, paper.arxiv_id),
  }));
}
