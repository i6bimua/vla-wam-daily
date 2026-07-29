import { describe, expect, it } from "vitest";
import type { FilterState } from "./filter";
import {
  buildPagefindFilters,
  createRetryableLoader,
  loadPagefindResultBatch,
  resolvePagefindResultHref,
  SEARCH_RESULT_BATCH_SIZE,
} from "./search";

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

describe("loadPagefindResultBatch", () => {
  it("loads only one 20-result batch and isolates individual data failures", async () => {
    const calls: number[] = [];
    const results = Array.from({ length: 45 }, (_, index) => ({
      data: async () => {
        calls.push(index);
        if (index === 3) throw new Error("broken result");
        return { index };
      },
    }));

    const batch = await loadPagefindResultBatch(results, 0);

    expect(SEARCH_RESULT_BATCH_SIZE).toBe(20);
    expect(calls).toEqual(Array.from({ length: 20 }, (_, index) => index));
    expect(batch.values).toEqual([
      ...Array.from({ length: 3 }, (_, index) => ({ index })),
      ...Array.from({ length: 16 }, (_, index) => ({ index: index + 4 })),
    ]);
    expect(batch.failedCount).toBe(1);
    expect(batch.nextOffset).toBe(20);
    expect(batch.totalCount).toBe(45);
    expect(batch.hasMore).toBe(true);
  });

  it("loads the next slice without revisiting earlier results", async () => {
    const calls: number[] = [];
    const results = Array.from({ length: 45 }, (_, index) => ({
      data: async () => {
        calls.push(index);
        return index;
      },
    }));

    const batch = await loadPagefindResultBatch(results, 20);

    expect(calls).toEqual(Array.from({ length: 20 }, (_, index) => index + 20));
    expect(batch.values).toEqual(calls);
    expect(batch.nextOffset).toBe(40);
    expect(batch.hasMore).toBe(true);
  });

  it("isolates a result whose data loader throws synchronously", async () => {
    const batch = await loadPagefindResultBatch(
      [
        {
          data: () => {
            throw new Error("synchronous failure");
          },
        },
        { data: async () => "usable result" },
      ],
      0,
    );

    expect(batch.values).toEqual(["usable result"]);
    expect(batch.failedCount).toBe(1);
    expect(batch.hasMore).toBe(false);
  });
});

describe("createRetryableLoader", () => {
  it("clears a rejected cached promise so the next call can retry", async () => {
    let attempts = 0;
    const load = createRetryableLoader(async () => {
      attempts += 1;
      if (attempts === 1) throw new Error("temporary import failure");
      return "ready";
    });

    await expect(load()).rejects.toThrow("temporary import failure");
    await expect(load()).resolves.toBe("ready");
    expect(attempts).toBe(2);
  });

  it("shares one in-flight successful load", async () => {
    let attempts = 0;
    const load = createRetryableLoader(async () => {
      attempts += 1;
      return "ready";
    });

    await expect(Promise.all([load(), load()])).resolves.toEqual([
      "ready",
      "ready",
    ]);
    expect(attempts).toBe(1);
  });
});
