import { defineConfig } from "@playwright/test";
import { tmpdir } from "node:os";
import { resolve } from "node:path";

function normalizeBasePath(value: string | undefined): string {
  const trimmed = value?.trim() ?? "";
  if (!trimmed || trimmed === "/") return "/";
  return `/${trimmed.replace(/^\/+|\/+$/g, "")}/`;
}

const port = Number(process.env.PLAYWRIGHT_PORT ?? "4321");
const origin =
  process.env.PLAYWRIGHT_ORIGIN?.replace(/\/+$/, "") ??
  `http://127.0.0.1:${port}`;
const baseURL = new URL(normalizeBasePath(process.env.BASE_PATH), origin).href;
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH;

export default defineConfig({
  testDir: "./tests",
  outputDir: resolve(tmpdir(), "vla-wam-daily-playwright-results"),
  fullyParallel: false,
  use: {
    baseURL,
    browserName: "chromium",
    channel: executablePath
      ? undefined
      : (process.env.PLAYWRIGHT_CHANNEL ?? "chrome"),
    launchOptions: executablePath ? { executablePath } : undefined,
    trace: "retain-on-failure",
  },
  webServer: {
    command: `pnpm preview --host 127.0.0.1 --port ${port}`,
    url: baseURL,
    reuseExistingServer: true,
    timeout: 30_000,
  },
});
