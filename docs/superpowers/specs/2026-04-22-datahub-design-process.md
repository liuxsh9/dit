# Dit (dit) 设计过程完整记录

> 本文档由脚本从 Claude Code 会话历史中自动提取，保留了设计过程中的完整对话内容。

---

## 用户 (Turn 1)

根据需求开始完整设计： @docs/requirements.md

---

### 设计师回复

项目目前只有 `docs/requirements.md`，是一个全新项目。需求文档我已经仔细读过了。

---

### 设计师回复

这次设计涉及不少架构图、数据流、UI 布局对比，可能需要可视化支持。

我们接下来要设计的内容会包含一些可视化的部分（架构图、数据流、Web UI 的 PR 评审界面 mockup 等）。我可以在浏览器里实时给你画出来，做并排对比。这个功能比较新，会消耗较多 token。要试试吗？（需要打开本地 URL）

---

## 用户 (Turn 2)

先不用，尽量复用一些成熟项目的前端方案和可视化设计

---

### 设计师回复

好的，跳过可视化，复用成熟项目。开始逐个澄清关键问题。

先做一些竞品调研，再来问你关键决策问题。

---

### 设计师回复

调研完了。要点：Dolt 有真正的行级 diff 但本质是数据库，100GB+ 规模有挑战；LakeFS 只能做文件级 diff；HuggingFace 也是文件级。**没有现成方案能完美满足你的需求**——需要混合方案或自研行级 diff 层。

先问几个最关键的约束。

**问题 1/N：部署环境与基础设施**

这决定了哪些方案根本不可行（比如能不能依赖云原生组件）。你们的部署环境是？

---

### 设计师回复

明白，本地开发 + 内部生产，需要兼顾两端。

**问题 2/N：数据存储现状**

---

### 设计师回复

S3 兼容对象存储，很好，LakeFS / 对象存储后端都可以用。

**问题 3/N：行级 diff 的核心机制**

这是最核心的技术决策。按需求描述，10000 → 9500 行的变更要精确定位到哪 500 行被删。而且还有"query 相同、response 刷新"的场景。有几种实现路径：

---

### 设计师回复

方案 A，最接近 Git 哲学。简洁，query+response 刷新看起来是删+增，但可以在展示层用启发式匹配做"疑似更新"的提示。

**问题 4/N：前端方案**

---

### 设计师回复

Gitea/Forgejo fork，好选择。Go 单二进制、代码简洁、改造成本低。

**问题 5/N：用户规模与团队协作**

---

### 设计师回复

**问题 6/N：CLI 实现语言**

---

## 用户 (Turn 3)

稍等，CLI 的问题我们再讨论下

---

### 设计师回复

好，CLI 这块再展开。

我提出 Go 是因为想和 Gitea 同栈、单二进制好分发、性能好。但有几个值得讨论的点：

1. **客户端环境**：用户跑 CLI 的机器是 Linux 训练机？还是个人 Mac？如果是异构，单二进制确实重要。
2. **算法库依赖**：行级 diff 的 hash 计算、JSON canonicalize 都很简单，无所谓语言。但如果要做"疑似 response 刷新"的相似度匹配（比如 query 哈希聚类），Python 生态更现成。
3. **未来扩展**：如果想做 `dh stats`（token 统计、字段分布等数据分析），Python + pandas/datasets 库更顺手。
4. **团队习惯**：你们做 LLM 训练，团队大概率以 Python 为主，Go 维护者可能少。

所以可能的组合：
- **A. 纯 Go**：和 Gitea 同栈，分发简单，但团队学习成本高
- **B. 纯 Python**：团队顺手，但分发要 pip + venv，单文件要 PyInstaller
- **C. Go 核心 + Python SDK**：Go 做 `dh clone/add/commit/push` 这些核心 VCS 操作（性能敏感），Python 提供 `dit` SDK 给数据分析/CI 脚本调用 HTTP API

你怎么看？或者还有别的考量？

---

## 用户 (Turn 4)

还是 python 吧，大家熟悉一些，用 uv 管理环境

---

### 设计师回复

好，Python + uv 管理。CLI 用 Click/Typer，分发用 `uv tool install`，工程化也现代。

继续。

**问题 7/N：CI/CD 集成方式**

需求里提到 CI 会校验格式、关键词、闭合性等，merge 前必须通过。

---

### 设计师回复

明白，CI 是异步 S3 解耦模式：上传增量 → 触发质检 API → 轮询/回调等结果 → 读 pass/fail 路径。留好接口，不急实现。

关键信息已经收集够了，我来汇总确认然后提出整体方案。

---

### 设计师回复

## 已确认的核心约束汇总

| 维度 | 决定 |
|---|---|
| 部署 | 本地开发 + 公司内网生产 |
| 存储后端 | S3 兼容对象存储 |
| 行级 diff | 方案 A：内容指纹 + 行级存储 |
| Web 前端 | Fork Gitea/Forgejo 改造 |
| 用户规模 | 中型（20-100 人，多团队） |
| CLI 语言 | Python + uv |
| CI | Webhook + S3 解耦，预留接口 |

## 整体架构候选方案（2 选 1）

下面是两个差别较大的整体走向，请你选一个，我们再继续细化。两种都建立在已确认的约束之上。

---

### 方案 X：「轻 Fork + 数据层独立」（推荐）

**核心思路**：Gitea 只改最小必要的部分（UI 入口、PR 评审界面），把所有"数据 git"的核心逻辑放进一个独立的 `dit-core` Python 服务，Gitea 通过插件/API 调用它。

**组件**：
- **dit-core**（Python, FastAPI）：行级对象存储、diff、merge、引用管理。所有数据相关 API。
- **dit-storage**（S3 + PostgreSQL）：
  - S3 存"行对象"（按 sha256 内容寻址，类似 git blob，可压缩去重）
  - S3 存"manifest"（每个文件版本 = 一个有序的行 hash 列表）
  - PostgreSQL 存元数据、引用（branch/tag）、commit 图、PR 状态、权限
- **dit-cli**（Python + Typer）：本地客户端，模仿 git CLI
- **dit-web**：Gitea fork，删除 git 仓库相关页面，新增"数据仓库"页面，diff/PR 评审 UI 调用 dit-core API
- **CI Bridge**：独立小服务，PR 触发时打包增量到 S3 + 调外部 API + 轮询结果

**优势**：
- 数据层完全自主可控，Gitea 升级不阻塞
- Python 单语言（除 Gitea），团队维护友好
- 行级存储实现简单清晰
- 未来想换前端（比如自建）只需替换 web 层

**劣势**：
- Gitea 的 PR/权限/通知系统要"借用"，部分功能需要桥接代码
- 不是真 git，所以 Gitea 的 git 协议、SSH 推拉等不能直接用

---

### 方案 Y：「深度改造 Gitea」

**核心思路**：把 Gitea 的 git 仓库后端替换成自定义的"行级对象库"，让 Gitea 以为它在管理 git，实际下层是 dit。

**组件**：
- 改 Gitea 的 `modules/git`（用 go-git 抽象层），让它读写一个 dit 后端（gRPC 调 dit-core）
- dit-core 同方案 X，但需要实现"伪装成 git 协议"的接口
- CLI 直接用 git？或自定义？

**优势**：
- 一次改造，Gitea 全部功能（PR、权限、SSH、Webhook、Actions）天然可用
- 用户体验和真 git 完全一致

