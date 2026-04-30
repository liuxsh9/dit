# Dit 生产部署与验收清单

本文面向从本地测试迁移到真实服务器的部署。目标是把已知易错点前置检查，避免上线后再靠人工逐项调试。

## 0. 服务器前置条件

**软件依赖：**

- Docker Engine >= 24 + Docker Compose V2（或 Docker Desktop）
- git
- curl（用于 smoke test）

**硬件要求：**

| 级别 | CPU | 内存 | 磁盘 |
|------|-----|------|------|
| 最低 | 2 核 | 2 GB | 10 GB |
| 推荐 | 4 核 | 4 GB | 50 GB+（随数据集规模增长） |

## 1. 架构和端口

推荐部署包含三类核心服务（TLS 可选）：

| 服务 | 默认端口 | 作用 |
|------|----------|------|
| datahub-gateway | 3000 | Forgejo 网关和 Web UI，代理 data repo 请求到 core |
| dit-core | 8000（仅内部） | FastAPI 数据版本管理 API、对象存储、验证、diff、PR API |
| PostgreSQL | 5432（仅内部） | core 元数据、权限、PR、CI 检查等表 |
| Caddy（可选） | 80/443 | TLS 终止、反向代理，自动 Let's Encrypt 证书 |

默认模式下 gateway 直接暴露 3000 端口，适合内网部署或无域名场景。
启用 TLS profile 后，外部流量通过 Caddy（80/443）进入，gateway 端口仅内部可见。

网关通过 `X-Service-Token` 调用 core。该值必须与 core 的 `DIT_SERVER_SERVICE_TOKEN` 完全一致。

## 2. Core 必填配置

| 变量 | 示例 | 说明 |
|------|------|------|
| `DIT_SERVER_DATABASE_URL` | `postgresql+asyncpg://dit:dit@db:5432/dit` | asyncpg 格式数据库 URL |
| `DIT_SERVER_DATA_DIR` | `/data/dit` | core 对象存储目录，必须持久化 |
| `DIT_SERVER_SERVICE_TOKEN` | 随机长密钥 | 首次 admin token 引导和网关服务间认证 |
| `DIT_SERVER_HOST` | `0.0.0.0` | 监听地址 |
| `DIT_SERVER_PORT` | `8000` | 监听端口 |
| `DIT_SERVER_AUTO_MIGRATE` | `1` | 容器启动前自动执行 Alembic 迁移；生产可设为 `0` 后由发布流程单独迁移 |
| `DIT_SERVER_WORKERS` | `2` | gunicorn worker 进程数，建议设为 CPU 核数 × 2 |
| `DIT_SERVER_RATE_LIMIT` | （空，禁用） | 全局速率限制，格式如 `100/minute`、`10/second`；留空则不限速 |

## 3. Gateway 必填配置

在 Forgejo `app.ini` 或环境变量中配置：

```ini
[datahub]
ENABLED = true
CORE_URL = http://core:8000
SERVICE_TOKEN = <same-as-DIT_SERVER_SERVICE_TOKEN>
```

Docker 环境变量形式：

```bash
FORGEJO__datahub__ENABLED=true
FORGEJO__datahub__CORE_URL=http://core:8000
FORGEJO__datahub__SERVICE_TOKEN=<same-as-DIT_SERVER_SERVICE_TOKEN>
```

## 3.1 Caddy TLS 配置（可选）

如果有公网域名且需要 HTTPS，可以启用 Caddy TLS profile：

```bash
docker compose --profile tls up -d
```

Caddy 通过 `DOMAIN` 环境变量确定公网域名，自动申请和续期 Let's Encrypt 证书。

| 变量 | 示例 | 说明 |
|------|------|------|
| `DOMAIN` | `data.example.com` | 公网域名；默认 `localhost`（自签名证书） |

内网部署或无域名场景不需要启用此 profile，直接通过 `http://<server-ip>:3000` 访问即可。

## 4. 推荐 Docker Compose 流程

如果使用 datahub-gateway 仓库的一体化 Docker Compose 部署：

```bash
git clone https://github.com/liuxsh9/datahub-gateway.git
cd datahub-gateway
```

1. 创建 `.env`：

```bash
SERVICE_TOKEN=<generate-a-long-random-secret>
POSTGRES_PASSWORD=<generate-a-db-password>
DIT_DB_PASSWORD=<generate-a-dit-db-password>
# GATEWAY_PORT=3000              # 可选，修改 gateway 暴露端口
# CORE_IMAGE=ghcr.io/liuxsh9/dit-core:latest  # 默认从 registry 拉取
```

2. 启动（内网 HTTP 模式，无需域名）：

```bash
docker compose up -d
# 访问 http://<server-ip>:3000
```

启用 TLS（需要域名）：

```bash
echo "DOMAIN=data.example.com" >> .env
docker compose --profile tls up -d
# 访问 https://data.example.com
```

