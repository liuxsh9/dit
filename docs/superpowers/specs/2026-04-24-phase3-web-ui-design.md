# Phase 3: Web UI — Forgejo 集成设计文档

## 1. 目标

在 Forgejo 上实现 DataHub 的 Web 界面：创建/浏览数据仓库、PR 审查、行级评论、权限管理、通知集成。Forgejo 已有完整的协作基础设施（用户/组织/PR/通知），Phase 3 的核心是**在现有框架内做有限改造**——新增 `data` repo type，替换对应模板调 datahub-core API。

## 2. 前置依赖

- Phase 0 (本地 dit CLI)、Phase 1 (远程协作)、Phase 2 (合并与分支) 已完成
- datahub-core FastAPI 服务已有：objects/refs CAS/merge/merge-preview/webhooks/tokens 路由
- dit CLI 已有：init/add/commit/log/diff/status/branch/checkout/merge/cherry-pick/tag/push/clone/fetch/pull

## 3. 整体架构

### 3.1 服务拓扑

```
                    ┌──────────────────────────────────────┐
                    │      datahub-gateway (Forgejo fork)   │
  浏览器 ──HTTP──→  │                                      │
  dit CLI ──HTTP──→ │  Go 后端 + Vue 3 前端                │
                    │  ┌─ 用户/组织/Team          (内置)    │
                    │  ├─ OAuth2/API token/SSH    (内置)    │
                    │  ├─ PR 生命周期/评论        (内置+扩展)│
                    │  ├─ 通知/邮件/Webhook       (内置)    │
                    │  ├─ /api/v1/repos/.../datahub/*       │
                    │  │  (代理路由 → datahub-core)         │
                    │  └─ data repo 模板 (新增)             │
                    └────────────┬──────────────────────────┘
                                 │ HTTP (内网)
                    ┌────────────▼──────────────────────────┐
                    │      datahub-core (Python FastAPI)     │
                    │  ┌─ 对象存储 (rows/manifests/trees)    │
                    │  ├─ Refs CAS                           │
                    │  ├─ Diff / Merge / Blame                │
                    │  ├─ 搜索 / 统计                        │
                    │  └─ sidecar 元数据                     │
                    └────────────┬──────────────────────────┘
                                 │
                    ┌────────────▼────────┐
                    │   PostgreSQL         │
                    │   datahub schema     │
                    │   forgejo schema     │
                    └─────────────────────┘
                    ┌─────────────────────┐
                    │   文件系统 /data/    │
                    │   content-addressed  │
                    │   objects            │
                    └─────────────────────┘
```

### 3.2 关键架构决策

1. **datahub-core 退化为内部服务**：不再对外暴露端口。所有外部请求经 Forgejo 代理，统一认证。Phase 0-2 的 token 体系保留为服务间认证（Forgejo → datahub-core 用内部 service token）。

2. **共享 PostgreSQL 实例，不同 schema**：Forgejo 用自己的 schema，datahub-core 用 `datahub` schema。PR 表在 Forgejo 侧，通过 `repo_id` 关联。

3. **Forgejo `repo.type` 扩展**：新增 `type=data` 枚举值。`type=data` 时：
   - 不创建 bare git repo
   - 文件浏览/diff/commit 走 datahub-core API
   - PR merge 走 datahub-core merge API
   - 其余（Issue、Wiki、Discussion）保持不变

4. **CLI 走 Forgejo 代理**：`dit push/clone/fetch` 改调 Forgejo 代理路由，统一认证入口。datahub-core 不再直接面向用户。

5. **PR 评论存 Forgejo 侧**：复用 Forgejo 已有 Comment 模型，扩展字段支持行级定位（`row_hash` + `field_path`），免费获得通知/邮件/webhook 集成。

6. **Forgejo 开发方式**：Fork 源码做侵入式修改，保持 Docker 化部署兼容，沿用 Forgejo 主线的 CI/发布基础设施。

### 3.3 通信方式

Forgejo (Go) → datahub-core (Python) 通过 HTTP API 调用。Forgejo 代理层职责：

1. 认证校验（Forgejo token → 权限检查）
2. 附加内部 service token header
3. 转发请求到 datahub-core
4. 透传响应

