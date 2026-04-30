# 行级追溯 (Blame)

`dit blame` 追溯 JSONL 文件中每一行的来源：是谁、在哪个提交中引入了这条训练样本。当你发现一条有问题的训练数据时，blame 能帮你快速定位它的来源和上下文。

---

## 基本用法

```bash
dit blame train.jsonl
```

输出：

```
Blame for train.jsonl at heads/main (commit abc12345)

 Row  Commit    Author    Date                  Content
──────────────────────────────────────────────────────────────────────────
   0  abc1234   alice     2026-04-20 10:00 UTC  {"messages":[{"role":"user","content":"什么是...
   1  def5678   bob       2026-04-21 14:30 UTC  {"messages":[{"role":"user","content":"解释列...
   2  abc1234   alice     2026-04-20 10:00 UTC  {"messages":[{"role":"user","content":"什么是...
   3  ghi9012   alice     2026-04-22 09:15 UTC  {"messages":[{"role":"user","content":"如何处...
──────────────────────────────────────────────────────────────────────────
4 rows, 3 commits, 2 authors
```

每行显示：
- Row: 行索引（manifest 中的顺序）
- Commit: 引入该行的提交哈希（7 位缩写）
- Author: 提交作者
- Date: 提交时间
- Content: 行内容前 60 个字符

---

## 查看指定分支或提交

```bash
# 查看特定分支的 blame
dit blame train.jsonl --ref feature/new-data

# 查看特定提交的 blame
dit blame train.jsonl --ref abc1234567890abcd...
```

---

## 追溯单行历史

当你想了解某一行的完整变更历史时，使用 `--row` 参数：

```bash
dit blame train.jsonl --row 1
```

输出：

```
History for train.jsonl row 1 at heads/main

  Commit    Author  Date                  Event     Content
─────────────────────────────────────────────────────────────────────────────
  def5678   bob     2026-04-21 14:30 UTC  refresh   {"messages":[{"role":"user","content":"解释...
  abc1234   alice   2026-04-20 10:00 UTC  added     {"messages":[{"role":"user","content":"解释...
─────────────────────────────────────────────────────────────────────────────
2 events (query_fingerprint: 9a3f...b2c1)
```

事件类型：

| 事件 | 含义 |
|------|------|
| added | 该行首次出现在文件中 |
| refresh | 同一问题的回答被更新（query_fingerprint 相同，row_hash 不同） |
| removed | 该行被从文件中移除 |

`--row` 模式通过 query_fingerprint 跟踪行的身份，所以即使回答被刷新多次，也能追溯完整历史。

---

## 典型场景：定位问题样本

假设模型评估发现某条训练数据导致了不良输出，你想找到是谁添加的：

```bash
# 先搜索找到问题行的位置
dit search "有问题的回答关键词"

# 输出显示在 train.jsonl 第 42 行
# 用 blame 追溯来源
dit blame train.jsonl --row 42
```

输出会告诉你这条数据是谁在什么时候添加的，以及它是否经历过刷新。

---

## JSON 输出

用 `--format json` 获取机器可读的输出，方便脚本处理：

```bash
dit blame train.jsonl --format json
```

```json
{
  "commit_hash": "abc12345...",
  "file": "train.jsonl",
  "entries": [
    {
      "row_index": 0,
      "row_hash": "...",
      "commit_hash": "...",
      "author": "alice",
      "timestamp": 1713600000,
      "query_fingerprint": "..."
    }
  ],
  "summary": {
    "total_rows": 4,
    "unique_commits": 3,
    "unique_authors": 2
  }
}
```

---

## 小贴士

- blame 只沿第一父提交回溯（与 `git blame` 默认行为一致），合并提交作为整体归因
- blame 是实时计算的，不需要预构建索引；遇到未变化的文件会提前终止遍历
- Content 列只显示前 60 个字符，用于快速识别行内容
- 结合 `dit search` 和 `dit blame` 可以快速完成"找到问题数据 → 追溯来源"的工作流
