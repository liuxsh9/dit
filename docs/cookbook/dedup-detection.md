# 重复检测

Dit 提供 `dit dedup` 命令，用于检测数据集中的重复行。该命令仅做检测和报告，不会自动删除或修改任何数据。

## 两种重复类型

| 类型 | 判定条件 | 严重级别 | 含义 |
|------|----------|----------|------|
| 完全重复 (Exact) | 相同 `row_hash` | WARNING | 内容完全一致，几乎肯定是非预期的 |
| 查询重复 (Query) | 相同 `query_fingerprint`，不同 `row_hash` | INFO | 同一问题的不同回答，蒸馏场景下属于正常现象 |

完全重复意味着两行的 JSON 内容逐字节相同（包括 query 和 response）。这通常是数据处理流程中的 bug，应当引起重视。

查询重复则是同一个 prompt 对应了多个不同的 response，这在 distillation 工作流中是有意为之的。

## 基本用法

```bash
# 检测当前 main 分支的重复
dit dedup

# 指定分支或 commit
dit dedup --ref feature/v2

# 只检查特定路径前缀下的文件
dit dedup --path train

# 只看完全重复
dit dedup --exact-only

# 只看查询重复
dit dedup --query-only

# 输出 JSON 格式（适合脚本处理）
dit dedup --format json
```

## 命令参数

```
dit dedup [--ref REF] [--path PREFIX] [--format table|json] [--exact-only] [--query-only]
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--ref` | `main` | 分支名或 64 位 commit hash |
| `--path` | 无 | 路径前缀过滤 |
| `--format` | `table` | 输出格式：table 或 json |
| `--exact-only` | false | 仅显示完全重复 |
| `--query-only` | false | 仅显示查询重复 |

## 输出示例

发现重复时：

```
$ dit dedup
Duplicate detection for heads/main (commit abc12345)

⚠ EXACT DUPLICATES (2 groups, 6 rows) — identical content
────────────────────────────────────────────────────────────────
  row_hash    Count  Files
  a1b2c3d4      3x   train.jsonl (x2), eval.jsonl (x1)
  e5f6g7h8      3x   train.jsonl (x3)

ℹ QUERY DUPLICATES (3 groups, 8 rows) — same query, different response
────────────────────────────────────────────────────────────────
  fingerprint  Variants  Files
  9a3fb2c1     2 variants  train.jsonl (x2)
  d4e5f6a7     3 variants  train.jsonl (x2), eval.jsonl (x1)

Summary: 100 rows across 5 files
  Exact duplicates: 2 groups (6 rows) ⚠ WARNING
  Query duplicates: 3 groups (8 rows) ℹ INFO
```

无重复时：

```
$ dit dedup
Duplicate detection for heads/main (commit abc12345)

No duplicates found. 100 rows across 5 files.
```

## 退出码与 CI 集成

| 退出码 | 含义 |
|--------|------|
| `0` | 无重复，或仅有查询重复 (INFO) |
| `1` | 存在完全重复 (WARNING) |

在 CI 中可以直接用退出码做质量门禁：

```bash
# CI pipeline 示例
dit dedup --exact-only
if [ $? -ne 0 ]; then
  echo "发现完全重复行，请检查数据"
  exit 1
fi
```

## 跨文件检测

`dit dedup` 会扫描 commit 中所有 manifest 文件。如果同一行出现在 `train.jsonl` 和 `eval.jsonl` 中，会被报告为完全重复。这是最常见的跨文件重复场景——训练集和评估集不应有重叠。

## 注意事项

- dedup 是纯检测工具，不会删除、清理或修改任何数据
- 完全重复 (identical row_hash) 应视为严重警告，需要人工排查
- 查询重复在蒸馏场景下是正常的，不必恐慌
- 检测基于精确 hash 匹配，不做模糊匹配或语义相似度判断
- 每次运行都是实时计算，不缓存结果