Service token 为预共享密钥，通过环境变量 `DATAHUB_SERVICE_TOKEN` 配置，Forgejo 和 datahub-core 启动时加载。

---

## 4. 子项目拆分

按功能垂直拆分为 3 个子项目，每个独立出 plan → 实现：

| 子项目 | 范围 | 涉及层 | 依赖 |
|--------|------|--------|------|
| **3A: 数据仓库基础** | data repo type、文件浏览、dataset 主页、CLI 代理路由 | Go + Vue + Python | 无 |
| **3B: PR & Diff 审查** | PR 创建/合并流程、三段式 Diff UI、行级评论 | Go + Vue + Python | 3A |
| **3C: 权限 & 通知** | 6 级权限映射、分支保护、通知集成 | Go + Vue | 3B |

执行顺序：3A → 3B → 3C

---

## 5. 子项目 3A：数据仓库基础

### 5.1 Forgejo 后端改造 (Go)

#### repo.type 扩展

- `models/repo/type.go` 新增 `RepositoryTypeData` 枚举值
- 创建 data repo 时跳过 `git init --bare`，改为调 datahub-core `POST /repos` 初始化
- 数据库 `repository` 表已有 `type` 字段（Forgejo 原生支持 mirror 等类型），直接扩展枚举

#### 代理路由 (Proxy Routes)

新增路由组 `/api/v1/repos/{owner}/{repo}/datahub/*`，约 10 个 endpoint 一一代理到 datahub-core：

| Forgejo 代理路由 | datahub-core 目标 | 用途 |
|---|---|---|
| `POST .../datahub/objects/{type}/{hash}` | `POST /v1/repos/{repo}/objects/{type}/{hash}` | push 上传对象 |
| `GET .../datahub/objects/{type}/{hash}` | `GET /v1/repos/{repo}/objects/{type}/{hash}` | 读取对象 |
| `POST .../datahub/objects/batch-exists` | `POST /v1/repos/{repo}/objects/batch-exists` | 批量检查 |
| `GET .../datahub/refs` | `GET /v1/repos/{repo}/refs` | 列出引用 |
| `GET .../datahub/refs/{type}/{name}` | `GET /v1/repos/{repo}/refs/{type}/{name}` | 读取引用 |
| `POST .../datahub/refs/{type}/{name}` | `POST /v1/repos/{repo}/refs/{type}/{name}` | CAS 更新引用 |
| `DELETE .../datahub/refs/{type}/{name}` | `DELETE /v1/repos/{repo}/refs/{type}/{name}` | 删除引用 |
| `GET .../datahub/tree/{commit}/{path}` | `GET /v1/repos/{repo}/tree/{commit}/{path}` | 目录树 |
| `GET .../datahub/manifest/{commit}/{path}` | `GET /v1/repos/{repo}/manifest/{commit}/{path}` | 文件内容 |
| `GET .../datahub/log` | `GET /v1/repos/{repo}/log` | 提交历史 |
| `POST .../datahub/diff` | `POST /v1/repos/{repo}/diff` | Diff 计算 |
| `POST .../datahub/merge-preview` | `POST /v1/repos/{repo}/merge-preview` | 合并预览 |
| `POST .../datahub/merge` | `POST /v1/repos/{repo}/merge` | 执行合并 |

#### 内部 HTTP 客户端

Go 侧封装 `DataHubClient` struct：

```go
type DataHubClient struct {
    BaseURL      string   // e.g. "http://datahub-core:8000/api/v1"
    ServiceToken string   // 内部 service token
    HTTPClient   *http.Client
}
```

配置通过 Forgejo `app.ini` 的 `[datahub]` section：

```ini
[datahub]
CORE_URL = http://localhost:8000/api/v1
SERVICE_TOKEN = <shared-secret>
```

### 5.2 datahub-core API 扩展 (Python)

Phase 0-2 已有的 API 基本够用，需补充以下端点：

