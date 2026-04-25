# DataHub 手动测试指南 06：PR 与代码审查

本指南覆盖 DataHub 拉取请求（Pull Request）的完整工作流：创建 PR、评论、审查、合并（含冲突解决）、分支保护规则、审查者规则，以及常见边界场景。所有操作均通过 `curl` 直接调用 REST API。

**前置条件**：
- 已完成 **指南 00**（服务端在 `localhost:8000` 正常运行，Admin 令牌已备好）
- 已完成 **指南 03** 或 **指南 05**（至少有一个仓库，已有数据推送，存在 `heads/main` 引用）
- 服务端仓库中需要有 **两个分支**，本指南会在第 1 节准备

---

## 目录

1. [前置条件：准备双分支仓库](#1-前置条件准备双分支仓库)
2. [创建 PR](#2-创建-pr)
3. [列出和查看 PR](#3-列出和查看-pr)
4. [PR 评论](#4-pr-评论)
5. [PR 审查（Reviews）](#5-pr-审查reviews)
6. [合并 PR（无冲突）](#6-合并-pr无冲突)
7. [合并 PR（有冲突）](#7-合并-pr有冲突)
8. [分支保护](#8-分支保护)
9. [审查者规则](#9-审查者规则)
10. [PR 状态管理](#10-pr-状态管理)
11. [边界场景](#11-边界场景)

---

## 1. 前置条件：准备双分支仓库

### 1.1 设置环境变量

```bash
# Admin 令牌（执行所有操作）
export TOKEN="<你的 admin token>"

# Reviewer 令牌（提交 review 需要 reviewer 权限）
# 若没有单独的 reviewer 令牌，admin 令牌同样满足要求
export REVIEWER_TOKEN="<你的 reviewer token，或与 TOKEN 相同>"

# 服务端地址
export BASE="http://localhost:8000"

# 用于本指南测试的仓库名（将在下方创建）
export REPO="pr-test-repo"
```

### 1.2 创建测试仓库

```bash
curl -s -X POST "$BASE/api/v1/repos" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d "{\"name\": \"$REPO\"}" | python3 -m json.tool
```

预期输出（HTTP 201）：
```json
{
    "id": 2,
    "name": "pr-test-repo"
}
```

验证清单：
- [ ] 返回 HTTP 201，`name` 与 `$REPO` 一致

### 1.3 确认 main 分支已存在（或推送初始提交）

如果该仓库是全新创建的，需要先推送 `main` 和 `feature` 两个分支。若复用指南 03/05 的已有仓库并已有 `heads/main`，可将 `$REPO` 设为已有仓库名，然后从 1.4 开始操作。

**方式 A：使用指南 03/05 的已有仓库**

```bash
# 将 REPO 设置为已有仓库名
export REPO="my-dataset"

# 查看现有分支
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/refs" | python3 -m json.tool
```

**方式 B：通过 dit CLI 向新仓库推送数据**

```bash
# 在本地仓库目录下执行（参考指南 03）
dit remote add origin "$BASE" --token "$TOKEN"
dit push origin main
```

验证清单：
- [ ] `GET /api/v1/repos/$REPO/refs` 返回包含 `heads/main` 的列表

### 1.4 获取 main 分支的当前提交哈希

```bash
export MAIN_HASH=$(curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/refs/heads/main" \
     | python3 -c "import sys,json; print(json.load(sys.stdin)['target_hash'])")
echo "main 当前提交：$MAIN_HASH"
```

验证清单：
- [ ] `$MAIN_HASH` 为 64 位十六进制字符串

### 1.5 创建 feature 分支（指向同一提交）

若仓库中已有 `heads/feature` 分支，跳过此步。

```bash
curl -s -X POST "$BASE/api/v1/repos/$REPO/refs/heads/feature" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d "{\"old\": null, \"new\": \"$MAIN_HASH\"}" | python3 -m json.tool
```

预期输出（HTTP 200）：
```json
{
    "name": "heads/feature",
    "target_hash": "a1b2c3d4e5f6..."
}
```

验证清单：
- [ ] 返回 HTTP 200，`target_hash` 与 `$MAIN_HASH` 一致

> **说明**：feature 分支当前和 main 指向同一提交（fast-forward 情形）。在第 6 节测试三路合并时，需要 feature 分支存在发散提交。本节只需确保两个分支都存在即可，具体的发散场景见第 6、7 节。

### 1.6 保存分支哈希到变量

```bash
export FEATURE_HASH=$(curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/refs/heads/feature" \
     | python3 -c "import sys,json; print(json.load(sys.stdin)['target_hash'])")
echo "feature 当前提交：$FEATURE_HASH"
```

验证清单：
- [ ] `$FEATURE_HASH` 为 64 位十六进制字符串
- [ ] 两个分支均存在于服务端

---

## 2. 创建 PR

### 2.1 创建基础 PR

将 `feature` 分支的变更合入 `main`。

```bash
curl -s -X POST "$BASE/api/v1/repos/$REPO/pulls" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "title": "新增训练数据：扩充对话场景",
       "source_branch": "feature",
       "target_branch": "main",
       "author": "zhangsan",
       "description": "本 PR 在 feature 分支上新增了 50 条对话数据，用于扩充 SFT 训练集。"
     }' | python3 -m json.tool
```

预期输出（HTTP 201）：
```json
{
    "id": 1,
    "pull_request_id": 1,
    "repo_id": 2,
    "title": "新增训练数据：扩充对话场景",
    "author": "zhangsan",
    "status": "open",
    "source_ref": "heads/feature",
    "target_ref": "heads/main",
    "base_commit": "a1b2c3d4...",
    "source_commit": "a1b2c3d4...",
    "target_commit": "a1b2c3d4...",
    "merge_commit": null,
    "is_mergeable": true,
    "conflict_files": [],
    "stats_added": 0,
    "stats_removed": 0,
    "stats_refreshed": 0,
    "created_at": "2026-04-25T10:00:00+00:00",
    "updated_at": "2026-04-25T10:00:00+00:00"
}
```

```bash
# 保存 PR ID 供后续步骤使用
export PR_ID=1
```

验证清单：
- [ ] 返回 HTTP 201
- [ ] `status` 为 `"open"`
- [ ] `source_ref` 为 `"heads/feature"`，`target_ref` 为 `"heads/main"`
- [ ] 包含 `stats_added`、`stats_removed`、`stats_refreshed`（diff 统计）
- [ ] 包含 `is_mergeable`（可合并性检查）
- [ ] `pull_request_id` 为本仓库内自增 ID（从 1 开始）

> **说明**：创建 PR 时服务端会自动计算 diff 统计和可合并性。两个分支指向同一提交时，`stats_*` 均为 0，`is_mergeable` 为 `true`。

### 2.2 创建带 description 的 PR

`description` 字段为可选项：

```bash
curl -s -X POST "$BASE/api/v1/repos/$REPO/pulls" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "title": "修复标注错误行",
       "source_branch": "feature",
       "target_branch": "main",
       "author": "lisi",
       "description": "修复了 data.jsonl 中 row_hash=abc123 行的标注错误"
     }' | python3 -m json.tool
```

验证清单：
- [ ] 返回 HTTP 201
- [ ] `pull_request_id` 为 2（第二个 PR）

---

## 3. 列出和查看 PR

### 3.1 列出仓库所有 PR

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/pulls" | python3 -m json.tool
```

预期输出：
```json
[
    {
        "pull_request_id": 1,
        "title": "新增训练数据：扩充对话场景",
        "status": "open",
        "author": "zhangsan",
        ...
    },
    {
        "pull_request_id": 2,
        "title": "修复标注错误行",
        "status": "open",
        "author": "lisi",
        ...
    }
]
```

验证清单：
- [ ] 返回 HTTP 200，为数组
- [ ] 按 `pull_request_id` 升序排列
- [ ] 包含刚创建的两个 PR

### 3.2 按状态过滤 PR（只看 open）

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/pulls?status=open" | python3 -m json.tool
```

验证清单：
- [ ] 返回 HTTP 200
- [ ] 所有返回的 PR `status` 均为 `"open"`

### 3.3 按状态过滤 PR（只看 closed，当前应为空）

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/pulls?status=closed" | python3 -m json.tool
```

预期输出：
```json
[]
```

验证清单：
- [ ] 返回 HTTP 200，空数组（目前没有已关闭的 PR）

### 3.4 获取单个 PR 详情

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/pulls/$PR_ID" | python3 -m json.tool
```

预期输出（HTTP 200，包含完整字段）：
```json
{
    "id": 1,
    "pull_request_id": 1,
    "repo_id": 2,
    "title": "新增训练数据：扩充对话场景",
    "author": "zhangsan",
    "status": "open",
    "source_ref": "heads/feature",
    "target_ref": "heads/main",
    "base_commit": "a1b2c3d4...",
    "source_commit": "a1b2c3d4...",
    "target_commit": "a1b2c3d4...",
    "merge_commit": null,
    "is_mergeable": true,
    "conflict_files": [],
    "stats_added": 0,
    "stats_removed": 0,
    "stats_refreshed": 0,
    "created_at": "2026-04-25T10:00:00+00:00",
    "updated_at": "2026-04-25T10:00:00+00:00"
}
```

验证清单：
- [ ] 返回 HTTP 200
- [ ] `pull_request_id` 与请求路径中的 ID 一致
- [ ] `merge_commit` 为 `null`（尚未合并）

### 3.5 获取不存在的 PR（404）

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/pulls/9999" | python3 -m json.tool
```

预期输出：
```json
{
    "detail": "Pull request #9999 not found"
}
```

验证清单：
- [ ] 返回 HTTP 404

---

## 4. PR 评论

PR 评论支持四种粒度：通用评论、文件级、行级（row_hash）、字段级（field_path）。

### 4.1 创建通用评论

```bash
curl -s -X POST "$BASE/api/v1/repos/$REPO/pulls/$PR_ID/comments" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "author": "reviewer1",
       "body": "整体数据质量不错，有几行需要仔细检查。"
     }' | python3 -m json.tool
```

预期输出（HTTP 201）：
```json
{
    "id": 1,
    "pull_request_meta_id": 1,
    "author": "reviewer1",
    "body": "整体数据质量不错，有几行需要仔细检查。",
    "file_path": null,
    "row_hash": null,
    "field_path": null,
    "change_type": null,
    "created_at": "2026-04-25T10:01:00+00:00",
    "updated_at": "2026-04-25T10:01:00+00:00"
}
```

```bash
# 保存评论 ID 供后续更新/删除使用
export COMMENT_ID=1
```

验证清单：
- [ ] 返回 HTTP 201
- [ ] `file_path`、`row_hash`、`field_path`、`change_type` 均为 `null`（通用评论）
- [ ] `author` 和 `body` 与请求一致

### 4.2 创建文件级评论

针对特定文件路径的评论：

```bash
curl -s -X POST "$BASE/api/v1/repos/$REPO/pulls/$PR_ID/comments" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "author": "reviewer1",
       "body": "这个文件中有部分行的 system 提示词不符合规范，请统一格式。",
       "file_path": "train/sft.jsonl"
     }' | python3 -m json.tool
```

预期输出（HTTP 201）：
```json
{
    "id": 2,
    "author": "reviewer1",
    "body": "这个文件中有部分行的 system 提示词不符合规范，请统一格式。",
    "file_path": "train/sft.jsonl",
    "row_hash": null,
    "field_path": null,
    "change_type": null,
    ...
}
```

验证清单：
- [ ] 返回 HTTP 201
- [ ] `file_path` 为 `"train/sft.jsonl"`，其余定位字段为 `null`

### 4.3 创建行级评论（指定 row_hash）

针对某一具体数据行的评论：

```bash
curl -s -X POST "$BASE/api/v1/repos/$REPO/pulls/$PR_ID/comments" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "author": "reviewer2",
       "body": "这行数据中 assistant 的回复存在事实性错误，需要人工修正。",
       "file_path": "train/sft.jsonl",
       "row_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
       "change_type": "added"
     }' | python3 -m json.tool
```

预期输出（HTTP 201）：
```json
{
    "id": 3,
    "author": "reviewer2",
    "body": "这行数据中 assistant 的回复存在事实性错误，需要人工修正。",
    "file_path": "train/sft.jsonl",
    "row_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "field_path": null,
    "change_type": "added",
    ...
}
```

验证清单：
- [ ] 返回 HTTP 201
- [ ] `row_hash` 为 64 位哈希
- [ ] `change_type` 为 `"added"`

### 4.4 创建字段级评论（指定 field_path）

针对某一行的特定字段的评论：

```bash
curl -s -X POST "$BASE/api/v1/repos/$REPO/pulls/$PR_ID/comments" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "author": "reviewer2",
       "body": "messages[1].content 字段语义不清晰，建议改写。",
       "file_path": "train/sft.jsonl",
       "row_hash": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
       "field_path": "messages[1].content",
       "change_type": "refreshed"
     }' | python3 -m json.tool
```

预期输出（HTTP 201）：
```json
{
    "id": 4,
    "author": "reviewer2",
    "body": "messages[1].content 字段语义不清晰，建议改写。",
    "file_path": "train/sft.jsonl",
    "row_hash": "bbbb...bbbb",
    "field_path": "messages[1].content",
    "change_type": "refreshed",
    ...
}
```

验证清单：
- [ ] 返回 HTTP 201
- [ ] `field_path` 为 `"messages[1].content"`

### 4.5 列出所有评论

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/pulls/$PR_ID/comments" | python3 -m json.tool
```

预期输出（4 条评论，按创建时间排序）：
```json
[
    {"id": 1, "body": "整体数据质量不错...", "file_path": null, ...},
    {"id": 2, "body": "这个文件中有部分行...", "file_path": "train/sft.jsonl", ...},
    {"id": 3, "body": "这行数据中 assistant...", "row_hash": "aaaa...", ...},
    {"id": 4, "body": "messages[1].content...", "field_path": "messages[1].content", ...}
]
```

验证清单：
- [ ] 返回 HTTP 200，为数组
- [ ] 按 `created_at` 升序排列
- [ ] 共 4 条评论

### 4.6 按文件路径过滤评论

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/pulls/$PR_ID/comments?file_path=train/sft.jsonl" \
     | python3 -m json.tool
```

预期输出（只返回 file_path 为 train/sft.jsonl 的评论，共 3 条）：
```json
[
    {"id": 2, "file_path": "train/sft.jsonl", ...},
    {"id": 3, "file_path": "train/sft.jsonl", ...},
    {"id": 4, "file_path": "train/sft.jsonl", ...}
]
```

验证清单：
- [ ] 返回 HTTP 200
- [ ] 所有结果的 `file_path` 均为 `"train/sft.jsonl"`
- [ ] 通用评论（`file_path: null`）不出现在结果中

### 4.7 更新评论内容

```bash
curl -s -X PATCH \
     "$BASE/api/v1/repos/$REPO/pulls/$PR_ID/comments/$COMMENT_ID" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"body": "整体数据质量不错，建议再次人工抽检 10% 的样本。"}' \
     | python3 -m json.tool
```

预期输出（HTTP 200）：
```json
{
    "id": 1,
    "body": "整体数据质量不错，建议再次人工抽检 10% 的样本。",
    "author": "reviewer1",
    ...
}
```

验证清单：
- [ ] 返回 HTTP 200
- [ ] `body` 已更新为新内容

### 4.8 删除评论

```bash
# 先创建一条待删除的评论
export DEL_COMMENT_ID=$(curl -s -X POST \
     "$BASE/api/v1/repos/$REPO/pulls/$PR_ID/comments" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"author": "tmp", "body": "临时评论，待删除"}' \
     | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "待删除的评论 ID：$DEL_COMMENT_ID"

# 删除该评论
curl -s -X DELETE \
     "$BASE/api/v1/repos/$REPO/pulls/$PR_ID/comments/$DEL_COMMENT_ID" \
     -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

预期输出（HTTP 200）：
```json
{
    "status": "deleted",
    "id": 5
}
```

验证清单：
- [ ] 返回 HTTP 200，`status` 为 `"deleted"`
- [ ] 再次 GET 评论列表，确认该评论已不存在

```bash
# 确认删除成功
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/pulls/$PR_ID/comments" \
     | python3 -c "import sys,json; ids=[c['id'] for c in json.load(sys.stdin)]; print('评论 ID 列表：', ids)"
```

验证清单：
- [ ] 被删除的 ID 不在列表中

---

## 5. PR 审查（Reviews）

审查（Review）有两种状态：`approved`（批准）和 `changes_requested`（要求修改）。提交审查需要 `reviewer` 权限。

### 5.1 提交 approved 审查

```bash
curl -s -X POST "$BASE/api/v1/repos/$REPO/pulls/$PR_ID/reviews" \
     -H "Authorization: Bearer $REVIEWER_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"status": "approved"}' | python3 -m json.tool
```

预期输出（HTTP 201）：
```json
{
    "id": 1,
    "pull_request_id": 1,
    "token_id": 1,
    "status": "approved",
    "created_at": "2026-04-25T10:05:00+00:00"
}
```

验证清单：
- [ ] 返回 HTTP 201
- [ ] `status` 为 `"approved"`
- [ ] `pull_request_id` 与 `$PR_ID` 一致
- [ ] `token_id` 为提交审查的令牌 ID

### 5.2 列出 PR 的所有审查

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/pulls/$PR_ID/reviews" | python3 -m json.tool
```

预期输出：
```json
[
    {
        "id": 1,
        "pull_request_id": 1,
        "token_id": 1,
        "status": "approved",
        "created_at": "2026-04-25T10:05:00+00:00"
    }
]
```

验证清单：
- [ ] 返回 HTTP 200，为数组
- [ ] 包含刚提交的 `approved` 审查

### 5.3 更新审查（Upsert）—— 同一令牌再次提交会覆盖

如果同一令牌已对该 PR 提交过审查，再次提交会**覆盖**原有状态（upsert），而非新增一条：

```bash
curl -s -X POST "$BASE/api/v1/repos/$REPO/pulls/$PR_ID/reviews" \
     -H "Authorization: Bearer $REVIEWER_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"status": "changes_requested"}' | python3 -m json.tool
```

预期输出（HTTP 201，相同的 `id`，状态已变更）：
```json
{
    "id": 1,
    "pull_request_id": 1,
    "token_id": 1,
    "status": "changes_requested",
    "created_at": "2026-04-25T10:05:00+00:00"
}
```

验证清单：
- [ ] 返回 HTTP 201
- [ ] `id` 与第一次相同（更新，不是新增）
- [ ] `status` 已改为 `"changes_requested"`

再次列出审查确认只有一条：

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/pulls/$PR_ID/reviews" \
     | python3 -c "import sys,json; r=json.load(sys.stdin); print('审查条数：', len(r), '状态：', r[0]['status'] if r else 'N/A')"
```

验证清单：
- [ ] 审查列表仍只有 1 条（覆盖而非追加）
- [ ] 状态为 `"changes_requested"`

### 5.4 改回 approved

审查者审核修改后，恢复 approved：

```bash
curl -s -X POST "$BASE/api/v1/repos/$REPO/pulls/$PR_ID/reviews" \
     -H "Authorization: Bearer $REVIEWER_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"status": "approved"}' | python3 -m json.tool
```

验证清单：
- [ ] 返回 HTTP 201，`status` 重新变为 `"approved"`

### 5.5 无效 status 值（422）

```bash
curl -s -X POST "$BASE/api/v1/repos/$REPO/pulls/$PR_ID/reviews" \
     -H "Authorization: Bearer $REVIEWER_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"status": "rejected"}' | python3 -m json.tool
```

预期输出（HTTP 422）：
```json
{
    "detail": [
        {
            "type": "literal_error",
            "loc": ["body", "status"],
            "msg": "Input should be 'approved' or 'changes_requested'"
        }
    ]
}
```

验证清单：
- [ ] 返回 HTTP 422（Unprocessable Entity）
- [ ] 错误提示 `status` 只能为 `"approved"` 或 `"changes_requested"`

---

## 6. 合并 PR（无冲突）

### 6.1 快进合并（Fast-forward）

当 `target` 分支是 `source` 分支的直接祖先时（即 main 没有在 feature 分叉后有新提交），执行快进合并。

当前两个分支指向同一提交，恰好满足快进条件（base commit == target commit）：

```bash
curl -s -X POST "$BASE/api/v1/repos/$REPO/pulls/$PR_ID/merge" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "message": "Merge feature: 新增训练数据",
       "author": "merger"
     }' | python3 -m json.tool
```

预期输出（HTTP 200）：
```json
{
    "id": 1,
    "pull_request_id": 1,
    "status": "merged",
    "merge_commit": "a1b2c3d4...",
    "fast_forward": true,
    "target_commit": "a1b2c3d4...",
    ...
}
```

验证清单：
- [ ] 返回 HTTP 200
- [ ] `status` 为 `"merged"`
- [ ] `fast_forward` 为 `true`
- [ ] `merge_commit` 非 `null`（快进时等于 source_commit）

### 6.2 确认 main 分支引用已更新

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/refs/heads/main" | python3 -m json.tool
```

验证清单：
- [ ] `target_hash` 已更新为合并后的提交哈希
- [ ] 与 `merge_commit` 字段值一致

### 6.3 三路合并（Three-way merge）

三路合并需要 main 和 feature 在公共祖先之后各自有不同提交。创建一个新的发散场景：

```bash
# 1. 创建新的测试仓库（保持干净状态）
export REPO3W="pr-merge-3way"
curl -s -X POST "$BASE/api/v1/repos" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d "{\"name\": \"$REPO3W\"}" > /dev/null

# 2. 通过 dit CLI 推送两个发散分支（在本地仓库执行）
#    或者使用指南 04 中的 merge 测试场景，此处假设已有发散分支

# 3. 假设 $REPO3W 中已有发散的 main 和 feature 分支，创建 PR
export PR3W_ID=$(curl -s -X POST "$BASE/api/v1/repos/$REPO3W/pulls" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "title": "三路合并测试",
       "source_branch": "feature",
       "target_branch": "main",
       "author": "tester"
     }' | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('pull_request_id', 'ERROR:' + str(d)))")
echo "PR ID：$PR3W_ID"
```

> **注意**：如果仓库中只有一个提交或 main/feature 指向同一提交，此步会返回 `is_mergeable: true` 且 `stats_added/removed` 为 0。要触发真正的三路合并，需要两个分支在公共祖先之后各自有提交。可使用 `dit` CLI 在本地做两次不同的提交并分别推送到 main 和 feature。

```bash
# 合并（三路合并场景）
curl -s -X POST "$BASE/api/v1/repos/$REPO3W/pulls/$PR3W_ID/merge" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "message": "Merge feature: 三路合并测试",
       "author": "merger"
     }' | python3 -m json.tool
```

预期输出（无冲突时，HTTP 200）：
```json
{
    "pull_request_id": 1,
    "status": "merged",
    "merge_commit": "e5f6a7b8c9d0...",
    "fast_forward": false,
    ...
}
```

验证清单：
- [ ] 返回 HTTP 200
- [ ] `fast_forward` 为 `false`（三路合并创建新的 merge commit）
- [ ] `merge_commit` 为新创建的合并提交哈希（与 source/target 不同）
- [ ] `status` 为 `"merged"`

---

## 7. 合并 PR（有冲突）

### 7.1 准备冲突场景

冲突发生在：main 和 feature 分别**修改了同一文件的同一行**（删除同一行数据但添加了不同的替代行）。此情形需要通过 `dit` CLI 在本地构造并推送。

> **前提**：使用指南 04 中构造的冲突仓库，或按照以下说明构造：
> - 公共祖先提交包含行 `row_hash=A`
> - main 分支：删除行 A，添加行 B
> - feature 分支：删除行 A，添加行 C

以下假设仓库 `conflict-repo` 已按此方式准备好：

```bash
export REPO_CF="conflict-repo"
export PR_CF_ID=$(curl -s -X POST "$BASE/api/v1/repos/$REPO_CF/pulls" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "title": "冲突合并测试",
       "source_branch": "feature",
       "target_branch": "main",
       "author": "tester"
     }' | python3 -c "import sys,json; print(json.load(sys.stdin)['pull_request_id'])")
echo "冲突 PR ID：$PR_CF_ID"
```

### 7.2 合并时得到冲突响应（409）

```bash
curl -s -X POST "$BASE/api/v1/repos/$REPO_CF/pulls/$PR_CF_ID/merge" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "message": "尝试合并冲突分支",
       "author": "merger"
     }' | python3 -m json.tool
```

预期输出（HTTP 409）：
```json
{
    "detail": {
        "message": "Merge conflicts — cannot auto-merge",
        "conflicts": [
            {
                "file_path": "train/sft.jsonl",
                "conflict_type": "modify_modify"
            }
        ]
    }
}
```

验证清单：
- [ ] 返回 HTTP 409（Conflict）
- [ ] `detail.message` 包含 `"Merge conflicts"`
- [ ] `detail.conflicts` 数组列出冲突文件和冲突类型
- [ ] PR 状态仍为 `"open"`（未合并）

```bash
# 确认 PR 仍为 open
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO_CF/pulls/$PR_CF_ID" \
     | python3 -c "import sys,json; print('PR 状态：', json.load(sys.stdin)['status'])"
```

验证清单：
- [ ] PR 状态仍为 `"open"`

### 7.3 使用 merge-preview 端点预检冲突

在提交 merge 之前，可用 `merge-preview` 端点预先检查是否存在冲突：

```bash
curl -s -X POST "$BASE/api/v1/repos/$REPO_CF/merge-preview" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "source_branch": "feature",
       "target_branch": "main"
     }' | python3 -m json.tool
```

预期输出：
```json
{
    "mergeable": false,
    "merge_base": "base_commit_hash...",
    "conflicts": [
        {
            "file_path": "train/sft.jsonl",
            "conflict_type": "modify_modify"
        }
    ]
}
```

验证清单：
- [ ] 返回 HTTP 200
- [ ] `mergeable` 为 `false`
- [ ] `conflicts` 列出冲突文件

### 7.4 解决冲突（POST /pulls/{id}/resolve）

解决冲突时，为每个冲突文件提供一个 `row_hash`，指定选择 `ours`（main 分支的行）还是 `theirs`（feature 分支的行）。`choice` 字段用于标记语义，实际生效的是传入的 `row_hash`。

```bash
# 查看冲突 PR 中的 conflict_files，获取冲突的文件路径
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO_CF/pulls/$PR_CF_ID" \
     | python3 -c "import sys,json; d=json.load(sys.stdin); print('conflict_files:', d['conflict_files'])"
```

```bash
# 解决冲突：选择 "theirs"（feature 分支的行 hash）
curl -s -X POST "$BASE/api/v1/repos/$REPO_CF/pulls/$PR_CF_ID/resolve" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "resolutions": [
         {
           "file_path": "train/sft.jsonl",
           "row_hash": "<feature 分支在冲突文件中的 row_hash>",
           "choice": "theirs"
         }
       ],
       "message": "解决冲突：采用 feature 分支的版本",
       "author": "resolver"
     }' | python3 -m json.tool
```

预期输出（HTTP 200，PR 状态变为 merged）：
```json
{
    "pull_request_id": 1,
    "status": "merged",
    "merge_commit": "f0a1b2c3d4e5...",
    "is_mergeable": null,
    ...
}
```

验证清单：
- [ ] 返回 HTTP 200
- [ ] `status` 为 `"merged"`
- [ ] `merge_commit` 非 `null`

### 7.5 对无冲突 PR 调用 /resolve（400）

```bash
# PR_ID 指向已经是可合并状态（无冲突）的 PR（需要是 open 状态）
# 先创建一个无冲突的新 PR 用于测试
export NO_CONF_PR=$(curl -s -X POST "$BASE/api/v1/repos/$REPO/pulls" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "title": "无冲突 PR",
       "source_branch": "feature",
       "target_branch": "main",
       "author": "tester"
     }' | python3 -c "import sys,json; print(json.load(sys.stdin).get('pull_request_id', 'ERROR'))")
echo "无冲突 PR ID：$NO_CONF_PR"

curl -s -X POST "$BASE/api/v1/repos/$REPO/pulls/$NO_CONF_PR/resolve" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "resolutions": [],
       "message": "no conflicts",
       "author": "tester"
     }' | python3 -m json.tool
```

预期输出（HTTP 400）：
```json
{
    "detail": "No conflicts to resolve — use /merge instead"
}
```

验证清单：
- [ ] 返回 HTTP 400，提示应使用 `/merge` 而非 `/resolve`

---

## 8. 分支保护

分支保护规则限制对特定分支的合并行为，可以要求 PR 审批、禁止强制推送等。**创建/修改/删除规则需要 admin 权限**。

### 8.1 创建分支保护规则

为 `main` 分支创建保护规则，要求至少 1 个 approved 审查：

```bash
curl -s -X POST "$BASE/api/v1/repos/$REPO/branch-protection" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "branch_pattern": "main",
       "require_pr": true,
       "required_approvals": 1,
       "block_force_push": true,
       "auto_delete_branch": false
     }' | python3 -m json.tool
