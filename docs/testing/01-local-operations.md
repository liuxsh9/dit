# Dit 手动测试指南 01：本地操作基础

本指南覆盖 Dit CLI（`dit`）的核心本地工作流，包括：初始化仓库、添加文件、提交、查看状态与差异、日志回溯，以及若干边界场景的验证。

---

## 目录

1. [测试准备](#1-测试准备)
2. [初始化仓库 — dit init](#2-初始化仓库--dit-init)
3. [添加文件 — dit add](#3-添加文件--dit-add)
4. [查看状态 — dit status](#4-查看状态--dit-status)
5. [提交变更 — dit commit](#5-提交变更--dit-commit)
6. [查看日志 — dit log](#6-查看日志--dit-log)
7. [修改与差异 — dit diff](#7-修改与差异--dit-diff)
8. [多次提交](#8-多次提交)
9. [非 JSONL 文件（Blob）](#9-非-jsonl-文件blob)
10. [边界场景](#10-边界场景)

---

## 1. 测试准备

### 1.1 确认 dit 可用

```bash
dit --help
dit version
```

验证清单：
- [ ] `dit --help` 显示帮助信息，列出 `init`、`add`、`commit`、`status`、`diff`、`log` 等子命令
- [ ] `dit version` 输出版本号（如 `dit 0.1.0`）

> 若当前终端还未安装 `dit`，请先完成指南 00 的 `uv tool install --force .` 步骤。后续测试需要在临时目录中直接运行 `dit`，不依赖仓库根目录下的 `uv run`。

### 1.2 创建测试工作目录

```bash
export TEST_DIR=$(mktemp -d)
echo "测试目录：$TEST_DIR"
cd "$TEST_DIR"
```

> 所有后续步骤均在 `$TEST_DIR` 下执行。测试完成后可直接删除整个目录。

### 1.3 准备测试用 JSONL 数据

本指南使用两个 JSONL 文件模拟真实 SFT 编程数据集。

**文件 1：`train.jsonl`（3 条训练样本）**

```bash
cat > "$TEST_DIR/train.jsonl" << 'EOF'
{"messages": [{"role": "system", "content": "你是一位资深 Python 工程师，擅长算法与数据结构。回答时先用 <thinking> 标签梳理思路，再给出最终答案。"}, {"role": "user", "content": "请用 Python 实现一个 LRU 缓存，要求 get 和 put 操作均为 O(1) 时间复杂度。"}, {"role": "assistant", "content": "<thinking>\n需要结合哈希表（快速查找）和双向链表（维护访问顺序）。Python 的 OrderedDict 已经实现了这个组合，可以直接使用。\n</thinking>\n\n```python\nfrom collections import OrderedDict\n\nclass LRUCache:\n    def __init__(self, capacity: int):\n        self.cache = OrderedDict()\n        self.capacity = capacity\n\n    def get(self, key: int) -> int:\n        if key not in self.cache:\n            return -1\n        self.cache.move_to_end(key)\n        return self.cache[key]\n\n    def put(self, key: int, value: int) -> None:\n        if key in self.cache:\n            self.cache.move_to_end(key)\n        self.cache[key] = value\n        if len(self.cache) > self.capacity:\n            self.cache.popitem(last=False)\n```\n\n**关键点**：`move_to_end` 将最近访问的元素移到末尾；`popitem(last=False)` 弹出最久未使用的元素（队首）。"}]}
{"messages": [{"role": "system", "content": "你是一位资深 Python 工程师，擅长算法与数据结构。回答时先用 <thinking> 标签梳理思路，再给出最终答案。"}, {"role": "user", "content": "二分查找的时间复杂度是多少？请给出 Python 实现，并说明为什么必须要求数组有序。"}, {"role": "assistant", "content": "<thinking>\n二分查找每次将搜索空间减半，所以时间复杂度是 O(log n)。数组有序是前提，因为算法依赖「中间值比左侧大、比右侧小」这一不变式来决定向哪半边继续搜索。\n</thinking>\n\n时间复杂度：**O(log n)**，空间复杂度 O(1)（迭代版本）。\n\n```python\ndef binary_search(arr: list[int], target: int) -> int:\n    left, right = 0, len(arr) - 1\n    while left <= right:\n        mid = left + (right - left) // 2\n        if arr[mid] == target:\n            return mid\n        elif arr[mid] < target:\n            left = mid + 1\n        else:\n            right = mid - 1\n    return -1\n```\n\n**为什么必须有序**：算法通过比较中间值来「丢弃」一半，若无序则无法保证目标在保留的那一半里。"}]}
{"messages": [{"role": "system", "content": "你是一位资深 Python 工程师，擅长算法与数据结构。回答时先用 <thinking> 标签梳理思路，再给出最终答案。"}, {"role": "user", "content": "用 Python 实现快速排序，并解释最坏情况是什么时候。"}, {"role": "assistant", "content": "<thinking>\n快速排序的核心是分区（partition）。最坏情况发生在每次 pivot 都选到最大或最小值，导致分区极度不平衡（一侧 0 个元素，另一侧 n-1 个），退化为 O(n²)。\n</thinking>\n\n```python\ndef quicksort(arr: list[int]) -> list[int]:\n    if len(arr) <= 1:\n        return arr\n    pivot = arr[len(arr) // 2]  # 取中间元素作 pivot，减少最坏情况概率\n    left = [x for x in arr if x < pivot]\n    middle = [x for x in arr if x == pivot]\n    right = [x for x in arr if x > pivot]\n    return quicksort(left) + middle + quicksort(right)\n```\n\n**最坏情况**：数组已经有序（正序或逆序）且每次选首/尾元素作 pivot，时间复杂度退化为 **O(n²)**。使用随机 pivot 或「三数取中」可大幅降低最坏情况概率。"}]}
EOF
```

**文件 2：`eval.jsonl`（2 条评估样本）**

```bash
cat > "$TEST_DIR/eval.jsonl" << 'EOF'
{"messages": [{"role": "system", "content": "你是一位代码评审专家。"}, {"role": "user", "content": "下面的代码有什么问题？\n\n```python\ndef divide(a, b):\n    return a / b\n```"}, {"role": "assistant", "content": "这段代码存在两个问题：\n\n1. **除零错误**：当 `b == 0` 时会抛出 `ZeroDivisionError`，缺少边界检查。\n2. **类型未声明**：没有类型注解，调用方不清楚参数类型期望。\n\n建议改写：\n```python\ndef divide(a: float, b: float) -> float:\n    if b == 0:\n        raise ValueError(\"除数不能为零\")\n    return a / b\n```"}]}
{"messages": [{"role": "system", "content": "你是一位代码评审专家。"}, {"role": "user", "content": "如何判断一个字符串是否为回文？"}, {"role": "assistant", "content": "最简洁的 Python 写法：\n\n```python\ndef is_palindrome(s: str) -> bool:\n    s = s.lower().replace(\" \", \"\")\n    return s == s[::-1]\n```\n\n**说明**：先统一转小写、去空格，再与逆序字符串比较。时间复杂度 O(n)，空间复杂度 O(n)（切片会创建新字符串）。"}]}
EOF
```

验证清单：
- [ ] `$TEST_DIR/train.jsonl` 存在，`wc -l "$TEST_DIR/train.jsonl"` 输出 `3`
- [ ] `$TEST_DIR/eval.jsonl` 存在，`wc -l "$TEST_DIR/eval.jsonl"` 输出 `2`
- [ ] 每行均为合法 JSON：`python3 -c "import json,sys; [json.loads(l) for l in open('$TEST_DIR/train.jsonl')]"` 无报错

---

## 2. 初始化仓库 — dit init

### 操作

```bash
cd "$TEST_DIR"
dit init
```

### 预期输出

```
Initialized empty dit repository in /tmp/tmp.xxxxxxxxxx
```

### 验证方法

```bash
# 检查目录结构
ls -la "$TEST_DIR/.dit/"
ls -la "$TEST_DIR/.dit/objects/"
ls -la "$TEST_DIR/.dit/refs/"

# 检查 HEAD 文件内容
cat "$TEST_DIR/.dit/HEAD"
```

**预期目录结构：**
```
.dit/
├── HEAD            ← 内容应为 "ref:main"
├── objects/        ← 对象存储根目录（空）
└── refs/
    ├── heads/      ← 分支引用目录（空，还没有提交）
    └── tags/       ← 标签引用目录（空）
```

验证清单：
- [ ] 输出含 "Initialized empty dit repository"
- [ ] `.dit/` 目录存在
- [ ] `.dit/HEAD` 文件存在，内容为 `ref:main`
- [ ] `.dit/objects/` 目录存在
- [ ] `.dit/refs/heads/` 目录存在
- [ ] `.dit/refs/tags/` 目录存在

### 幂等性验证（已初始化时再次 init）

```bash
dit init
```

预期输出（不报错，不破坏现有仓库）：
```
Already initialized dit repository in /tmp/tmp.xxxxxxxxxx
```

验证清单：
- [ ] 第二次 `init` 退出码为 0
- [ ] 输出含 "Already initialized" 或 "already"
- [ ] `.dit/HEAD` 内容未变

---

## 3. 添加文件 — dit add

### 3.1 添加单个 JSONL 文件

#### 操作

```bash
cd "$TEST_DIR"
dit add train.jsonl
```

#### 预期输出

```
  staged train.jsonl (3 rows)
```

#### 验证方法

```bash
# 检查 staging index 文件是否创建
cat "$TEST_DIR/.dit/index"
```

预期输出（格式为 JSON）：
```json
{"train.jsonl": {"hash": "<64位十六进制哈希>", "type": "manifest"}}
```

验证清单：
- [ ] 输出含 "staged train.jsonl (3 rows)"
- [ ] `.dit/index` 文件存在
- [ ] `index` 内容是合法 JSON，包含 `"train.jsonl"` 键
- [ ] `index` 中 `train.jsonl` 的 `"type"` 为 `"manifest"`
- [ ] 对应的 manifest 对象已写入 `.dit/objects/manifests/`（用 `ls .dit/objects/manifests/` 可见两层子目录）

### 3.2 添加第二个文件

```bash
dit add eval.jsonl
```

预期输出：
```
  staged eval.jsonl (2 rows)
```

验证清单：
- [ ] 输出含 "staged eval.jsonl (2 rows)"
- [ ] `index` 内容现包含 `"train.jsonl"` 和 `"eval.jsonl"` 两个键

### 3.3 使用 `.` 批量添加

重置 index 后用 `.` 一次性添加所有文件：

```bash
# 清空 index（模拟重新暂存）
echo '{}' > "$TEST_DIR/.dit/index"

cd "$TEST_DIR"
dit add .
```

预期输出（顺序可能不同）：
```
  staged eval.jsonl (2 rows)
  staged train.jsonl (3 rows)
```

验证清单：
- [ ] 两个文件均出现在输出中，行数正确
- [ ] `index` 包含两个条目

### 3.4 添加不存在的文件（错误场景）

```bash
dit add nonexistent.jsonl
echo "退出码: $?"
```

预期行为：
- 输出 `fatal: pathspec 'nonexistent.jsonl' did not match any files`
- 退出码非 0

验证清单：
- [ ] 输出含 "fatal"
- [ ] 退出码不为 0（`echo $?` 不输出 `0`）

---

## 4. 查看状态 — dit status

### 4.1 有暂存文件时的状态（提交前）

此时 `train.jsonl` 和 `eval.jsonl` 已暂存（接续 3.3 的操作）。

```bash
cd "$TEST_DIR"
dit status
```

预期输出（节选）：
```
On branch main

Staged files:
  eval.jsonl
  train.jsonl

Unstaged changes:
  new file: eval.jsonl
  new file: train.jsonl
```

> **说明**：暂存区有内容 AND 工作目录中存在从未提交过的文件，两个区段均会显示。

验证清单：
- [ ] 输出第一行为 `On branch main`
- [ ] "Staged files" 区段列出 `eval.jsonl` 和 `train.jsonl`
- [ ] "Unstaged changes" 区段将两个文件标记为 `new file`

### 4.2 空仓库（无任何提交，无暂存）

```bash
# 创建全新的临时仓库验证空状态
EMPTY_DIR=$(mktemp -d)
cd "$EMPTY_DIR"
dit init
dit status
```

预期输出：
```
On branch main

Nothing to commit, working directory clean.
```

验证清单：
- [ ] 输出含 "Nothing to commit"
- [ ] 退出码为 0

清理临时目录：
```bash
rm -rf "$EMPTY_DIR"
cd "$TEST_DIR"
```

---

## 5. 提交变更 — dit commit

### 5.1 创建第一次提交

#### 操作

确保 `train.jsonl` 和 `eval.jsonl` 均已暂存（如未完成 3.3，重新执行 `dit add .`）：

```bash
cd "$TEST_DIR"
dit add .
dit commit -m "初始数据集：3条训练样本 + 2条评估样本"
```

#### 预期输出

```
[main <8位哈希>] 初始数据集：3条训练样本 + 2条评估样本
```

#### 验证方法

```bash
# 1. 确认 main 分支引用已写入
cat "$TEST_DIR/.dit/refs/heads/main"
# 预期：64位十六进制哈希

# 2. 确认 staging index 已清空
cat "$TEST_DIR/.dit/index"
# 预期：{}

# 3. 确认 objects 目录中有 commits 和 trees 子目录
ls "$TEST_DIR/.dit/objects/"
# 预期含：commits/ manifests/ rows/ trees/
```

**通过 Python 内省 commit 对象：**

```bash
cd "$TEST_DIR"
uv run python3 - << 'PYEOF'
from pathlib import Path
from dit.core.store import ObjectStore
from dit.core.objects import deserialize_commit, deserialize_tree, deserialize_manifest
from dit.core.refs import RefStore

dot = Path(".dit")
store = ObjectStore(dot / "objects")
refs = RefStore(dot)

commit_hash = refs.resolve_head()
print(f"HEAD commit: {commit_hash}")

commit = deserialize_commit(store.read("commits", commit_hash))
print(f"Author:  {commit.author}")
print(f"Message: {commit.message}")
print(f"Parents: {commit.parent_hashes}")
print(f"Tree:    {commit.tree_hash}")

tree = deserialize_tree(store.read("trees", commit.tree_hash))
print(f"\n根 tree 条目数：{len(tree.entries)}")
for e in sorted(tree.entries, key=lambda x: x.name):
    print(f"  {e.name:<20} type={e.obj_type}  hash={e.obj_hash[:8]}...")
PYEOF
```

预期输出（示例）：
```
HEAD commit: a1b2c3d4e5f6...（64 位哈希）
Author:  <你的用户名>
Message: 初始数据集：3条训练样本 + 2条评估样本
Parents: []
Tree:    <64 位哈希>

根 tree 条目数：2
  eval.jsonl           type=manifest  hash=xxxxxxxx...
  train.jsonl          type=manifest  hash=yyyyyyyy...
```

验证清单：
- [ ] 输出含 `[main <hash>]` 和提交消息
- [ ] `.dit/refs/heads/main` 包含 64 位十六进制哈希
- [ ] `.dit/index` 内容为 `{}`（index 已清空）
- [ ] `objects/commits/` 目录下有对象文件（3 级子目录结构：`aa/bb/<全哈希>`）
- [ ] 上述 Python 脚本输出中 `Parents: []`（根提交无父提交）
- [ ] 根 tree 包含 `eval.jsonl` 和 `train.jsonl`，类型均为 `manifest`

### 5.2 空暂存区提交（错误场景）

```bash
cd "$TEST_DIR"
dit commit -m "空提交"
echo "退出码: $?"
```

预期行为：
- 输出 `nothing to commit (staging area is empty)`
- 退出码非 0

验证清单：
- [ ] 输出含 "nothing to commit"
- [ ] 退出码不为 0

---

## 6. 查看日志 — dit log

### 6.1 查看当前日志（一次提交后）

```bash
cd "$TEST_DIR"
dit log
```

预期输出格式：
```
commit a1b2c3d4e5f6...（64 位完整哈希）
Author: <用户名>
Date:   2026-04-25 08:00:00 UTC

    初始数据集：3条训练样本 + 2条评估样本

```

验证清单：
- [ ] 输出含 `commit ` 开头的 64 位哈希行
- [ ] 输出含 `Author:` 行（值为当前系统用户名或 `DIT_AUTHOR` 环境变量）
- [ ] 输出含 `Date:` 行，格式为 `YYYY-MM-DD HH:MM:SS UTC`
- [ ] 输出含提交消息 "初始数据集：3条训练样本 + 2条评估样本"

### 6.2 空仓库的 log

```bash
EMPTY2=$(mktemp -d)
cd "$EMPTY2"
dit init
dit log
```

预期输出：
```
No commits yet.
```

验证清单：
- [ ] 输出含 "No commits yet."
- [ ] 退出码为 0

清理：
```bash
rm -rf "$EMPTY2"
cd "$TEST_DIR"
```

---

## 7. 修改与差异 — dit diff

`dit diff` 比较**工作目录**与 HEAD 提交之间的行级差异（不是 staging area），以 manifest 层面的增删统计为单位。

### 7.1 无变更时的 diff

```bash
cd "$TEST_DIR"
dit diff
```

预期输出：
```
No changes.
```

验证清单：
- [ ] 输出为 "No changes."
- [ ] 退出码为 0

### 7.2 修改文件后查看 diff

向 `train.jsonl` 追加一条新的训练样本：

```bash
cat >> "$TEST_DIR/train.jsonl" << 'EOF'
{"messages": [{"role": "system", "content": "你是一位资深 Python 工程师，擅长算法与数据结构。回答时先用 <thinking> 标签梳理思路，再给出最终答案。"}, {"role": "user", "content": "解释 Python 的 GIL（全局解释器锁）及其对多线程程序的影响。"}, {"role": "assistant", "content": "<thinking>\nGIL 是 CPython 的实现细节，保证同一时刻只有一个线程执行 Python 字节码。对 CPU 密集型任务影响大，对 I/O 密集型任务影响小。\n</thinking>\n\n**GIL（Global Interpreter Lock）**是 CPython 解释器的互斥锁，确保任意时刻只有一个线程在执行 Python 字节码。\n\n**影响**：\n- **CPU 密集型**（如数值计算）：多线程无法并行，性能提升有限 → 改用 `multiprocessing` 或 `concurrent.futures.ProcessPoolExecutor`\n- **I/O 密集型**（如网络、文件读写）：线程在等待 I/O 时会释放 GIL，多线程依然有效\n\n```python\n# I/O 密集型 — 多线程有效\nimport threading, urllib.request\n\ndef fetch(url):\n    urllib.request.urlopen(url).read()\n\nthreads = [threading.Thread(target=fetch, args=(f'http://example.com/{i}',)) for i in range(10)]\nfor t in threads: t.start()\nfor t in threads: t.join()\n```"}]}
EOF
```

现在执行 diff：

```bash
cd "$TEST_DIR"
dit diff
```

预期输出：
```
train.jsonl: 3 → 4 rows (+1, -0)
```

验证清单：
- [ ] 输出含 `train.jsonl`
- [ ] 输出含 `3 → 4 rows`
- [ ] 输出含 `+1`（新增 1 行）
- [ ] `eval.jsonl` 不出现（未修改）

### 7.3 替换行内容触发"刷新"检测

修改 `eval.jsonl` 中第一条样本的 assistant 回答（保持 user 问题不变）：

```bash
cat > "$TEST_DIR/eval.jsonl" << 'EOF'
{"messages": [{"role": "system", "content": "你是一位代码评审专家。"}, {"role": "user", "content": "下面的代码有什么问题？\n\n```python\ndef divide(a, b):\n    return a / b\n```"}, {"role": "assistant", "content": "这段代码有两个主要问题：\n\n1. **缺少除零保护**：`b == 0` 时抛 `ZeroDivisionError`。\n2. **没有类型注解**：影响可读性和 IDE 支持。\n\n推荐写法：\n```python\ndef divide(a: float, b: float) -> float:\n    if b == 0:\n        raise ValueError(\"b 不能为零\")\n    return a / b\n```\n\n同时考虑是否需要处理 `b` 为 `None` 或非数值类型的情况。"}]}
{"messages": [{"role": "system", "content": "你是一位代码评审专家。"}, {"role": "user", "content": "如何判断一个字符串是否为回文？"}, {"role": "assistant", "content": "最简洁的 Python 写法：\n\n```python\ndef is_palindrome(s: str) -> bool:\n    s = s.lower().replace(\" \", \"\")\n    return s == s[::-1]\n```\n\n**说明**：先统一转小写、去空格，再与逆序字符串比较。时间复杂度 O(n)，空间复杂度 O(n)（切片会创建新字符串）。"}]}
EOF
```

执行 diff：

```bash
cd "$TEST_DIR"
dit diff
```

预期输出（含行数变化 + refresh 提示）：
```
eval.jsonl: 2 → 2 rows (+1, -1)
  Likely refreshed: 1 rows
train.jsonl: 3 → 4 rows (+1, -0)
```

> **说明**：当 user 问题（`query_fingerprint`）相同但 assistant 回答不同时，dit 将其识别为"刷新"（refresh），而非简单的删除 + 新增。

验证清单：
- [ ] `eval.jsonl` 出现，行数显示 `2 → 2`，差值含 `+1` 和 `-1`
- [ ] `eval.jsonl` 条目下出现 "Likely refreshed: 1 rows"
- [ ] `train.jsonl` 显示 `3 → 4 rows`

### 7.4 新文件的 diff

```bash
cat > "$TEST_DIR/extra.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "什么是 Python 的装饰器？"}, {"role": "assistant", "content": "装饰器是一个接受函数作为参数并返回新函数的高阶函数，用 `@` 语法糖修饰目标函数。"}]}
EOF

dit diff
```

预期输出（新文件 extra.jsonl 出现）：
```
eval.jsonl: 2 → 2 rows (+1, -1)
  Likely refreshed: 1 rows
extra.jsonl: new file (1 rows)
train.jsonl: 3 → 4 rows (+1, -0)
```

验证清单：
- [ ] `extra.jsonl: new file (1 rows)` 出现在输出中

---

## 8. 多次提交

本节构建一个包含多次提交的历史，验证父子链与 `log` 输出顺序。

### 8.1 提交第二次（包含所有修改）

```bash
cd "$TEST_DIR"
dit add .
dit commit -m "增加GIL样本、修订eval回答、新增extra.jsonl"
```

预期输出：
```
[main <hash>] 增加GIL样本、修订eval回答、新增extra.jsonl
```

### 8.2 再做一次小修改并提交

向 `extra.jsonl` 追加一行：

```bash
cat >> "$TEST_DIR/extra.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "Python 中 `__init__` 和 `__new__` 有什么区别？"}, {"role": "assistant", "content": "`__new__` 负责创建实例（分配内存），`__init__` 负责初始化实例（设置属性）。通常只需重写 `__init__`；在需要控制不可变对象（如 `int` 子类）的创建时才重写 `__new__`。"}]}
EOF

cd "$TEST_DIR"
dit add extra.jsonl
dit commit -m "extra.jsonl 补充 __new__ vs __init__ 样本"
```

### 8.3 验证 log 顺序与父子链

```bash
dit log
```

预期输出（最新提交在最前）：
```
commit <hash3>
Author: <用户名>
Date:   <时间戳> UTC

    extra.jsonl 补充 __new__ vs __init__ 样本

commit <hash2>
Author: <用户名>
Date:   <时间戳> UTC

    增加GIL样本、修订eval回答、新增extra.jsonl

commit <hash1>
Author: <用户名>
Date:   <时间戳> UTC

    初始数据集：3条训练样本 + 2条评估样本

```

验证清单：
- [ ] 日志输出 3 个 commit 块
- [ ] 最新提交排在最前（含 "__new__ vs __init__"）
- [ ] 最旧提交排在最后（含 "初始数据集"）
- [ ] 每个 commit 块格式正确（commit / Author / Date / 消息四部分）

**通过 Python 验证父子链：**

```bash
cd "$TEST_DIR"
uv run python3 - << 'PYEOF'
from pathlib import Path
from dit.core.store import ObjectStore
from dit.core.objects import deserialize_commit
from dit.core.refs import RefStore

dot = Path(".dit")
store = ObjectStore(dot / "objects")
refs = RefStore(dot)

h = refs.resolve_head()
chain = []
while h:
    c = deserialize_commit(store.read("commits", h))
    chain.append((h[:8], c.message, c.parent_hashes))
    h = c.parent_hashes[0] if c.parent_hashes else None

print(f"提交链长度：{len(chain)}")
for i, (sh, msg, parents) in enumerate(chain):
    parent_str = parents[0][:8] if parents else "（根提交）"
    print(f"  [{i}] {sh}  msg='{msg[:30]}'  parent={parent_str}")
PYEOF
```

预期输出：
```
提交链长度：3
  [0] xxxxxxxx  msg='extra.jsonl 补充 __new__ vs __init__ 样'  parent=yyyyyyyy
  [1] yyyyyyyy  msg='增加GIL样本、修订eval回答、新增extra.json'  parent=zzzzzzzz
  [2] zzzzzzzz  msg='初始数据集：3条训练样本 + 2条评估样本'  parent=（根提交）
```

验证清单：
- [ ] 链长度为 3
- [ ] 第 0 个（最新）commit 的 `parent` 等于第 1 个 commit 的前 8 位哈希
- [ ] 第 2 个（最旧）commit 的 `parent` 显示 "（根提交）"

### 8.4 提交后状态验证

```bash
cd "$TEST_DIR"
dit status
```

预期输出：
```
On branch main

Nothing to commit, working directory clean.
```

验证清单：
- [ ] 输出含 "Nothing to commit"
- [ ] 没有 "Staged files" 或 "Unstaged changes" 区段

---

## 9. 非 JSONL 文件（Blob）

dit 将非 `.jsonl` 文件作为不透明的 **blob** 对象存储，不解析行内容。

### 9.1 添加 README 文件

```bash
cat > "$TEST_DIR/README.md" << 'EOF'
# 算法训练数据集

本仓库包含 Python 算法相关的 SFT 训练数据。

## 文件说明
- `train.jsonl`: 训练集（4 条样本）
- `eval.jsonl`:  评估集（2 条样本）
- `extra.jsonl`: 补充样本（2 条）
EOF

cd "$TEST_DIR"
dit add README.md
```

预期输出：
```
  staged README.md (blob)
```

验证清单：
- [ ] 输出含 "staged README.md (blob)"（注意：blob 不显示行数）
- [ ] `index` 中 README.md 的 `"type"` 为 `"blob"`

```bash
cat "$TEST_DIR/.dit/index" | python3 -m json.tool
```

验证清单（接续）：
- [ ] JSON 中 `README.md` 条目的 `"type"` 字段值为 `"blob"`
- [ ] JSONL 文件条目的 `"type"` 字段值均为 `"manifest"`

### 9.2 提交并验证 tree 条目类型

```bash
dit commit -m "添加 README.md 文档"
```

```bash
cd "$TEST_DIR"
uv run python3 - << 'PYEOF'
from pathlib import Path
from dit.core.store import ObjectStore
from dit.core.objects import deserialize_commit, deserialize_tree
from dit.core.refs import RefStore

dot = Path(".dit")
store = ObjectStore(dot / "objects")
refs = RefStore(dot)

h = refs.resolve_head()
commit = deserialize_commit(store.read("commits", h))
tree = deserialize_tree(store.read("trees", commit.tree_hash))

print(f"根 tree 条目（共 {len(tree.entries)} 个）：")
for e in sorted(tree.entries, key=lambda x: x.name):
    print(f"  {e.name:<20} type={e.obj_type}")
PYEOF
```

预期输出：
```
根 tree 条目（共 4 个）：
  README.md            type=blob
  eval.jsonl           type=manifest
  extra.jsonl          type=manifest
  train.jsonl          type=manifest
```

验证清单：
- [ ] `README.md` 的 `type` 为 `blob`
- [ ] 所有 `.jsonl` 文件的 `type` 均为 `manifest`

### 9.3 status 显示

```bash
dit status
```

验证清单：
- [ ] 输出含 "Nothing to commit, working directory clean."（blob 文件已提交，状态干净）

---

## 10. 边界场景

### 10.1 空 JSONL 文件

```bash
cd "$TEST_DIR"
touch "$TEST_DIR/empty.jsonl"
dit add empty.jsonl
```

预期输出：
```
  staged empty.jsonl (0 rows)
```

验证清单：
- [ ] 输出含 "staged empty.jsonl (0 rows)"（不报错，正常处理）

```bash
dit commit -m "添加空数据文件"
```

验证清单：
- [ ] commit 成功，退出码为 0

**查看空文件的 manifest 内容：**

```bash
cd "$TEST_DIR"
uv run python3 - << 'PYEOF'
from pathlib import Path
from dit.core.store import ObjectStore
from dit.core.objects import deserialize_commit, deserialize_tree, deserialize_manifest
from dit.core.refs import RefStore
from dit.core.tree_walker import flatten_tree

dot = Path(".dit")
store = ObjectStore(dot / "objects")
refs = RefStore(dot)

h = refs.resolve_head()
commit = deserialize_commit(store.read("commits", h))
flat = flatten_tree(store, commit.tree_hash)

obj_type, obj_hash, _ = flat["empty.jsonl"]
manifest = deserialize_manifest(store.read("manifests", obj_hash))
print(f"empty.jsonl manifest entries: {len(manifest.entries)}")
PYEOF
```

验证清单：
- [ ] 输出 `empty.jsonl manifest entries: 0`

### 10.2 包含中文和特殊字符的内容

```bash
cat > "$TEST_DIR/unicode.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "用 Python 输出 \"Hello, 世界！\" 并包含换行符 \\n 和制表符 \\t。"}, {"role": "assistant", "content": "```python\nprint(\"Hello, 世界！\\n\\t缩进行\")\n```\n输出：\n```\nHello, 世界！\n\t缩进行\n```"}]}
{"messages": [{"role": "user", "content": "emoji 在字符串中怎么处理？🐍"}, {"role": "assistant", "content": "Python 3 的字符串是 Unicode 序列，可以直接包含 emoji：\n```python\ns = \"Python 🐍 is great 🎉\"\nprint(len(s))   # 字符数，不是字节数\nprint(s.encode('utf-8'))  # 查看 UTF-8 字节\n```"}]}
EOF

cd "$TEST_DIR"
dit add unicode.jsonl
dit commit -m "添加含中文和emoji的测试数据"
```

验证清单：
- [ ] `add` 输出 "staged unicode.jsonl (2 rows)"，无编码错误
- [ ] `commit` 成功
- [ ] `log` 中提交消息 "添加含中文和emoji的测试数据" 正常显示

### 10.3 子目录中的 JSONL 文件

```bash
mkdir -p "$TEST_DIR/data/coding"
mkdir -p "$TEST_DIR/data/review"

cat > "$TEST_DIR/data/coding/advanced.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "什么是 Python 的元类（metaclass）？"}, {"role": "assistant", "content": "元类是创建类的类。`type` 本身就是默认元类。通过继承 `type` 并重写 `__new__` 或 `__init__`，可以在类定义时注入自定义行为（如 ORM 的字段自动注册）。"}]}
EOF

cat > "$TEST_DIR/data/review/style.jsonl" << 'EOF'
{"messages": [{"role": "user", "content": "PEP 8 中变量命名规范是什么？"}, {"role": "assistant", "content": "PEP 8 规定：普通变量和函数用 `snake_case`；类名用 `PascalCase`；常量用 `ALL_CAPS`；私有属性加单下划线前缀 `_private`；名称混淆用双下划线前缀 `__mangled`。"}]}
EOF

cd "$TEST_DIR"
dit add .
dit commit -m "添加子目录数据：coding/advanced 和 review/style"
```

验证清单：
- [ ] `add` 输出含 "staged data/coding/advanced.jsonl (1 rows)"
- [ ] `add` 输出含 "staged data/review/style.jsonl (1 rows)"
- [ ] `commit` 成功

**验证子目录结构在 tree 中的嵌套：**

```bash
cd "$TEST_DIR"
uv run python3 - << 'PYEOF'
from pathlib import Path
from dit.core.store import ObjectStore
from dit.core.objects import deserialize_commit, deserialize_tree
from dit.core.refs import RefStore

dot = Path(".dit")
store = ObjectStore(dot / "objects")
refs = RefStore(dot)

h = refs.resolve_head()
commit = deserialize_commit(store.read("commits", h))

def print_tree(tree_hash, indent=0):
    tree = deserialize_tree(store.read("trees", tree_hash))
    for e in sorted(tree.entries, key=lambda x: x.name):
        prefix = "  " * indent
        print(f"{prefix}{e.name}  [{e.obj_type}]")
        if e.obj_type == "tree":
            print_tree(e.obj_hash, indent + 1)

print("树结构：")
print_tree(commit.tree_hash)
PYEOF
```

预期输出（节选）：
```
树结构：
README.md  [blob]
data  [tree]
  coding  [tree]
    advanced.jsonl  [manifest]
  review  [tree]
    style.jsonl  [manifest]
...
```

验证清单：
- [ ] `data` 为 `tree` 类型（不是扁平路径 `data/coding/advanced.jsonl`）
- [ ] `data/coding` 和 `data/review` 均为嵌套 tree
- [ ] 叶节点 `.jsonl` 文件类型为 `manifest`

### 10.4 修改行顺序不触发 diff（相同行集合）

dit 以行哈希集合为单位比较，相同的行集合产生相同的 manifest 哈希，不视为变更。

```bash
# 将 eval.jsonl 的两行顺序调换
cat > "$TEST_DIR/eval.jsonl" << 'EOF'
{"messages": [{"role": "system", "content": "你是一位代码评审专家。"}, {"role": "user", "content": "如何判断一个字符串是否为回文？"}, {"role": "assistant", "content": "最简洁的 Python 写法：\n\n```python\ndef is_palindrome(s: str) -> bool:\n    s = s.lower().replace(\" \", \"\")\n    return s == s[::-1]\n```\n\n**说明**：先统一转小写、去空格，再与逆序字符串比较。时间复杂度 O(n)，空间复杂度 O(n)（切片会创建新字符串）。"}]}
{"messages": [{"role": "system", "content": "你是一位代码评审专家。"}, {"role": "user", "content": "下面的代码有什么问题？\n\n```python\ndef divide(a, b):\n    return a / b\n```"}, {"role": "assistant", "content": "这段代码有两个主要问题：\n\n1. **缺少除零保护**：`b == 0` 时抛 `ZeroDivisionError`。\n2. **没有类型注解**：影响可读性和 IDE 支持。\n\n推荐写法：\n```python\ndef divide(a: float, b: float) -> float:\n    if b == 0:\n        raise ValueError(\"b 不能为零\")\n    return a / b\n```\n\n同时考虑是否需要处理 `b` 为 `None` 或非数值类型的情况。"}]}
EOF
```

> 上面两行与当前 HEAD 中的 `eval.jsonl` 内容相同，只是顺序互换。

```bash
cd "$TEST_DIR"
dit diff
```

> **注意**：由于 manifest 存储行的顺序（entries 列表有序），行顺序变化**会**产生不同的 manifest 哈希，因此 dit 会检测到变化。这是预期行为。

预期输出：
```
eval.jsonl: 2 → 2 rows (+2, -2)
```

验证清单：
- [ ] 行顺序互换后 `diff` 检测到 `eval.jsonl` 有变化
- [ ] 行数仍显示为 `2 → 2`（内容相同，只是顺序不同）

恢复 eval.jsonl 原始顺序：
```bash
cat > "$TEST_DIR/eval.jsonl" << 'EOF'
{"messages": [{"role": "system", "content": "你是一位代码评审专家。"}, {"role": "user", "content": "下面的代码有什么问题？\n\n```python\ndef divide(a, b):\n    return a / b\n```"}, {"role": "assistant", "content": "这段代码有两个主要问题：\n\n1. **缺少除零保护**：`b == 0` 时抛 `ZeroDivisionError`。\n2. **没有类型注解**：影响可读性和 IDE 支持。\n\n推荐写法：\n```python\ndef divide(a: float, b: float) -> float:\n    if b == 0:\n        raise ValueError(\"b 不能为零\")\n    return a / b\n```\n\n同时考虑是否需要处理 `b` 为 `None` 或非数值类型的情况。"}]}
{"messages": [{"role": "system", "content": "你是一位代码评审专家。"}, {"role": "user", "content": "如何判断一个字符串是否为回文？"}, {"role": "assistant", "content": "最简洁的 Python 写法：\n\n```python\ndef is_palindrome(s: str) -> bool:\n    s = s.lower().replace(\" \", \"\")\n    return s == s[::-1]\n```\n\n**说明**：先统一转小写、去空格，再与逆序字符串比较。时间复杂度 O(n)，空间复杂度 O(n)（切片会创建新字符串）。"}]}
EOF

dit diff
```

验证清单：
- [ ] 恢复后 `diff` 输出 "No changes."（eval.jsonl 内容完全一致）

### 10.5 在仓库外执行命令（错误场景）

```bash
cd /tmp
dit status
echo "退出码: $?"
```

预期行为：
- 输出 `fatal: not a dit repository`（会向上遍历父目录，均未找到 `.dit/`）
- 退出码非 0

验证清单：
- [ ] 输出含 "fatal: not a dit repository"
- [ ] 退出码不为 0

```bash
# 恢复到测试目录
cd "$TEST_DIR"
```

---

## 附录：对象存储结构说明

提交完成后，`.dit/objects/` 目录结构遵循三级分片（取哈希前 2 位 + 后 2 位 + 完整哈希）：

```
.dit/objects/
├── commits/
│   └── a1/          ← 哈希前两位
│       └── b2/      ← 哈希第三四位
│           └── a1b2c3...（完整 64 位哈希，压缩存储）
├── manifests/       ← 每个 JSONL 文件的行索引
├── rows/            ← 每一个训练样本的 canonical JSON
├── trees/           ← 目录树结构
└── blobs/           ← 非 JSONL 文件（如 README.md）
```

**关键设计说明**：
- 所有对象使用 **SHA-256 内容哈希**命名，相同内容只存储一次（内容寻址）
- 所有对象在写入前使用 **zstd 压缩**，空间效率高
- 写入采用原子操作（先写 `tmp/`，再 `os.replace`），不会产生部分写入的损坏文件
- `index` 文件（`.dit/index`）是纯 JSON 格式，暂存后读取直接可见

---

*上一篇：[00 - 环境搭建与部署验证](./00-setup-and-deployment.md)*  
*下一篇：[02 - 分支与标签](./02-branching-and-tags.md)*
