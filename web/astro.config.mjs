import { defineConfig } from "astro/config";

function normalizeBasePath(value) {
  const trimmed = value.trim();
  if (!trimmed || trimmed === "/") return "/";
  if (
    trimmed.includes("?") ||
    trimmed.includes("#") ||
    trimmed.includes("..")
  ) {
    throw new Error("BASE_PATH must be a plain absolute URL path");
  }
  return `/${trimmed.replace(/^\/+|\/+$/g, "")}/`;
}

function repositoryCoordinates(value) {
  if (!value) return null;
  const match = /^([^/\s]+)\/([^/\s]+)$/.exec(value.trim());
  if (!match) {
    throw new Error("GITHUB_REPOSITORY must have the form owner/repository");
  }
  return { owner: match[1], repository: match[2] };
}

const coordinates = repositoryCoordinates(process.env.GITHUB_REPOSITORY);
const configuredSite = process.env.SITE_URL
  ? new URL(process.env.SITE_URL)
  : new URL(
      coordinates
        ? `https://${coordinates.owner}.github.io/`
        : "http://localhost:4321/",
    );
const repositoryBase =
  coordinates &&
  coordinates.repository.toLowerCase() !==
    `${coordinates.owner.toLowerCase()}.github.io`
    ? `/${coordinates.repository}/`
    : "/";
const base = normalizeBasePath(
  process.env.BASE_PATH ??
    (process.env.SITE_URL ? configuredSite.pathname : repositoryBase),
);

export default defineConfig({
  site: configuredSite.origin,
  base,
  output: "static",
  trailingSlash: "always",
});
