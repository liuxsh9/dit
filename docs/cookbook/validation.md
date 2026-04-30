# 数据校验

Dit 提供 `dit validate` 命令，根据 `.ditvalidate.yaml` 规则文件对已提交的 JSONL 数据进行校验。校验会遍历 commit 中所有行，逐条检查是否违反规则。

## 规则文件格式

在仓库根目录创建 `.ditvalidate.yaml`：

```yaml
# 必须包含的字段（缺少任何一个即为违规）
required_fields:
  - messages
  - source

# 禁止出现的关键词（大小写不敏感，匹配整行 JSON）
forbidden_keywords:
  - "TODO"
  - "PLACEHOLDER"
  - "lorem ipsum"

# 单行最小字符数（compact JSON 格式）
min_row_chars: 50

# 单行最大字符数（compact JSON 格式）
max_row_chars: 50000
```

如果 `.ditvalidate.yaml` 不存在，所有规则默认为空/null，校验会直接通过。

## 可用规则

| 规则 | 类型 | 说明 |
|------|------|------|
| `required_fields` | list[str] | 每行 JSON 必须包含的顶层字段 |
| `forbidden_keywords` | list[str] | 行内不得出现的关键词（不区分大小写） |
| `min_row_chars` | int | 行的 compact JSON 最少字符数 |
| `max_row_chars` | int | 行的 compact JSON 最多字符数 |

字符数检查基于 `json.dumps(row, ensure_ascii=False, separators=(",",":"))` 的结果长度。

## 基本用法

```bash
# 校验 main 分支
dit validate

# 校验指定分支
dit validate --ref feature/new-data

# 输出 JSON 格式
dit validate --format json
```

## 命令参数

```
dit validate [--ref REF] [--format table|json]
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--ref` | `main` | 分支名或 commit hash |
| `--format` | `table` | 输出格式 |

## 输出示例

校验通过：

```
$ dit validate
Validating heads/main (commit abc12345)
Rules: required_fields=[messages, source]  forbidden_keywords=3  max_row_chars=50000

Checked 1700 rows across 3 files.
PASS
```

校验失败：

```
$ dit validate
Validating heads/main (commit abc12345)
Rules: required_fields=[messages, source]  forbidden_keywords=3  max_row_chars=50000

FAIL — 4 violation(s)

  File            Row  Rule               Detail
  train.jsonl       5  required_fields    missing field: source
  train.jsonl      42  forbidden_keywords keyword "TODO" found
  eval.jsonl       11  min_row_chars      row has 23 chars (minimum 50)
  eval.jsonl       88  max_row_chars      row has 51203 chars (limit 50000)
```

JSON 格式输出：

```bash
$ dit validate --format json
```

```json
{
  "status": "fail",
  "violations": [
    {
      "file": "train.jsonl",
      "row_index": 5,
      "row_hash": "a1b2c3d4...",
      "rule": "required_fields",
      "detail": "missing field: source"
    }
  ],
  "checked_rows": 1700
}
```

## 退出码

| 退出码 | 含义 |
|--------|------|
| `0` | 校验通过 (PASS) |
| `1` | 存在违规 (FAIL) |

## CI 集成

```bash
# GitHub Actions 示例
- name: Validate training data
  run: |
    dit validate --ref ${{ github.sha }}
```

```bash
# 通用 CI 脚本
dit validate --format json > validation-report.json
if [ $? -ne 0 ]; then
  echo "数据校验失败，详见 validation-report.json"
  exit 1
fi
```

## 注意事项

- 校验针对已提交的数据运行，不检查工作目录中未提交的文件
- 所有违规都会被收集并报告，不会在第一个错误处停止
- `forbidden_keywords` 匹配的是整行 JSON 的序列化文本，包括字段名
- 规则文件本身的格式错误会导致命令报错退出