```

预期输出（HTTP 201）：
```json
{
    "id": 1,
    "repo_id": 2,
    "branch_pattern": "main",
    "require_pr": true,
    "required_approvals": 1,
    "block_force_push": true,
    "auto_delete_branch": false
}
```

```bash
export BP_RULE_ID=1
```

验证清单：
- [ ] 返回 HTTP 201
- [ ] `branch_pattern` 为 `"main"`
- [ ] `required_approvals` 为 1

### 8.2 列出分支保护规则

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/branch-protection" | python3 -m json.tool
```

预期输出：
```json
[
    {
        "id": 1,
        "repo_id": 2,
        "branch_pattern": "main",
        "require_pr": true,
        "required_approvals": 1,
        "block_force_push": true,
        "auto_delete_branch": false
    }
]
```

验证清单：
- [ ] 返回 HTTP 200，包含刚创建的规则

### 8.3 验证：有保护规则但无审批时合并被阻止（403）

> **前提**：使用 `/api/v1/repos/{repo}/merge` 端点（直接合并，非 PR 路径）并传入 `pull_request_id` 参数。分支保护检查在 `merge.py` 路由中执行，需要同时提供 `pull_request_id`。

先创建一个新 PR 并**不提交任何 approved 审查**：

```bash
export PR_PROT=$(curl -s -X POST "$BASE/api/v1/repos/$REPO/pulls" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "title": "无审批 PR（测试分支保护）",
       "source_branch": "feature",
       "target_branch": "main",
       "author": "tester"
     }' | python3 -c "import sys,json; print(json.load(sys.stdin).get('pull_request_id', 'ERROR'))")
echo "保护测试 PR ID：$PR_PROT"
```

