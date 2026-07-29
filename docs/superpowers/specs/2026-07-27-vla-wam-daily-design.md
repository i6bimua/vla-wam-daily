# VLA/WAM Daily 设计规格

日期：2026-07-27  
状态：已复核通过

## 1. 目标

构建一个无需常驻服务器的公开研究门户，每天从 arXiv 收集与
Vision-Language-Action（VLA）和 World Action Model（WAM）相关的论文，
使用 DeepSeek 对标题和摘要做结构化中文分析，并通过 GitHub Pages 发布。

首版成功标准：

- 每天北京时间 10:30 自动运行，也能手动运行。
- 覆盖 VLA、WAM、机器人世界模型、latent/video action model、通用机器人策略，
  以及直接相关的数据集和评测。
- 保留英文原始信息，同时提供中文标题、中文一句话总结和结构化分析。
- 提供主题筛选、日期归档、中英文搜索、RSS 和 Weekly Top 5。
- 对通过发布阈值的论文解析 arXiv HTML，远程展示可用的 Fig. 1 / Fig. 2 和 caption。
- 不使用数据库、常驻服务器或上游作者的第三方代理服务。
- 单篇失败不产生伪造内容；整批失败不覆盖已经部署的正常网站。
- DeepSeek API 密钥只存放在 GitHub Actions Secrets 中。

## 2. 代码来源与许可证

项目创建为独立新仓库，不直接 fork 或复制
`monologg/nlp-arxiv-daily`。该仓库在设计时没有明确许可证，不适合作为可修改和
再分发的代码底座。

新项目自行实现 Python 数据管线和 Astro 前端。README 可以将
`monologg/nlp-arxiv-daily`、`dw-dengwei/daily-arXiv-ai-enhanced`、
`20bytes/vlm-arxiv-daily` 和 `Vincentqyw/cv-arxiv-daily` 列为调研或灵感来源，
但不复制无明确许可的源码。

新仓库默认采用 MIT License。

## 3. 系统架构

系统由三个独立部分组成：

1. Python 数据管线：抓取、预筛、AI 分析、校验、缓存和归档。
2. Astro 静态站：读取版本化 JSON，生成页面、搜索索引和 RSS。
3. GitHub Actions：运行测试、每日更新和 GitHub Pages 部署。

建议目录：

```text
vla-wam-daily/
├── pipeline/
│   ├── arxiv_client.py
│   ├── prefilter.py
│   ├── deepseek_client.py
│   ├── analyzer.py
│   ├── figures.py
│   ├── resources.py
│   ├── schema.py
│   ├── storage.py
│   └── cli.py
├── config/
│   └── topics.yaml
├── prompts/
│   └── analysis-v1.md
├── data/
│   ├── latest.json
│   └── archive/
│       └── YYYY-MM.json
├── web/
├── tests/
└── .github/workflows/
```

模块边界：

- `arxiv_client` 只负责 arXiv 查询、节流和标准化元数据。
- `prefilter` 只执行确定性规则，不调用模型，也不决定最终发布。
- `deepseek_client` 只负责认证、请求、重试和 JSON 响应。
- `analyzer` 负责构造分析输入、验证评分语义和生成分析记录。
- `figures` 只解析通过发布阈值论文的 arXiv HTML，返回 Figure URL、caption 和状态，
  不下载或保存图片字节。
- `resources` 只提取论文元数据中可以验证的项目或代码 URL。
- `schema` 定义数据契约和枚举。
- `storage` 负责缓存、版本更新、月度归档和原子写入。
- `cli` 组合各模块，并提供每日运行、预览和历史回填入口。

## 4. 每日数据流

1. 查询 `cs.RO`、`cs.CV`、`cs.AI` 和 `cs.LG` 的近期论文。
2. 以规范化 arXiv ID 和版本去重。
3. 执行本地关键词与组合规则预筛。
4. 检查缓存；版本、模型和 Prompt 均相同的记录不再调用 DeepSeek。
5. 使用 DeepSeek 对候选论文做结构化分析。
6. 相关性评分通过仓库默认或本次配置的发布阈值后，才请求 arXiv HTML 并解析
   Fig. 1 / Fig. 2；Figure 失败只生成降级状态，不阻塞论文发布。
