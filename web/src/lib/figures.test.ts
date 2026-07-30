import { describe, expect, it } from "vitest";
import { resolveFigurePanelSource } from "./figures";

const originalUrl = "https://arxiv.org/html/2607.12345v1/x1.png";

describe("Figure panel source resolution", () => {
  it("prefers a cached path beneath the GitHub Pages base path", () => {
    expect(
      resolveFigurePanelSource({
        originalUrl,
        cachedPath: "/figures/2607.12345/v1/fig1-panel1.png",
        basePath: "/vla-wam-daily/",
      }),
    ).toEqual({
      displayUrl: "/vla-wam-daily/figures/2607.12345/v1/fig1-panel1.png",
      downloadUrl: "/vla-wam-daily/figures/2607.12345/v1/fig1-panel1.png",
      originalUrl,
      isLocal: true,
    });
  });

  it.each([undefined, null])(
    "falls back to arXiv when the cached path is %s",
    (cachedPath) => {
      expect(
        resolveFigurePanelSource({
          originalUrl,
          cachedPath,
          basePath: "/vla-wam-daily/",
        }),
      ).toEqual({
        displayUrl: originalUrl,
        downloadUrl: originalUrl,
        originalUrl,
        isLocal: false,
      });
    },
  );

  it("normalizes the root Pages base path", () => {
    expect(
      resolveFigurePanelSource({
        originalUrl,
        cachedPath: "/figures/2607.12345/v1/fig1-panel1.svg",
        basePath: "/",
      }).displayUrl,
    ).toBe("/figures/2607.12345/v1/fig1-panel1.svg");
  });

  it.each([
    "figures/2607.12345/v1/fig1-panel1.png",
    "/other/2607.12345/v1/fig1-panel1.png",
    "/figures/../../secret.png",
    "https://example.com/image.png",
  ])("rejects an unsafe cached path: %s", (cachedPath) => {
    expect(() =>
      resolveFigurePanelSource({
        originalUrl,
        cachedPath,
        basePath: "/vla-wam-daily/",
      }),
    ).toThrow(/cached Figure path/i);
  });

  it.each(["", "vla-wam-daily", "//vla-wam-daily//"])(
    "rejects an invalid Pages base path: %s",
    (basePath) => {
      expect(() =>
        resolveFigurePanelSource({
          originalUrl,
          cachedPath: "/figures/2607.12345/v1/fig1-panel1.png",
          basePath,
        }),
      ).toThrow(/base path/i);
    },
  );
});