**劣势**：
- 改造深度极大，风险高，Gitea 升级几乎不可能
- "git 但又不是 git"会有大量边界 case
- 工作量翻倍

---

我强烈推荐 **方案 X**：边界清晰、可独立演进、团队能维护。方案 Y 看起来优雅但是个大坑——HuggingFace 当年就是因为深陷 git LFS 改造泥潭才孵化出 XetHub。

你选哪个？

---

## 用户 (Turn 5)

X 吧，另外，是不是就和 dolt 无关了？有使用 dolt 会更好的部分么？

---

### 设计师回复

好问题。在方案 X 下，Dolt 基本用不上了，原因：

1. **行级 diff/merge**：我们自建的内容指纹方案更轻，直接跑在 S3 上，不需要先 import 进数据库
2. **commit 图/分支管理**：PostgreSQL + 自建的 ref 逻辑就够了，Dolt 的 git 语义反而多了一层转换
3. **规模**：Dolt 在 100GB+ JSONL 场景没有被验证过，且需要 JSONL → 表 → JSONL 的往返转换
4. **运维**：多一个有状态的 MySQL 兼容服务器，团队还得学 Dolt 特有的 SQL 扩展语法

**唯一 Dolt 有优势的场景**：如果未来想对历史数据做跨版本 SQL 查询（比如 `SELECT * FROM rows AS OF 'v3.2' WHERE token_count > 4096`），Dolt 天然支持这个。但这个需求可以后期用 DuckDB + 我们的版本快照来替代，不需要提前引入。

结论：**不依赖 Dolt，保持架构简单**。如果将来有"跨版本 SQL 分析"的强需求，可以作为可选的分析层接入，不影响核心设计。

继续推进设计细节？

---

## 用户 (Turn 6)

好 继续

---

### 设计师回复

下面我分 8 个章节逐步展开设计。每节结束我会停下来等你确认，再推进下一节。

---

## §1 核心概念与数据模型

Dit 的核心抽象与 Git 一一对应，但"最小单元"不同：Git 的最小单元是文件内容，Dit 的最小单元是 **JSONL 中的一行**。

**对象类型**（都用内容寻址，SHA-256）：

| 对象 | 说明 | 存储位置 |
|---|---|---|
| **Row** | 一行 JSON 的 canonical 序列化字节。hash = sha256(canonical(row))。行是 Dit 的"atom" | S3 `objects/rows/<hash[0:2]>/<hash>` |
| **Manifest** | 对应一个 jsonl 文件的版本：有序的 row hash 列表 + 文件元信息。hash = sha256(manifest内容) | S3 `objects/manifests/<hash[0:2]>/<hash>` |
| **Tree** | 对应一个目录：子目录名 + 子 manifest/tree 的 hash 映射 | S3 `objects/trees/<hash[0:2]>/<hash>` |
| **Commit** | 指向 root tree、父 commit、作者、时间、message | S3 `objects/commits/<hash[0:2]>/<hash>` |
| **Tag/Branch** | 可变引用 → commit hash | PostgreSQL（不在 S3 上，保证事务） |
| **RowMeta (sidecar)** | 行的"git-note"：额外元数据（构建流程、构建人、标签等），以 row hash 为主键 | PostgreSQL |

**关键设计决定**：

1. **行 canonical 化**：入库前对 JSON 做 RFC 8785 JSON Canonicalization，保证"语义相同即 hash 相同"。浮点和 key 顺序不会带来伪变更。
2. **行级去重**：多个文件共享同一行只存一份 row 对象。天然支持"下采样"场景——一阶段→二阶段采样的数据，不会被重复存储。
3. **manifest 有序**：保留 jsonl 中每行的顺序（对训练 shuffle 前顺序可能有意义）。
4. **sidecar 元数据**：不污染 JSONL 内容。用户定义的标签、构建人、构建流程存在 PostgreSQL 中，通过 row hash 关联。JSONL 导出时保持纯净。
5. **"疑似刷新"识别**：在 manifest 层保留一个 `query_fingerprint` 辅助索引（对 role=user 的 content 做 hash）。diff 展示时，若一行被删、另一行被增，且 query_fingerprint 相同，UI 会标记为"response 刷新"而非"删+增"。底层仍是标准的删+增操作，不破坏 Git 模型的简洁性。

**对比 Git**：
```
Git:         blob  →  tree  →  commit  →  ref
Dit:     row   →  manifest  →  tree  →  commit  →  ref
           (新增一层，因为文件本身是可分解的)
```

这个模型确认后，我再讲 §2 存储布局细节。有疑问吗？

---

## 用户 (Turn 7)

很棒 继续

---

### 设计师回复



## §2 存储布局与 Lazy Clone

**S3 存储目录结构**：

```
s3://<bucket>/<repo-name>/
├── objects/
│   ├── rows/<hash[0:2]>/<hash>          # 行对象（zstd 压缩）
│   ├── manifests/<hash[0:2]>/<hash>     # 文件清单
│   ├── trees/<hash[0:2]>/<hash>         # 目录树
│   └── commits/<hash[0:2]>/<hash>       # 提交对象
├── packs/                                # 可选：冷数据打包（见下文）
│   └── <pack-hash>.pack
└── tmp/                                  # 临时上传区
```

**本地工作区布局**（用户机器上）：

```
~/data/my-sft-repo/
├── .dit/
│   ├── config                   # remote URL、user info
│   ├── HEAD                     # 当前 branch ref
│   ├── refs/
│   │   ├── heads/<branch>       # 本地分支 → commit hash
│   │   └── remotes/origin/...   # 远程分支快照
│   ├── index                    # staging area：文件路径 → manifest hash
│   ├── objects/                 # 本地对象缓存（只缓存用过的）
│   └── rowmeta/                 # 本地元数据缓存
├── feature-impl/
│   ├── coding-general.jsonl     # 工作区文件（用户直接编辑的原始 JSONL）
│   └── coding-hard.jsonl
└── bug-fix/
    └── bugfix-v2.jsonl
```

**Lazy Clone 策略**（解决 100GB+ 不能全量下载的问题）：

```
dh clone <repo>                    # 只拉 commit 图 + root tree → 秒级完成
dh checkout feature-impl/          # 只下载这个目录的 manifest + row 对象
dh checkout bug-fix/bugfix-v2.jsonl  # 只下载单文件
```

具体分级：

| 操作 | 下载内容 | 典型耗时 |
|---|---|---|
| `dh clone` | commit 链 + 顶层 tree（几 KB） | < 1s |
| `dh ls` | 按需展开 tree 对象 | < 1s |
| `dh checkout <dir>` | 该目录的 manifests + 所有 row 对象 | 取决于数据量 |
| `dh checkout <file>` | 单个 manifest + 其 row 对象 | 秒级 |
| `dh diff` | 只需两个 manifest，row 只下载有差异的 | 很快 |

**关键机制**：

1. **按需物化**：工作区中未 checkout 的目录只显示为占位符（类似 git sparse checkout）。`dh status` 只检测已 checkout 文件的变更。
2. **本地对象缓存**：下载的 row 对象缓存在 `.dit/objects/`，再次用到时无需重新下载。可设 LRU 上限（如 10GB），超出自动清理旧对象。
3. **Pack 打包**（后期优化）：对于冷版本，将大量小 row 对象合并成 pack 文件，减少 S3 请求数。实现上参考 git packfile 格式，但更简单——因为 row 之间没有 delta 依赖，直接拼接 + 索引即可。
4. **并发下载**：checkout 大目录时，用 asyncio + aioboto3 并发拉取 row 对象，充分利用带宽。