尝试通过 `/merge` 端点合并（不提供 `pull_request_id`）：

```bash
curl -s -X POST "$BASE/api/v1/repos/$REPO/merge" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "source_branch": "feature",
       "target_branch": "main",
       "message": "尝试合并",
       "author": "tester"
     }' | python3 -m json.tool
```

预期输出（HTTP 403）：
```json
{
    "detail": "Branch 'main' requires 1 approval(s). Provide pull_request_id."
}
```

验证清单：
- [ ] 返回 HTTP 403（Forbidden）
- [ ] 错误信息说明需要提供 `pull_request_id`

提供 `pull_request_id` 但没有 approved 审查时：

```bash
curl -s -X POST "$BASE/api/v1/repos/$REPO/merge" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d "{
       \"source_branch\": \"feature\",
       \"target_branch\": \"main\",
       \"message\": \"尝试合并\",
       \"author\": \"tester\",
       \"pull_request_id\": $PR_PROT
     }" | python3 -m json.tool
```

预期输出（HTTP 403）：
```json
{
    "detail": "Branch 'main' requires 1 approval(s), but only 0 found for PR 3."
}
```

验证清单：
- [ ] 返回 HTTP 403
- [ ] 错误信息包含 `"requires 1 approval(s), but only 0 found"`

### 8.4 提交 approved 审查后合并成功

