import { figureDownloadFilename } from "./figure-download";

const cachedFigurePathPattern =
  /^\/figures\/\d{4}\.\d{4,5}\/v[1-9]\d*\/fig[12]-panel[1-9]\d*\.(png|jpg|webp|gif|svg)$/;
const cachedFigureDownloadPathPattern =
  /(?:^|\/)figures\/(\d{4}\.\d{4,5})\/v([1-9]\d*)\/fig([12])-panel([1-9]\d*)\.(png|jpg|webp|gif|svg)$/;
const basePathPattern = /^\/(?:[^/]+\/)*$/;

export interface FigurePanelSource {
  displayUrl: string;
  downloadUrl: string;
  originalUrl: string | null;
  isLocal: boolean;
}

export function figurePanelDownloadFilename(input: {
  arxivId: string;
  version: number;
  figure: number;
  panel: number;
  source: FigurePanelSource;
}): string {
  const { arxivId, version, figure, panel, source } = input;
  if (!source.isLocal) {
    if (source.originalUrl === null) {
      throw new Error("Remote Figure panel requires an original URL");
    }
    return figureDownloadFilename({
      arxivId,
      version,
      figure,
      panel,
      imageUrl: source.originalUrl,
    });
  }

  const cachedIdentity = cachedFigureDownloadPathPattern.exec(
    new URL(source.downloadUrl, "https://local.invalid").pathname,
  );
  if (
    !cachedIdentity ||
    cachedIdentity[1] !== arxivId ||
    Number.parseInt(cachedIdentity[2] ?? "", 10) !== version ||
    Number.parseInt(cachedIdentity[3] ?? "", 10) !== figure ||
    Number.parseInt(cachedIdentity[4] ?? "", 10) !== panel
  ) {
    throw new Error("Invalid cached Figure download identity");
  }
  const extension = cachedIdentity[5];
  if (!extension) throw new Error("Invalid cached Figure download extension");
  return `${arxivId}-v${version}-fig${figure}-panel${panel}.${extension.toLowerCase()}`;
}

export function resolveFigurePanelSource(input: {
  originalUrl: string | null;
  cachedPath: string | null | undefined;
  basePath: string;
}): FigurePanelSource {
  const { originalUrl, cachedPath, basePath } = input;
  if (!basePathPattern.test(basePath)) {
    throw new Error("Invalid GitHub Pages base path");
  }
  if (cachedPath === null || cachedPath === undefined) {
    if (originalUrl === null) {
      throw new Error("Figure panel requires a remote or cached source");
    }
    return {
      displayUrl: originalUrl,
      downloadUrl: originalUrl,
      originalUrl,
      isLocal: false,
    };
  }
  if (!cachedFigurePathPattern.test(cachedPath)) {
    throw new Error("Invalid cached Figure path");
  }
  const localUrl =
    basePath === "/" ? cachedPath : `${basePath.slice(0, -1)}${cachedPath}`;
  return {
    displayUrl: localUrl,
    downloadUrl: localUrl,
    originalUrl,
    isLocal: true,
  };
}
