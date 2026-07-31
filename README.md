# VLA/WAM Daily

面向 Vision-Language-Action（VLA）、World Action Model（WAM）、机器人世界模型、
Speculative Decoding（推测解码）和 Quantization（模型量化）的每日研究门户。项目用
Python 从 arXiv 获取元数据并调用 DeepSeek 做摘要级中文分析，再由 Astro 生成静态网站、
Pagefind 搜索索引和 RSS，通过 GitHub Actions 部署到 GitHub Pages；不需要数据库或
常驻服务器。

## 功能

- 每天通过 arXiv OAI-PMH 抓取 `cs.RO`、`cs.CV`、`cs.AI`、`cs.LG`、`cs.CL`
  元数据，先用完整概念词与受控组合规则做确定性关键词预筛，再做 DeepSeek 相关性评分；
  手动指定论文时仍使用 arXiv 查询 API。
- 展示中英文标题、一句话总结、核心贡献、方法、摘要报告的实验结果与局限，以及与
  VLA/WAM 的关系。
- 提供 Today、VLA、WAM、World Models、Datasets、Benchmarks、
  Speculative Decoding、Quantization、Weekly Top 5、月度归档和 RSS。
- Pagefind 支持中英文全文搜索；页面支持主题、日期、分数、代码状态筛选和移动端布局。
- 按“arXiv HTML → arXiv 源码包 → arXiv PDF 自动裁剪”的顺序恢复 Fig. 1，
  同时从 HTML 识别 Fig. 2、英文 caption 和多 panel 原图；把可用面板永久缓存到
  本站静态资源，首页卡片直接展示 Fig. 1，详情页展示来源并下载 Fig. 1 / Fig. 2。
- 在常规每日运行、未使用 `--force-arxiv-id` 强制重分析时，缓存论文分析与 Figure
  元数据；相同论文版本、模型和 Prompt 不重复产生分析费用。
- 默认回看 3 天只是 arXiv 增量抓取窗口，不是保留期限；所有已发布论文记录和分析按月
  永久保留，历史 Figure 镜像也不会因为离开回看窗口而删除。
- 每日任务、测试、静态构建和 GitHub Pages 发布均由 GitHub Actions 完成。

## 模型与分析边界

`config/topics.yaml` 提供两个 DeepSeek 档位：

| 档位 | 默认模型 | 用途 |
| --- | --- | --- |
| `quality` | `deepseek-v4-pro` | 默认，优先分析质量 |
| `economy` | `deepseek-v4-flash` | 降低每日调用成本 |

可以用环境变量 `DEEPSEEK_MODEL` 覆盖所选档位的模型。实际使用的模型名和 Prompt
版本会写入每篇记录的 provenance。AI 输入只包含标题、摘要、arXiv 分类和命中的预筛
规则；不会读取 PDF 全文，也不会猜测摘要没有提供的实验数字、代码地址、项目地址或
局限。

仓库默认/示例发布阈值是 6，运行时可通过 CLI 或手动工作流输入覆盖。`DataFile`
不保存本次实际运行阈值，因此已构建页面不能仅凭数据文件宣称某次运行使用了哪个阈值。
`analysis.max_candidates` 只限制未命中分析缓存、实际需要调用 DeepSeek 的论文；缓存
命中不会占用该额度。默认每次最多分析 60 篇未缓存候选；超过上限会停止更新，而不是截断
后发布不完整结果。新论文分析失败比例超过 30% 时，整次数据更新失败并保留线上上一版。

失败处理是显式且有上限的：arXiv 和 DeepSeek 对超时、429 和瞬态错误执行有限指数退避；
重试耗尽后记录错误，不会用占位内容覆盖正常数据。无效或不符合 Schema 的 AI 输出绝不发布，
会计入失败且不写入分析缓存，以便下次运行重试。

## 本地开发

### 数据管线

