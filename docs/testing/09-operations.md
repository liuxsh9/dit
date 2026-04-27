# 09 — 运维功能测试指南

涵盖：blame（溯源）、gc（垃圾回收）、dedup（去重检测）、fsck（完整性校验）、监控（健康检查 / Prometheus / 请求日志）。

---

## 1. 前置条件

本指南假设已有一个多 commit、多分支的仓库。以下脚本可快速构建测试环境：

```bash
# 新建本地仓库
mkdir -p /tmp/dit-ops-test && cd /tmp/dit-ops-test
dit init

# 准备测试数据：train.jsonl 含 messages 字段（用于 query_fingerprint）
cat > train.jsonl <<'EOF'
{"messages":[{"role":"user","content":"介绍Python"}],"response":"Python是一种..."}
{"messages":[{"role":"user","content":"什么是机器学习"}],"response":"机器学习是..."}
{"messages":[{"role":"user","content":"解释梯度下降"}],"response":"梯度下降是..."}
EOF

cat > eval.jsonl <<'EOF'
{"messages":[{"role":"user","content":"介绍Python"}],"response":"Python是...（另一版本）"}
{"messages":[{"role":"user","content":"独立问题A"}],"response":"回答A"}
EOF

dit add train.jsonl
dit commit -m "feat: 初始训练数据 (3 rows)"

# 在 train.jsonl 中追加一行，制造第二次 commit
echo '{"messages":[{"role":"user","content":"什么是深度学习"}],"response":"深度学习是..."}' >> train.jsonl
dit add train.jsonl
dit commit -m "feat: 追加深度学习条目"

# 新增 eval.jsonl
dit add eval.jsonl
dit commit -m "feat: 添加 eval 数据集"

# 记录各 commit hash
export C3_HASH=$(dit log --format json | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[0]['hash'])")
export C2_HASH=$(dit log --format json | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[1]['hash'])")
export C1_HASH=$(dit log --format json | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[2]['hash'])")
echo "C1=$C1_HASH"
echo "C2=$C2_HASH"
echo "C3=$C3_HASH"
```

预期状态（`dit log` 输出示意）：

```
commit <C3_HASH>
Author: alice
Date:   2026-04-25 ...

    feat: 添加 eval 数据集

commit <C2_HASH>
...
    feat: 追加深度学习条目

commit <C1_HASH>
...
    feat: 初始训练数据 (3 rows)
```

> **约定**：后续步骤中 `$C1_HASH`、`$C2_HASH`、`$C3_HASH` 分别代表上述三个 commit 的完整 64 位哈希。API 章节会额外设置 `$REPO`、`$TOKEN`、`$ADMIN_TOKEN`。

---

## 2. CLI: blame — 数据溯源

### 2.1 全文件 blame

```bash
cd /tmp/dit-ops-test
dit blame train.jsonl
```

预期输出（表格格式）：

```
Blame for train.jsonl at heads/main (commit <C3_HASH[:8]>)

 Row  Commit     Author   Date                  Content
────────────────────────────────────────────────────────────────────
   0  <C1[:7]>   alice    2026-04-25 ...        {"messages":[{"role...
   1  <C1[:7]>   alice    2026-04-25 ...        {"messages":[{"role...
   2  <C1[:7]>   alice    2026-04-25 ...        {"messages":[{"role...
   3  <C2[:7]>   alice    2026-04-25 ...        {"messages":[{"role...
────────────────────────────────────────────────────────────────────
4 rows, 2 commits, 1 authors
```

验证检查点：
- [ ] 第 0–2 行（初始 3 条）归属 C1
- [ ] 第 3 行（追加条目）归属 C2
- [ ] 汇总行显示 `4 rows, 2 commits, 1 authors`

### 2.2 指定行 blame（--row N）

```bash
dit blame train.jsonl --row 3
```

预期输出（单行历史）：

```
History for train.jsonl row 3 at heads/main

  Commit    Author   Date                   Event     Content
────────────────────────────────────────────────────────────────────
  <C2[:7]>  alice    2026-04-25 ...         added     {"messages"...
────────────────────────────────────────────────────────────────────
1 events
```

验证检查点：
- [ ] Event 为 `added`
- [ ] Commit 为 C2