**写入流程**（push 时）：

```
本地新增/修改的行 → 计算 hash → 仅上传远端不存在的 row 对象（HEAD 请求检查）
→ 上传新 manifest → 上传新 tree → 上传 commit → 更新远端 ref（通过 API，带 CAS 保护）
```

CAS（Compare-And-Swap）保护：push 时检查远端 branch 的 commit hash 是否还是你 pull 时的值，防止并发覆盖。不一致则报冲突，需先 pull。

§2 确认？然后讲 §3 CLI 交互设计。

---

## 用户 (Turn 8)

这部分讨论下。其实，我也不太希望和 s3 绑定。我可以准备一台硬盘 10T 的服务器，服务完全不依赖 s3，就在服务器的某个路径下完成。未来我可能最多会做个触发任务，比如当 pr 被合入时，将main 上传覆盖到 s3 的某个路径，或者随时根据 s3 上所需的集结位置去将 main（也就是完整的一个文件夹，包含所有最新的 jsonl) 上传上去。s3 也会经常被用作 ci 的数据中转地。单核心本质上，系统和 s3 无关，就靠那台服务器即可

---

### 设计师回复

这个改动很好，架构反而更简单了。去掉 S3 依赖后，对象存储就是服务器本地文件系统上的一个目录，和 git 的 `.git/objects` 完全同构。

修订后的 §2：

## §2（修订）存储布局与 Lazy Clone

**服务端存储**（10T 服务器，比如 `/data/dit/`）：

```
/data/dit/
├── repos/
│   └── <repo-name>/
│       ├── objects/
│       │   ├── rows/<hash[0:2]>/<hash[2:4]>/<hash>    # 行对象（zstd 压缩）
│       │   ├── manifests/<hash[0:2]>/<hash>
│       │   ├── trees/<hash[0:2]>/<hash>
│       │   └── commits/<hash[0:2]>/<hash>
│       ├── refs/
│       │   ├── heads/<branch>        # branch → commit hash
│       │   └── tags/<tag>
│       ├── packs/                    # 可选：冷数据打包
│       └── tmp/                      # push 上传暂存区
└── config.yaml                       # 全局配置
```

Row 对象用两级目录分片（`hash[0:2]/hash[2:4]/`），因为百万行级别下单目录文件数可达百万，两级分片保证每个目录 < 256 个子目录 × 256 个文件，文件系统友好。

**本地工作区**（不变）：

```
~/data/my-sft-repo/
├── .dit/
│   ├── config              # remote: http://<server>:8000/api/repos/<name>
│   ├── HEAD
│   ├── refs/
│   ├── index
│   └── objects/            # 本地对象缓存
├── feature-impl/
│   └── coding-general.jsonl
└── ...
```

**传输协议**：

CLI ↔ 服务端通过 HTTP API 通信（dit-core 的 FastAPI），不再走 S3 SDK。

```
GET  /api/repos/{repo}/refs/{branch}           → commit hash
GET  /api/repos/{repo}/objects/{type}/{hash}    → 对象内容
POST /api/repos/{repo}/objects/{type}/{hash}    → 上传对象
POST /api/repos/{repo}/refs/{branch}            → 更新 ref（CAS）
POST /api/repos/{repo}/objects/batch            → 批量检查存在性
```

**Lazy Clone 机制不变**，只是底层从 S3 GET 换成 HTTP GET，逻辑完全一样。大文件 checkout 时 CLI 并发请求多个 row 对象。

**S3 的定位变成"外部集成"**：

```
dh export main s3://bucket/sft-data/code/    # 将 main 分支完整导出为 JSONL 文件夹到 S3
dh export main ./local-path/                  # 也可以导出到本地路径
```

这是一个独立命令，不影响核心存储。PR 合入时可以配 webhook 自动触发 export。CI 场景同理——触发时 export 增量到 S3 某路径，调质检 API，等结果。

**关于容量**：
- 100GB 原始 JSONL，行级去重 + zstd 压缩后预计 30-50GB
- 加上历史版本（内容寻址天然去重，版本间共享大量相同行），10T 足够存数百个版本
- 后期如果空间紧张，可以 gc 清理无引用对象

§2 修订版确认？

---

## 用户 (Turn 9)

确认

---

### 设计师回复



## §3 CLI 交互设计

CLI 名称用 `dh`（短，不和 git 冲突）。命令面尽量贴 git，让用户零迁移成本。

**仓库初始化与克隆**

```bash
dh init                               # 在当前目录建空仓库（一般用不到，仓库由服务端创建）
dh clone <repo-url>                   # 浅克隆：只拉 commit 链 + root tree
dh clone <repo-url> --paths feature-impl/,bug-fix/  # 只拉指定目录
dh clone <repo-url> --depth 1         # 只拉最新一次 commit
```

**工作区操作**

```bash
dh status                  # 已 checkout 文件的变更摘要：每个文件 +N -M 行
dh status feature-impl/coding.jsonl   # 单文件详细：哪些行 hash 是新增/删除
dh ls                      # 列出当前 commit 的目录树（含未 checkout 的占位）
dh ls feature-impl/        # 展开某目录
dh checkout <path>         # 物化目录或文件到工作区
dh fetch <path>            # 同上但只下载到 .dit/objects/，不展开到工作区
```

**变更与提交**

```bash
dh add <file>              # 把工作区变更加入 staging（重新计算行 hash，更新 manifest）
dh add .                   # 全部
dh diff                    # 工作区 vs HEAD
dh diff --staged           # staging vs HEAD
dh diff <commit1> <commit2> [path]   # 任意两点对比
dh commit -m "msg"
dh log [path]
dh show <commit>
```

**Diff 输出样例**（关键差异点）：

```
$ dh diff feature-impl/coding-hard.jsonl

feature-impl/coding-hard.jsonl: 10000 → 9700 lines (-500, +200)

Removed (500):
  L0042  hash=a3b9c1...  query="实现一个 LRU cache"
  L0103  hash=7d2f88...  query="解释 Python GIL"
  ...

Added (200):
  L9501  hash=c4e2a0...  query="实现一个 LFU cache"
  ...

Likely refreshed (320 rows, query unchanged but response differs):
  query_fp=8a3b2c... old_hash=11ab.. → new_hash=99ef..  [user msg: "实现冒泡排序..."]
  query_fp=4f1e9d... old_hash=22cd.. → new_hash=88aa..
  ...
  (Use --no-refresh-detect to disable)
```

这正面回答了需求中"删了几行如何识别"以及"response 刷新如何记录"两个核心痛点。

**远程同步**

```bash
dh fetch                   # 拉远端引用
dh pull                    # fetch + rebase 当前 branch
dh push                    # 推到远端 branch（带 CAS 保护）
dh push --force            # 仅特定权限用户可用
```

**分支与合并**

```bash
dh branch                              # 列分支
dh branch <name>                       # 创建
dh checkout <branch>
dh switch <branch>                     # 同上，git 风格
dh merge <branch>                      # 三方合并
dh rebase <branch>
dh tag v2026-w17                       # 打 tag（用于周版本）
```

