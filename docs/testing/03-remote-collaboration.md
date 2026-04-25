# DataHub 手动测试指南 03：远程协作

本指南覆盖 DataHub 的远程仓库操作，包括：远程配置管理、首次推送、克隆、拉取更新、分支推送、令牌认证，以及若干边界场景验证。

**前置条件**：
- 已完成 **指南 00**（服务端在 `localhost:8000` 正常运行，Admin 令牌 `dit_admin_token` 已备好）
- 已完成 **指南 01**（本地仓库至少有一次提交）

---

## 目录

1. [前置条件确认](#1-前置条件确认)
2. [配置远程](#2-配置远程)
3. [服务端创建仓库](#3-服务端创建仓库)
4. [首次推送](#4-首次推送)
5. [克隆仓库](#5-克隆仓库)
6. [协作流程](#6-协作流程)
7. [拉取更新](#7-拉取更新)
8. [分支推送](#8-分支推送)
9. [令牌认证](#9-令牌认证)
10. [边界场景](#10-边界场景)

---

## 1. 前置条件确认

### 1.1 确认服务端运行

```bash
curl -s http://localhost:8000/api/health | python3 -m json.tool
```

验证清单：
- [ ] 返回 HTTP 200，JSON 中含 `"status": "ok"`（或类似健康字段）

### 1.2 确认 Admin 令牌可用

```bash
export ADMIN_TOKEN="<你的 admin token>"   # 替换为实际令牌
curl -s -H "Authorization: token $ADMIN_TOKEN" \
     http://localhost:8000/api/v1/repos | python3 -m json.tool
```

验证清单：
- [ ] 返回 HTTP 200，可以是空数组 `[]` 或已有仓库列表
- [ ] 不出现 `401 Unauthorized`

### 1.3 准备本地工作目录

```bash
export WORK_DIR=$(mktemp -d)
echo "工作目录：$WORK_DIR"
mkdir -p "$WORK_DIR/repo-a"  # 用作推送方
```

### 1.4 初始化本地仓库并创建至少一次提交

```bash
cd "$WORK_DIR/repo-a"
uv run dit init

cat > train.jsonl << 'EOF'
{"messages": [{"role": "user", "content": "What is 2+2?"}, {"role": "assistant", "content": "4"}]}
{"messages": [{"role": "user", "content": "Name a color."}, {"role": "assistant", "content": "Blue"}]}
EOF

uv run dit add train.jsonl
uv run dit commit -m "v1: initial training data"
uv run dit log
```

验证清单：
- [ ] `dit init` 输出 `Initialized empty dit repository`
- [ ] `dit add` 输出 `staged train.jsonl (2 rows)`
- [ ] `dit commit` 输出 `[main <hash>] v1: initial training data`
- [ ] `dit log` 显示该提交记录

---

## 2. 配置远程

### 2.1 添加远程

```bash
cd "$WORK_DIR/repo-a"
uv run dit remote add origin http://localhost:8000/sft-demo
```

验证清单：
- [ ] 输出 `Remote 'origin' added: http://localhost:8000/sft-demo`

### 2.2 查看远程列表

```bash
uv run dit remote list
```

验证清单：
- [ ] 输出格式为 `origin  http://localhost:8000/sft-demo`（名称与 URL 以 Tab 分隔）

### 2.3 添加令牌（方法一：remote add 时附带）

```bash
uv run dit remote remove origin  # 先删除刚才添加的
uv run dit remote add origin http://localhost:8000/sft-demo --token "$ADMIN_TOKEN"
uv run dit remote list
```

验证清单：
- [ ] `remote remove` 输出 `Remote 'origin' removed.`
- [ ] `remote add` 成功，`remote list` 仍显示 `origin`

> 注意：`remote list` 只显示名称和 URL，令牌不会明文输出。

### 2.4 更新令牌（方法二：auth set-token）

```bash
uv run dit auth set-token "$ADMIN_TOKEN"
```

验证清单：
- [ ] 输出 `Token stored for remote 'origin'.`

> `auth set-token` 默认作用于 `origin`。若需指定其他远程，使用 `--remote <name>`。

### 2.5 删除远程

```bash
uv run dit remote add backup http://localhost:8000/backup-repo
uv run dit remote list
uv run dit remote remove backup
uv run dit remote list
```

验证清单：
- [ ] 第一次 `remote list` 显示 `origin` 和 `backup` 两条
- [ ] `remote remove backup` 输出 `Remote 'backup' removed.`
- [ ] 第二次 `remote list` 只显示 `origin`

### 2.6 检查配置文件内容

```bash
cat "$WORK_DIR/repo-a/.datahub/config"
```

验证清单：
- [ ] 文件为 TOML 格式，含 `[remote.origin]` 段落
- [ ] 含 `url = "http://localhost:8000/sft-demo"`
- [ ] 含 `token = "<令牌值>"`

---

## 3. 服务端创建仓库

在推送之前，必须先在服务端创建目标仓库。

```bash
curl -s -X POST http://localhost:8000/api/v1/repos \
     -H "Content-Type: application/json" \
     -H "Authorization: token $ADMIN_TOKEN" \
     -d '{"name": "sft-demo"}' | python3 -m json.tool
```

验证清单：
- [ ] 返回 HTTP 201
- [ ] 响应 JSON 中含 `"name": "sft-demo"`

**确认仓库已出现在列表中：**

```bash
curl -s -H "Authorization: token $ADMIN_TOKEN" \
     http://localhost:8000/api/v1/repos | python3 -m json.tool
```

验证清单：
- [ ] 列表中包含 `sft-demo`

---

## 4. 首次推送

### 4.1 执行推送

```bash
cd "$WORK_DIR/repo-a"
uv run dit push
```

验证清单：
- [ ] 输出包含 `Pushed N new objects to origin/main`（N > 0）
- [ ] 输出包含当前 commit 的前 8 位 hash

### 4.2 服务端验证 — 检查 ref

```bash
curl -s -H "Authorization: token $ADMIN_TOKEN" \
     "http://localhost:8000/api/v1/repos/sft-demo/refs/heads/main" | python3 -m json.tool
```

验证清单：
- [ ] 返回 HTTP 200
- [ ] 响应含 `"target_hash"` 字段，值为 64 位十六进制字符串
- [ ] 与 `dit log` 显示的本地 commit hash 一致

### 4.3 服务端验证 — 确认对象存在

记录 commit hash：

```bash
LOCAL_HASH=$(cd "$WORK_DIR/repo-a" && uv run dit log | head -1 | awk '{print $2}')
echo "Commit hash: $LOCAL_HASH"

# 检查 commit 对象
curl -s -o /dev/null -w "%{http_code}" \
     -H "Authorization: token $ADMIN_TOKEN" \
     "http://localhost:8000/api/v1/repos/sft-demo/objects/commits/$LOCAL_HASH"
echo ""
```

验证清单：
- [ ] 返回 `200`（对象已上传到服务端）

### 4.4 重复推送（幂等性）

```bash
uv run dit push
```

验证清单：
- [ ] 退出码为 0
- [ ] 输出包含 `Pushed 0 new objects` 或类似提示，说明无新对象需要上传

---

## 5. 克隆仓库

### 5.1 克隆到新目录

```bash
cd "$WORK_DIR"
uv run dit clone http://localhost:8000/sft-demo repo-b --token "$ADMIN_TOKEN"
```

验证清单：
- [ ] 输出 `Cloning http://localhost:8000/sft-demo -> ...repo-b`
- [ ] 输出 `  train.jsonl`（已物化的文件）
- [ ] 输出 `Clone complete. 1 commit(s).`
- [ ] 目录 `repo-b/` 已创建

### 5.2 验证克隆目录结构

```bash
ls -la "$WORK_DIR/repo-b/"
ls -la "$WORK_DIR/repo-b/.datahub/"
cat "$WORK_DIR/repo-b/.datahub/config"
```

验证清单：
- [ ] `repo-b/train.jsonl` 存在
- [ ] `repo-b/.datahub/` 目录存在，含 `objects/`、`HEAD` 等文件
- [ ] `.datahub/config` 中 `[remote.origin]` 的 URL 为 `http://localhost:8000/sft-demo`

### 5.3 验证克隆内容与原始内容一致

```bash
echo "=== 原始仓库 ==="
cat "$WORK_DIR/repo-a/train.jsonl"
echo ""
echo "=== 克隆仓库 ==="
cat "$WORK_DIR/repo-b/train.jsonl"

# 行数比较
wc -l "$WORK_DIR/repo-a/train.jsonl" "$WORK_DIR/repo-b/train.jsonl"
```

验证清单：
- [ ] 两个文件行数相同
- [ ] 内容逐行一致（或语义等价）

### 5.4 验证 git log 一致

```bash
echo "=== repo-a log ==="
cd "$WORK_DIR/repo-a" && uv run dit log

echo "=== repo-b log ==="
cd "$WORK_DIR/repo-b" && uv run dit log
```

验证清单：
- [ ] 两个仓库的 commit hash、作者、消息完全一致

---

## 6. 协作流程

本节模拟两位开发者 A（repo-a）和 B（repo-b）之间的协作：B 新增一条数据并推送，A 拉取更新。

### 6.1 B 在克隆仓库中修改数据

```bash
cd "$WORK_DIR/repo-b"

cat > train.jsonl << 'EOF'
{"messages": [{"role": "user", "content": "What is 2+2?"}, {"role": "assistant", "content": "4"}]}
{"messages": [{"role": "user", "content": "Name a color."}, {"role": "assistant", "content": "Blue"}]}
{"messages": [{"role": "user", "content": "Capital of France?"}, {"role": "assistant", "content": "Paris"}]}
EOF

uv run dit add train.jsonl
uv run dit commit -m "v2: add geography question"
uv run dit log
```

验证清单：
- [ ] `dit add` 输出 `staged train.jsonl (3 rows)`
- [ ] `dit commit` 输出 `[main <hash>] v2: add geography question`
- [ ] `dit log` 显示 2 条提交：`v2:...` 和 `v1:...`

### 6.2 B 推送到服务端

```bash
cd "$WORK_DIR/repo-b"
uv run dit push
```

验证清单：
- [ ] 输出 `Pushed N new objects to origin/main`

### 6.3 服务端验证 ref 已更新

```bash
curl -s -H "Authorization: token $ADMIN_TOKEN" \
     "http://localhost:8000/api/v1/repos/sft-demo/refs/heads/main" | python3 -m json.tool
```

记录新的 `target_hash`，应与 B 的 v2 commit hash 一致：

```bash
cd "$WORK_DIR/repo-b" && uv run dit log | head -1
```

验证清单：
- [ ] 服务端 `target_hash` 与 B 本地 `dit log` 首行 hash 相同

---

## 7. 拉取更新

### 7.1 Fetch（只下载，不合并）

```bash
cd "$WORK_DIR/repo-a"
uv run dit fetch
```

验证清单：
- [ ] 输出 `Fetched N new objects from origin/main`（N > 0）
- [ ] 本地 `train.jsonl` 内容**未**改变（fetch 不更新工作目录）

```bash
wc -l "$WORK_DIR/repo-a/train.jsonl"
```

验证清单：
- [ ] 仍为 2 行（fetch 后工作文件未变）

### 7.2 Pull（下载 + 快进合并）

```bash
cd "$WORK_DIR/repo-a"
uv run dit pull
```

验证清单：
- [ ] 输出包含 `Pulled N new objects. Now at <hash>.`
- [ ] 输出中的 hash 与 B 的 v2 commit hash 一致

### 7.3 验证工作目录已更新

```bash
cat "$WORK_DIR/repo-a/train.jsonl"
wc -l "$WORK_DIR/repo-a/train.jsonl"
```

验证清单：
- [ ] 文件现在有 3 行
- [ ] 第三行包含 `"Capital of France?"`

### 7.4 两端内容一致性最终确认

```bash
diff "$WORK_DIR/repo-a/train.jsonl" "$WORK_DIR/repo-b/train.jsonl"
echo "diff 退出码: $?"
```

验证清单：
- [ ] `diff` 无输出，退出码为 0（两文件完全相同）

---

## 8. 分支推送

### 8.1 在 repo-a 创建 feature 分支并提交

```bash
cd "$WORK_DIR/repo-a"
uv run dit checkout -b feature/new-dataset

cat >> eval.jsonl << 'EOF'
{"messages": [{"role": "user", "content": "What is the speed of light?"}, {"role": "assistant", "content": "Approximately 3×10^8 m/s"}]}
EOF

uv run dit add eval.jsonl
uv run dit commit -m "feat: add eval dataset"
uv run dit log
```

验证清单：
- [ ] `checkout -b` 输出 `Switched to new branch 'feature/new-dataset'.`
- [ ] commit 成功，log 显示新的提交

### 8.2 推送 feature 分支

```bash
uv run dit push --branch feature/new-dataset
```

验证清单：
- [ ] 输出 `Pushed N new objects to origin/feature/new-dataset`

### 8.3 服务端验证分支 ref

```bash
curl -s -H "Authorization: token $ADMIN_TOKEN" \
     "http://localhost:8000/api/v1/repos/sft-demo/refs/heads/feature/new-dataset" \
     | python3 -m json.tool
```

验证清单：
- [ ] 返回 HTTP 200
- [ ] `target_hash` 与 feature 分支本地最新 commit 一致

### 8.4 克隆指定分支

```bash
cd "$WORK_DIR"
uv run dit clone http://localhost:8000/sft-demo repo-feature \
    --token "$ADMIN_TOKEN" --branch feature/new-dataset
```

验证清单：
- [ ] 输出 `Clone complete.`
- [ ] `repo-feature/eval.jsonl` 存在且包含刚才添加的数据
- [ ] `repo-feature/` 中不存在 main 分支特有的第 3 条 train 数据（因为 feature 是从 v2 之后独立的）

> 注意：feature 分支的 `train.jsonl` 应包含 v2 的 3 行（feature 从 main 的 v2 分出），`eval.jsonl` 包含 1 行。

---

## 9. 令牌认证

### 9.1 无令牌推送应失败

```bash
cd "$WORK_DIR"
mkdir repo-noauth
cd repo-noauth
uv run dit init

cat > data.jsonl << 'EOF'
{"messages": [{"role": "user", "content": "test"}, {"role": "assistant", "content": "response"}]}
EOF
uv run dit add data.jsonl
uv run dit commit -m "test commit"

# 添加远程但不设置令牌
uv run dit remote add origin http://localhost:8000/sft-demo
uv run dit push
```

验证清单：
- [ ] 推送**失败**，退出码非 0
- [ ] 错误信息包含 `401`、`Unauthorized` 或 `forbidden` 等认证失败提示

> 服务端默认要求令牌验证，无令牌推送会被拒绝。

### 9.2 令牌错误推送应失败

```bash
uv run dit auth set-token "invalid_token_xyz"
uv run dit push
```

验证清单：
- [ ] 推送**失败**，退出码非 0
- [ ] 错误信息包含认证失败相关内容

### 9.3 正确令牌推送成功

```bash
uv run dit auth set-token "$ADMIN_TOKEN"
uv run dit push
```

验证清单：
- [ ] 推送成功，输出 `Pushed N new objects to origin/main`（或 `0 new objects` 如果对象已存在）

### 9.4 auth login（存储 Forgejo 凭据）

```bash
cd "$WORK_DIR/repo-a"
uv run dit auth login --url http://localhost:8000 --token "$ADMIN_TOKEN"
```

验证清单：
- [ ] 输出 `Credentials saved to ...`
- [ ] 输出 `Logged in to http://localhost:8000`
- [ ] 凭据文件存在于 `.datahub/credentials`（或 `~/.datahub/credentials` 如果不在仓库目录内）

---

## 10. 边界场景

### 10.1 推送到不存在的仓库

```bash
cd "$WORK_DIR/repo-a"

# 配置指向不存在的仓库
uv run dit remote add nonexist http://localhost:8000/does-not-exist --token "$ADMIN_TOKEN"
uv run dit push --remote nonexist
```

验证清单：
- [ ] 推送失败，退出码非 0
- [ ] 错误信息包含 `404`、`not found` 或类似提示

清理：

```bash
uv run dit remote remove nonexist
```

### 10.2 已是最新，pull 无变化

```bash
cd "$WORK_DIR/repo-b"
uv run dit pull
```

验证清单：
- [ ] 输出 `Already up to date.`
- [ ] 退出码为 0
- [ ] 工作目录文件内容未改变

### 10.3 fetch 已是最新

```bash
cd "$WORK_DIR/repo-b"
uv run dit fetch
```

验证清单：
- [ ] 输出 `Already up to date.`

### 10.4 克隆空仓库

先在服务端创建一个空仓库（不推送任何内容）：

```bash
curl -s -X POST http://localhost:8000/api/v1/repos \
     -H "Content-Type: application/json" \
     -H "Authorization: token $ADMIN_TOKEN" \
     -d '{"name": "empty-repo"}' | python3 -m json.tool
```

然后尝试克隆：

```bash
cd "$WORK_DIR"
uv run dit clone http://localhost:8000/empty-repo repo-empty --token "$ADMIN_TOKEN"
```

验证清单：
- [ ] 克隆失败，输出包含 `fatal: remote branch 'main' not found` 或类似错误
- [ ] 退出码非 0

### 10.5 分歧推送被拒绝（并发冲突）

模拟两个客户端从相同 base 分别提交，后推送的一方被拒绝：

```bash
# 确认 repo-a 和 repo-b 当前 main 分支一致
cd "$WORK_DIR/repo-a" && uv run dit log | head -2
cd "$WORK_DIR/repo-b" && uv run dit log | head -2

# repo-a 新增提交并推送（先行一步）
cd "$WORK_DIR/repo-a"
echo '{"messages": [{"role": "user", "content": "a side"}, {"role": "assistant", "content": "yes"}]}' >> train.jsonl
uv run dit add train.jsonl
uv run dit commit -m "a-side commit"
uv run dit push

# repo-b 从相同 base（v2）独立提交（注意：此时 repo-b 的 main 仍指向旧 hash）
cd "$WORK_DIR/repo-b"
echo '{"messages": [{"role": "user", "content": "b side"}, {"role": "assistant", "content": "yes"}]}' >> train.jsonl
uv run dit add train.jsonl
uv run dit commit -m "b-side diverged commit"
uv run dit push
```

验证清单：
- [ ] repo-a 的 push 成功
- [ ] repo-b 的 push 失败，退出码非 0
- [ ] 错误信息包含 `rejected`、`not a descendant` 或 `Pull first` 等提示

### 10.6 网络错误模拟

将远程 URL 改为不可达地址，验证错误处理：

```bash
cd "$WORK_DIR/repo-a"
uv run dit remote add badhost http://localhost:19999/testrepo
uv run dit push --remote badhost
```

验证清单：
- [ ] 命令失败，退出码非 0
- [ ] 输出包含连接错误（`Connection refused`、`connect error` 或类似内容）
- [ ] 不崩溃（无未捕获的 Python traceback）

清理：

```bash
uv run dit remote remove badhost
```

---

## 测试完成清理

```bash
# 删除服务端测试仓库（可选）
curl -s -X DELETE \
     -H "Authorization: token $ADMIN_TOKEN" \
     "http://localhost:8000/api/v1/repos/sft-demo"

curl -s -X DELETE \
     -H "Authorization: token $ADMIN_TOKEN" \
     "http://localhost:8000/api/v1/repos/empty-repo"

# 删除本地测试目录
rm -rf "$WORK_DIR"
echo "清理完成"
```

---

## 快速参考

| 命令 | 说明 |
|------|------|
| `dit remote add <name> <url> [--token <tok>]` | 添加远程，可选附带令牌 |
| `dit remote list` | 列出所有远程 |
| `dit remote remove <name>` | 删除远程 |
| `dit auth set-token <token> [--remote <name>]` | 为远程更新令牌 |
| `dit auth login --url <url> --token <token>` | 存储 Forgejo 凭据 |
| `dit push [--remote <r>] [--branch <b>]` | 推送指定分支（默认 origin/main） |
| `dit fetch [--remote <r>] [--branch <b>]` | 仅下载远程对象，不更新本地分支 |
| `dit pull [--remote <r>] [--branch <b>]` | 下载 + 快进合并 + 物化工作目录 |
| `dit clone <url> [dest] [--token <tok>] [--branch <b>]` | 克隆远程仓库到新目录 |

**远程配置存储位置**：`.datahub/config`（TOML 格式，`[remote.<name>]` 段落）

**push/pull 协议要点**：
- push 使用 CAS（Compare-And-Swap）更新 ref，并发推送时后者会被拒绝
- pull 仅支持快进（fast-forward）；若本地与远程分歧，需手动解决后再 pull
- 对象按 `rows → manifests → sidecars → blobs → trees → commits` 顺序上传，保证依赖完整性
