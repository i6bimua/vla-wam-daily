# VLA/WAM Daily

面向 Vision-Language-Action（VLA）、World Action Model（WAM）和机器人世界模型的
每日研究门户。项目用 Python 从 arXiv 获取元数据并调用 DeepSeek 做摘要级中文分析，
再由 Astro 生成静态网站、Pagefind 搜索索引和 RSS，通过 GitHub Actions 部署到
GitHub Pages；不需要数据库或常驻服务器。

## 功能

- 每天通过 arXiv OAI-PMH 抓取 `cs.RO`、`cs.CV`、`cs.AI`、`cs.LG` 元数据，先做
  确定性关键词预筛，再做 DeepSeek 相关性评分；手动指定论文时仍使用 arXiv 查询 API。
- 展示中英文标题、一句话总结、核心贡献、方法、摘要报告的实验结果与局限，以及与
  VLA/WAM 的关系。
- 提供 Today、VLA、WAM、World Models、Datasets、Benchmarks、Weekly Top 5、
  月度归档和 RSS。
- Pagefind 支持中英文全文搜索；页面支持主题、日期、分数、代码状态筛选和移动端布局。
- 从 arXiv HTML 识别并远程展示 Fig. 1 / Fig. 2、英文 caption 和多 panel 原图。
- 在常规每日运行、未使用 `--force-arxiv-id` 强制重分析时，缓存论文分析与 Figure
  元数据；相同论文版本、模型和 Prompt 不重复产生分析费用。
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
每次最多分析 60 篇新候选；超过上限会停止更新，而不是截断后发布不完整结果。新论文
分析失败比例超过 30% 时，整次数据更新失败并保留线上上一版。

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
BASE_PATH=/ VLA_WAM_DATA_DIR=../tests/fixtures/data pnpm build
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

- `config/topics.yaml`：arXiv 分类、回看天数、请求间隔、超时与重试策略、预筛短语与
  组合规则、发布阈值、60 篇候选上限、30% 失败阈值、并发数和模型档位。默认给
  arXiv 请求 60 秒超时、最多 5 次指数退避重试；每日范围抓取默认使用更适合增量收割的
  OAI-PMH，避免 GitHub 公共 Runner 共享出口触发查询 API 的系统级限流。
- `prompts/analysis-v1.md`：DeepSeek 的结构化 JSON Prompt。修改时应同步增加
  `prompt_version`，使缓存和结果 provenance 可追溯。
- `data/latest.json`：最新数据；`data/archive/YYYY-MM.json`：月度归档；
  `data/cache/analyses.json` 与 `data/cache/figures.json`：分析和 Figure 元数据缓存。
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

`.github/workflows/daily.yml` 的计划表达式是 `30 2 * * *`（UTC），即每天
北京时间 10:30。GitHub 的定时任务可能因平台排队稍晚开始。非 dry-run 成功后，工作流
只提交 `data/`，再构建并发布 Pages；任何测试、数据校验或构建失败都不会替换线上
上一版。`.github/workflows/pages.yml` 在默认分支变化时使用现有数据重建页面，不调用
DeepSeek。

## Fig. 1 / Fig. 2

Figure 管线只在论文相关性评分通过发布阈值后访问带明确版本号的 arXiv HTML 页面。
解析器读取真实的 `figure` / `figcaption` 结构，根据 `Figure 1`、`Fig. 1`、
`Figure 2`、`Fig. 2` 等 caption 开头识别图号，并收集 Figure 内全部图片，因此支持
一个 Figure 的多 panel。它不猜测 `x1.png` 等资源文件名，也不从 PDF 截图。

仓库和 Pages 只保存 Figure 的 URL 和元数据（caption、状态、检查时间），
不保存图片字节。访客展开论文卡片时，浏览器直接从 arXiv 加载图片。每个 panel
都可打开原图；
“下载原图”会先用 `fetch` 获取图片并创建临时 Blob。如果 CORS、网络或浏览器策略
阻止 Blob 下载，页面会降级为新标签打开原图，用户仍可用浏览器保存。

除了 `available`，Figure Gallery 有三种非阻塞降级状态：

- `html_unavailable`：arXiv 暂无 HTML 版本，提供 PDF 链接。
- `not_found`：HTML 可用，但未识别到 Fig. 1 / Fig. 2，提供 HTML 和 PDF。
- `fetch_failed`：网络、服务或解析暂时失败，提供 PDF，并在后续每日运行重试。

Figure 图片的版权归论文作者或其他权利人所有，本站不托管图片文件。arXiv 的远程展示
不等于授予新的转载权限；查看、下载或复用前，请遵循论文许可证及论文页面标注的
许可证。详情见 [arXiv Permissions and Reuse](https://info.arxiv.org/help/license/reuse.html)
和 [arXiv License Information](https://info.arxiv.org/help/license/index.html)。

## 数据来源与项目来源

论文标题、摘要、作者、分类和版本来自 [arXiv API](https://info.arxiv.org/help/api/)，
Figure URL 与 caption 来自对应论文的 arXiv HTML，中文分析来自所配置的 DeepSeek
模型。项目/代码链接只从来源元数据中明确存在且通过校验的 URL 提取，不由模型生成。
arXiv API 不稳定提供作者机构，因此本站不推断或展示机构。

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
- **Figure 不显示或不能直接下载**：查看卡片中的三种状态提示。HTML 转换可能晚于
  论文发布；CORS 下载失败会自动打开原图，图片仍可从 arXiv 或 PDF 查看。
- **本地构建找不到数据**：从仓库根目录保留 `data/`，或为构建显式设置
  `VLA_WAM_DATA_DIR`；自定义数据目录的父目录必须预先存在。

## 局限

- AI 分析仅基于标题和摘要，不是论文全文评审，可能遗漏细节或误判相关性。
- arXiv 分类和关键词预筛存在漏报；模型评分也不能替代领域专家判断。
- Figure 依赖 arXiv HTML 转换、远程图片可用性和浏览器 CORS 策略；不是每篇论文
  都有可解析的 Fig. 1 / Fig. 2。
- 站点不托管论文图片、不截图 PDF，也不保证图片永久可访问或可依法再分发。
- RSS、搜索和归档是静态构建产物；最近一次工作流失败时会继续展示上一版。
- Weekly Top 5 是从当周高分论文中确定性选取的阅读入口，不是引用量或学术影响排名。

## License

项目源码使用 [MIT License](LICENSE)。论文元数据、摘要、Figure、论文正文和其他第三方
内容不因本项目许可证而改变其原有版权或论文许可证。
