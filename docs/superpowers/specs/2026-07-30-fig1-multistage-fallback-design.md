# VLA/WAM Daily Fig. 1 多级回退设计

日期：2026-07-30
状态：待实现

## 背景与目标

首页已经优先展示永久缓存的 Figure 1，但当前缺图包含两类不同问题：

1. arXiv HTML 已包含 Figure 1，图片和 `figcaption` 却是相邻节点而非同一个
   `<figure>` 的子节点。现有解析器因此漏掉 RLMM-Flow（2607.26460v1）和
   PACE（2606.00537v2）的 Figure 1。
2. arXiv 暂未提供 HTML，或者 HTML 转换结果确实没有可用的 Figure 1。此时仅靠
   HTML 无法完成展示。

本次目标是让 Figure 1 获取形成可靠的官方来源多级回退，同时保持现有永久缓存、下载、
详情页 Figure 1/2 展示和每日发布不被图片失败阻塞：

```text
严格 arXiv HTML
→ arXiv 源码包中的 Figure 1 原始素材
→ arXiv PDF 的 Figure 1 自动裁剪
→ 详情页保留首个可用 Figure
→ 明确的不可用提示
```

首页仍只展示真正的 Figure 1。Figure 1 不可用时不能把 Figure 2 冒充为 Figure 1；
详情页可以继续显示正确标号的 Figure 2。

## 方案比较

### 采用：HTML 修复 + 官方源码 + 官方 PDF

该方案优先复用 arXiv 已结构化的数据，只有前两层失败才执行版面裁剪。成功产物继续存入
`web/public/figures/{arxiv_id}/v{version}/`，随 GitHub Pages 永久发布。

优点：

- 能直接修复当前已确认的 arXiv HTML 结构变体。
- 不依赖第三方图床、ar5iv 或项目页的长期可用性。
- HTML/源码能保留原始图像质量；PDF 能覆盖 TikZ、多面板排版和复杂宏。
- 每个版本只需成功生成一次，后续运行直接复用本地缓存。

代价：

- 需要安全处理源码压缩包。
- PDF 裁剪是启发式过程，必须设置置信条件，宁可返回不可用也不能截错图。
- 本地生成的面板没有单独的远程原图 URL，数据模型和页面动作需要支持“只有缓存文件”。

### 未采用：只修 HTML

改动最小，也能修复 RLMM-Flow 和 PACE，但无法覆盖 See2Think、Speech2Grasp 等暂时没有
arXiv HTML 的论文。

### 未采用：第三方 HTML 或 Figure API

ar5iv 或第三方论文服务可以提高短期覆盖率，但会引入额外服务可用性、页面结构、来源
校验和版权提示问题。本次不把第三方服务放入生产链路。

## 组件边界

### `ArxivFigureClient`

继续负责下载和解析官方 arXiv HTML。解析规则扩展为两种受控结构：

1. 标准结构：图片和 `figcaption` 位于同一个 `<figure>`。
2. 松散结构：目标 `<figure>` 只有带编号的 `figcaption`，图片位于它紧邻的前置兄弟
   容器中。

松散结构只能回看同一父容器内、紧邻 Figure 的一个前置元素；遇到另一个
`figure`、标题、正文段落或无图片容器即停止。图片仍必须通过现有 arXiv 主机、论文 ID、
版本和路径校验。该限制避免把正文插图或上一节图片错误关联到 Figure 1。

标准结构优先；只有标准 Figure 内没有图片时才使用松散结构。图注、锚点、去重和 Figure
1/2 编号规则保持不变。

### `ArxivSourceFigureExtractor`

新增独立的官方源码提取器：

- 请求精确版本的 `https://arxiv.org/e-print/{arxiv_id}v{version}`。
- 限制下载字节数、压缩包成员数、单成员大小和总解压大小。
- 拒绝绝对路径、`..`、符号链接、硬链接、设备文件和其他非普通文件。
- 优先检查包含 `\documentclass` 的主 TeX 文件，并受限展开本包内的
  `\input`/`\include`。
