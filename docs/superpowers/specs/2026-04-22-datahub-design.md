# Dit (dit) — SFT 训练数据版本控制系统设计文档

## 1. 总体定位

针对大模型 SFT 训练数据的 git。CLI 名称 `dit`（data + git），提供 git 风格的版本控制体验，专门优化 JSONL 数据的行级追踪、差异对比与协作管理。

### 1.1 核心目标

- 像 git 管理代码一样管理 JSONL 训练数据，行级 diff 而非文件级
- 支持 20-100 人的中型团队协作，含 PR 评审、权限、分支保护
- 处理百万行 / 百 GB 级数据集，支持 lazy clone
- 元数据不污染 JSONL 内容，sidecar 管理
- 复用 Forgejo 提供 GitHub 风格 Web UI

### 1.2 核心约束

| 维度 | 决定 |
|---|---|
| 部署 | 本地开发 + 公司内网生产（10T 服务器） |
| 存储后端 | 服务器本地文件系统（非 S3） |
| 行级 diff | 内容指纹方案（SHA-256，canonical JSON） |
| Web 前端 | Fork Forgejo 改造 |
| CLI 语言 | Python + uv |
| CI | Webhook + S3 解耦，预留接口 |

### 1.3 不做什么

- 不做实时协同编辑
- 不做数据标注平台（已有独立工具）
- 不做训练调度集成
- 不做 Dolt/DuckDB SQL 查询层（后期可选）
- 不做跨仓库 fork
- 不做 Git 协议兼容（独立协议）
- 不依赖 Dolt

---

## 2. 核心概念与数据模型

### 2.1 对象类型

所有对象内容寻址（SHA-256），不可变。

| 对象 | 说明 | 对应 Git |
|---|---|---|
| **Row** | 一行 JSON 的 canonical 序列化字节 | blob（更细粒度） |
| **Manifest** | 一个 jsonl 文件版本 = 有序 row hash 列表 + 文件元信息 | blob |
| **Tree** | 目录结构：子 manifest/tree 的 hash 映射 | tree |
| **Commit** | 指向 root tree、父 commit、作者、时间、message | commit |
| **Tag/Branch** | 可变引用 → commit hash | ref |
| **RowMeta** | 行的 sidecar 元数据，row hash 为主键 | git notes |

层级关系：

```
Git:         blob  →  tree  →  commit  →  ref
Dit:     row   →  manifest  →  tree  →  commit  →  ref
```

### 2.2 关键设计决定

1. **行 canonical 化**：入库前对 JSON 做 RFC 8785 JSON Canonicalization Scheme，保证语义相同即 hash 相同。
2. **行级去重**：多文件共享同一行只存一份 row 对象。天然支持下采样场景。
3. **manifest 有序**：保留 jsonl 中行顺序。
4. **sidecar 元数据**：不污染 JSONL 内容。通过 row hash 关联，存 PostgreSQL。
5. **query fingerprint 辅助索引**：manifest 中为每行保留 query_fingerprint（role=user 的 content 的 hash）。diff 时若一行删、一行增且 query_fingerprint 相同，标记为"response 刷新"。底层仍是标准删+增。

---

## 3. 存储布局

### 3.1 服务端（10T 服务器）

```
/data/dit/
├── repos/
│   └── <repo-name>/
│       ├── objects/
│       │   ├── rows/<hash[0:2]>/<hash[2:4]>/<hash>    # zstd 压缩
│       │   ├── manifests/<hash[0:2]>/<hash>
│       │   ├── trees/<hash[0:2]>/<hash>
│       │   └── commits/<hash[0:2]>/<hash>
│       ├── refs/
│       │   ├── heads/<branch>
│       │   └── tags/<tag>
│       ├── packs/                    # 可选：冷数据打包
│       └── tmp/                      # push 暂存区
└── config.yaml
```

Row 对象用两级目录分片（`hash[0:2]/hash[2:4]/`），百万行级别下保证每目录文件数可控。

### 3.2 客户端工作区

