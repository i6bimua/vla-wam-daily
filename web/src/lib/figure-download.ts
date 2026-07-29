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

export interface FigureDownloadIdentity {
  arxivId: string;
  version: number;
  figure: number;
  panel: number;
  imageUrl: string;
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