- 定位第一个编号 Figure 环境，提取 Figure 1 caption 和
  `\includegraphics` 素材。
- 只在素材与 Figure 环境关系明确时返回结果。单个 PNG/JPEG/WebP/GIF/SVG 可以直接
  保存；单页 PDF 素材渲染为 PNG；TikZ、外部命令、无法解析的宏和不明确的多面板布局
  交给 PDF 回退。

提取器只返回已验证的 caption、来源和本地面板字节，不负责更新 JSON 或页面。

### `ArxivPdfFigureExtractor`

新增官方 PDF 回退：

- 请求精确版本的 `https://arxiv.org/pdf/{arxiv_id}v{version}`。
- 使用 PDF 文本坐标寻找以 `Figure 1` 或 `Fig. 1` 开头的图注。
- 在同页中选择紧邻图注、与图注水平重叠且位于图注上方的图像/绘图区域。
- 只在候选区域唯一、尺寸合理、不覆盖页眉页脚且不包含相邻 Figure caption 时接受。
- 以约 300 DPI 渲染为单张 PNG，保留完整组合图；caption 使用 PDF 中提取的普通文本。

PDF 解析和渲染采用宽松许可证的 Python 依赖，不引入 AGPL 依赖。找不到唯一候选区域时
返回 `not_found`，不使用整页截图蒙混通过。

### `FigureRecoveryService`

新增编排服务，接收 `FigureGallery`、论文 ID、版本和现有公共目录：

1. 若 Gallery 已有 Figure 1，只执行现有远程图片镜像。
2. 若缺少 Figure 1，先用新版 HTML 解析器重新获取一次并合并 Figure 1/2。
3. HTML 仍无 Figure 1 时尝试源码提取。
4. 源码不明确时尝试 PDF 裁剪。
5. 成功后原子写入现有版本目录并返回更新后的 Gallery。
6. 单篇恢复失败只记录状态和日志，不抛出到每日发布顶层。

`synchronize_figure_assets` 仍负责扫描 `latest.json`、全部月度归档和 Figure 缓存，然后
将恢复后的同一 Gallery 一次性回写所有位置。这保证历史论文也能补图，而不仅是三天
抓取窗口中的论文。

## 数据契约

`FigureAsset.source` 扩展为：

```text
arxiv_html   直接解析并镜像 arXiv HTML 图片
arxiv_source 从官方 e-print 源码包提取
arxiv_pdf    从官方 PDF 裁剪
```

为保持现有 JSON 结构兼容，`image_urls` 仍与面板一一对应，但元素允许为 `null`：

- HTML 面板：`image_urls[index]` 是严格校验的 arXiv HTML 图片 URL。
- 源码/PDF 面板：`image_urls[index]` 为 `null`，
  `cached_image_paths[index]` 必须是有效本地路径。
- 每个面板必须至少有远程 URL 或本地缓存路径之一。
- 两个数组非空且长度必须一致；读取旧数据时，缺省的 `cached_image_paths` 按
  `image_urls` 长度补 `null`。

`source_url` 根据来源使用：

- `arxiv_html`：带 Figure 锚点的版本化 HTML URL。
- `arxiv_source`：精确版本的 e-print URL。
- `arxiv_pdf`：精确版本的 PDF URL。

所有来源仍限定为 HTTPS arXiv 主机，并且 URL 中的论文 ID、版本必须与 Gallery 一致。

`FigureGallery` 增加有默认值的恢复元数据，保证既有文件可读：

```text
recovery_status:
  not_attempted | available | not_found | fetch_failed
recovery_checked_at: UTC 时间或 null
```

规则：

- 已有 Figure 1 的旧 Gallery 在读取时归一化为 `available`。
- 同一版本的 `available` 和 `not_found` 永久缓存。
- `fetch_failed` 24 小时后重试。
- 新 arXiv 版本使用新缓存键并重新执行全部层级。
- 解析器规则升级时，通过代码中的恢复版本号让缺少 Figure 1 的旧缓存重新检查一次。

## 本地文件与原子写入

