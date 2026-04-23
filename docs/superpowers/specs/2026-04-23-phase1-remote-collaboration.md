# Phase 1: Remote Collaboration — 设计文档

> 基于 [Phase 0 总体设计](2026-04-22-datahub-design.md) 的 Phase 1 细化设计。本文档记录了设计过程中的所有决策和完整技术方案。

---

## 1. Phase 1 目标

在 Phase 0 本地 CLI（init/add/commit/log/diff/status）基础上，实现多人远程协作：

- HTTP API 服务端（FastAPI）
- CLI 远程命令：clone / push / pull / fetch / remote
- PostgreSQL 存储 refs、repos、tokens
- CAS 并发保护
- API Token 认证
- 最小 lazy clone（metadata 全拉，row 数据按需下载）

---

## 2. 设计决策记录

### Q1: 服务端 refs 存储 — PG 还是文件系统？

**决定：直接上 PostgreSQL。**

理由：Phase 1 就是引入服务端的阶段，PG 是迟早要上的，CAS 用事务比文件锁可靠得多，PG 运维成本在内网环境很低。

### Q2: 认证方案

**决定：简单 API Token。**

服务端生成 token 存 PG（存 hash），CLI 配 token，请求带 `Authorization: Bearer <token>`。够用、实现简单、与后续 Forgejo OAuth 不冲突。

### Q3: 多仓库命名空间

**决定：扁平命名。**

repo URL 形如 `http://server/sft-code`，所有 repo 平铺。管理员通过 API 创建。后续可平滑扩展成 `<owner>/<repo>` namespace。

### Q4: Lazy clone 程度

**决定：最小 lazy clone。**

`dit clone` 拉全部 commits + trees + manifests（元数据轻量），rows 按需下载。diff/log/status 不需要网络请求，大头（row 数据）是 lazy 的。

### Q5: 服务端项目结构

**决定：同一个 Python 包。**

在 `src/dit/` 下新增 `server/` 模块，CLI 和服务端共享 `core/`。服务端依赖放 `[server]` optional group，`uv sync --extra server` 安装。

### 全局兼容性检查结论

- ObjectStore 文件系统 + PG refs 分层，Phase 2-5 不需要改
- API 路径 `/api/v1/repos/{repo}/...` 兼容 Phase 3 nginx 代理
- PG schema 用 `datahub` namespace，避免和 Phase 3 Forgejo 冲突
- 依赖用 optional group `[server]` 隔离，CLI 核心保持轻量
- ORM 用 SQLAlchemy 2.0 async，配合 alembic 迁移链

---

## 3. 整体架构

```
dit CLI ──── HTTP/JSON ────→ datahub-server (FastAPI)
                                    │
                    ┌───────────────┼───────────────┐
                    ▼               ▼               ▼
              ObjectStore      PostgreSQL        Config
              (文件系统)        (refs, repos,    (pydantic-settings)
              rows/manifests    tokens)
              /trees/commits
```

服务端是一个 FastAPI 应用，通过 `dit serve` 启动或 `uvicorn dit.server.app:app` 独立运行。复用 Phase 0 的 `dit.core`（ObjectStore、对象序列化）。

---

## 4. PostgreSQL Schema

所有表在 `datahub` schema 下，alembic 管理迁移。

```sql
CREATE SCHEMA datahub;

-- 仓库注册表
CREATE TABLE datahub.repos (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(128) UNIQUE NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- 引用表（branches/tags），CAS 更新
CREATE TABLE datahub.refs (
    repo_id     INT NOT NULL REFERENCES datahub.repos(id),
    name        VARCHAR(256) NOT NULL,       -- e.g. "heads/main"
    target_hash CHAR(64) NOT NULL,
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (repo_id, name)
);

-- API Token 认证
CREATE TABLE datahub.tokens (
    id          SERIAL PRIMARY KEY,
    token_hash  CHAR(64) NOT NULL UNIQUE,    -- SHA-256 of raw token
    label       VARCHAR(128) NOT NULL,       -- "zhangsan-laptop"
    repo_scope  INT REFERENCES datahub.repos(id),  -- NULL = all repos
    permissions VARCHAR(32) NOT NULL DEFAULT 'push', -- 'push' / 'read' / 'admin'
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    expires_at  TIMESTAMPTZ
);
```

**CAS 更新 refs：**

```sql
UPDATE datahub.refs SET target_hash = $new, updated_at = NOW()
WHERE repo_id = $repo AND name = $ref AND target_hash = $old
RETURNING target_hash;
```

rows affected = 0 → CAS 失败 → 返回 409 Conflict。新 ref 用 INSERT。

---

## 5. 服务端 API 路由

```
POST   /api/v1/repos                              创建仓库
GET    /api/v1/repos                              列出仓库

GET    /api/v1/repos/{repo}/refs                   列出所有 refs
GET    /api/v1/repos/{repo}/refs/{ref_type}/{name} 获取 ref → hash
POST   /api/v1/repos/{repo}/refs/{ref_type}/{name} CAS 更新 ref {old, new}

GET    /api/v1/repos/{repo}/objects/{type}/{hash}  下载对象
POST   /api/v1/repos/{repo}/objects/{type}/{hash}  上传对象（幂等）
POST   /api/v1/repos/{repo}/objects/batch-exists   批量存在性检查

POST   /api/v1/admin/tokens                        创建 token
DELETE /api/v1/admin/tokens/{id}                    撤销 token
```