7. 发布达到本次阈值的论文。
8. 验证完整数据集后，原子更新 `latest.json`、Figure 元数据缓存和月度归档。
9. 生成 Astro 网站、Pagefind 索引和 RSS。
10. 所有检查成功后，提交数据并部署 GitHub Pages。

每日任务是幂等的。相同论文和相同版本重复运行不会创建重复记录，也不会产生重复
AI 费用。

## 5. 本地预筛

第一层预筛用于降低 API 调用量，不直接决定论文是否发布。

完整概念规则包括：

- `vision-language-action`
- `vision language action`
- `world action model`
- `world-action model`
- `latent action model`
- `video action model`
- `action-conditioned world model`
- `generalist robot policy`
- `robot foundation model`
- `multimodal robot policy`

组合规则包括：

- `vision-language` 或 `VLM`，同时出现 `robot`、`policy`、`action` 或
  `manipulation`。
- `world model` 或 `video model`，同时出现 `robot`、`action`、
  `manipulation` 或 `control`。
- `generalist` 或 `foundation`，同时出现 `robot policy`。

不得用孤立的 `VLA` 或 `WAM` 缩写作为命中条件。规则匹配大小写不敏感，并对连字符、
空格和常见复数形式做规范化。

默认每次最多把 60 篇新候选论文发送给 DeepSeek。超过上限时记录告警并停止更新，
而不是任意截断并部署不完整结果。

## 6. DeepSeek 分析

默认配置：

- Base URL：`https://api.deepseek.com`
- 模型：`deepseek-v4-pro`
- 运行档位：`quality`
- 思考模式：关闭
- 响应格式：JSON Output
- API 密钥环境变量：`DEEPSEEK_API_KEY`

可选 `economy` 档使用 `deepseek-v4-flash`。`DEEPSEEK_MODEL` 可以覆盖默认模型，
但模型名必须写入结果的 provenance。

每篇论文的分析输入只包含标题、摘要、arXiv 分类和命中的预筛规则。Prompt 必须：

- 明确要求输出 JSON。
- 提供完整 JSON 示例。
- 将分类和标签限制为允许的枚举。
- 禁止补写摘要未提供的数字、局限、代码或项目地址。
- 要求未知信息输出 `摘要未说明` 或 `null`。

DeepSeek 输出字段：

- `relevance_score`：1 到 10 的整数。
- `primary_topic`：`VLA`、`WAM`、`World Model`、`Dataset` 或
  `Benchmark`。
- `tags`：受控标签数组。
- `title_zh`
- `one_sentence_summary`
- `main_contribution`
- `method`
- `key_results`
- `limitations`
- `relation_to_vla_wam`

评分解释：

| 分数 | 含义 |
| --- | --- |
| 9–10 | 明确以 VLA、WAM 或机器人动作世界模型为核心 |
| 7–8 | 强相关方法、数据集、评测或通用机器人策略 |
| 6 | 对该方向有直接价值的相邻研究 |
| 1–5 | 主题过远，不在公开页面展示 |

仓库默认/示例发布阈值是 6，可通过配置、CLI 和手动工作流输入修改。
DataFile 不持久化本次实际运行阈值，因此已构建页面不能据此宣称当前运行阈值；
页面只应展示每篇论文 provenance 中实际记录的模型和 Prompt 版本，并把 6 描述为
仓库默认/示例值。

## 7. 数据契约

顶层数据文件带有 `schema_version`、`generated_at`、运行统计和论文数组。单篇记录形状：

```json
{
  "arxiv_id": "2607.xxxxx",
  "version": 1,
  "published_at": "2026-07-27T00:00:00Z",
  "updated_at": "2026-07-27T00:00:00Z",
  "title": "Original English title",
  "title_zh": "中文标题",
  "authors": ["Author One", "Author Two"],
  "arxiv_categories": ["cs.RO", "cs.CV"],
  "abstract": "Original English abstract",
  "matched_rules": ["vision-language + robot policy"],
  "analysis": {
    "relevance_score": 8,
    "primary_topic": "WAM",
    "tags": ["Robot Manipulation", "Video Model"],
    "one_sentence_summary": "中文一句话总结",
    "main_contribution": "中文核心贡献",
    "method": "中文方法",
    "key_results": "摘要报告的结果，或“摘要未说明”",
    "limitations": "摘要报告的局限，或“摘要未说明”",
    "relation_to_vla_wam": "中文关系说明"
  },
  "resources": {
    "arxiv_url": "https://arxiv.org/abs/2607.xxxxx",
    "pdf_url": "https://arxiv.org/pdf/2607.xxxxx",
    "project_url": null,
    "code_url": null
  },
  "figure_gallery": {
    "status": "available",
    "html_url": "https://arxiv.org/html/2607.xxxxxv1",
    "figures": [
      {
        "number": 1,
        "label": "Figure 1",
        "caption": "Original English caption.",
        "image_urls": [
          "https://arxiv.org/html/2607.xxxxxv1/x1.png"
        ],
        "source_url": "https://arxiv.org/html/2607.xxxxxv1#S1.F1",
        "source": "arxiv_html"
      }
    ],
    "checked_at": "2026-07-27T00:00:00Z"
  },
  "provenance": {
    "analysis_scope": "title_and_abstract",
    "model": "deepseek-v4-pro",
    "prompt_version": "1",
    "analyzed_at": "2026-07-27T00:00:00Z"
  }
}
```