```
~/data/my-sft-repo/
├── .dit/
│   ├── config              # remote URL、user info
│   ├── HEAD                # 当前 branch ref
│   ├── refs/
│   │   ├── heads/<branch>
│   │   └── remotes/origin/...
│   ├── index               # staging area
│   └── objects/            # 本地对象缓存（LRU，可设上限）
├── feature-impl/
│   └── coding-general.jsonl
└── ...
```

### 3.3 Lazy Clone

| 操作 | 下载内容 | 典型耗时 |
|---|---|---|
| `dit clone` | commit 链 + 顶层 tree | < 1s |
| `dit ls` | 按需展开 tree | < 1s |
| `dit checkout <dir>` | manifests + row 对象 | 取决于数据量 |
| `dit checkout <file>` | 单 manifest + 其 rows | 秒级 |
| `dit diff` | 只需两个 manifest，差异 row 按需下载 | 快 |

未 checkout 的目录显示为占位符（类似 git sparse checkout）。`dit status` 只检测已 checkout 文件。

### 3.4 传输协议

CLI ↔ 服务端通过 HTTP API（dit-core FastAPI）。

```
GET  /api/v1/repos/{repo}/refs/{branch}           → commit hash
GET  /api/v1/repos/{repo}/objects/{type}/{hash}    → 对象内容
POST /api/v1/repos/{repo}/objects/{type}/{hash}    → 上传对象
POST /api/v1/repos/{repo}/refs/{branch}            → CAS 更新 ref
POST /api/v1/repos/{repo}/objects/batch-exists      → 批量存在性检查
```

### 3.5 写入流程

```
本地新增/修改行 → 计算 hash → batch-exists 过滤已存在对象
→ 上传新 row 对象 → 上传 manifest → 上传 tree → 上传 commit
→ CAS 更新远端 ref（old_hash + new_hash，防并发覆盖）
```

每个对象先写 `tmp/<uuid>`，校验 sha256 后 rename 到最终路径（原子操作）。

### 3.6 S3 定位

S3 不是核心存储，是外部集成目标：

```bash
dit export main s3://bucket/sft-data/code/
dit export main ./local-path/
```

PR 合入时可配 webhook 自动触发 export。CI 场景同理。

---

## 4. CLI 设计

CLI 名称 `dit`，命令面贴 git。

### 4.1 仓库初始化与克隆

```bash
dit init
dit clone <repo-url>
dit clone <repo-url> --paths feature-impl/,bug-fix/
dit clone <repo-url> --depth 1
```

### 4.2 工作区操作

```bash
dit status                              # 变更摘要：每文件 +N -M 行
dit status <file>                       # 单文件行级详情
dit ls                                  # 目录树（含未 checkout 占位）
dit checkout <path>                     # 物化目录/文件到工作区
dit fetch <path>                        # 只下载到缓存，不展开
dit sparse-checkout set/add/list <path> # 动态调整稀疏范围
```

### 4.3 变更与提交

```bash
dit add <file>
dit add .                               # 新增 > 1GB 文件时提示确认
dit diff                                # 工作区 vs HEAD
dit diff --staged                       # staging vs HEAD
dit diff <ref1> <ref2> [path]           # 任意两点对比
dit diff --format=json                  # JSON 输出，CI 可解析
dit commit -m "msg"                     # 自动执行 dit validate（pre-commit hook）
dit log [path]
dit show <commit>
dit reset <file>                        # unstage
dit reset --hard                        # 回退工作区
dit restore <file>                      # 恢复到 HEAD
dit rm <file>                           # 删除并 stage
dit stash / dit stash pop
```

### 4.4 Diff 输出格式

```
$ dit diff feature-impl/coding-hard.jsonl

feature-impl/coding-hard.jsonl: 10000 → 9700 lines (-500, +200)

Removed (500):
  L0042  hash=a3b9c1...  query="实现一个 LRU cache"
  L0103  hash=7d2f88...  query="解释 Python GIL"
  ...

Added (200):
  L9501  hash=c4e2a0...  query="实现一个 LFU cache"
  ...

Likely refreshed (320 rows, query unchanged but response differs):
  query_fp=8a3b2c... old_hash=11ab.. → new_hash=99ef..  query: "实现冒泡排序..."
  ...
  (Use --no-refresh-detect to disable)
```

### 4.5 远程同步