```bash
# 提交 approved 审查
curl -s -X POST "$BASE/api/v1/repos/$REPO/pulls/$PR_PROT/reviews" \
     -H "Authorization: Bearer $REVIEWER_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"status": "approved"}' > /dev/null

# 再次尝试合并（通过 /merge 端点，携带 pull_request_id）
curl -s -X POST "$BASE/api/v1/repos/$REPO/merge" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d "{
       \"source_branch\": \"feature\",
       \"target_branch\": \"main\",
       \"message\": \"审批后合并\",
       \"author\": \"merger\",
       \"pull_request_id\": $PR_PROT
     }" | python3 -m json.tool
```

预期输出（HTTP 200）：
```json
{
    "commit_hash": "a1b2c3d4...",
    "fast_forward": true
}
```

验证清单：
- [ ] 返回 HTTP 200，合并成功
- [ ] 有审批后分支保护不再阻止合并

### 8.5 使用 Glob 模式（如 `release/*`）

```bash
curl -s -X POST "$BASE/api/v1/repos/$REPO/branch-protection" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "branch_pattern": "release/*",
       "require_pr": true,
       "required_approvals": 2,
       "block_force_push": true,
       "auto_delete_branch": true
     }' | python3 -m json.tool
```

预期输出（HTTP 201）：
```json
{
    "id": 2,
    "branch_pattern": "release/*",
    "required_approvals": 2,
    ...
}
```

