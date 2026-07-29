import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it, vi } from "vitest";

const managedEnvironment = [
  "BASE_PATH",
  "PLAYWRIGHT_CHANNEL",
  "PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH",
  "PLAYWRIGHT_PORT",
] as const;
const originalEnvironment = Object.fromEntries(
  managedEnvironment.map((name) => [name, process.env[name]]),
);
const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../..");

type LoadedConfig = {
  outputDir?: string;
  use?: {
    baseURL?: string;
    browserName?: string;
    channel?: string;
    launchOptions?: { executablePath?: string };
  };
  webServer?: {
    command?: string;
    reuseExistingServer?: boolean;
    url?: string;
  };
};

async function loadConfig(
  environment: Partial<
    Record<(typeof managedEnvironment)[number], string>
  > = {},
): Promise<LoadedConfig> {
  for (const name of managedEnvironment) delete process.env[name];
  Object.assign(process.env, environment);
  vi.resetModules();
  return (await import("../../playwright.config")).default as LoadedConfig;
}

afterEach(() => {
  for (const name of managedEnvironment) {
    const value = originalEnvironment[name];
    if (value === undefined) delete process.env[name];
    else process.env[name] = value;
  }
  vi.resetModules();
});

describe("Playwright server isolation", () => {
  it.each(["", "0", "65536", "1.5", "1e3", "abc", " 4321 "])(
    "rejects invalid PLAYWRIGHT_PORT=%j",
    async (port) => {
      await expect(loadConfig({ PLAYWRIGHT_PORT: port })).rejects.toThrow(
        /PLAYWRIGHT_PORT/,
      );
    },
  );

  it.each([
    "../papers",
    "/papers?mode=test",
    "/papers#fragment",
    "https://example.com/papers",
    "/papers//nested",
    "/papers\\nested",
    "/papers/%2f",
  ])("rejects unsafe BASE_PATH=%j", async (basePath) => {
    await expect(loadConfig({ BASE_PATH: basePath })).rejects.toThrow(
      /BASE_PATH/,
    );
  });

  it("uses one strict Vite preview URL for baseURL and webServer", async () => {
    const config = await loadConfig({
      BASE_PATH: "/vla-wam-daily/",
      PLAYWRIGHT_PORT: "4567",
    });

    expect(config.use?.baseURL).toBe("http://127.0.0.1:4567/vla-wam-daily/");
    expect(config.webServer).toMatchObject({
      command:
        "pnpm exec vite preview --host 127.0.0.1 --port 4567 --strictPort --outDir dist --base /vla-wam-daily/",
      reuseExistingServer: false,
      url: config.use?.baseURL,
    });
    expect(config.outputDir).toContain("4567");
    expect(config.outputDir).toContain("vla-wam-daily");
  });
});

describe("Playwright browser selection", () => {
  it("uses Playwright-managed Chromium by default", async () => {
    const config = await loadConfig();

    expect(config.use).toMatchObject({ browserName: "chromium" });
    expect(config.use?.channel).toBeUndefined();
    expect(config.use?.launchOptions).toBeUndefined();
  });

  it("uses only explicitly configured browser overrides", async () => {
    const channelConfig = await loadConfig({ PLAYWRIGHT_CHANNEL: "chrome" });
    expect(channelConfig.use?.channel).toBe("chrome");

    const executableConfig = await loadConfig({
      PLAYWRIGHT_CHANNEL: "chrome",
      PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH: "/opt/chromium",
    });
    expect(executableConfig.use?.channel).toBeUndefined();
    expect(executableConfig.use?.launchOptions).toEqual({
      executablePath: "/opt/chromium",
    });
  });

  it("does not expose an origin override escape hatch", async () => {
    const source = await readFile(
      resolve(webRoot, "playwright.config.ts"),
      "utf8",
    );

    expect(source).not.toContain("PLAYWRIGHT_ORIGIN");
  });
});
