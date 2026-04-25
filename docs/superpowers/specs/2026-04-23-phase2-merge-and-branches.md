# Phase 2: 合并与分支管理 — 设计文档

> 基于 [总体设计](2026-04-22-datahub-design.md) 的 Phase 2 细化设计。PR 流程推迟到 Phase 3（随 Forgejo 一起实现）。

---

## 1. Phase 2 目标

在 Phase 0（本地 CLI）+ Phase 1（远程协作）基础上，实现分支管理和三方合并：

- 分支管理 CLI：`branch`、`checkout`、`switch`
- 三方合并算法（merge-base 查找 + manifest 集合运算）
- query_fingerprint 刷新检测（区分"冲突"和"刷新"）
- 冲突检测与标记（写入冲突文件）
- 冲突解决流程：手动编辑 → `dit merge --continue`
- Cherry-pick：单 commit 应用
- Webhook 事件骨架（ref 变更通知）

- 服务端 merge API（merge-preview + merge）
- `dit tag` 命令

**不含**（推迟到后续阶段）：
- PR 数据模型与 CLI（`dit pr create/list/merge`）→ Phase 3 随 Forgejo
- PR review / comment / status check → Phase 3
- Web UI → Phase 3
- `dit rebase`（见 Q8 决策）→ Phase 5
- `dit reflog` → Phase 5
- TUI mergetool → Phase 5

---

## 2. 设计决策记录

### Q1: PR 数据模型放哪里？

**决定：推迟到 Phase 3 Forgejo。**

Phase 2 只做本地 merge 和 cherry-pick。PR 生命周期（创建/评审/合入）与 Forgejo 的用户/通知/权限系统强耦合，拆开做成本高收益低。Phase 2 的 `dit merge` 命令可以在本地或远程场景直接使用，不依赖 PR。

### Q2: 分支管理 CLI 何时做？

**决定：作为 Phase 2 前置任务。**

merge 依赖分支管理，必须先有 `dit branch`、`dit checkout -b`、`dit switch`。RefStore 已经支持 `list_branches()` / `set_branch()` / `get_branch()`，CLI 命令只是封装。

### Q3: 冲突解决方式

**决定：CLI 手动解决。**

冲突写入 `.datahub/MERGE_HEAD`（记录来源 commit）和 `.datahub/MERGE_MSG`（预填 merge message），冲突行写入 `.datahub/conflicts.json`（结构化数据）。用户手动编辑 JSONL 文件选边，然后 `dit add` + `dit merge --continue` 完成。不做 TUI mergetool（Phase 5 可做）。

### Q4: merge-base 算法

**决定：BFS 最近公共祖先。**

两个 commit 的 merge-base = 最近公共祖先。用 BFS 从两个 commit 向上遍历 parent chain，找到第一个公共祖先。对于 linear history（目前最常见），退化为简单线性扫描。

### Q5: 合并粒度

**决定：按 manifest（文件）粒度。**

三方合并以文件为单位：对每个 JSONL 文件，比较 base/ours/theirs 的 manifest。行级操作是集合运算（row_hash 集合的交/差），不需要文本级合并。

### Q6: cherry-pick 实现

**决定：复用三方合并。**

cherry-pick 一个 commit = 以该 commit 的 parent 为 base，该 commit 为 theirs，当前 HEAD 为 ours 做三方合并。与 git cherry-pick 实现原理相同。

### Q7: Webhook 骨架范围

**决定：最小事件系统。**

定义事件类型枚举（ref_update、branch_create、branch_delete），提供注册 webhook URL 的 API 和 DB 表，ref 变更时异步 POST 到注册的 URL。实际 CI 集成留 Phase 4。

Phase 3 引入 Forgejo 后，Forgejo 自带完整 Webhook 系统。Phase 2 的 webhook 表和逻辑届时将被 Forgejo 替代，Phase 2 的 webhook 仅用于内部测试和验证事件模型。

### Q8: rebase 是否在 Phase 2 实现？

**决定：推迟到 Phase 5。**

