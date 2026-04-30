# Pull Request 与代码审查

Dit 的 Pull Request（PR）机制用于在合并数据变更前进行审查。PR 通过 dit-core 的 REST API 或 datahub-gateway Web UI 操作。本指南以 API 调用为主，展示完整的 PR 工作流。

## 前置准备

```bash
# 服务端地址和认证令牌
export BASE="http://server:8000"
export TOKEN="dit_xxxxxxxxxxxx"
export REPO="my-dataset"
```

确保仓库中已有至少两个分支（如 `main` 和 `feature`），且 `feature` 分支包含待审查的变更。

## 完整工作流

### 1. 创建 PR

```bash
curl -s -X POST "$BASE/api/v1/repos/$REPO/pulls" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "title": "新增 200 条对话训练数据",
       "description": "扩充客服场景的 SFT 数据，覆盖退换货和投诉处理。",
       "source_branch": "feature",
       "target_branch": "main"
     }' | python3 -m json.tool
```

返回示例：

```json
{
    "pull_request_id": 1,
    "title": "新增 200 条对话训练数据",
    "status": "open",
    "source_ref": "heads/feature",
    "target_ref": "heads/main",
    "is_mergeable": true,
    "stats_added": 200,
    "stats_removed": 0,
    "stats_refreshed": 0
}
```

服务端会自动计算 diff 统计（新增/删除/刷新行数）和可合并性。

```bash
export PR_ID=1
```

### 2. 查看 PR 详情

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/pulls/$PR_ID" | python3 -m json.tool
```

列出仓库所有 PR，支持按状态过滤：

```bash
# 所有 open 状态的 PR
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/pulls?status=open" | python3 -m json.tool
```

### 3. 添加评论

PR 评论支持四种粒度，从粗到细：

**通用评论**（针对整个 PR）：

```bash
curl -s -X POST "$BASE/api/v1/repos/$REPO/pulls/$PR_ID/comments" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "author": "reviewer1",
       "body": "整体数据质量不错，有几行需要仔细检查。"
     }' | python3 -m json.tool
```

**文件级评论**（针对某个文件）：

```bash
curl -s -X POST "$BASE/api/v1/repos/$REPO/pulls/$PR_ID/comments" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "author": "reviewer1",
       "body": "这个文件中部分行的 system 提示词格式不统一。",
       "file_path": "train/sft.jsonl"
     }' | python3 -m json.tool
```

**行级评论**（针对某一数据行，通过 row_hash 定位）：

```bash
curl -s -X POST "$BASE/api/v1/repos/$REPO/pulls/$PR_ID/comments" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "author": "reviewer2",
       "body": "这行的 assistant 回复存在事实性错误。",
       "file_path": "train/sft.jsonl",
       "row_hash": "a1b2c3d4e5f6...",
       "change_type": "added"
     }' | python3 -m json.tool
```

**字段级评论**（针对某行的特定 JSON 字段）：

```bash
curl -s -X POST "$BASE/api/v1/repos/$REPO/pulls/$PR_ID/comments" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "author": "reviewer2",
       "body": "messages[1].content 语义不清晰，建议改写。",
       "file_path": "train/sft.jsonl",
       "row_hash": "a1b2c3d4e5f6...",
       "field_path": "messages[1].content",
       "change_type": "refreshed"
     }' | python3 -m json.tool
```

查看 PR 的所有评论：

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/pulls/$PR_ID/comments" | python3 -m json.tool
```

按文件路径过滤评论：

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/pulls/$PR_ID/comments?file_path=train/sft.jsonl" \
     | python3 -m json.tool
```

### 4. 提交审查

审查有两种状态：`approved`（批准）和 `changes_requested`（要求修改）。

```bash
# 批准
curl -s -X POST "$BASE/api/v1/repos/$REPO/pulls/$PR_ID/reviews" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"status": "approved"}' | python3 -m json.tool
```

```bash
# 要求修改
curl -s -X POST "$BASE/api/v1/repos/$REPO/pulls/$PR_ID/reviews" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"status": "changes_requested"}' | python3 -m json.tool
```

同一令牌对同一 PR 再次提交审查会覆盖之前的状态（upsert），不会新增记录。

### 5. 合并 PR

```bash
curl -s -X POST "$BASE/api/v1/repos/$REPO/pulls/$PR_ID/merge" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "message": "Merge: 新增 200 条对话训练数据",
       "author": "maintainer"
     }' | python3 -m json.tool
```

返回示例：

```json
{
    "pull_request_id": 1,
    "status": "merged",
    "merge_commit": "e5f6a7b8...",
    "fast_forward": true
}
```

如果存在冲突，合并会返回 HTTP 409 并列出冲突文件，需要通过 `/resolve` 端点解决。

## 分支保护规则

管理员可以为特定分支设置保护规则，要求 PR 必须获得足够的审批才能合并。

### 创建保护规则

```bash
curl -s -X POST "$BASE/api/v1/repos/$REPO/branch-protection" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "branch_pattern": "main",
       "require_pr": true,
       "required_approvals": 1,
       "block_force_push": true
     }' | python3 -m json.tool
```

`branch_pattern` 支持 glob 模式，例如 `release/*` 可以匹配所有 release 分支。

### 保护规则生效后

未满足审批要求时，合并会被拒绝（HTTP 403）：

```json
{
    "detail": "Branch 'main' requires 1 approval(s), but only 0 found for PR 1."
}
```

需要先获得足够的 approved 审查，再执行合并。

### 查看和删除规则

```bash
# 列出所有保护规则
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/branch-protection" | python3 -m json.tool

# 删除规则
curl -s -X DELETE "$BASE/api/v1/repos/$REPO/branch-protection/1" \
     -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

## 审查者规则

审查者规则根据文件路径的 glob 模式自动匹配需要审查的人员，可选绑定到特定令牌。

```bash
# 创建规则：train 目录下的文件变更需要审查
curl -s -X POST "$BASE/api/v1/repos/$REPO/reviewer-rules" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"pattern": "train/**"}' | python3 -m json.tool

# 查询哪些规则匹配给定文件
curl -s -X POST "$BASE/api/v1/repos/$REPO/reviewer-rules/match" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"file_paths": ["train/sft.jsonl", "eval/test.jsonl"]}' \
     | python3 -m json.tool
```

## 典型审查流程总结

```
1. 标注员在 feature 分支提交数据变更并推送
2. 通过 API 或 Web UI 创建 PR（feature → main）
3. 审查者查看 diff，添加行级/字段级评论
4. 标注员根据评论修改数据，推送新提交
5. 审查者确认修改，提交 approved 审查
6. 满足分支保护要求后，合并 PR
```

## 注意事项

- 创建评论和提交审查需要 reviewer 及以上权限，合并需要 committer 及以上权限
- 已合并的 PR 不能再次合并（HTTP 400），已关闭的 PR 需要先重新打开
- 同一 source 和 target 分支不能创建 PR（HTTP 400）
- 审查状态是 upsert 语义：同一令牌重复提交会覆盖，不会累加审批数
- 分支保护规则的创建和删除需要 admin 权限