```bash
# 越界 row index 应报错
dit blame train.jsonl --row 99
```

- [ ] 退出码为 1，输出包含 `fatal`

### 2.3 JSON 格式输出

```bash
dit blame train.jsonl --format json | python3 -m json.tool
```

预期 JSON 结构：

```json
{
  "commit_hash": "<C3_HASH>",
  "file": "train.jsonl",
  "entries": [
    {
      "row_index": 0,
      "row_hash": "...",
      "commit_hash": "<C1_HASH>",
      "author": "alice",
      "timestamp": ...,
      "query_fingerprint": "...",
      "content_preview": "..."
    },
    ...
  ],
  "summary": {
    "total_rows": 4,
    "unique_commits": 2,
    "unique_authors": 1
  }
}
```

验证检查点：
- [ ] `entries` 数组长度为 4
- [ ] 每个 entry 包含 `row_hash`、`commit_hash`、`author`、`timestamp`、`content_preview`
- [ ] 初始 3 行的 `query_fingerprint` 非空（含 `messages` 字段）

```bash
# 单行历史 JSON
dit blame train.jsonl --row 0 --format json | python3 -m json.tool
```

- [ ] 包含 `events` 数组，事件类型为 `"added"`

### 2.4 指定 --ref

```bash
# 基于较早的 commit 做 blame
dit blame train.jsonl --ref "$C2_HASH"
```

- [ ] 输出显示 4 行（C2 已包含追加的第 4 行，尚未包含 eval.jsonl）
- [ ] 退出码为 0

---

## 3. CLI: gc — 垃圾回收

### 3.1 准备孤立对象

GC 只删除"不可达且超过宽限期"的对象。为制造可测试的场景，需手动写入孤立数据并调整 mtime：

```bash
cd /tmp/dit-ops-test

# 查看当前对象数量
find .dit/objects -type f | wc -l

# 写入一个孤立的 rows 对象（不属于任何 commit）
python3 - <<'PY'
import json, hashlib, time, os
from pathlib import Path

data = json.dumps({"orphan": True}, separators=(",",":"), sort_keys=True).encode()
h = hashlib.sha256(data).hexdigest()
shard = Path(".dit/objects/rows") / h[:2] / h[2:4]
shard.mkdir(parents=True, exist_ok=True)
obj_path = shard / h

import pyzstd
compressed = pyzstd.compress(data)
obj_path.write_bytes(compressed)

# 设为 25 小时前（超过默认 24h 宽限期）
old_time = time.time() - 90000
os.utime(obj_path, (old_time, old_time))
print(f"orphan hash: {h}")
print(f"orphan path: {obj_path}")
PY
```

记录输出的 `<ORPHAN_HASH>` 和 `<ORPHAN_PATH>`。

### 3.2 dry-run 预览

```bash
dit gc --dry-run
```

预期输出：

```
Garbage collection (dry run) — grace period: 24h

Object type    Live   Unreachable   Would delete
──────────────────────────────────────────────────
commits           N             0              0
trees             N             0              0
manifests         N             0              0
rows              5             1              1
sidecars          N             0              0
blobs             0             0              0
──────────────────────────────────────────────────
TOTAL             N             1              1
```

验证检查点：
- [ ] `rows` 行 `Would delete` 列为 1
- [ ] 孤立文件 `<ORPHAN_PATH>` 仍然存在

```bash
ls <ORPHAN_PATH>  # 文件应存在
```

### 3.3 实际 GC 执行

```bash
dit gc
```

预期输出：

```
Deleted 1 unreachable object(s) (1 row).
```

验证检查点：
- [ ] 输出包含 `Deleted`
- [ ] 孤立文件已消失：`ls <ORPHAN_PATH>` 报错 `No such file`
- [ ] 正常对象未被删除：`dit log` 仍正常显示

### 3.4 自定义宽限期

