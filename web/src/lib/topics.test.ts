import { describe, expect, it } from "vitest";
import { TOPIC_ROUTES, topicRouteBySlug } from "./topics";

describe("TOPIC_ROUTES", () => {
  it("is the single complete mapping for the five public topic routes", () => {
    expect(TOPIC_ROUTES.map(({ slug, topic }) => [slug, topic])).toEqual([
      ["vla", "VLA"],
      ["wam", "WAM"],
      ["world-model", "World Model"],
      ["dataset", "Dataset"],
      ["benchmark", "Benchmark"],
    ]);
    expect(new Set(TOPIC_ROUTES.map((route) => route.slug)).size).toBe(5);
    expect(new Set(TOPIC_ROUTES.map((route) => route.topic)).size).toBe(5);
  });

  it("looks up known slugs and rejects unknown routes", () => {
    expect(topicRouteBySlug("world-model")?.topic).toBe("World Model");
    expect(topicRouteBySlug("unknown")).toBeUndefined();
  });
});
