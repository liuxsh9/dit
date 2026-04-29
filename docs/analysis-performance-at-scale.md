# Dit 大规模性能分析

> 基于代码静态分析，非基准测试。假设网络带宽 10 MB/s。

## 测试场景

| 场景 | 规模 | 说明 |
|------|------|------|
| A | 100 GB 仓库 | 10,000 个 JSONL 文件，平均 10 MB，100 个目录 |
| B | 5 GB 单文件 | 1 个 JSONL，500 万行，每行 ~1 KB |
| C | 20 GB 变更 | 单次 commit 修改 2,000 个文件 |
| D | 网络传输 | 10 MB/s 带宽 |

## 对象模型回顾

```
commit → tree → [subtree...] → manifest → row (每行一个对象)
                              → sidecar (统计信息)
```

- 每个 row 对象 = canonical JSON + SHA-256 hash + zstd 压缩后独立存储
- manifest = row hash 列表（每个 entry ~80 bytes: 64 hex hash + fingerprint）
- tree = entry 列表（name + type + hash + sidecar_hash）

## 逐命令分析

---

### dit add

**代码路径**: `main.py:127` → `build_manifest_for_file_streaming()` → 逐行 `read_rows()` + `canonical_json()` + `row_hash()` + `store.write()`

**内存**: 流式处理（`workspace.py:60-72`），每次只持有 1 行。manifest entries 列表在内存中累积，每个 entry ~130 bytes。**不会 OOM。**

| 场景 | 内存峰值 | 磁盘写入 | CPU 瓶颈 | 预估耗时 |
|------|----------|----------|----------|----------|
| B: 5GB/500万行 | ~650 MB (manifest entries) | 500万个 row 对象 + 1 manifest | JCS 序列化 + SHA-256 | ~15-25 分钟 |
| C: 20GB/2000文件 | ~50 MB (单文件 manifest) | 约 2000万 row 对象 | 同上 | ~60-100 分钟 |

**计算过程 (场景 B)**:
- 500万行 × (JSON parse + JCS canonical + SHA-256 + zstd compress + 磁盘写入)
- JSON parse: ~0.5 μs/行 → 2.5s
- JCS canonical: ~1 μs/行 → 5s
- SHA-256 (1KB): ~2 μs/行 → 10s
- zstd compress (1KB): ~5 μs/行 → 25s
- 磁盘写入 (SSD random 4K): ~50 μs/行 → **250s** ← 瓶颈
- manifest entries 内存: 500万 × 130 bytes ≈ 650 MB

**P1 问题: 每行一次磁盘写入**
`store.write()` 对每个 row 对象执行: `mkdir -p` + `write tmp` + `os.replace`。500万行 = 500万次随机写入。

**P2 问题: manifest entries 内存累积**
500万行的 manifest entries 列表占 ~650 MB。不会 OOM 但不理想。

---

### dit commit

**代码路径**: `main.py:273` → 读 staging index → `build_nested_tree()` → 写 tree + commit 对象

**内存**: 只处理 index 和 tree 结构，与文件内容无关。10,000 文件的 index ~几 MB。

| 场景 | 预估耗时 |
|------|----------|
| A: 10,000 文件 | < 1 秒 |
| C: 2,000 文件变更 | < 1 秒 |

**无性能问题。** commit 只写元数据，不触碰行数据。

---

### dit push

**代码路径**: `main.py:1751` → `walk_commit_objects()` → `batch_exists()` → `upload_batch()`

**关键流程**:
1. `walk_commit_objects`: 遍历本地所有 commit 的完整对象图，收集所有 hash
2. 与 remote 的对象图做差集
3. `batch_exists` 检查 remote 缺少哪些对象
4. `upload_batch` 分批上传（100 个/批 或 10MB/批）

| 场景 | 对象数 | 网络请求 | 传输量 | 预估耗时 |
|------|--------|----------|--------|----------|
| C: 20GB 变更 | ~2000万 row + 2000 manifest | batch_exists: ~2000 请求 + upload: ~数万批 | ~8-12 GB (zstd 压缩后) | **15-20 分钟** |
| 首次 push 100GB | ~1亿 row | batch_exists: ~10,000 请求 + upload: ~数十万批 | ~40-60 GB | **1-2 小时** |

**计算过程 (场景 C, 20GB 变更)**:
- 2000万 row 对象，每个 ~1KB，zstd 压缩后 ~400-600 bytes
- 传输量: 2000万 × 500 bytes ≈ 10 GB
- 10 GB / 10 MB/s = **1000 秒 ≈ 17 分钟**（纯传输）
- batch_exists 请求: 2000万 / 10,000 = 2000 次 HTTP 请求
- upload_batch: 10 MB/批 → ~1000 批