```bash
# 写入一个 2 小时前的孤立对象，使用 --grace 1 删除
python3 - <<'PY'
import json, hashlib, time, os, pyzstd
from pathlib import Path

data = json.dumps({"orphan2h": True}, separators=(",",":"), sort_keys=True).encode()
h = hashlib.sha256(data).hexdigest()
shard = Path(".dit/objects/rows") / h[:2] / h[2:4]
shard.mkdir(parents=True, exist_ok=True)
obj_path = shard / h
obj_path.write_bytes(pyzstd.compress(data))
old_time = time.time() - 7200  # 2小时前
os.utime(obj_path, (old_time, old_time))
print(f"path: {obj_path}")
PY

# dry-run 确认
dit gc --grace 1 --dry-run

# 实际执行
dit gc --grace 1
```

- [ ] `--grace 1` 时，2 小时前的对象被纳入删除范围
- [ ] 对象文件消失

### 3.5 JSON 格式输出

```bash
# 再写一个孤立对象后执行
dit gc --dry-run --format json | python3 -m json.tool
```

预期 JSON 字段：

```json
{
  "live_counts":    {"commits": ..., "trees": ..., "manifests": ..., "rows": ..., "sidecars": 0, "blobs": 0},
  "deleted_counts": {"rows": 1, ...},
  "skipped_counts": {...},
  "total_scanned":  ...,
  "total_deleted":  1,
  "tmp_deleted":    0,
  "errors":         []
}
```

验证检查点：
- [ ] 六类对象均出现在 `live_counts`
- [ ] `errors` 为空数组

---

## 4. CLI: dedup — 重复检测

### 4.1 准备含重复项的数据

```bash
cd /tmp/dit-ops-test

# 在 eval.jsonl 中添加与 train.jsonl 完全相同的一行（精确重复）
python3 - <<'PY'
import json
# 取 train.jsonl 第一行（介绍Python）
row_exact = {"messages":[{"role":"user","content":"介绍Python"}],"response":"Python是一种..."}
# 同 query 不同 response（query 重复）
row_qdup  = {"messages":[{"role":"user","content":"什么是机器学习"}],"response":"机器学习另一版本"}

with open("eval.jsonl", "a") as f:
    f.write(json.dumps(row_exact, ensure_ascii=False) + "\n")
    f.write(json.dumps(row_qdup, ensure_ascii=False) + "\n")
PY

dit add eval.jsonl
dit commit -m "test: 注入重复数据"

export C4_HASH=$(dit log --format json | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[0]['hash'])")
echo "C4=$C4_HASH"
```

### 4.2 默认检测（全部类型）

```bash
dit dedup
```

预期输出示意：

```
Duplicate detection for heads/main (commit <C4[:8]>)

⚠ EXACT DUPLICATES (1 groups, 2 rows) — identical content
────────────────────────────────────────────────────────────
  row_hash    Count  Files
  <HASH[:8]>    2x   train.jsonl (×1), eval.jsonl (×1)

ℹ QUERY DUPLICATES (2 groups, ...) — same query, different response
────────────────────────────────────────────────────────────
  fingerprint  Variants  Files
  ...

Summary: ... rows across 2 files
  Exact duplicates: 1 groups (...) WARNING
  Query duplicates: ... groups (...) INFO
```

验证检查点：
- [ ] 退出码为 1（存在 exact duplicate，severity=warning）
- [ ] 输出显示 `EXACT DUPLICATES`
- [ ] 输出显示 `QUERY DUPLICATES`

### 4.3 --exact-only

```bash
dit dedup --exact-only
```

- [ ] 仅显示 `EXACT DUPLICATES` 部分，不显示 `QUERY DUPLICATES`
- [ ] 退出码为 1

### 4.4 --query-only

```bash
dit dedup --query-only
```

- [ ] 仅显示 `QUERY DUPLICATES` 部分，不显示 `EXACT DUPLICATES`
- [ ] 退出码为 0（query dup 仅 info 级别）

### 4.5 --path 路径过滤

```bash
# 只扫描 train.jsonl，eval.jsonl 不在范围内
dit dedup --path train.jsonl
```

- [ ] 结果中只涉及 `train.jsonl`，不出现跨文件重复
- [ ] 如 train.jsonl 本身无 exact dup，退出码为 0

```bash
# 目录前缀过滤（如有子目录时）
dit dedup --path data/
```

### 4.6 JSON 格式输出

```bash
dit dedup --format json | python3 -m json.tool
```

预期结构：