理由：
1. rebase 需要对 commit chain 进行"重放"——为每个 commit 创建新对象（新 tree hash、新 parent），实现复杂度高
2. 数据版本控制场景下，merge commit 是更自然的协作模型（保留完整合并历史），rebase 的"线性历史"优势不如代码仓库明显
3. Phase 1 的 `dit pull` 当前实现是 fast-forward only（非 fast-forward 报错），这个行为是合理的——远程分支 diverge 时应该手动 merge 而不是自动 rebase
4. Phase 2 优先解决三方合并和冲突解决，这是分支协作的核心路径

影响：`dit pull` 保持 fast-forward only 行为。当本地和远程 diverge 时，用户需要 `dit fetch` + `dit merge origin/<branch>` 手动合并。

### Q9: 服务端 merge API

**决定：Phase 2 实现 merge-preview 和 merge 两个服务端 API。**

- `POST /api/v1/repos/{repo}/merge-preview`：接收 source_branch + target_branch，返回冲突列表和变更摘要（不实际修改任何 ref）
- `POST /api/v1/repos/{repo}/merge`：执行 merge，创建 merge commit，更新 target branch ref

这两个 API 复用 `dit.core.merge` 模块（CLI 和 server 共享核心逻辑），是 Phase 3 Web UI PR 合入按钮的后端基础。

### Q10: tag 命令

**决定：Phase 2 实现 `dit tag`。**

tag 本质上是轻量级 ref（指向 commit hash 的只读引用），实现简单：
- `dit tag <name>` — 在当前 HEAD 创建 tag
- `dit tag -d <name>` — 删除 tag
- `dit tag` — 列出所有 tag

存储在 `.datahub/refs/tags/<name>`，与 branch 同构但不可变。服务端已有 refs 路由，tag 只需在 ref name 中区分 `heads/` 和 `tags/` 前缀。

---

## 3. 架构概览

### 3.1 新增/修改模块

```
src/dit/core/
├── merge.py          # 新增：三方合并算法
├── merge_base.py     # 新增：merge-base 查找
├── refs.py           # 修改：增加 delete_branch(), tag 支持
├── walker.py         # 已有：is_ancestor() 复用
├── diff.py           # 已有：diff_manifests() 复用
└── objects.py        # 已有

src/dit/cli/
└── main.py           # 修改：新增 branch/checkout/switch/merge/cherry-pick/tag 命令

src/dit/server/
├── models.py         # 修改：新增 Webhook 模型
├── routes/
│   ├── merge.py      # 新增：merge-preview + merge 路由
│   └── webhooks.py   # 新增：webhook CRUD 路由
└── webhooks.py       # 新增：事件触发逻辑
```

### 3.2 命令列表

| 命令 | 说明 |
|---|---|
| `dit branch` | 列出所有分支 |
| `dit branch <name>` | 创建新分支（不切换） |
| `dit branch -d <name>` | 删除分支 |
| `dit checkout -b <name>` | 创建并切换到新分支 |
| `dit checkout <name>` | 切换到已有分支 |
| `dit switch <name>` | 同 checkout（不含 -b） |
| `dit merge <branch>` | 将指定分支合入当前分支 |
| `dit merge --continue` | 冲突解决后继续合并 |
| `dit merge --abort` | 取消合并 |
| `dit cherry-pick <commit>` | 将指定 commit 应用到当前分支 |
| `dit cherry-pick --continue` | 冲突解决后继续 cherry-pick |
| `dit cherry-pick --abort` | 取消 cherry-pick |
| `dit tag` | 列出所有 tag |
| `dit tag <name>` | 在当前 HEAD 创建 tag |
| `dit tag -d <name>` | 删除 tag |

---

## 4. 分支管理

### 4.1 RefStore 扩展

现有 `RefStore` 已支持：
- `get_branch(name)` / `set_branch(name, hash)` / `list_branches()`
- `current_branch()` / `resolve_head()` / `get_head()`
- HEAD 格式：`ref:<branch_name>\n`