3. 本地开发模式（从源码构建 core 镜像，需要 `../datahub` 并排 checkout）：

```bash
cp docker-compose.override.yml.example docker-compose.override.yml
docker compose up --build -d
```

4. 查看状态：

```bash
docker compose ps
docker compose logs --tail=100 core
docker compose logs --tail=100 gateway
```

## 5. 切流前 Smoke

在 datahub-gateway 目录下，用 `docker compose exec` 快速验证：

```bash
# core 健康检查
docker compose exec core curl -f http://localhost:8000/health

# core metrics
docker compose exec core curl -f http://localhost:8000/metrics

# core 未认证请求应返回 401
docker compose exec core curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/v1/repos
# 预期输出: 401

# gateway 健康检查
docker compose exec gateway curl -f http://localhost:3000/api/v1/version
```

如果同时 clone 了 datahub 仓库，也可以用自动化 smoke 脚本：

```bash
cd /path/to/datahub

# 内网 HTTP 模式
CORE_URL=http://localhost:8000 \
GATEWAY_URL=http://localhost:3000 \
./scripts/deployment-smoke.sh

# TLS 模式
CORE_URL=http://localhost:8000 \
GATEWAY_URL=https://data.example.com \
./scripts/deployment-smoke.sh
```

首次部署还应额外验证 admin token 引导路径：

```bash
CORE_URL=http://localhost:8000 \
DIT_SERVER_SERVICE_TOKEN=<same-as-service-token> \
CREATE_BOOTSTRAP_TOKEN=1 \
./scripts/deployment-smoke.sh
```

预期最后输出：

```text
deployment smoke checks passed
```

## 6. 首个数据仓库验收

部署 smoke 通过后，用真实 UI/API 做一次最小业务验收：

1. 登录 gateway，创建一个 data repo。
2. 用 `dit` 创建一个本地仓库，提交一份 ML 2.0 / OpenAI messages JSONL。
3. push 到 gateway 对应 data repo。
4. 打开 data repo 首页，确认能看到 latest commit、文件行数、文件 size、metadata coverage、quality checks。
5. 打开 JSONL 文件，确认 SFT 行以 conversation 卡片展示，而不是整行大字符串。
6. 创建一个数据变更，再打开 diff，确认新增/删除/修改的行能结构化展示。

## 7. 已知部署风险和处理

| 症状 | 根因 | 处理 |
|------|------|------|
| core `/health` 返回数据库 unhealthy | 数据库未启动、URL 错误、迁移失败 | 检查 `DIT_SERVER_DATABASE_URL`，查看 core 日志中的 Alembic 输出 |
| core `/health` 返回 data_dir unhealthy | 数据目录不存在或未挂载 | 持久化并创建 `DIT_SERVER_DATA_DIR` |
| 首次创建 admin token 返回 401 | service token 未设置或不匹配 | 确认 `DIT_SERVER_SERVICE_TOKEN` 与请求头 `X-Service-Token` 一致并重启 |
| gateway 创建 data repo 失败 | gateway 到 core 的服务令牌或 core URL 错误 | 检查 `[datahub] CORE_URL`、`SERVICE_TOKEN` 和 core 日志 |
| gateway 容器没有读取 `FORGEJO__datahub__...` | 使用了错误 Dockerfile 或绕过官方 entrypoint | 使用根目录 `Dockerfile`，不要使用已废弃的 `Dockerfile.datahub` |
| SQLite 相关测试或本地二进制缺驱动 | 构建缺少 sqlite tags | 使用 `TAGS='bindata sqlite sqlite_unlock_notify' make backend` 或官方 Dockerfile |
| UI 仍是旧前端资源 | 前端 bundle 未重新构建/未进入 bindata | 先运行 `NODE_ENV=development npx webpack`，再构建后端；Dockerfile 会自动执行完整构建 |
| core 启动报 `database "dit" does not exist` | 非 Compose 部署未创建 dit 数据库和用户 | Compose 部署会自动执行 `scripts/init-db.sh`；手动部署需先创建数据库：`CREATE USER dit WITH PASSWORD '...'; CREATE DATABASE dit OWNER dit;` |

## 8. 最低上线门槛

- [ ] core 镜像启动前迁移已执行，数据库包含最新 Alembic 版本。
- [ ] core `/health`、`/metrics` 通过。
- [ ] 未认证 API 返回 401。
- [ ] service-token bootstrap 至少在新环境验证一次。
- [ ] gateway `/api/healthz` 通过。
- [ ] gateway `[datahub]` 配置启用且能连通 core。
- [ ] 创建 data repo 会同步创建 core backing repo。
- [ ] JSONL 文件浏览、dataset overview、diff 结构化展示通过。
- [ ] `DIT_SERVER_DATA_DIR`、Forgejo `/data`、PostgreSQL volume 均为持久化存储。
- [ ] 已完成一次备份和恢复演练，见 [灾难恢复与兼容性策略](./disaster-recovery.md)。
