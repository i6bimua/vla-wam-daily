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

function countElementsWithAttribute(source, tagName, attribute) {
  const elements = source.match(new RegExp(`<${tagName}\\b[^>]*>`, "gi")) ?? [];
  const attributePattern = new RegExp(`\\s${attribute}(?:\\s|=|\\/?>)`, "i");
  return elements.filter((element) => attributePattern.test(element)).length;
}

const home = await html("index.html");
const available = await html("papers/2607.12345/index.html");
const fallbackCases = [
  [
    "2607.20001",
    "html_unavailable",
    "arXiv 暂未提供 HTML 版本，无法提取 Fig. 1 / Fig. 2。",
    false,
  ],
  ["2607.20002", "not_found", "在 arXiv HTML 中未找到 Fig. 1 / Fig. 2。", true],
  [
    "2607.20003",
    "fetch_failed",
    "本次获取 Fig. 1 / Fig. 2 失败，论文其它内容仍可正常阅读。",
    true,
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
  home.includes("data-figure-preview") &&
    home.includes("/figures/2607.12345/v1/fig1-panel1.svg"),
  "home must render the cached Figure 1 preview",
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
  countElementsWithAttribute(available, "img", "data-figure-image") === 2,
  "available fixture must render Fig. 1 and Fig. 2 images",
);
requireBuild(
  countElementsWithAttribute(available, "button", "data-figure-download") === 1,
  "available fixture must retain one remote fallback download button",
);
requireBuild(
  countElementsWithAttribute(available, "div", "data-figure-panel") === 2,
  "available fixture must render exactly two Figure panels",
);
requireBuild(
  (available.match(/<figure\b[^>]*class="remote-figure"/g)?.length ?? 0) ===
    2 && (available.match(/<figcaption\b/g)?.length ?? 0) === 2,
  "available fixture must use figure and figcaption semantics",
);
requireBuild(
  available.includes("data-download-name=") &&
    available.includes("查看 arXiv 原图") &&
    available.includes("下载本站缓存") &&
    available.includes("下载原图"),
  "available fixture must expose cached, original-image, and fallback download actions",
);
requireBuild(
  !available.includes('aria-label="查看 Figure 1 面板 1/1 原图"') &&
    available.includes('aria-label="下载 Figure 1 面板 1/1 原图"') &&
    available.includes('aria-label="查看 Figure 1 面板 1/1 对应的论文 PDF"') &&
    available.includes('aria-label="查看 Figure 2 面板 1/1 原图"') &&
    available.includes('aria-label="下载 Figure 2 面板 1/1 原图"'),
  "available fixture actions must distinguish local-only and remote panels",
);
requireBuild(
  available.includes('download="2607.12345-v1-fig1-panel1.svg"'),
  "cached fixture must preserve its local download extension",
);

for (const [arxivId, status, message, showsHtmlLink] of fallbackCases) {
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
    countElementsWithAttribute(page, "img", "data-figure-image") === 0 &&
      countElementsWithAttribute(page, "button", "data-figure-download") ===
        0 &&
      countElementsWithAttribute(page, "div", "data-figure-panel") === 0,
    `${arxivId} must not render Figure images, panels, or download controls`,
  );
  requireBuild(
    page.includes('class="figure-gallery__html-link"') === showsHtmlLink,
    `${arxivId} HTML link visibility must match Figure status`,
  );
}

await stat(resolve(dist, "pagefind/pagefind.js"));
await stat(resolve(dist, "figures/2607.12345/v1/fig1-panel1.svg"));
