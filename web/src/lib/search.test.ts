import { describe, expect, it } from "vitest";
import type { FilterState } from "./filter";
import { buildPagefindFilters, resolvePagefindResultHref } from "./search";

describe("buildPagefindFilters", () => {
  it("builds AND facets with OR values for topics and scores", () => {
    const state: FilterState = {
      query: "robot",
      topics: ["WAM", "VLA"],
      minimumScore: 8,
      code: "yes",
      date: "2026-07-27",
    };

    expect(buildPagefindFilters(state)).toEqual({
      topic: { any: ["VLA", "WAM"] },
      score: { any: ["8", "9", "10"] },
      code: "yes",
      date: "2026-07-27",
    });
  });

  it("omits unset facets but keeps the default publication threshold", () => {
    expect(
      buildPagefindFilters({
        query: "",
        topics: [],
        minimumScore: 6,
        code: "",
        date: "",
      }),
    ).toEqual({
      score: { any: ["6", "7", "8", "9", "10"] },
    });
  });
});

describe("resolvePagefindResultHref", () => {
  const origin = "https://research.example";
  const base = "/vla-wam-daily/";

  it.each([
    ["/papers/2607.12345/", "/vla-wam-daily/papers/2607.12345/"],
    ["/vla-wam-daily/papers/2607.12345/", "/vla-wam-daily/papers/2607.12345/"],
    [
      "https://research.example/vla-wam-daily/papers/2607.12345/",
      "/vla-wam-daily/papers/2607.12345/",
    ],
    ["/papers/2607.12345", "/vla-wam-daily/papers/2607.12345/"],
  ])("maps %s into the project base", (rawUrl, expected) => {
    expect(resolvePagefindResultHref(rawUrl, origin, base)).toBe(expected);
  });

  it("keeps root deployments base-safe", () => {
    expect(
      resolvePagefindResultHref(
        "/papers/2607.12345/",
        "https://research.example",
        "/",
      ),
    ).toBe("/papers/2607.12345/");
  });

  it.each([
    "https://evil.example/papers/2607.12345/",
    "//evil.example/papers/2607.12345/",
    "javascript:alert(1)",
    "/vla-wam-daily/vla-wam-daily/papers/2607.12345/",
    "/about/",
    "/vla-wam-daily/papers/not-an-id/",
    "/vla-wam-daily/papers/2607.12345/?next=javascript:alert(1)",
    "/vla-wam-daily/papers/2607.12345/#section",
  ])("rejects unsafe or non-paper result URL %s", (rawUrl) => {
    expect(resolvePagefindResultHref(rawUrl, origin, base)).toBeNull();
  });
});