**P0 问题: `walk_commit_objects` 全量遍历**
`walker.py:7-59` 从 HEAD 开始遍历**所有** commit 的完整对象图。对于 100GB 仓库（假设 100 个 commit），它会读取并反序列化所有 commit + tree + manifest 对象，收集所有 row hash 到内存中的 set。

- 1亿 row hash × 64 bytes = **6.4 GB 内存**
- 遍历过程需要读取所有 manifest 对象（每个 ~几十 KB）= 大量随机磁盘读取

这是 push 的最大瓶颈。即使只改了 1 行，push 也要遍历整个历史的对象图。

**P0 问题: `batch_exists` 逐类型串行**
`remote.py:105-111` 的 `batch_exists` 每次最多 10,000 个 hash。2000万 row hash 需要 2000 次 HTTP 请求，串行执行。即使每次 50ms，也要 100 秒。

**P1 问题: upload_batch 使用 base64 编码**
`remote.py:72-96` 将二进制数据 base64 编码后放入 JSON body。base64 膨胀 33%，10 GB 数据变成 13.3 GB 传输。

---

### dit pull

**代码路径**: `main.py:2100` → `_fetch_objects_since()` → `_materialize_tree()`

**关键流程**:
1. 获取 remote HEAD hash
2. `_fetch_objects_since`: 从 remote 下载新 commit + tree + manifest + row 对象
3. `_materialize_tree`: 重建工作目录

| 场景 | 预估耗时 |
|------|----------|
| 增量 pull (100 文件变更) | 30-60 秒 |
| 增量 pull (2000 文件变更) | 15-20 分钟 |

**P1 问题: `_materialize_tree` 逐文件写入**
`workspace.py:75-85` 的 `materialize_file` 逐行从 store 读取 row 对象并写入文件。对于 500万行的文件，需要 500万次 `store.read()`（每次 = 磁盘读取 + zstd 解压）。

---

### dit clone

**代码路径**: `main.py:1880` → 下载所有 commit → `_clone_tree_objects` → 下载 manifest + row → `_materialize_tree`

**关键流程**:
1. 遍历 commit 链，逐个下载 commit 对象
2. 对每个 commit 的 tree 递归下载 tree + manifest 对象
3. 遍历所有 manifest，逐个下载缺失的 row 对象
4. 物化工作目录

| 场景 | HTTP 请求数 | 传输量 | 预估耗时 |
|------|------------|--------|----------|
| A: 100GB 仓库 (full) | ~1亿 (逐 row 下载) | ~40-60 GB | **1-2 小时** |
| A: 100GB 仓库 (sparse) | ~10,000 (tree + sidecar) | ~几 MB | **< 1 分钟** |

**P0 问题: clone 逐个下载 row 对象**
`main.py:1946-1957` 对每个 manifest 的每个 row，调用 `rc.download_object("rows", entry.row_hash)`。这是**逐个 HTTP GET**，没有批量下载。

- 1亿 row = 1亿次 HTTP 请求
- 即使每次 1ms（本地网络），也要 **28 小时**
- 实际网络延迟 ~50ms/请求 → 完全不可行

这是 dit 目前最严重的性能问题。

---

### dit status

**代码路径**: `main.py:350` → `find_jsonl_files()` → `flatten_tree()` → 逐文件比较 manifest hash

**内存**: 加载 HEAD tree 的 flat map + 当前文件列表。10,000 文件 ~几 MB。

| 场景 | 预估耗时 |
|------|----------|
| A: 10,000 文件 | 1-3 秒 (stat cache hit) |
| B: 5GB 单文件 (已修改) | 15-25 分钟 (需重算 manifest) |

**P1 问题: status 对修改过的文件需要重算完整 manifest**
stat cache miss 时，需要重新 `build_manifest_for_file_streaming` 来比较 hash。对 5GB 文件意味着重新处理 500万行。

---

### dit checkout / dit merge

**checkout**: `_materialize_tree` 有优化——比较新旧 tree 的 manifest hash，只重写变化的文件。性能取决于变化文件数量，不取决于仓库总大小。

**merge**: `three_way_merge` 在 manifest 级别比较（文件级），冲突时在 row 级别做三路合并。内存中持有 3 个版本的 manifest entries。

| 场景 | 预估耗时 |
|------|----------|
| checkout (100 文件变化) | 5-15 秒 |
| merge (无冲突, 100 文件) | 5-15 秒 |
| merge (B: 5GB 文件冲突) | 15-25 分钟 (需物化整个文件) |

---

## 性能问题汇总

### P0 — 会导致功能不可用

