# 垃圾回收与完整性检查

Dit 提供 `dit gc` 和 `dit fsck` 两个维护命令，分别用于清理不可达对象和验证对象存储完整性。

## dit gc — 垃圾回收

### 什么是不可达对象

"不可达"指的是没有被任何 branch、tag 或 staging index 引用链所指向的对象。常见产生场景：

- 删除分支后，该分支独有的 commit 链变为不可达
- `dit add` 后未 commit，之后又 `dit add` 了新版本，旧 manifest/row 变为不可达
- push 或 merge 操作中途失败，留下的中间对象
- 进程崩溃留下的 tmp 文件

### 预览模式（推荐先执行）

```bash
dit gc --dry-run
```

输出示例：

```text
Garbage collection (dry run) — grace period: 24h

Object type    Live    Unreachable    Would delete
─────────────────────────────────────────────────────
commits           5              2               1
trees             5              2               1
manifests         3              1               1
rows             15              3               2
sidecars          3              0               0
blobs             1              0               0
─────────────────────────────────────────────────────
TOTAL            32              8               5

3 unreachable objects within grace period (skipped).
1 stale tmp file would be deleted.
```

### 执行回收

```bash
# 使用默认 24 小时 grace period
dit gc

# 自定义 grace period（单位：小时）
dit gc --grace 48

# 更激进的回收（仅清理 1 小时前的对象）
dit gc --grace 1
```

执行输出：

```text
Garbage collection — grace period: 24h

Deleted 5 unreachable objects (1 commit, 1 tree, 1 manifest, 2 rows).
Deleted 1 stale tmp file.
Freed ~12.4 KB.
```

### Grace Period 的作用

Grace period 保护正在进行中的操作。例如 push 过程中，对象已写入但 ref 尚未更新，此时这些对象技术上"不可达"，但不应被删除。默认 24 小时足以覆盖所有正常操作场景。

只有 mtime 早于 grace period 的不可达对象才会被删除。

### JSON 输出

```bash
dit gc --dry-run --format json
```

适合在脚本或 CI 中使用，输出结构化的回收统计。

---

## dit fsck — 完整性检查

### 基本用法

```bash
dit fsck
```

输出示例（健康仓库）：

```text
Object store integrity check

Hash verification:
  commits           5  ✓
  trees             5  ✓
  manifests         3  ✓
  rows             15  ✓
  sidecars          3  ✓
  blobs             1  ✓

Graph verification:
  Refs checked: 3 (2 branch(es), 1 tag(s))
  Commits reachable: 5
  All references valid ✓

✓ No issues found. 32 objects checked.
```

### 检查内容

fsck 执行两类验证：

1. **Hash 验证** — 解压每个对象文件，重新计算 SHA-256，与文件名比对。检测磁盘损坏或意外篡改。
2. **Graph 验证** — 从所有 ref 出发遍历对象图，确认每个引用的对象都存在且可读。检测悬空引用。

### 跳过部分检查

```bash
# 只做 graph 验证（跳过耗时的 hash 校验）
dit fsck --no-hash-check

# 只做 hash 验证（跳过 graph 遍历）
dit fsck --no-graph-check

# JSON 输出
dit fsck --format json
```

### 发现问题时的输出

```text
Object store integrity check

Hash verification:
  commits           5  ✓
  trees             5  ✓
  manifests         3  ✗ 1 error(s)
  rows             15  ✓
  sidecars          3  ✓
  blobs             1  ✓

Graph verification:
  Refs checked: 3 (2 branch(es), 1 tag(s))
  Commits reachable: 5
  1 missing or dangling reference(s) found

ERRORS (2):
  [manifests] a3f1b2c4d5e6f7a8...: hash mismatch: expected a3f1b2c4d5e6f7a8..., got 9c8b7a6f5e4d3c2b...
  [rows] 1234abcd5678ef90...: missing row object

✗ 2 error(s), 0 warning(s). 32 objects checked.
```

---

## 最佳实践

- **先 dry-run 再执行**：养成 `dit gc --dry-run` 的习惯，确认回收范围合理后再真正执行
- **定期回收**：频繁的分支创建/删除、merge 操作后，运行 gc 释放磁盘空间
- **怀疑损坏时跑 fsck**：磁盘故障、异常断电、手动修改 `.dit/` 目录后，立即执行 fsck
- **恢复备份后跑 fsck**：确认备份数据的完整性
- **不要在操作进行中跑 gc**：确保没有正在执行的 push/merge/commit 操作，或使用足够长的 grace period
- **gc 不可逆**：被删除的对象无法恢复，如果不确定，使用较长的 grace period（如 `--grace 72`）
