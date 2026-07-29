# VLA/WAM Daily Fig. 1 / Fig. 2 远程展示设计

日期：2026-07-29  
状态：已复核通过

## 目标

让读者展开论文卡片后，不离开 VLA/WAM Daily 就能看到论文的 Figure 1
和 Figure 2、对应英文图注，并能打开或下载 arXiv 提供的原图。

本功能采用用户选择的“全部远程展示”策略：

- 图片始终由 `arxiv.org` 提供，不复制进 Git 仓库或 GitHub Pages artifact。
- 页面明确标注图片来源并链接到论文的 arXiv HTML/PDF。
- 如果 arXiv HTML 不存在、转换失败或缺少目标图，只显示降级提示和 PDF 链接。
- 不从 PDF 自动截图，也不因为图像失败而阻塞当天论文发布。

arXiv 官方说明图片复用权限取决于论文许可证。本网站不重新托管文件，并在页面保留
作者、论文和 arXiv 来源，但远程展示模式仍应附带版权提示。参考：

- [arXiv Permissions and Reuse](https://info.arxiv.org/help/license/reuse.html)
- [arXiv License Information](https://info.arxiv.org/help/license/index.html)

## 方案选择

### 采用：解析 arXiv HTML 的真实 Figure 节点

抓取 `https://arxiv.org/html/{arxiv_id}v{version}`，解析带编号图注的
`figure`/`figcaption` 结构，只选择 Figure 1 和 Figure 2，然后把相对图片地址解析为
绝对 HTTPS URL。

优点：

- 图号与 caption 关联准确，不依赖图片文件名。
- 支持 Figure 内的多面板/子图。
- 不增加仓库和 Pages 体积。
- 能提供原始图注、HTML 锚点和图片地址。

### 未采用：猜测 `x1.png` 和 `x2.png`

arXiv HTML 经常生成类似文件名，但资源编号不构成公开的 Figure 编号契约。直接猜测
可能把公式、子图或其他资源错配成 Figure 1/2。

### 未采用：第三方图像接口或 PDF 截图

第三方接口增加可用性和数据一致性依赖。PDF 截图需要额外版面分析工具，会产生误截、
运行时间、托管体积和更明显的转载问题。两者均不属于第一版范围。

## 数据契约

新增三个模型：

```text
FigureAsset
  number: 1 | 2
  label: 非空字符串
  caption: 非空字符串
  image_urls: 1 个或多个 arxiv.org HTTPS URL
  source_url: 对应 Figure 的 arXiv HTML 锚点
  source: "arxiv_html"

FigureGallery
  status: "available" | "html_unavailable" | "not_found" | "fetch_failed"
  html_url: arXiv HTML URL
  figures: FigureAsset[]，最多包含 Figure 1 和 Figure 2
  checked_at: UTC 时间

FigureCacheEntry
  key: "{arxiv_id}:v{version}"
  gallery: FigureGallery
```

`PaperRecord` 增加必需字段 `figure_gallery`。项目尚未公开发布，因此该增量仍属于初始
`schema_version: "1"`，不需要兼容已经发布的数据。

约束：

- Figure 号只能是 1 或 2，并按编号升序输出。
- 同一 Figure 可包含多个图片 URL，用于多面板/子图。
- `image_urls`、`source_url` 和 `html_url` 必须是 HTTPS。
- 图片及 Figure 来源 URL 只允许主机 `arxiv.org` 或 `www.arxiv.org`。
- 不接受 HTML 中指向项目站、GitHub、广告或其他域名的图片。
- 图注用于 UI 文本和 `alt` 文本，必须经过普通文本提取，不能保留任意 HTML。

## 抓取与解析

新增独立组件 `ArxivFigureClient`，依赖注入现有 `httpx.AsyncClient`，职责仅限：

1. 构造带明确版本号的 arXiv HTML URL。
2. 使用项目 User-Agent、超时和有限重试获取 HTML。
3. 解析真实 Figure 元素及其 caption。
4. 识别 `Figure 1`、`Fig. 1`、`Figure 2`、`Fig. 2` 等编号形式。
5. 收集 Figure 内所有 `<img>` 的 `src`；忽略 data URI、空地址和非 arXiv 主机。
6. 返回严格校验的 `FigureGallery`。

解析使用轻量 HTML5 解析器，不用正则表达式解析整份 HTML。Figure 编号只从 caption
开头提取，避免正文中“如 Figure 1 所示”的交叉引用被误认为图片。

状态规则：

- HTTP 404、HTML 不支持页面或明确无 HTML：`html_unavailable`。
- HTML 正常但没有 Figure 1/2：`not_found`。
- 超时、5xx、解析异常且重试耗尽：`fetch_failed`。
- 至少找到一张目标图：`available`；缺少另一张时保留部分结果。

## 流水线与缓存

相关性评分通过发布阈值后才解析图像，只为最终发布的论文请求 HTML，避免扩大 arXiv
流量和工作流耗时。仓库默认阈值可被本次运行配置覆盖；Figure 管线接收筛选结果，不从
`DataFile` 反推阈值。

缓存键为 `arxiv_id + version`：

- `available` 结果对该版本永久复用。
- `html_unavailable`、`not_found`、`fetch_failed` 为负缓存，24 小时后允许重试，
  因为 arXiv HTML 转换可能晚于论文元数据出现。
- 新版本使用新键并重新解析。
- 缓存只保存 URL、caption、状态和时间，不保存图片字节。

每篇论文的图像失败只记录为运行统计和日志，不计入 LLM 失败比例，也不取消本次成功
发布。运行统计的稳定字段为 `figure_cache_hits`、`figure_requests`、
`figure_available`、`figure_unavailable`、`figure_failed`。

## 页面体验

论文卡片使用可键盘操作的展开区域：

- 收起状态继续显示题目、摘要和核心分析。
- 点击卡片的“查看详情”后，在分析内容下方显示 Figure 1/2。
- 桌面端两列，移动端单列；多子图在所属 Figure 内顺序排列。
- 图片使用 `loading="lazy"`，避免首页一次加载所有远程图。
- 每张 Figure 显示图号、英文 caption、图片来源。
- “查看原图”在新标签打开 arXiv 图片。
- “下载原图”在浏览器中用 `fetch` 获取 arXiv 图片并创建临时 Blob 下载；arXiv
  当前图片响应允许跨域读取。若 CORS、网络或浏览器策略阻止下载，自动退化为新标签
  打开原图，用户仍可保存。
- 多子图分别提供下载按钮，并使用
  `{arxiv_id}-v{version}-fig{number}-panel{index}.{ext}` 作为建议文件名。
- 图片加载失败时隐藏破损图标，显示“图片暂时无法加载”和 PDF 链接。
- `alt` 文本由 `Figure {number}: {caption}` 生成；过长 caption 在可视区域折叠，
  但辅助技术和详情区域仍能访问完整文本。

当 Gallery 不可用时：

- `html_unavailable`：提示“arXiv 暂无 HTML 版本”，提供 PDF。
- `not_found`：提示“未能在 arXiv HTML 中识别 Fig. 1 / Fig. 2”，提供 HTML 和 PDF。
- `fetch_failed`：提示“图片信息暂时获取失败；下一次每日运行将重试”，并提供 PDF。

页面底部方法说明增加：

> Figure 图片由访问者浏览器直接从 arXiv 加载，版权归原作者或权利人所有；
> 本站不托管图片文件。复用和下载请遵循论文页面标注的许可证。

## 安全与可靠性

- 构建时仅请求 `https://arxiv.org/html/` 下、由严格 arXiv ID 和版本生成的页面。
- 解析后的所有 URL 再次经过 scheme/host allowlist 校验。
- 前端不注入 caption HTML，只渲染普通文本。
- 外链使用 `rel="noopener noreferrer"`。
- HTTP 响应设置合理的最大 HTML 大小，防止异常大响应占用内存。
- HTML 抓取沿用 arXiv 友好的请求间隔和明确 User-Agent。
- 图片 URL 不进入 Pagefind 正文索引；caption 可进入全文搜索。

## 测试

Python 单元测试覆盖：

- 含 Figure 1、Figure 2 和无关 Figure/Table 的标准 HTML。
- `Figure`/`Fig.` 编号变体、大小写、空白和冒号。
- 多子图、相对 URL、绝对 URL、重复 URL。
- 非 arXiv URL、data URI、缺少 caption、空 caption。
- 404、5xx、超时、解析失败、部分成功。
- 成功缓存、24 小时负缓存、新版本缓存失效。
- JSON 序列化和严格模型校验。

Astro/浏览器测试覆盖：

- 点击卡片后显示 Figure 1/2 和 caption。
- 移动端顺序与可访问名称。
- 查看原图链接。
- 成功 Blob 下载及跨域失败时的新标签降级。
- 图片加载失败占位。
- Gallery 三种不可用状态与 PDF 降级链接。

端到端干运行使用固定 HTML fixture，不在 CI 单元测试中依赖实时 arXiv。最终发布前用
一篇真实 VLA 论文进行非阻塞验证。

## 验收标准

- 对具有有效 arXiv HTML 的论文，展开卡片即可看到可用的 Figure 1/2。
- 图号、caption 和图片对应正确，多子图不丢失。
- 每张远程图可以打开原图；允许跨域时可一键保存，失败时可靠降级。
- 页面和仓库不保存论文图片字节。
- 缺失 HTML、缺图或网络错误不会阻塞每日发布。
- 桌面端、移动端和键盘操作均通过浏览器测试。
- 页面展示来源与版权提示。
- 验收文案明确说明图片由 arXiv 远程提供、不保存图片字节，下载与复用必须遵循
  论文页面标注的许可证。