**元数据（sidecar）操作**

这是 Dit 独有的命令，对应需求里"元数据不污染 JSONL"。

```bash
dh meta set <file> <line-spec> key=value     # 给指定行加元数据
dh meta set <file> <line-spec> --from-file labels.json
dh meta get <file> <line-spec>
dh meta query "tag=hard AND author=zhangsan" # 跨仓库查
dh meta export <file> -o meta.parquet
```

`<line-spec>` 可以是：
- `L42`：第 42 行（按当前 manifest 顺序）
- `hash:a3b9c1`：行 hash 前缀
- `range:L100-L200`
- `query:"实现 LRU"`：query 全文匹配

**数据分析辅助**

```bash
dh stats                              # 整仓 token 统计、按目录/文件聚合
dh stats feature-impl/                # 单目录
dh validate                           # 跑本地校验（格式、关键词），不需联网
dh sample <file> -n 10                # 随机采样几行查看
```

**导出与外部集成**

```bash
dh export <ref> <target>              # ref 可以是 branch/tag/commit
                                       # target 可以是本地路径或 s3://...
dh export main s3://bucket/sft/code/ --incremental --since=v2026-w16
                                       # 增量导出：只导出从某 ref 以来的变更
```

**冲突解决**

```bash
dh merge feature-branch
# 输出冲突文件 + 行级冲突说明，类似：
# CONFLICT in feature-impl/coding-hard.jsonl
#   L142  added by both branches with different content
#   L207  modified in ours, deleted in theirs
dh mergetool                          # 启动交互式行级 TUI 解冲突
dh merge --continue
```

冲突展示给到行级，但解决粒度是"按行选边"，比 git 文本合并更确定（因为每行是独立 JSON，不会出现部分行合并）。

§3 CLI 设计确认后，§4 讲服务端 API 与权限。

---

## 用户 (Turn 10)

1. 把 dh 换为 dt 如何？毕竟想和 git 更像一些，还有没有更好的建议？ 
2. 命令面尽量贴 git 是非常重要的，你再作为 git 专家和 sft 数据更新管理关键，二次 review 一下还有没有优化点

---

### 设计师回复

两个好问题。

### 1. 命名

`dt` 比 `dh` 好，但我建议 **`dit`**：

- 3 个字母，和 `git` 完全同构，肌肉记忆迁移最自然
- 发音 `/dɪt/`，像 "data + git" 的缩写
- `dit clone`, `dit push`, `dit diff` — 读起来很顺
- 不和常见命令冲突

对比：
| 候选 | 优势 | 劣势 |
|---|---|---|
| `dt` | 短 | 2 字母不像 `git`，发音不直觉 |
| `dit` | 3 字母同构，好记好念 | 无 |
| `dat` | 明确 "data" | 和 Windows `dat` 文件关联，含义泛 |
| `dgit` | 直白 | 4 字母，敲多了 |

### 2. CLI 命令面二次 Review

作为 git 专家 + SFT 数据管理视角重新审视，补充以下缺失和优化：

**缺失的 git 核心命令**：

```bash
dit blame <file> [line-spec]        # 每行追溯到引入它的 commit 和 author
                                     # SFT 场景极重要：这条数据谁加的？什么时候？
dit stash                            # 暂存工作区变更，切分支时用
dit stash pop
dit reset <file>                     # 从 staging 撤回（unstage）
dit reset --hard                     # 回退工作区到 HEAD
dit restore <file>                   # 恢复工作区文件到 HEAD 版本
dit rm <file>                        # 显式删除文件并 stage
dit cherry-pick <commit>             # 从其他分支摘取特定 commit
dit remote add/remove/list           # 远程仓库管理
dit gc                               # 清理无引用对象，回收空间
dit reflog                           # 引用变更历史，误操作恢复
```

**SFT 数据场景独有补充**：

```bash
dit dedup [path]                     # 跨文件查重：发现不同 jsonl 中 hash 相同的行
                                     # 一阶段 → 二阶段下采样时，确认行重复率
dit dedup --by-query [path]          # 按 query_fingerprint 查重：query 相同但 response 不同
                                     # 精确发现"蒸馏多组"的情况

dit search "关键词" [path]            # 按 query/response 内容搜索行
dit search --field messages[0].content "LRU"

dit history <file> <line-spec>       # 单行的完整变更史：何时引入，何时被刷新 response
                                     # 比 blame 更深——blame 只告诉当前版本，history 追溯全链

dit stats --compare v1 v2            # 两个版本间的统计对比：行数、token 分布、分类变化
```

**工作流优化**：

```bash
dit sparse-checkout set <path> ...   # clone 后动态调整稀疏范围（不重新 clone）
dit sparse-checkout add <path>
dit sparse-checkout list

dit tag v2026-w17 --export s3://...  # 打 tag 同时触发导出（周版本场景的快捷方式）

dit pr create <branch> --title "..."  # 直接从 CLI 创建 PR（类似 gh pr create）
dit pr list
dit pr merge <id>
```

**配置文件补充**：

```
.ditignore                            # 忽略文件（如临时分析脚本、.ipynb_checkpoints）
.ditattributes                        # 文件属性（如指定某些文件跳过行级解析，按二进制处理）
.ditvalidate.yaml                     # 本地校验规则（格式检查、关键词黑名单等）
                                      # dit validate 时自动加载
```

**原方案中的修正**：

1. `dit diff` 的输出格式里，"Likely refreshed" 很好但需要一个 `--format=json` 选项，方便 CI 脚本解析
2. `dit validate` 应该在 `dit commit` 时自动执行（pre-commit hook），不通过就拒绝提交，避免"忘了 validate"
3. `dit add .` 需要警告大文件——如果某个 jsonl 是新增且 > 1GB，提示确认

§3 修订版确认后继续 §4？

---

## 用户 (Turn 11)

继续

---

### 设计师回复

## §4 服务端架构、API 与权限

### 4.1 服务端组件拆分

```
┌──────────────────────────────────────────────────┐
│                    Web UI (浏览器)                │
│            Fork: Forgejo (Gitea 的 fork)          │
└───────────────────┬──────────────────────────────┘
                    │ HTTP
┌───────────────────▼──────────────────────────────┐
│              datahub-gateway (Go)                │
│   - 复用 Forgejo 的用户/权限/PR/通知/Webhook      │
│   - 改造: git 仓库页面 → 数据仓库页面            │
│   - 改造: diff 视图 → 调 dit-core 渲染        │
│   - 保留: Issue/Discussion/Wiki/Actions           │
└──────┬─────────────────────────┬─────────────────┘
       │ gRPC/HTTP                │ 
       │                          │ SQL
┌──────▼──────────────────┐  ┌────▼─────────────┐
│  dit-core (Python)   │  │  PostgreSQL       │
│  FastAPI + Uvicorn       │  │  - users/perms    │
│  - 对象读写               │  │  - refs/PRs       │
│  - diff/merge/blame      │  │  - sidecar meta   │
│  - 行级搜索               │  │  - audit log      │
│  - dedup/stats           │  └──────────────────┘
└──────┬──────────────────┘
       │ 文件 IO
┌──────▼──────────────────┐
│  /data/dit/repos/    │
│  (本地文件系统对象库)      │
└─────────────────────────┘
```