验证清单：
- [ ] 返回 HTTP 201，`branch_pattern` 为 `"release/*"`
- [ ] `required_approvals` 为 2，`auto_delete_branch` 为 `true`

### 8.6 更新分支保护规则

```bash
curl -s -X PUT "$BASE/api/v1/repos/$REPO/branch-protection/$BP_RULE_ID" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "required_approvals": 2,
       "auto_delete_branch": true
     }' | python3 -m json.tool
```

预期输出（HTTP 200）：
```json
{
    "id": 1,
    "branch_pattern": "main",
    "required_approvals": 2,
    "auto_delete_branch": true,
    "require_pr": true,
    "block_force_push": true
}
```

验证清单：
- [ ] 返回 HTTP 200
- [ ] `required_approvals` 已更新为 2
- [ ] `auto_delete_branch` 已更新为 `true`
- [ ] 未传入的字段（`require_pr`、`block_force_push`）保持不变

### 8.7 删除分支保护规则

```bash
curl -s -X DELETE "$BASE/api/v1/repos/$REPO/branch-protection/$BP_RULE_ID" \
     -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

预期输出（HTTP 200）：
```json
{
    "status": "deleted",
    "id": 1
}
```

再次列出规则确认已删除：

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/branch-protection" | python3 -m json.tool
```

