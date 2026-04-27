# Dit 手动测试指南 05：服务端核心 API

本指南覆盖 Dit Server 的核心 REST API 端点，通过 curl 直接测试，不经过 `dit` CLI。适合验证服务端行为、调试集成问题，或在没有 CLI 客户端的环境中使用。

**前置条件**：
- 已完成 **指南 00**（服务端在 `localhost:8000` 正常运行，Admin 令牌已备好）
- 已完成 **指南 03**（至少有一个仓库，并有数据已推送，存在 `heads/main` 引用）

---

## 目录

1. [前置条件确认](#1-前置条件确认)
2. [仓库管理 Repos](#2-仓库管理-repos)
3. [引用管理 Refs](#3-引用管理-refs)
4. [对象操作 Objects](#4-对象操作-objects)
5. [树浏览 Tree](#5-树浏览-tree)
6. [提交日志 Log](#6-提交日志-log)
7. [Manifest 浏览](#7-manifest-浏览)
8. [Diff API](#8-diff-api)
9. [权限验证](#9-权限验证)
10. [边界场景](#10-边界场景)

---

## 1. 前置条件确认

### 1.1 确认服务端运行

```bash
curl -s http://localhost:8000/health | python3 -m json.tool
```

预期输出：
```json
{
    "status": "healthy",
    "checks": {
        "database": {
            "status": "healthy",
            "latency_ms": 1.23
        },
        "data_dir": {
            "status": "healthy"
        }
    }
}
```

验证清单：
- [ ] 返回 HTTP 200，`status` 为 `"healthy"`
- [ ] `checks.database.status` 和 `checks.data_dir.status` 均为 `"healthy"`

### 1.2 设置环境变量

后续所有命令均使用这些变量。根据实际情况修改值。

```bash
# Admin 令牌（可执行所有操作）
export TOKEN="<你的 admin token>"

# 服务端地址
export BASE="http://localhost:8000"

# 目标仓库（指南 03 中推送过数据的仓库）
export REPO="my-dataset"
```

### 1.3 确认令牌有效

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos" | python3 -m json.tool
```

预期输出（仓库列表，可能为空数组）：
```json
[
    {"id": 1, "name": "my-dataset"}
]
```

验证清单：
- [ ] 返回 HTTP 200，不出现 `401 Unauthorized`

### 1.4 获取已有的提交哈希

后续测试需要真实的 commit hash。先查询 `heads/main` 获取：

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/refs/heads/main" | python3 -m json.tool
```

预期输出：
```json
{
    "name": "heads/main",
    "target_hash": "a1b2c3d4e5f6..."
}
```

```bash
# 将 commit hash 保存到变量，后续步骤使用
export COMMIT_HASH="<target_hash 的值>"
```

验证清单：
- [ ] 返回 HTTP 200，`target_hash` 为 64 位十六进制字符串

---

## 2. 仓库管理 Repos

### 2.1 创建仓库

**需要 admin 权限。**

```bash
curl -s -X POST "$BASE/api/v1/repos" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"name": "test-api-repo"}' | python3 -m json.tool
```

预期输出（HTTP 201）：
```json
{
    "id": 2,
    "name": "test-api-repo"
}
```

验证清单：
- [ ] 返回 HTTP 201
- [ ] 响应包含 `id`（整数）和 `name`

### 2.2 重复创建同名仓库

```bash
curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/api/v1/repos" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"name": "test-api-repo"}'
```

预期输出：
```
409
```

验证清单：
- [ ] 返回 HTTP 409（Conflict）

### 2.3 列出所有仓库

**需要 read 权限。**

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos" | python3 -m json.tool
```

预期输出：
```json
[
    {"id": 1, "name": "my-dataset"},
    {"id": 2, "name": "test-api-repo"}
]
```

验证清单：
- [ ] 返回 HTTP 200，结果按仓库名字母顺序排列
- [ ] 包含刚创建的 `test-api-repo`

---

## 3. 引用管理 Refs

以下操作均针对 `$REPO` 仓库（指南 03 中已有数据的仓库）。

### 3.1 列出仓库的所有引用

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/refs" | python3 -m json.tool
```

预期输出（至少包含 `heads/main`）：
```json
[
    {
        "name": "heads/main",
        "target_hash": "a1b2c3d4e5f6..."
    }
]
```

验证清单：
- [ ] 返回 HTTP 200，为数组
- [ ] 包含 `heads/main` 引用

### 3.2 获取单个引用

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/refs/heads/main" | python3 -m json.tool
```

预期输出：
```json
{
    "name": "heads/main",
    "target_hash": "a1b2c3d4e5f6..."
}
```

验证清单：
- [ ] 返回 HTTP 200
- [ ] `name` 为 `"heads/main"`，`target_hash` 为 64 位哈希

### 3.3 创建新引用（CAS 写入，old 为 null）

**需要 push（committer）权限。**

CAS（Compare-And-Swap）：当 `old` 为 `null` 或空字符串时，执行插入操作。

```bash
curl -s -X POST "$BASE/api/v1/repos/$REPO/refs/heads/feature-api-test" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d "{\"old\": null, \"new\": \"$COMMIT_HASH\"}" | python3 -m json.tool
```

预期输出（HTTP 200）：
```json
{
    "name": "heads/feature-api-test",
    "target_hash": "a1b2c3d4e5f6..."
}
```

验证清单：
- [ ] 返回 HTTP 200
- [ ] `target_hash` 与传入的 `new` 值一致

### 3.4 CAS 更新引用

将刚创建的 `feature-api-test` 分支的引用更新，模拟推送新提交。  
`new` 应使用真实存在的 commit hash。若当前测试仓库只有一个提交，可暂时复用 `$COMMIT_HASH` 验证 CAS 机制；不要使用随机构造的哈希，避免把引用指向不存在的对象。

```bash
export OLD_HASH="$COMMIT_HASH"

# 优先从日志中取另一个真实 commit；如果没有，就回退为当前 COMMIT_HASH
export NEW_HASH=$(curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/log?ref=heads/main&limit=2" \
     | python3 -c "import sys,json; c=json.load(sys.stdin)['commits']; print(c[0]['commit_hash'] if c else '')")
export NEW_HASH="${NEW_HASH:-$COMMIT_HASH}"

curl -s -X POST "$BASE/api/v1/repos/$REPO/refs/heads/feature-api-test" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d "{\"old\": \"$OLD_HASH\", \"new\": \"$NEW_HASH\"}" | python3 -m json.tool
```

预期输出（HTTP 200）：
```json
{
    "name": "heads/feature-api-test",
    "target_hash": "<NEW_HASH>"
}
```

验证清单：
- [ ] 返回 HTTP 200
- [ ] `target_hash` 已更新为新值

### 3.5 CAS 冲突（old 值与当前不匹配）

用错误的 `old` 值模拟并发冲突：

```bash
curl -s -X POST "$BASE/api/v1/repos/$REPO/refs/heads/feature-api-test" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"old": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc", "new": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"}' | python3 -m json.tool
```

预期输出（HTTP 409）：
```json
{
    "detail": "CAS conflict: expected cccccccc..."
}
```

验证清单：
- [ ] 返回 HTTP 409（Conflict）
- [ ] 错误信息包含 `"CAS conflict"`

### 3.6 创建标签引用

```bash
curl -s -X POST "$BASE/api/v1/repos/$REPO/refs/tags/v1.0.0" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d "{\"old\": null, \"new\": \"$COMMIT_HASH\"}" | python3 -m json.tool
```

预期输出（HTTP 200）：
```json
{
    "name": "tags/v1.0.0",
    "target_hash": "a1b2c3d4e5f6..."
}
```

验证清单：
- [ ] 返回 HTTP 200，`name` 为 `"tags/v1.0.0"`

### 3.7 删除引用

**需要 admin 权限。**

```bash
curl -s -X DELETE "$BASE/api/v1/repos/$REPO/refs/heads/feature-api-test" \
     -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

预期输出（HTTP 200）：
```json
{
    "status": "deleted"
}
```

再次查询确认已删除：

```bash
curl -s -o /dev/null -w "%{http_code}" \
     -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/refs/heads/feature-api-test"
```

预期输出：
```
404
```

验证清单：
- [ ] DELETE 返回 HTTP 200，`status` 为 `"deleted"`
- [ ] 随后的 GET 返回 HTTP 404

---

## 4. 对象操作 Objects

对象操作仅需通过 `verify_token` 验证（任意有效令牌即可，不区分权限级别）。

### 4.1 上传对象（行数据）

对象路径格式：`/api/v1/repos/{repo}/objects/{obj_type}/{hash}`  
上传时服务端会校验请求体的 SHA-256 哈希与路径中的哈希是否一致。

```bash
# 准备测试数据
export OBJ_BODY='{"messages": [{"role": "user", "content": "hello api test"}]}'
export OBJ_HASH=$(echo -n "$OBJ_BODY" | sha256sum | awk '{print $1}')
echo "对象哈希：$OBJ_HASH"
```

```bash
curl -s -o /dev/null -w "%{http_code}" \
     -X POST "$BASE/api/v1/repos/$REPO/objects/rows/$OBJ_HASH" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/octet-stream" \
     --data-binary "$OBJ_BODY"
```

预期输出：
```
204
```

验证清单：
- [ ] 返回 HTTP 204（No Content）

### 4.2 上传幂等性验证

重复上传同一对象应返回 204，不报错：

```bash
curl -s -o /dev/null -w "%{http_code}" \
     -X POST "$BASE/api/v1/repos/$REPO/objects/rows/$OBJ_HASH" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/octet-stream" \
     --data-binary "$OBJ_BODY"
```

预期输出：
```
204
```

验证清单：
- [ ] 再次上传仍返回 HTTP 204（幂等）

### 4.3 下载对象

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/objects/rows/$OBJ_HASH"
```

预期输出（原始字节，即上传时的内容）：
```
{"messages": [{"role": "user", "content": "hello api test"}]}
```

验证清单：
- [ ] 返回 HTTP 200，响应体与上传内容完全一致
- [ ] `Content-Type` 为 `application/octet-stream`

### 4.4 批量存在性检查

检查哪些对象已存在于服务端：

```bash
export MISSING_HASH="ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"

curl -s -X POST "$BASE/api/v1/repos/$REPO/objects/batch-exists" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d "{\"obj_type\": \"rows\", \"hashes\": [\"$OBJ_HASH\", \"$MISSING_HASH\"]}" \
     | python3 -m json.tool
```

预期输出：
```json
{
    "exists": {
        "<OBJ_HASH>": true,
        "ffffffffffffffff...": false
    }
}
```

验证清单：
- [ ] 返回 HTTP 200
- [ ] 已上传的哈希值为 `true`，未知哈希为 `false`

---

## 5. 树浏览 Tree

树浏览端点用于查看某次提交下的目录结构，路径格式：  
`GET /api/v1/repos/{repo}/tree/{commit_hash}/{path}`

`path` 为空或 `/` 时查看根目录。

### 5.1 浏览根目录树

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/tree/$COMMIT_HASH/" | python3 -m json.tool
```

预期输出（具体内容取决于推送的数据）：
```json
{
    "commit_hash": "a1b2c3d4e5f6...",
    "path": "",
    "entries": [
        {
            "name": "train",
            "obj_type": "tree",
            "obj_hash": "e3f4a5b6...",
            "sidecar_hash": null
        },
        {
            "name": "README.md",
            "obj_type": "blob",
            "obj_hash": "c7d8e9f0...",
            "sidecar_hash": null
        }
    ]
}
```

验证清单：
- [ ] 返回 HTTP 200
- [ ] `entries` 为数组，每项包含 `name`、`obj_type`、`obj_hash`、`sidecar_hash`
- [ ] 目录显示为 `obj_type: "tree"`，文件为 `"blob"` 或 `"manifest"`

### 5.2 浏览子目录

查看 `train` 子目录（将 `train` 替换为实际存在的目录名）：

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/tree/$COMMIT_HASH/train" | python3 -m json.tool
```

预期输出：
```json
{
    "commit_hash": "a1b2c3d4e5f6...",
    "path": "train",
    "entries": [
        {
            "name": "sft.jsonl",
            "obj_type": "manifest",
            "obj_hash": "d1e2f3a4...",
            "sidecar_hash": null
        }
    ]
}
```

验证清单：
- [ ] 返回 HTTP 200，`path` 字段为 `"train"`
- [ ] 数据文件 `.jsonl` 的 `obj_type` 为 `"manifest"`

---

## 6. 提交日志 Log

日志端点返回某个引用或提交哈希的提交历史，支持分页。

**参数说明：**
- `ref`（必填）：引用名称（如 `heads/main`）或直接传入 commit hash
- `limit`：每页返回条数，默认 20，最大 200
- `offset`：跳过前 N 条，默认 0

### 6.1 查询默认日志（最近 20 条）

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/log?ref=heads/main" | python3 -m json.tool
```

预期输出（含提交列表，最新提交在最前）：
```json
{
    "ref": "heads/main",
    "total_fetched": 3,
    "offset": 0,
    "limit": 3,
    "commits": [
        {
            "commit_hash": "a1b2c3d4...",
            "tree_hash": "e5f6a7b8...",
            "parent_hashes": ["c9d0e1f2..."],
            "author": "alice",
            "message": "add eval data",
            "timestamp": 1714000000
        },
        ...
    ]
}
```

验证清单：
- [ ] 返回 HTTP 200
- [ ] `commits` 数组按时间倒序（最新提交在最前）
- [ ] 每个 commit 包含 `commit_hash`、`author`、`message`、`timestamp`、`parent_hashes`

### 6.2 分页：取第一页（limit=2）

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/log?ref=heads/main&limit=2&offset=0" \
     | python3 -m json.tool
```

验证清单：
- [ ] 返回 HTTP 200
- [ ] `commits` 数组长度为 2（或不足 2 条时为实际条数）
- [ ] 返回的是最近的 2 次提交

### 6.3 分页：跳过前两条（offset=2）

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/log?ref=heads/main&limit=10&offset=2" \
     | python3 -m json.tool
```

验证清单：
- [ ] 返回 HTTP 200
- [ ] `offset` 字段值为 2
- [ ] 返回的 commits 不含最近的 2 次提交

### 6.4 通过 commit hash 直接查询日志

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/log?ref=$COMMIT_HASH&limit=5" \
     | python3 -m json.tool
```

验证清单：
- [ ] 返回 HTTP 200，以指定的 commit 为起点向前追溯

---

## 7. Manifest 浏览

Manifest 端点用于分页查看某个 `.jsonl` 文件在特定提交下的行列表（行哈希 + 查询指纹）。

**路径格式：**  
`GET /api/v1/repos/{repo}/manifest/{commit_hash}/{path}`

**参数说明：**
- `offset`：行偏移，默认 0
- `limit`：每页行数，默认 50，上限 500

### 7.1 查询 manifest 文件的完整列表

将 `train/sft.jsonl` 替换为实际存在的 `.jsonl` 文件路径：

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/manifest/$COMMIT_HASH/train/sft.jsonl" \
     | python3 -m json.tool
```

预期输出：
```json
{
    "commit_hash": "a1b2c3d4...",
    "path": "train/sft.jsonl",
    "total": 1000,
    "offset": 0,
    "limit": 50,
    "entries": [
        {
            "row_hash": "f1e2d3c4b5a6...",
            "query_fingerprint": "q_abc123"
        },
        ...
    ]
}
```

验证清单：
- [ ] 返回 HTTP 200
- [ ] `total` 为文件总行数
- [ ] `entries` 每项含 `row_hash`（64 位哈希）和 `query_fingerprint`
- [ ] 默认返回 50 条（或不足 50 条时为实际条数）

### 7.2 分页查询：取第 2 页（offset=50, limit=50）

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/manifest/$COMMIT_HASH/train/sft.jsonl?offset=50&limit=50" \
     | python3 -m json.tool
```

验证清单：
- [ ] 返回 HTTP 200
- [ ] `offset` 为 50，`entries` 为第 51-100 行

### 7.3 limit 超上限（自动钳制到 500）

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/manifest/$COMMIT_HASH/train/sft.jsonl?limit=9999" \
     | python3 -m json.tool
```

验证清单：
- [ ] 返回 HTTP 200，实际返回条数不超过 500

---

## 8. Diff API

Diff 端点比较两个提交（或两个引用）之间的 manifest 差异。  
**方法：POST**，请求体为 JSON。

**请求字段：**
- `old_commit` / `new_commit`：直接使用 commit hash
- `from_ref` / `to_ref`：使用引用名称（与 commit 字段互斥）
- `path`（可选）：只比较指定文件路径
- `include_rows`（可选，默认 false）：是否在结果中包含具体的行内容
- `offset` / `limit`：行级分页（仅 `include_rows=true` 时有效）

### 8.1 比较两个引用

首先准备两个提交哈希。若仓库只有一个提交，跳过此步；若有多个提交，先从日志获取旧的提交哈希：

```bash
# 从日志中取倒数第二条提交的哈希
export OLD_COMMIT=$(curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/log?ref=heads/main&limit=2" \
     | python3 -c "import sys,json; c=json.load(sys.stdin)['commits']; print(c[-1]['commit_hash']) if len(c)>1 else print(c[0]['commit_hash'])")
export NEW_COMMIT="$COMMIT_HASH"
echo "old=$OLD_COMMIT"
echo "new=$NEW_COMMIT"
```

```bash
curl -s -X POST "$BASE/api/v1/repos/$REPO/diff" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d "{\"old_commit\": \"$OLD_COMMIT\", \"new_commit\": \"$NEW_COMMIT\"}" \
     | python3 -m json.tool
```

预期输出（若两次提交之间有文件变化）：
```json
{
    "old_commit": "c9d0e1f2...",
    "new_commit": "a1b2c3d4...",
    "summary": {
        "files_changed": 1,
        "rows_added": 50,
        "rows_removed": 10,
        "rows_refreshed": 0
    },
    "files": [
        {
            "path": "train/sft.jsonl",
            "added": 50,
            "removed": 10,
            "refreshed": 0,
            "old_total": 900,
            "new_total": 940
        }
    ]
}
```

验证清单：
- [ ] 返回 HTTP 200
- [ ] `summary` 包含 `files_changed`、`rows_added`、`rows_removed`、`rows_refreshed`
- [ ] `files` 数组列出有变化的文件及其统计

### 8.2 使用引用名称比较（from_ref / to_ref）

```bash
curl -s -X POST "$BASE/api/v1/repos/$REPO/diff" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"from_ref": "heads/main", "to_ref": "heads/main"}' \
     | python3 -m json.tool
```

预期输出（同一引用，无差异）：
```json
{
    "old_commit": "a1b2c3d4...",
    "new_commit": "a1b2c3d4...",
    "summary": {
        "files_changed": 0,
        "rows_added": 0,
        "rows_removed": 0,
        "rows_refreshed": 0
    },
    "files": []
}
```

验证清单：
- [ ] 返回 HTTP 200，`files` 为空数组（相同引用无差异）

### 8.3 包含行详情（include_rows=true）

```bash
curl -s -X POST "$BASE/api/v1/repos/$REPO/diff" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d "{
       \"old_commit\": \"$OLD_COMMIT\",
       \"new_commit\": \"$NEW_COMMIT\",
       \"include_rows\": true,
       \"limit\": 5
     }" | python3 -m json.tool
```

预期输出（每个文件条目额外包含行级详情）：
```json
{
    "files": [
        {
            "path": "train/sft.jsonl",
            "added": 50,
            "removed": 10,
            "added_rows": [
                {
                    "row_hash": "f1e2d3c4...",
                    "position": 0,
                    "content": {"messages": [...]}
                }
            ],
            "removed_rows": [...],
            "refreshed_rows": [...],
            "total_changes": 60,
            "has_more": true
        }
    ]
}
```

验证清单：
- [ ] 返回 HTTP 200
- [ ] 每个文件条目含 `added_rows`、`removed_rows`、`refreshed_rows`
- [ ] `added_rows` 每项含 `row_hash`、`position`、`content`
- [ ] `has_more` 在有更多数据时为 `true`

### 8.4 只比较特定文件（path 过滤）

```bash
curl -s -X POST "$BASE/api/v1/repos/$REPO/diff" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d "{
       \"old_commit\": \"$OLD_COMMIT\",
       \"new_commit\": \"$NEW_COMMIT\",
       \"path\": \"train/sft.jsonl\"
     }" | python3 -m json.tool
```

验证清单：
- [ ] 返回 HTTP 200
- [ ] `files` 数组中只包含 `"train/sft.jsonl"`（若该文件有变化）

---

## 9. 权限验证

Dit 使用分级角色系统（从低到高）：`reader` < `reviewer` < `committer` < `maintainer` < `admin` < `owner`。  
旧版权限名 `read`/`push`/`admin` 分别映射到 `reader`/`committer`/`admin`。

各端点的最低权限要求：
- `GET /api/v1/repos` — read（reader）
- `POST /api/v1/repos` — admin
- `GET /api/v1/repos/{repo}/refs` — read（reader）
- `POST /api/v1/repos/{repo}/refs/...` — push（committer）
- `DELETE /api/v1/repos/{repo}/refs/...` — admin
- `GET/POST /api/v1/repos/{repo}/objects/...` — 任意有效令牌（verify_token）
- `GET /api/v1/repos/{repo}/tree/...` — read（reader）
- `GET /api/v1/repos/{repo}/log` — read（reader）
- `GET /api/v1/repos/{repo}/manifest/...` — read（reader）
- `POST /api/v1/repos/{repo}/diff` — read（reader）

### 9.1 准备只读令牌

先通过 `dit` CLI 或直接在数据库中创建一个只有 read 权限的令牌，或使用指南 00 中已有的 reader 令牌：

```bash
export READ_TOKEN="<只读令牌>"
```

### 9.2 只读令牌可以列出仓库

```bash
curl -s -o /dev/null -w "%{http_code}" \
     -H "Authorization: Bearer $READ_TOKEN" \
     "$BASE/api/v1/repos"
```

预期输出：
```
200
```

验证清单：
- [ ] 返回 HTTP 200

### 9.3 只读令牌无法创建仓库

```bash
curl -s -X POST "$BASE/api/v1/repos" \
     -H "Authorization: Bearer $READ_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"name": "should-fail"}' | python3 -m json.tool
```

预期输出（HTTP 403）：
```json
{
    "detail": "Requires admin permission"
}
```

验证清单：
- [ ] 返回 HTTP 403（Forbidden）

### 9.4 只读令牌无法创建/更新引用

```bash
curl -s -X POST "$BASE/api/v1/repos/$REPO/refs/heads/readonly-test" \
     -H "Authorization: Bearer $READ_TOKEN" \
     -H "Content-Type: application/json" \
     -d "{\"old\": null, \"new\": \"$COMMIT_HASH\"}" | python3 -m json.tool
```

预期输出（HTTP 403）：
```json
{
    "detail": "Requires push permission"
}
```

验证清单：
- [ ] 返回 HTTP 403

### 9.5 无令牌访问受保护端点

```bash
curl -s -o /dev/null -w "%{http_code}" \
     "$BASE/api/v1/repos"
```

预期输出：
```
401
```

验证清单：
- [ ] 返回 HTTP 401（Unauthorized），缺少 Authorization 头

### 9.6 无效令牌

```bash
curl -s -o /dev/null -w "%{http_code}" \
     -H "Authorization: Bearer invalid-token-xyz" \
     "$BASE/api/v1/repos"
```

预期输出：
```
401
```

验证清单：
- [ ] 返回 HTTP 401

---

## 10. 边界场景

### 10.1 访问不存在的仓库（404）

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/no-such-repo/refs" | python3 -m json.tool
```

预期输出：
```json
{
    "detail": "Repo 'no-such-repo' not found"
}
```

验证清单：
- [ ] 返回 HTTP 404，错误信息明确提示仓库名

### 10.2 获取不存在的引用（404）

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/refs/heads/nonexistent-branch" | python3 -m json.tool
```

预期输出：
```json
{
    "detail": "Ref 'heads/nonexistent-branch' not found"
}
```

验证清单：
- [ ] 返回 HTTP 404

### 10.3 下载不存在的对象（404）

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/objects/rows/0000000000000000000000000000000000000000000000000000000000000000" \
     | python3 -m json.tool
```

预期输出：
```json
{
    "detail": "Object not found"
}
```

验证清单：
- [ ] 返回 HTTP 404

### 10.4 上传时哈希不匹配（400）

```bash
# 使用错误的哈希（全零）上传内容
curl -s -X POST "$BASE/api/v1/repos/$REPO/objects/rows/0000000000000000000000000000000000000000000000000000000000000000" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/octet-stream" \
     --data-binary "hello world" | python3 -m json.tool
```

预期输出（HTTP 400）：
```json
{
    "detail": "Hash mismatch: path has 0000...0000, body hashes to b94d27b9..."
}
```

验证清单：
- [ ] 返回 HTTP 400，错误信息包含 `"Hash mismatch"`

### 10.5 浏览不存在的树路径（404）

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/tree/$COMMIT_HASH/nonexistent/path" | python3 -m json.tool
```

预期输出：
```json
{
    "detail": "Path 'nonexistent/path' not found in tree"
}
```

验证清单：
- [ ] 返回 HTTP 404，错误信息包含 `"not found in tree"`

### 10.6 日志：不存在的引用（404）

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/log?ref=heads/no-such-branch" | python3 -m json.tool
```

预期输出：
```json
{
    "detail": "Ref 'heads/no-such-branch' not found"
}
```

验证清单：
- [ ] 返回 HTTP 404

### 10.7 Manifest：不存在的提交哈希（404）

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/manifest/zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz/train/sft.jsonl" \
     | python3 -m json.tool
```

预期输出：
```json
{
    "detail": "Commit not found"
}
```

验证清单：
- [ ] 返回 HTTP 404

### 10.8 Diff：缺少必要参数（400）

```bash
curl -s -X POST "$BASE/api/v1/repos/$REPO/diff" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{}' | python3 -m json.tool
```

预期输出（HTTP 400）：
```json
{
    "detail": "Must provide old_commit/new_commit or from_ref/to_ref"
}
```

验证清单：
- [ ] 返回 HTTP 400，提示需要提供 commit 或 ref 参数

### 10.9 并发 CAS 冲突（409）

模拟两个并发写入者同时尝试更新同一引用，只有一个应该成功。该场景需要两个真实存在、且不同于 `$COMMIT_HASH` 的目标 commit hash；如果当前仓库只有一个提交，可跳过本小节，或先通过 CLI/对象 API 创建两个真实提交后再执行。

```bash
export NEW_HASH_1="<真实存在的 commit hash 1>"
export NEW_HASH_2="<真实存在的 commit hash 2>"

# 先创建一个测试引用
curl -s -X POST "$BASE/api/v1/repos/$REPO/refs/heads/cas-concurrent-test" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d "{\"old\": null, \"new\": \"$COMMIT_HASH\"}" > /dev/null

# 并发发起两个 CAS 更新（使用 & 放入后台）
curl -s -X POST "$BASE/api/v1/repos/$REPO/refs/heads/cas-concurrent-test" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d "{\"old\": \"$COMMIT_HASH\", \"new\": \"$NEW_HASH_1\"}" \
     -o /tmp/cas_result_1.json &

curl -s -X POST "$BASE/api/v1/repos/$REPO/refs/heads/cas-concurrent-test" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d "{\"old\": \"$COMMIT_HASH\", \"new\": \"$NEW_HASH_2\"}" \
     -o /tmp/cas_result_2.json &

wait

echo "结果 1："; cat /tmp/cas_result_1.json | python3 -m json.tool
echo "结果 2："; cat /tmp/cas_result_2.json | python3 -m json.tool
```

预期结果：两个请求中，一个返回 HTTP 200（成功），另一个返回包含 `"CAS conflict"` 的响应体（对应 HTTP 409）。

验证清单：
- [ ] 两次并发请求一成一败
- [ ] 失败的一方响应体含 `"CAS conflict"`
- [ ] 最终引用指向某一个新哈希（不再是原始的 `$COMMIT_HASH`）

```bash
# 确认最终状态
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/refs/heads/cas-concurrent-test" | python3 -m json.tool
```

验证清单：
- [ ] 最终 `target_hash` 为两次写入中成功的那个值

---

## 小结

| 端点 | 方法 | 最低权限 | 关键验证点 |
|------|------|----------|-----------|
| `/api/v1/repos` | GET | reader | 返回仓库列表（按名排序） |
| `/api/v1/repos` | POST | admin | 201 创建成功，409 重名冲突 |
| `/api/v1/repos/{repo}/refs` | GET | reader | 引用列表 |
| `/api/v1/repos/{repo}/refs/{type}/{name}` | GET | reader | 单个引用，404 不存在 |
| `/api/v1/repos/{repo}/refs/{type}/{name}` | POST | committer | CAS 更新，409 冲突 |
| `/api/v1/repos/{repo}/refs/{type}/{name}` | DELETE | admin | 200 删除，404 不存在 |
| `/api/v1/repos/{repo}/objects/batch-exists` | POST | 任意令牌 | 批量存在性检查 |
| `/api/v1/repos/{repo}/objects/{type}/{hash}` | GET | 任意令牌 | 对象下载，404 不存在 |
| `/api/v1/repos/{repo}/objects/{type}/{hash}` | POST | 任意令牌 | 上传（哈希验证），幂等 |
| `/api/v1/repos/{repo}/tree/{commit}/{path}` | GET | reader | 目录树，404 路径/提交不存在 |
| `/api/v1/repos/{repo}/log` | GET | reader | 分页提交日志（ref 必填） |
| `/api/v1/repos/{repo}/manifest/{commit}/{path}` | GET | reader | 分页行列表，limit 上限 500 |
| `/api/v1/repos/{repo}/diff` | POST | reader | 提交/引用间 diff，可含行详情 |
