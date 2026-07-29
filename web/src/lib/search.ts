import {
  parseFilterState,
  serializeFilterState,
  type FilterState,
} from "./filter";

export type PagefindFilterValue = string | { any: string[] };

export function buildPagefindFilters(
  state: FilterState,
): Record<string, PagefindFilterValue> {
  const canonical = parseFilterState(`?${serializeFilterState(state)}`);
  const filters: Record<string, PagefindFilterValue> = {
    score: {
      any: Array.from({ length: 11 - canonical.minimumScore }, (_, index) =>
        String(index + canonical.minimumScore),
      ),
    },
  };
  if (canonical.topics.length) {
    filters.topic = { any: canonical.topics };
  }
  if (canonical.code) filters.code = canonical.code;
  if (canonical.date) filters.date = canonical.date;
  return filters;
}

function normalizeBasePath(value: string): string | null {
  if (value === "/") return value;
  if (
    !value.startsWith("/") ||
    !value.endsWith("/") ||
    value.includes("//") ||
    value.includes("\\") ||
    value.includes("?") ||
    value.includes("#") ||
    value.split("/").some((segment) => segment === "." || segment === "..")
  ) {
    return null;
  }
  return value;
}

export function resolvePagefindResultHref(
  rawUrl: string,
  origin: string,
  basePath: string,
): string | null {
  const base = normalizeBasePath(basePath);
  if (!base || !rawUrl || rawUrl.trim() !== rawUrl) return null;

  let site: URL;
  let result: URL;
  try {
    site = new URL(origin);
    result = new URL(rawUrl, site.origin);
  } catch {
    return null;
  }
  if (
    !["http:", "https:"].includes(site.protocol) ||
    site.username ||
    site.password ||
    result.origin !== site.origin ||
    result.username ||
    result.password ||
    result.search ||
    result.hash
  ) {
    return null;
  }

  const doubleBase = base === "/" ? null : `${base}${base.slice(1)}`;
  if (doubleBase && result.pathname.startsWith(doubleBase)) return null;

  let relativePath: string;
  if (base !== "/" && result.pathname.startsWith(base)) {
    relativePath = result.pathname.slice(base.length);
  } else if (result.pathname.startsWith("/papers/")) {
    relativePath = result.pathname.slice(1);
  } else if (base === "/" && result.pathname.startsWith("/")) {
    relativePath = result.pathname.slice(1);
  } else {
    return null;
  }

  const match = /^papers\/(\d{4}\.\d{4,5})\/?$/.exec(relativePath);
  return match ? `${base}papers/${match[1]}/` : null;
}
