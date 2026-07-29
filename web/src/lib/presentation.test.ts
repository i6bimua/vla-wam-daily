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