需要 Python 3.13 和 [uv](https://docs.astral.sh/uv/)。以下示例在交互式 shell 中
读取密钥，不把密钥写入命令参数、配置文件或 shell 历史：

```bash
uv python install 3.13
uv sync --frozen
read -rsp "DeepSeek API key: " DEEPSEEK_API_KEY && echo
export DEEPSEEK_API_KEY
uv run vla-wam-daily daily --dry-run
unset DEEPSEEK_API_KEY
```

`dry-run` 仍会查询 arXiv 并调用 DeepSeek，但只输出运行报告，不写入数据、不提交、
也不部署。不要把真实密钥直接写在命令行、`.env`、YAML、Issue 或 Git commit 中；
运行后用 `unset DEEPSEEK_API_KEY` 清理当前 shell。

后端质量门禁：

```bash
uv run pytest
uv run ruff check src tests
uv run mypy
```

### 静态网站

需要 Node.js 24 和 pnpm 11.9.0：

```bash
cd web
npm install --global pnpm@11.9.0
pnpm install --frozen-lockfile
pnpm test
pnpm format:check
pnpm exec playwright install chromium
BASE_PATH=/ VLA_WAM_DATA_DIR=../tests/fixtures/data VLA_WAM_PUBLIC_DIR=../tests/fixtures/public pnpm build
pnpm test:e2e
```

Playwright strict E2E 会独占默认端口 `4321`，自动启动并关闭自己的 Vite preview；
不要同时运行手动 preview，否则 strict port 检查会失败。

如需人工查看构建结果，请等 E2E 结束后，在另一个终端单独运行最后一条预览命令：

```bash
pnpm preview --host 127.0.0.1
```

preview 是前台进程，查看结束后按 `Ctrl-C` 停止。

正常构建时可省略 fixture 环境变量，直接运行 `pnpm build`，网站会读取仓库的
`data/`。`BASE_PATH` 用于本地根路径或 GitHub Pages 项目子路径。GitHub Actions 中
Astro 根据 `GITHUB_REPOSITORY` 推导项目子路径，显式 `BASE_PATH` 仍可覆盖自动推导值。

## 配置

- `config/topics.yaml`：arXiv 分类（含 `cs.CL`）、回看天数、请求间隔、超时与重试
  策略、预筛短语与组合规则、发布阈值、60 篇未缓存候选上限、30% 失败阈值、并发数和
  模型档位。推测解码与模型量化使用完整短语或受控词组组合，避免把 Stable Diffusion、
  拓扑量子化等同名概念误收。默认给 arXiv 请求 60 秒超时、最多 5 次指数退避重试；
  每日范围抓取默认使用更适合增量收割的 OAI-PMH，避免 GitHub 公共 Runner 共享出口
  触发查询 API 的系统级限流。
- `prompts/analysis-v2.md`：DeepSeek 的结构化 JSON Prompt。修改时应同步增加
  `prompt_version`，使缓存和结果 provenance 可追溯。
- `data/latest.json`：最新数据；`data/archive/YYYY-MM.json`：永久月度归档；
  `data/cache/analyses.json` 与 `data/cache/figures.json`：分析和 Figure 元数据缓存。
- `web/public/figures/{arxiv_id}/v{version}/`：已发布论文的本地 Figure 面板镜像；
  文件名固定为 `fig{number}-panel{index}.{ext}`，论文新版本使用独立目录。
- CLI 的 `--lookback-days`、`--profile`、`--threshold`、`--force-arxiv-id` 和
  `--dry-run` 可覆盖本次运行选项。查看完整参数可运行
  `uv run vla-wam-daily daily --help`。

若使用自定义 `data_dir`，请先创建它的父目录。程序只会创建最终一级目录；父目录链
缺失时会安全失败，避免向意外位置写入。

## GitHub Pages 与每日更新

1. 在 GitHub 仓库的 **Settings → Secrets and variables → Actions** 新建 Repository
   Secret `DEEPSEEK_API_KEY`。不要创建明文同名 Variable。
2. 如需覆盖档位模型，可新建 Repository Variable `DEEPSEEK_MODEL`；留空则使用
   `quality` 的 `deepseek-v4-pro` 或手动选择的 `economy` 模型。
3. 在 **Settings → Pages → Build and deployment** 中把 Source 设为
   **GitHub Actions**，并允许仓库 Actions 运行。
4. 首次可在 **Actions → Daily arXiv Update → Run workflow** 用
   `workflow_dispatch` 选择参数。建议先选 `dry_run: true` 检查报告，再执行一次
   非 dry-run。
5. 部署 job 使用 `github-pages` environment。若仓库启用了 environment 审批或
   分支保护，需要允许默认分支部署。

`.github/workflows/daily.yml` 使用 `0 7 * * *` 和 `Asia/Shanghai` 时区，即每天
北京时间 07:00。GitHub 的定时任务可能因平台排队稍晚开始。非 dry-run 成功后，工作流
只提交 `data/` 和 `web/public/figures/`，再构建并发布 Pages；任何测试、数据校验或
构建失败都不会替换线上上一版。`.github/workflows/pages.yml` 在默认分支变化时使用
现有数据与 Figure 镜像重建页面，不调用 DeepSeek。

## Fig. 1 / Fig. 2

Figure 管线只在论文相关性评分通过发布阈值后运行。获取 Fig. 1 / Fig. 2 的固定顺序是
**arXiv HTML → arXiv 源码包 → arXiv PDF 自动裁剪**，每一层都只请求与论文 ID 和
版本完全一致的 arXiv 官方资源。HTML 解析器读取真实的 `figure` / `figcaption`
结构，也支持图片位于 caption Figure 紧邻前置容器的受控布局；源码层接受关系明确的
单一素材及 `overpic` 的本地背景素材；最后才从 PDF 中识别 Figure 1 / Figure 2
caption 与图像区域，以约 300 DPI 生成本地 PNG。精确区域识别失败时，会使用图注锚定
的较大页面区域兜底，但仍受页面、像素和文件大小限制。

源码与 PDF 的恢复面板没有虚构的远程原图 URL，只提供本站缓存下载和论文 PDF；详情页
会分别标注“来源：arXiv 源码包”或“来源：PDF 自动裁剪”。HTML 来源仍提供定位原文和
查看 arXiv 原图。Figure 2 永远不会冒充 Figure 1：首页缺少真实 Fig. 1 时会明确提示
暂不可用，详情页则可继续按真实编号展示已有的 Fig. 2。

通过发布阈值的论文会永久写入月度归档；默认回看 3 天是抓取窗口，不是保留期限。
同步器每次扫描全部月度归档、`latest.json` 和 Figure 元数据缓存，把可用面板写入
`web/public/figures/{arxiv_id}/v{version}/`。镜像范围只包含已发布论文的 Fig. 1 /
Fig. 2，每个响应最大 15 MB；固定的论文、版本、Figure 和 panel 路径既避免重名，也让
已有文件在后续运行直接复用。这个有界策略会随已发布论文增长，但不会自动删除历史版本。

成功的同版本恢复结果会永久复用；`not_found` 或 `fetch_failed` 结果在 24 小时后
重试。解析规则版本升级时，旧的未找到结果会立即重新检查。需要
手动回填全部最新记录、月度归档和 Figure 缓存时，从仓库根目录运行：

```bash
uv run vla-wam-daily sync-figures
```

命令会统一更新 `data/latest.json`、`data/archive/*.json`、
`data/cache/figures.json` 和 `web/public/figures/{arxiv_id}/v{version}/`；重复运行会
直接复用永久缓存，不重写已恢复的非空面板。PDF 文本定位使用 MIT 许可的
`pdfplumber`，裁剪渲染使用 BSD-3-Clause / Apache-2.0 许可的 `pypdfium2`，不引入
AGPL 依赖。

首页与详情页优先从本站缓存加载，并提供“下载本站缓存”；每个 panel 同时保留规范的
arXiv 原图链接与定位原文入口。某个面板镜像失败不会阻断论文发布：页面改用 arXiv
原图，后续每日运行重试。远程降级的“下载原图”会用 `fetch` 获取图片并创建临时 Blob；
如果 CORS、网络或浏览器策略阻止 Blob 下载，页面会降级为新标签打开 arXiv 原图。

Figure Gallery 的 Fig. 1 恢复状态包括 `not_attempted`、`available`、
`not_found` 和 `fetch_failed`。`not_found` 表示三层官方来源均没有达到安全置信度；
`fetch_failed` 表示网络、服务或解析暂时失败，页面仍提供 PDF，并在 24 小时后重试。
底层 HTML 请求仍保留 `html_unavailable` 状态，用于区分 arXiv 尚未生成 HTML 的情况。

本地镜像只改善页面可用性，不改变内容权利：Figure 图片版权仍归论文作者或其他
权利人所有，本站源码的 MIT License 不覆盖这些图片。镜像或 arXiv 远程展示都不等于
授予新的转载权限；查看、下载或复用前，请遵循论文许可证及论文页面标注的许可证。
详情见 [arXiv Permissions and Reuse](https://info.arxiv.org/help/license/reuse.html)
和 [arXiv License Information](https://info.arxiv.org/help/license/index.html)。

## 数据来源与项目来源

论文标题、摘要、作者、分类和版本来自 [arXiv API](https://info.arxiv.org/help/api/)，
Figure URL 与 caption 优先来自对应论文的 arXiv HTML；无法取得时，可从对应精确版本
的官方源码包或 PDF 生成本站缓存。中文分析来自所配置的 DeepSeek 模型。项目/代码链接
只从来源元数据中明确存在且通过校验的 URL 提取，不由模型生成。arXiv API 不稳定提供
作者机构，因此本站不推断或展示机构。

本项目是独立实现，没有 fork 或复制下列项目的源码；它们是需求调研和产品设计参考：

- [monologg/nlp-arxiv-daily](https://github.com/monologg/nlp-arxiv-daily)
- [dw-dengwei/daily-arXiv-ai-enhanced](https://github.com/dw-dengwei/daily-arXiv-ai-enhanced)
- [Vincentqyw/cv-arxiv-daily](https://github.com/Vincentqyw/cv-arxiv-daily)
- [AutoLLM/ArxivDigest](https://github.com/AutoLLM/ArxivDigest)

## 故障排查

- **提示缺少 API key**：确认 `DEEPSEEK_API_KEY` 是 Repository Secret，名称大小写
  完全一致；来自 fork 的 pull request 默认不能读取上游 Secret。
- **Pages 返回 404**：确认 Pages Source 是 GitHub Actions，`github-pages`
  environment 未阻止部署，并检查 Actions 中 `Deploy Pages` 的目标 URL。
- **每日任务没有准点运行**：先确认工作流位于默认分支且 Actions 已启用；可用
  `workflow_dispatch` 手动补跑。GitHub schedule 不是实时调度器，排队延迟不代表失败。
- **没有新论文**：查看运行摘要，区分 arXiv 正常返回零篇、预筛无命中和 arXiv
  请求失败；必要时将 `lookback_days` 暂时调大。
- **候选超过 60 或失败超过 30%**：这是防止不完整数据上线的质量门槛。检查关键词、
  arXiv/DeepSeek 状态后重跑，不要通过提交半成品 JSON 绕过。
- **Figure 不显示或不能直接下载**：先检查相应
  `web/public/figures/{arxiv_id}/v{version}/` 文件是否存在，再查看卡片中的恢复状态
  提示。可运行 `uv run vla-wam-daily sync-figures` 手动回填；缓存失败会使用可信的
  arXiv HTML 原图并在后续每日运行重试，CORS 下载失败会自动打开原图。源码或 PDF
  回退因置信不足而返回 `not_found` 时，请从论文 PDF 查看，不要放宽规则强行截取。
- **本地构建找不到数据**：从仓库根目录保留 `data/`，或为构建显式设置
  `VLA_WAM_DATA_DIR`；自定义数据目录的父目录必须预先存在。

## 局限

- AI 分析仅基于标题和摘要，不是论文全文评审，可能遗漏细节或误判相关性。
- arXiv 分类和关键词预筛存在漏报；模型评分也不能替代领域专家判断。
- Figure 依赖 arXiv HTML、源码包或 PDF 中存在可明确识别的素材；不是每篇论文都有
  达到置信条件的 Fig. 1 / Fig. 2，远程下载降级仍可能受浏览器 CORS 策略影响。
- 站点只缓存已发布论文中可靠识别的 Fig. 1 / Fig. 2；PDF 自动裁剪可能因置信不足而
  明确降级，也不保证论文内容可依法再分发，使用者仍需核对原论文许可证。
- RSS、搜索和归档是静态构建产物；最近一次工作流失败时会继续展示上一版。
- Weekly Top 5 是从当周高分论文中确定性选取的阅读入口，不是引用量或学术影响排名。

## License

项目源码使用 [MIT License](LICENSE)。论文元数据、摘要、Figure、论文正文和其他第三方
内容不因本项目许可证而改变其原有版权或论文许可证。
