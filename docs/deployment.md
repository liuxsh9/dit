# Dit 生产部署与验收清单

本文面向从本地测试迁移到真实服务器的部署。目标是把已知易错点前置检查，避免上线后再靠人工逐项调试。

## 1. 架构和端口

推荐部署包含三类服务：

| 服务 | 默认端口 | 作用 |
|------|----------|------|
| PostgreSQL | 5432 | core 元数据、权限、PR、CI 检查等表 |
| dit-core | 8000 | FastAPI 数据版本管理 API、对象存储、验证、diff、PR API |
| datahub-gateway | 3000 | Forgejo 网关和 Web UI，代理 data repo 请求到 core |

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

## 4. 推荐 Docker Compose 流程

如果使用 `/Users/lxs/code/datahub-gateway/docker-compose.yml` 这套一体化部署：

1. 在 `datahub-gateway` 仓库创建 `.env`：

```bash
SERVICE_TOKEN=<generate-a-long-random-secret>
POSTGRES_PASSWORD=<generate-a-db-password>
DIT_DB_PASSWORD=<generate-a-dit-db-password>
```

2. 确认目录布局：

```text
/path/to/datahub
/path/to/datahub-gateway
```

`datahub-gateway/docker-compose.yml` 默认用 `../datahub` 构建 core 镜像。

3. 构建并启动：

```bash
cd /path/to/datahub-gateway
docker compose up --build -d
```

4. 查看状态：

```bash
docker compose ps
docker compose logs --tail=100 core
docker compose logs --tail=100 gateway
```

## 5. 切流前 Smoke

在 core 仓库执行：

```bash
cd /path/to/datahub
CORE_URL=http://localhost:8000 \
GATEWAY_URL=http://localhost:3000 \
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
