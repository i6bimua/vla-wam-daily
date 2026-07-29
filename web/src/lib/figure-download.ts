const arxivIdPattern = /^\d{4}\.\d{4,5}$/;
const allowedHosts = new Set(["arxiv.org", "www.arxiv.org"]);
const safeExtensions = new Set([
  "png",
  "jpg",
  "jpeg",
  "gif",
  "webp",
  "svg",
  "avif",
]);

export const MAX_FIGURE_DOWNLOAD_BYTES = 20 * 1024 * 1024;

export interface TrustedImageResponseIdentity {
  arxivId: string;
  version: number;
}

export interface FigureDownloadIdentity {
  arxivId: string;
  version: number;
  figure: number;
  panel: number;
  imageUrl: string;
}

async function rejectImageResponse(
  response: Response,
  error: Error,
): Promise<never> {
  try {
    await response.body?.cancel(error);
  } catch {
    // Cleanup must not replace the validation error that caused the rejection.
  }
  throw error;
}

export async function readTrustedArxivImageResponse(
  response: Response,
  identity: TrustedImageResponseIdentity,
  maxBytes = MAX_FIGURE_DOWNLOAD_BYTES,
): Promise<Blob> {
  if (!Number.isSafeInteger(maxBytes) || maxBytes < 1) {
    return rejectImageResponse(
      response,
      new Error("Figure download size limit must be a positive integer"),
    );
  }
  if (
    !isTrustedArxivImageUrl(response.url, identity.arxivId, identity.version)
  ) {
    return rejectImageResponse(
      response,
      new Error("Figure response did not use a trusted arXiv Figure URL"),
    );
  }
  const contentType =
    response.headers
      .get("content-type")
      ?.split(";", 1)[0]
      ?.trim()
      .toLowerCase() ?? "";
  if (!response.ok || !contentType.startsWith("image/")) {
    return rejectImageResponse(
      response,
      new Error("Figure response is not a successful image"),
    );
  }
  const contentLength = response.headers.get("content-length")?.trim();
  if (
    contentLength &&
    /^\d+$/.test(contentLength) &&
    BigInt(contentLength) > BigInt(maxBytes)
  ) {
    return rejectImageResponse(
      response,
      new Error("Figure response exceeds the download size limit"),
    );
  }
  if (!response.body) {
    return rejectImageResponse(
      response,
      new Error("Figure response has no readable body"),
    );
  }

  const reader = response.body.getReader();
  const chunks: ArrayBuffer[] = [];
  let receivedBytes = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      receivedBytes += value.byteLength;
      if (receivedBytes > maxBytes) {
        const error = new Error(
          "Figure response exceeds the download size limit",
        );
        try {
          await reader.cancel(error);
        } catch {
          // Preserve the size-limit error if the underlying stream also fails.
        }
        throw error;
      }
      const ownedChunk = new Uint8Array(value.byteLength);
      ownedChunk.set(value);
      chunks.push(ownedChunk.buffer);
    }
  } finally {
    reader.releaseLock();
  }
  const blob = new Blob(chunks, { type: contentType });
  if (!blob.type.toLowerCase().startsWith("image/")) {
    throw new Error("Figure Blob is not an image");
  }
  return blob;
}

export function isTrustedArxivImageUrl(
  value: string,
  arxivId: string,
  version: number,
): boolean {
  if (
    !arxivIdPattern.test(arxivId) ||
    !Number.isInteger(version) ||
    version < 1
  ) {
    return false;
  }
  try {
    const url = new URL(value);
    const paperPrefix = `/html/${arxivId}v${version}/`;
    return (
      url.protocol === "https:" &&
      allowedHosts.has(url.hostname) &&
      !url.username &&
      !url.password &&
      !url.port &&
      !url.hash &&
      url.pathname.startsWith(paperPrefix) &&
      url.pathname.length > paperPrefix.length
    );
  } catch {
    return false;
  }
}

export function extensionForFigureUrl(value: string): string {
  const path = new URL(value).pathname;
  const match = /\.([a-z0-9]+)$/i.exec(path);
  const candidate = match?.[1]?.toLowerCase();
  return candidate && safeExtensions.has(candidate) ? `.${candidate}` : ".img";
}

export function figureDownloadFilename(
  identity: FigureDownloadIdentity,
): string {
  const { arxivId, version, figure, panel, imageUrl } = identity;
  if (
    !isTrustedArxivImageUrl(imageUrl, arxivId, version) ||
    ![1, 2].includes(figure) ||
    !Number.isInteger(panel) ||
    panel < 1
  ) {
    throw new Error("Invalid Figure download identity");
  }
  return `${arxivId}-v${version}-fig${figure}-panel${panel}${extensionForFigureUrl(imageUrl)}`;
}
