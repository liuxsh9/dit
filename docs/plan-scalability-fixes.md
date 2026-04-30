# 可扩展性修复计划

> 目标：解决 review 中发现的 P0-P2 级别问题，使 dit 能够在真实生产环境（几十GB数据集、百万行文件、几十人团队）中可靠运行。
> P3 问题（sparse clone、Web UI 等）暂不处理。与前端耦合的问题遗留。

## 修复范围与优先级

### Phase 1: 内存安全 — 流式 add（P0-1）

**问题**：`build_manifest_for_file()` 将所有 row_data 加载到内存 dict 中。10GB 文件 → OOM。

**修复方案**：
- 新增 `build_manifest_for_file_streaming(path, store)` — 逐行读取、计算 hash、立即写入 store，不在内存中保留 row bytes
- 返回 `Manifest` 对象（entries 列表仍需保留，但每个 entry 仅 ~130 bytes，百万行 ≈ 130MB，可接受）
- CLI `add` 命令改用 streaming 版本
- 保留原 `build_manifest_for_file()` 签名不变（向后兼容，用于小文件场景如 diff/status）

**影响文件**：
- `src/dit/core/workspace.py` — 新增 streaming 函数
- `src/dit/cli/main.py:166` — add 命令改用 streaming 版本

**测试**：验证 streaming 版本产出与原版本一致的 Manifest

---

### Phase 2: Walker 迭代化 + 防栈溢出（P1-8 + P0-3 部分）

**问题**：`walker.py` 使用递归 DFS，超过 ~1000 commits 会 RecursionError。

**修复方案**：
- `_walk_commit` / `_walk_tree` / `_walk_manifest` 改为显式栈的迭代实现
- `_is_ancestor_dfs` 同样改为迭代
- 保持函数签名和返回值不变

**影响文件**：
- `src/dit/core/walker.py`

**测试**：新增测试验证 1500+ commit 链不会 RecursionError

---

### Phase 3: Index 文件锁（P1-5）

**问题**：`StagingIndex` 无锁，并发 `dit add` 会丢数据。

**修复方案**：
- 使用 `fcntl.flock()` (Unix) 对 index 文件加排他锁
- 在 `_load` + `_write` 操作期间持锁
- 提供 context manager `locked()` 供外部使用
- 锁获取超时 10s，超时后抛出明确错误

**影响文件**：
- `src/dit/core/index.py`

**测试**：多进程并发写入测试，验证不丢数据

---

### Phase 4: Push 批量上传（P0-2 部分）

**问题**：push 逐对象上传，百万对象需要百万次 HTTP 请求。

**修复方案**：
- `RemoteClient` 新增 `upload_batch(obj_type, items: list[tuple[str, bytes]])` 方法
- 服务端新增 `/objects/batch-upload` 端点，接收多个对象
- CLI push 改为分批上传（每批 100 个对象或 10MB，取较小值）
- 保留单对象上传作为 fallback（兼容旧版 server）

**影响文件**：
- `src/dit/core/remote.py` — 新增 batch upload 方法
- `src/dit/server/routes/objects.py` — 新增 batch upload 端点
- `src/dit/cli/main.py:1528-1536` — push 改用批量上传

**测试**：集成测试验证批量上传正确性

---

### Phase 5: Status/Diff 的 stat cache（P0-4）

**问题**：`status` 和 `diff` 每次都重新解析整个 JSONL 文件计算 manifest hash。

**修复方案**：
- 新增 `.dit/stat-cache` 文件，存储 `{rel_path: {mtime, size, manifest_hash}}`
- `status`/`diff` 先检查 mtime+size 是否变化，未变化则直接使用缓存的 manifest_hash
- `add` 命令成功后更新 stat-cache
- cache miss 时 fallback 到完整计算（安全降级）

**影响文件**：
- `src/dit/core/stat_cache.py` — 新模块
- `src/dit/cli/main.py` — status/diff 使用 cache

**测试**：验证 cache hit/miss 行为正确

---

## 遗留项（不在本轮修复）

| 问题 | 原因 |
|------|------|
| Sparse/Shallow clone | P3，需要协议层设计 |
| Pack 文件 | P3，架构变更大 |
| Push 增量发现（不走全历史） | 依赖 commit graph cache，复杂度高，后续迭代 |
| query_fingerprint 碰撞风险 | P2，需要与数据格式团队讨论 |
| GC 全量扫描优化 | P2，当前规模可接受 |
| 前端耦合问题 | 等用户修改 dit-gateway 后再处理 |

## 执行原则

1. 每个 Phase 独立一个 commit
2. TDD：先写测试，再实现
3. 保守修改：不改变公共 API 签名，新增函数而非修改旧函数
4. 所有现有测试必须继续通过
