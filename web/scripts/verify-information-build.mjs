import { readFile, stat } from "node:fs/promises";
import { extname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
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

async function searchBuiltPagefind(query) {
  const modulePath = resolve(dist, "pagefind/pagefind.js");
  const nativeFetch = globalThis.fetch.bind(globalThis);
  let pagefind;
  globalThis.fetch = async (input, init) => {
    const target =
      input instanceof Request
        ? input.url
        : input instanceof URL
          ? input.href
          : String(input);
    const url = new URL(target);
    if (url.protocol !== "file:") return nativeFetch(input, init);

    const bytes = await readFile(fileURLToPath(url));
    const extension = extname(url.pathname);
    const contentType =
      extension === ".json"
        ? "application/json"
        : extension === ".pagefind"
          ? "application/wasm"
          : "application/octet-stream";
    return new Response(bytes, {
      status: 200,
      headers: { "content-type": contentType },
    });
  };

  try {
    pagefind = await import(
      `${pathToFileURL(modulePath).href}?information=${Date.now()}`
    );
    await pagefind.options({ baseUrl: base });
    return (await pagefind.search(query)).results.length;
  } finally {
    if (pagefind?.destroy) await pagefind.destroy();
    globalThis.fetch = nativeFetch;
  }
}

const home = await text("index.html");
const archiveIndex = await text("archive/index.html");
const weekly = await text("weekly/index.html");
const methodology = await text("methodology/index.html");
const notFound = await text("404.html");

const topicExpectations = [
  ["vla", "视觉语言动作（VLA）论文", expectEmptyArchive ? 0 : 4],
  ["wam", "世界动作模型（WAM）论文", expectEmptyArchive ? 0 : 1],
  ["world-model", "机器人世界模型论文", 0],
  ["dataset", "VLA/WAM 数据集", 0],
  ["benchmark", "VLA/WAM 基准评测", 0],
  ["speculative-decoding", "推测解码论文", 0],
  ["quantization", "模型量化论文", 0],
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
  const entry = JSON.parse(await text("pagefind/pagefind-entry.json"));
  const indexedPages = Object.values(entry.languages ?? {}).reduce(
    (count, language) => count + (language.page_count ?? 0),
    0,
  );
  requireBuild(
    archiveIndex.includes("归档尚为空") &&
      countPaperCards(weekly) === 0 &&
      weekly.includes("过去七天尚无符合发布条件的论文") &&
      methodology.includes("暂无已发布论文，无法从当前数据确认模型或 Prompt") &&
      methodology.includes("quality profile 的默认模型为") &&
      methodology.includes("deepseek-v4-pro"),
    "empty data must still build archive, weekly, and fallback methodology pages",
  );
  requireBuild(
    indexedPages === 0 && (await searchBuiltPagefind("methodology")) === 0,
    "empty data must index zero paper pages and return zero search results",
  );
} else {
  const archiveMonth = await text("archive/2026-07/index.html");
  requireBuild(
    archiveIndex.includes(`href="${base}archive/2026-07/"`) &&
      archiveIndex.includes("5 篇论文"),
    "archive index must link the fixture month with its current-paper count",
  );
  requireBuild(
    archiveMonth.includes("2026-07-27") &&
      countPaperCards(archiveMonth) === 5 &&
      archiveMonth.includes(`href="${base}papers/2607.12345/"`) &&
      archiveMonth.includes(`href="${base}papers/2607.09999/"`),
    "monthly archive must group compact cards by day and retain detail links",
  );
  requireBuild(
    countPaperCards(home) === 5 &&
      home.includes(`data-id="2607.09999"`) &&
      home.includes("Archive-only cumulative homepage fixture"),
    "home must include the archive-only fixture omitted from latest.json",
  );
  requireBuild(
    countPaperCards(weekly) === 2 &&
      weekly.includes("2607.12345") &&
      weekly.includes("2607.20001") &&
      weekly.includes(`href="${base}papers/2607.12345/"`) &&
      weekly.includes("2026-07-29 10:30:00 北京时间"),
    "weekly page must use generated_at and topic-balanced compact detail links",
  );
  requireBuild(
    methodology.includes("当前已发布论文记录的分析模型为") &&
      methodology.includes("Prompt 版本为"),
    "methodology must derive current model and prompt provenance from published papers",
  );
}
requireBuild(
  methodology.includes(
    expectEmptyArchive
      ? "2026-07-27 08:00:00 北京时间"
      : "2026-07-29 10:30:00 北京时间",
  ) &&
    methodology.includes("deepseek-v4-pro") &&
    methodology.includes("两级筛选") &&
    methodology.includes("--config-path") &&
    methodology.includes("CLI --threshold") &&
    methodology.includes("当前构建数据不携带实际运行阈值") &&
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
  items.length === (expectEmptyArchive ? 0 : 5),
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
