import { readFile, stat } from "node:fs/promises";
import { extname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import astroConfig from "../astro.config.mjs";

const dist = resolve("dist");

async function html(relativePath) {
  return readFile(resolve(dist, relativePath), "utf8");
}

function requireBuild(condition, message) {
  if (!condition) {
    throw new Error(`Search build verification failed: ${message}`);
  }
}

const base = astroConfig.base;
const home = await html("index.html");
const searchPage = await html("search/index.html");
const detail = await html("papers/2607.12345/index.html");

requireBuild(
  home.includes(`href="${base}search/"`),
  "home navigation must link to the base-scoped search route",
);
requireBuild(
  home.includes("data-explorer") && home.includes("data-filter-form"),
  "home must render the progressively enhanced paper explorer",
);
requireBuild(
  searchPage.includes(`action="${base}search/"`) &&
    searchPage.includes(`data-base="${base}"`),
  "search form and Pagefind loader must use the project base",
);
requireBuild(
  !home.includes('<details class="analysis" open>') &&
    detail.includes('<details class="analysis" open>'),
  "home Figure details must stay closed while paper detail stays open",
);

const embeddedMatch =
  /<script\b[^>]*data-paper-json[^>]*>([\s\S]*?)<\/script>/.exec(home);
requireBuild(embeddedMatch, "home must embed minimal filter JSON");
const embeddedSource = embeddedMatch[1];
const filterRecords = JSON.parse(embeddedSource);
requireBuild(
  Array.isArray(filterRecords) && filterRecords.length === 4,
  "filter JSON must contain one record per fixture paper",
);
const expectedRecordKeys = [
  "arxivId",
  "date",
  "hasCode",
  "score",
  "searchText",
  "topic",
];
requireBuild(
  filterRecords.every(
    (record) =>
      JSON.stringify(Object.keys(record).sort()) ===
      JSON.stringify(expectedRecordKeys),
  ),
  "filter JSON must not contain full paper or Figure payloads",
);
requireBuild(
  !embeddedSource.includes("figure_gallery") &&
    !embeddedSource.includes('"abstract"'),
  "filter JSON must omit Figure and standalone abstract fields",
);

for (const attribute of [
  'data-pagefind-meta="title[content]"',
  'data-pagefind-meta="title_zh[content]"',
  'data-pagefind-meta="summary[content]"',
  'data-pagefind-filter="topic[content]"',
  'data-pagefind-filter="score[content]"',
  'data-pagefind-filter="code[content]"',
  'data-pagefind-filter="date[content]"',
]) {
  requireBuild(detail.includes(attribute), `detail must contain ${attribute}`);
}

const pagefindModulePath = resolve(dist, "pagefind/pagefind.js");
await stat(pagefindModulePath);
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
    `${pathToFileURL(pagefindModulePath).href}?verify=${Date.now()}`
  );
  await pagefind.options({ baseUrl: base });
  const filters = await pagefind.filters();
  for (const key of ["topic", "score", "code", "date"]) {
    requireBuild(
      filters[key] && Object.keys(filters[key]).length > 0,
      `Pagefind index must expose the ${key} filter`,
    );
  }
  requireBuild(
    filters.topic.VLA === 4 &&
      filters.score["6"] === 3 &&
      filters.score["8"] === 1 &&
      filters.code.no === 4 &&
      filters.date["2026-07-27"] === 4,
    "Pagefind filter counts must match fixture papers",
  );

  const response = await pagefind.search("vision", {
    filters: { score: { any: ["6", "7", "8", "9", "10"] } },
  });
  requireBuild(
    response.results.length > 0,
    "Pagefind must return the English fixture paper",
  );
  const result = await response.results[0].data();
  requireBuild(
    result.meta.title &&
      result.meta.title_zh &&
      result.meta.summary &&
      result.url === `${base}papers/2607.12345/`,
    `Pagefind result must expose metadata and the expected raw paper URL: ${JSON.stringify(
      { meta: result.meta, raw_url: result.raw_url, url: result.url },
    )}`,
  );
  requireBuild(
    result.url === `${base}papers/2607.12345/` &&
      !result.url.startsWith(`${base}${base.slice(1)}`),
    "Pagefind result must map to the project-base paper URL",
  );
} finally {
  if (pagefind?.destroy) await pagefind.destroy();
  globalThis.fetch = nativeFetch;
}