```json
{
  "commit_hash": "...",
  "exact_duplicates": [
    {
      "row_hash": "...",
      "count": 2,
      "occurrences": [
        {"file": "train.jsonl", "row_index": 0, "content_preview": "..."},
        {"file": "eval.jsonl",  "row_index": 2, "content_preview": "..."}
      ]
    }
  ],
  "query_duplicates": [...],
  "summary": {
    "total_rows": ...,
    "total_files": 2,
    "exact_dup_groups": 1,
    "exact_dup_rows": 2,
    "query_dup_groups": ...,
    "query_dup_rows": ...,
    "severity": "warning"
  }
}
```

验证检查点：
- [ ] `summary.severity` 为 `"warning"`
- [ ] `exact_duplicates[0].occurrences` 长度为 2，分属不同文件

---

## 5. CLI: fsck — 对象库完整性校验

### 5.1 干净仓库的完整校验

```bash
cd /tmp/dit-ops-test
dit fsck
```

预期输出：

```
Object store integrity check

Hash verification:
  commits          N  ✓
  trees            N  ✓
  manifests        N  ✓
  rows             N  ✓
  sidecars         N  ✓
  blobs            N  ✓

Graph verification:
  Refs checked: 1 (1 branch(es), 0 tag(s))
  Commits reachable: 4
  All references valid ✓

✓ No issues found. N objects checked.
```

验证检查点：
- [ ] 退出码为 0
- [ ] 输出包含 `No issues found`
- [ ] 所有对象类型校验标记为 `✓`

### 5.2 跳过哈希校验（--no-hash-check）

```bash
dit fsck --no-hash-check
```

- [ ] 不输出 `Hash verification:` 部分
- [ ] 仍执行 `Graph verification:`
- [ ] 退出码为 0

### 5.3 跳过图校验（--no-graph-check）

```bash
dit fsck --no-graph-check
```

- [ ] 不输出 `Graph verification:` 部分
- [ ] 仍执行 `Hash verification:`
- [ ] 退出码为 0

### 5.4 JSON 格式输出

```bash
dit fsck --format json | python3 -m json.tool
```

预期结构：

```json
{
  "checked_objects": {
    "commits": 4, "trees": 4, "manifests": 3,
    "rows": 10, "sidecars": 0, "blobs": 0
  },
  "errors":   [],
  "warnings": [],
  "total_checked": ...,
  "total_errors":  0,
  "total_warnings": 0
}
```

验证检查点：
- [ ] `errors` 和 `warnings` 均为空数组
- [ ] `total_checked` 等于 `checked_objects` 各项之和

---

## 6. API: blame

> 前提：服务器已启动，`$TOKEN` 有 read 权限，`$ADMIN_TOKEN` 有 admin 权限。先把本地 `/tmp/dit-ops-test` 推送到服务端测试仓库：

```bash
export BASE_URL="http://localhost:8000"
export REPO="dit-ops-test"
export TOKEN="<你的 reader/committer token>"
export ADMIN_TOKEN="<你的 admin token>"

curl -s -X POST "$BASE_URL/api/v1/repos" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"$REPO\"}" | python3 -m json.tool

cd /tmp/dit-ops-test
dit remote add origin "$BASE_URL/$REPO" --token "$ADMIN_TOKEN"
dit push origin main
```

- [ ] 服务端仓库创建成功，或已存在时确认后续 push 可用
- [ ] `dit push origin main` 成功

### 6.1 全文件 blame

```bash
curl -s \
  -H "Authorization: Bearer $TOKEN" \
  "$BASE_URL/api/v1/repos/$REPO/blame/$C3_HASH/train.jsonl" \
  | python3 -m json.tool
```

验证检查点：
- [ ] HTTP 200
- [ ] `entries` 数组长度与本地 blame 结果一致
- [ ] 第 0–2 行 `commit_hash` 指向 C1，第 3 行指向 C2

### 6.2 单行历史（?row=N）

```bash
curl -s \
  -H "Authorization: Bearer $TOKEN" \
  "$BASE_URL/api/v1/repos/$REPO/blame/$C3_HASH/train.jsonl?row=3" \
  | python3 -m json.tool
```

