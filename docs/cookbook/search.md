# 全文搜索

`dit search` 在已提交的数据中搜索包含指定关键词的行。搜索是大小写不敏感的子串匹配，支持按字段路径过滤，适合在大量训练数据中快速定位特定样本。

---

## 基本搜索

```bash
dit search "LRU缓存"
```

输出：

```
Searching heads/main (commit abc12345) for "LRU缓存"

File            Row   Excerpt
────────────────────────────────────────────────────────────────────────
train.jsonl     42    ...实现一个LRU缓存，支持get和put操作...
train.jsonl     187   ...LRU缓存淘汰策略是指最近最少使用...
eval.jsonl      5     ...LRU缓存的时间复杂度为O(1)...
────────────────────────────────────────────────────────────────────────
3 matches (scanned 1700 rows)
```

Excerpt 列显示匹配位置前后各约 20 个字符的上下文。

---

## 限定文件范围

只在指定文件或目录中搜索：

```bash
# 搜索单个文件
dit search "装饰器" train.jsonl

# 搜索子目录下的所有文件
dit search "装饰器" data/coding/
```

输出：

```
Searching heads/main (commit abc12345) for "装饰器" in train.jsonl

File          Row   Excerpt
──────────────────────────────────────────────────────────────────
train.jsonl   12    ...Python 的装饰器是一个高阶函数...
──────────────────────────────────────────────────────────────────
1 match (scanned 500 rows)
```

---

## 按字段过滤

使用 `--field` 参数限定搜索范围到 JSON 的特定字段，避免误匹配：

```bash
# 只在 user 消息中搜索
dit search --field messages[0].content "排序算法"

# 只在 assistant 回答中搜索
dit search --field messages[1].content "时间复杂度"
```

输出：

```
Searching heads/main (commit abc12345) for "排序算法" in field messages[0].content

File          Row   Excerpt
──────────────────────────────────────────────────────────────────
train.jsonl   42    ...请解释常见的排序算法及其复杂度...
──────────────────────────────────────────────────────────────────
1 match (scanned 1700 rows)
```

字段路径支持点号和方括号索引：

| 路径 | 含义 |
|------|------|
| `messages[0].content` | 第一条消息的 content |
| `messages[1].content` | 第二条消息的 content |
| `instruction` | 顶层 instruction 字段 |
| `meta.source` | 嵌套的 meta.source 字段 |

如果某行数据中不存在指定的字段路径，该行会被静默跳过。

---

## 搜索指定分支或提交

```bash
# 搜索特定分支
dit search --ref feature/new-data "正则表达式"

# 搜索特定提交
dit search --ref abc1234 "正则表达式"
```

---

## 控制结果数量

默认最多返回 50 条匹配。达到上限时会提示：

```
Limit reached. Pass --limit N to see more.
```

调整上限：

```bash
dit search --limit 200 "Python"
```

---

## JSON 输出

用 `--format json` 获取结构化输出，方便与其他工具配合：

```bash
dit search --format json "LRU缓存"
```

```json
{
  "commit_hash": "abc12345...",
  "query": "LRU缓存",
  "field_path": null,
  "matches": [
    {
      "file": "train.jsonl",
      "row_index": 42,
      "row_hash": "3a9f...",
      "content": {"messages": [{"role": "user", "content": "..."}]},
      "highlight": "...实现一个LRU缓存，支持get和put..."
    }
  ],
  "total_scanned": 1700,
  "limit_reached": false
}
```

---

## 搜索 + Blame 组合

一个常见的工作流：先搜索定位问题数据，再用 blame 追溯来源。

```bash
# 1. 搜索包含特定内容的行
dit search "有问题的回答"
# 发现在 train.jsonl 第 42 行

# 2. 追溯这行的来源
dit blame train.jsonl --row 42
# 发现是 bob 在 4 月 21 日添加的
```

---

## 小贴士

- 搜索只在已提交的数据上执行，工作目录中未提交的修改不会被搜索到
- 搜索是大小写不敏感的子串匹配，不支持正则表达式
- `--field` 过滤在数据 schema 不统一时很有用：不存在该字段的行会被跳过而非报错
- `total_scanned` 字段告诉你搜索扫描了多少行，可以用来评估搜索开销
- 搜索是暴力遍历，没有索引。对于百万行级别的数据集，建议用 `--limit` 和文件路径缩小范围
