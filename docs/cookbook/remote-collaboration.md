# 远程协作

Dit 支持通过 HTTP 协议与远程服务端同步数据仓库。本指南介绍从配置远程、认证、推送到多人协作拉取的完整流程。

## 核心概念

- **远程（remote）**：指向 dit-core 服务端上某个仓库的 URL，格式为 `http://server:8000/repo-name`
- **令牌（token）**：访问远程仓库的凭据，服务端通过令牌识别身份和权限
- **角色层级**：reader < reviewer < committer < maintainer < admin < owner，权限逐级递增。reader 只能拉取，committer 可以推送，admin 可以管理令牌和分支保护规则

## 完整工作流

### 1. 初始化本地仓库

```bash
dit init
cat > train.jsonl << 'EOF'
{"messages": [{"role": "user", "content": "你好"}, {"role": "assistant", "content": "你好！有什么可以帮你的？"}]}
EOF
dit add train.jsonl
dit commit -m "初始训练数据"
```

### 2. 添加远程并设置令牌

添加远程时可以直接附带令牌：

```bash
dit remote add origin http://server:8000/my-dataset --token "dit_xxxxxxxxxxxx"
```

也可以先添加远程，再单独设置令牌：

```bash
dit remote add origin http://server:8000/my-dataset
dit auth set-token "dit_xxxxxxxxxxxx"
```

`auth set-token` 默认作用于 `origin`，如需指定其他远程：

```bash
dit auth set-token "dit_xxxxxxxxxxxx" --remote backup
```

### 3. 查看和管理远程

```bash
# 列出所有远程
dit remote list
# 输出：
# origin	http://server:8000/my-dataset

# 删除远程
dit remote remove origin

# 添加多个远程
dit remote add origin http://server:8000/my-dataset --token "tok_a"
dit remote add backup http://backup:8000/my-dataset --token "tok_b"
```

### 4. 推送到远程

首次推送前，需要在服务端创建仓库（通过 Web UI 或 API）。然后：

```bash
dit push
```

输出示例：

```
Pushed 5 new objects to origin/main (a1b2c3d4)
```

推送指定分支到指定远程：

```bash
dit push --remote origin --branch feature/new-data
```

重复推送是幂等的，已存在的对象不会重复上传。

### 5. 在另一台机器上克隆

```bash
dit clone http://server:8000/my-dataset ./local-copy --token "dit_xxxxxxxxxxxx"
```

输出示例：

```
Cloning http://server:8000/my-dataset -> ./local-copy
  train.jsonl
Clone complete. 1 commit(s).
```

克隆指定分支：

```bash
dit clone http://server:8000/my-dataset ./local-copy \
    --token "dit_xxxxxxxxxxxx" --branch feature/new-data
```

### 6. 拉取更新

当其他协作者推送了新提交后，拉取最新变更：

```bash
dit pull
```

输出示例：

```
Pulled 3 new objects. Now at e5f6a7b8.
```

如果本地已是最新：

```
Already up to date.
```

也可以只下载对象而不更新工作目录（类似 git fetch）：

```bash
dit fetch
```

## 多人协作示例

假设 A 和 B 两位标注员协作维护同一个数据集：

```bash
# A：推送初始数据
dit push

# B：克隆仓库
dit clone http://server:8000/sft-data ./sft-data --token "tok_b"

# B：修改数据并推送
cd sft-data
# ... 编辑 train.jsonl ...
dit add train.jsonl
dit commit -m "新增 50 条对话数据"
dit push

# A：拉取 B 的更新
dit pull
```

如果 A 和 B 同时基于相同版本各自提交并推送，后推送的一方会被拒绝：

```
error: push rejected — remote ref is not a descendant. Pull first.
```

此时需要先 `dit pull` 合并远程变更，再重新推送。

## 注意事项

- 远程配置存储在 `.dit/config` 文件中（TOML 格式），令牌以明文保存，注意文件权限
- `dit remote list` 只显示名称和 URL，不会输出令牌
- push 使用 CAS（Compare-And-Swap）更新引用，保证并发安全
- pull 仅支持快进合并（fast-forward），分歧场景需要先合并再推送
- 无令牌或令牌错误时，push/pull/clone 会返回认证失败错误，不会静默失败
- 如果部署了 dit-gateway，可以用 `dit auth login --url http://server:3000 --token "forgejo_token"` 存储网关凭据