验证检查点：
- [ ] HTTP 200
- [ ] `events[0].event` 为 `"added"`
- [ ] `events[0].commit_hash` 为 C2

### 6.3 越界 row 返回 400

```bash
curl -s -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer $TOKEN" \
  "$BASE_URL/api/v1/repos/$REPO/blame/$C3_HASH/train.jsonl?row=9999"
```

- [ ] HTTP 状态码为 `400`

### 6.4 文件不存在返回 404

```bash
curl -s -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer $TOKEN" \
  "$BASE_URL/api/v1/repos/$REPO/blame/$C3_HASH/nonexistent.jsonl"
```

- [ ] HTTP 状态码为 `404`

---

## 7. API: gc

> GC API 需要 admin 权限（`require_permission("admin")`）。

### 7.1 dry_run 模式（需 admin）

```bash
curl -s -X POST \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"grace_hours": 24, "dry_run": true}' \
  "$BASE_URL/api/v1/repos/$REPO/gc" \
  | python3 -m json.tool
```

预期响应：

```json
{
  "live_counts":    {"commits": ..., ...},
  "deleted_counts": {"rows": ..., ...},
  "skipped_counts": {...},
  "total_scanned":  ...,
  "total_deleted":  ...,
  "tmp_deleted":    0,
  "errors":         []
}
```

验证检查点：
- [ ] HTTP 200
- [ ] `errors` 为空数组

### 7.2 实际 GC（grace_hours: 0 — 谨慎！）

```bash
# 只在测试环境中使用 grace_hours: 0
curl -s -X POST \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"grace_hours": 0, "dry_run": false}' \
  "$BASE_URL/api/v1/repos/$REPO/gc" \
  | python3 -m json.tool
```

验证检查点：
- [ ] HTTP 200
- [ ] 响应中 `total_deleted` 与 dry_run 预测的 `deleted_counts` 总和一致

### 7.3 非 admin 权限返回 403

```bash
curl -s -o /dev/null -w "%{http_code}" \
  -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"dry_run": true}' \
  "$BASE_URL/api/v1/repos/$REPO/gc"
```

- [ ] HTTP 状态码为 `403`

---

## 8. API: dedup

### 8.1 全量重复检测

```bash
curl -s \
  -H "Authorization: Bearer $TOKEN" \
  "$BASE_URL/api/v1/repos/$REPO/dedup/$C4_HASH" \
  | python3 -m json.tool
```

验证检查点：
- [ ] HTTP 200
- [ ] `summary.exact_dup_groups` >= 1
- [ ] `exact_duplicates[0].occurrences` 跨越 train.jsonl 和 eval.jsonl

### 8.2 路径过滤（?path=train.jsonl）

```bash
curl -s \
  -H "Authorization: Bearer $TOKEN" \
  "$BASE_URL/api/v1/repos/$REPO/dedup/$C4_HASH?path=train.jsonl" \
  | python3 -m json.tool
```

验证检查点：
- [ ] `summary.total_files` 为 1
- [ ] `exact_dup_groups` 为 0（跨文件重复消失）

### 8.3 commit 不存在返回 404

```bash
curl -s -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer $TOKEN" \
  "$BASE_URL/api/v1/repos/$REPO/dedup/$( python3 -c 'print("0"*64)')"
```

- [ ] HTTP 状态码为 `404`

---

## 9. API: fsck

> fsck API 需要 admin 权限。

### 9.1 完整校验

```bash
curl -s -X POST \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"check_hashes": true, "check_graph": true}' \
  "$BASE_URL/api/v1/repos/$REPO/fsck" \
  | python3 -m json.tool
```

预期响应：

```json
{
  "checked_objects": {"commits": ..., "trees": ..., "manifests": ..., "rows": ..., "sidecars": 0, "blobs": 0},
  "errors":          [],
  "warnings":        [],
  "total_checked":   ...,
  "total_errors":    0,
  "total_warnings":  0
}
```

验证检查点：
- [ ] HTTP 200
- [ ] `total_errors` 为 0
- [ ] `errors` 和 `warnings` 均为空数组

### 9.2 仅哈希校验（skip graph）

