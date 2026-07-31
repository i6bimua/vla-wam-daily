import type { Topic } from "./schema";

export interface TopicRoute {
  slug: string;
  topic: Topic;
  navLabel: string;
  title: string;
  description: string;
}

export const TOPIC_ROUTES = [
  {
    slug: "vla",
    topic: "VLA",
    navLabel: "VLA",
    title: "视觉语言动作（VLA）论文",
    description: "追踪视觉、语言与机器人动作统一建模的最新研究。",
  },
  {
    slug: "wam",
    topic: "WAM",
    navLabel: "WAM",
    title: "世界动作模型（WAM）论文",
    description: "追踪显式联合世界状态与机器人动作建模的最新研究。",
  },
  {
    slug: "world-model",
    topic: "World Model",
    navLabel: "世界模型",
    title: "机器人世界模型论文",
    description: "追踪动作条件预测、具身世界建模与机器人视频生成研究。",
  },
  {
    slug: "dataset",
    topic: "Dataset",
    navLabel: "数据集",
    title: "VLA/WAM 数据集",
    description: "汇集训练与评估视觉语言动作系统所需的数据资源。",
  },
  {
    slug: "benchmark",
    topic: "Benchmark",
    navLabel: "基准评测",
    title: "VLA/WAM 基准评测",
    description: "汇集机器人通用策略、世界模型与动作生成的评测研究。",
  },
  {
    slug: "speculative-decoding",
    topic: "Speculative Decoding",
    navLabel: "推测解码",
    title: "推测解码论文",
    description: "追踪草稿模型、验证器与并行生成加速研究。",
  },
  {
    slug: "quantization",
    topic: "Quantization",
    navLabel: "模型量化",
    title: "模型量化论文",
    description: "追踪整数、低比特、权重与激活量化研究。",
  },
] as const satisfies readonly TopicRoute[];

export function topicRouteBySlug(slug: string): TopicRoute | undefined {
  return TOPIC_ROUTES.find((route) => route.slug === slug);
}
