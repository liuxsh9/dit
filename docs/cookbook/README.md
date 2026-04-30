# Dit Cookbook

Dit 功能使用手册。每篇指南覆盖一个核心功能，包含典型用法、命令示例和注意事项。

---

## 核心本地功能

| 指南 | 说明 |
|------|------|
| [行级版本控制](row-level-versioning.md) | init / add / commit / status / log — 从零开始的完整工作流 |
| [语义差异对比](semantic-diffs.md) | dit diff — added / removed / refreshed 行的检测与 query fingerprinting |
| [三方合并](three-way-merge.md) | branch / checkout / merge / cherry-pick — 行级三方合并与冲突处理 |
| [行级追溯](blame.md) | dit blame — 追踪每一行数据的来源 commit |
| [全文搜索](search.md) | dit search — 跨行全文搜索与字段过滤 |

## 数据质量

| 指南 | 说明 |
|------|------|
| [重复检测](dedup-detection.md) | dit dedup — 完全重复与查询重复的检测（仅报告，不自动清理） |
| [数据校验](validation.md) | .ditvalidate.yaml 规则定义与 dit validate 命令 |
| [Sidecar 元数据](sidecar-metadata.md) | dit meta — 字符数、token 估算、字段数、语言检测 |
| [数据导出](export.md) | dit export — 导出为 JSONL / CSV，支持元数据附带 |

## 远程协作

| 指南 | 说明 |
|------|------|
| [远程协作](remote-collaboration.md) | remote / push / pull / clone — 多人协作完整流程 |
| [稀疏克隆](sparse-clone.md) | clone --sparse — 大数据集的按需拉取 |
| [Pull Request](pull-requests.md) | 服务端 PR 工作流 — 创建、评论、审查、合并 |

## 运维

| 指南 | 说明 |
|------|------|
| [Zstd 压缩与存储](compression.md) | 对象存储布局、zstd 压缩机制、磁盘占用查看 |
| [垃圾回收与完整性检查](garbage-collection.md) | dit gc / dit fsck — 空间回收与数据完整性验证 |

---

## 快速导航

按使用场景查找：

- **刚开始用 Dit？** → [行级版本控制](row-level-versioning.md)
- **想了解 diff 为什么能识别"换了回答"？** → [语义差异对比](semantic-diffs.md)
- **多人协作怎么搞？** → [远程协作](remote-collaboration.md) → [Pull Request](pull-requests.md)
- **数据集太大不想全量下载？** → [稀疏克隆](sparse-clone.md)
- **数据质量检查？** → [重复检测](dedup-detection.md) → [数据校验](validation.md)
- **磁盘空间不够了？** → [垃圾回收](garbage-collection.md)
- **怀疑数据损坏？** → [垃圾回收与完整性检查](garbage-collection.md)（fsck 部分）