缓存键由 `arxiv_id + version + model + prompt_version` 组成。arXiv 新版本会重新分析，
当前页面展示最新版本，归档保留旧版本的可追溯记录。升级模型或 Prompt 不自动重算全部
历史数据；历史回填由显式 CLI 或手动工作流触发。

`code_url` 和 `project_url` 只接受从 arXiv 元数据中的明确 URL 提取并完成基本 URL
校验的结果，不能由模型生成或猜测。

`figure_gallery` 只保存 arXiv HTML URL、图片 URL、caption、状态和检查时间，不保存
图片字节。Figure 缓存键是 `arxiv_id + version`；正缓存长期复用，三种负状态
`html_unavailable`、`not_found`、`fetch_failed` 在 24 小时后可重试。

## 8. 网站信息架构

站点品牌：

> VLA/WAM Daily  
> Tracking Vision-Language-Action and World Action Models

主导航：

- Today
- VLA
- WAM
- World Models
- Datasets
- Benchmarks
- Weekly Top 5
- Archive
- RSS

论文卡片默认展示：

- 英文标题和中文标题
- 作者、发布日期和 arXiv 分类
- 主分类、标签和相关性评分
- 中文一句话总结
- arXiv、PDF、Project 和 Code 链接
- “AI 分析仅基于标题与摘要”标识

可展开区域展示核心贡献、方法、实验结果、局限和与 VLA/WAM 的关系。
若 arXiv HTML 可用，可展开区域还以远程方式展示 Fig. 1 / Fig. 2、多面板原图和
英文 caption；图片或 HTML 不可用时提供 HTML/PDF 降级链接。

搜索覆盖中英文标题、英文摘要、作者、标签和中文分析。筛选支持日期、主分类、
相关性分数和代码状态。筛选状态写入 URL 查询参数，刷新或分享链接后保持不变。

页面默认先按日期降序、再按相关性降序排列。站点支持暗色模式、移动端布局、静态 SEO、
月度归档和 RSS。

arXiv API 不可靠地提供作者机构，因此摘要级首版不展示机构信息。

Weekly Top 5 不额外调用模型。它从当周高分论文中确定性选取，并限制同一主分类最多两个
名额，保证主题多样性。相同输入必须产生相同榜单。

About/Methodology 页面公开：

- 数据来源和更新时间。
- 预筛规则和评分解释。
- 数据中实际记录的模型与 Prompt 版本，以及仓库默认/示例发布阈值；由于
  `DataFile` 不持久化本次实际运行阈值，不能从静态数据证明某次运行的阈值。
- 摘要级 AI 分析的局限。
- Figure 来自 arXiv 远程资源、不保存图片字节，以及版权和论文许可证提示。
- 问题反馈和项目仓库链接。

## 9. GitHub Actions 与部署

### `ci.yml`

在主分支 push 和 pull request 时运行：

- Python 格式、静态检查和单元测试。
- 数据 Schema 契约测试。
- 前端格式、类型检查和测试。
- Astro 生产构建。

### `daily.yml`

在 `30 2 * * *` UTC（北京时间 10:30）运行，也支持 `workflow_dispatch`。

手动输入支持：

- 回看天数。
- `quality` 或 `economy` 档。
- 发布阈值。
- 指定 arXiv ID 强制重新分析。
- dry-run，只生成报告而不提交和部署。

非 dry-run 的成功流程：

