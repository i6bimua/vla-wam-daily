import { expect, test, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";

const onePixelPng = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
  "base64",
);

async function routeFigureImages(page: Page): Promise<void> {
  await page.route("https://arxiv.org/html/**", async (route) => {
    await route.fulfill({
      status: 200,
      headers: {
        "access-control-allow-origin": "*",
        "content-length": String(onePixelPng.byteLength),
        "content-type": "image/png",
      },
      body: onePixelPng,
    });
  });
}

test("desktop home exposes research cards and live filters", async ({
  page,
}) => {
  await page.goto(".");

  await expect(
    page.getByRole("link", { name: "VLA/WAM Daily 首页" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: /把机器人前沿/ }),
  ).toBeVisible();
  await expect(page.locator("[data-result-count]")).toHaveText("4");

  await page.getByLabel("最低相关性").selectOption("8");

  await expect(page.locator("[data-result-count]")).toHaveText("1");
  await expect(page.locator("[data-paper-card]:visible")).toHaveCount(1);
  await expect(page).toHaveURL(/score=8/);
});

test("filter state survives URL load and reload", async ({ page }) => {
  await page.goto("?topic=VLA&score=7");

  await expect(page.getByRole("checkbox", { name: "VLA" })).toBeChecked();
  await expect(page.getByLabel("最低相关性")).toHaveValue("7");
  await expect(page.locator("[data-result-count]")).toHaveText("1");

  await page.reload();

  await expect(page.getByRole("checkbox", { name: "VLA" })).toBeChecked();
  await expect(page.getByLabel("最低相关性")).toHaveValue("7");
  await expect(page.locator("[data-result-count]")).toHaveText("1");
});

test("Pagefind searches English and Chinese paper content", async ({
  page,
}) => {
  await page.goto("search/?q=vision");

  await expect(page.locator("[data-search-status]")).toContainText("已展示");
  await expect(page.locator("[data-search-results]")).toContainText(
    "A Vision-Language-Action Policy",
  );

  await page.getByRole("searchbox", { name: "全文搜索" }).fill("视觉语言动作");
  await page.getByRole("button", { name: "搜索" }).click();

  await expect(page.locator("[data-search-status]")).toContainText("已展示");
  await expect(page.locator("[data-search-results] a")).toContainText(
    "用于机器人操作的视觉语言动作策略",
  );
});

test("mobile navigation and paper detail remain usable", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await routeFigureImages(page);
  await page.goto(".");

  const navigation = page.getByRole("navigation", { name: "主导航" });
  await expect(navigation).toBeVisible();
  await expect(navigation.getByRole("link", { name: "搜索" })).toBeVisible();

  await page.locator('[data-paper-card][data-id="2607.12345"] h2 a').click();

  await expect(page.getByRole("heading", { level: 1 })).toContainText(
    "用于机器人操作的视觉语言动作策略",
  );
  await expect(page.getByRole("link", { name: "PDF" })).toBeVisible();
});

test("paper detail immediately renders remote Figure 1 and Figure 2", async ({
  page,
}) => {
  await routeFigureImages(page);
  await page.goto("papers/2607.12345/");

  await expect(page.locator("details.analysis")).toHaveAttribute("open", "");
  await expect(
    page.getByRole("heading", { name: "Fig. 1 & Fig. 2" }),
  ).toBeVisible();
  await expect(page.getByText("The model architecture.")).toBeVisible();
  await expect(page.getByText("Robot evaluation environments.")).toBeVisible();
  await expect(page.locator("[data-figure-image]")).toHaveCount(2);
});

test("download button saves a routed arXiv image with a stable name", async ({
  page,
}) => {
  await routeFigureImages(page);
  await page.goto("papers/2607.12345/");

  const downloadPromise = page.waitForEvent("download");
  await page
    .getByRole("button", { name: "下载 Figure 1 面板 1/1 原图" })
    .click();
  const download = await downloadPromise;

  expect(download.suggestedFilename()).toBe("2607.12345-v1-fig1-panel1.png");
  expect(await download.failure()).toBeNull();
  const downloadPath = await download.path();
  expect(downloadPath).not.toBeNull();
  const downloadedBytes = await readFile(downloadPath!);
  expect(downloadedBytes.subarray(0, 8)).toEqual(
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
  );
  expect(downloadedBytes).toEqual(onePixelPng);
});

test("broken remote images expose the PDF fallback", async ({ page }) => {
  await page.route("https://arxiv.org/html/**", (route) =>
    route.abort("failed"),
  );
  await page.goto("papers/2607.12345/");
  await page.locator(".figure-gallery__grid").scrollIntoViewIfNeeded();

  await expect(
    page.getByText("该面板暂时无法从 arXiv 加载。").first(),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: /查看 PDF/ }).first(),
  ).toBeVisible();
});

test("figure gallery becomes one column on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await routeFigureImages(page);
  await page.goto("papers/2607.12345/");

  const gallery = page.locator(".figure-gallery__grid");
  await gallery.scrollIntoViewIfNeeded();
  await expect(gallery).toHaveCSS("grid-template-columns", /^\d+(\.\d+)?px$/);
  const leftEdges = await page
    .locator(".remote-figure")
    .evaluateAll((nodes) =>
      nodes.map((node) => Math.round(node.getBoundingClientRect().left)),
    );
  expect(new Set(leftEdges).size).toBe(1);
});

for (const [id, message] of [
  ["2607.20001", "arXiv 暂未提供 HTML 版本，无法提取 Fig. 1 / Fig. 2。"],
  ["2607.20002", "在 arXiv HTML 中未找到 Fig. 1 / Fig. 2。"],
  ["2607.20003", "本次获取 Fig. 1 / Fig. 2 失败，论文其它内容仍可正常阅读。"],
] as const) {
  test(`Figure fallback for ${id}`, async ({ page }) => {
    await page.goto(`papers/${id}/`);

    await expect(page.getByText(message)).toBeVisible();
    await expect(page.getByRole("link", { name: /查看 PDF/ })).toBeVisible();
  });
}
