# Dit 灾难恢复与兼容性策略

本文定义最坏情况发生时的恢复边界：数据库、对象目录、网关配置和客户端协议必须能被备份、恢复和向后兼容地演进。

## 1. 数据恢复边界

一个完整的 core 备份必须同时包含：

| 数据 | 默认位置 | 原因 |
|------|----------|------|
| PostgreSQL `dit` 数据库 | `DIT_SERVER_DATABASE_URL` | 仓库、refs、tokens、PR、评论、审批、CI checks |
| core 对象目录 | `DIT_SERVER_DATA_DIR` | commits、trees、manifests、rows、sidecars、blobs |

这两部分必须来自同一个一致性窗口。只备份数据库或只备份对象目录都不够安全：refs 可能指向不存在的对象，或对象存在但没有任何 ref 能到达。

## 2. Core 备份

强一致备份推荐先停止 core 或阻断写入，然后执行：

```bash
cd /path/to/datahub
DIT_BACKUP_DIR=/secure/backups/dit \
DIT_SERVER_DATABASE_URL='postgresql+asyncpg://dit:***@db:5432/dit' \
DIT_SERVER_DATA_DIR=/data/dit \
DIT_BACKUP_CONFIRM_QUIESCED=1 \
./scripts/backup.sh
```

脚本会生成：

```text
dit-core-YYYYMMDDTHHMMSSZ/
  postgres.dump
  data-dir.tar.gz
  checksums.sha256
  manifest.json
```

如果必须在线备份，可以设置 `DIT_BACKUP_ALLOW_ONLINE=1`，但这只适合低风险场景；生产切换、升级前备份应使用 quiesced 模式。

## 3. Core 恢复

恢复是破坏性操作，必须显式确认：

```bash
cd /path/to/datahub
DIT_RESTORE_CONFIRM=I_UNDERSTAND_THIS_OVERWRITES_DATA \
DIT_RESTORE_OVERWRITE_DATA_DIR=1 \
DIT_SERVER_DATABASE_URL='postgresql+asyncpg://dit:***@db:5432/dit' \
DIT_SERVER_DATA_DIR=/data/dit \
./scripts/restore.sh /secure/backups/dit/dit-core-YYYYMMDDTHHMMSSZ
```

恢复后执行：

```bash
CORE_URL=http://localhost:8000 ./scripts/deployment-smoke.sh
```

再对关键仓库执行 admin fsck：

```bash
curl -fsS -X POST "$CORE_URL/api/v1/repos/$REPO/fsck" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"check_hashes":true,"check_graph":true}'
```

## 4. Gateway 一体化备份

如果使用 `dit-gateway/docker-compose.yml` 部署，优先使用 gateway 仓库的 compose 脚本。它会停止 gateway/core 写入面，备份 Forgejo DB、Dit DB、Forgejo volume 和 core object volume。

```bash
cd /path/to/dit-gateway
DIT_GATEWAY_BACKUP_DIR=/secure/backups/dit-gateway \
./scripts/compose-backup.sh
```

恢复：

```bash
cd /path/to/dit-gateway
DIT_GATEWAY_RESTORE_CONFIRM=I_UNDERSTAND_THIS_DESTROYS_COMPOSE_VOLUMES \
./scripts/compose-restore.sh /secure/backups/dit-gateway/dit-gateway-YYYYMMDDTHHMMSSZ
```

## 5. 向后兼容规则

生产演进必须遵守这些规则：

- 对象存储对象不可变。已有 hash 对应的 bytes 不允许改写，只能新增对象。
- 已发布对象格式只能做可选字段扩展；读取路径必须容忍旧对象缺少新字段。
- API 响应可增加字段，但不能删除或改名已有字段；破坏性变更必须新增版本路径。
- Alembic 迁移必须前向可重复执行，新增列优先 nullable 或带 server default。
- 新权限模型必须保留旧 token `permissions` 到新 `role` 的映射。
- ML 2.0 / JSONL 格式变化只应影响 validate/UI 渲染，不能影响对象可读性和导出能力。
- GC 只能删除不可达且超过 grace period 的对象；恢复演练前不要运行 aggressive GC。

## 6. 升级前最低动作

- [ ] 停止写入或进入维护窗口。
- [ ] 执行 core 或 gateway 一体化备份。
- [ ] 保存当前镜像 tag、git commit、`.env`/`app.ini` 的脱敏副本。
- [ ] 在 staging 环境跑恢复脚本并执行 smoke/fsck。
- [ ] 再升级生产。

## 7. 恢复优先级

最坏情况下按以下顺序处理：

1. 保护现场：停止 core/gateway，保留 PostgreSQL volume 和对象目录，不要运行 GC。
2. 复制原始 volume 到安全位置。
3. 用最近一次备份恢复到临时环境。
4. 运行 smoke 和 fsck，确认 refs 到对象图完整。
5. 将临时环境切为生产，或将修复后的 DB/对象目录回灌原环境。