| 新增 API | 说明 |
|---|---|
| `GET /v1/repos/{repo}/tree/{commit_hash}/{path}` | 返回 tree 对象的目录列表（名称、类型、hash、行数） |
| `GET /v1/repos/{repo}/manifest/{commit_hash}/{path}` | 返回 manifest 内容，支持分页（`offset`/`limit` 查询参数） |
| `GET /v1/repos/{repo}/log?ref=&limit=&offset=` | 提交历史，支持分页和按 ref 过滤 |
| `POST /v1/repos/{repo}/objects/batch-exists` | 接收 hash 列表，返回存在/不存在的 hash 集合 |

#### 内部认证中间件

新增 service token 认证：请求头 `X-Service-Token` 匹配预共享密钥时跳过 Forgejo token 校验。datahub-core 同时保留现有 token 认证（向后兼容开发/测试场景）。

### 5.3 Web 前端 — 数据集主页

#### 数据集主页（替换 git 仓库主页）

- 顶部：repo 名称、描述、star/fork 计数（复用 Forgejo 现有组件）
- 统计卡片：文件数、总行数、最近提交时间
- 文件树浏览器：目录 + `.jsonl` 文件列表，显示行数
- 分支/tag 选择器：复用 Forgejo 组件，数据源改为 datahub refs API
- 描述区域：文件列表下方渲染 Forgejo repo description（Markdown 格式），不在 data repo tree 中存 README（tree 只支持 JSONL manifest）

#### JSONL 文件查看器

- 分页加载（每页 50 行，通过 manifest API 的 offset/limit）
- 每行 JSON 结构化渲染：
  - `messages[]` 数组展开为对话气泡
  - `role` 标签着色：user=蓝、assistant=绿、system=灰、tool=橙
  - 代码块语法高亮
- 行号 + row_hash 短码（前 8 位）显示
- 可折叠长内容（超过 5 行的 content 默认折叠）

#### 实现策略

- Forgejo 用 Go template 渲染页面骨架
- `type=data` 时加载不同模板：
  - `templates/repo/datahub/home.tmpl` — 数据集主页
  - `templates/repo/datahub/view.tmpl` — 文件查看
  - `templates/repo/datahub/commits.tmpl` — 提交历史
- 文件查看器的交互部分用 Vue 3 组件，挂载到模板中的 `<div id="datahub-viewer">`
- 数据通过 Forgejo 代理 API 获取（前端 JS → Forgejo API → datahub-core）

### 5.4 dit CLI 适配

- `dit remote` URL 格式改为 Forgejo 地址：`http://forgejo:3000/{owner}/{repo}`
- `dit push/clone/fetch` 改调 Forgejo 代理路由（`/api/v1/repos/{owner}/{repo}/datahub/*`）
- 认证改用 Forgejo API token：
  - `dit auth login <forgejo-url>` — 输入 Forgejo 用户名 + API token
  - token 存储在 `~/.datahub/credentials`（已有机制）
  - 请求头改为 `Authorization: token <forgejo-api-token>`（Forgejo 标准格式）

---

## 6. 子项目 3B：PR & Diff 审查

### 6.1 PR 数据模型

复用 Forgejo 现有 `pull_request` 表，新增副表存 data-specific 元数据：

```sql
CREATE TABLE data_pull_request_meta (
    id              BIGSERIAL PRIMARY KEY,
    pull_request_id BIGINT NOT NULL REFERENCES pull_request(id),
    source_ref      VARCHAR(256) NOT NULL,
    target_ref      VARCHAR(256) NOT NULL,
    base_commit     CHAR(64) NOT NULL,
    source_commit   CHAR(64) NOT NULL,
    target_commit   CHAR(64) NOT NULL,
    merge_commit    CHAR(64),
    is_mergeable    BOOLEAN,
    conflict_files  TEXT,
    stats_added     INT DEFAULT 0,
    stats_removed   INT DEFAULT 0,
    stats_refreshed INT DEFAULT 0,
    UNIQUE(pull_request_id)
);
```

字段说明：
- `source_ref` / `target_ref`：PR 的源和目标分支（如 `heads/feature/zhangsan`、`heads/main`）
- `base_commit`：三方合并的公共祖先 commit hash
- `source_commit` / `target_commit`：创建 PR 时的两端 HEAD
- `merge_commit`：合并后的 commit hash（合并前为 null）
- `is_mergeable`：缓存的可合并状态（由 merge-preview 更新）
- `conflict_files`：冲突文件路径的 JSON 数组
- `stats_*`：变更统计摘要

