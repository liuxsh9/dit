# Dit 手动测试指南 02：分支管理与标签

本指南覆盖 Dit CLI（`dit`）的分支与标签工作流，包括：列出/创建/切换/删除分支、在分支上提交并验证数据隔离、创建/列出/删除标签，以及若干边界场景的验证。

---

## 目录

1. [前置条件](#1-前置条件)
2. [查看分支 — dit branch](#2-查看分支--dit-branch)
3. [创建分支](#3-创建分支)
4. [切换分支 — checkout 与 switch](#4-切换分支--checkout-与-switch)
5. [分支上提交与数据隔离验证](#5-分支上提交与数据隔离验证)
6. [删除分支 — dit branch -d](#6-删除分支--dit-branch--d)
7. [创建标签 — dit tag](#7-创建标签--dit-tag)
8. [列出标签](#8-列出标签)
9. [删除标签 — dit tag -d](#9-删除标签--dit-tag--d)
10. [边界场景](#10-边界场景)
11. [文件系统层验证速查](#11-文件系统层验证速查)

---

## 1. 前置条件

本指南建立在指南 01 的测试仓库基础上。读者应已完成指南 01 的全部步骤，并在 `$TEST_DIR` 中有一个包含 **至少 2 次提交** 的仓库。

### 1.1 确认已有仓库和提交历史

```bash
cd "$TEST_DIR"
uv run dit log
```

预期输出（至少两个 commit 块）：

```
commit <hash2>
Author: <用户名>
Date:   <时间戳> UTC

    <最近一次提交消息>

commit <hash1>
Author: <用户名>
Date:   <时间戳> UTC

    <最早一次提交消息>
```

验证清单：
- [ ] `$TEST_DIR` 存在且包含 `.dit/` 目录
- [ ] `dit log` 输出至少 2 个 commit 块，无错误
- [ ] `.dit/refs/heads/main` 文件存在，内容为 64 位十六进制哈希
- [ ] `dit status` 输出 "Nothing to commit, working directory clean."

### 1.2 若需要重建测试仓库

如果 `$TEST_DIR` 不再存在，执行以下步骤快速重建（使用最小数据集）：

```bash
export TEST_DIR=$(mktemp -d)
echo "测试目录：$TEST_DIR"
cd "$TEST_DIR"

uv run dit init

cat > "$TEST_DIR/train.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "什么是 Python 列表推导式？"}, {"role": "assistant", "content": "列表推导式是一种简洁创建列表的方式，语法为 `[expr for item in iterable if cond]`。"}]}
{"messages": [{"role": "user", "content": "Python 中 range(5) 产生哪些数？"}, {"role": "assistant", "content": "产生 0, 1, 2, 3, 4，共 5 个整数，不包含终止值 5。"}]}
EOF

uv run dit add .
uv run dit commit -m "初始训练数据：2条基础样本"

cat >> "$TEST_DIR/train.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "如何在 Python 中打开文件？"}, {"role": "assistant", "content": "使用 `with open('file.txt', 'r') as f:` 语句，`with` 块结束后文件自动关闭。"}]}
EOF

uv run dit add .
uv run dit commit -m "补充文件操作样本"
```

验证清单：
- [ ] `dit log` 输出 2 个 commit 块
- [ ] `train.jsonl` 有 3 行（`wc -l "$TEST_DIR/train.jsonl"` 输出 `3`）
- [ ] `dit status` 显示干净工作目录

---

## 2. 查看分支 — dit branch

### 操作

```bash
cd "$TEST_DIR"
uv run dit branch
```

### 预期输出

```
* main <8位哈希>
```

`*` 前缀标记当前所在分支，后跟该分支最新 commit 的前 8 位哈希。

### 验证方法

```bash
# 检查 HEAD 文件内容
cat "$TEST_DIR/.dit/HEAD"
# 预期：ref:main

# 检查分支引用文件
ls "$TEST_DIR/.dit/refs/heads/"
# 预期：main

cat "$TEST_DIR/.dit/refs/heads/main"
# 预期：64 位十六进制哈希
```

验证清单：
- [ ] `dit branch` 输出中 `main` 前有 `*`（当前分支标记）
- [ ] 输出的 8 位哈希与 `.dit/refs/heads/main` 文件内容前 8 位一致
- [ ] `.dit/HEAD` 内容为 `ref:main`
- [ ] `.dit/refs/heads/` 目录下只有 `main` 一个文件

---

## 3. 创建分支

### 3.1 使用 `dit branch <name>` 创建分支

#### 操作

```bash
cd "$TEST_DIR"
uv run dit branch feature-x
```

#### 预期输出

```
Created branch 'feature-x' at <8位哈希>.
```

#### 验证方法

```bash
# 列出所有分支
uv run dit branch
# 预期：* main 和 feature-x 均出现，* 仍在 main

# 检查分支引用文件是否已创建
ls "$TEST_DIR/.dit/refs/heads/"
cat "$TEST_DIR/.dit/refs/heads/feature-x"
```

验证清单：
- [ ] 输出含 "Created branch 'feature-x'"
- [ ] `dit branch` 列表中 `feature-x` 出现，但 `*` 仍标记 `main`（创建分支不自动切换）
- [ ] `.dit/refs/heads/feature-x` 文件存在
- [ ] `feature-x` 和 `main` 引用文件内容 **相同**（新分支从当前 HEAD 创建，指向同一 commit）

```bash
# 验证两个分支指向同一 commit
diff "$TEST_DIR/.dit/refs/heads/main" "$TEST_DIR/.dit/refs/heads/feature-x"
# 预期：无输出（内容相同）
```

验证清单（接续）：
- [ ] `diff` 命令无输出，即两个分支初始指向同一 commit

### 3.2 使用 `dit checkout -b <name>` 创建并切换分支

#### 操作

```bash
cd "$TEST_DIR"
uv run dit checkout -b feature-y
```

#### 预期输出

```
Switched to new branch 'feature-y'.
```

#### 验证方法

```bash
# 列出分支，验证 * 已移至 feature-y
uv run dit branch
# 预期：
#   feature-x <哈希>
# * feature-y <哈希>
#   main <哈希>

# 检查 HEAD 文件
cat "$TEST_DIR/.dit/HEAD"
# 预期：ref:feature-y
```

验证清单：
- [ ] 输出含 "Switched to new branch 'feature-y'"
- [ ] `dit branch` 中 `*` 标记在 `feature-y` 前
- [ ] `.dit/HEAD` 内容为 `ref:feature-y`
- [ ] `.dit/refs/heads/feature-y` 文件存在

### 3.3 创建已存在的分支（错误场景）

```bash
cd "$TEST_DIR"
uv run dit branch feature-x
echo "退出码: $?"
```

预期行为：
- 输出 `fatal: branch 'feature-x' already exists`
- 退出码非 0

验证清单：
- [ ] 输出含 "already exists"
- [ ] 退出码不为 0

---

## 4. 切换分支 — checkout 与 switch

> 当前状态：位于 `feature-y` 分支（接续 3.2）。

### 4.1 使用 `dit checkout` 切换到已有分支

#### 操作

```bash
cd "$TEST_DIR"
uv run dit checkout main
```

#### 预期输出

```
Switched to branch 'main'.
```

#### 验证方法

```bash
# 验证 HEAD 已更新
cat "$TEST_DIR/.dit/HEAD"
# 预期：ref:main

uv run dit branch
# 预期：* main
```

验证清单：
- [ ] 输出含 "Switched to branch 'main'"
- [ ] `.dit/HEAD` 内容变为 `ref:main`
- [ ] `dit branch` 中 `*` 已移至 `main`

### 4.2 使用 `dit switch` 切换分支

`dit switch` 与 `dit checkout`（不带 `-b`）功能相同，仅用于切换已存在的分支。

#### 操作

```bash
cd "$TEST_DIR"
uv run dit switch feature-x
```

#### 预期输出

```
Switched to branch 'feature-x'.
```

#### 验证方法

```bash
cat "$TEST_DIR/.dit/HEAD"
# 预期：ref:feature-x

uv run dit branch
# 预期：* feature-x
```

验证清单：
- [ ] 输出含 "Switched to branch 'feature-x'"
- [ ] `.dit/HEAD` 内容为 `ref:feature-x`

切换回 main，为后续步骤做准备：

```bash
uv run dit checkout main
cat "$TEST_DIR/.dit/HEAD"
# 预期：ref:main
```

验证清单：
- [ ] 切回 `main` 后 `.dit/HEAD` 内容恢复为 `ref:main`

---

## 5. 分支上提交与数据隔离验证

本节验证分支上的提交不影响其他分支（分支隔离），以及切换分支时工作目录文件会正确还原（文件物化）。

### 5.1 在 feature-x 上做一次新提交

#### 操作

先切换到 `feature-x`：

```bash
cd "$TEST_DIR"
uv run dit checkout feature-x
```

向数据集追加一条新样本：

```bash
cat >> "$TEST_DIR/train.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "Python 中 enumerate 函数的作用是什么？"}, {"role": "assistant", "content": "`enumerate(iterable)` 返回 (索引, 值) 对的迭代器，常用于需要索引的循环：`for i, v in enumerate(lst):`。"}]}
EOF

uv run dit add train.jsonl
uv run dit commit -m "feature-x：补充 enumerate 样本"
```

#### 预期输出

```
[feature-x <哈希>] feature-x：补充 enumerate 样本
```

#### 验证方法

```bash
# feature-x 的日志应比 main 多一个 commit
uv run dit log
```

验证清单：
- [ ] `commit` 输出含 `[feature-x <哈希>]`
- [ ] `dit log` 在 feature-x 上输出的 commit 数比 main 多 1（至少 3 条）
- [ ] `wc -l "$TEST_DIR/train.jsonl"` 在 feature-x 上输出 `4`

```bash
# 查看 feature-x 分支引用（已更新到新 commit）
cat "$TEST_DIR/.dit/refs/heads/feature-x"
# 与 main 分支引用不同
diff "$TEST_DIR/.dit/refs/heads/main" "$TEST_DIR/.dit/refs/heads/feature-x"
# 预期：有差异（两个分支现在指向不同 commit）
```

验证清单（接续）：
- [ ] `diff` 输出不为空，即两个分支现在指向不同 commit

### 5.2 切回 main，验证数据隔离

#### 操作

```bash
cd "$TEST_DIR"
uv run dit checkout main
```

#### 验证方法

```bash
# 工作目录中 train.jsonl 应还原为 3 行（main 分支的版本）
wc -l "$TEST_DIR/train.jsonl"
# 预期：3（feature-x 上新增的第 4 行不在 main 上）

# main 的日志应不含 feature-x 的 commit
uv run dit log
```

验证清单：
- [ ] `wc -l train.jsonl` 在 main 上输出 `3`（feature-x 的新增行不可见）
- [ ] `dit log` 在 main 上不含 "feature-x：补充 enumerate 样本" 这条消息
- [ ] `dit status` 显示 "Nothing to commit, working directory clean."

> **说明**：切换分支时，dit 会将工作目录文件还原为目标分支 HEAD commit 的快照（文件物化）。只存在于 feature-x 但不在 main 上的文件会被移除；main 上的版本会被写回磁盘。

### 5.3 验证 feature-x 上新增文件在 main 不可见

在 feature-x 上创建一个 main 没有的文件：

```bash
cd "$TEST_DIR"
uv run dit checkout feature-x

cat > "$TEST_DIR/feature-only.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "这是仅在 feature-x 分支存在的样本。"}, {"role": "assistant", "content": "仅在 feature-x 上存在。"}]}
EOF

uv run dit add feature-only.jsonl
uv run dit commit -m "feature-x：新增仅该分支存在的文件"
```

切回 main 并验证：

```bash
uv run dit checkout main

ls "$TEST_DIR/feature-only.jsonl" 2>/dev/null && echo "存在" || echo "不存在"
# 预期：不存在
```

验证清单：
- [ ] 切回 `main` 后 `feature-only.jsonl` 在工作目录中**不存在**
- [ ] `dit status` 显示干净工作目录（无 "new file: feature-only.jsonl"）

---

## 6. 删除分支 — dit branch -d

> 准备工作：确保当前在 `main` 分支（`cat .dit/HEAD` 应为 `ref:main`）。

### 6.1 删除已有分支

#### 操作

```bash
cd "$TEST_DIR"
uv run dit branch -d feature-y
```

#### 预期输出

```
Deleted branch 'feature-y'.
```

#### 验证方法

```bash
# feature-y 不再出现在分支列表中
uv run dit branch
# 预期：feature-x 和 main，无 feature-y

# 引用文件已删除
ls "$TEST_DIR/.dit/refs/heads/"
# 预期：feature-x  main（无 feature-y）
```

验证清单：
- [ ] 输出含 "Deleted branch 'feature-y'."
- [ ] `dit branch` 列表中 `feature-y` 已消失
- [ ] `.dit/refs/heads/feature-y` 文件**不存在**（`ls .dit/refs/heads/` 中无此文件）
- [ ] `.dit/refs/heads/main` 和 `feature-x` 文件仍然存在

### 6.2 删除不存在的分支（错误场景）

```bash
cd "$TEST_DIR"
uv run dit branch -d nonexistent
echo "退出码: $?"
```

预期行为：
- 输出 `error: branch 'nonexistent' not found`
- 退出码非 0

验证清单：
- [ ] 输出含 "not found"
- [ ] 退出码不为 0

---

## 7. 创建标签 — dit tag

> 以下操作在 `main` 分支上进行。

### 7.1 在当前 HEAD 上打标签

#### 操作

先记录当前 HEAD commit 哈希：

```bash
cd "$TEST_DIR"
HEAD_HASH=$(cat "$TEST_DIR/.dit/refs/heads/main")
echo "当前 main HEAD：$HEAD_HASH"

uv run dit tag v1.0
```

#### 预期输出

```
Created tag 'v1.0' at <8位哈希>.
```

#### 验证方法

```bash
# 检查标签引用文件已创建
ls "$TEST_DIR/.dit/refs/tags/"
# 预期：v1.0

cat "$TEST_DIR/.dit/refs/tags/v1.0"
# 预期：64 位十六进制哈希，与 HEAD_HASH 相同
```

```bash
# 验证标签指向的 commit 与当前 HEAD 一致
MAIN_HASH=$(cat "$TEST_DIR/.dit/refs/heads/main")
TAG_HASH=$(cat "$TEST_DIR/.dit/refs/tags/v1.0")
[ "$MAIN_HASH" = "$TAG_HASH" ] && echo "一致" || echo "不一致"
# 预期：一致
```

验证清单：
- [ ] 输出含 "Created tag 'v1.0' at"，8 位哈希与 HEAD 前 8 位一致
- [ ] `.dit/refs/tags/v1.0` 文件存在
- [ ] 文件内容与 `.dit/refs/heads/main` 相同（标签指向当前 HEAD commit）

### 7.2 再打一个标签（用于后续列出测试）

在 `feature-x` 分支上打一个标签：

```bash
cd "$TEST_DIR"
uv run dit checkout feature-x
uv run dit tag v2.0-beta
```

#### 预期输出

```
Created tag 'v2.0-beta' at <8位哈希>.
```

#### 验证方法

```bash
ls "$TEST_DIR/.dit/refs/tags/"
# 预期：v1.0  v2.0-beta

# v2.0-beta 指向 feature-x 的 HEAD，与 main 不同
FEATURE_HASH=$(cat "$TEST_DIR/.dit/refs/heads/feature-x")
TAG2_HASH=$(cat "$TEST_DIR/.dit/refs/tags/v2.0-beta")
[ "$FEATURE_HASH" = "$TAG2_HASH" ] && echo "一致" || echo "不一致"
# 预期：一致
```

切回 main：

```bash
uv run dit checkout main
```

验证清单：
- [ ] `.dit/refs/tags/v2.0-beta` 文件存在
- [ ] `v2.0-beta` 指向 `feature-x` 的 HEAD，内容与 `.dit/refs/heads/feature-x` 相同
- [ ] `v1.0` 和 `v2.0-beta` 指向**不同** commit（`diff .dit/refs/tags/v1.0 .dit/refs/tags/v2.0-beta` 有差异）

### 7.3 对同一名称重复打标签（错误场景）

```bash
cd "$TEST_DIR"
uv run dit tag v1.0
echo "退出码: $?"
```

预期行为：
- 输出 `fatal: tag 'v1.0' already exists`
- 退出码非 0

验证清单：
- [ ] 输出含 "already exists"
- [ ] 退出码不为 0

---

## 8. 列出标签

### 8.1 有标签时列出

#### 操作

```bash
cd "$TEST_DIR"
uv run dit tag
```

#### 预期输出

```
  v1.0     <8位哈希>
  v2.0-beta <8位哈希>
```

标签按字母顺序排列，每行格式为 `  <标签名> <8位哈希>`。

验证清单：
- [ ] 输出中 `v1.0` 出现
- [ ] 输出中 `v2.0-beta` 出现
- [ ] 退出码为 0

### 8.2 无标签时的输出

```bash
# 创建全新的仓库验证空标签列表
NOTAG_DIR=$(mktemp -d)
cd "$NOTAG_DIR"
uv run dit init

cat > "$NOTAG_DIR/data.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "测试"}, {"role": "assistant", "content": "测试回复"}]}
EOF
uv run dit add .
uv run dit commit -m "测试提交"

uv run dit tag
```

预期输出：
```
No tags.
```

验证清单：
- [ ] 输出为 "No tags."（大小写不敏感均可接受）
- [ ] 退出码为 0

```bash
rm -rf "$NOTAG_DIR"
cd "$TEST_DIR"
```

---

## 9. 删除标签 — dit tag -d

### 9.1 删除已有标签

#### 操作

```bash
cd "$TEST_DIR"
uv run dit tag -d v2.0-beta
```

#### 预期输出

```
Deleted tag 'v2.0-beta'.
```

#### 验证方法

```bash
uv run dit tag
# 预期：只剩 v1.0，v2.0-beta 不出现

ls "$TEST_DIR/.dit/refs/tags/"
# 预期：只有 v1.0
```

验证清单：
- [ ] 输出含 "Deleted tag 'v2.0-beta'."
- [ ] `dit tag` 列表中 `v2.0-beta` 已消失，`v1.0` 仍在
- [ ] `.dit/refs/tags/v2.0-beta` 文件**不存在**

### 9.2 删除不存在的标签（错误场景）

```bash
cd "$TEST_DIR"
uv run dit tag -d nonexistent
echo "退出码: $?"
```

预期行为：
- 输出 `error: tag 'nonexistent' not found`
- 退出码非 0

验证清单：
- [ ] 输出含 "not found"
- [ ] 退出码不为 0

---

## 10. 边界场景

### 10.1 切换到不存在的分支（错误场景）

```bash
cd "$TEST_DIR"
uv run dit checkout ghost-branch
echo "退出码: $?"
```

预期行为：
- 输出 `error: branch 'ghost-branch' not found`
- 退出码非 0

验证清单：
- [ ] 输出含 "not found"
- [ ] 退出码不为 0
- [ ] `.dit/HEAD` 内容未变（仍为 `ref:main`）

### 10.2 switch 到不存在的分支（错误场景）

```bash
cd "$TEST_DIR"
uv run dit switch ghost-branch
echo "退出码: $?"
```

预期行为：与 `checkout` 相同

验证清单：
- [ ] 输出含 "not found"
- [ ] 退出码不为 0

### 10.3 删除当前所在分支（错误场景）

当前位于 `main` 分支，尝试删除 `main`：

```bash
cd "$TEST_DIR"
# 确认当前在 main
cat "$TEST_DIR/.dit/HEAD"
# 预期：ref:main

uv run dit branch -d main
echo "退出码: $?"
```

预期行为：
- 输出 `error: cannot delete current branch 'main'`
- 退出码非 0

验证清单：
- [ ] 输出含 "cannot delete current branch"
- [ ] 退出码不为 0
- [ ] `.dit/refs/heads/main` 文件依然存在

### 10.4 有未提交修改时切换分支（错误场景）

直接修改工作目录，不 add 不 commit，然后尝试切换分支：

```bash
cd "$TEST_DIR"
# 确保在 main
uv run dit checkout main

# 修改文件但不暂存
echo '{"messages": [{"role": "user", "content": "临时修改，不提交"}, {"role": "assistant", "content": "测试"}]}' >> "$TEST_DIR/train.jsonl"

# 尝试切换到 feature-x
uv run dit checkout feature-x
echo "退出码: $?"
```

预期行为：
- 输出含 "uncommitted changes"（`error: working directory has uncommitted changes`）
- 退出码非 0
- 工作目录和 HEAD 均未改变

验证清单：
- [ ] 输出含 "uncommitted"（不区分大小写）
- [ ] 退出码不为 0
- [ ] `.dit/HEAD` 仍为 `ref:main`（未发生切换）

恢复工作目录（丢弃该临时修改）：

```bash
# 利用 checkout main 强制还原工作目录文件（此时 HEAD 已经是 main，不会拒绝）
# 注意：需要先使文件恢复干净状态；最简单的办法是用 HEAD 的 train.jsonl 覆盖
cd "$TEST_DIR"
uv run python3 - << 'PYEOF'
from pathlib import Path
from dit.core.store import ObjectStore
from dit.core.objects import deserialize_commit, deserialize_manifest
from dit.core.refs import RefStore
from dit.core.tree_walker import flatten_tree
from dit.core.workspace import materialize_file

dot = Path(".dit")
store = ObjectStore(dot / "objects")
refs = RefStore(dot)

h = refs.resolve_head()
commit = deserialize_commit(store.read("commits", h))
flat = flatten_tree(store, commit.tree_hash)
for path, (obj_type, obj_hash, _) in flat.items():
    if obj_type == "manifest":
        m_data = store.read("manifests", obj_hash)
        manifest = deserialize_manifest(m_data)
        materialize_file(Path("."), path, manifest, store)
        print(f"已还原：{path}")
PYEOF
```

验证清单（接续）：
- [ ] `dit status` 恢复为 "Nothing to commit, working directory clean."

### 10.5 有暂存内容时切换分支（错误场景）

```bash
cd "$TEST_DIR"
# 确保在 main 且工作目录干净
uv run dit status

# 创建并暂存新文件，但不提交
cat > "$TEST_DIR/staged-only.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "已暂存但未提交的样本"}, {"role": "assistant", "content": "测试"}]}
EOF

uv run dit add staged-only.jsonl

# 尝试切换分支
uv run dit checkout feature-x
echo "退出码: $?"
```

预期行为：
- 输出 `error: staging area is not empty — please commit or reset first`
- 退出码非 0

验证清单：
- [ ] 输出含 "staging area is not empty"
- [ ] 退出码不为 0
- [ ] `.dit/HEAD` 仍为 `ref:main`

清理暂存区：

```bash
echo '{}' > "$TEST_DIR/.dit/index"
rm "$TEST_DIR/staged-only.jsonl"
uv run dit status
# 预期：Nothing to commit, working directory clean.
```

### 10.6 无提交时打标签（错误场景）

```bash
EMPTY_TAG_DIR=$(mktemp -d)
cd "$EMPTY_TAG_DIR"
uv run dit init
uv run dit tag v0.0
echo "退出码: $?"
```

预期行为：
- 输出 `fatal: no commits yet`
- 退出码非 0

验证清单：
- [ ] 输出含 "no commits yet"
- [ ] 退出码不为 0
- [ ] `.dit/refs/tags/` 目录下无任何文件

```bash
rm -rf "$EMPTY_TAG_DIR"
cd "$TEST_DIR"
```

---

## 11. 文件系统层验证速查

本节汇总了关键 refs 文件的路径与预期内容，方便在测试过程中随时手动核查底层存储状态。

### 11.1 .dit/refs/ 目录结构

```
.dit/
└── refs/
    ├── heads/          ← 分支引用目录
    │   ├── main        ← main 分支 HEAD commit 哈希（64 位十六进制 + 换行）
    │   ├── feature-x   ← feature-x 分支 HEAD commit 哈希
    │   └── ...         ← 其他分支
    └── tags/           ← 标签引用目录
        ├── v1.0        ← v1.0 标签指向的 commit 哈希（64 位十六进制 + 换行）
        └── ...         ← 其他标签
```

### 11.2 快速验证命令汇总

```bash
# 查看当前所在分支（HEAD 内容）
cat "$TEST_DIR/.dit/HEAD"

# 列出所有分支引用文件
ls -1 "$TEST_DIR/.dit/refs/heads/"

# 查看某分支指向的完整 commit 哈希
cat "$TEST_DIR/.dit/refs/heads/main"
cat "$TEST_DIR/.dit/refs/heads/feature-x"

# 列出所有标签引用文件
ls -1 "$TEST_DIR/.dit/refs/tags/"

# 查看某标签指向的完整 commit 哈希
cat "$TEST_DIR/.dit/refs/tags/v1.0"

# 验证两个引用是否指向同一 commit（用于验证分支/标签创建后的初始状态）
diff "$TEST_DIR/.dit/refs/heads/main" "$TEST_DIR/.dit/refs/tags/v1.0"
```

### 11.3 用 Python 内省 refs 状态

```bash
cd "$TEST_DIR"
uv run python3 - << 'PYEOF'
from pathlib import Path
from dit.core.refs import RefStore

dot = Path(".dit")
refs = RefStore(dot)

print(f"HEAD          : {refs.get_head()}")
print(f"当前分支      : {refs.current_branch()}")
print(f"当前分支哈希  : {refs.resolve_head()}")

print("\n全部分支：")
for name, h in sorted(refs.list_branches().items()):
    marker = "*" if name == refs.current_branch() else " "
    print(f"  {marker} {name:<20} {h[:16]}...")

print("\n全部标签：")
tags = refs.list_tags()
if tags:
    for name, h in sorted(tags.items()):
        print(f"    {name:<20} {h[:16]}...")
else:
    print("    （无标签）")
PYEOF
```

预期输出示例（执行本指南全部步骤后）：

```
HEAD          : ref:main
当前分支      : main
当前分支哈希  : <64位哈希>

全部分支：
  * main                 <哈希前16位>...
    feature-x            <哈希前16位>...

全部标签：
    v1.0                 <哈希前16位>...
```

验证清单：
- [ ] `HEAD` 格式为 `ref:<分支名>`（符号引用，非裸哈希）
- [ ] 当前分支名称正确
- [ ] 所有分支均显示，`*` 标记当前分支
- [ ] 标签列表与 `dit tag` 命令输出一致

---

## 清理

测试完成后，可删除测试目录：

```bash
rm -rf "$TEST_DIR"
```

---

*上一篇：[01 - 本地操作基础](./01-local-operations.md)*  
*下一篇：[03 - 远程协作](./03-remote-collaboration.md)*
