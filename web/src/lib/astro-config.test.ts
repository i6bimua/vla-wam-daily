import { randomUUID } from "node:crypto";
import { afterEach, describe, expect, it } from "vitest";

const originalEnvironment = {
  SITE_URL: process.env.SITE_URL,
  BASE_PATH: process.env.BASE_PATH,
  GITHUB_REPOSITORY: process.env.GITHUB_REPOSITORY,
  VLA_WAM_PUBLIC_DIR: process.env.VLA_WAM_PUBLIC_DIR,
};

async function importConfig(siteUrl: string) {
  process.env.SITE_URL = siteUrl;
  delete process.env.BASE_PATH;
  delete process.env.GITHUB_REPOSITORY;
  const url = new URL("../../astro.config.mjs", import.meta.url);
  url.searchParams.set("test", randomUUID());
  return import(url.href);
}

afterEach(() => {
  for (const [name, value] of Object.entries(originalEnvironment)) {
    if (value === undefined) delete process.env[name];
    else process.env[name] = value;
  }
});

describe("SITE_URL validation", () => {
  it.each([
    "ftp://papers.example/research",
    "https://user:secret@papers.example/research",
    "https://papers.example/research?preview=1",
    "https://papers.example/research?",
    "https://papers.example/research#preview",
    "https://papers.example/research#",
  ])("rejects an unsafe site URL: %s", async (siteUrl) => {
    await expect(importConfig(siteUrl)).rejects.toThrow(/SITE_URL/);
  });

  it("accepts an HTTPS path and uses it as the normalized base", async () => {
    const { default: config } = await importConfig(
      "https://papers.example/research",
    );

    expect(config.site).toBe("https://papers.example");
    expect(config.base).toBe("/research/");
  });

  it("uses an isolated public directory for fixture builds", async () => {
    process.env.VLA_WAM_PUBLIC_DIR = "../tests/fixtures/public";

    const { default: config } = await importConfig(
      "https://papers.example/research",
    );

    expect(config.publicDir).toBe("../tests/fixtures/public");
  });
});