```bash
dit fetch
dit pull                                # fetch + rebase
dit push
dit push --force                        # 需特定权限
dit remote add/remove/list
```

### 4.6 分支与合并

```bash
dit branch / dit branch <name>
dit checkout <branch> / dit switch <branch>
dit merge <branch>                      # 三方合并
dit rebase <branch>
dit cherry-pick <commit>
dit tag <name>
dit reflog
```

### 4.7 元数据操作

```bash
dit meta set <file> <line-spec> key=value
dit meta set <file> <line-spec> --from-file labels.json
dit meta get <file> <line-spec>
dit meta query "tag=hard AND author=zhangsan"
dit meta stats <key> --in <path>
dit meta export <file> -o meta.parquet
dit meta import labels.csv --key-col=row_hash --value-cols=tag,difficulty
dit meta migrate --from-ref HEAD~1 --by-query-fp
dit meta snapshot <name>
dit meta restore <name>
```

`<line-spec>` 支持：`L42`、`hash:a3b9c1`、`range:L100-L200`、`query:"实现 LRU"`。

### 4.8 数据分析与校验

```bash
dit stats [path]                        # token 统计、按目录/文件聚合
dit stats --compare <ref1> <ref2>       # 两版本统计对比
dit validate                            # 本地格式/关键词校验（读 .ditvalidate.yaml）
dit sample <file> -n 10                 # 随机采样查看
dit dedup [path]                        # 跨文件行级查重
dit dedup --by-query [path]             # 按 query 查重（发现蒸馏多组）
dit search "关键词" [path]              # 按内容搜索
dit search --field messages[0].content "LRU"
dit history <file> <line-spec>          # 单行完整变更史
dit blame <file> [line-spec]            # 行级追溯 commit 和 author
dit gc                                  # 清理无引用对象
```

### 4.9 导出与 PR

```bash
dit export <ref> <target>               # target: 本地路径或 s3://...
dit export <ref> <target> --incremental --since=<ref>
dit export <ref> <target> --with-meta
dit export <ref> <target> --with-meta --embed

dit pr create <branch> --title "..."
dit pr list
dit pr merge <id>

dit tag <name> --export <target>        # 打 tag 同时触发导出
```

### 4.10 配置文件

```
.ditignore                              # 忽略文件
.ditattributes                          # 文件属性（如跳过行级解析、按二进制处理）
.ditvalidate.yaml                       # 校验规则（dit commit 时自动执行）
```

---

## 5. 服务端架构

### 5.1 组件拆分

```
用户浏览器 ──┐
             │ HTTPS
dit CLI ─────┤
             ▼
       ┌─ nginx (:443) ─────────────────────┐
       │  /api/v1/*  → dit-core (:8000)  │
       │  /*         → forgejo (:3000)        │
       └──┬──────────────────┬───────────────┘
          ▼                  ▼
   dit-core         forgejo (fork)
   (Python FastAPI)     (Go 单二进制)
          │                  │
          ▼                  ▼
     /data/dit/     PostgreSQL (:5432)
     repos/objects/     (共用实例，不同 schema)
```

### 5.2 职责分工

- **dit-core**（Python FastAPI）：对象存储读写、行级 diff/merge/blame、搜索、统计、导出。= "数据层"。无状态，可横向扩展。
- **datahub-gateway**（Forgejo fork）：用户/权限/PR 生命周期/通知/Webhook/Actions。= "协作层"。
- **PostgreSQL**：所有可变、事务性状态（refs、PR、元数据、审计）。
- **文件系统**：不可变 content-addressed 对象。

### 5.3 核心 API

