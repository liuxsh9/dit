# Dit 手动测试指南 04：合并与冲突解决

本指南覆盖 Dit CLI（`dit`）的合并工作流，包括：快进合并、三方合并、冲突检测与解决、中止合并、Cherry-pick，以及文件级冲突和边界场景。

> **前置要求**：已完成指南 01（本地操作）和指南 02（分支与标签）的测试，熟悉 `dit init / add / commit / branch / checkout` 的基本用法。

---

## 目录

1. [前置条件 — 初始化测试环境](#1-前置条件--初始化测试环境)
2. [快进合并 (Fast-forward merge)](#2-快进合并-fast-forward-merge)
3. [三方合并 (Three-way merge)](#3-三方合并-three-way-merge)
4. [冲突合并 (Conflict merge)](#4-冲突合并-conflict-merge)
5. [冲突解决 (Conflict resolution)](#5-冲突解决-conflict-resolution)
6. [中止合并 (Abort merge)](#6-中止合并-abort-merge)
7. [Cherry-pick — 摘取单个提交](#7-cherry-pick--摘取单个提交)
8. [Cherry-pick 冲突](#8-cherry-pick-冲突)
9. [文件级冲突场景](#9-文件级冲突场景)
10. [边界场景](#10-边界场景)

---

## 1. 前置条件 — 初始化测试环境

### 1.1 创建测试目录

```bash
export MERGE_TEST=$(mktemp -d)
echo "测试目录：$MERGE_TEST"
cd "$MERGE_TEST"
```

### 1.2 初始化仓库并写入基础数据

本节将建立一个具有"分叉历史"的仓库，作为后续所有测试的起点。

```bash
dit init
```

**写入基础训练文件 `train.jsonl`（3 条样本，代表合并基点数据）：**

```bash
cat > "$MERGE_TEST/train.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "什么是 Python 的 GIL？"}, {"role": "assistant", "content": "GIL（全局解释器锁）是 CPython 的互斥锁，同一时刻只允许一个线程执行 Python 字节码。"}]}
{"messages": [{"role": "user", "content": "解释 Python 的装饰器"}, {"role": "assistant", "content": "装饰器是一个可调用对象，接受一个函数并返回修改后的函数，常用 @syntax 语法糖。"}]}
{"messages": [{"role": "user", "content": "什么是生成器？"}, {"role": "assistant", "content": "生成器是使用 yield 语句的函数，每次调用 next() 时才产生下一个值，节省内存。"}]}
EOF
```

**提交基础数据：**

```bash
dit add train.jsonl
dit commit -m "initial: 3 Python QA rows"
```

验证清单：
- [ ] `dit log` 显示一条 `initial: 3 Python QA rows` 提交
- [ ] 工作目录有 `train.jsonl`，内容 3 行

---

## 2. 快进合并 (Fast-forward merge)

**适用场景**：目标分支（main）没有在源分支分叉后做任何新提交，源分支是目标分支的后裔。dit 直接移动分支指针，不创建合并提交。

### 2.1 创建 feature 分支并添加新数据

```bash
dit checkout -b feature/ff-demo
```

**向 `train.jsonl` 追加一条新样本（feature 分支独有）：**

```bash
cat >> "$MERGE_TEST/train.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "Python 中如何实现单例模式？"}, {"role": "assistant", "content": "可以用模块级变量、__new__ 方法或装饰器实现。最简单的是使用模块本身，Python 模块天然是单例。"}]}
EOF
```

```bash
dit add train.jsonl
dit commit -m "feature: add singleton QA"
```

记录 feature 分支的提交哈希：

```bash
FEATURE_HASH=$(dit log --oneline | head -1 | awk '{print $1}')
echo "feature 分支最新提交：$FEATURE_HASH"
```

### 2.2 切回 main 并执行快进合并

```bash
dit checkout main
dit merge feature/ff-demo
```

预期输出（含 `fast-forward`，大小写不限）：

```
Fast-forward to <hash>.
```

### 2.3 验证快进合并结果

```bash
dit log --oneline
```

验证清单：
- [ ] 输出包含 `Fast-forward`
- [ ] `main` 分支 HEAD 与 `feature/ff-demo` HEAD 相同（没有创建新的合并提交）
- [ ] `train.jsonl` 共 4 行，第 4 行含 `单例模式`
- [ ] `dit log` 显示 2 条提交，没有带两个父节点的合并提交

**检查 main 指针是否已移动到 feature 哈希：**

```bash
cat "$MERGE_TEST/.dit/refs/heads/main"
# 应与 $FEATURE_HASH 相同
```

- [ ] `main` 指针等于 `$FEATURE_HASH`

---

## 3. 三方合并 (Three-way merge)

**适用场景**：两个分支从同一基点独立发展，修改的是**不同文件**（或同一文件中不同的行），没有冲突。dit 创建一个有两个父节点的合并提交。

### 3.1 构建分叉历史

重新初始化一个干净测试目录（避免与上一节状态混淆）：

```bash
export THREEWAY_DIR=$(mktemp -d)
cd "$THREEWAY_DIR"
dit init
```

**写入共同基础文件：**

```bash
cat > "$THREEWAY_DIR/base.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "什么是 REST API？"}, {"role": "assistant", "content": "REST 是一种基于 HTTP 的架构风格，使用标准方法（GET/POST/PUT/DELETE）操作资源。"}]}
EOF
dit add base.jsonl
dit commit -m "base: REST API QA"
```

**创建 feature 分支，添加 `feature.jsonl`：**

```bash
dit checkout -b feature/new-data
```

```bash
cat > "$THREEWAY_DIR/feature.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "解释 HTTP 状态码 404 和 500"}, {"role": "assistant", "content": "404 表示资源未找到，500 表示服务器内部错误。两者的区别在于前者是客户端错误，后者是服务端错误。"}]}
EOF
dit add feature.jsonl
dit commit -m "feature: add HTTP status codes QA"
```

**切回 main，添加不同的 `main-extra.jsonl`：**

```bash
dit checkout main
```

```bash
cat > "$THREEWAY_DIR/main-extra.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "什么是 JSON Web Token？"}, {"role": "assistant", "content": "JWT 是一种用于在网络应用间安全传递信息的开放标准，由 Header、Payload 和 Signature 三部分组成。"}]}
EOF
dit add main-extra.jsonl
dit commit -m "main: add JWT QA"
```

查看分叉图示（此时 main 和 feature 各有一个 base 之后的提交）：

```bash
dit log --oneline
```

### 3.2 执行三方合并

```bash
dit merge feature/new-data
```

预期输出：

```
Merge made: [main <hash>] Merge branch 'feature/new-data' into main
```

### 3.3 验证三方合并结果

```bash
ls "$THREEWAY_DIR"
```

验证清单：
- [ ] 输出包含 `Merge made`（而非 `Fast-forward`）
- [ ] 工作目录同时存在 `base.jsonl`、`feature.jsonl`、`main-extra.jsonl`
- [ ] `dit log --oneline` 最新一条是合并提交，消息含 `Merge branch`
- [ ] `.dit/MERGE_HEAD` 文件**不存在**（合并已完成，状态文件已清理）

**验证合并提交有两个父节点：**

```bash
dit log --oneline
# 复制顶部 merge commit 的 hash（前 8 位），下面替换 <MERGE_HASH>
```

> dit 的 `log` 命令目前不直接显示父节点列表；验证父节点数量可通过查看 `.dit/objects/commits/` 下对应文件的内容，或运行集成测试。日常手动验证看 `Merge made` 即可。

---

## 4. 冲突合并 (Conflict merge)

**适用场景**：两个分支对**同一文件的同一行（相同 query_fingerprint）**给出了不同的答案（不同 row_hash），dit 无法自动决定保留哪一个，触发冲突。

> **核心原理**：dit 使用行级三方合并。每条 JSONL 记录的 `query_fingerprint` 由 user turn 内容决定。若 ours 和 theirs 都以不同内容"刷新"了同一个 query_fingerprint 对应的行，则产生 `both_modified` 冲突。

### 4.1 构建冲突场景

```bash
export CONFLICT_DIR=$(mktemp -d)
cd "$CONFLICT_DIR"
dit init
```

**写入共同基础（1 条样本，只有 user turn，没有 assistant turn）：**

```bash
cat > "$CONFLICT_DIR/data.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "用一句话描述递归"}]}
EOF
dit add data.jsonl
dit commit -m "base: user-only row"
```

**创建 feature 分支，给出 feature 的回答：**

```bash
dit checkout -b feature/answer-A
```

```bash
cat > "$CONFLICT_DIR/data.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "用一句话描述递归"}, {"role": "assistant", "content": "递归是函数在执行过程中调用自身的编程技术，通常需要基本情况（base case）来终止。"}]}
EOF
dit add data.jsonl
dit commit -m "feature: answer A for recursion"
```

**切回 main，给出不同的回答：**

```bash
dit checkout main
```

```bash
cat > "$CONFLICT_DIR/data.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "用一句话描述递归"}, {"role": "assistant", "content": "递归是解决问题的方法，将大问题分解为结构相同的小问题，直到达到可直接求解的最小规模。"}]}
EOF
dit add data.jsonl
dit commit -m "main: answer B for recursion"
```

### 4.2 触发冲突

```bash
dit merge feature/answer-A
```

预期输出（exit code 非 0）：

```
CONFLICT: 1 file(s) have conflicts.
  data.jsonl (both_modified)

Resolve conflicts, then: dit add <files> && dit merge --continue
```

### 4.3 验证冲突状态文件

冲突发生后，dit 在 `.dit/` 目录写入以下状态文件：

```bash
ls "$CONFLICT_DIR/.dit/"
```

验证清单：
- [ ] `.dit/MERGE_HEAD` 存在，内容为 feature 分支的提交哈希
- [ ] `.dit/MERGE_MSG` 存在，内容为默认合并消息
- [ ] `.dit/conflicts.json` 存在

**查看 conflicts.json 内容：**

```bash
cat "$CONFLICT_DIR/.dit/conflicts.json"
```

预期结构：

```json
{
  "base_commit": "<base_hash>",
  "ours_commit": "<main_hash>",
  "theirs_commit": "<feature_hash>",
  "conflicts": [
    {
      "file_path": "data.jsonl",
      "conflict_type": "both_modified"
    }
  ]
}
```

验证清单：
- [ ] `conflict_type` 为 `both_modified`
- [ ] `file_path` 为 `data.jsonl`

---

## 5. 冲突解决 (Conflict resolution)

继续上一节的冲突状态。

### 5.1 查看当前冲突文件内容

冲突发生后，dit 将**部分合并结果**写入工作目录（非冲突行已合并，冲突行以 ours 状态保留）：

```bash
cat "$CONFLICT_DIR/data.jsonl"
```

此时文件内容是 main 分支（ours）的版本。需要人工编辑，选择保留哪个答案，或合并两个答案。

### 5.2 手动解决冲突——选择最终内容

将两个答案合并为一个更好的版本：

```bash
cat > "$CONFLICT_DIR/data.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "用一句话描述递归"}, {"role": "assistant", "content": "递归是函数调用自身的技术，通过将大问题分解为同构的小问题来求解，必须有终止条件（base case）避免无限递归。"}]}
EOF
```

### 5.3 将解决后的文件加入暂存区

```bash
dit add data.jsonl
dit status
```

验证清单：
- [ ] `status` 显示 `data.jsonl` 处于已暂存（staged）状态

### 5.4 完成合并

```bash
dit merge --continue
```

预期输出：

```
[main <hash>] Merge branch 'feature/answer-A' into main
```

### 5.5 验证合并完成

```bash
ls "$CONFLICT_DIR/.dit/"
dit log --oneline
cat "$CONFLICT_DIR/data.jsonl"
```

验证清单：
- [ ] `.dit/MERGE_HEAD` 已删除
- [ ] `.dit/MERGE_MSG` 已删除
- [ ] `.dit/conflicts.json` 已删除
- [ ] `dit log` 最新提交为合并提交（含 `Merge branch`）
- [ ] `data.jsonl` 包含解决后的内容（含 `终止条件` 或你手动写入的内容）

**尝试在没有暂存文件的情况下执行 --continue（应当报错）：**

```bash
# 先重新触发一个冲突状态用于测试（可跳过，仅用于确认错误提示）
# 若不想额外操作，直接验证以下命令在当前目录（无冲突）下的输出
dit merge --continue
```

- [ ] 报错：`error: no merge in progress`

---

## 6. 中止合并 (Abort merge)

**适用场景**：发现合并方向错误，或暂时不想处理冲突，用 `--abort` 将工作目录和分支状态恢复到合并前。

### 6.1 重新触发冲突

```bash
export ABORT_DIR=$(mktemp -d)
cd "$ABORT_DIR"
dit init
```

```bash
cat > "$ABORT_DIR/data.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "什么是闭包？"}]}
EOF
dit add data.jsonl
dit commit -m "base"
```

```bash
dit checkout -b feature/closure
cat > "$ABORT_DIR/data.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "什么是闭包？"}, {"role": "assistant", "content": "闭包是引用了外部函数作用域变量的内部函数，即使外部函数已返回，这些变量仍然有效。(feature版)"}]}
EOF
dit add data.jsonl
dit commit -m "feature: closure answer"
```

```bash
dit checkout main
cat > "$ABORT_DIR/data.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "什么是闭包？"}, {"role": "assistant", "content": "闭包让函数记住并访问其词法作用域，即便函数在词法作用域外执行。(main版)"}]}
EOF
dit add data.jsonl
dit commit -m "main: closure answer"
```

```bash
dit merge feature/closure
```

- [ ] 输出含 `CONFLICT`，退出码非 0

### 6.2 记录冲突前的文件内容

```bash
# 记录 main 分支在合并前的内容
BEFORE_MERGE="闭包让函数记住并访问其词法作用域"
cat "$ABORT_DIR/data.jsonl"
```

### 6.3 执行 abort

```bash
dit merge --abort
```

预期输出：

```
Merge aborted.
```

### 6.4 验证状态已恢复

```bash
ls "$ABORT_DIR/.dit/"
cat "$ABORT_DIR/data.jsonl"
dit status
```

验证清单：
- [ ] `.dit/MERGE_HEAD` 已删除
- [ ] `.dit/MERGE_MSG` 已删除
- [ ] `.dit/conflicts.json` 已删除
- [ ] `data.jsonl` 内容回到 main 分支的版本（含 `main版`）
- [ ] `dit status` 显示工作目录干净

**在没有合并进行时执行 abort（应当报错）：**

```bash
dit merge --abort
```

- [ ] 报错：`error: no merge in progress`

---

## 7. Cherry-pick — 摘取单个提交

**适用场景**：只想把某个分支上的某一个提交的改动应用到当前分支，而不合并整个分支。Cherry-pick 以目标提交的父节点为基点，做三方合并，创建一个新的单父提交（不是合并提交）。

### 7.1 构建 cherry-pick 环境

```bash
export CP_DIR=$(mktemp -d)
cd "$CP_DIR"
dit init
```

**初始数据：**

```bash
cat > "$CP_DIR/data.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "什么是哈希表？"}, {"role": "assistant", "content": "哈希表是通过哈希函数将键映射到存储位置的数据结构，理想情况下查找、插入、删除均为 O(1)。"}]}
EOF
dit add data.jsonl
dit commit -m "base: hash table QA"
```

**在 experiment 分支上做两个提交：**

```bash
dit checkout -b experiment
```

提交 A — 添加新文件：

```bash
cat > "$CP_DIR/new-algo.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "解释 Dijkstra 算法"}, {"role": "assistant", "content": "Dijkstra 算法用于求单源最短路径，使用优先队列（最小堆）逐步松弛边，时间复杂度为 O((V+E)logV)。"}]}
EOF
dit add new-algo.jsonl
dit commit -m "experiment: add Dijkstra QA"
```

记录这个提交哈希（后面要 cherry-pick 它）：

```bash
PICK_HASH=$(dit log --oneline | head -1 | awk '{print $1}')
echo "要 cherry-pick 的提交：$PICK_HASH"
```

提交 B — 在 experiment 分支继续做不相关改动：

```bash
cat > "$CP_DIR/extra.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "什么是 A* 算法？"}, {"role": "assistant", "content": "A* 是启发式搜索算法，结合了 Dijkstra 的最短路径保证和贪心最佳优先搜索的效率，使用 f(n)=g(n)+h(n) 评估节点。"}]}
EOF
dit add extra.jsonl
dit commit -m "experiment: add A-star QA"
```

### 7.2 切回 main 并执行 cherry-pick

```bash
dit checkout main
```

确认 main 上没有 `new-algo.jsonl`：

```bash
ls "$CP_DIR/"
```

- [ ] `new-algo.jsonl` 不存在（只有 `data.jsonl`）

**执行 cherry-pick：**

```bash
dit cherry-pick "$PICK_HASH"
```

预期输出：

```
[main <hash>] cherry-pick: experiment: add Dijkstra QA
```

### 7.3 验证 cherry-pick 结果

```bash
ls "$CP_DIR/"
cat "$CP_DIR/new-algo.jsonl"
dit log --oneline
```

验证清单：
- [ ] `new-algo.jsonl` 现在存在于 main 分支
- [ ] `extra.jsonl` **不存在**（只摘取了提交 A，不包括提交 B）
- [ ] `dit log` 最新提交消息含 `cherry-pick`
- [ ] 最新提交只有 **1 个父节点**（cherry-pick 不创建合并提交）

**确认 cherry-pick 状态文件已清理：**

```bash
ls "$CP_DIR/.dit/"
```

- [ ] `.dit/CHERRY_PICK_HEAD` 不存在

---

## 8. Cherry-pick 冲突

**适用场景**：要 cherry-pick 的提交与当前分支修改了相同的行，触发冲突。

### 8.1 构建 cherry-pick 冲突场景

```bash
export CP_CONFLICT_DIR=$(mktemp -d)
cd "$CP_CONFLICT_DIR"
dit init
```

```bash
cat > "$CP_CONFLICT_DIR/data.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "什么是 TCP/IP？"}]}
EOF
dit add data.jsonl
dit commit -m "base"
```

**feature 分支做修改（这个提交是我们想 cherry-pick 的）：**

```bash
dit checkout -b feature/tcp
cat > "$CP_CONFLICT_DIR/data.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "什么是 TCP/IP？"}, {"role": "assistant", "content": "TCP/IP 是互联网的核心协议族，TCP 提供可靠传输，IP 负责寻址和路由。(feature版)"}]}
EOF
dit add data.jsonl
dit commit -m "feature: TCP/IP answer"
PICK_HASH=$(dit log --oneline | head -1 | awk '{print $1}')
echo "cherry-pick 目标：$PICK_HASH"
```

**回到 main，也修改同一行（制造冲突）：**

```bash
dit checkout main
cat > "$CP_CONFLICT_DIR/data.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "什么是 TCP/IP？"}, {"role": "assistant", "content": "TCP/IP 协议栈分四层：应用层、传输层、网络层、链路层，每层有明确的职责分工。(main版)"}]}
EOF
dit add data.jsonl
dit commit -m "main: TCP/IP answer"
```

### 8.2 触发 cherry-pick 冲突

```bash
dit cherry-pick "$PICK_HASH"
```

预期输出（exit code 非 0）：

```
CONFLICT: 1 file(s) have conflicts.
  data.jsonl (both_modified)

Resolve conflicts, then: dit add <files> && dit cherry-pick --continue
```

验证清单：
- [ ] `.dit/CHERRY_PICK_HEAD` 存在（内容为目标提交哈希）
- [ ] `.dit/MERGE_HEAD` **不存在**（cherry-pick 使用独立状态文件）
- [ ] `.dit/conflicts.json` 存在

```bash
ls "$CP_CONFLICT_DIR/.dit/"
cat "$CP_CONFLICT_DIR/.dit/conflicts.json"
```

### 8.3 解决冲突并继续

```bash
cat > "$CP_CONFLICT_DIR/data.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "什么是 TCP/IP？"}, {"role": "assistant", "content": "TCP/IP 是互联网核心协议族，分四层架构（应用/传输/网络/链路），TCP 提供可靠传输，IP 负责寻址路由。(resolved)"}]}
EOF
dit add data.jsonl
dit cherry-pick --continue
```

预期输出：

```
[main <hash>] cherry-pick: feature: TCP/IP answer
```

验证清单：
- [ ] `.dit/CHERRY_PICK_HEAD` 已删除
- [ ] `data.jsonl` 包含解决后的内容
- [ ] `dit log` 最新提交含 `cherry-pick`

### 8.4 测试 cherry-pick --abort

重新触发冲突（快速复现）：

```bash
export CP_ABORT_DIR=$(mktemp -d)
cd "$CP_ABORT_DIR"
dit init

cat > "$CP_ABORT_DIR/data.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "什么是虚拟内存？"}]}
EOF
dit add data.jsonl
dit commit -m "base"

dit checkout -b feature/vm
cat > "$CP_ABORT_DIR/data.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "什么是虚拟内存？"}, {"role": "assistant", "content": "虚拟内存是操作系统抽象层，让每个进程拥有独立的地址空间，通过页面置换算法管理物理内存与磁盘的换入换出。(feature)"}]}
EOF
dit add data.jsonl
dit commit -m "feature: virtual memory"
VM_PICK_HASH=$(dit log --oneline | head -1 | awk '{print $1}')

dit checkout main
cat > "$CP_ABORT_DIR/data.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "什么是虚拟内存？"}, {"role": "assistant", "content": "虚拟内存允许程序使用比物理内存更大的地址空间，通过将不活跃页面换出到磁盘来实现。(main)"}]}
EOF
dit add data.jsonl
dit commit -m "main: virtual memory"

dit cherry-pick "$VM_PICK_HASH"
```

- [ ] 触发冲突，`.dit/CHERRY_PICK_HEAD` 存在

```bash
dit cherry-pick --abort
```

预期输出：

```
Cherry-pick aborted.
```

```bash
cat "$CP_ABORT_DIR/data.jsonl"
ls "$CP_ABORT_DIR/.dit/"
```

验证清单：
- [ ] 输出 `Cherry-pick aborted.`
- [ ] `data.jsonl` 内容恢复为 main 分支版本（含 `main`）
- [ ] `.dit/CHERRY_PICK_HEAD` 已删除

---

## 9. 文件级冲突场景

本节测试文件级别的三方合并判断逻辑，不涉及行级内容比较。

### 9.1 场景一：两个分支各添加不同文件（无冲突）

```bash
export FILE_CONFLICT_DIR=$(mktemp -d)
cd "$FILE_CONFLICT_DIR"
dit init

cat > "$FILE_CONFLICT_DIR/shared.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "什么是微服务？"}, {"role": "assistant", "content": "微服务是将单体应用拆分为多个小型、独立部署的服务的架构风格。"}]}
EOF
dit add shared.jsonl
dit commit -m "base: microservices"
```

feature 分支添加 `feature-only.jsonl`：

```bash
dit checkout -b feature/add-file
cat > "$FILE_CONFLICT_DIR/feature-only.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "什么是 Docker？"}, {"role": "assistant", "content": "Docker 是容器化平台，将应用及其依赖打包到轻量级容器中，确保环境一致性。"}]}
EOF
dit add feature-only.jsonl
dit commit -m "feature: add Docker QA"
```

main 分支添加 `main-only.jsonl`：

```bash
dit checkout main
cat > "$FILE_CONFLICT_DIR/main-only.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "什么是 Kubernetes？"}, {"role": "assistant", "content": "Kubernetes（K8s）是容器编排系统，用于自动化容器的部署、扩缩容和管理。"}]}
EOF
dit add main-only.jsonl
dit commit -m "main: add K8s QA"
```

```bash
dit merge feature/add-file
```

验证清单：
- [ ] 合并**无冲突**，输出 `Merge made`
- [ ] 工作目录同时存在 `shared.jsonl`、`feature-only.jsonl`、`main-only.jsonl`

### 9.2 场景二：一个分支删除文件，另一个分支不动（无冲突）

```bash
export DEL_DIR=$(mktemp -d)
cd "$DEL_DIR"
dit init

cat > "$DEL_DIR/to-delete.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "占位问题"}, {"role": "assistant", "content": "占位答案"}]}
EOF
cat > "$DEL_DIR/keep.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "保留问题"}, {"role": "assistant", "content": "保留答案"}]}
EOF
dit add to-delete.jsonl keep.jsonl
dit commit -m "base: two files"
```

feature 分支删除 `to-delete.jsonl`：

```bash
dit checkout -b feature/delete
rm "$DEL_DIR/to-delete.jsonl"
dit add to-delete.jsonl
dit commit -m "feature: delete to-delete.jsonl"
```

main 分支保持不动，只添加一条新记录到 keep.jsonl：

```bash
dit checkout main
cat >> "$DEL_DIR/keep.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "追加问题"}, {"role": "assistant", "content": "追加答案"}]}
EOF
dit add keep.jsonl
dit commit -m "main: append to keep.jsonl"
```

```bash
dit merge feature/delete
```

验证清单：
- [ ] 合并**无冲突**，输出 `Merge made`
- [ ] `to-delete.jsonl` 不存在（feature 的删除操作被采纳）
- [ ] `keep.jsonl` 存在且包含 2 行（main 的追加操作被保留）

### 9.3 场景三：两个分支都新增了**同名文件**但内容不同（both_added 冲突）

```bash
export BOTH_ADD_DIR=$(mktemp -d)
cd "$BOTH_ADD_DIR"
dit init

cat > "$BOTH_ADD_DIR/existing.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "已有数据"}]}
EOF
dit add existing.jsonl
dit commit -m "base"
```

feature 分支创建 `new-topic.jsonl`：

```bash
dit checkout -b feature/new-topic
cat > "$BOTH_ADD_DIR/new-topic.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "什么是 GraphQL？"}, {"role": "assistant", "content": "GraphQL 是 API 查询语言，允许客户端按需获取数据，避免过度获取。(feature版)"}]}
EOF
dit add new-topic.jsonl
dit commit -m "feature: add new-topic.jsonl"
```

main 分支也创建同名的 `new-topic.jsonl`，但内容不同：

```bash
dit checkout main
cat > "$BOTH_ADD_DIR/new-topic.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "什么是 GraphQL？"}, {"role": "assistant", "content": "GraphQL 由 Facebook 开发，与 REST 相比提供更灵活的数据查询接口。(main版)"}]}
EOF
dit add new-topic.jsonl
dit commit -m "main: add new-topic.jsonl"
```

```bash
dit merge feature/new-topic
```

预期输出（exit code 非 0）：

```
CONFLICT: 1 file(s) have conflicts.
  new-topic.jsonl (both_added)
```

验证清单：
- [ ] 冲突类型为 `both_added`
- [ ] `.dit/MERGE_HEAD` 存在
- [ ] `.dit/conflicts.json` 中 `conflict_type` 为 `both_added`

**解决 both_added 冲突（选择合并后的内容）：**

```bash
cat > "$BOTH_ADD_DIR/new-topic.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "什么是 GraphQL？"}, {"role": "assistant", "content": "GraphQL 是 Facebook 开发的 API 查询语言，允许客户端按需获取数据，与 REST 相比更灵活高效。(resolved)"}]}
EOF
dit add new-topic.jsonl
dit merge --continue
```

- [ ] 合并成功，`.dit/MERGE_HEAD` 已删除

### 9.4 场景四：modify/delete 冲突（一侧删除文件，另一侧修改文件）

```bash
export MOD_DEL_DIR=$(mktemp -d)
cd "$MOD_DEL_DIR"
dit init

cat > "$MOD_DEL_DIR/contested.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "什么是 CAP 定理？"}]}
EOF
dit add contested.jsonl
dit commit -m "base"
```

feature 分支修改该文件：

```bash
dit checkout -b feature/modify
cat > "$MOD_DEL_DIR/contested.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "什么是 CAP 定理？"}, {"role": "assistant", "content": "CAP 定理指出分布式系统无法同时满足一致性（C）、可用性（A）、分区容错性（P）三项保证。"}]}
EOF
dit add contested.jsonl
dit commit -m "feature: answer CAP theorem"
```

main 分支删除该文件：

```bash
dit checkout main
rm "$MOD_DEL_DIR/contested.jsonl"
dit add contested.jsonl
dit commit -m "main: remove contested.jsonl"
```

```bash
dit merge feature/modify
```

预期输出（exit code 非 0）：

```
CONFLICT: 1 file(s) have conflicts.
  contested.jsonl (modify_delete)
```

验证清单：
- [ ] 冲突类型为 `modify_delete`
- [ ] `.dit/conflicts.json` 内容确认冲突类型

```bash
cat "$MOD_DEL_DIR/.dit/conflicts.json"
```

**中止此合并（不解决，直接放弃）：**

```bash
dit merge --abort
```

- [ ] `contested.jsonl` 不存在（恢复到 main 分支的已删除状态）

---

## 10. 边界场景

### 10.1 合并自身（应报错）

```bash
export EDGE_DIR=$(mktemp -d)
cd "$EDGE_DIR"
dit init
cat > "$EDGE_DIR/data.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "测试数据"}]}
EOF
dit add data.jsonl
dit commit -m "initial"

dit merge main
```

验证清单：
- [ ] 报错：`error: cannot merge a branch into itself`
- [ ] 退出码非 0

### 10.2 合并不存在的分支（应报错）

```bash
cd "$EDGE_DIR"
dit merge nonexistent-branch
```

验证清单：
- [ ] 报错：`error: branch 'nonexistent-branch' not found`
- [ ] 退出码非 0

### 10.3 在有未暂存内容时尝试合并（应报错）

```bash
cd "$EDGE_DIR"
dit branch feature/edge
cat > "$EDGE_DIR/data.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "未提交的修改"}]}
EOF
dit add data.jsonl
# 暂存但不提交
dit merge feature/edge
```

验证清单：
- [ ] 报错：`error: staging area is not empty — please commit first`
- [ ] 退出码非 0

### 10.4 合并已合并的分支（Already up to date）

```bash
export UPTODATE_DIR=$(mktemp -d)
cd "$UPTODATE_DIR"
dit init
cat > "$UPTODATE_DIR/data.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "问题"}]}
EOF
dit add data.jsonl
dit commit -m "initial"

# 创建 feature 分支，添加数据
dit checkout -b feature/already-merged
cat > "$UPTODATE_DIR/more.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "更多数据"}]}
EOF
dit add more.jsonl
dit commit -m "feature: more data"

# 合并到 main
dit checkout main
dit merge feature/already-merged

# 再次合并同一个分支
dit merge feature/already-merged
```

验证清单：
- [ ] 第一次合并输出 `Fast-forward`（或 `Merge made`）
- [ ] 第二次合并输出 `Already up to date.`
- [ ] 退出码为 0

### 10.5 Cherry-pick 一个无效哈希（应报错）

```bash
cd "$EDGE_DIR"
dit cherry-pick 0000000000000000000000000000000000000000000000000000000000000000
```

验证清单：
- [ ] 报错：`error: commit '00000000' not found`
- [ ] 退出码非 0

### 10.6 Cherry-pick 根提交（应报错）

根提交没有父提交，无法进行三方合并：

```bash
export ROOT_DIR=$(mktemp -d)
cd "$ROOT_DIR"
dit init
cat > "$ROOT_DIR/data.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "根提交数据"}]}
EOF
dit add data.jsonl
dit commit -m "root commit"

ROOT_HASH=$(dit log --oneline | head -1 | awk '{print $1}')
dit cherry-pick "$ROOT_HASH"
```

验证清单：
- [ ] 报错：`error: cannot cherry-pick a root commit`
- [ ] 退出码非 0

### 10.7 在 merge 进行中执行 cherry-pick（互斥，应报错）

```bash
export MUTEX_DIR=$(mktemp -d)
cd "$MUTEX_DIR"
dit init
cat > "$MUTEX_DIR/data.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "互斥测试"}]}
EOF
dit add data.jsonl
dit commit -m "base"

# 手动写入 MERGE_HEAD 模拟合并进行中
echo "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" > "$MUTEX_DIR/.dit/MERGE_HEAD"

dit cherry-pick bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
```

验证清单：
- [ ] 报错含 `merge`（提示先完成或中止当前合并）
- [ ] 退出码非 0

```bash
# 清理手动写入的状态文件
rm "$MUTEX_DIR/.dit/MERGE_HEAD"
```

---

## 小结

| 场景 | 命令 | 关键验证点 |
|------|------|------------|
| 快进合并 | `dit merge <branch>` | 输出 `Fast-forward`，无新合并提交 |
| 三方合并 | `dit merge <branch>` | 输出 `Merge made`，新提交有 2 个父节点 |
| 冲突合并 | `dit merge <branch>` | 退出码非 0，`.dit/MERGE_HEAD` 存在 |
| 解决冲突 | `dit add <file> && dit merge --continue` | `.dit/MERGE_HEAD` 删除，新提交有 2 个父节点 |
| 中止合并 | `dit merge --abort` | 工作目录恢复，状态文件清理 |
| Cherry-pick | `dit cherry-pick <hash>` | 新提交只有 1 个父节点，消息含 `cherry-pick` |
| Cherry-pick 冲突 | 冲突后 `dit cherry-pick --continue/--abort` | 使用 `CHERRY_PICK_HEAD`（非 `MERGE_HEAD`） |
| both_added 冲突 | `dit merge <branch>` | `conflict_type: both_added` |
| modify_delete 冲突 | `dit merge <branch>` | `conflict_type: modify_delete` |
| both_modified 冲突 | `dit merge <branch>` | `conflict_type: both_modified` |

> **行级三方合并要点**：dit 以 `query_fingerprint`（由 user turn 内容哈希得到）为行的身份标识。只有当两个分支都"刷新"（修改）了同一个 `query_fingerprint` 对应的行且结果不同时，才产生 `both_modified` 冲突；若只有一侧修改，则自动采纳修改方的版本。
