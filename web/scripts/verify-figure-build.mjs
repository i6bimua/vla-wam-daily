import { readFile, stat } from "node:fs/promises";
import { resolve } from "node:path";
import astroConfig from "../astro.config.mjs";

const dist = resolve("dist");

async function html(path) {
  return readFile(resolve(dist, path), "utf8");
}

function requireBuild(condition, message) {
  if (!condition)
    throw new Error(`Figure build verification failed: ${message}`);
}

function countMarkup(source, pattern) {
  return source.match(pattern)?.length ?? 0;
}

const home = await html("index.html");
const available = await html("papers/2607.12345/index.html");
const fallbackCases = [
  [
    "2607.20001",
    "html_unavailable",
    "arXiv 暂未提供 HTML 版本，无法提取 Fig. 1 / Fig. 2。",
  ],
  ["2607.20002", "not_found", "在 arXiv HTML 中未找到 Fig. 1 / Fig. 2。"],
  [
    "2607.20003",
    "fetch_failed",
    "本次获取 Fig. 1 / Fig. 2 失败，论文其它内容仍可正常阅读。",
  ],
];

requireBuild(
  home.includes(`href="${astroConfig.base}papers/2607.12345/"`),
  "home paper links must include the configured base path",
);
requireBuild(
  !home.includes('<details class="analysis" open>'),
  "home Figure details must remain closed",
);
requireBuild(
  available.includes('<details class="analysis" open>'),
  "paper detail Figure section must be open",
);
requireBuild(
  available.includes("Fig. 1 &amp; Fig. 2"),
  "available paper must show the Figure heading",
);
requireBuild(
  countMarkup(available, /<img\b/g) === 2,
  "available fixture must render Fig. 1 and Fig. 2 images",
);
requireBuild(
  countMarkup(available, /<button\b/g) === 2,
  "available fixture must render a download button for each panel",
);
requireBuild(
  available.includes("data-download-name=") &&
    available.includes("查看原图") &&
    available.includes("下载原图"),
  "available fixture must expose original-image and download actions",
);

for (const [arxivId, status, message] of fallbackCases) {
  const page = await html(`papers/${arxivId}/index.html`);
  requireBuild(
    page.includes(`data-figure-status="${status}"`),
    `${arxivId} must expose ${status}`,
  );
  requireBuild(
    page.includes(message),
    `${arxivId} must explain its Figure status`,
  );
  requireBuild(page.includes("查看 PDF"), `${arxivId} must link to the PDF`);
  requireBuild(
    countMarkup(page, /<img\b/g) === 0 && countMarkup(page, /<button\b/g) === 0,
    `${arxivId} must not render image or download controls`,
  );
}

await stat(resolve(dist, "pagefind/pagefind.js"));