1. 抓取和分析。
2. 校验数据及运行质量门槛。
3. 提交 `data/` 变更。
4. 构建 Pages artifact。
5. 部署到 `github-pages` environment。

### `pages.yml`

人工修改前端、配置或既有数据后构建并部署，不调用 DeepSeek。

GitHub Actions 权限按 job 最小化。只有更新数据的 job 获得 `contents: write`，
部署 job 获得 `pages: write` 和 `id-token: write`。日志不得输出 API 密钥、完整请求头
或其他 Secret。

## 10. 错误处理与可观测性

- arXiv 请求使用明确 User-Agent、请求间隔和指数退避。
- DeepSeek 对超时、429 和临时服务错误做有上限的退避重试。
- JSON 解析和 Schema 校验失败时，该论文不发布，并保留可诊断的错误类别。
- 单篇失败不会中断其他论文；失败项在下次运行继续处理。
- 新论文分析失败比例超过 30% 时，整个数据更新失败。
- 所有目标数据先写临时文件，完整校验通过后再原子替换。
- 测试、数据校验或 Astro 构建失败时不部署，线上站点保持上一版。
- 运行摘要记录抓取数、预筛数、缓存命中数、模型调用数、发布数、失败数、
  Token 用量和错误类别。
- Figure 可观测性使用稳定字段 `figure_cache_hits`、`figure_requests`、
  `figure_available`、`figure_unavailable`、`figure_failed`；Figure 失败不计入
  DeepSeek 的 30% 分析失败阈值。
- “arXiv 正常返回零篇新论文”和“arXiv 请求失败”必须是不同状态。

## 11. 测试策略

Python 单元测试覆盖：

- 关键词规范化与组合规则。
- 去重和版本更新。
- 缓存键和 Prompt 版本。
- DeepSeek 正常 JSON、空响应、截断 JSON、429、超时和重试。
- 评分范围、分类枚举和未知字段处理。
- URL 提取和校验。
- Figure 真实节点解析、图号/caption 绑定、多面板、远程 URL allowlist、缓存和
  三种降级状态。
- 月度归档、原子写入和 Schema 迁移。

离线集成测试使用固定 arXiv Feed 和 DeepSeek 响应 fixture，完整运行
`fetch → prefilter → analyze → store`，不访问网络。

前端测试覆盖：

- JSON 数据契约。
- 首页和论文卡片渲染。
- 中英文搜索。
- 主题、日期、分数和代码筛选。
- URL 筛选状态恢复。
- 归档、Weekly Top 5 和 RSS。
- 桌面与移动端浏览器冒烟测试。
- Astro 生产构建。
- Fig. 1 / Fig. 2 展开、远程图片加载、Blob 下载、CORS 失败后打开原图、PDF
  降级和移动端可访问性。

## 12. 非目标

首版不包含：

- PDF 全文下载和全文级总结。
- PDF Figure 截图、论文图片字节托管或绕过论文许可证的再分发。
- 邮件、微信或 Slack 推送。
- 用户登录、云端收藏或个性化推荐账户。
- 作者机构推断。
- 由模型猜测代码仓库、项目页或实验数字。
- 常驻后端、数据库或管理后台。
- 自动重算全部历史论文。

这些功能只有在每日管线稳定并积累实际使用反馈后才考虑。

## 13. 验收标准

首版完成时必须满足：

1. 使用固定 fixture 可以离线生成有效的月度数据和完整网站。
2. 使用测试用 DeepSeek 密钥手动运行时，能抓取、预筛、分析并发布至少一篇样本论文。
3. 相同输入重复运行不会增加论文记录或重复调用模型。
4. 失败和无新论文两种情况均不会破坏线上站点。
5. GitHub Pages 可访问，并能完成搜索、筛选、归档、RSS 和移动端浏览。
6. 页面明确披露分析范围、模型、更新时间和数据限制。
7. README 包含本地运行、Secrets、Pages 设置、手动触发和故障排查说明。
8. 通过发布阈值的论文会得到严格校验的 Figure Gallery；有图时能看到正确的
   Fig. 1 / Fig. 2、caption 和多面板，无图或抓取失败时不阻塞论文发布。
9. Figure 下载优先使用浏览器 Blob，CORS 或网络失败时打开 arXiv 原图；仓库、
   Pages artifact 和数据文件均不保存论文图片字节，页面披露来源、版权和许可证限制。