新增：
- `delete_branch(name) -> bool`：删除 `refs/heads/<name>` 文件

### 4.2 checkout 行为

`dit checkout <branch>` 切换分支：
1. 检查 staging area 为空（否则报错"please commit or reset first"）
2. 检查目标分支存在
3. 读取目标分支的 commit → tree → manifests
4. 对比当前 HEAD tree 和目标 tree：只对 manifest hash 不同的文件调用 `materialize_file()`（跳过未变更文件，对大数据集性能关键）
5. 删除目标 tree 中不存在但当前工作区存在的 JSONL 文件
6. 更新 HEAD 为 `ref:<branch>`

`dit checkout -b <name>` 创建并切换：
1. 检查目标分支不存在（否则报错）
2. `set_branch(name, current_head_commit)`
3. 更新 HEAD 为 `ref:<name>`
4. 工作区不变

### 4.3 安全检查

切换分支前检查工作区是否有未提交的变更：
- 对比当前 JSONL 文件的 manifest hash 与 HEAD tree 中记录的 hash
- 如果有差异，拒绝切换并提示 "error: working directory has uncommitted changes"

---

## 5. 三方合并算法

### 5.1 merge-base 查找

```python
def find_merge_base(store: ObjectStore, hash_a: str, hash_b: str) -> str | None:
```

算法：BFS 交替扩展两个 commit 的祖先集合，第一个出现在两个集合中的 commit 即为 merge-base。

特殊情况：
- 如果 A 是 B 的祖先 → merge-base = A（fast-forward 场景）
- 如果 B 是 A 的祖先 → merge-base = B（already up to date）
- 无公共祖先 → 返回 None（不相关的历史）

### 5.2 文件级三方比较

给定三个 commit 的 tree（base/ours/theirs），按文件名对齐：

| Base | Ours | Theirs | 处理 |
|---|---|---|---|
| 有 | 有(同) | 有(同) | 保留不变 |
| 有 | 有(改) | 有(同) | 取 Ours |
| 有 | 有(同) | 有(改) | 取 Theirs |
| 有 | 有(改) | 有(改) | → 行级三方合并 |
| 有 | 删 | 有(同) | 删除 |
| 有 | 有(同) | 删 | 删除 |
| 有 | 删 | 删 | 删除 |
| 有 | 删 | 有(改) | **冲突**：Ours 删了但 Theirs 修改了 |
| 有 | 有(改) | 删 | **冲突**：Theirs 删了但 Ours 修改了 |
| 无 | 有 | 无 | 取 Ours（新文件） |
| 无 | 无 | 有 | 取 Theirs（新文件） |
| 无 | 有 | 有(同) | 保留一份 |
| 无 | 有 | 有(不同) | **冲突**：双方新增了同名但不同内容的文件 |

"改" = manifest hash 不同于 base 的。

### 5.3 行级三方合并（核心）

当一个文件在 base/ours/theirs 三份 manifest 都存在且 ours ≠ theirs ≠ base 时，进入行级合并。

```python
@dataclass
class MergeResult:
    merged_entries: list[ManifestEntry]  # 无冲突的合并结果
    conflicts: list[MergeConflict]       # 冲突列表

@dataclass
class MergeConflict:
    file_path: str
    conflict_type: str       # "both_modified" | "modify_delete"
    base_entry: ManifestEntry | None
    ours_entry: ManifestEntry | None
    theirs_entry: ManifestEntry | None
    query_fingerprint: str | None
```

行级合并逻辑（对单个文件的三份 manifest）：

1. **构建索引**：
   - `base_hashes: set[str]` = base manifest 的 row_hash 集合
   - `ours_hashes: set[str]` = ours manifest 的 row_hash 集合
   - `theirs_hashes: set[str]` = theirs manifest 的 row_hash 集合
   - `base_by_qfp: dict[str, ManifestEntry]` = base 按 query_fingerprint 索引
   - `ours_by_qfp: dict[str, ManifestEntry]` = ours 按 query_fingerprint 索引
   - `theirs_by_qfp: dict[str, ManifestEntry]` = theirs 按 query_fingerprint 索引