### 6.2 PR 生命周期

| 操作 | Forgejo 侧 | datahub-core 侧 |
|---|---|---|
| 创建 PR | 写入 `pull_request` + `data_pull_request_meta` | 调 `POST /diff` 预计算变更统计 |
| 查看 Diff | 前端请求 Diff 数据 | 调 `POST /diff` 实时计算 |
| 更新 PR（新 push） | 更新 `source_commit`，重算统计 | 调 `POST /diff` + `POST /merge-preview` |
| Merge PR | 更新状态为 merged | 调 `POST /merge` 执行合并 |
| 关闭 PR | 更新状态为 closed | 无操作 |

PR 创建入口：
- Web UI：Forgejo PR 创建表单，`type=data` 时后端走 datahub 流程
- CLI：`dit pr create <branch> --title "..."` → 调 Forgejo PR API

### 6.3 datahub-core Diff API 扩展

现有 `POST /diff` 返回基础 diff，需扩展响应格式以支持 PR 审查场景：

```json
// 请求
POST /v1/repos/{repo}/diff
{
    "from_ref": "heads/main",
    "to_ref": "heads/feature/zhangsan",
    "file_path": "feature-impl/coding-hard.jsonl",  // 可选，单文件 diff
    "offset": 0,
    "limit": 100
}

// 响应
{
    "summary": {
        "files_changed": 3,
        "rows_added": 150,
        "rows_removed": 42,
        "rows_refreshed": 78
    },
    "files": [
        {
            "path": "feature-impl/coding-hard.jsonl",
            "added": 80,
            "removed": 20,
            "refreshed": 45,
            "changes": [
                {
                    "type": "added",
                    "row_hash": "a1b2c3...",
                    "row_content": { "messages": [...] },
                    "position": 142
                },
                {
                    "type": "refreshed",
                    "old_row_hash": "d4e5f6...",
                    "new_row_hash": "g7h8i9...",
                    "query_fingerprint": "qfp123...",
                    "old_content": { "messages": [...] },
                    "new_content": { "messages": [...] },
                    "position": 207
                },
                {
                    "type": "removed",
                    "row_hash": "j0k1l2...",
                    "row_content": { "messages": [...] },
                    "position": 89
                }
            ]
        }
    ],
    "has_more": true,
    "total_changes": 270
}
```

字段说明：
- `position`：行在目标 manifest 中的索引位置（0-based），用于前端排序和定位
- 当 `file_path` 省略时，返回所有文件的变更摘要（不含 `row_content`/`changes`，只含计数）
- 指定 `file_path` 时返回该文件的详细行级变更，支持 offset/limit 分页

### 6.4 Diff UI — 三段折叠渲染 (Vue 3)

#### 文件列表视图

PR 的 "Files changed" 标签页：
- 文件列表，每文件显示 `+N -N ~N`（新增/删除/刷新）
- 点击文件展开该文件的行级 diff

#### 单文件 Diff 视图

三段折叠布局：

```
┌─ feature-impl/coding-hard.jsonl ──────────────────┐
│  +80 行  -20 行  ~45 刷新                          │
│                                                    │
│  ▼ 删除 (20 行)                                    │
│  ┌────────────────────────────────────────────┐    │
│  │ #89  [user] 请实现快速排序                 │ 💬 │
│  │      [assistant] def quicksort(arr):...    │    │
│  │ #102 [user] 解释递归的概念                 │ 💬 │
│  │      [assistant] 递归是指函数调用自身...    │    │
│  └────────────────────────────────────────────┘    │
│                                                    │
│  ▼ 新增 (80 行)                                    │
│  ┌────────────────────────────────────────────┐    │
│  │ #142 [user] 实现二叉搜索树                 │ 💬 │
│  │      [assistant] class BST: ...            │    │
│  │ ...                                         │    │
│  │      [加载更多... 显示 50/80]               │    │
│  └────────────────────────────────────────────┘    │
│                                                    │
│  ▼ 刷新 (45 行) — query 不变，response 更新        │
│  ┌────────────────────────────────────────────┐    │
│  │ #207 [user] 写一个归并排序                 │    │
│  │      旧 [assistant] def merge_sort(a)...   │ 红 │
│  │      新 [assistant] def merge_sort(lst)... │ 绿 │
│  └────────────────────────────────────────────┘    │
└────────────────────────────────────────────────────┘
```

