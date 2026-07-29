import { describe, expect, it } from "vitest";
import { formatBeijingTimestamp } from "./time";

describe("formatBeijingTimestamp", () => {
  it("formats the generated instant as a complete Asia/Shanghai timestamp", () => {
    expect(formatBeijingTimestamp("2026-07-29T02:30:00Z")).toBe(
      "2026-07-29 10:30:00 北京时间",
    );
    expect(formatBeijingTimestamp("2026-12-31T18:15:20Z")).toBe(
      "2027-01-01 02:15:20 北京时间",
    );
  });

  it("rejects an invalid timestamp", () => {
    expect(() => formatBeijingTimestamp("not-a-date")).toThrow(TypeError);
  });
});