```
# 对象层
GET    /v1/repos/{repo}/objects/{type}/{hash}
HEAD   /v1/repos/{repo}/objects/{type}/{hash}
POST   /v1/repos/{repo}/objects/{type}/{hash}
POST   /v1/repos/{repo}/objects/batch-exists

# 引用层
GET    /v1/repos/{repo}/refs/heads/{branch}
POST   /v1/repos/{repo}/refs/heads/{branch}        # CAS 更新
GET    /v1/repos/{repo}/refs

# 高层查询
GET    /v1/repos/{repo}/tree/{commit}/{path}
GET    /v1/repos/{repo}/manifest/{commit}/{path}
GET    /v1/repos/{repo}/log
GET    /v1/repos/{repo}/blame/{commit}/{path}
POST   /v1/repos/{repo}/diff
POST   /v1/repos/{repo}/merge-preview
POST   /v1/repos/{repo}/merge

# 搜索
POST   /v1/repos/{repo}/search

# sidecar 元数据
GET    /v1/repos/{repo}/meta/rows/{hash}
POST   /v1/repos/{repo}/meta/rows/{hash}
POST   /v1/repos/{repo}/meta/query

# 导出
POST   /v1/repos/{repo}/export
GET    /v1/repos/{repo}/exports/{job_id}
```

---

## 6. 权限模型

### 6.1 角色

| 角色 | 仓库权限 | 分支默认策略 |
|---|---|---|
| **Owner** | 全部，含删除仓库 | 可直推 main |
| **Admin** | 管理成员、设置、权限 | 可直推 main |
| **Maintainer** | Merge PR、创建/删除分支 | 可直推 main，要 review |
| **Committer** | push 到非保护分支、创建 PR | main 必须通过 PR |
| **Reviewer** | 审 PR、评论，不能合入 | - |
| **Reader** | 只读、clone、export | - |

### 6.2 分支保护规则

每个分支可单独配置：
- 必须 PR 合入，不能直推
- 需要 N 个 reviewer approve
- 必须通过 CI（status check pass）
- 特定 reviewer 必审（类似 CODEOWNERS，可配"某分类数据必须某组长审"）
- 不允许 force push
- 合入后自动删除 feature 分支

---

## 7. 三方合并算法

### 7.1 原理

```
Base（共同祖先 commit 的 manifest）
Ours（target branch 当前 manifest）
Theirs（source branch 当前 manifest）
```

对每行 hash 做集合运算：

| Base | Ours | Theirs | 结果 |
|---|---|---|---|
| 有 | 有 | 有 | 保留 |
| 有 | 删 | 有 | 删除（Ours 删的） |
| 有 | 有 | 删 | 删除（Theirs 删的） |
| 有 | 删 | 删 | 删除 |
| 无 | 有 | 无 | 保留（Ours 新增） |
| 无 | 无 | 有 | 保留（Theirs 新增） |
| 无 | 有 | 有 | hash 相同则保留一份 |
| 有 | 改 | 改 | **冲突**（同 query_fp，不同新 response） |

因为最小单元是完整的一行 JSON，合并比 git 文本合并更确定——不会出现"半行合并"。

### 7.2 冲突解决

冲突展示到行级，解决粒度是"按行选边"：

```bash
dit merge feature-branch
# CONFLICT in feature-impl/coding-hard.jsonl
#   L142  added by both branches with different content
#   L207  modified in ours, deleted in theirs
dit mergetool                          # TUI 交互式解冲突
dit merge --continue
```

---

## 8. 元数据系统（Sidecar）

### 8.1 层次

| 层 | 说明 | 存储 |
|---|---|---|
| 仓库级 | 训练框架版本、schema version、全局标签定义 | PostgreSQL repo_meta |
| 文件级 | 文件描述、负责人、所属专项 | PostgreSQL file_meta |
| 行级 | 构建人/流程/时间、质量标签、蒸馏来源、CI 结果 | PostgreSQL row_meta |

### 8.2 数据库 Schema

```sql
CREATE TABLE row_meta (
    repo_id     INT NOT NULL,
    row_hash    CHAR(64) NOT NULL,
    key         VARCHAR(128) NOT NULL,
    value       JSONB NOT NULL,
    updated_by  INT REFERENCES users(id),
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (repo_id, row_hash, key)
);
CREATE INDEX idx_row_meta_kv ON row_meta (repo_id, key, value);

CREATE TABLE file_meta (
    repo_id     INT NOT NULL,
    file_path   TEXT NOT NULL,
    key         VARCHAR(128) NOT NULL,
    value       JSONB NOT NULL,
    updated_by  INT REFERENCES users(id),
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (repo_id, file_path, key)
);

CREATE TABLE repo_meta (
    repo_id     INT NOT NULL,
    key         VARCHAR(128) NOT NULL,
    value       JSONB NOT NULL,
    PRIMARY KEY (repo_id, key)
);

CREATE TABLE meta_audit_log (
    id          BIGSERIAL PRIMARY KEY,
    repo_id     INT NOT NULL,
    row_hash    CHAR(64),
    file_path   TEXT,
    key         VARCHAR(128),
    old_value   JSONB,
    new_value   JSONB,
    changed_by  INT REFERENCES users(id),
    changed_at  TIMESTAMPTZ DEFAULT NOW()
);
```