交互规则：
- 默认三段全部折叠，只显示数字摘要
- 每段展开后按 position 排序
- 单文件 >100 行变更时分页加载（每页 50 行，通过 diff API 的 offset/limit）
- 刷新行并排展示 old/new response，高亮文本级差异
- 每行右侧有评论图标，点击展开评论输入框

#### Vue 3 组件结构

```
web_src/js/components/datahub/
├── DiffView.vue           # 顶层 diff 容器
├── DiffFileSummary.vue    # 文件列表中的单文件摘要行
├── DiffFileDetail.vue     # 单文件的三段折叠 diff
├── DiffSection.vue        # 一个折叠段（deleted/added/refreshed）
├── DiffRow.vue            # 单行变更的渲染
├── DiffRowRefreshed.vue   # 刷新行的 old/new 并排渲染
├── JsonlRowRenderer.vue   # JSONL 行的结构化渲染（messages 展开、role 着色）
├── RowComment.vue         # 行级评论线程
└── shared/
    ├── Pagination.vue     # 分页加载控件
    └── api.ts             # datahub API 调用封装
```

### 6.5 行级评论

#### 数据模型扩展

扩展 Forgejo `comment` 表，新增字段：

```sql
ALTER TABLE comment ADD COLUMN row_hash CHAR(64);
ALTER TABLE comment ADD COLUMN field_path VARCHAR(256);
ALTER TABLE comment ADD COLUMN change_type VARCHAR(16);
```

- `tree_path`：JSONL 文件路径（复用现有字段）
- `row_hash`：被评论的行 hash（新字段）
- `field_path`：JSON 内部路径，可选，精确到 messages 的某个元素（如 `messages[2].content`）
- `change_type`：评论所在的 diff 区段（`added` / `removed` / `refreshed`）

#### 评论渲染

前端根据 `row_hash` 将评论锚定到 diff 中对应行的下方。复用 Forgejo 现有的评论线程 UI：reply、resolve/unresolve、emoji reaction。

#### 评论 API

复用 Forgejo 的 PR comment API（`POST /api/v1/repos/{owner}/{repo}/pulls/{index}/reviews`），扩展请求体支持新字段：

```json
{
    "body": "这个 response 的格式不对，缺少 closing tag",
    "path": "feature-impl/coding-hard.jsonl",
    "row_hash": "a1b2c3d4...",
    "field_path": "messages[1].content",
    "change_type": "added"
}
```

### 6.6 Merge 执行流程

```
用户点击 "Merge PR"
        │
        ▼
Forgejo 后端: 权限检查（是否有 push/maintainer 权限）
        │
        ▼
Forgejo 后端: 检查分支保护规则（review 数、CI 状态）
        │
        ▼
Forgejo → datahub-core: POST /merge
        { source_ref, target_ref, message, author }
        │
        ├─ 成功 → 返回 merge_commit hash
        │         更新 data_pull_request_meta.merge_commit
        │         更新 PR 状态为 merged
        │         触发合并通知
        │
        └─ 冲突 → 返回 conflict 详情
                  显示冲突文件列表
                  用户在 Web 上按行选边解决
```

冲突解决 UI（3B 后期）：
- 列出冲突文件和冲突行
- 每行冲突展示 ours/theirs 两个版本
- 用户逐行点选保留哪一方
- 确认后提交解决方案，重新尝试合并

---

## 7. 子项目 3C：权限 & 通知

### 7.1 权限模型映射

DataHub 的 6 级角色映射到 Forgejo 的 Team/Collaboration 权限体系：

| DataHub 角色 | Forgejo 映射 | 实现方式 |
|---|---|---|
| **Owner** | Repository Owner | Forgejo 内置 |
| **Admin** | Team with Admin access | Forgejo 内置 |
| **Maintainer** | Team with Write access + `can_merge` 标记 | 扩展 Team 表加 `can_merge` 布尔字段 |
| **Committer** | Team with Write access | Forgejo 内置 |
| **Reviewer** | Team with custom `review` access level | 新增 access level |
| **Reader** | Team with Read access | Forgejo 内置 |