**职责分工**：
- **datahub-gateway**（Forgejo fork）：用户/权限/PR 生命周期/通知/Webhook/Actions，= "协作层"
- **dit-core**（Python FastAPI）：对象存储、行级 diff、merge、搜索、统计，= "数据层"
- **PostgreSQL**：所有可变、事务性状态
- **文件系统**：不可变对象（content-addressed）

Gateway 只认"数据仓库 ID + 路径 + ref"这种层级，不关心行级细节；Core 只认对象和路径，不关心用户是谁。两层通过仓库 ID 连接。

### 4.2 核心 API（dit-core）

```
# 对象层（content-addressed，不可变）
GET    /v1/repos/{repo}/objects/{type}/{hash}           获取对象
HEAD   /v1/repos/{repo}/objects/{type}/{hash}           检查存在
POST   /v1/repos/{repo}/objects/{type}/{hash}           上传对象
POST   /v1/repos/{repo}/objects/batch-exists            批量存在性检查（push 前问）

# 引用层（可变，事务性）
GET    /v1/repos/{repo}/refs/heads/{branch}             → commit hash
POST   /v1/repos/{repo}/refs/heads/{branch}             CAS 更新 (old_hash, new_hash)
GET    /v1/repos/{repo}/refs                            列出所有 ref

# 高层查询
GET    /v1/repos/{repo}/tree/{commit}/{path}            列目录
GET    /v1/repos/{repo}/manifest/{commit}/{path}        读 manifest
GET    /v1/repos/{repo}/log?path=&limit=                提交历史
GET    /v1/repos/{repo}/blame/{commit}/{path}           行级 blame
POST   /v1/repos/{repo}/diff                            两点 diff
POST   /v1/repos/{repo}/merge-preview                   合并预览（含冲突）
POST   /v1/repos/{repo}/merge                           执行合并

# 行级搜索
POST   /v1/repos/{repo}/search                          按 query/字段搜索行

# sidecar 元数据
GET    /v1/repos/{repo}/meta/rows/{hash}                行元数据
POST   /v1/repos/{repo}/meta/rows/{hash}                设置
POST   /v1/repos/{repo}/meta/query                      跨行查询

# 导出
POST   /v1/repos/{repo}/export                          导出 ref → 目标（本地/S3）
GET    /v1/repos/{repo}/exports/{job_id}                导出任务状态
```

**对象上传的一致性保护**：
- 上传前先 `batch-exists` 过滤已存在对象
- 每个对象上传走 `PUT /tmp/<uuid>` → 校验 sha256 → `mv` 到最终路径（同文件系统 rename 是原子的）
- 整个 push 是"先传对象 → 最后 CAS 更新 ref"：即使中途失败，对象也不会损坏历史，后续 gc 会清理

### 4.3 权限模型

**对标 GitHub，细化到分支级**：

| 角色 | 仓库权限 | 分支默认策略 |
|---|---|---|
| **Owner** | 全部，含删除仓库 | 可直推 main |
| **Admin** | 管理成员、设置、权限 | 可直推 main |
| **Maintainer** | Merge PR、创建/删除分支 | 可直推 main，要 review |
| **Committer** | push 到非保护分支、创建 PR | main 必须通过 PR |
| **Reviewer** | 审 PR、评论，不能合入 | - |
| **Reader** | 只读、clone、export | - |

**分支保护规则**（每个分支可单独配置）：
- 必须 PR 合入，不能直推
- 需要 N 个 reviewer approve
- 必须通过 CI（status check pass）
- 特定 reviewer 必审（如 `CODEOWNERS` 机制，SFT 里可能是"某分类数据必须由某组长审"）
- 不允许 force push
- 合入后自动删除 feature 分支

**周版本工作流配套**：
- `main` 保护 + reviewer 必审 + CI 必过
- `release/v2026-w17` 保护，只允许 Maintainer+ 推修复
- Tag `v2026-w17.0`、`v2026-w17.1`（修复版）按语义化版本组织

### 4.4 审计与可追溯

PostgreSQL 中维护完整的 `audit_log`：
- 每次 ref 变更（push/merge/branch create）
- 每次权限变更
- 每次 sidecar meta 修改

这解决了需求中"excel 记录"被遗漏的痛点——系统自动记录，不依赖人工。

---

§4 确认？下一节讲 §5 PR 评审与 Web UI 改造。

---

## 用户 (Turn 12)

确认

---

### 设计师回复



## §5 PR 评审与 Web UI 改造

### 5.1 Forgejo 改造范围

Forgejo 自带的可以**直接复用**：
- 用户/组织/团队管理
- 仓库列表、设置、成员管理
- Issue、Discussion、Wiki、Project 看板
- PR 生命周期（创建/评论/审批/合入/关闭）
- Webhook 配置
- Forgejo Actions（CI 编排）
- 通知（邮件、站内、Webhook）
- API token、SSH key 管理
- OAuth（接公司 SSO）

需要**改造或替换**的：
- 仓库主页：从"git 文件树"换成"数据集树"，每个文件显示行数、token 总量、最近变更
- 文件查看：JSONL 不再当文本展示，而是分页渲染每行 JSON（含语法高亮、可折叠 messages 数组）
- Diff 视图：完全替换。调 dit-core 的 diff API，渲染行级变更（含 query 刷新识别）
- Blame 视图：行级 blame
- Commit 视图：显示该 commit 的文件级摘要 + 可展开看行级变更
- Compare 视图：两个 ref 的对比

需要**新增**的：
- 数据集统计面板（token 分布、长度分布、按字段聚合）
- 行级搜索界面
- sidecar 元数据查看/编辑
- 导出任务面板（触发导出 + 查进度 + 下载链接）

### 5.2 Diff/PR 评审 UI 设计

**PR 列表页**：基本沿用 Forgejo 样式，每个 PR 多两列：行变更（+200/-500）、影响文件数。

**PR 详情页**：

```
┌─ PR #42: 二阶段 SFT 数据下采样 (feature-impl/) ──────────┐
│ 状态: Open  作者: zhangsan  reviewers: lisi, wangwu    │
│ ───────────────────────────────────────────────────── │
│ Conversation │ Files Changed (3) │ Stats │ Checks (2) │
│ ───────────────────────────────────────────────────── │
│                                                       │
│ ┌─ feature-impl/coding-hard.jsonl ──────┐            │
│ │ 10000 → 9700 行 (-500, +200, ~320 刷新)│ [展开]   │
│ │ Token: 4.2M → 4.1M                    │            │
│ └────────────────────────────────────────┘            │
│                                                       │
│ ▼ feature-impl/coding-general.jsonl                  │
│ ┌─ 折叠分组：500 行被删除 ──────────[展开列表] ──┐ │
│ │  -L42  hash:a3b9..  query="实现一个 LRU"        │ │
│ │  -L103 hash:7d2f..  query="解释 GIL"            │ │
│ └──────────────────────────────────────────────────┘ │
│ ┌─ 折叠分组：200 行被新增 ──────────[展开列表] ──┐ │
│ ├─ 折叠分组：320 行 response 刷新 ───[展开] ─────┤ │
│ │ ▼ query: "实现冒泡排序"  (query_fp: 8a3b2c..)   │ │
│ │   ┌─ 旧 response (hash:11ab..) ─┐               │ │
│ │   │ def bubble_sort(arr): ...   │               │ │
│ │   └─────────────────────────────┘               │ │
│ │   ┌─ 新 response (hash:99ef..) ─┐  [💬评论]    │ │
│ │   │ def bubble_sort(arr):       │               │ │
│ │   │     """优化版本..."""       │               │ │
│ │   └─────────────────────────────┘               │ │
│ └──────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────┘
```

