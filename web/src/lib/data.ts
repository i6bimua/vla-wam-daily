import { readdir, readFile } from "node:fs/promises";
import { isAbsolute, join, resolve } from "node:path";
import type { ZodError } from "zod";
import { dataFileSchema, type DataFile, type Paper } from "./schema";

const archiveNamePattern = /^\d{4}-(0[1-9]|1[0-2])\.json$/;

export class DataLoadError extends Error {
  override readonly name = "DataLoadError";

  constructor(message: string, options?: ErrorOptions) {
    super(message, options);
  }
}

export function resolveDataDir(
  configuredPath = process.env.VLA_WAM_DATA_DIR,
  baseDirectory = process.cwd(),
): string {
  if (configuredPath === undefined) {
    return resolve(baseDirectory, "../data");
  }
  const trimmed = configuredPath.trim();
  if (!trimmed || trimmed.includes("\0")) {
    throw new DataLoadError(
      "Data directory override must be a non-empty filesystem path",
    );
  }
  return isAbsolute(trimmed)
    ? resolve(trimmed)
    : resolve(baseDirectory, trimmed);
}

export const defaultDataDir = resolveDataDir();

function describeSchemaError(error: ZodError): string {
  return error.issues
    .slice(0, 3)
    .map((issue) => `${issue.path.join(".") || "<root>"}: ${issue.message}`)
    .join("; ");
}

async function loadDataFile(path: string, label: string): Promise<DataFile> {
  let contents: string;
  try {
    contents = await readFile(path, "utf8");
  } catch (error) {
    throw new DataLoadError(`${label} is missing or unreadable: ${path}`, {
      cause: error,
    });
  }

  let raw: unknown;
  try {
    raw = JSON.parse(contents);
  } catch (error) {
    throw new DataLoadError(`${label} contains invalid JSON: ${path}`, {
      cause: error,
    });
  }

  const result = dataFileSchema.safeParse(raw);
  if (!result.success) {
    throw new DataLoadError(
      `${label} failed schema validation: ${path} (${describeSchemaError(result.error)})`,
      { cause: result.error },
    );
  }
  return result.data;
}

function isPreferredVersion(candidate: Paper, current: Paper): boolean {
  if (candidate.version !== current.version) {
    return candidate.version > current.version;
  }
  const candidateUpdated = Date.parse(candidate.updated_at);
  const currentUpdated = Date.parse(current.updated_at);
  if (candidateUpdated !== currentUpdated) {
    return candidateUpdated > currentUpdated;
  }
  const candidateAnalyzed = Date.parse(candidate.provenance.analyzed_at);
  const currentAnalyzed = Date.parse(current.provenance.analyzed_at);
  if (candidateAnalyzed !== currentAnalyzed) {
    return candidateAnalyzed > currentAnalyzed;
  }
  return JSON.stringify(candidate) > JSON.stringify(current);
}

export function selectCurrentPapers(papers: readonly Paper[]): Paper[] {
  const newest = new Map<string, Paper>();
  for (const paper of papers) {
    const current = newest.get(paper.arxiv_id);
    if (!current || isPreferredVersion(paper, current)) {
      newest.set(paper.arxiv_id, paper);
    }
  }
  return [...newest.values()].sort(
    (left, right) =>
      Date.parse(right.published_at) - Date.parse(left.published_at) ||
      right.analysis.relevance_score - left.analysis.relevance_score ||
      left.arxiv_id.localeCompare(right.arxiv_id) ||
      right.version - left.version,
  );
}

export async function loadArchive(dataDir = defaultDataDir): Promise<Paper[]> {
  const archiveDirectory = join(resolve(dataDir), "archive");
  let entries;
  try {
    entries = await readdir(archiveDirectory, { withFileTypes: true });
  } catch (error) {
    throw new DataLoadError(
      `Archive directory is missing or unreadable: ${archiveDirectory}`,
      {
        cause: error,
      },
    );
  }

  const jsonEntries = entries.filter((entry) => entry.name.endsWith(".json"));
  const invalidEntry = jsonEntries.find(
    (entry) => !entry.isFile() || !archiveNamePattern.test(entry.name),
  );
  if (invalidEntry) {
    throw new DataLoadError(
      `Archive contains an unsafe or invalid JSON file name: ${invalidEntry.name}`,
    );
  }

  const papers: Paper[] = [];
  for (const entry of jsonEntries.sort((left, right) =>
    left.name.localeCompare(right.name),
  )) {
    const path = join(archiveDirectory, entry.name);
    const archive = await loadDataFile(path, `Archive file ${entry.name}`);
    papers.push(...archive.papers);
  }
  return selectCurrentPapers(papers);
}

export async function loadLatestDataFile(
  dataDir = defaultDataDir,
): Promise<DataFile> {
  const path = join(resolve(dataDir), "latest.json");
  return loadDataFile(path, "Latest data file");
}

export async function loadLatest(dataDir = defaultDataDir): Promise<Paper[]> {
  return selectCurrentPapers((await loadLatestDataFile(dataDir)).papers);
}
