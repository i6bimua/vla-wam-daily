import { defineConfig } from "@playwright/test";
import { tmpdir } from "node:os";
import { resolve } from "node:path";

function normalizeBasePath(value: string | undefined): string {
  if (!value || value === "/") return "/";
  const path = value.startsWith("/") ? value.slice(1) : value;
  const unwrapped = path.endsWith("/") ? path.slice(0, -1) : path;
  const segments = unwrapped.split("/");
  if (
    !segments.length ||
    segments.some(
      (segment) =>
        !segment ||
        segment === "." ||
        segment === ".." ||
        !/^[A-Za-z0-9._~-]+$/.test(segment),
    )
  ) {
    throw new TypeError("BASE_PATH must be a safe URL path");
  }
  return `/${segments.join("/")}/`;
}

function parsePort(value: string | undefined): number {
  const raw = value ?? "4321";
  if (!/^[0-9]+$/.test(raw)) {
    throw new TypeError("PLAYWRIGHT_PORT must be an integer from 1 to 65535");
  }
  const port = Number(raw);
  if (!Number.isInteger(port) || port < 1 || port > 65_535) {
    throw new TypeError("PLAYWRIGHT_PORT must be an integer from 1 to 65535");
  }
  return port;
}

const port = parsePort(process.env.PLAYWRIGHT_PORT);
const basePath = normalizeBasePath(process.env.BASE_PATH);
const baseURL = `http://127.0.0.1:${port}${basePath}`;
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH;
const channel = process.env.PLAYWRIGHT_CHANNEL;
const browserOverride = executablePath
  ? { launchOptions: { executablePath } }
  : channel
    ? { channel }
    : {};
const outputBase = basePath === "/" ? "root" : basePath.slice(1, -1);

export default defineConfig({
  testDir: "./tests",
  outputDir: resolve(
    tmpdir(),
    `vla-wam-daily-playwright-${port}-${outputBase.replaceAll("/", "-")}`,
  ),
  fullyParallel: false,
  use: {
    baseURL,
    browserName: "chromium",
    ...browserOverride,
    trace: "retain-on-failure",
  },
  webServer: {
    command: `pnpm exec vite preview --host 127.0.0.1 --port ${port} --strictPort --outDir dist --base ${basePath}`,
    url: baseURL,
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