2. **集合运算**（对每个 base row）：
   - base有, ours有, theirs有 → 保留（三方一致或未变）
   - base有, ours删(不在 ours_hashes), theirs有 → 删除
   - base有, ours有, theirs删 → 删除
   - base有, ours删, theirs删 → 删除

3. **刷新检测**（对每个 base row 的 query_fingerprint）：
   - base 中的行在 ours 中消失了，但 ours 中有一个 **不同 row_hash 但相同 query_fingerprint** 的行 → ours 做了 response 刷新
   - 同理对 theirs
   - 如果 ours 和 theirs 都对同一个 query_fingerprint 做了刷新，且新 row_hash 不同 → **冲突**（both_modified）
   - 如果只有一边刷新 → 取刷新方

4. **新增行**：
   - ours 中有但 base 中无（且不是刷新产物） → ours 新增
   - theirs 中有但 base 中无 → theirs 新增
   - 两边新增了 row_hash 相同的行 → 保留一份（无冲突）

5. **合并输出与行顺序**：
   - `merged_entries` = 保留的行 + 无冲突的新增/刷新行
   - `conflicts` = 冲突列表
   - 行顺序算法：
     1. 以 ours manifest 的行序为骨架遍历
     2. 对 ours 中的每一行：如果该行在合并结果中保留（未被删除/冲突），保持其在 ours 中的原始位置
     3. 对 ours 中刷新过的行：新 row_hash 占据原行位置
     4. theirs 独有的新增行（base 无、ours 无）按 theirs 中的原始顺序追加到末尾
     5. 被删除的行（ours 或 theirs 删的）从结果中移除，不留占位
   - 这保证了：ours 修改不改变行序，theirs 新增追加到尾部，结果可预测

### 5.4 API

```python
# src/dit/core/merge.py

def three_way_merge(
    store: ObjectStore,
    base_hash: str | None,     # base commit hash (None for unrelated histories)
    ours_hash: str,             # target branch commit hash
    theirs_hash: str,           # source branch commit hash
) -> MergeResult:
    """Perform three-way merge at tree level, delegating to manifest-level merge per file."""

def merge_manifests(
    base: Manifest | None,
    ours: Manifest,
    theirs: Manifest,
    file_path: str,
) -> tuple[list[ManifestEntry], list[MergeConflict]]:
    """Merge two manifests using a common base. Returns (merged_entries, conflicts)."""
```

---

## 6. 冲突表示与解决

### 6.1 冲突状态文件

合并产生冲突时，写入以下文件到 `.datahub/`：

| 文件 | 内容 |
|---|---|
| `MERGE_HEAD` | theirs commit hash（正在合并的来源 commit） |
| `MERGE_MSG` | 预填的 merge commit message |
| `conflicts.json` | 冲突详情（结构化 JSON） |

`conflicts.json` 格式：
```json
{
  "base_commit": "abc123...",
  "ours_commit": "def456...",
  "theirs_commit": "789abc...",
  "conflicts": [
    {
      "file_path": "feature-impl/coding-hard.jsonl",
      "conflict_type": "both_modified",
      "query_fingerprint": "8a3b2c...",
      "ours_row_hash": "11ab...",
      "theirs_row_hash": "99ef...",
      "base_row_hash": "55cd..."
    },
    {
      "file_path": "bug-fix/v2.jsonl",
      "conflict_type": "modify_delete",
      "ours_row_hash": null,
      "theirs_row_hash": "77ff...",
      "base_row_hash": "33aa..."
    }
  ]
}
```

### 6.2 工作区状态

冲突时，对每个有冲突的文件：
- 无冲突的行照常写入 JSONL
- 冲突行**取 ours 版本**写入 JSONL（默认保留当前分支）
- `conflicts.json` 记录所有冲突详情

用户解决冲突流程：
1. `dit merge feature-branch` → 输出冲突文件列表
2. 查看 `.datahub/conflicts.json` 了解冲突详情
3. 手动编辑 JSONL 文件（替换/删除冲突行）
4. `dit add <files>` 标记解决
5. `dit merge --continue` 完成合并（创建 merge commit）