**关键交互**：

1. **三段折叠**：删除/新增/刷新分组折叠展示，避免万行 diff 撑爆页面
2. **行级评论**：每行都有评论锚点，参考 GitHub PR 行级讨论
3. **JSON 结构化渲染**：每行不是平铺的 JSON 字符串，而是按 messages[] 展开，user/assistant/tool 不同颜色区分
4. **大变更分页**：单文件超过 1000 行变更时分页加载，按需展开
5. **侧边栏**：Files Changed 树，能快速跳到某个文件

**Stats 标签页**：
- 行数变化、token 变化（按 user/assistant 分别统计）
- 元数据维度的分布变化（如：标签 X 的占比从 12% → 18%）
- 这些数据由 dit-core 在 PR 创建时计算并缓存

**Checks 标签页**（CI 集成）：
- 每个 check 显示状态、耗时、log 链接
- 失败时高亮，并展示外部 CI 系统返回的失败行列表（深链回 Files 视图）

### 5.3 文件浏览页

打开一个 jsonl 文件时：

```
┌─ feature-impl/coding-hard.jsonl @ main ──────────────────┐
│ 9700 lines · 4.1M tokens · last updated by lisi 2 days ago│
│ [Raw] [History] [Blame] [Stats] [Download] [Edit Meta]   │
│ ────────────────────────────────────────────────────────│
│ Search: [_______________]   Filter: [tag=hard ▾]        │
│ ────────────────────────────────────────────────────────│
│ L1  ▶ {messages: [user: "实现冒泡排序"...]}  [meta][⋯] │
│ L2  ▶ {messages: [user: "..."]}              [meta][⋯] │
│ L3  ▶ ...                                                │
│ ────────────────────────────────────────────────────────│
│ < 1  2  3 ... 970 >  (100 lines per page)               │
└─────────────────────────────────────────────────────────┘
```

点击行的 `▶` 展开完整 JSON；`[meta]` 弹出 sidecar 编辑器；`[⋯]` 含"复制 hash、查看 blame、行历史"等操作。

### 5.4 数据集主页

```
┌─ pangu-sft-code ─────────────────────────────────────────┐
│ ⭐ 12  👁 38  🔱 4  branches  78 tags                     │
│ ────────────────────────────────────────────────────────│
│ branch: main ▾   [+ New PR]  [Export] [Clone]            │
│ ────────────────────────────────────────────────────────│
│ 📁 feature-impl/        12 files · 1.2M lines · zhangsan│
│ 📁 bug-fix/              8 files · 340K lines  · wangwu │
│ 📁 general/              5 files · 800K lines  · lisi   │
│ 📁 ascendc/              6 files · 220K lines  · chen   │
│ 📄 README.md                                             │
│ 📄 .ditvalidate.yaml                                     │
│ ────────────────────────────────────────────────────────│
│ Latest commit: a3b9c1...  "二阶段下采样 v17"  · 2h ago  │
│                                                          │
│ Total: 31 files · 2.56M lines · 12.3M tokens · 38GB     │
└─────────────────────────────────────────────────────────┘
```

### 5.5 改造工程量评估

Forgejo 是 Go 单体仓库，模板用 Go html/template + 少量 Vue/HTMX。改造策略：

1. **新增"数据仓库"类型**：和原有 git 仓库共存，通过 `repo.type` 区分。原有 git 仓库功能不破坏。
2. **替换路由**：`/[owner]/[repo]/` 在 type=data 时走新模板，调 dit-core API 渲染。
3. **PR 模型借用**：复用 PR 数据库表（id/title/author/state），新增一个 `data_pr_meta` 副表存"源 commit、目标 commit"等数据特有字段。
4. **Diff 渲染**：完全独立的前端模块（建议用 Vue 3 + 独立打包，挂在某个路由下）；服务端 Go 只做"调 core API + 透传"。

工程量估计（不含 dit-core 本身）：
- 仓库类型扩展 + 路由 + 列表页：1-2 周
- 文件查看 + Stats：1 周
- Diff/PR 评审 UI（最重）：3-4 周
- 元数据编辑、搜索：1-2 周
- 总计：约 2-3 个月一个全栈工程师

§5 确认？下一节 §6 数据流与典型工作流。

---

## 用户 (Turn 13)

确认，总体没问题，细节到时候再打磨

---

### 设计师回复



## §6 数据流与典型工作流

### 6.1 日常开发流（单人）

```
zhangsan 负责 feature-impl/ 下的数据

1. dit clone http://server:8000/pangu-sft-code --paths feature-impl/
2. 编辑 feature-impl/coding-hard.jsonl（删了质量差的 500 行，新增 200 行）
3. dit status
     feature-impl/coding-hard.jsonl: -500, +200
4. dit validate
     ✓ 格式检查通过
     ✓ 关键词检查通过
     ⚠ 2 行 token 数超过 32K（警告，非阻断）
5. dit add feature-impl/coding-hard.jsonl
6. dit commit -m "清理低质量数据，补充 hard 难度题"
7. dit push origin feature/zhangsan-w17
8. dit pr create feature/zhangsan-w17 --title "W17 coding-hard 数据更新"
9. → Web 上 reviewer 看 diff、Stats，approve
10. → Maintainer merge → main 更新
```

### 6.2 Response 刷新流（教师模型换代）

```
蒸馏新模型后，需要刷新一批 response

1. dit checkout -b refresh/deepseek-r2
2. 本地脚本跑蒸馏，覆盖 coding-general.jsonl
   （query 不变，response 全换，行数可能微调）
3. dit diff
     coding-general.jsonl: -8000, +8000, ~7800 refreshed
     Likely refreshed (7800 rows, query unchanged):
       query_fp=... old → new
     Net new: 200, Net removed: 200
4. dit commit -m "deepseek-r2 蒸馏刷新 coding-general"
5. dit push → PR → review → merge
```

reviewer 在 PR 页面看到的核心信息：7800 行是 response 刷新（自动折叠），200 行净增，200 行净删。重点审 净增/净删 的部分。

### 6.3 周版本发布流

```
每周四出版本

1. Maintainer: dit tag v2026-w17 main
2. Maintainer: dit export v2026-w17 /data/exports/sft-code-w17/
   → 生成完整 JSONL 文件夹，交给 SFT 主线
3. 发现异常 → 在 main 上修复 → 重新打 tag
   dit tag v2026-w17.1 main
   dit export v2026-w17.1 /data/exports/sft-code-w17.1/
```

也可以把 export 挂在 Webhook 上：打 tag 自动 export 到约定路径。

### 6.4 一阶段 → 二阶段下采样流

```
二阶段训练需要从一阶段数据中下采样

1. dit checkout -b stage2/w17-sample
2. 跑采样脚本：从各 jsonl 按策略抽样，生成新的 stage2/ 目录
3. dit add stage2/
4. dit dedup stage2/ --against feature-impl/ bug-fix/ general/
     stage2/sample.jsonl: 8200/10000 行与一阶段数据重复 ✓
     （确认下采样逻辑正确）
5. dit commit → push → PR → merge
```

