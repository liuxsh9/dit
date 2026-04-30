# 数据导出

`dit export` 从 object store 中还原 JSONL 文件，写入本地目录。支持 JSONL 和 CSV 两种格式，可选附带 sidecar 元数据。

## 基本用法

```bash
# 导出 main 分支的所有文件到当前目录
dit export

# 导出到指定目录
dit export --output ./exported

# 导出指定分支
dit export --ref feature/v2 --output ./exported

# 导出单个文件
dit export --file train.jsonl --output ./exported
```

## 命令参数

```
dit export [--ref REF] [--file PATH] [--format jsonl|csv] [--include-meta] [--output PATH]
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--ref` | `main` | 分支名或 commit hash |
| `--file` | 无（导出全部） | 只导出指定文件 |
| `--format` | `jsonl` | 输出格式：`jsonl` 或 `csv` |
| `--include-meta` | false | 同时导出 `.meta.json` 元数据文件 |
| `--output` | `.`（当前目录） | 输出目录 |

## 导出为 JSONL

```bash
$ dit export --output ./data
Exporting from main (commit abc12345)
  train.jsonl (1500 rows)... done
  eval.jsonl (200 rows)... done
Exported 2 files to ./data/
```

导出的 JSONL 文件每行一个 JSON 对象，与原始数据格式一致：

```jsonl
{"messages":[{"role":"user","content":"什么是机器学习？"},{"role":"assistant","content":"机器学习是..."}],"source":"textbook"}
{"messages":[{"role":"user","content":"解释梯度下降"},{"role":"assistant","content":"梯度下降是..."}],"source":"wiki"}
```

## 导出为 CSV

```bash
$ dit export --format csv --output ./csv-data
Exporting from main (commit abc12345)
  train.csv (1500 rows)... done
  eval.csv (200 rows)... done
Exported 2 files to ./csv-data/
```

CSV 格式会将顶层 JSON 字段展开为列。嵌套对象和数组会被序列化为 JSON 字符串。列名按字母排序。

## 附带元数据导出

```bash
$ dit export --include-meta --output ./exported
Exporting from main (commit abc12345)
  train.jsonl (1500 rows)... done
  train.jsonl.meta.json... done
  eval.jsonl (200 rows)... done
  eval.jsonl.meta.json... done
Exported 2 files to ./exported/
```

生成的 `.meta.json` 文件包含该文件的 sidecar 汇总信息：

```json
{
  "file": "train.jsonl",
  "manifest_hash": "abc123...",
  "sidecar_hash": "def456...",
  "row_count": 1500,
  "char_count": 4521000,
  "token_estimate": 1130250,
  "avg_fields": 5.2,
  "lang_distribution": {"zh": 0.82, "en": 0.18}
}
```

只有已计算 sidecar 的文件才会生成 `.meta.json`。如果需要元数据，先运行 `dit meta compute`。

## 输出目录结构

导出会保留仓库中的目录结构：

```
exported/
  train.jsonl
  train.jsonl.meta.json
  eval.jsonl
  eval.jsonl.meta.json
  subdir/
    extra.jsonl
    extra.jsonl.meta.json
```

## 导出历史版本

```bash
# 导出某个特定 commit
dit export --ref abc1234567890abcdef... --output ./snapshot

# 导出某个分支的最新状态
dit export --ref experiment/gpt4-distill --output ./gpt4-data
```

## 典型场景

训练前导出数据：

```bash
# 导出最新数据用于训练
dit export --output /data/training/
python train.py --data /data/training/train.jsonl
```

版本快照归档：

```bash
# 导出带元数据的完整快照
dit export --ref v1.0 --include-meta --output ./release/v1.0/
```

格式转换：

```bash
# 导出为 CSV 供非技术人员查看
dit export --format csv --file eval.jsonl --output ./for-review/
```

## 注意事项

- 导出的是已提交的数据，不包含工作目录中未提交的变更
- `--output` 目录不存在时会自动创建
- 导出大文件时所有行会加载到内存，注意可用内存
- CSV 格式适合小规模数据查看，大规模训练数据建议使用 JSONL
- `--file` 参数需要匹配仓库中的完整路径（如 `subdir/data.jsonl`）