```bash
curl -s -X POST \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"check_hashes": true, "check_graph": false}' \
  "$BASE_URL/api/v1/repos/$REPO/fsck" \
  | python3 -m json.tool
```

- [ ] HTTP 200，`total_errors` 为 0

### 9.3 仅图校验（skip hash）

```bash
curl -s -X POST \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"check_hashes": false, "check_graph": true}' \
  "$BASE_URL/api/v1/repos/$REPO/fsck" \
  | python3 -m json.tool
```

- [ ] HTTP 200，`total_errors` 为 0

### 9.4 非 admin 权限返回 403

```bash
curl -s -o /dev/null -w "%{http_code}" \
  -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{}' \
  "$BASE_URL/api/v1/repos/$REPO/fsck"
```

- [ ] HTTP 状态码为 `403`

---

## 10. 监控: 健康检查

### 10.1 GET /health — 正常状态

```bash
curl -s http://localhost:8000/health | python3 -m json.tool
```

预期响应（HTTP 200）：

```json
{
  "status": "healthy",
  "checks": {
    "database": {
      "status": "healthy",
      "latency_ms": 0.42
    },
    "data_dir": {
      "status": "healthy"
    }
  }
}
```

验证检查点：
- [ ] HTTP 200
- [ ] `status` 为 `"healthy"`
- [ ] `checks.database.status` 为 `"healthy"`，且包含 `latency_ms` 字段
- [ ] `checks.data_dir.status` 为 `"healthy"`

### 10.2 验证 database 检查

```bash
# 获取响应状态码
curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health
```

- [ ] 状态码为 `200`

### 10.3 data_dir 不存在时返回 503

> 仅可在测试环境中模拟：将服务器 `data_dir` 指向不存在的路径后重启。

```bash
# 启动时指定不存在的 data_dir（测试用）
DIT_SERVER_DATA_DIR=/nonexistent/path dit serve &
curl -s http://localhost:8000/health | python3 -m json.tool
```

预期：
- [ ] HTTP 503
- [ ] `status` 为 `"unhealthy"`
- [ ] `checks.data_dir.status` 为 `"unhealthy"`，包含 `error` 字段

---

## 11. 监控: Prometheus 指标

### 11.1 基线请求后获取 /metrics

```bash
# 先制造若干请求
curl -s http://localhost:8000/health > /dev/null
curl -s -H "Authorization: Bearer $TOKEN" \
  "$BASE_URL/api/v1/repos/$REPO/blame/$C3_HASH/train.jsonl" > /dev/null

# 获取 metrics
curl -s http://localhost:8000/metrics
```

### 11.2 dit_http_requests_total

```bash
curl -s http://localhost:8000/metrics | grep dit_http_requests_total
```

预期示例输出：

```
# HELP dit_http_requests_total Total HTTP requests
# TYPE dit_http_requests_total counter
dit_http_requests_total{method="GET",path="/health",status="200"} 2.0
dit_http_requests_total{method="GET",path="/api/v1/repos/{repo}/blame/{hash}/train.jsonl",status="200"} 1.0
```

验证检查点：
- [ ] 出现 `dit_http_requests_total` 计数器
- [ ] path 中 `/health` 请求被计入
- [ ] path 中仓库名被规范化为 `{repo}`，commit hash 被规范化为 `{hash}`

### 11.3 路径规范化验证

发送一个含真实 commit hash 的请求，再查看 metrics：

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  "$BASE_URL/api/v1/repos/$REPO/blame/$C3_HASH/train.jsonl" > /dev/null