验证清单：
- [ ] DELETE 返回 HTTP 200，`status` 为 `"deleted"`
- [ ] 随后的 GET 不含已删除规则

---

## 9. 审查者规则

审查者规则（Reviewer Rules）根据文件路径的 Glob 模式自动匹配需要审查的 token。**创建/删除规则需要 admin 权限**。

### 9.1 创建审查者规则（不绑定 token）

```bash
curl -s -X POST "$BASE/api/v1/repos/$REPO/reviewer-rules" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "pattern": "train/**"
     }' | python3 -m json.tool
```

预期输出（HTTP 201）：
```json
{
    "id": 1,
    "repo_id": 2,
    "pattern": "train/**",
    "reviewer_token_id": null
}
```

```bash
export RR_ID=1
```

验证清单：
- [ ] 返回 HTTP 201
- [ ] `pattern` 为 `"train/**"`
- [ ] `reviewer_token_id` 为 `null`（未绑定特定 token）

### 9.2 创建审查者规则（绑定指定 token）

先获取一个 reviewer token 的 ID（从数据库查询或使用已知 ID）：

```bash
# 假设 reviewer token ID 为 2，根据实际情况调整
export REVIEWER_TK_ID=2

curl -s -X POST "$BASE/api/v1/repos/$REPO/reviewer-rules" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d "{
       \"pattern\": \"eval/**\",
       \"reviewer_token_id\": $REVIEWER_TK_ID
     }" | python3 -m json.tool
```

预期输出（HTTP 201）：
```json
{
    "id": 2,
    "repo_id": 2,
    "pattern": "eval/**",
    "reviewer_token_id": 2
}
```

验证清单：
- [ ] 返回 HTTP 201
- [ ] `reviewer_token_id` 为 2（绑定到指定令牌）

### 9.3 列出所有审查者规则

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/reviewer-rules" | python3 -m json.tool
```

预期输出：
```json
[
    {"id": 1, "pattern": "train/**", "reviewer_token_id": null, ...},
    {"id": 2, "pattern": "eval/**", "reviewer_token_id": 2, ...}
]
```

验证清单：
- [ ] 返回 HTTP 200，按 `id` 升序排列
- [ ] 包含刚创建的两条规则

### 9.4 匹配审查者规则（POST /reviewer-rules/match）

给定一组文件路径，返回匹配的规则列表：

```bash
curl -s -X POST "$BASE/api/v1/repos/$REPO/reviewer-rules/match" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "file_paths": ["train/sft.jsonl", "scripts/preprocess.py"]
     }' | python3 -m json.tool
