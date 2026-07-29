import { readFile, stat } from "node:fs/promises";
import { resolve } from "node:path";
import { XMLParser } from "fast-xml-parser";
import { SyntaxValidator } from "fast-xml-validator";
import astroConfig from "../astro.config.mjs";

const dist = resolve("dist");
const base = astroConfig.base;
const site = new URL(astroConfig.site);
const expectEmptyArchive = process.env.VLA_WAM_EXPECT_EMPTY_ARCHIVE === "1";

async function text(relativePath) {
  return readFile(resolve(dist, relativePath), "utf8");
}

function requireBuild(condition, message) {
  if (!condition) {
    throw new Error(`Information build verification failed: ${message}`);
  }
}

function countPaperCards(source) {
  return source.match(/\sdata-paper-card(?:\s|=|>)/g)?.length ?? 0;
}

function itemArray(value) {
  if (value === undefined) return [];
  return Array.isArray(value) ? value : [value];
}

const home = await text("index.html");
const archiveIndex = await text("archive/index.html");
const weekly = await text("weekly/index.html");
const methodology = await text("methodology/index.html");
const notFound = await text("404.html");

const topicExpectations = [
  ["vla", "视觉语言动作（VLA）论文", expectEmptyArchive ? 0 : 4],
  ["wam", "世界动作模型（WAM）论文", 0],
  ["world-model", "机器人世界模型论文", 0],
  ["dataset", "VLA/WAM 数据集", 0],
  ["benchmark", "VLA/WAM 基准评测", 0],
];
for (const [slug, title, expectedCount] of topicExpectations) {
  const page = await text(`topics/${slug}/index.html`);
  requireBuild(page.includes(title), `${slug} topic must render its title`);
  requireBuild(
    page.includes("data-explorer") && countPaperCards(page) === expectedCount,
    `${slug} topic must render PaperExplorer with ${expectedCount} fixture papers`,
  );
  requireBuild(
    home.includes(`href="${base}topics/${slug}/"`),
    `${slug} topic must be linked from the global navigation`,
  );
}

if (expectEmptyArchive) {
  requireBuild(
    archiveIndex.includes("归档尚为空") &&
      countPaperCards(weekly) === 0 &&
      weekly.includes("过去七天尚无符合发布条件的论文") &&
      methodology.includes("quality profile 默认回退"),
    "empty data must still build archive, weekly, and fallback methodology pages",
  );
} else {
  const archiveMonth = await text("archive/2026-07/index.html");
  requireBuild(
    archiveIndex.includes(`href="${base}archive/2026-07/"`) &&
      archiveIndex.includes("4 篇论文"),
    "archive index must link the fixture month with its current-paper count",
  );
  requireBuild(
    archiveMonth.includes("2026-07-27") &&
      countPaperCards(archiveMonth) === 4 &&
      archiveMonth.includes(`href="${base}papers/2607.12345/"`),
    "monthly archive must group compact cards by day and retain detail links",
  );
  requireBuild(
    countPaperCards(weekly) === 2 &&
      weekly.includes("2607.12345") &&
      weekly.includes("2607.20001") &&
      weekly.includes(`href="${base}papers/2607.12345/"`) &&
      weekly.includes("2026-07-29"),
    "weekly page must use generated_at and topic-balanced compact detail links",
  );
}
requireBuild(
  methodology.includes(expectEmptyArchive ? "2026-07-27" : "2026-07-29") &&
    methodology.includes("deepseek-v4-pro") &&
    methodology.includes("两级筛选") &&
    methodology.includes("不会猜测") &&
    methodology.includes("Fig. 1 / Fig. 2") &&
    methodology.includes("工作流"),
  "methodology must disclose current provenance and non-guessing pipeline behavior",
);
requireBuild(
  notFound.includes(`href="${base}"`),
  "404 page must link to the configured base",
);

for (const path of [
  "weekly/",
  "archive/",
  "search/",
  "rss.xml",
  "methodology/",
]) {
  requireBuild(
    home.includes(`href="${base}${path}"`),
    `${path} must be linked from the global navigation`,
  );
}
requireBuild(
  home.includes(
    `rel="alternate" type="application/rss+xml" title="VLA/WAM Daily RSS" href="${base}rss.xml"`,
  ),
  "home must preserve RSS discovery with the configured base",
);

const rssSource = await text("rss.xml");
const validation = SyntaxValidator.validate(rssSource);
requireBuild(
  validation === true,
  `RSS XML must be well formed: ${JSON.stringify(validation)}`,
);
const parsed = new XMLParser({ ignoreAttributes: false }).parse(rssSource);
const channel = parsed?.rss?.channel;
const items = itemArray(channel?.item);
requireBuild(
  channel?.title === "VLA/WAM Daily" &&
    channel?.language === "zh-CN" &&
    channel?.link === new URL(base, site).href,
  "RSS channel metadata and site link must match the configured base",
);
requireBuild(
  items.length === (expectEmptyArchive ? 0 : 4),
  "RSS item count must match the current archive",
);
for (const item of items) {
  const link = new URL(item.link);
  const paperPathPrefix = `${base}papers/`;
  requireBuild(
    link.origin === site.origin &&
      link.pathname.startsWith(paperPathPrefix) &&
      /^\d{4}\.\d{4,5}\/$/.test(link.pathname.slice(paperPathPrefix.length)),
    `RSS item link must be absolute and base-safe: ${item.link}`,
  );
  requireBuild(
    typeof item.title === "string" &&
      item.title.includes(" / ") &&
      typeof item.description === "string" &&
      item.description.length > 0 &&
      Number.isFinite(Date.parse(item.pubDate)),
    "RSS items must have bilingual titles, summaries, and valid publication dates",
  );
}
if (!expectEmptyArchive) {
  const expectedFirstLink = new URL(`${base}papers/2607.12345/`, site.origin)
    .href;
  requireBuild(
    items[0]?.link === expectedFirstLink &&
      items[0]?.title.includes("A Vision-Language-Action Policy") &&
      items[0]?.title.includes("用于机器人操作"),
    "RSS items must preserve deterministic archive order and bilingual titles",
  );
}

await Promise.all([
  stat(resolve(dist, "pagefind/pagefind.js")),
  stat(resolve(dist, "rss.xml")),
  stat(resolve(dist, "404.html")),
]);
