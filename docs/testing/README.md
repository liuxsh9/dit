# Dit 手动测试指南 — 总索引

本系列指南用于对 Dit（`dit`）的**全部功能模块**进行人工 QA 验收测试。测试者按照指南编号从 00 开始逐步执行，每节均提供明确的操作步骤、预期输出和验证命令，确保功能行为与设计规格一致。

---

## 概述

Dit 是一个面向机器学习训练数据的版本管理系统，提供类 Git 的本地 CLI（`dit`）、基于 HTTP 的服务端 REST API、元数据计算、导出/搜索/去重等运维功能。

本测试系列覆盖以下层面：

- **本地工作流**：初始化、提交、分支、标签、合并、冲突解决
- **远程协作**：推送、拉取、克隆、令牌认证
- **服务端 API**：仓库管理、对象操作、引用、日志、权限
- **PR 与审查**：创建/评论/审查/合并、分支保护、审查者规则
- **元数据与 Sidecar**：计算、聚合统计、差异对比
- **导出/统计/搜索/校验**：export、stats、search、validate、CI checks
- **运维**：blame、gc、dedup、fsck、健康检查、Prometheus 监控

---

## 测试顺序

建议严格按照编号顺序执行，因为后续指南依赖前置指南建立的测试环境和数据：

```
00 → 01 → 02 → 03 → 04 → 05 → 06 → 07 → 08 → 09
```

每完成一篇指南，在下方[测试记录模板](#测试记录模板)中记录结果，再继续下一篇。

---

## 指南目录

| 编号 | 文件 | 标题 | 覆盖范围 | 预估时长 | 依赖 |
|------|------|------|---------|---------|------|
| 00 | [00-setup-and-deployment.md](00-setup-and-deployment.md) | 环境搭建与部署验证 | Python/uv/PostgreSQL 环境准备、本地与 Docker 启动、Admin 令牌创建 | 30–45 min | 无 |
| 01 | [01-local-operations.md](01-local-operations.md) | 本地操作基础 | `dit init/add/status/commit/log/diff`、Blob 文件、边界场景 | 30–40 min | 00 |
| 02 | [02-branching-and-tags.md](02-branching-and-tags.md) | 分支管理与标签 | `dit branch/checkout/switch/tag`、数据隔离验证、边界场景 | 30–40 min | 01 |
| 03 | [03-remote-collaboration.md](03-remote-collaboration.md) | 远程协作 | 远程配置、首次推送、克隆、拉取、分支推送、令牌认证 | 30–40 min | 00 + 01 |
| 04 | [04-merge-and-conflict.md](04-merge-and-conflict.md) | 合并与冲突解决 | 快进合并、三方合并、冲突检测与解决、中止合并、Cherry-pick | 40–50 min | 01 + 02 |
| 05 | [05-server-api-core.md](05-server-api-core.md) | 服务端核心 API | REST API：仓库/引用/对象/树/日志/Manifest/Diff/权限 | 30–40 min | 00 + 03 |
| 06 | [06-pull-requests.md](06-pull-requests.md) | PR 与代码审查 | PR 创建/评论/审查/合并（含冲突）、分支保护、审查者规则 | 40–50 min | 00 + 03/05 |
| 07 | [07-metadata-and-sidecar.md](07-metadata-and-sidecar.md) | 元数据与 Sidecar | `dit meta compute/show/diff`、Sidecar 聚合统计、REST API 路径 | 30–40 min | 01 |
| 08 | [08-export-stats-search-validate.md](08-export-stats-search-validate.md) | Export / Stats / Search / Validate / CI | 导出、行统计、全文搜索、数据校验、CI 集成检查 | 40–50 min | 07 |
| 09 | [09-operations.md](09-operations.md) | 运维功能 | blame、gc、dedup（检测/报告）、fsck、健康检查、Prometheus | 40–50 min | 07 + 08 |

**总预估时长**：约 6–7 小时（含环境准备）

---

## 前置条件总览

执行指南 00 之前，确认本机已具备以下条件：

| 依赖 | 最低版本 | 验证命令 |
|------|---------|---------|
| Python | 3.12+ | `python --version` |
| uv | 最新稳定版 | `uv --version` |
| PostgreSQL | 13+ | `psql --version` |
| curl | 任意版本 | `curl --version` |
| Docker（可选） | 20+ | `docker --version` |

**代码仓库**：测试前须已克隆 Dit 源码并位于项目根目录。

**数据库**：需要一个可用的 PostgreSQL 实例，测试过程中会创建专用数据库（如 `dit_test`），测试结束后可手动删除。

---

## 样例数据格式

各指南中使用的 JSONL 测试文件遵循 **OpenAI messages 格式**（SFT 训练数据标准格式）：

```jsonl
{"messages":[{"role":"system","content":"你是一名助手"},{"role":"user","content":"介绍 Python"},{"role":"assistant","content":"Python 是一种解释型、面向对象的高级编程语言……"}]}
{"messages":[{"role":"user","content":"什么是机器学习"},{"role":"assistant","content":"机器学习是人工智能的一个分支……"}]}
```

关键字段说明：

| 字段 | 说明 |
|------|------|
| `messages` | 消息数组，每条含 `role`（`system` / `user` / `assistant`）和 `content` |
| `response`（可选） | 部分指南使用此字段作为独立响应列，与 `messages[-1].content` 等价 |

Sidecar 元数据会从 `messages` 字段提取 `char_count`、`token_estimate`（`char_count // 4`）、`field_count`、`lang`（`en` / `zh` / `ru` / `ar` / `null`）。

---

## 测试记录模板

执行每篇指南时，复制以下模板并填写，便于追踪进度和汇总问题。

```markdown
## 测试记录 — 指南 XX：<标题>

- **测试者**：
- **测试日期**：
- **Dit 版本/Commit**：
- **测试环境**：（本地开发 / Docker / 其他）

### 章节结果

| 章节 | 标题 | 结果 | 备注 |
|------|------|------|------|
| 1    |      | ✅ 通过 / ❌ 失败 / ⚠️ 部分 / ⏭️ 跳过 | |
| 2    |      |      | |
| …    |      |      | |

### 发现的问题

| # | 章节 | 问题描述 | 严重程度（P0/P1/P2/P3）| 重现步骤 |
|---|------|---------|----------------------|---------|
| 1 |      |         |                      |         |

### 总体结论

- [ ] 全部通过，可提测
- [ ] 存在失败项，需修复后重测
- [ ] 仅存在轻微问题，已记录，可继续

### 补充说明

（可选：环境差异、耗时、建议改进等）
```

---

## 问题反馈

测试过程中发现的 bug 或文档问题，请通过以下方式反馈：

1. **GitHub Issue**：在项目仓库提 Issue，标题格式为 `[Testing] 指南XX - <简述>`，正文粘贴测试记录中的"发现的问题"表格及重现步骤。

2. **文档问题**：若为指南本身的错误或歧义（非 bug），直接修改对应 `docs/testing/XX-*.md` 文件并提 PR，PR 标题格式为 `docs(testing): fix guide XX - <简述>`。

3. **严重程度定义**：

   | 级别 | 含义 |
   |------|------|
   | P0 | 服务崩溃 / 数据损坏 / 无法继续测试 |
   | P1 | 核心功能不可用，无绕过方案 |
   | P2 | 功能异常，有绕过方案或影响范围有限 |
   | P3 | 轻微问题：UI/文案/日志等非功能性缺陷 |
