# Sidecar 元数据

Dit 的 sidecar 系统为每个 JSONL 文件的每一行计算元数据（字符数、token 估算、字段数、语言），存储为独立的 content-addressable 对象。元数据按需计算，不影响 commit 速度。

## 元数据字段

每行数据会生成以下 sidecar 字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `char_count` | int | UTF-8 字符数 |
| `token_estimate` | int | 粗略 token 估算 (`char_count // 4`) |
| `field_count` | int | 顶层 JSON 字段数 |
| `lang` | str/null | 检测到的语言：`zh`/`en`/`ru`/`ar`/`null` |

语言检测基于 Unicode 范围启发式：CJK 字符判定为 zh，西里尔字母为 ru，阿拉伯字母为 ar，其余默认 en。最长字符串不足 10 字符时返回 null。

## 计算元数据

```bash
# 为所有缺少 sidecar 的文件计算元数据
dit meta compute

# 只计算指定文件
dit meta compute --file train.jsonl
```

输出：

```
$ dit meta compute
Computing metadata for train.jsonl (1500 rows)... done (sidecar: abc123)
Computing metadata for eval.jsonl (200 rows)... done (sidecar: def456)
Created commit: 789abc "meta: compute sidecar metadata"
```

`dit meta compute` 会创建一个新 commit，将 sidecar hash 关联到对应的 manifest。该操作是幂等的——已有 sidecar 的文件会被跳过。

## 查看元数据

```bash
# 表格格式（默认）
dit meta show train.jsonl

# JSON 格式
dit meta show train.jsonl --format json
```

表格输出：

```
$ dit meta show train.jsonl
File: train.jsonl (1500 rows)
Sidecar: abc123

  Total chars:    4,521,000
  Token estimate: 1,130,250
  Avg fields/row: 5.2
  Languages:      zh (82%), en (18%)
```

## 对比元数据

```bash
# 对比两个 commit 之间的元数据变化
dit meta diff abc12345 def67890

# 限定到某个文件
dit meta diff abc12345 def67890 --file train.jsonl
```

输出：

```
$ dit meta diff abc12345 def67890
train.jsonl:
  Rows:           1500 → 1620 (+120)
  Token estimate:  1.13M → 1.22M (+90K)
  Languages:       zh 82%→80%, en 18%→20%
```

这在 PR review 时很有用——快速了解数据量和语言分布的变化。

## 仓库统计

`dit stats` 聚合所有文件的 sidecar 数据，给出仓库级别的概览：

```bash
# 全仓库统计
dit stats

# 指定路径
dit stats train.jsonl

# 对比两个版本
dit stats --compare abc12345 def67890

# JSON 输出
dit stats --format json
```

输出：

```
$ dit stats
Repo stats at heads/main (commit abc12345)

File                   Rows     Tokens    Chars    Avg fields  Lang
─────────────────────────────────────────────────────────────────────
train.jsonl            1,500    ~375K     1.5M     4.2         zh 82%
eval.jsonl               200    ~48K      192K     4.1         zh 79%
─────────────────────────────────────────────────────────────────────
TOTAL                  1,700    ~423K     1.69M    4.2         zh 81%
```

## 典型工作流

```bash
# 1. 添加数据并提交
dit add train.jsonl
dit commit -m "add training data v2"

# 2. 计算元数据
dit meta compute

# 3. 查看统计
dit stats

# 4. 提交新版本后对比变化
dit meta diff HEAD~1 HEAD
```

## 注意事项

- `dit meta compute` 是惰性的，只在显式调用时才计算
- token 估算使用 `char_count // 4`，是粗略值，不等同于实际 tokenizer 结果
- 语言检测是简单的 Unicode 范围启发式，不使用 NLP 模型
- sidecar 对象通过 push/pull 自动同步到远端
- 没有 sidecar 的文件在 `dit stats` 中显示为 `—`，不计入汇总
- 相同内容的 manifest 总是产生相同的 sidecar hash（确定性计算）