### 8.3 元数据与版本的关系

元数据**不参与 commit 的 hash 计算**。修改元数据不产生新 commit（同 git notes）。审计通过 meta_audit_log 追溯。

导出时可选附带方式：

```bash
dit export main ./output/                            # 纯 JSONL
dit export main ./output/ --with-meta                 # JSONL + 同名 .meta.jsonl
dit export main ./output/ --with-meta --embed          # 元数据写入 JSONL metadata 字段
```

### 8.4 Response 刷新时的元数据迁移

新行 hash 变了，旧行元数据不自动继承。提供辅助迁移：

```bash
dit meta migrate --from-ref HEAD~1 --by-query-fp
# 找 query_fingerprint 相同的新旧行对
# 复制旧行 meta 到新行，标记 source_hash
# 用户 review 后 confirm
```

---

## 9. PR 评审与 Web UI

### 9.1 Forgejo 改造范围

**直接复用**：用户/组织/团队管理、Issue、Discussion、Wiki、PR 生命周期、Webhook、Actions、通知、OAuth/SSO、API token/SSH key。

**改造替换**：
- 仓库主页：git 文件树 → 数据集树（显示行数、token、最近变更）
- 文件查看：JSONL 分页渲染、messages 数组可折叠、语法高亮
- Diff 视图：调 dit-core diff API，行级变更 + query 刷新识别
- Blame 视图：行级 blame
- Commit 视图：文件级摘要 + 可展开行级变更

**新增**：
- 数据集统计面板（token 分布、长度分布）
- 行级搜索界面
- sidecar 元数据查看/编辑
- 导出任务面板

### 9.2 PR 评审 UI

核心交互：
1. **三段折叠**：删除/新增/刷新分组折叠，避免万行 diff 撑爆页面
2. **行级评论**：每行有评论锚点
3. **JSON 结构化渲染**：messages[] 展开，user/assistant/tool 不同颜色
4. **大变更分页**：单文件 > 1000 行变更时分页加载
5. **Stats 标签页**：行数/token 变化、元数据维度分布变化
6. **Checks 标签页**：CI 状态、失败行深链回 Files 视图

### 9.3 改造策略

1. 新增"数据仓库"类型（`repo.type`），与原 git 仓库共存
2. `type=data` 时走新模板，调 dit-core API
3. PR 复用现有表，新增 `data_pr_meta` 副表
4. Diff 渲染用 Vue 3 独立打包，挂在特定路由

---

## 10. 典型工作流

### 10.1 日常开发（单人）

```bash
dit clone http://server:8000/pangu-sft-code --paths feature-impl/
# 编辑 jsonl
dit status                  # 查看变更
dit validate                # 本地校验
dit add .
dit commit -m "清理低质量数据"
dit push origin feature/zhangsan-w17
dit pr create feature/zhangsan-w17 --title "W17 数据更新"
# Web 上 review → merge
```

### 10.2 Response 刷新

```bash
dit checkout -b refresh/deepseek-r2
# 跑蒸馏脚本，覆盖 jsonl
dit diff                    # 看到 ~7800 refreshed, +200, -200
dit meta migrate --from-ref HEAD~1 --by-query-fp
dit commit -m "deepseek-r2 蒸馏刷新"
dit push → PR → review → merge
```

### 10.3 周版本发布

```bash
dit tag v2026-w17 main
dit export v2026-w17 /data/exports/sft-code-w17/
# 发现异常 → 修复 → 重新 tag
dit tag v2026-w17.1 main
dit export v2026-w17.1 /data/exports/sft-code-w17.1/
```