### 6.3 merge --abort

清除合并状态，还原工作区到合并前：
1. 读取 `ours_commit` 从 conflicts.json
2. 用 ours_commit 的 tree 重新 materialize 工作区
3. 删除 `MERGE_HEAD`、`MERGE_MSG`、`conflicts.json`

### 6.4 merge --continue

1. 检查 `MERGE_HEAD` 存在（正在合并）
2. 检查 staging area 非空（用户已 add 解决后的文件）
3. 正常 commit 流程，但 parent_hashes = [ours_hash, theirs_hash]（merge commit 有两个 parent）
4. 使用 `MERGE_MSG` 作为默认 message（可用 -m 覆盖）
5. 清除 `MERGE_HEAD`、`MERGE_MSG`、`conflicts.json`

---

## 7. dit merge 完整流程

```
dit merge <source-branch>
```

1. **前置检查**：
   - 当前分支不能是 source-branch
   - staging area 必须为空
   - 不能已在 merge 进行中（MERGE_HEAD 不存在）

2. **读取 commit**：
   - ours_hash = 当前分支的 HEAD commit
   - theirs_hash = source-branch 的 HEAD commit

3. **查找 merge-base**：
   - base_hash = find_merge_base(store, ours_hash, theirs_hash)

4. **Fast-forward 检测**：
   - 如果 ours_hash == base_hash → fast-forward：直接把当前分支指向 theirs_hash，materialize 工作区
   - 如果 theirs_hash == base_hash → already up to date，无需操作

5. **三方合并**：
   - 调用 three_way_merge(store, base_hash, ours_hash, theirs_hash)
   - 得到 merged tree entries + conflicts

6. **无冲突**：
   - 将 merged 的 manifests/rows 写入 store
   - 构建新 tree → 新 commit（parent = [ours_hash, theirs_hash]）
   - 更新当前分支 ref
   - Materialize 工作区

7. **有冲突**：
   - 将无冲突部分写入 store + 工作区
   - 冲突行取 ours 版本写入工作区
   - 写入 MERGE_HEAD、MERGE_MSG、conflicts.json
   - 输出冲突列表，提示用户解决

---

## 8. Cherry-pick

```
dit cherry-pick <commit-hash>
```

1. 读取目标 commit 和其第一个 parent
2. 三方合并：base = parent, ours = HEAD, theirs = target commit
3. 如果无冲突，创建新 commit（单 parent = 当前 HEAD，message 复用原 commit message，前缀 "cherry-pick: "）
4. 如果有冲突，进入冲突状态

### 8.1 冲突状态文件

Cherry-pick 使用与 merge 互斥的状态文件：

| 文件 | 内容 |
|---|---|
| `CHERRY_PICK_HEAD` | 被 cherry-pick 的 commit hash |
| `MERGE_MSG` | 预填 message（复用同一文件，与 merge 共享） |
| `conflicts.json` | 冲突详情（格式同 merge） |

互斥检查：`MERGE_HEAD` 和 `CHERRY_PICK_HEAD` 不能同时存在。merge 和 cherry-pick 命令启动前都要检查。

### 8.2 cherry-pick --continue

1. 检查 `CHERRY_PICK_HEAD` 存在
2. 检查 staging area 非空
3. 创建 commit：parent_hashes = [当前 HEAD]（单 parent，不是 merge commit）
4. 使用 `MERGE_MSG` 作为 message（可用 -m 覆盖）
5. 清除 `CHERRY_PICK_HEAD`、`MERGE_MSG`、`conflicts.json`

### 8.3 cherry-pick --abort

1. 读取 conflicts.json 中的 ours_commit
2. 用 ours_commit 的 tree 重新 materialize 工作区
3. 清除 `CHERRY_PICK_HEAD`、`MERGE_MSG`、`conflicts.json`

---

## 9. 服务端 Merge API

### 9.1 merge-preview