```

预期输出（只匹配 `train/**` 规则）：
```json
[
    {
        "id": 1,
        "pattern": "train/**",
        "reviewer_token_id": null
    }
]
```

验证清单：
- [ ] 返回 HTTP 200
- [ ] 只返回匹配的规则（`train/**` 匹配 `train/sft.jsonl`）
- [ ] `eval/**` 规则不在结果中（没有 eval/ 路径的文件）

### 9.5 同时匹配多条规则

```bash
curl -s -X POST "$BASE/api/v1/repos/$REPO/reviewer-rules/match" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "file_paths": ["train/sft.jsonl", "eval/benchmark.jsonl"]
     }' | python3 -m json.tool
```

预期输出（两条规则都匹配）：
```json
[
    {"id": 1, "pattern": "train/**", ...},
    {"id": 2, "pattern": "eval/**", ...}
]
```

验证清单：
- [ ] 返回 HTTP 200，两条规则都出现在结果中

### 9.6 无文件匹配时返回空数组

```bash
curl -s -X POST "$BASE/api/v1/repos/$REPO/reviewer-rules/match" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "file_paths": ["docs/readme.md"]
     }' | python3 -m json.tool
```

预期输出：
```json
[]
```

验证清单：
- [ ] 返回 HTTP 200，空数组（没有规则匹配 `docs/readme.md`）

### 9.7 删除审查者规则

```bash
curl -s -X DELETE "$BASE/api/v1/repos/$REPO/reviewer-rules/$RR_ID" \
     -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

预期输出（HTTP 200）：
```json
{
    "status": "deleted",
    "id": 1
}
```

验证清单：
- [ ] 返回 HTTP 200，`status` 为 `"deleted"`
- [ ] GET 规则列表后不再包含已删除的规则

---

## 10. PR 状态管理

### 10.1 关闭 PR（status → closed）

```bash
# 先创建一个新的 open PR
export PR_TO_CLOSE=$(curl -s -X POST "$BASE/api/v1/repos/$REPO/pulls" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "title": "准备关闭的 PR",
       "source_branch": "feature",
       "target_branch": "main",
       "author": "tester"
     }' | python3 -c "import sys,json; print(json.load(sys.stdin).get('pull_request_id', 'ERROR'))")
echo "待关闭 PR ID：$PR_TO_CLOSE"

# 关闭 PR
curl -s -X PATCH "$BASE/api/v1/repos/$REPO/pulls/$PR_TO_CLOSE" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"status": "closed"}' | python3 -m json.tool
```

预期输出（HTTP 200）：
```json
{
    "pull_request_id": 4,
    "status": "closed",
    ...
}
```

验证清单：
- [ ] 返回 HTTP 200
- [ ] `status` 已变为 `"closed"`

### 10.2 重新开启已关闭的 PR（status → open）

```bash
curl -s -X PATCH "$BASE/api/v1/repos/$REPO/pulls/$PR_TO_CLOSE" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"status": "open"}' | python3 -m json.tool
```

预期输出（HTTP 200）：
```json
{
    "pull_request_id": 4,
    "status": "open",
    ...
}
```

验证清单：
- [ ] 返回 HTTP 200，`status` 重新变为 `"open"`

### 10.3 更新 PR 标题

```bash
curl -s -X PATCH "$BASE/api/v1/repos/$REPO/pulls/$PR_TO_CLOSE" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"title": "更新后的 PR 标题：重构数据清洗流程"}' | python3 -m json.tool
```

预期输出（HTTP 200）：
```json
{
    "pull_request_id": 4,
    "title": "更新后的 PR 标题：重构数据清洗流程",
    "status": "open",
    ...
}
```

验证清单：
- [ ] 返回 HTTP 200，`title` 已更新

### 10.4 同时更新标题和状态

```bash
curl -s -X PATCH "$BASE/api/v1/repos/$REPO/pulls/$PR_TO_CLOSE" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "title": "最终标题",
       "status": "closed"
     }' | python3 -m json.tool
```

验证清单：
- [ ] 返回 HTTP 200，`title` 和 `status` 均已更新

### 10.5 按状态过滤验证 closed PR

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/pulls?status=closed" | python3 -m json.tool
```

验证清单：
- [ ] 返回 HTTP 200，结果中包含刚关闭的 PR
- [ ] 所有返回的 PR `status` 均为 `"closed"`

---

## 11. 边界场景

### 11.1 合并已合并的 PR（400）

```bash
# 尝试再次合并已经是 merged 状态的 PR（使用第 6 节成功合并的 PR）
curl -s -X POST "$BASE/api/v1/repos/$REPO/pulls/$PR_ID/merge" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"message": "再次合并", "author": "tester"}' | python3 -m json.tool
```

预期输出（HTTP 400）：
```json
{
    "detail": "Pull request is already merged"
}
```

验证清单：
- [ ] 返回 HTTP 400
- [ ] 错误信息为 `"Pull request is already merged"`

### 11.2 合并已关闭的 PR（400）

```bash
# 找到一个 closed 状态的 PR
curl -s -X POST "$BASE/api/v1/repos/$REPO/pulls/$PR_TO_CLOSE/merge" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"message": "合并已关闭的 PR", "author": "tester"}' | python3 -m json.tool
```

预期输出（HTTP 400）：
```json
{
    "detail": "Cannot merge a closed pull request"
}
```

验证清单：
- [ ] 返回 HTTP 400，错误信息为 `"Cannot merge a closed pull request"`

### 11.3 更新已合并 PR 的状态（400）

```bash
curl -s -X PATCH "$BASE/api/v1/repos/$REPO/pulls/$PR_ID" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"status": "closed"}' | python3 -m json.tool
```

预期输出（HTTP 400）：
```json
{
    "detail": "Cannot update a merged pull request"
}
```

验证清单：
- [ ] 返回 HTTP 400，错误信息包含 `"Cannot update a merged pull request"`

### 11.4 创建相同 source 和 target 分支的 PR（400）

```bash
curl -s -X POST "$BASE/api/v1/repos/$REPO/pulls" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "title": "自我 PR",
       "source_branch": "main",
       "target_branch": "main",
       "author": "tester"
     }' | python3 -m json.tool
```

预期输出（HTTP 400）：
```json
{
    "detail": "Source and target branches must be different"
}
```

验证清单：
- [ ] 返回 HTTP 400，错误信息包含 `"must be different"`

### 11.5 创建 PR 时 source 分支不存在（404）

```bash
curl -s -X POST "$BASE/api/v1/repos/$REPO/pulls" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "title": "不存在的分支",
       "source_branch": "nonexistent-branch",
       "target_branch": "main",
       "author": "tester"
     }' | python3 -m json.tool
```

预期输出（HTTP 404）：
```json
{
    "detail": "Branch 'nonexistent-branch' not found"
}
```

验证清单：
- [ ] 返回 HTTP 404，错误信息明确指出不存在的分支名

### 11.6 没有 required_approvals 时直接合并不被阻止

```bash
# 确认当前 main 分支无保护规则（若 8.7 节已删除规则）
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/branch-protection" | python3 -m json.tool
```

若规则列表为空，不带 `pull_request_id` 的 `/merge` 请求应该成功：

```bash
curl -s -X POST "$BASE/api/v1/repos/$REPO/merge" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "source_branch": "feature",
       "target_branch": "main",
       "message": "无保护规则，直接合并",
       "author": "tester"
     }' | python3 -m json.tool
```

预期输出（HTTP 200）：
```json
{
    "commit_hash": "...",
    "fast_forward": true
}
```

验证清单：
- [ ] 无分支保护规则时，合并返回 HTTP 200

### 11.7 重复创建相同 branch_pattern 的保护规则（409）

```bash
# 先创建一条规则
curl -s -X POST "$BASE/api/v1/repos/$REPO/branch-protection" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"branch_pattern": "stable"}' > /dev/null

# 再次创建相同 pattern
curl -s -X POST "$BASE/api/v1/repos/$REPO/branch-protection" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"branch_pattern": "stable"}' | python3 -m json.tool
```

预期输出（HTTP 409）：
```json
{
    "detail": "Branch protection rule for pattern 'stable' already exists"
}
```

验证清单：
- [ ] 返回 HTTP 409，错误信息包含 `"already exists"`

### 11.8 删除不存在的保护规则（404）

```bash
curl -s -X DELETE "$BASE/api/v1/repos/$REPO/branch-protection/99999" \
     -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

预期输出（HTTP 404）：
```json
{
    "detail": "Branch protection rule #99999 not found"
}
```

验证清单：
- [ ] 返回 HTTP 404

### 11.9 删除不存在的审查者规则（404）

```bash
curl -s -X DELETE "$BASE/api/v1/repos/$REPO/reviewer-rules/99999" \
     -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

预期输出（HTTP 404）：
```json
{
    "detail": "Reviewer rule #99999 not found"
}
```

验证清单：
- [ ] 返回 HTTP 404

### 11.10 对不存在的 PR 提交评论（404）

```bash
curl -s -X POST "$BASE/api/v1/repos/$REPO/pulls/9999/comments" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"author": "r1", "body": "不存在的 PR"}' | python3 -m json.tool
```

预期输出（HTTP 404）：
```json
{
    "detail": "PR #9999 not found"
}
```

验证清单：
- [ ] 返回 HTTP 404

---

## 小结

| 端点 | 方法 | 最低权限 | 关键验证点 |
|------|------|----------|-----------|
| `/api/v1/repos/{repo}/pulls` | POST | push | 201 创建，自动计算 diff 统计和可合并性 |
| `/api/v1/repos/{repo}/pulls` | GET | read | 列表，支持 `?status=` 过滤 |
| `/api/v1/repos/{repo}/pulls/{id}` | GET | read | 单个 PR 详情，404 不存在 |
| `/api/v1/repos/{repo}/pulls/{id}` | PATCH | push | 更新标题/状态，400 不可更新 merged |
| `/api/v1/repos/{repo}/pulls/{id}/merge` | POST | push | 合并（快进/三路），409 有冲突，400 已合并/关闭 |
| `/api/v1/repos/{repo}/pulls/{id}/resolve` | POST | push | 解决冲突后合并，400 无冲突时报错 |
| `/api/v1/repos/{repo}/pulls/{id}/comments` | POST | read | 创建评论，支持通用/文件/行/字段粒度 |
| `/api/v1/repos/{repo}/pulls/{id}/comments` | GET | read | 列表，支持 `?file_path=` 过滤 |
| `/api/v1/repos/{repo}/pulls/{id}/comments/{cid}` | PATCH | read | 更新评论内容 |
| `/api/v1/repos/{repo}/pulls/{id}/comments/{cid}` | DELETE | push | 删除评论 |
| `/api/v1/repos/{repo}/pulls/{id}/reviews` | POST | reviewer | 提交审查（upsert），422 非法 status |
| `/api/v1/repos/{repo}/pulls/{id}/reviews` | GET | read | 列出审查 |
| `/api/v1/repos/{repo}/merge-preview` | POST | read | 预检合并冲突，不写入 |
| `/api/v1/repos/{repo}/merge` | POST | push | 直接合并（含分支保护检查） |
| `/api/v1/repos/{repo}/branch-protection` | GET | read | 列出保护规则 |
| `/api/v1/repos/{repo}/branch-protection` | POST | admin | 创建规则，409 重复 pattern |
| `/api/v1/repos/{repo}/branch-protection/{id}` | PUT | admin | 更新规则（PATCH 语义，只传修改字段） |
| `/api/v1/repos/{repo}/branch-protection/{id}` | DELETE | admin | 删除规则，404 不存在 |
| `/api/v1/repos/{repo}/reviewer-rules` | GET | read | 列出审查者规则 |
| `/api/v1/repos/{repo}/reviewer-rules` | POST | admin | 创建规则（可绑定 token） |
| `/api/v1/repos/{repo}/reviewer-rules/match` | POST | read | Glob 匹配文件路径返回规则 |
| `/api/v1/repos/{repo}/reviewer-rules/{id}` | DELETE | admin | 删除规则，404 不存在 |