Forgejo 原生有 Owner / Admin / Write / Read 四级。需要扩展的部分：

1. **Maintainer vs Committer 区分**：两者在 Forgejo 均为 Write access。区分方式是在 `team` 表或 `collaboration` 表新增 `can_merge BOOLEAN DEFAULT false`。权限检查时：Write + can_merge = Maintainer，Write 无 can_merge = Committer。

2. **Reviewer 角色**：新增 access level 介于 Read 和 Write 之间。Reviewer 可以查看 PR diff、提交 review comment、approve/request changes，但不能 push 或 merge。

#### 权限检查点

| 操作 | 所需最低角色 | 检查位置 |
|---|---|---|
| 浏览仓库 / clone / export | Reader | Forgejo 内置 |
| 提交 PR review / 评论 | Reviewer | Forgejo 代理层 |
| push 到非保护分支 | Committer | Forgejo 代理层 |
| 创建 PR | Committer | Forgejo 内置 |
| Merge PR | Maintainer | Forgejo 代理层 |
| 创建/删除分支 | Maintainer | Forgejo 代理层 |
| 管理成员/设置 | Admin | Forgejo 内置 |
| 删除仓库 | Owner | Forgejo 内置 |

代理路由的权限检查逻辑：
1. Forgejo 中间件已完成 token → user 认证
2. 代理层查询 user 在该 repo 的 access level
3. 对比所需角色，不足则返回 403

### 7.2 分支保护规则

复用 Forgejo 的 `protected_branch` 表和机制，为 `type=data` 的 repo 做适配：

| 保护规则 | Forgejo 已有 | 需要适配 |
|---|---|---|
| 必须通过 PR 合入 | 是 | 合并时调 datahub-core 而非 git merge |
| 需要 N 个 approve | 是 | 复用 |
| 必须通过 CI（status check） | 是 | 复用 |
| 特定 reviewer 必审 | 部分（CODEOWNERS） | 扩展：按文件路径模式配置必审人 |
| 不允许 force push | 是 | 代理层拦截 CAS 的 force update |
| 合入后自动删除 feature 分支 | 是 | merge 完成后调 `DELETE /refs` |

#### 必审人配置

在 Forgejo 仓库设置中新增"必审人规则"配置页面（存储在 Forgejo 数据库，非 data repo tree 中的文件，因为 tree 只支持 JSONL manifest）：

| 文件路径模式 | 必须审批的团队/用户 |
|---|---|
| `feature-impl/**` | @team-feature-lead |
| `bug-fix/**` | @team-qa |
| `general/**` | @team-data-lead |
| `*` | @team-maintainers |

数据模型：新增 `data_reviewer_rule` 表：

```sql
CREATE TABLE data_reviewer_rule (
    id          BIGSERIAL PRIMARY KEY,
    repo_id     BIGINT NOT NULL REFERENCES repository(id),
    pattern     VARCHAR(256) NOT NULL,
    team_id     BIGINT REFERENCES team(id),
    user_id     BIGINT REFERENCES "user"(id),
    CHECK (team_id IS NOT NULL OR user_id IS NOT NULL)
);
```

PR 创建/更新时，Forgejo 后端根据 diff 涉及的文件路径匹配规则，自动分配 reviewer。

### 7.3 通知集成

完全复用 Forgejo 现有通知体系，无需额外开发：

| 事件 | 通知方式 | 来源 |
|---|---|---|
| PR 创建 | Web + 邮件 | Forgejo 内置 |
| PR 评论 | Web + 邮件 | Forgejo 内置 |
| PR 合并 | Web + 邮件 | Forgejo 内置 |
| Review 请求 | Web + 邮件 | Forgejo 内置 |
| 提及 @user | Web + 邮件 | Forgejo 内置 |
| 分支保护违规 | Web | Forgejo 内置 |

因为 PR 评论存 Forgejo 侧，所有评论相关的通知自动生效。