```
POST /api/v1/repos/{repo}/merge-preview
```

请求体：
```json
{
  "source_branch": "feature/zhangsan-w17",
  "target_branch": "main"
}
```

响应（200）：
```json
{
  "mergeable": true,
  "merge_base": "abc123...",
  "files_changed": 3,
  "rows_added": 200,
  "rows_removed": 50,
  "rows_refreshed": 320,
  "conflicts": []
}
```

或有冲突（200，`mergeable: false`）：
```json
{
  "mergeable": false,
  "merge_base": "abc123...",
  "conflicts": [
    {
      "file_path": "feature-impl/coding-hard.jsonl",
      "conflict_type": "both_modified",
      "query_fingerprint": "8a3b2c..."
    }
  ]
}
```

实现：调用 `dit.core.merge.three_way_merge()` 做 dry-run，不写入任何对象或 ref。

### 9.2 merge

```
POST /api/v1/repos/{repo}/merge
```

请求体：
```json
{
  "source_branch": "feature/zhangsan-w17",
  "target_branch": "main",
  "message": "Merge feature/zhangsan-w17 into main",
  "author": "zhangsan"
}
```

响应（200）：
```json
{
  "commit_hash": "def456...",
  "fast_forward": false
}
```

错误：
- 409 Conflict：有行级冲突，无法自动合并（返回冲突列表）
- 404：source 或 target branch 不存在
- 409：target branch ref 被并发更新（CAS 失败）

实现：
1. 调用 `three_way_merge()` 执行合并
2. 如有冲突，返回 409 + 冲突列表（服务端不支持交互式解决，强制无冲突才能 merge）
3. 无冲突时写入 merged objects，创建 merge commit，CAS 更新 target branch ref
4. 触发 `ref_update` webhook 事件

---

## 10. Tag

### 10.1 存储

Tag 存储在 `.datahub/refs/tags/<name>`，文件内容为 commit hash。与 branch 结构完全一致，仅路径前缀不同。

### 10.2 RefStore 扩展

```python
# refs.py 新增方法
def get_tag(self, name: str) -> str | None
def set_tag(self, name: str, commit_hash: str) -> None
def delete_tag(self, name: str) -> bool
def list_tags(self) -> dict[str, str]
```

tags_dir = `.datahub/refs/tags/`

### 10.3 CLI

- `dit tag`：列出所有 tag（名称 + commit hash 前 8 位）
- `dit tag <name>`：在当前 HEAD 创建 tag。如果 tag 已存在，报错。
- `dit tag -d <name>`：删除 tag。不存在则报错。

### 10.4 服务端

服务端已有 refs 路由（`/api/v1/repos/{repo}/refs/heads/{branch}`）。tag 复用同一路由结构：
- `GET /api/v1/repos/{repo}/refs/tags/{name}` — 获取 tag
- `POST /api/v1/repos/{repo}/refs/tags/{name}` — 创建 tag（不需要 CAS，创建即 immutable）
- `DELETE /api/v1/repos/{repo}/refs/tags/{name}` — 删除 tag

---

## 11. Webhook 事件骨架

### 9.1 事件类型

```python
class WebhookEvent(str, Enum):
    REF_UPDATE = "ref_update"
    BRANCH_CREATE = "branch_create"
    BRANCH_DELETE = "branch_delete"
```

### 9.2 数据库模型

```python
class Webhook(Base):
    __tablename__ = "webhooks"
    __table_args__ = {"schema": "datahub"}

    id: int                    # PK
    repo_id: int               # FK -> repos.id
    url: str                   # POST target URL
    secret: str                # HMAC secret for signature
    events: str                # comma-separated event types, e.g. "ref_update,branch_create"
    active: bool               # enable/disable
    created_at: datetime
```

### 9.3 API 路由

```
POST   /api/v1/repos/{repo}/webhooks        # 创建 webhook
GET    /api/v1/repos/{repo}/webhooks        # 列出 webhooks
DELETE /api/v1/repos/{repo}/webhooks/{id}   # 删除 webhook
```

### 9.4 触发时机

