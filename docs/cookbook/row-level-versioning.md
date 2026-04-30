# 行级版本控制

Dit 是专为 LLM SFT 训练数据设计的版本控制工具。与 git 管理代码文件不同，dit 将每一行 JSON 作为独立的版本控制单元 -- 每行通过 RFC 8785 canonical JSON 序列化后计算 SHA-256 哈希，相同语义的数据永远产生相同的哈希值。

本文演示从零开始的完整工作流：初始化仓库、暂存文件、提交、查看状态与历史。

---

## 初始化仓库

```bash
mkdir my-sft-data && cd my-sft-data
dit init
```

输出：

```
Initialized empty dit repository in /home/user/my-sft-data
```

初始化后会生成 `.dit/` 目录，结构类似 git：

```
.dit/
├── HEAD            # 当前分支引用，初始为 "ref:main"
├── objects/        # 内容寻址对象存储
│   ├── rows/       # 每行 JSON 的 canonical 序列化
│   ├── manifests/  # JSONL 文件的行索引
│   ├── trees/      # 目录结构
│   └── commits/    # 提交对象
└── refs/
    ├── heads/      # 分支指针
    └── tags/       # 标签
```

---

## 准备训练数据

创建一个标准的 SFT 训练文件：

```bash
cat > train.jsonl << 'EOF'
{"messages": [{"role": "user", "content": "什么是 Python 的 GIL？"}, {"role": "assistant", "content": "GIL 是 CPython 的全局解释器锁，同一时刻只允许一个线程执行字节码。"}]}
{"messages": [{"role": "user", "content": "解释列表推导式"}, {"role": "assistant", "content": "列表推导式是 [expr for item in iterable if cond] 的简洁语法。"}]}
{"messages": [{"role": "user", "content": "什么是装饰器？"}, {"role": "assistant", "content": "装饰器是接受函数并返回修改后函数的高阶函数，用 @syntax 修饰。"}]}
EOF
```

---

## 暂存文件

```bash
dit add train.jsonl
```

输出：

```
  staged train.jsonl (3 rows)
```

dit 在暂存时会解析 JSONL，将每行 JSON 做 canonical 序列化并计算 SHA-256 哈希，写入 `objects/rows/`。同时生成 manifest（有序的 row hash 列表）写入 `objects/manifests/`。

批量暂存所有文件：

```bash
dit add .
```

---

## 查看状态

```bash
dit status
```

输出：

```
On branch main

Staged files:
  train.jsonl

Unstaged changes:
  new file: train.jsonl
```

---

## 提交

```bash
dit commit -m "初始训练集：3条 Python QA 样本"
```

输出：

```
[main a1b2c3d4] 初始训练集：3条 Python QA 样本
```

提交后暂存区自动清空，`dit status` 会显示 "Nothing to commit, working directory clean."。

---

## 查看提交历史

```bash
dit log
```

输出：

```
commit a1b2c3d4e5f6...（64 位完整哈希）
Author: liuxsh9
Date:   2026-04-30 10:00:00 UTC

    初始训练集：3条 Python QA 样本

```

---

## 修改数据并查看差异

追加一条新样本：

```bash
cat >> train.jsonl << 'EOF'
{"messages": [{"role": "user", "content": "Python 中如何处理异常？"}, {"role": "assistant", "content": "使用 try/except 块捕获异常，finally 用于清理资源。"}]}
EOF

dit diff
```

输出：

```
train.jsonl: 3 → 4 rows (+1)
```

暂存并提交：

```bash
dit add train.jsonl
dit commit -m "补充异常处理样本"
```

再次查看日志，最新提交在最前：

```bash
dit log
```

---

## 行哈希机制

dit 的核心设计：每行 JSON 在入库前经过 RFC 8785 JSON Canonicalization Scheme 处理，然后计算 SHA-256。这意味着：

- 字段顺序不影响哈希：`{"a":1,"b":2}` 和 `{"b":2,"a":1}` 产生相同哈希
- 多余空格不影响：紧凑格式和美化格式的同一条数据哈希相同
- 相同内容只存储一次：跨文件的重复行自动去重

这使得 dit 能在行级别精确追踪数据变化，而不是像 git 那样只能做文件级别的 diff。

---

## 小贴士

- `dit add .` 会递归扫描当前目录下所有 `.jsonl` 文件，非 JSONL 文件作为 blob 存储
- 空 JSONL 文件可以正常暂存和提交（0 rows）
- 子目录中的文件会保留目录结构（嵌套 tree 对象）
- 在 `.dit/` 目录不存在的路径下执行 dit 命令会报错：`fatal: not a dit repository`
