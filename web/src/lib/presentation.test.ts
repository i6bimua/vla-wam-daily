import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const sourceRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");

function channelToLinear(value: number): number {
  const normalized = value / 255;
  return normalized <= 0.04045
    ? normalized / 12.92
    : ((normalized + 0.055) / 1.055) ** 2.4;
}

function luminance(hex: string): number {
  const channels = hex
    .slice(1)
    .match(/.{2}/g)
    ?.map((channel) => channelToLinear(Number.parseInt(channel, 16)));
  if (!channels || channels.length !== 3) {
    throw new Error(`Invalid six-digit hex color: ${hex}`);
  }
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
}

function contrastRatio(foreground: string, background: string): number {
  const lighter = Math.max(luminance(foreground), luminance(background));
  const darker = Math.min(luminance(foreground), luminance(background));
  return (lighter + 0.05) / (darker + 0.05);
}

function lightThemeToken(css: string, name: string): string {
  const lightTheme = /:root\s*\{(?<body>[\s\S]*?)\}/.exec(css)?.groups?.body;
  const value = new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{6})`).exec(
    lightTheme ?? "",
  )?.[1];
  if (!value) throw new Error(`Missing light theme token --${name}`);
  return value;
}

describe("site presentation contracts", () => {
  it("discovers the future RSS feed through a base-safe URL", async () => {
    const layout = await readFile(
      resolve(sourceRoot, "layouts/BaseLayout.astro"),
      "utf8",
    );

    expect(layout).toContain("const base = import.meta.env.BASE_URL;");
    expect(layout).toMatch(
      /<link\s+rel="alternate"\s+type="application\/rss\+xml"\s+title="VLA\/WAM Daily RSS"\s+href=\{`\$\{base\}rss\.xml`\}\s*\/>/,
    );
  });

  it.each(["accent", "muted"])(
    "keeps light-theme --%s small text above a 4.7:1 contrast margin",
    async (token) => {
      const css = await readFile(
        resolve(sourceRoot, "styles/global.css"),
        "utf8",
      );
      const foreground = lightThemeToken(css, token);
      const paper = lightThemeToken(css, "paper");

      expect(contrastRatio(foreground, paper)).toBeGreaterThanOrEqual(4.7);
    },
  );
});

describe("remote Figure component contracts", () => {
  it("prefers cached panels while retaining canonical arXiv originals", async () => {
    const component = await readFile(
      resolve(sourceRoot, "components/FigureGallery.astro"),
      "utf8",
    );

    expect(component).toContain(
      'import { resolveFigurePanelSource } from "../lib/figures";',
    );
    expect(component).toContain("figure.cached_image_paths[index]");
    expect(component).toContain("source.displayUrl");
    expect(component).toContain("source.originalUrl");
    expect(component).toContain("source.isLocal");
    expect(component).toContain("下载本站缓存");
    expect(component).toContain("查看 arXiv 原图");
    expect(component).toMatch(
      /href=\{source\.downloadUrl\}[\s\S]*download=\{filename\}/,
    );
  });

  it("uses privacy-preserving lazy images and safe external links", async () => {
    const component = await readFile(
      resolve(sourceRoot, "components/FigureGallery.astro"),
      "utf8",
    );

    expect(component).toMatch(
      /<img[\s\S]*loading="lazy"[\s\S]*decoding="async"[\s\S]*referrerpolicy="no-referrer"[\s\S]*data-figure-image/,
    );
    expect(component).toContain('target="_blank"');
    expect(component).toContain('rel="noopener noreferrer"');
    expect(component).toContain("查看 arXiv 原图");
    expect(component).toContain("下载原图");
  });

  it("follows redirects then validates and streams the final response", async () => {
    const component = await readFile(
      resolve(sourceRoot, "components/FigureGallery.astro"),
      "utf8",
    );

    expect(component).toContain('credentials: "omit"');
    expect(component).toContain('referrerPolicy: "no-referrer"');
    expect(component).toContain('redirect: "follow"');
    expect(component).toContain("readTrustedArxivImageResponse");
    expect(component).toMatch(/finally\s*\{/);
    expect(component).toContain("window.open");
    expect(component).toContain("window.location.assign");
  });

  it("handles cached broken images after binding the error listener", async () => {
    const component = await readFile(
      resolve(sourceRoot, "components/FigureGallery.astro"),
      "utf8",
    );

    expect(component).toContain("function markFigureImageFailed");
    expect(component).toMatch(
      /image\.addEventListener\("error",\s*\(\)\s*=>\s*markFigureImageFailed\(image\)\)/,
    );
    expect(component).toMatch(
      /image\.complete\s*&&\s*image\.naturalWidth\s*===\s*0[\s\S]*markFigureImageFailed\(image\)/,
    );
  });

  it("reserves stable media space and explicitly hides failed images", async () => {
    const css = await readFile(
      resolve(sourceRoot, "styles/global.css"),
      "utf8",
    );

    expect(css).toMatch(
      /\.remote-figure__media\s*\{[^}]*aspect-ratio:\s*4\s*\/\s*3/s,
    );
    expect(css).toMatch(
      /\.remote-figure__media img\s*\{[^}]*width:\s*100%[^}]*height:\s*100%[^}]*object-fit:\s*contain/s,
    );
    expect(css).toMatch(
      /\.remote-figure__media img\[hidden\]\s*\{[^}]*display:\s*none/s,
    );
  });

  it("uses Figure semantics and panel-specific accessible action names", async () => {
    const component = await readFile(
      resolve(sourceRoot, "components/FigureGallery.astro"),
      "utf8",
    );

    expect(component).toContain('<figure class="remote-figure">');
    expect(component).toContain('<figcaption class="remote-figure__caption">');
    expect(component).toMatch(
      /aria-label=\{`查看 \$\{figure\.label\} \$\{panelName\} 原图`\}/,
    );
    expect(component).toMatch(
      /aria-label=\{`下载 \$\{figure\.label\} \$\{panelName\} 原图`\}/,
    );
  });

  it("omits the known-unavailable HTML link without adding CORS image mode", async () => {
    const component = await readFile(
      resolve(sourceRoot, "components/FigureGallery.astro"),
      "utf8",
    );

    expect(component).toMatch(
      /gallery\.status\s*!==\s*"html_unavailable"[\s\S]*figure-gallery__html-link/,
    );
    expect(component).not.toMatch(/crossorigin/i);
  });

  it("defines all isolated Figure failure states and PDF fallbacks", async () => {
    const component = await readFile(
      resolve(sourceRoot, "components/FigureGallery.astro"),
      "utf8",
    );

    for (const status of ["html_unavailable", "not_found", "fetch_failed"]) {
      expect(component).toContain(status);
    }
    expect(component).toContain("data-figure-error");
    expect(component).toContain("查看 PDF");
    expect(component).toMatch(/本地缓存[\s\S]*arXiv\s+原图/);
  });
});