### 7.4 Webhook 集成

Phase 2 的 webhook 骨架（datahub-core 侧）将被 Forgejo 内置 webhook 替代：

- Forgejo 原生 webhook 支持：push、PR 创建/合并/关闭、comment、release 等事件
- `type=data` 的 repo 触发的 webhook 事件与 git repo 一致
- datahub-core 侧的 webhook 表和路由可在 Phase 3 完成后标记为 deprecated

---

## 8. 前端技术方案

### 8.1 Vue 3 集成方式

Forgejo 前端混合使用 Go template + Vue 3（Fomantic UI）。DataHub 的前端组件策略：

- 页面骨架（header、sidebar、footer）复用 Go template
- 数据展示区域用 Vue 3 SFC（Single File Components）
- 构建：集成到 Forgejo 现有的 webpack/esbuild 构建流程
- 路由：不使用 Vue Router，每个页面是独立的 Vue app 挂载点

### 8.2 新增模板文件

```
templates/repo/datahub/
├── home.tmpl              # 数据集主页
├── view.tmpl              # JSONL 文件查看
├── commits.tmpl           # 提交历史
├── diff.tmpl              # PR diff 视图（Vue 3 挂载点）
└── settings.tmpl          # 数据仓库设置（权限、保护规则）
```

### 8.3 新增 Vue 组件

```
web_src/js/components/datahub/
├── DatasetHome.vue             # 数据集主页（统计卡片 + 文件树）
├── FileTree.vue                # 文件树浏览器
├── JsonlViewer.vue             # JSONL 文件查看器
├── JsonlRowRenderer.vue        # 单行 JSON 结构化渲染
├── DiffView.vue                # Diff 顶层容器
├── DiffFileSummary.vue         # 文件变更摘要
├── DiffFileDetail.vue          # 单文件三段折叠 diff
├── DiffSection.vue             # 折叠段（deleted/added/refreshed）
├── DiffRow.vue                 # 单行变更渲染
├── DiffRowRefreshed.vue        # 刷新行 old/new 并排
├── RowComment.vue              # 行级评论线程
├── BranchSelector.vue          # 分支/tag 选择器（复用 Forgejo 数据源适配）
├── ConflictResolver.vue        # 冲突解决 UI
└── shared/
    ├── Pagination.vue          # 分页控件
    ├── api.ts                  # datahub 代理 API 封装
    └── types.ts                # TypeScript 类型定义
```

---

## 9. 部署架构

### 9.1 Docker Compose 部署

```yaml
services:
  datahub-gateway:
    image: datahub-gateway:latest    # Forgejo fork 镜像
    ports:
      - "3000:3000"
    environment:
      - DATAHUB__CORE_URL=http://datahub-core:8000/api/v1
      - DATAHUB__SERVICE_TOKEN=${SERVICE_TOKEN}
    volumes:
      - forgejo-data:/data
    depends_on:
      - postgres
      - datahub-core

  datahub-core:
    image: datahub-core:latest
    expose:
      - "8000"                        # 只内网可达，不对外
    environment:
      - DATABASE_URL=postgresql+asyncpg://...
      - DATA_DIR=/data/datahub
      - SERVICE_TOKEN=${SERVICE_TOKEN}
    volumes:
      - datahub-objects:/data/datahub

  postgres:
    image: postgres:16
    volumes:
      - pg-data:/var/lib/postgresql/data
```

### 9.2 开发环境

- Fork Forgejo 源码到独立 repo（如 `datahub-gateway`）
- Go 1.22+ / Node.js 20+ / Python 3.12+
- `make watch` 启动 Forgejo 开发服务器（hot reload Go templates + Vue）
- datahub-core 用 `uv run uvicorn` 启动
- 共享同一个 PostgreSQL 实例

---

## 10. 不做什么（Phase 3 范围外）

- sidecar 元数据查看/编辑（Phase 4）
- 行级搜索（Phase 4）
- 统计面板 / token 分布图表（Phase 4）
- 导出任务面板（Phase 4）
- Blame 视图（Phase 5）
- CI bridge 集成（Phase 4）
- 跨仓库 fork（不做）
- 实时协同编辑（不做）
