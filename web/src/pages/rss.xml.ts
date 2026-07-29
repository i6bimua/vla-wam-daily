import rss from "@astrojs/rss";
import type { APIContext } from "astro";
import { loadArchive } from "../lib/data";
import { createRssItems } from "../lib/rss";

export async function GET(context: APIContext): Promise<Response> {
  if (!context.site) {
    throw new TypeError("RSS generation requires Astro site configuration");
  }
  const base = import.meta.env.BASE_URL;
  const feedSite = new URL(base, context.site);
  return rss({
    title: "VLA/WAM Daily",
    description: "每日精选视觉语言动作、世界动作模型与机器人世界模型研究。",
    site: feedSite,
    customData: "<language>zh-CN</language>",
    items: createRssItems(await loadArchive(), context.site, base),
  });
}