### 10.4 下采样

```bash
dit checkout -b stage2/w17-sample
# 跑采样脚本
dit dedup stage2/ --against feature-impl/ bug-fix/ general/
# 确认重复率符合预期
dit commit → push → PR → merge
```

### 10.5 多人并行冲突

第一个 PR merge 正常。第二个 PR merge 时三方合并：新增行无冲突直接合入，已删行保持删除，双方修改同行则标记冲突，在 Web 上选边解决。

### 10.6 CI 集成（预留）

PR 创建/更新 → webhook → CI bridge 打包增量到 S3 → 调质检 API → 轮询结果 → 更新 PR status check。接口预留，核心实现只需 CI bridge 骨架 + PR status check 写入。

---

## 11. 技术栈

| 层 | 技术 | 理由 |
|---|---|---|
| dit-core | Python 3.12+ / FastAPI / Uvicorn | 团队熟悉，async IO |
| CLI (dit) | Python / Typer / httpx | 同语言，uv 分发 |
| Web | Forgejo fork (Go / Go template / Vue 3) | 复用 80% 协作功能 |
| 数据库 | PostgreSQL 16 | refs、元数据、审计、PR |
| 对象存储 | 本地文件系统 (ext4/xfs) | 简单可靠 |
| 压缩 | zstd (pyzstd) | 高压缩比、快速解压 |
| JSON 规范化 | RFC 8785 (jcs) | 语义相同 = hash 相同 |
| 包管理 | uv | CLI 分发、开发环境 |
| 部署 | systemd / docker-compose | 生产环境 |
| 反向代理 | nginx | 统一入口、TLS |

---

## 12. 项目分期

### Phase 0：基础骨架（3-4 周）

能跑通本地单机 `dit init / add / commit / log / diff`。

- 对象模型（row / manifest / tree / commit）
- 本地对象存储读写
- CLI 基础命令
- JSON canonical 化 + sha256 行指纹
- 本地 `.dit/` 工作区管理
- 单元测试覆盖核心模型

交付：`uv tool install dit`，本地管理 JSONL 版本。

### Phase 1：远程协作（3-4 周）

多人 clone / push / pull，分支管理。

- HTTP API（对象传输、ref 管理）
- PostgreSQL 接入
- CLI 远程命令
- Lazy clone + sparse checkout
- CAS 并发保护
- API token 认证

交付：团队可共享数据仓库。

### Phase 2：合并与 PR（3-4 周）

三方合并、冲突解决、PR 流程。

- 三方合并算法
- query_fingerprint 刷新检测
- PR 数据模型
- CLI merge/cherry-pick/pr 命令
- Webhook 骨架

交付：完整分支协作流。

### Phase 3：Web UI（6-8 周）

Forgejo 改造上线。

- "数据仓库"类型
- 数据集主页、文件浏览页
- Diff/PR 评审 UI（Vue 3）
- 权限模型接入
- 通知集成

交付：Web 平台可用。

### Phase 4：元数据与高级功能（4-6 周）

- sidecar 元数据全套
- 行级搜索
- stats 统计面板
- export 命令
- CI bridge 骨架

交付：功能完整 1.0。

### Phase 5：打磨与生产化（持续）

性能优化、blame、gc、stash/reflog、dedup、监控、备份、CI 对接。

---

## 13. 风险与应对

| 风险 | 影响 | 应对 |
|---|---|---|
| Forgejo 改造深度超预期 | Phase 3 延期 | 先纯 API + CLI，Web 可后上线 |
| 百万行 diff 性能 | diff/merge 慢 | manifest 是有序 hash 列表，diff = O(n) 集合运算；大 manifest 分块并行 |
| JSON canonical 边界 case | 相同数据不同 hash | 严格 RFC 8785，入库前统一处理 |
| pangu 数据格式变更 | schema 不兼容 | 存储原始 canonical JSON，format 变更只影响 validate 和 UI 渲染 |
| 单服务器单点故障 | 数据丢失 | 对象不可变，rsync 备份；PostgreSQL 标准备份 |
| 10T 空间不足 | 存储满 | gc + pack 压缩；扩盘/NAS |
