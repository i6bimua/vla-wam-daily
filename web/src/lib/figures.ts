import { figureDownloadFilename } from "./figure-download";

const cachedFigurePathPattern =
  /^\/figures\/\d{4}\.\d{4,5}\/v[1-9]\d*\/fig[12]-panel[1-9]\d*\.(png|jpg|webp|gif|svg)$/;
const basePathPattern = /^\/(?:[^/]+\/)*$/;

export interface FigurePanelSource {
  displayUrl: string;
  downloadUrl: string;
  originalUrl: string;
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
  const remoteFilename = figureDownloadFilename({
    arxivId,
    version,
    figure,
    panel,
    imageUrl: source.originalUrl,
  });
  if (!source.isLocal) return remoteFilename;

  const extension = /\.(png|jpg|webp|gif|svg)$/i.exec(source.downloadUrl)?.[0];
  if (!extension) {
    throw new Error("Invalid cached Figure download extension");
  }
  return remoteFilename.replace(/\.[a-z0-9]+$/i, extension.toLowerCase());
}

export function resolveFigurePanelSource(input: {
  originalUrl: string;
  cachedPath: string | null | undefined;
  basePath: string;
}): FigurePanelSource {
  const { originalUrl, cachedPath, basePath } = input;
  if (!basePathPattern.test(basePath)) {
    throw new Error("Invalid GitHub Pages base path");
  }
  if (cachedPath === null || cachedPath === undefined) {
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
