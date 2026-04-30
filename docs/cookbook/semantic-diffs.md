# 语义差异对比

`dit diff` 提供行级语义差异对比，而非传统的文本行 diff。它能区分三种变化类型：新增行、删除行、刷新行（同一问题的回答被更新）。这对 SFT 数据迭代特别有用 -- 你可以清楚看到哪些训练样本被替换了更好的回答。

---

## 基本用法

`dit diff` 默认比较工作目录与 HEAD 提交之间的差异：

```bash
dit diff
```

无变化时输出：

```
No changes.
```

---

## 三种差异类型

### 1. 新增行 (+)

向文件追加新样本：

```bash
cat >> train.jsonl << 'EOF'
{"messages": [{"role": "user", "content": "什么是协程？"}, {"role": "assistant", "content": "协程是可以暂停和恢复的函数，Python 中用 async/await 实现。"}]}
EOF

dit diff
```

输出：

```
train.jsonl: 3 → 4 rows (+1)
```

### 2. 删除行 (-)

如果删掉某行数据，diff 会显示减少的行数。

### 3. 刷新行 (~refreshed)

当你修改了某条样本的回答，但保持问题不变时，dit 会识别为"刷新"：

```bash
# 修改第一条样本的 assistant 回答，保持 user 问题不变
cat > eval.jsonl << 'EOF'
{"messages": [{"role": "user", "content": "什么是 GIL？"}, {"role": "assistant", "content": "GIL 是 CPython 的全局解释器锁，保证线程安全但限制了并行性能。推荐用 multiprocessing 绕过。"}]}
EOF

dit diff
```

输出：

```
eval.jsonl: 1 → 1 rows (~1 refreshed)
  Likely refreshed: 1 rows
```

---

## Query Fingerprint 机制

dit 如何判断一行是"刷新"而非"删除+新增"？

每行数据会计算一个 query_fingerprint：将所有 `role=user` 的 message content 拼接后做 SHA-256。当两行的 query_fingerprint 相同但 row_hash 不同时，dit 判定为同一问题的回答被刷新了。

```
行 A: user="什么是 GIL？" + assistant="旧回答"  → query_fp = sha256("什么是 GIL？")
行 B: user="什么是 GIL？" + assistant="新回答"  → query_fp = sha256("什么是 GIL？")

query_fp 相同，row_hash 不同 → 识别为 refresh
```

这在 SFT 数据迭代中非常常见：模型回答质量不够好，重新生成回答后替换原始数据。

---

## 比较指定提交

比较两个提交之间的差异：

```bash
# 比较当前 HEAD 与上一个提交
dit diff HEAD~1

# 比较两个具体的提交哈希
dit diff abc1234 def5678
```

比较分支之间的差异：

```bash
# 比较当前分支与 main 分支
dit diff main

# 比较两个分支
dit diff main feature/new-data
```

---

## 新文件的 diff

当工作目录中出现从未提交过的新文件时：

```bash
cat > extra.jsonl << 'EOF'
{"messages": [{"role": "user", "content": "什么是元类？"}, {"role": "assistant", "content": "元类是创建类的类，type 是默认元类。"}]}
EOF

dit diff
```

输出：

```
extra.jsonl: new file (1 rows)
```

---

## 行顺序变化

dit 的 manifest 保留行顺序。即使行集合完全相同，只要顺序变化就会被检测到：

```bash
# 假设 eval.jsonl 有两行，互换顺序后
dit diff
```

输出：

```
eval.jsonl: 2 → 2 rows (+2, -2)
```

顺序变化被视为"删除旧顺序的行 + 新增新顺序的行"。如果你只是想重排数据而不想产生大量 diff 噪音，建议在一次提交中完成。

---

## 小贴士

- diff 输出只显示有变化的文件，未修改的文件不会出现
- refresh 检测依赖 query_fingerprint，只对包含 `messages` 字段且有 `role=user` 的数据有效
- 对于 `instruction/response` 格式的数据，query_fingerprint 基于 `instruction` 字段
- 非 JSONL 文件（blob）的 diff 只显示文件级别的变化（modified/new/deleted），不做行级分析