- `{ref_type}` = `heads` 或 `tags`
- 认证：所有请求 `Authorization: Bearer <token>`，FastAPI `APIKeyHeader` dependency
- 权限：读操作 `read`，写操作 `push`，管理操作 `admin`

---

## 6. CLI 新增命令

```bash
dit serve --port 8000              # 启动 FastAPI 服务端
dit remote add origin <url>        # 添加 remote
dit remote remove origin           # 移除 remote
dit remote list                    # 列出 remotes
dit clone <url> [--token <token>]  # 克隆仓库
dit push [remote] [branch]         # 推送
dit pull [remote] [branch]         # 拉取 (fetch + fast-forward)
dit fetch [remote]                 # 拉取远端 refs 和对象
dit auth set-token <token>         # 保存 token 到 .datahub/config
```

**Remote 配置** — `.datahub/config` 用 TOML：

```toml
[remote.origin]
url = "http://server:8000"
token = "dit_xxxxxxxxxxxx"
```

---

## 7. 远程客户端

`src/dit/core/remote.py`，封装 httpx 调用：

```python
class RemoteClient:
    def __init__(self, url: str, token: str): ...
    async def get_ref(self, repo: str, ref: str) -> str | None: ...
    async def cas_ref(self, repo: str, ref: str, old: str, new: str) -> bool: ...
    async def upload_object(self, repo: str, obj_type: str, data: bytes) -> str: ...
    async def download_object(self, repo: str, obj_type: str, hash: str) -> bytes: ...
    async def batch_exists(self, repo: str, obj_type: str, hashes: list[str]) -> dict[str, bool]: ...
```

CLI 是同步的（typer），内部用 `asyncio.run()` 调异步客户端。

---

## 8. Push / Pull / Clone 流程

### Push

1. 读取本地 HEAD commit
2. 获取远端 ref 的 commit hash
3. fast-forward 检查（本地是远端的后代）
4. walk commit→tree→manifests→rows 收集新对象
5. batch-exists 过滤已存在对象
6. 上传（先 rows → manifests → tree → commit）
7. CAS 更新远端 ref
8. CAS 失败 → 提示用户先 pull

### Clone（最小 lazy）

1. 获取远端 main ref → commit hash
2. 下载 commit 链 + tree + manifests
3. 存入本地 `.datahub/objects/`
4. 设置本地 ref: `heads/main` = commit hash, HEAD → main
5. 设置 remote origin
6. 物化工作区：对每个 manifest 下载 rows → 重建 JSONL 文件

### Pull（仅 fast-forward）

1. fetch：获取远端 ref，下载缺失 commits/trees/manifests
2. fast-forward 检查
3. 是 → 更新本地 ref，下载新 rows，重建变更 JSONL
4. 否 → 报错 "not fast-forward, merge not supported yet"

---

## 9. 文件结构

```
src/dit/
├── __init__.py
├── cli/
│   ├── main.py              # 现有命令 + remote/clone/push/pull/serve/auth
│   └── __init__.py
├── core/
│   ├── diff.py              # (现有)
│   ├── hash.py              # (现有)
│   ├── index.py             # (现有)
│   ├── objects.py           # (现有)
│   ├── refs.py              # (现有，客户端继续用文件 refs)
│   ├── store.py             # (现有)
│   ├── workspace.py         # (现有) + 新增 materialize 功能
│   ├── remote.py            # 新增：httpx 远程客户端
│   └── config.py            # 新增：TOML 配置读写
└── server/
    ├── __init__.py
    ├── app.py               # FastAPI app + 中间件
    ├── config.py            # pydantic-settings 服务端配置
    ├── auth.py              # token 校验 dependency
    ├── database.py          # SQLAlchemy async engine + session
    ├── models.py            # SQLAlchemy ORM models
    ├── routes/
    │   ├── __init__.py
    │   ├── repos.py         # 仓库 CRUD
    │   ├── refs.py          # ref 读写 + CAS
    │   └── objects.py       # 对象上传/下载/batch-exists
    └── alembic/
        ├── env.py
        └── versions/
            └── 001_initial.py
```

---

## 10. 依赖

```toml
[project]
dependencies = [
    "typer>=0.15",
    "pyzstd>=0.16",
    "jcs>=0.2",
    "httpx>=0.27",
    "tomli-w>=1.0",
]

[project.optional-dependencies]
server = [
    "fastapi>=0.115",
    "uvicorn>=0.32",
    "sqlalchemy[asyncio]>=2.0",
    "asyncpg>=0.30",
    "pydantic-settings>=2.0",
    "alembic>=1.14",
]
```

用 `uv` 管理：`uv sync` 安装 CLI 依赖，`uv sync --extra server` 安装服务端依赖。

---

## 11. Phase 1 不做什么

- 三方合并（Phase 2）
- 非 fast-forward pull/push（Phase 2）
- Web UI（Phase 3）
- Sidecar 元数据（Phase 4）
- Sparse checkout 占位符（过度设计，最小 lazy 够用）
- 用户/组织命名空间（Phase 3 Forgejo 管理）
