import { describe, expect, it } from "vitest";
import { TOPIC_ROUTES, topicRouteBySlug } from "./topics";

describe("TOPIC_ROUTES", () => {
  it("is the single complete mapping for the seven public topic routes", () => {
    expect(TOPIC_ROUTES.map(({ slug, topic }) => [slug, topic])).toEqual([
      ["vla", "VLA"],
      ["wam", "WAM"],
      ["world-model", "World Model"],
      ["dataset", "Dataset"],
      ["benchmark", "Benchmark"],
      ["speculative-decoding", "Speculative Decoding"],
      ["quantization", "Quantization"],
    ]);
    expect(new Set(TOPIC_ROUTES.map((route) => route.slug)).size).toBe(7);
    expect(new Set(TOPIC_ROUTES.map((route) => route.topic)).size).toBe(7);
  });

  it("looks up known slugs and rejects unknown routes", () => {
    expect(topicRouteBySlug("world-model")?.topic).toBe("World Model");
    expect(topicRouteBySlug("speculative-decoding")?.topic).toBe(
      "Speculative Decoding",
    );
    expect(topicRouteBySlug("quantization")?.topic).toBe("Quantization");
    expect(topicRouteBySlug("unknown")).toBeUndefined();
  });
});
