import { describe, expect, it } from "vitest";
import {
  extensionForFigureUrl,
  figureDownloadFilename,
  isTrustedArxivImageUrl,
} from "./figure-download";

describe("Figure download metadata", () => {
  it.each([
    ["x.PNG", ".png"],
    ["x.jpeg", ".jpeg"],
    ["x.svg", ".svg"],
    ["x.webp", ".webp"],
    ["x.avif", ".avif"],
  ])("uses a conservative extension for %s", (path, expected) => {
    expect(
      extensionForFigureUrl(`https://arxiv.org/html/2607.12345v2/${path}`),
    ).toBe(expected);
  });

  it.each([
    "https://arxiv.org/html/2607.12345v2/figure",
    "https://arxiv.org/html/2607.12345v2/figure.html",
    "https://arxiv.org/html/2607.12345v2/figure.png.exe",
    "https://arxiv.org/html/2607.12345v2/figure.png%2Ehtml",
  ])("falls back for a missing or unsafe extension: %s", (url) => {
    expect(extensionForFigureUrl(url)).toBe(".img");
  });

  it("ignores query text when selecting the extension", () => {
    expect(
      extensionForFigureUrl(
        "https://arxiv.org/html/2607.12345v2/figure.png?filename=attack.html",
      ),
    ).toBe(".png");
  });

  it("builds a stable filename from validated identity fields", () => {
    expect(
      figureDownloadFilename({
        arxivId: "2607.12345",
        version: 2,
        figure: 1,
        panel: 3,
        imageUrl: "https://arxiv.org/html/2607.12345v2/x1.PNG?download=.html",
      }),
    ).toBe("2607.12345-v2-fig1-panel3.png");
  });

  it.each([
    "http://arxiv.org/html/2607.12345v2/x1.png",
    "https://example.com/html/2607.12345v2/x1.png",
    "https://user:secret@arxiv.org/html/2607.12345v2/x1.png",
    "https://arxiv.org:444/html/2607.12345v2/x1.png",
    "https://arxiv.org/html/2607.12345v2/x1.png#payload",
    "https://arxiv.org/html/2607.12345v1/x1.png",
  ])("rejects an untrusted or mismatched Figure URL: %s", (url) => {
    expect(isTrustedArxivImageUrl(url, "2607.12345", 2)).toBe(false);
    expect(() =>
      figureDownloadFilename({
        arxivId: "2607.12345",
        version: 2,
        figure: 1,
        panel: 1,
        imageUrl: url,
      }),
    ).toThrow();
  });

  it("rejects invalid paper and panel identities", () => {
    expect(() =>
      figureDownloadFilename({
        arxivId: "../paper",
        version: 0,
        figure: 3,
        panel: 0,
        imageUrl: "https://arxiv.org/html/2607.12345v2/x1.png",
      }),
    ).toThrow();
  });
});