- refs 路由 CAS 更新成功后 → `ref_update` 事件
- 新增 branch 创建/删除 API 路由 → 对应事件

### 9.5 Payload 格式

```json
{
  "event": "ref_update",
  "repo": "sft-code",
  "ref": "heads/main",
  "old_hash": "abc123...",
  "new_hash": "def456...",
  "timestamp": "2026-04-23T10:30:00Z"
}
```

发送方式：异步 POST（fire-and-forget），带 HMAC-SHA256 签名 header `X-DataHub-Signature`。

发送失败不重试，事件可能丢失。这是骨架级实现，Phase 3 Forgejo 上线后将被 Forgejo 的 Webhook 系统替代（Forgejo 自带重试、delivery log 等完整功能）。Phase 2 的 webhook 仅用于验证事件模型和内部测试。

---

## 12. 文件结构总览

### 新增文件

| 文件 | 职责 |
|---|---|
| `src/dit/core/merge.py` | 三方合并算法：merge_manifests() + three_way_merge() |
| `src/dit/core/merge_base.py` | merge-base 查找算法 |
| `src/dit/server/routes/merge.py` | merge-preview + merge API 路由 |
| `src/dit/server/routes/webhooks.py` | webhook CRUD 路由 |
| `src/dit/server/webhooks.py` | webhook 事件触发逻辑 |
| `src/dit/server/alembic/versions/002_webhooks.py` | webhook 表迁移 |
| `tests/test_merge.py` | 三方合并单元测试 |
| `tests/test_merge_base.py` | merge-base 算法测试 |
| `tests/test_cli_branch.py` | branch/checkout/switch CLI 测试 |
| `tests/test_cli_merge.py` | merge CLI 集成测试 |
| `tests/test_cli_cherry_pick.py` | cherry-pick CLI 测试 |
| `tests/test_cli_tag.py` | tag CLI 测试 |
| `tests/server/test_routes_merge.py` | merge API 路由测试 |
| `tests/server/test_routes_webhooks.py` | webhook 路由测试 |

### 修改文件

| 文件 | 变更 |
|---|---|
| `src/dit/core/refs.py` | 新增 delete_branch(), tag 方法 (get/set/delete/list_tags) |
| `src/dit/cli/main.py` | 新增 branch/checkout/switch/merge/cherry-pick/tag 命令 |
| `src/dit/server/models.py` | 新增 Webhook 模型 |
| `src/dit/server/app.py` | 注册 merge_router, webhooks_router |
| `src/dit/server/routes/refs.py` | CAS 更新后触发 webhook 事件；新增 tag 路由 |

---

## 13. 依赖

无新的外部依赖。所有功能使用现有 Python 标准库 + 已有依赖（typer、httpx、sqlalchemy、pydantic-settings 等）。

Webhook 异步 POST 使用 httpx.AsyncClient（服务端已有 httpx 依赖）。

---

## 14. 测试策略

- **merge_base.py**：纯算法测试，构造各种 commit DAG 验证（linear、diamond、criss-cross、no common ancestor）
- **merge.py**：单元测试覆盖 §5.3 中的所有行级合并场景（8 种 case + 刷新检测 + 行顺序验证）
- **CLI branch/checkout/switch**：使用 CliRunner 测试，验证 HEAD 切换、工作区 materialize、dirty check
- **CLI merge**：集成测试，构造分支分叉场景，验证 fast-forward、无冲突三方合并、冲突检测 + continue + abort
- **CLI cherry-pick**：集成测试，验证无冲突 cherry-pick、冲突 cherry-pick + continue + abort
- **CLI tag**：CliRunner 测试，验证 create/list/delete
- **server merge routes**：使用 conftest.py async client fixture，测试 merge-preview（含冲突和无冲突）、merge（成功、409 冲突、CAS 失败）
- **server tag routes**：创建/获取/删除 tag
- **webhook routes**：使用现有 conftest.py 的 async client fixture 测试 CRUD
- **webhook trigger**：mock httpx.AsyncClient 验证事件发送