curl -s http://localhost:8000/metrics | grep "blame.*{hash}"
```

- [ ] path label 为 `/api/v1/repos/{repo}/blame/{hash}/train.jsonl`（64 位 hex hash 被替换为 `{hash}`，仓库名替换为 `{repo}`）

### 11.4 latency histogram

```bash
curl -s http://localhost:8000/metrics | grep dit_http_request_duration_seconds
```

预期示例：

```
# HELP dit_http_request_duration_seconds HTTP request latency in seconds
# TYPE dit_http_request_duration_seconds histogram
dit_http_request_duration_seconds_bucket{method="GET",path="/health",le="0.01"} 2.0
...
dit_http_request_duration_seconds_sum{method="GET",path="/health"} 0.001234
dit_http_request_duration_seconds_count{method="GET",path="/health"} 2.0
```

验证检查点：
- [ ] 包含 `_bucket`、`_sum`、`_count` 三类指标
- [ ] bucket 边界覆盖 `0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0`

### 11.5 in_progress gauge

```bash
curl -s http://localhost:8000/metrics | grep dit_http_requests_in_progress
```

预期示例（静止状态下应为 0）：

```
# HELP dit_http_requests_in_progress Number of HTTP requests currently being processed
# TYPE dit_http_requests_in_progress gauge
dit_http_requests_in_progress{method="GET"} 0.0
```

验证检查点：
- [ ] 指标存在
- [ ] 服务器空闲时 gauge 值为 0

### 11.6 /metrics 本身不被记录

```bash
# 多次请求 metrics 端点
curl -s http://localhost:8000/metrics > /dev/null
curl -s http://localhost:8000/metrics > /dev/null

# 再次查看，/metrics 自身不应出现在 path label 中
curl -s http://localhost:8000/metrics | grep 'path="/metrics"'
```

- [ ] 无输出（/metrics 请求不被 MetricsMiddleware 计入）

---

## 12. 监控: 请求日志

### 12.1 x-request-id 透传

```bash
# 发送带自定义 x-request-id 的请求，检查响应头
curl -s -v \
  -H "x-request-id: my-test-id-12345" \
  http://localhost:8000/health 2>&1 | grep -i "x-request-id"
```

预期：

```
< x-request-id: my-test-id-12345
```

验证检查点：
- [ ] 响应头中 `x-request-id` 与请求头一致（透传）

### 12.2 自动生成 x-request-id

```bash
# 不带 x-request-id，观察响应头
curl -s -v http://localhost:8000/health 2>&1 | grep -i "x-request-id"
```

预期：

```
< x-request-id: <8位随机hex>
```

验证检查点：
- [ ] 响应头包含 `x-request-id`，格式为 8 位十六进制字符串

### 12.3 结构化 JSON 日志

> 日志输出到 `dit.access` logger，需确认服务端已配置 JSON 日志格式。

查看服务端日志（或将 stdout 重定向到文件），预期每行格式：

```json
{"request_id": "my-test-id-12345", "method": "GET", "path": "/health", "status": 200, "latency_ms": 0.52, "client_ip": "127.0.0.1"}
```

验证检查点：
- [ ] 日志为合法 JSON，可解析
- [ ] 包含 `request_id`、`method`、`path`、`status`、`latency_ms`、`client_ip` 字段
- [ ] HTTP 4xx 以 `WARNING` 级别记录，5xx 以 `ERROR` 级别记录，2xx/3xx 以 `INFO` 级别记录

### 12.4 /metrics 不产生访问日志

```bash
curl -s http://localhost:8000/metrics > /dev/null
```

- [ ] 服务端日志中无 `/metrics` 路径的条目（LoggingMiddleware 跳过 /metrics）

---

## 13. 边界场景

### 13.1 blame 不存在的文件

```bash
cd /tmp/dit-ops-test
dit blame nonexistent.jsonl
```

- [ ] 退出码为 1
- [ ] 输出包含 `fatal: ...not found`

### 13.2 blame 在无 commit 的仓库

```bash
mkdir /tmp/dit-empty && cd /tmp/dit-empty
dit init
dit blame train.jsonl
```

- [ ] 退出码为 1（`fatal: no commits on branch 'main'`）

### 13.3 GC grace=0（危险操作，仅测试环境）

```bash
cd /tmp/dit-ops-test

# 写入一个"新鲜"的孤立对象（刚写入，mtime=now）
python3 - <<'PY'
import json, hashlib, pyzstd
from pathlib import Path

data = json.dumps({"fresh_orphan": True}, separators=(",",":"), sort_keys=True).encode()
h = hashlib.sha256(data).hexdigest()
shard = Path(".dit/objects/rows") / h[:2] / h[2:4]
shard.mkdir(parents=True, exist_ok=True)
(shard / h).write_bytes(pyzstd.compress(data))
print(f"hash={h}")
PY

