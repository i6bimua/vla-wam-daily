import { describe, expect, it } from "vitest";
import * as figureDownload from "./figure-download";
import {
  extensionForFigureUrl,
  figureDownloadFilename,
  isTrustedArxivImageUrl,
  readTrustedArxivImageResponse,
} from "./figure-download";

const trustedImageUrl = "https://arxiv.org/html/2607.12345v2/x1.png";

function streamedResponse(options: {
  chunks: number[][];
  contentLength?: number;
  contentType?: string;
  status?: number;
  url?: string;
}) {
  let cancelled = false;
  let index = 0;
  let reads = 0;
  const stream = new ReadableStream<Uint8Array>(
    {
      pull(controller) {
        reads += 1;
        const chunk = options.chunks[index];
        index += 1;
        if (chunk) controller.enqueue(Uint8Array.from(chunk));
        else controller.close();
      },
      cancel() {
        cancelled = true;
      },
    },
    { highWaterMark: 0 },
  );
  const headers = new Headers({
    "content-type": options.contentType ?? "image/png",
  });
  if (options.contentLength !== undefined) {
    headers.set("content-length", String(options.contentLength));
  }
  const response = new Response(stream, {
    headers,
    status: options.status ?? 200,
  });
  Object.defineProperty(response, "url", {
    value: options.url ?? trustedImageUrl,
  });
  Object.defineProperty(response, "blob", {
    value: () => {
      throw new Error("response.blob() must not buffer Figure downloads");
    },
  });
  return {
    response,
    readCount: () => reads,
    wasCancelled: () => cancelled,
  };
}

describe("Figure download metadata", () => {
  it("exposes a bounded trusted image response reader", () => {
    expect(
      (
        figureDownload as typeof figureDownload & {
          readTrustedArxivImageResponse?: unknown;
        }
      ).readTrustedArxivImageResponse,
    ).toBeTypeOf("function");
  });

  it("streams a small trusted image into a Blob without response.blob()", async () => {
    const { response } = streamedResponse({
      chunks: [
        [1, 2],
        [3, 4],
      ],
      contentLength: 4,
    });

    const blob = await readTrustedArxivImageResponse(response, {
      arxivId: "2607.12345",
      version: 2,
    });

    expect(blob.type).toBe("image/png");
    expect([...new Uint8Array(await blob.arrayBuffer())]).toEqual([1, 2, 3, 4]);
  });

  it.each([
    "https://cdn.example.com/html/2607.12345v2/x1.png",
    "https://arxiv.org/html/2607.12345v3/x1.png",
  ])("rejects an untrusted final response URL: %s", async (url) => {
    const { response } = streamedResponse({
      chunks: [[1]],
      contentLength: 1,
      url,
    });

    await expect(
      readTrustedArxivImageResponse(response, {
        arxivId: "2607.12345",
        version: 2,
      }),
    ).rejects.toThrow(/trusted arXiv Figure URL/i);
  });

  it("rejects an oversized declared Content-Length before reading", async () => {
    const { response, readCount } = streamedResponse({
      chunks: [[1, 2, 3, 4, 5]],
      contentLength: 5,
    });

    await expect(
      readTrustedArxivImageResponse(
        response,
        { arxivId: "2607.12345", version: 2 },
        4,
      ),
    ).rejects.toThrow(/size limit/i);
    expect(readCount()).toBe(0);
  });

  it("cancels a stream that exceeds the limit without Content-Length", async () => {
    const { response, wasCancelled } = streamedResponse({
      chunks: [[1, 2, 3], [4, 5, 6], [7]],
    });

    await expect(
      readTrustedArxivImageResponse(
        response,
        { arxivId: "2607.12345", version: 2 },
        5,
      ),
    ).rejects.toThrow(/size limit/i);
    expect(wasCancelled()).toBe(true);
  });

  it.each([
    ["an unsuccessful response", { status: 404 }],
    ["non-image response", { contentType: "text/html" }],
  ])("rejects %s", async (_description, overrides) => {
    const { response, readCount } = streamedResponse({
      chunks: [[1]],
      contentLength: 1,
      ...overrides,
    });

    await expect(
      readTrustedArxivImageResponse(response, {
        arxivId: "2607.12345",
        version: 2,
      }),
    ).rejects.toThrow(/successful image/i);
    expect(readCount()).toBe(0);
  });

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
