# 推测解码与模型量化主题设计

## 目标

在现有 VLA/WAM Daily 中增加两个可独立收录的研究主题：

- `Speculative Decoding`（推测解码）
- `Quantization`（模型量化）

独立的推测解码或模型量化论文可以达到发布阈值；若论文同时直接服务
VLA、WAM、机器人策略或机器人世界模型，则获得更高的相关性评分。

## 数据来源

arXiv 分类在现有 `cs.RO`、`cs.CV`、`cs.AI`、`cs.LG` 基础上增加 `cs.CL`。
推测解码研究大量出现在计算语言学分类中；跨分类论文仍由现有 arXiv ID 和版本去重。

## 关键词预筛

### 推测解码

直接短语包括：

- `speculative decoding`
- `speculative sampling`
- `assisted decoding`
- `assisted generation`
- `self-speculative decoding`
- `lookahead decoding`

组合规则要求推测语境与生成或推理语境同时出现，例如：

- `speculative` 与 `decoding`、`sampling`、`generation` 或 `inference`
- `draft model`、`drafter` 或 `verifier` 与 `decoding`、`generation` 或
  `language model`

不单独匹配 `SD`，因为它常表示 Stable Diffusion；也不单独匹配 `draft model`，
避免普通草稿或模型描述误报。

### 模型量化

直接短语包括：

- `model quantization`
- `neural network quantization`
- `LLM quantization`
- `VLM quantization`
- `integer quantization`
- `integer-only quantization`
- `post-training quantization`
- `quantization-aware training`
- `weight-only quantization`
- `activation quantization`
- `low-bit quantization`

普通 `quantization`、`quantized`、`INT4`、`INT8` 或 `low-bit` 只在同时出现
`model`、`neural network`、`transformer`、`language model`、`LLM`、`VLM`、
`weights`、`activations` 或 `inference` 等模型语境时匹配。

## 分析 Schema 与评分

`primary_topic` 增加 `Speculative Decoding` 和 `Quantization`。标签增加
`Efficient Inference`、`Speculative Decoding` 和 `Model Quantization`。
旧的五个主题和全部现有标签继续有效，因此历史 JSON 无需迁移。

DeepSeek prompt 升级为 v2，评分规则改为：

- 9–10：VLA、WAM、机器人动作世界模型，或推测解码/量化与这些机器人主题的直接交叉。
- 7–8：推测解码或模型量化是论文核心，但不直接涉及机器人主题。
- 6：对上述主题有明确方法价值的邻近研究。
- 1–5：误匹配、只使用歧义缩写，或没有明确研究价值。

模型仍必须只依据标题和摘要，不得推断未陈述的实验结果。持久化字段
`relation_to_vla_wam` 暂时保留以兼容历史数据；前端标签改为“研究相关性”，
独立效率论文可以明确写“与 VLA/WAM 无直接关系，但对模型推理效率有价值”。

## 缓存与回填

`analysis.prompt_version` 升级为 `"2"`。v1 缓存继续保留，但不会冒充 v2 分析。
Figure 缓存键不依赖 prompt 版本，因此已经下载的 Figure 继续复用。

发布后依次执行 1 天、2 天、3 天回看。每次运行会复用前一次生成的 v2 缓存，
逐步覆盖三天窗口，避免一次产生超过 60 个真实 DeepSeek 请求。最终自动任务仍保持
默认三天回看。

这一回填依赖
[`2026-07-31-uncached-candidate-limit-design.md`](2026-07-31-uncached-candidate-limit-design.md)
中的修复：`analysis.max_candidates` 只限制未缓存、实际需要调用 DeepSeek 的
`pending` 论文。缓存命中不占额度，强制重分析仍计入额度，超过上限时仍在任何模型、
Figure 或数据写入前失败。

## 页面

- 顶部导航增加“推测解码”和“模型量化”。
- 首页和搜索页的主题筛选增加两个主题。
- 新增 `/topics/speculative-decoding/` 与 `/topics/quantization/`。
- 首页说明、方法页、搜索说明、RSS 和归档说明扩展到两个效率主题。
- 站点品牌 `VLA/WAM Daily` 保持不变。
- 现有移动端导航和响应式布局继续使用统一的 `TOPIC_ROUTES` 数据源。

## 测试

### Python

- 明确短语与受约束组合规则能匹配目标论文。
- `SD`、Stable Diffusion、无模型语境的普通量化不会匹配。
- 两个新主题和三个新标签通过严格 Schema。
- prompt v2 包含主题、标签和分层评分规则。
- 总候选超过 60、但未缓存候选不超过 60 时成功。
- 未缓存候选超过 60 时不调用模型、不请求 Figure、不写数据。
- 强制重分析绕过缓存并计入上限。

### Web

- Topic Schema、筛选顺序和两个专题路由一致。
- 顶部导航和页面说明包含两个新主题。
- 历史五主题数据仍能解析和构建。
- 完整 Vitest、Prettier、Astro check/build 和严格 E2E 通过。

### 生产

- CI 与 Pages 部署成功。
- 1 天、2 天、3 天回填依次成功。
- 最终三天运行的 `prefiltered` 可以超过 60，但 `model_calls` 不超过 60。
- 线上首页、两个专题页、搜索和 RSS 返回成功，新增论文可被筛选。
