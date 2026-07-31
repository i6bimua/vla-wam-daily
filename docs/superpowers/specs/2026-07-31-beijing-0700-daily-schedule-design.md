# 北京时间 07:00 每日刷新设计

## 目标

把 VLA/WAM Daily 的自动刷新时间从北京时间每天 10:30 改为每天 07:00。
手动触发入口和每日任务的抓取、分析、Figure 缓存、数据保存及 Pages 发布逻辑保持不变。

## 调度设计

在 `.github/workflows/daily.yml` 中使用 GitHub Actions 原生的时区感知计划：

```yaml
on:
  schedule:
    - cron: "0 7 * * *"
      timezone: "Asia/Shanghai"
  workflow_dispatch:
```

`Asia/Shanghai` 不使用夏令时，因此该计划全年对应北京时间 07:00。显式时区比写成前一天
`23:00 UTC` 更容易阅读和维护。

GitHub Actions 的计划任务可能因平台排队而在 07:00 之后几分钟开始；这是平台调度特性，
不改变计划时间。

## 文档范围

同步更新 README 和当前产品设计说明中的默认更新时间。历史实施计划作为开发记录保留，
不批量重写。

## 验证

- 解析 workflow YAML，确认只有一个每日 schedule。
- 断言 cron 为 `0 7 * * *`，timezone 为 `Asia/Shanghai`。
- 断言 `workflow_dispatch` 仍存在。
- 搜索面向用户的当前文档，确保不再把默认更新时间写成 10:30。
- 运行仓库现有的 workflow/文档相关测试与格式检查。