成功的源码/PDF产物继续使用：

```text
/figures/{arxiv_id}/v{version}/fig1-panel1.{ext}
```

写入沿用现有临时文件、`fsync`、`os.replace` 和目录边界检查。恢复器不得覆盖已经存在且
非空的同版本面板，除非缓存记录与文件不一致。下载、解压和 PDF 临时文件只存在于系统
临时目录，流程结束后删除。

## 页面行为

首页：

- 有 Figure 1 时优先显示本地缓存。
- HTML 原图尚未镜像时可临时回退到受信任的远程 URL。
- 只有 Figure 2 时仍显示“Fig. 1 暂不可用”，不会错误改标。

详情页：

- HTML 来源保留“定位原文”和“查看 arXiv 原图”。
- 源码来源显示“来源：arXiv 源码包”；PDF 来源显示“来源：PDF 自动裁剪”。
- 只有本地面板时提供“下载本站缓存”和“查看论文 PDF”，不显示不存在的远程原图链接。
- Figure 2 若可用继续按真实编号展示。
- 页脚明确说明源码/PDF回退属于从官方论文文件生成的本站缓存，下载和复用仍以论文许可
  为准。

## 错误处理与观测

Figure 同步报告新增：

```text
html_recovered
source_recovered
pdf_recovered
recovery_not_found
recovery_failed
```

每次日志包含论文 ID、版本、尝试层级和最终来源，但不输出压缩包内容或任意远程响应体。
源码损坏、PDF 解析失败、网络超时和单篇写入失败均降级，不影响其他论文和 Pages 构建。

## 测试策略

Python 测试：

- 先用真实结构裁剪后的 fixture 重现“图片与空 Figure 为相邻节点”，确认旧解析器失败，
  新解析器准确恢复 Figure 1。
- 验证不会跨标题、正文、另一个 Figure 或父容器错误回看图片。
- 验证标准 Figure 内图片优先于相邻图片。
- 覆盖源码包路径穿越、链接、解压大小、成员数量和不明确 Figure 环境。
- 覆盖单个浏览器图片、单页 PDF 素材和需要交给 PDF 的复杂源码。
- 使用小型固定 PDF fixture 覆盖唯一 Figure 1、无 caption、多个歧义候选和裁剪边界。
- 覆盖 `null` 远程 URL、本地路径对齐、来源 URL 身份和旧 JSON 兼容。
- 覆盖恢复层级顺序、永久缓存、24 小时失败重试和单篇失败隔离。
- 覆盖同步报告计数以及 latest/archive/cache 的一致回写。

前端测试：

- 首页显示本地恢复的 Figure 1。
- 详情页对 HTML、源码、PDF 三种来源显示正确文案和动作。
- 本地-only 面板没有“查看 arXiv 原图”链接，但可以下载缓存并打开 PDF。
- 只有 Figure 2 时首页仍显示正确的 Figure 1 不可用提示。

集成验证：

- 对 2607.26460v1 和 2606.00537v2 验证 HTML 层恢复 Figure 1。
- 对当前 HTML 不可用的论文验证源码或 PDF 层至少一种成功。
- 连续运行两次同步，第二次不得重新下载或重写已恢复面板。
- 运行全部 Python、类型、格式、Web 单元测试、浏览器测试和生产构建。

实时 arXiv 只用于本地/发布前集成验证；CI 单元测试全部使用固定 fixture。

## 验收标准

- RLMM-Flow 和 PACE 首页卡片显示正确的 Figure 1。
- HTML 真不可用时，能从官方源码或官方 PDF 自动生成 Figure 1 的永久本地缓存。
- 任何 Figure 2 都不会冒充 Figure 1。
- 详情页正确显示 Figure 1/2、来源、图注和可用下载动作。
- 历史归档、最新数据和 Figure 缓存保持一致。
- 已成功的恢复结果在同一论文版本上永久复用。
- 错图风险高于阈值时明确降级，不截整页、不阻塞每日发布。
- 所有新增网络入口、压缩包处理和本地路径均通过边界与安全测试。