### 6.5 多人并行 + 冲突场景

```
zhangsan 和 lisi 同时改 bug-fix/bugfix-v2.jsonl

zhangsan:
  dit checkout -b fix/zhangsan-cleanup
  删除 100 行、新增 50 行
  dit push

lisi:
  dit checkout -b fix/lisi-add
  新增 200 行
  dit push

两个 PR 分别 merge 到 main 时：
- 第一个 merge 正常
- 第二个 merge 时 dit-core 做三方合并：
  - lisi 新增的 200 行：无冲突，直接合入
  - zhangsan 删除的 100 行 lisi 没碰：无冲突，保持删除
  - 如果 lisi 恰好修改了 zhangsan 删除的某行：冲突
    → PR 页面标红冲突行，lisi 在 Web 上选择保留哪边
```

**三方合并原理**：

```
Base (共同祖先 commit 的 manifest)
  ∩
Ours (target branch 当前 manifest)
  ∩
Theirs (source branch 当前 manifest)

对每行 hash:
- Base有, Ours有, Theirs有 → 保留
- Base有, Ours删, Theirs有 → Ours 删了，保持删除
- Base有, Ours有, Theirs删 → Theirs 删了，保持删除
- Base有, Ours删, Theirs删 → 双方都删了，保持删除
- Base无, Ours有, Theirs无 → Ours 新增
- Base无, Ours无, Theirs有 → Theirs 新增
- Base无, Ours有, Theirs有 → 双方同时新增同一行（hash 相同），保留一份
- Base有, Ours改, Theirs改 → 冲突（通过 query_fp 识别：同 query，不同新 response）
```

因为最小单元是"一整行 JSON"（不可分割），合并比 git 文本合并更确定：不会出现 git 那种"半行合并"的混乱。大多数情况下是"行的集合运算"。

### 6.6 CI 集成流（预留）

```
PR 创建/更新时触发 webhook → datahub-gateway 调 CI bridge：

1. CI bridge 拿到 PR 增量行，打包为临时 JSONL
2. 上传到 S3 约定路径 s3://ci-staging/{pr-id}/input/
3. 调用质检 API：POST /api/check {s3_path: "...", callback: "..."}
4. 质检系统异步处理
5. 完成后写结果到 s3://ci-staging/{pr-id}/pass/ 和 /fail/
6. CI bridge 轮询或回调拿到结果
7. 更新 PR status check：pass/fail + 失败行清单
8. PR 页面 Checks 标签展示结果
```

接口预留，核心实现只需 CI bridge 的骨架和 PR status check 的写入。

---

§6 确认？下一节 §7 讲元数据系统细节。

---

## 用户 (Turn 14)

可以

---

### 设计师回复



## §7 元数据系统（Sidecar）

### 7.1 设计原则

核心矛盾：JSONL 的字段格式由 pangu 训练框架定义，不能随意加字段；但数据管理需要丰富的元信息跟着每行走。

解法：**元数据与数据行通过 row hash 关联，存在 PostgreSQL 中，永不写入 JSONL**。

### 7.2 元数据层次

```
┌─────────────────────────────────────────┐
│ 仓库级 (repo_meta)                      │
│   训练框架版本、数据格式 schema version   │
│   全局标签定义、字段枚举值                │
├─────────────────────────────────────────┤
│ 文件级 (file_meta)                      │
│   文件描述、负责人、所属专项              │
│   文件的数据来源说明                      │
├─────────────────────────────────────────┤
│ 行级 (row_meta)    ← 核心               │
│   构建人、构建流程、构建时间              │
│   质量标签（人工标注结果）                │
│   蒸馏来源（教师模型、温度等）            │
│   CI 检查结果、标注轮次                  │
│   自定义 KV（用户自由扩展）              │
└─────────────────────────────────────────┘
```

### 7.3 数据库 Schema

```sql
-- 行级元数据：核心表
CREATE TABLE row_meta (
    repo_id     INT NOT NULL,
    row_hash    CHAR(64) NOT NULL,       -- sha256
    key         VARCHAR(128) NOT NULL,
    value       JSONB NOT NULL,
    updated_by  INT REFERENCES users(id),
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (repo_id, row_hash, key)
);

-- 索引：按 key-value 查询（"找出所有 tag=hard 的行"）
CREATE INDEX idx_row_meta_kv ON row_meta (repo_id, key, value);

-- 文件级元数据
CREATE TABLE file_meta (
    repo_id     INT NOT NULL,
    file_path   TEXT NOT NULL,
    key         VARCHAR(128) NOT NULL,
    value       JSONB NOT NULL,
    updated_by  INT REFERENCES users(id),
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (repo_id, file_path, key)
);

-- 仓库级元数据
CREATE TABLE repo_meta (
    repo_id     INT NOT NULL,
    key         VARCHAR(128) NOT NULL,
    value       JSONB NOT NULL,
    PRIMARY KEY (repo_id, key)
);

-- 元数据变更审计
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

### 7.4 元数据生命周期

**关键问题**：行是内容寻址的，response 刷新后 hash 变了，老 hash 上的元数据怎么办？

```
旧行 hash=11ab..  meta: {author: "zhangsan", tag: "hard", source: "gpt4"}
                          ↓
response 刷新后
新行 hash=99ef..  meta: ???
```

**策略**：

1. **不自动继承**。新行是新内容，元数据不默认搬过来。
2. **CLI 辅助迁移**：
   ```bash
   dit meta migrate --from-ref HEAD~1 --by-query-fp
   # 找到 query_fingerprint 相同的新旧行对
   # 将旧行 meta 复制到新行，并标记 source_hash=11ab..
   # 用户 review 后 confirm
   ```
3. **批量标注工具**：
   ```bash
   dit meta set feature-impl/coding-hard.jsonl --all source="deepseek-r2" batch="refresh-w17"
   dit meta set feature-impl/coding-hard.jsonl --range L1-L200 tag="hard"
   dit meta import labels.csv --key-col=row_hash --value-cols=tag,difficulty
   ```

### 7.5 元数据与版本的关系

元数据**不参与 commit 的 hash 计算**，属于"旁路信息"。这个决定意味着：

- 修改元数据不产生新 commit（和 git notes 一样）
- 元数据有自己的审计日志（meta_audit_log），可追溯
- 导出时可选择是否附带元数据：
  ```bash
  dit export main ./output/                            # 纯 JSONL，给训练用
  dit export main ./output/ --with-meta                 # JSONL + 同名 .meta.jsonl
  dit export main ./output/ --with-meta --embed          # 元数据写入 JSONL 的 metadata 字段
  ```

**为什么不纳入版本控制**：
- 标注是持续进行的，一天可能改几百次标签，每次产生 commit 噪音太大
- 训练只看 JSONL 内容，meta 不影响训练结果，不需要版本快照
- 审计日志已经提供完整的变更历史

**例外**：如果未来有场景需要"冻结某版本的元数据"（如标注验收），可以支持：
```bash
dit meta snapshot v2026-w17          # 将当前元数据状态快照为一个命名版本
dit meta restore v2026-w17           # 恢复到某快照
```
这个作为后期功能预留。

### 7.6 元数据查询场景

```bash
# 找出所有未标注的行
dit meta query "NOT EXISTS tag" --in feature-impl/

# 找出 zhangsan 构建的、教师模型为 gpt4 的行
dit meta query "author=zhangsan AND source=gpt4"