# 默认 grace=24h 时，新鲜对象不被删除
dit gc --dry-run
# → Would delete 列应为 0（在宽限期内）

# grace=0 时（秒数=0），所有孤立对象立即可被删除
dit gc --grace 0 --dry-run
# → 新鲜对象也出现在 Would delete 列中
```

验证检查点：
- [ ] `--grace 24` dry-run：新鲜孤立对象计入 `Unreachable`，但 `Would delete` 为 0，并显示 `within grace period (skipped)`
- [ ] `--grace 0` dry-run：新鲜孤立对象出现在 `would delete` 列

> 警告：生产环境**切勿**使用 `--grace 0`，会删除正在写入中的对象。

### 13.4 dedup 在单文件仓库（无跨文件重复）

```bash
mkdir /tmp/dit-single && cd /tmp/dit-single
dit init
cat > data.jsonl <<'EOF'
{"messages":[{"role":"user","content":"q1"}],"response":"a1"}
{"messages":[{"role":"user","content":"q2"}],"response":"a2"}
EOF
dit add data.jsonl
dit commit -m "single file"

dit dedup
```

- [ ] 退出码为 0
- [ ] 输出包含 `No duplicates found`

```bash
# 在同一文件中添加精确重复
echo '{"messages":[{"role":"user","content":"q1"}],"response":"a1"}' >> data.jsonl
dit add data.jsonl
dit commit -m "add exact dup"

dit dedup
```

- [ ] 退出码为 1
- [ ] 输出包含 `EXACT DUPLICATES`，文件列只有 `data.jsonl`

### 13.5 fsck 检测损坏对象（手动破坏）

```bash
cd /tmp/dit-ops-test

# 找到一个 rows 对象路径
ROW_FILE=$(find .dit/objects/rows -type f | head -1)
echo "将要破坏: $ROW_FILE"

# 备份
cp "$ROW_FILE" "${ROW_FILE}.bak"

# 破坏文件内容（写入非法 zstd 数据）
echo "not valid zstd" > "$ROW_FILE"

# 运行 fsck
dit fsck
```

预期输出：

```
Object store integrity check

Hash verification:
  rows            X  ✗ 1 error(s)
  ...

ERRORS (1):
  [rows] <HASH[:16]>...: corrupt object: decompression failed

✗ 1 error(s), 0 warning(s). N objects checked.
```

验证检查点：
- [ ] 退出码为 1
- [ ] 输出包含 `corrupt object: decompression failed`
- [ ] `ERRORS` 部分列出受影响的对象

```bash
# 还原文件
cp "${ROW_FILE}.bak" "$ROW_FILE"
dit fsck
# → 退出码 0，No issues found
```

### 13.6 fsck --format json 检测损坏

```bash
# 再次破坏一个对象
ROW_FILE=$(find .dit/objects/rows -type f | head -1)
cp "$ROW_FILE" "${ROW_FILE}.bak"
python3 -c "import pyzstd; open('$ROW_FILE', 'wb').write(pyzstd.compress(b'corrupted content'))"

dit fsck --format json | python3 -m json.tool
```

预期 JSON 包含：

```json
{
  "errors": [
    {
      "severity": "error",
      "obj_type": "rows",
      "obj_hash": "...",
      "message": "hash mismatch: ..."
    }
  ],
  "total_errors": 1
}
```

验证检查点：
- [ ] `total_errors` >= 1
- [ ] `errors[0].severity` 为 `"error"`
- [ ] `errors[0].message` 包含 `hash mismatch` 或 `corrupt`

```bash
# 还原
cp "${ROW_FILE}.bak" "$ROW_FILE"
```

### 13.7 blame 在悬挂引用场景

```bash
cd /tmp/dit-ops-test

# 使用一个不存在的 commit hash
dit blame train.jsonl --ref 0000000000000000000000000000000000000000000000000000000000000001
```

- [ ] 退出码为 1
- [ ] 输出包含 `fatal`

### 13.8 API: blame 未授权

```bash
curl -s -o /dev/null -w "%{http_code}" \
  "$BASE_URL/api/v1/repos/$REPO/blame/$C3_HASH/train.jsonl"
```

- [ ] 状态码为 `401` 或 `403`（无 token 时）