| # | 问题 | 位置 | 影响 | 建议修复 |
|---|------|------|------|----------|
| 1 | **clone 逐个下载 row 对象** | `main.py:1946-1957` | 1亿 row = 1亿次 HTTP GET，100GB 仓库 clone 需要数天 | 添加 `batch_download` API，一次请求下载多个对象；或打包为 packfile 流式传输 |
| 2 | **push/pull `walk_commit_objects` 全量遍历** | `walker.py:7-59` | 每次 push 遍历完整历史的所有对象，1亿 row hash 占 6.4 GB 内存 | 改为增量遍历：只遍历 local_hash..HEAD 之间的 commit，不遍历 remote 已有的部分 |

### P1 — 性能不可接受但功能可用

| # | 问题 | 位置 | 影响 | 建议修复 |
|---|------|------|------|----------|
| 3 | **add 每行一次磁盘写入** | `store.py:21-37` | 500万行 = 500万次 fsync，5GB 文件 add 需 15-25 分钟 | 批量写入：累积 N 个对象后一次性写入；或使用 packfile 格式 |
| 4 | **upload_batch base64 编码** | `remote.py:72-96` | 传输量膨胀 33% | 改用 multipart/form-data 或 raw binary streaming |
| 5 | **materialize_file 逐行读取 store** | `workspace.py:75-85` | 500万行 = 500万次随机读取 + zstd 解压 | 批量读取：预加载 manifest 中所有 row hash 对应的对象 |
| 6 | **batch_exists 串行请求** | `main.py:1800-1802` | 2000万 hash / 10,000 = 2000 次串行 HTTP | 并行请求或增大 batch size |
| 7 | **status 对大文件需重算 manifest** | `main.py:350+` | 5GB 文件 stat cache miss → 重新处理 500万行 | 分块 hash：文件级 mtime+size 快速判断，只在变化时重算 |

### P2 — 可优化但不紧急

| # | 问题 | 位置 | 影响 | 建议修复 |
|---|------|------|------|----------|
| 8 | **manifest entries 内存累积** | `workspace.py:60-72` | 500万行 manifest ~650 MB | 分块 manifest 或流式写入 |
| 9 | **zstd 逐对象压缩** | `store.py:27` | 小对象（~1KB）压缩比差，overhead 高 | 对小对象批量压缩（dictionary mode 或 packfile） |

---

## 场景耗时估算汇总

### 场景 A: 100 GB 仓库 (10,000 文件 × 10 MB)

| 操作 | 耗时 | 瓶颈 |
|------|------|------|
| `dit add .` (首次) | ~2-4 小时 | 磁盘写入 (1亿 row 对象) |
| `dit commit` | < 1 秒 | — |
| `dit push` (首次) | ~1-2 小时 | 网络传输 + walk_commit_objects 内存 |
| `dit clone` (full) | **数天** | 逐 row HTTP GET (P0 #1) |
| `dit clone --sparse` | < 1 分钟 | — |
| `dit status` | 1-3 秒 | stat cache |
| `dit checkout` (100 文件变化) | 5-15 秒 | materialize |

### 场景 B: 5 GB 单文件 (500万行)

| 操作 | 耗时 | 瓶颈 |
|------|------|------|
| `dit add` | ~15-25 分钟 | 磁盘写入 (500万 row 对象) |
| `dit commit` | < 1 秒 | — |
| `dit status` (文件已修改) | ~15-25 分钟 | 重算 manifest |
| `dit push` (增量, 10% 行变化) | ~3-5 分钟 | 网络传输 |

### 场景 C: 20 GB 变更 (2,000 文件)

| 操作 | 耗时 | 瓶颈 |
|------|------|------|
| `dit add` (2000 文件) | ~60-100 分钟 | 磁盘写入 |
| `dit commit` | < 1 秒 | — |
| `dit push` | ~15-20 分钟 | 网络传输 + base64 膨胀 |

### 场景 D: 网络传输 (10 MB/s)

| 数据量 | 原始 | zstd 压缩后 (~40-60%) | 传输时间 |
|--------|------|----------------------|----------|
| 1 GB | 1 GB | ~500 MB | ~50 秒 |
| 20 GB | 20 GB | ~10 GB | ~17 分钟 |
| 100 GB | 100 GB | ~50 GB | ~85 分钟 |

注意: base64 编码使实际传输量再增加 33%。

---

## 优先修复建议

**第一优先级 (P0)**: 添加 `batch_download` API，解决 clone 不可用问题。这是阻塞性问题——没有批量下载，full clone 对任何超过 1GB 的仓库都不实际。sparse clone 绕过了这个问题，但 `sparse-checkout add` 对大文件同样受影响。

**第二优先级 (P1)**: push 增量遍历。当前 `walk_commit_objects` 遍历完整历史，对于有长历史的大仓库会 OOM。改为只遍历 `remote_hash..local_hash` 之间的新 commit。

**第三优先级 (P1)**: store 批量写入。将 `store.write` 改为支持批量模式，累积对象后一次性写入，减少 fsync 次数。