# 统计各标签分布
dit meta stats tag --in feature-impl/
  hard:    3200 (33%)
  medium:  4500 (46%)
  easy:    2000 (21%)

# Web UI 上也有等价的筛选面板
```

§7 确认？最后一节 §8 讲技术栈汇总、项目分期和风险。

---

## 用户 (Turn 15)

确认

---

### 设计师回复



## §8 技术栈汇总、项目分期与风险

### 8.1 技术栈总览

| 层 | 技术 | 理由 |
|---|---|---|
| **dit-core** | Python 3.12+ / FastAPI / Uvicorn | 团队熟悉，async IO 性能足够 |
| **CLI (dit)** | Python / Typer / httpx | 同语言、uv 分发 |
| **Web (datahub-gateway)** | Forgejo fork (Go / Go template / Vue 3) | 复用 80% 协作功能 |
| **数据库** | PostgreSQL 16 | refs、元数据、审计、PR 状态、权限 |
| **对象存储** | 本地文件系统 (ext4/xfs) | 10T 服务器，简单可靠 |
| **对象压缩** | zstd (pyzstd) | 高压缩比、快速解压 |
| **JSON 规范化** | RFC 8785 (jcs) | 保证语义相同则 hash 相同 |
| **包管理** | uv | CLI 分发、开发环境管理 |
| **进程管理** | systemd / docker-compose | 生产部署 |
| **反向代理** | nginx | 统一入口、TLS、路由分发 |

### 8.2 部署架构

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
   dit-core         forgejo
   (FastAPI)            (Go 单二进制)
          │                  │
          ▼                  ▼
     /data/dit/     PostgreSQL (:5432)
     repos/objects/     (共用一个实例，不同 schema)
```

单台 10T 服务器跑全部组件。如果后期需要，core 可以横向扩展（无状态，对象库在共享路径上）。

### 8.3 项目分期

**Phase 0：基础骨架（3-4 周）**

目标：能跑通 `dit init / add / commit / log / diff` 的本地单机流程。

- dit-core 对象模型（row / manifest / tree / commit）
- 本地对象存储读写
- CLI 基础命令：init、add、status、commit、log、diff
- JSON canonical 化 + sha256 行指纹
- 本地 `.dit/` 工作区管理
- 单元测试覆盖核心模型

交付物：一个 Python 包，`uv tool install dit`，能在本地管理 JSONL 版本。

**Phase 1：远程协作（3-4 周）**

目标：多人能 clone / push / pull，有分支管理。

- dit-core HTTP API（对象传输、ref 管理）
- PostgreSQL 接入（refs、commit 索引）
- CLI 远程命令：clone、push、pull、fetch
- Lazy clone + sparse checkout
- 分支/tag 管理
- CAS 并发保护
- 基础认证（API token）

交付物：团队可以在服务器上共享数据仓库。

**Phase 2：合并与 PR（3-4 周）**

目标：三方合并、冲突解决、PR 流程。

- 三方合并算法
- query_fingerprint 刷新检测
- 冲突检测与标记
- PR 数据模型（PostgreSQL）
- CLI：merge、cherry-pick、pr create/list
- webhook 骨架

交付物：完整的分支协作流。

**Phase 3：Web UI（6-8 周）**

目标：Forgejo 改造上线。

- Forgejo fork + "数据仓库"类型
- 数据集主页、文件浏览页
- Diff/PR 评审 UI（Vue 3 组件）
- 行级 JSON 渲染
- 权限模型接入
- 通知集成

交付物：Web 平台可用。

**Phase 4：元数据与高级功能（4-6 周）**

目标：sidecar 元数据、搜索、统计、导出。

- row_meta / file_meta 系统
- CLI meta 命令全套
- Web 元数据编辑/查看
- 行级搜索
- stats 统计面板
- export 命令（本地路径 + S3）
- CI bridge 骨架

交付物：功能完整的 1.0 版本。

**Phase 5：打磨与生产化（持续）**

- 性能优化（pack 打包、并发下载调优）
- blame 功能
- gc 垃圾回收
- stash / reflog
- dedup 工具
- 监控告警
- 备份策略
- CI 与公司质检系统对接

### 8.4 风险与应对

| 风险 | 影响 | 应对 |
|---|---|---|
| **Forgejo 改造深度超预期** | Phase 3 延期 | 先做纯 API，Web 可以晚上线；团队初期用 CLI + 简易 Web 看板过渡 |
| **百万行 diff 性能** | diff/merge 慢 | manifest 是有序 hash 列表，diff = 两个列表的 LCS/set diff，O(n) 可解；大 manifest 分块并行 |
| **JSON canonical 化边界 case** | 相同数据不同 hash | 严格用 RFC 8785，入库前统一 canonical 化，不依赖用户格式 |
| **pangu 数据格式变更** | schema 不兼容 | 对象存储的是原始 JSON 字节（canonical 后），不做 schema 解析。format 变了只影响 validate 规则和 UI 渲染，不影响核心存储 |
| **单服务器单点故障** | 数据丢失 | 对象是不可变文件，定时 rsync 到备份盘/机器即可；PostgreSQL 用标准备份 |
| **10T 空间不够** | 存储满 | gc 清理无引用对象 + pack 压缩；实在不够可挂新盘或迁到 NAS |

### 8.5 不做什么（YAGNI）

明确排除以防范围蔓延：
- 不做实时协同编辑（不是 Google Docs）
- 不做数据标注平台（已有独立工具）
- 不做训练调度集成
- 不做 Dolt/DuckDB SQL 查询层（后期可选）
- 不做跨仓库 fork（先做好单仓库）
- 不做 Git 协议兼容（我们是独立协议）

---

全 8 节设计完成。整体确认后我来写正式 spec 文档。

---


┌────────────┬─────────────────────────────────────────────────────────────────┬──────────┐          
  │    阶段    │                              内容                               │ 预估周期 │
  ├────────────┼─────────────────────────────────────────────────────────────────┼──────────┤          
  │ Phase 0 ✅ │ 本地骨架 — init/add/commit/log/diff/status                      │ 3-4 周   │
  ├────────────┼─────────────────────────────────────────────────────────────────┼──────────┤
  │ Phase 1    │ 远程协作 — HTTP API, clone/push/pull, lazy clone, PostgreSQL    │ 3-4 周   │          
  ├────────────┼─────────────────────────────────────────────────────────────────┼──────────┤
  │ Phase 2    │ 合并与 PR — 三方合并, 冲突解决, cherry-pick, PR 数据模型        │ 3-4 周   │          
  ├────────────┼─────────────────────────────────────────────────────────────────┼──────────┤
  │ Phase 3    │ Web UI — Forgejo fork, 数据集浏览, Diff/PR 评审 UI (Vue 3)      │ 6-8 周   │
  ├────────────┼─────────────────────────────────────────────────────────────────┼──────────┤          
  │ Phase 4    │ 元数据与高级功能 — sidecar meta, 搜索, stats, export, CI bridge │ 4-6 周   │
  ├────────────┼─────────────────────────────────────────────────────────────────┼──────────┤          
  │ Phase 5    │ 打磨与生产化 — 性能优化, blame, gc, stash, dedup, 监控, 备份    │ 持续     │
  └────────────┴─────────────────────────────────────────────────────────────────┴──────────┘    