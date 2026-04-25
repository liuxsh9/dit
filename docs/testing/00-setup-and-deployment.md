# Dit 手动测试指南 00：环境搭建与部署验证

本指南是 Dit ("dit") 手动测试系列的第一篇，覆盖从零开始的完整部署流程，适用于**本地开发**和 **Docker 部署**两种场景。

---

## 目录

1. [环境准备](#1-环境准备)
2. [安装依赖](#2-安装依赖)
3. [数据库配置](#3-数据库配置)
4. [启动服务](#4-启动服务)
5. [验证部署](#5-验证部署)
6. [创建管理员令牌](#6-创建管理员令牌)
7. [Docker 部署](#7-docker-部署)
8. [常见问题](#8-常见问题)

---

## 1. 环境准备

### 1.1 系统要求

| 依赖 | 最低版本 | 说明 |
|------|---------|------|
| Python | 3.12+ | `python --version` 确认 |
| uv | 最新版 | Python 包管理器 |
| PostgreSQL | 13+ | 主数据库 |
| curl | 任意版本 | 验证 API 端点 |

### 1.2 安装 uv

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# 验证安装
uv --version
```

### 1.3 安装 PostgreSQL

```bash
# macOS (Homebrew)
brew install postgresql@15
brew services start postgresql@15

# Ubuntu / Debian
sudo apt-get install -y postgresql postgresql-contrib
sudo systemctl start postgresql
```

### 1.4 确认 Python 版本

```bash
python --version
# 预期输出: Python 3.12.x 或更高
```

验证清单：
- [ ] `uv --version` 正常输出版本号
- [ ] `python --version` 显示 3.12 或更高
- [ ] PostgreSQL 服务已启动（`pg_isready` 返回 "accepting connections"）

---

## 2. 安装依赖

项目使用 `uv` 管理依赖。服务端功能（FastAPI、数据库等）在 `server` extra 中定义。

### 2.1 安装核心依赖 + 服务端依赖

```bash
cd /path/to/dit

# 安装所有依赖，包含 server extra
uv sync --extra server

# 安装 CLI，使后续可在任意目录直接运行 dit
# 开发期验收推荐 editable 安装，确保 dit 与当前源码保持一致
uv tool install --force --editable .
```

### 2.2 验证 CLI 可用

```bash
dit --help
```

预期输出（节选）：
```
Usage: dit [OPTIONS] COMMAND [ARGS]...

  Git-like version control for SFT training data.

Options:
  ...

Commands:
  serve    Start the Dit HTTP API server.
  ...
```

验证清单：
- [ ] `uv sync --extra server` 无报错完成
- [ ] `uv tool install --force --editable .` 无报错完成
- [ ] `dit --help` 显示帮助信息并包含 `serve` 命令
- [ ] `dit version` 正常输出版本号（如 `dit 0.1.0`）

> **PATH 说明**：`uv tool install` 默认会把可执行文件安装到 `~/.local/bin`。如果执行 `dit` 提示找不到命令，请先把该目录加入 `PATH`。Windows 上通常对应 `%USERPROFILE%\\.local\\bin`。

---

## 3. 数据库配置

### 3.1 创建数据库和 Schema

以 PostgreSQL 超级用户（通常为 `postgres`）登录：

```bash
psql -U postgres
```

在 psql 提示符中执行：

```sql
-- 创建数据库
CREATE DATABASE dit;

-- 创建专用用户（可选，推荐生产环境使用）
CREATE USER dit_user WITH PASSWORD 'yourpassword';
GRANT ALL PRIVILEGES ON DATABASE dit TO dit_user;

-- 连接到新建数据库
\c dit

-- 创建 schema（Dit 所有表放在 dit schema 下）
CREATE SCHEMA IF NOT EXISTS dit;
GRANT ALL ON SCHEMA dit TO dit_user;  -- 若使用专用用户

\q
```

> **说明**：所有 Dit 数据库表均使用 `dit` schema（如 `dit.tokens`、`dit.repos`）。

### 3.2 配置环境变量

在项目根目录创建 `.env` 文件（或直接 export 到 shell）：

```bash
# 示例 .env 文件（本地开发用）

# 数据库连接 URL（asyncpg 格式）
DIT_SERVER_DATABASE_URL=postgresql+asyncpg://localhost/dit

# 若使用专用数据库用户：
# DIT_SERVER_DATABASE_URL=postgresql+asyncpg://dit_user:yourpassword@localhost/dit

# 数据文件存储目录（存放 objects 等二进制对象）
DIT_SERVER_DATA_DIR=/tmp/dit-data

# 服务监听地址和端口（默认值，可省略）
DIT_SERVER_HOST=0.0.0.0
DIT_SERVER_PORT=8000

# 服务令牌（可选）：设置后可用 X-Service-Token 绕过 DB 鉴权，用于服务间调用
# DIT_SERVER_SERVICE_TOKEN=your-internal-secret
```

加载环境变量：

```bash
export $(grep -v '^#' .env | xargs)
```

或直接在终端 export：

```bash
export DIT_SERVER_DATABASE_URL="postgresql+asyncpg://localhost/dit"
export DIT_SERVER_DATA_DIR="/tmp/dit-data"
```

### 3.3 创建数据目录

```bash
mkdir -p /tmp/dit-data
```

> **说明**：数据目录用于存放 objects 文件（commits、trees、manifests、rows 等）。健康检查会验证此目录是否存在。

### 3.4 运行数据库迁移

Dit 使用 Alembic 管理数据库 schema。迁移脚本和配置文件位于 `src/dit/server/` 目录下。

> **说明**：迁移命令会优先读取 `DIT_SERVER_DATABASE_URL`；若未设置，才回退到 `src/dit/server/alembic.ini` 中的默认 `sqlalchemy.url`。

推荐直接在项目根目录执行：

```bash
# 运行所有迁移（将 schema 升级至最新版本）
uv run alembic -c src/dit/server/alembic.ini upgrade head
```

如果你已经切到 `src/dit/server/` 目录，也可以执行：

```bash
cd src/dit/server
uv run alembic -c alembic.ini upgrade head
```

预期输出（节选）：
```
INFO  [alembic.runtime.migration] Context impl PostgreSQLImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> 001, initial schema
INFO  [alembic.runtime.migration] Running upgrade 001 -> 002, webhooks
INFO  [alembic.runtime.migration] Running upgrade 002 -> 003, pull request meta
INFO  [alembic.runtime.migration] Running upgrade 003 -> 004, pr comment
INFO  [alembic.runtime.migration] Running upgrade 004 -> 005, branch protection
INFO  [alembic.runtime.migration] Running upgrade 005 -> 006, pr approval
INFO  [alembic.runtime.migration] Running upgrade 006 -> 007, reviewer rules
```

验证迁移结果：

```bash
psql -U postgres -d dit -c "\dt dit.*"
```

预期输出（节选）：
```
                    List of relations
  Schema  |           Name            | Type  |  Owner
----------+---------------------------+-------+----------
 dit  | branch_protection         | table | ...
 dit  | ci_checks                 | table | ...
 dit  | data_pull_request_meta    | table | ...
 dit  | data_reviewer_rule        | table | ...
 dit  | pr_approval               | table | ...
 dit  | pr_comment                | table | ...
 dit  | refs                      | table | ...
 dit  | repos                     | table | ...
 dit  | tokens                    | table | ...
 dit  | webhooks                  | table | ...
```

验证清单：
- [ ] 数据库 `dit` 创建成功
- [ ] Schema `dit` 创建成功
- [ ] `alembic upgrade head` 无报错完成（显示 007 号迁移）
- [ ] `\dt dit.*` 列出至少 `tokens`、`repos`、`refs` 等表
- [ ] 数据目录 `/tmp/dit-data` 存在

---

## 4. 启动服务

### 方式一：使用 `dit serve` CLI 命令（推荐开发使用）

```bash
# 确保环境变量已设置
export DIT_SERVER_DATABASE_URL="postgresql+asyncpg://localhost/dit"
export DIT_SERVER_DATA_DIR="/tmp/dit-data"

dit serve
```

也可以指定地址和端口：

```bash
dit serve --host 127.0.0.1 --port 8000
```

预期输出：
```
INFO:     Started server process [12345]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
```

### 方式二：直接用 `uvicorn`（更多控制选项）

```bash
uv run uvicorn dit.server.app:app \
  --host 0.0.0.0 \
  --port 8000 \
  --reload          # 开发时可加此参数，代码改动自动重启
```

### 后台运行（可选）

```bash
# 后台启动并将日志写入文件
nohup dit serve > /tmp/dit.log 2>&1 &
echo "PID: $!"
```

停止服务：

```bash
kill $(cat /tmp/dit.pid)
# 或
pkill -f "dit serve"
```

验证清单：
- [ ] 服务启动无报错，终端显示 "Application startup complete."
- [ ] 未见 "Cannot connect to database" 等连接错误

---

## 5. 验证部署

服务启动后，在**另一个终端**执行以下检查。

### 5.1 健康检查

```bash
curl -s http://localhost:8000/health | python3 -m json.tool
```

预期输出（服务正常时）：
```json
{
    "status": "healthy",
    "checks": {
        "database": {
            "status": "healthy",
            "latency_ms": 2.15
        },
        "data_dir": {
            "status": "healthy"
        }
    }
}
```

HTTP 状态码应为 `200`。若任一检查失败，整体 `status` 变为 `"unhealthy"`，HTTP 状态码为 `503`。

确认状态码：

```bash
curl -o /dev/null -w "%{http_code}\n" http://localhost:8000/health
# 预期输出: 200
```

### 5.2 Metrics 端点

```bash
curl -s http://localhost:8000/metrics | head -30
```

预期输出（节选，Prometheus 格式）：
```
# HELP dit_http_requests_total Total HTTP requests
# TYPE dit_http_requests_total counter
dit_http_requests_total{method="GET",path="/health",status="200"} 2.0
# HELP dit_http_request_duration_seconds HTTP request latency in seconds
# TYPE dit_http_request_duration_seconds histogram
...
```

### 5.3 API 根路径响应

```bash
# 无认证请求受保护端点，应返回 401
curl -o /dev/null -w "%{http_code}\n" http://localhost:8000/api/v1/repos
# 预期输出: 401
```

验证清单：
- [ ] `/health` 返回 HTTP 200，`status` 为 `"healthy"`
- [ ] `/health` 的 `database.status` 为 `"healthy"`
- [ ] `/health` 的 `data_dir.status` 为 `"healthy"`
- [ ] `/metrics` 返回 Prometheus 格式的文本（含 `dit_http_requests_total`）
- [ ] 受保护端点返回 401（未认证）

---

## 6. 创建管理员令牌

Dit 使用 Bearer Token 鉴权。首个 admin 令牌需通过 **服务令牌（Service Token）** 引导创建。

### 6.1 方法一：使用 Service Token 引导（推荐）

设置服务令牌后重启服务：

```bash
export DIT_SERVER_SERVICE_TOKEN="my-bootstrap-secret"
# 重启服务使配置生效
```

使用 `X-Service-Token` 头创建第一个 admin 令牌：

```bash
curl -s -X POST http://localhost:8000/api/v1/admin/tokens \
  -H "Content-Type: application/json" \
  -H "X-Service-Token: my-bootstrap-secret" \
  -d '{
    "label": "admin-token",
    "permissions": "admin"
  }' | python3 -m json.tool
```

预期输出：
```json
{
    "id": 1,
    "label": "admin-token",
    "permissions": "admin",
    "token": "dit_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
}
```

> **重要**：`token` 字段只在创建时返回一次，请立即保存！后续只能看到 token ID，无法再次查看原始值。

### 6.2 方法二：直接插入数据库（紧急情况）

```bash
# 计算令牌的 SHA-256 哈希
TOKEN="dit_mybootstraptoken123"
TOKEN_HASH=$(echo -n "$TOKEN" | python3 -c "import sys,hashlib; print(hashlib.sha256(sys.stdin.read().encode()).hexdigest())")

psql -U postgres -d dit -c "
  INSERT INTO dit.tokens (token_hash, label, permissions, role)
  VALUES ('$TOKEN_HASH', 'bootstrap-admin', 'admin', 'owner');
"
```

### 6.3 保存令牌并验证

```bash
# 将令牌存入环境变量
export ADMIN_TOKEN="dit_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"

# 验证令牌有效：列出所有令牌
curl -s http://localhost:8000/api/v1/admin/tokens \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

> **注意**：上述 `GET /api/v1/admin/tokens` 仅为示意，实际端点以路由定义为准。可用以下方式快速测试令牌是否有效：

```bash
# 列出仓库（需要有效令牌）
curl -s -o /dev/null -w "%{http_code}\n" \
  http://localhost:8000/api/v1/repos \
  -H "Authorization: Bearer $ADMIN_TOKEN"
# 预期: 200
```

### 6.4 创建普通用户令牌（可选）

Admin 令牌到位后，可为其他场景创建不同权限的令牌：

```bash
# 创建只读令牌
curl -s -X POST http://localhost:8000/api/v1/admin/tokens \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -d '{"label": "reader-ci", "permissions": "read"}' | python3 -m json.tool

# 创建推送令牌（committer 权限）
curl -s -X POST http://localhost:8000/api/v1/admin/tokens \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -d '{"label": "push-bot", "permissions": "push"}' | python3 -m json.tool
```

验证清单：
- [ ] 成功创建 admin 令牌，收到 `"token": "dit_..."` 格式的原始令牌
- [ ] 使用该令牌访问 `/api/v1/repos` 返回 200
- [ ] 不带令牌访问返回 401，带错误令牌返回 401

---

## 7. Docker 部署

### 7.1 构建镜像

```bash
cd /path/to/dit

docker build -t dit-core:latest .
```

预期输出（节选）：
```
[+] Building ...
Step 1/9 : FROM python:3.12-slim
...
Successfully built xxxxxxxx
Successfully tagged dit-core:latest
```

### 7.2 使用 docker-compose（推荐）

创建 `docker-compose.yml`：

```yaml
version: "3.9"

services:
  postgres:
    image: postgres:15-alpine
    environment:
      POSTGRES_DB: dit
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 5s
      timeout: 3s
      retries: 5

  dit-migrate:
    image: dit-core:latest
    depends_on:
      postgres:
        condition: service_healthy
    environment:
      DIT_SERVER_DATABASE_URL: postgresql+asyncpg://postgres:postgres@postgres/dit
    working_dir: /app/src/dit/server
    command: >
      sh -c "pip install alembic asyncpg sqlalchemy &&
             alembic -c alembic.ini upgrade head"
    restart: "no"

  dit:
    image: dit-core:latest
    depends_on:
      postgres:
        condition: service_healthy
      dit-migrate:
        condition: service_completed_successfully
    environment:
      DIT_SERVER_DATABASE_URL: postgresql+asyncpg://postgres:postgres@postgres/dit
      DIT_SERVER_DATA_DIR: /data/dit
      DIT_SERVER_HOST: 0.0.0.0
      DIT_SERVER_PORT: 8000
      DIT_SERVER_SERVICE_TOKEN: change-me-in-production
    ports:
      - "8000:8000"
    volumes:
      - dit_data:/data/dit

volumes:
  pgdata:
  dit_data:
```

启动：

```bash
docker-compose up -d
```

### 7.3 单独运行容器（不用 compose）

先确保 PostgreSQL 可访问，然后：

```bash
# 准备数据目录
mkdir -p /tmp/dit-docker-data

docker run -d \
  --name dit-core \
  -p 8000:8000 \
  -e DIT_SERVER_DATABASE_URL="postgresql+asyncpg://postgres:postgres@host.docker.internal/dit" \
  -e DIT_SERVER_DATA_DIR="/data/dit" \
  -e DIT_SERVER_SERVICE_TOKEN="my-secret" \
  -v /tmp/dit-docker-data:/data/dit \
  dit-core:latest
```

> **macOS/Windows**：使用 `host.docker.internal` 访问宿主机 PostgreSQL。  
> **Linux**：使用 `--network host` 或宿主机实际 IP。

### 7.4 验证 Docker 部署

```bash
# 等待容器启动（约 10-15 秒，Dockerfile 有 start-period 设置）
sleep 15

# 检查容器状态
docker ps | grep dit-core

# 健康检查
curl -s http://localhost:8000/health | python3 -m json.tool

# 查看日志
docker logs dit-core --tail 50
```

### 7.5 在容器内运行迁移

若单独运行容器（未使用 compose 的 migrate 服务），需手动在容器内运行迁移：

```bash
docker exec dit-core \
  sh -c "cd /app/src/dit/server && alembic -c alembic.ini upgrade head"
```

验证清单：
- [ ] `docker build` 成功，镜像创建完毕
- [ ] 容器启动后 `docker ps` 显示 `STATUS` 为 `Up`
- [ ] Docker 健康检查通过（`STATUS` 中含 `(healthy)`）
- [ ] `curl http://localhost:8000/health` 返回 200 且 `status: healthy`
- [ ] `docker logs dit-core` 无致命错误

---

## 8. 常见问题

### 问题 1：启动时报 `Connection refused` 或 `could not connect to server`

**症状**：
```
sqlalchemy.exc.OperationalError: (asyncpg.exceptions.ConnectionRefusedError)
could not connect to server: Connection refused
```

**原因与解决**：
1. PostgreSQL 未启动 → `brew services start postgresql@15`（macOS）或 `sudo systemctl start postgresql`（Linux）
2. 数据库 URL 错误 → 检查 `DIT_SERVER_DATABASE_URL` 格式是否正确，用户名/密码/端口是否匹配
3. 数据库不存在 → 确认已执行 `CREATE DATABASE dit`

### 问题 2：健康检查 `data_dir` 状态为 `unhealthy`

**症状**：
```json
{"status": "unhealthy", "checks": {"data_dir": {"status": "unhealthy", "error": "data directory not found"}}}
```

**原因与解决**：
1. 目录不存在 → `mkdir -p $DIT_SERVER_DATA_DIR`
2. 环境变量未设置 → 确认 `DIT_SERVER_DATA_DIR` 已 export，默认值为 `/data/dit`

### 问题 3：`alembic upgrade head` 报 `schema "dit" does not exist`

**解决**：手动创建 schema：
```sql
psql -U postgres -d dit -c "CREATE SCHEMA IF NOT EXISTS dit;"
```

### 问题 4：`server` extra 未安装，`dit serve` 提示 `Server dependencies not installed`

**症状**：
```
Server dependencies not installed. Run: uv sync --extra server
```

**解决**：
```bash
uv sync --extra server
```

### 问题 5：迁移命令在项目根目录执行，报 `can't locate alembic.ini`

**原因**：`alembic.ini` 在 `src/dit/server/` 目录，不在根目录。

**解决**：
```bash
# 推荐：从根目录指定配置文件路径
uv run alembic -c src/dit/server/alembic.ini upgrade head

# 或者切换到 alembic.ini 所在目录再执行
cd src/dit/server
uv run alembic -c alembic.ini upgrade head
```

### 问题 6：创建令牌时返回 401

**可能原因**：
1. 未设置 `DIT_SERVER_SERVICE_TOKEN` 或值不匹配 → 检查环境变量后重启服务
2. `Authorization` 头格式错误 → 应为 `Bearer <token>`，注意大小写和空格

### 问题 7：Docker 容器频繁重启

**排查**：
```bash
docker logs dit-core --tail 100
```

常见原因：
- 数据库连接失败（PostgreSQL 未就绪）→ 等待 DB 健康后再启动，或使用 compose 的 `depends_on: condition: service_healthy`
- 数据目录未挂载 → 确认 `-v` 参数正确

### 问题 8：`Permission denied` 访问数据目录

```bash
# 确认当前用户对目录有写权限
ls -la $DIT_SERVER_DATA_DIR
chmod 755 $DIT_SERVER_DATA_DIR
```

---

## 附录：完整 .env 示例

```bash
# Dit 服务端配置
# 环境变量前缀：DIT_SERVER_

# 数据库连接（必填）
DIT_SERVER_DATABASE_URL=postgresql+asyncpg://localhost/dit

# 数据文件存储目录（必填，需提前创建）
DIT_SERVER_DATA_DIR=/tmp/dit-data

# 服务绑定地址（默认 0.0.0.0）
DIT_SERVER_HOST=0.0.0.0

# 服务监听端口（默认 8000）
DIT_SERVER_PORT=8000

# 服务间内部令牌（可选，配置后可用 X-Service-Token 头绕过 DB 鉴权）
# 用于引导创建第一个 admin 令牌，或服务间调用
# DIT_SERVER_SERVICE_TOKEN=change-me-in-production
```

---

*下一篇：[01 - 本地操作基础测试](./01-local-operations.md)*
