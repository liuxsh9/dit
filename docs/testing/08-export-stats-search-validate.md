# 08 — Export、Stats、Search、Validate 与 CI Checks

> **前置条件：** 已完成指南 07，理解 sidecar 的生成流程；API 章节需要 API Server 运行于 `http://localhost:8000` 并准备好有效 Token。
> **测试环境：** 本指南使用独立本地仓库 `~/dit-08-test`，避免复用前面指南留下的提交状态。

---

## 1. 前置条件确认

### 1.1 创建测试仓库

```bash
rm -rf ~/dit-08-test
mkdir -p ~/dit-08-test && cd ~/dit-08-test
dit init

cat > train.jsonl << 'EOF'
{"instruction":"实现一个LRU缓存，支持get和put操作","response":"可以用哈希表加双向链表实现LRU缓存。get和put操作都可以保持O(1)。"}
{"instruction":"什么是快速排序","response":"快速排序是一种分治排序算法，通过选择基准值将数组划分为较小和较大的两部分。"}
EOF

cat > eval.jsonl << 'EOF'
{"instruction":"LRU缓存的时间复杂度","response":"LRU缓存通常要求get和put都是O(1)。"}
EOF

dit add train.jsonl eval.jsonl
dit commit -m "add training data v1"
dit meta compute

# 形成第二个有 sidecar 的版本，供 stats --compare 和 --ref 测试使用
echo '{"instruction":"LRU缓存淘汰策略是什么","response":"当容量满时，LRU缓存会淘汰最近最少使用的条目。"}' >> train.jsonl
dit add train.jsonl
dit commit -m "add LRU eviction sample"
dit meta compute

dit log
```

预期输出（至少四次提交，最新一条为 meta 提交）：

```
commit a1b2c3d4e5f6...
Author: alice
Date:   2026-04-25 10:00:00 UTC

    meta: compute sidecar metadata

commit 8f9e7d6c5b4a...
Author: alice
Date:   2026-04-25 09:30:00 UTC

    add LRU eviction sample
```

- [ ] `dit log` 至少显示四条提交
- [ ] 最新提交消息包含 `meta: compute sidecar metadata`

### 1.2 确认 sidecar 已就绪

```bash
dit meta show train.jsonl
```

预期输出（示例）：

```
File: train.jsonl (3 rows)
Sidecar: 3a7bc1f2

  Total chars:    <N>
  Token estimate: <N/4 左右>
  Avg fields/row: 2.0
  Languages:      zh (100%)
```

- [ ] 输出中包含 `Sidecar:` 行，不报 `run 'dit meta compute' first`

### 1.3 记录关键哈希

```bash
# 记录当前 HEAD 的 meta commit，供后续测试使用
COMMIT_HEAD=$(dit log --format json | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[0]['hash'])")
echo "COMMIT_HEAD=$COMMIT_HEAD"

# 记录上一轮 meta commit（用于 stats --compare 和 export --ref）
COMMIT_PARENT=$(dit log --format json | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[2]['hash'])")
echo "COMMIT_PARENT=$COMMIT_PARENT"
```

- [ ] `COMMIT_HEAD` 为 64 位十六进制字符串
- [ ] `COMMIT_PARENT` 为 64 位十六进制字符串，且不等于 `COMMIT_HEAD`

---

## 2. CLI: export

### 2.1 默认导出（JSONL 格式）

```bash
mkdir -p /tmp/dit-export-test
dit export --output /tmp/dit-export-test
```

预期输出示例：

```
Exporting from main (commit a1b2c3d4)
  eval.jsonl (1 row)... done
  train.jsonl (3 rows)... done
Exported 2 files to /tmp/dit-export-test/
```

验证导出文件：

```bash
wc -l /tmp/dit-export-test/train.jsonl
python3 - <<'PY'
import json
from pathlib import Path
for line in Path("/tmp/dit-export-test/train.jsonl").read_text().splitlines():
    json.loads(line)
print("JSONL OK")
PY
```

- [ ] `Exported N files` 消息出现
- [ ] `/tmp/dit-export-test/train.jsonl` 文件存在
- [ ] 行数与仓库中行数一致
- [ ] 每行均为合法 JSON 对象

### 2.2 导出为 CSV 格式

```bash
mkdir -p /tmp/dit-export-csv
dit export --format csv --output /tmp/dit-export-csv
```

预期输出示例：

```
Exporting from main (commit a1b2c3d4)
  eval.jsonl (1 row)... done
  train.jsonl (3 rows)... done
Exported 2 files to /tmp/dit-export-csv/
```

验证 CSV 内容：

```bash
head -3 /tmp/dit-export-csv/train.jsonl
```

预期（字段按字母序排列，嵌套值 JSON 序列化）：

```
instruction,response
"实现一个LRU缓存，支持get和put操作","好的，LRU缓存可以..."
"什么是快速排序","快速排序是..."
```

- [ ] 首行为 CSV 表头（字段名按字母序）
- [ ] 嵌套结构（list/dict）以 JSON 字符串形式出现在单元格中

### 2.3 --file 过滤器：只导出单个文件

```bash
mkdir -p /tmp/dit-export-single
dit export --file train.jsonl --output /tmp/dit-export-single
```

预期输出示例：

```
Exporting from main (commit a1b2c3d4)
  train.jsonl (3 rows)... done
Exported 1 file to /tmp/dit-export-single/
```

- [ ] 仅创建 `train.jsonl`，`eval.jsonl` 不存在

```bash
ls /tmp/dit-export-single/
```

- [ ] 只有 `train.jsonl`，无 `eval.jsonl`

### 2.4 --include-meta：同时导出元数据文件

```bash
mkdir -p /tmp/dit-export-meta
dit export --include-meta --output /tmp/dit-export-meta
```

预期输出示例：

```
Exporting from main (commit a1b2c3d4)
  eval.jsonl (1 row)... done
  eval.jsonl.meta.json... done
  train.jsonl (3 rows)... done
  train.jsonl.meta.json... done
Exported 2 files to /tmp/dit-export-meta/
```

验证 meta 文件内容：

```bash
cat /tmp/dit-export-meta/train.jsonl.meta.json
```

预期 JSON 结构：

```json
{
  "file": "train.jsonl",
  "manifest_hash": "abc123...",
  "sidecar_hash": "def456...",
  "row_count": 3,
  "char_count": 300,
  "token_estimate": 75,
  "avg_fields": 2.0,
  "lang_distribution": {"zh": 3}
}
```

- [ ] `train.jsonl.meta.json` 文件存在
- [ ] 包含 `manifest_hash`、`sidecar_hash`、`row_count` 字段
- [ ] 无 sidecar 的文件不生成 `.meta.json`（见 12.3）

### 2.5 --ref 指定分支或 commit hash

```bash
mkdir -p /tmp/dit-export-ref
dit export --ref $COMMIT_PARENT --output /tmp/dit-export-ref
```

- [ ] 使用 commit hash 直接引用时导出成功（退出码 0）
- [ ] 导出的行数与父提交时一致（少于当前 HEAD）

### 2.6 错误场景：导出不存在的文件

```bash
dit export --file nonexistent.jsonl --output /tmp/dit-export-test
```

预期：

```
fatal: 'nonexistent.jsonl' not found in commit a1b2c3d4
```

- [ ] 退出码为 1
- [ ] 错误消息包含文件名

---

## 3. CLI: stats

### 3.1 默认统计（所有文件）

```bash
dit stats
```

预期输出示例：

```
Repo stats at main (commit a1b2c3d4)

File           Rows    Tokens      Chars  Avg fields  Lang
────────────────────────────────────────────────────────────
eval.jsonl        1      ~24       99         2.0  zh 100%
train.jsonl       3      ~91      365         2.0  zh 100%
────────────────────────────────────────────────────────────
TOTAL             4     ~115      464               zh 100%
```

- [ ] 表头包含 `File`、`Rows`、`Tokens`、`Chars`、`Avg fields`、`Lang`
- [ ] 每个有 sidecar 的文件显示数值
- [ ] `TOTAL` 行汇总数字正确
- [ ] 无 sidecar 的文件显示 `—`（em dash）

### 3.2 路径过滤器

```bash
dit stats train.jsonl
```

预期输出只包含 `train.jsonl`，不包含 `eval.jsonl`。

- [ ] `train.jsonl` 出现
- [ ] `eval.jsonl` 不出现

```bash
dit stats data/
```

若存在 `data/` 目录前缀的文件，仅显示该前缀下的文件。

- [ ] 只显示路径以 `data/` 开头的文件

### 3.3 --compare 对比两个提交

```bash
dit stats --compare $COMMIT_PARENT $COMMIT_HEAD
```

预期输出示例：

```
Stats delta: 8f9e7d6c -> a1b2c3d4

File           Rows (delta)        Tokens (delta)    Chars (delta)
────────────────────────────────────────────────────────────────────
train.jsonl        2 -> 3 (+1)      ~65 -> ~91 (+~26)  260 -> 365 (+105)
────────────────────────────────────────────────────────────────────
TOTAL                         +1                 +~26              +105
```

- [ ] 标题行显示两个 commit 的前 8 位
- [ ] Delta 列显示正负变化量（如 `+100`、`-20`）
- [ ] `TOTAL` 行汇总各 delta

### 3.4 --format json

```bash
dit stats --format json
```

验证 JSON 结构：

```bash
dit stats --format json | python3 -c "
import json, sys
data = json.load(sys.stdin)
assert 'commit_hash' in data
assert 'files' in data
assert 'totals' in data
assert isinstance(data['files'], list)
for f in data['files']:
    assert 'path' in f
    assert 'has_sidecar' in f
    if f['has_sidecar']:
        assert f['row_count'] is not None
print('JSON structure OK')
"
```

- [ ] 脚本输出 `JSON structure OK`
- [ ] 有 sidecar 的文件 `has_sidecar: true`，无 sidecar 的文件 `has_sidecar: false`

### 3.5 --compare --format json

```bash
dit stats --compare $COMMIT_PARENT $COMMIT_HEAD --format json | python3 -c "
import json, sys
data = json.load(sys.stdin)
assert 'commit1' in data
assert 'commit2' in data
assert 'files' in data
assert 'totals_delta' in data
for f in data['files']:
    assert 'path' in f
    assert 'old' in f
    assert 'new' in f
    assert 'delta' in f
    assert 'row_count' in f['delta']
print('compare JSON OK')
"
```

- [ ] 脚本输出 `compare JSON OK`

---

## 4. CLI: search

### 4.1 基本文本搜索

```bash
dit search "LRU缓存"
```

预期输出：

```
Searching heads/main (commit a1b2c3d4) for "LRU缓存"

File            Row  Excerpt
───────────────────────────────────────────────────────
train.jsonl       0  ...实现一个LRU缓存，支持get和put...
train.jsonl       1  ...LRU缓存淘汰策略...
eval.jsonl        0  ...LRU缓存的时间复杂度...
───────────────────────────────────────────────────────
3 matches (scanned 4 rows)
```

- [ ] 标题行包含查询词 `LRU缓存`
- [ ] 匹配行显示文件名、行索引、摘录（包含关键词上下文）
- [ ] `N matches (scanned M rows)` 汇总行出现
- [ ] 搜索大小写不敏感（搜索 `lru缓存` 结果相同）

### 4.2 路径过滤

```bash
dit search "LRU缓存" train.jsonl
```

- [ ] 结果只包含 `train.jsonl` 的匹配，`eval.jsonl` 不出现

### 4.3 --field 字段过滤

```bash
dit search "LRU缓存" --field "messages[0].content"
```

预期：只搜索 `messages[0].content` 字段值，其他字段不参与匹配。

验证：

```bash
dit search "hello" --field "instruction"
```

- [ ] 只在 `instruction` 字段匹配的行出现，`response` 字段中的 "hello" 不触发匹配

**嵌套路径格式说明：**

| 路径表达式 | 含义 |
|---|---|
| `instruction` | 顶层字段 |
| `messages[0].content` | messages 数组第 0 个元素的 content |
| `meta.source` | 嵌套 meta 对象的 source 字段 |

### 4.4 --limit 限制结果数

```bash
dit search "的" --limit 3
```

预期输出末尾：

```
3 matches (scanned N rows)
Limit reached. Pass --limit N to see more.
```

- [ ] 结果恰好 3 条
- [ ] 提示语 `Limit reached` 出现

### 4.5 无匹配

```bash
dit search "zzznomatch_xyz"
```

预期输出：

```
0 matches
(scanned 4 rows)
```

- [ ] 退出码为 0（无匹配不是错误）
- [ ] 显示扫描行数

### 4.6 --format json

```bash
dit search "LRU缓存" --format json
```

验证 JSON 结构：

```bash
dit search "LRU缓存" --format json | python3 -c "
import json, sys
data = json.load(sys.stdin)
assert 'commit_hash' in data
assert 'query' in data and data['query'] == 'LRU缓存'
assert 'matches' in data
assert 'total_scanned' in data
assert 'limit_reached' in data
for m in data['matches']:
    assert 'file' in m
    assert 'row_index' in m
    assert 'row_hash' in m
    assert 'content' in m
    assert 'highlight' in m
print('search JSON OK')
"
```

- [ ] 脚本输出 `search JSON OK`
- [ ] `highlight` 字段包含查询词附近的文本摘录

---

## 5. CLI: validate

### 5.1 创建 .ditvalidate.yaml 规则文件

在仓库根目录创建规则文件：

```bash
cat > ~/dit-08-test/.ditvalidate.yaml << 'EOF'
required_fields:
  - instruction
  - response

forbidden_keywords:
  - "PLACEHOLDER"
  - "TODO"

max_row_chars: 10000
min_row_chars: 20
EOF
```

- [ ] 文件已创建于仓库根目录（与 `.dit/` 同级）

### 5.2 规则文件无需提交即可生效（CLI 直接读取工作目录）

```bash
dit validate
```

预期输出（所有行符合规则）：

```
Validating heads/main (commit a1b2c3d4)
Rules: required_fields=[instruction, response]  forbidden_keywords=2  max_row_chars=10000  min_row_chars=20

Checked 4 rows across 2 files.
PASS
```

- [ ] 退出码为 0
- [ ] 输出 `PASS`
- [ ] 规则摘要行列出已激活的规则

### 5.3 制造违规并验证

临时写入一条违规数据，暂存并提交：

```bash
# 创建包含违规的测试数据（缺少 response 字段，含禁止词）
echo '{"instruction":"test missing response"}' > /tmp/bad_data.jsonl
echo '{"instruction":"正常指令","response":"包含PLACEHOLDER的回答"}' >> /tmp/bad_data.jsonl
cp /tmp/bad_data.jsonl ~/dit-08-test/bad.jsonl

dit add bad.jsonl
dit commit -m "add bad data for validate test"
```

运行验证：

```bash
dit validate
```

预期输出：

```
Validating heads/main (commit b2c3d4e5)
Rules: required_fields=[instruction, response]  forbidden_keywords=2  max_row_chars=10000  min_row_chars=20

FAIL — 2 violation(s)

File        Row   Rule                Detail
────────────────────────────────────────────────────────────────────────────────
bad.jsonl     0   required_fields     missing field: response
bad.jsonl     1   forbidden_keywords  keyword "PLACEHOLDER" found
────────────────────────────────────────────────────────────────────────────────
Checked 6 rows across 3 files.
```

- [ ] 退出码为 1
- [ ] 输出 `FAIL — N violation(s)`
- [ ] 违规表格包含 `File`、`Row`、`Rule`、`Detail` 列
- [ ] 每条违规的 `rule` 字段为 `required_fields` 或 `forbidden_keywords`

### 5.4 修复违规后重新验证

删除违规文件，提交修复：

```bash
rm ~/dit-08-test/bad.jsonl
dit add bad.jsonl
dit commit -m "remove bad data"

dit validate
```

- [ ] 退出码为 0
- [ ] 输出 `PASS`

### 5.5 --format json

```bash
dit validate --format json
```

验证：

```bash
dit validate --format json | python3 -c "
import json, sys
data = json.load(sys.stdin)
assert 'status' in data
assert 'violations' in data
assert 'checked_rows' in data
assert data['status'] in ('pass', 'fail')
if data['violations']:
    v = data['violations'][0]
    assert 'file' in v
    assert 'row_index' in v
    assert 'row_hash' in v
    assert 'rule' in v
    assert 'detail' in v
print('validate JSON OK, status:', data['status'])
"
```

- [ ] 脚本输出 `validate JSON OK, status: pass`（或 fail）
- [ ] `violations` 数组为空（pass 状态）

---

## 6. API: export

> **准备工作：** 确认 API Server 运行，获取有效 Token，并将本地 `~/dit-08-test` 推送到服务端。

```bash
export TOKEN="<你的 admin 或 committer token>"
export BASE_URL="http://localhost:8000"
export BASE="$BASE_URL/api/v1/repos"
export REPO="dit-08-test"

curl -s -X POST "$BASE_URL/api/v1/repos" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"$REPO\"}" | python3 -m json.tool

cd ~/dit-08-test
dit remote add origin "$BASE_URL/$REPO" --token "$TOKEN"
dit push origin main
```

- [ ] 服务端仓库创建成功，或已存在时确认后续 push 可用
- [ ] `dit push origin main` 成功，服务端 `heads/main` 指向当前本地 HEAD

### 6.1 导出为 JSONL

```bash
curl -s \
  -H "Authorization: Bearer $TOKEN" \
  "$BASE/$REPO/export/$COMMIT_HEAD/train.jsonl?format=jsonl"
```

预期：返回原始 JSONL 内容（每行一个 JSON 对象），Content-Type 为 `application/x-ndjson`。

```bash
curl -s -D - -o /tmp/dit-api-export.jsonl \
  -H "Authorization: Bearer $TOKEN" \
  "$BASE/$REPO/export/$COMMIT_HEAD/train.jsonl?format=jsonl" | grep content-type
```

- [ ] `content-type: application/x-ndjson`
- [ ] 响应体每行均为合法 JSON

### 6.2 导出为 CSV

```bash
curl -s \
  -H "Authorization: Bearer $TOKEN" \
  "$BASE/$REPO/export/$COMMIT_HEAD/train.jsonl?format=csv" \
  | head -5
```

- [ ] 首行为 CSV 表头
- [ ] Content-Type 为 `text/csv`

```bash
curl -s -D - -o /tmp/dit-api-export.csv \
  -H "Authorization: Bearer $TOKEN" \
  "$BASE/$REPO/export/$COMMIT_HEAD/train.jsonl?format=csv" | grep content-type
```

### 6.3 导出不存在的文件

```bash
curl -s -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer $TOKEN" \
  "$BASE/$REPO/export/$COMMIT_HEAD/nonexistent.jsonl?format=jsonl"
```

- [ ] HTTP 状态码为 `404`

### 6.4 无效 format 参数

```bash
curl -s -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer $TOKEN" \
  "$BASE/$REPO/export/$COMMIT_HEAD/train.jsonl?format=xlsx"
```

- [ ] HTTP 状态码为 `400`

---

## 7. API: stats

### 7.1 获取 commit 的全量统计

```bash
curl -s \
  -H "Authorization: Bearer $TOKEN" \
  "$BASE/$REPO/stats/$COMMIT_HEAD" | python3 -m json.tool | head -40
```

预期 JSON 结构：

```json
{
  "commit_hash": "a1b2c3d4...",
  "files": [
    {
      "path": "eval.jsonl",
      "row_count": 100,
      "char_count": 50000,
      "token_estimate": 12500,
      "avg_fields": 2.5,
      "lang_distribution": {"en": 60, "zh": 40},
      "has_sidecar": true
    },
    {
      "path": "train.jsonl",
      "row_count": 500,
      "char_count": 250000,
      "token_estimate": 62500,
      "avg_fields": 3.0,
      "lang_distribution": {"zh": 400, "en": 100},
      "has_sidecar": true
    }
  ],
  "totals": {
    "file_count": 2,
    "files_with_sidecar": 2,
    "row_count": 600,
    "char_count": 300000,
    "token_estimate": 75000,
    "lang_distribution": {"zh": 440, "en": 160}
  }
}
```

- [ ] 包含 `commit_hash`、`files`、`totals` 三个顶层字段
- [ ] `totals.file_count` 等于 `files` 数组长度
- [ ] 无 sidecar 的文件 `has_sidecar: false`，数值字段为 `null`

### 7.2 路径过滤 ?path=

```bash
curl -s \
  -H "Authorization: Bearer $TOKEN" \
  "$BASE/$REPO/stats/$COMMIT_HEAD?path=train.jsonl" | python3 -m json.tool
```

- [ ] `files` 数组只包含路径以 `train.jsonl` 开头的文件
- [ ] `totals.file_count` 对应过滤后的数量

### 7.3 不存在的 commit

```bash
curl -s -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer $TOKEN" \
  "$BASE/$REPO/stats/0000000000000000000000000000000000000000000000000000000000000000"
```

- [ ] HTTP 状态码为 `404`

---

## 8. API: search

### 8.1 基本搜索请求

```bash
curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "LRU缓存", "ref": "heads/main"}' \
  "$BASE/$REPO/search" | python3 -m json.tool | head -40
```

预期 JSON 结构：

```json
{
  "commit_hash": "a1b2c3d4...",
  "query": "LRU缓存",
  "field_path": null,
  "matches": [
    {
      "file": "eval.jsonl",
      "row_index": 0,
      "row_hash": "abc...",
      "content": {"messages": [{"role": "user", "content": "LRU缓存的时间复杂度"}], "response": "O(1)"},
      "highlight": "...LRU缓存的时间复杂度..."
    }
  ],
  "total_scanned": 600,
  "limit_reached": false
}
```

- [ ] `matches` 数组非空
- [ ] 每条 match 包含 `file`、`row_index`、`row_hash`、`content`、`highlight`
- [ ] `total_scanned` 等于仓库总行数

### 8.2 file 字段过滤

```bash
curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "LRU缓存", "ref": "heads/main", "file": "train.jsonl"}' \
  "$BASE/$REPO/search" | python3 -c "
import json,sys; d=json.load(sys.stdin)
assert all(m['file'].startswith('train.jsonl') for m in d['matches'])
print('file filter OK')
"
```

- [ ] 脚本输出 `file filter OK`

### 8.3 field 字段过滤

```bash
curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "LRU缓存", "ref": "heads/main", "field": "messages[0].content"}' \
  "$BASE/$REPO/search" | python3 -m json.tool
```

- [ ] 只有 `messages[0].content` 字段中含有 `LRU缓存` 的行出现在结果中

### 8.4 limit 参数

```bash
curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "的", "ref": "heads/main", "limit": 2}' \
  "$BASE/$REPO/search" | python3 -c "
import json,sys; d=json.load(sys.stdin)
assert len(d['matches']) <= 2
assert d['limit_reached'] == (len(d['matches']) == 2)
print('limit OK')
"
```

- [ ] 脚本输出 `limit OK`

### 8.5 使用 commit hash 代替 ref

```bash
curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"query\": \"LRU缓存\", \"ref\": \"$COMMIT_HEAD\"}" \
  "$BASE/$REPO/search" | python3 -c "
import json,sys; d=json.load(sys.stdin); print('commit hash ref OK, matches:', len(d['matches']))
"
```

- [ ] 脚本正常输出，无 404 错误

---

## 9. API: validate

> **注意：** API 的 validate 端点从已提交的 tree 中读取 `.ditvalidate.yaml`（以 blob 形式）。  
> 若规则文件尚未提交到 tree，则使用默认规则（所有规则为空，结果始终为 pass）。

### 9.1 提交规则文件到仓库

```bash
cd ~/dit-08-test

# 确保 .ditvalidate.yaml 存在
cat .ditvalidate.yaml

# 将规则文件加入版本控制
dit add .ditvalidate.yaml
dit commit -m "add validate rules"
dit push origin main

# 更新 COMMIT_HEAD
COMMIT_HEAD=$(dit log --format json | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[0]['hash'])")
echo $COMMIT_HEAD
```

- [ ] `.ditvalidate.yaml` 已提交
- [ ] `COMMIT_HEAD` 已更新

### 9.2 基本验证请求

```bash
curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"ref": "heads/main"}' \
  "$BASE/$REPO/validate" | python3 -m json.tool
```

预期 JSON 结构（通过时）：

```json
{
  "status": "pass",
  "violations": [],
  "checked_rows": 600
}
```

- [ ] `status` 为 `"pass"` 或 `"fail"`
- [ ] `violations` 为数组
- [ ] `checked_rows` 为整数

### 9.3 带违规数据的验证响应结构

先通过 API 提交违规数据（或重用前面 CLI 测试中的 bad.jsonl 提交），然后验证：

```bash
curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"ref": "heads/main"}' \
  "$BASE/$REPO/validate" | python3 -c "
import json,sys
data = json.load(sys.stdin)
print('status:', data['status'])
print('violation count:', len(data['violations']))
if data['violations']:
    v = data['violations'][0]
    print('first violation file:', v['file'])
    print('first violation rule:', v['rule'])
    print('first violation detail:', v['detail'])
    assert 'row_hash' in v
    assert 'row_index' in v
    print('violation structure OK')
"
```

- [ ] 各 violation 包含 `file`、`row_index`、`row_hash`、`rule`、`detail` 五个字段
- [ ] `status` 与 `violations` 数组是否为空保持一致

### 9.4 使用 commit hash 调用

```bash
curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"ref\": \"$COMMIT_HEAD\"}" \
  "$BASE/$REPO/validate" | python3 -c "
import json,sys; d=json.load(sys.stdin); print('validate by hash OK, status:', d['status'])
"
```

- [ ] 正常响应，不报 404

---

## 10. API: CI Checks

CI Checks 端点允许外部 CI 系统向指定 commit 上报检查结果，并查询结果。

> **权限说明：**  
> - `POST /checks`：需要 `push` 权限  
> - `GET /checks/{commit}`：需要 `read` 权限

### 10.1 上报 CI 检查结果（pending）

```bash
curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"commit_hash\": \"$COMMIT_HEAD\",
    \"check_name\": \"data-quality\",
    \"status\": \"pending\",
    \"details\": {\"message\": \"validation in progress\"}
  }" \
  "$BASE/$REPO/checks" | python3 -m json.tool
```

预期响应（HTTP 201）：

```json
{
  "id": 1,
  "repo_id": 1,
  "commit_hash": "a1b2c3d4...",
  "check_name": "data-quality",
  "status": "pending",
  "details": {"message": "validation in progress"},
  "created_at": "2026-04-25T10:00:00",
  "updated_at": "2026-04-25T10:00:00"
}
```

- [ ] HTTP 状态码为 `201`
- [ ] 响应包含 `id`、`commit_hash`、`check_name`、`status` 字段
- [ ] `status` 为 `"pending"`

### 10.2 更新检查结果（pass）

```bash
curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"commit_hash\": \"$COMMIT_HEAD\",
    \"check_name\": \"data-quality\",
    \"status\": \"pass\",
    \"details\": {\"checked_rows\": 600, \"violations\": 0}
  }" \
  "$BASE/$REPO/checks" | python3 -c "
import json,sys; d=json.load(sys.stdin)
assert d['status'] == 'pass'
assert d['details']['violations'] == 0
print('upsert OK, id:', d['id'])
"
```

- [ ] 同一 `check_name` 上报时为 upsert（更新已有记录，`id` 不变）
- [ ] `status` 更新为 `"pass"`
- [ ] `details` 内容被更新

### 10.3 上报 fail 检查

```bash
curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"commit_hash\": \"$COMMIT_HEAD\",
    \"check_name\": \"schema-lint\",
    \"status\": \"fail\",
    \"details\": {\"errors\": [\"missing required field in row 42\"]}
  }" \
  "$BASE/$REPO/checks" | python3 -c "
import json,sys; d=json.load(sys.stdin)
assert d['status'] == 'fail'
print('fail check OK')
"
```

- [ ] 上报成功，`status` 为 `"fail"`

### 10.4 查询 commit 的所有检查结果

```bash
curl -s \
  -H "Authorization: Bearer $TOKEN" \
  "$BASE/$REPO/checks/$COMMIT_HEAD" | python3 -m json.tool
```

预期 JSON 结构：

```json
{
  "commit_hash": "a1b2c3d4...",
  "checks": [
    {
      "id": 1,
      "check_name": "data-quality",
      "status": "pass",
      "details": {"checked_rows": 600, "violations": 0},
      "created_at": "2026-04-25T10:00:00",
      "updated_at": "2026-04-25T10:01:00"
    },
    {
      "id": 2,
      "check_name": "schema-lint",
      "status": "fail",
      "details": {"errors": ["missing required field in row 42"]},
      "created_at": "2026-04-25T10:00:30",
      "updated_at": "2026-04-25T10:00:30"
    }
  ]
}
```

- [ ] 顶层包含 `commit_hash` 和 `checks` 数组
- [ ] 上报的两条记录（`data-quality`、`schema-lint`）均出现
- [ ] 检查结果按 `id` 升序排列

### 10.5 查询无检查的 commit

```bash
curl -s \
  -H "Authorization: Bearer $TOKEN" \
  "$BASE/$REPO/checks/$COMMIT_PARENT" | python3 -c "
import json,sys; d=json.load(sys.stdin)
assert d['checks'] == []
print('empty checks OK')
"
```

- [ ] `checks` 数组为空，HTTP 状态码为 `200`

---

## 11. 验证规则详解：.ditvalidate.yaml 语法

`.ditvalidate.yaml` 位于仓库根目录（与 `.dit/` 同级）。CLI 读取工作目录中的文件；API 读取已提交树中的 blob。

### 11.1 完整语法示例

```yaml
# .ditvalidate.yaml
# 所有规则均为可选，未指定则跳过对应检查

# 必需字段：每行 JSON 对象必须包含这些顶层键
required_fields:
  - instruction
  - response

# 禁止关键词：在整个行的 JSON 序列化中搜索（大小写不敏感）
forbidden_keywords:
  - "PLACEHOLDER"
  - "TODO"
  - "<insert response here>"

# 字符数上限：超过此值的行视为违规（基于紧凑 JSON 序列化）
max_row_chars: 10000

# 字符数下限：低于此值的行视为违规
min_row_chars: 20
```

### 11.2 规则字段说明

| 字段 | 类型 | 说明 | 默认值 |
|---|---|---|---|
| `required_fields` | 字符串列表 | 每行必须包含的顶层 JSON 键名 | `[]`（不检查） |
| `forbidden_keywords` | 字符串列表 | 任何一个关键词出现在行的 JSON 序列化中即违规（大小写不敏感） | `[]`（不检查） |
| `max_row_chars` | 正整数 | 行的紧凑 JSON 字符串长度上限 | `null`（不限制） |
| `min_row_chars` | 正整数 | 行的紧凑 JSON 字符串长度下限 | `null`（不限制） |

### 11.3 违规格式

每条违规记录（JSON 模式）：

```json
{
  "file": "train.jsonl",
  "row_index": 42,
  "row_hash": "abc123def456...",
  "rule": "required_fields",
  "detail": "missing field: response"
}
```

`rule` 字段取值：`required_fields`、`forbidden_keywords`、`max_row_chars`、`min_row_chars`。

### 11.4 字符计数规则

字符计数基于紧凑 JSON 序列化（`separators=(",",":")`, `ensure_ascii=False`），而非原始文件字节数。例如：

```python
import json
row = {"instruction": "你好", "response": "hello"}
compact = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
# '{"instruction":"你好","response":"hello"}'
print(len(compact))  # 38
```

- [ ] 测试一行恰好在边界：`min_row_chars=38`，上面示例行恰好通过
- [ ] 测试 `min_row_chars=39`，上面示例行违规

### 11.5 常见错误

```yaml
# 错误：required_fields 应为列表
required_fields: instruction

# 错误：max_row_chars 应为正整数
max_row_chars: "10000"

# 错误：max_row_chars 不能为 0 或负数
max_row_chars: -1
```

上述写法均触发 `ValueError: invalid .ditvalidate.yaml` 错误。

---

## 12. 边界场景

### 12.1 search：空查询

CLI 要求查询词为必填参数，空字符串行为测试：

```bash
dit search ""
```

- [ ] 空串会匹配所有行（因为任何字符串都包含空串），结果受 `--limit` 限制

### 12.2 validate：无 .ditvalidate.yaml 文件

```bash
# 临时移走规则文件
mv ~/dit-08-test/.ditvalidate.yaml /tmp/.ditvalidate.yaml.bak
dit validate
```

预期：

```
Validating heads/main (commit a1b2c3d4)

Checked 4 rows across 2 files.
PASS
```

- [ ] 无规则文件时默认所有规则为空
- [ ] 退出码为 0，输出 `PASS`
- [ ] Rules 摘要行为空（不显示 `forbidden_keywords` 等）

```bash
# 恢复规则文件
mv /tmp/.ditvalidate.yaml.bak ~/dit-08-test/.ditvalidate.yaml
```

### 12.3 export：导出没有 sidecar 的文件仍可成功

先创建一个尚未运行 `dit meta compute` 的 JSONL 文件：

```bash
cat > nosidecar.jsonl << 'EOF'
{"instruction":"No sidecar yet","response":"This row is intentionally not processed."}
EOF
dit add nosidecar.jsonl
dit commit -m "add file without sidecar"
```

```bash
mkdir -p /tmp/dit-export-nosidecar
dit export --file nosidecar.jsonl --output /tmp/dit-export-nosidecar
```

- [ ] 导出成功，退出码为 0
- [ ] `nosidecar.jsonl` 文件正常导出（export 不依赖 sidecar）
- [ ] `--include-meta` 时无 sidecar 的文件不生成 `.meta.json`

### 12.4 stats：文件无 sidecar

```bash
dit stats nosidecar.jsonl
```

预期输出：

```
Repo stats at main (commit a1b2c3d4) — nosidecar.jsonl

File        Rows    Tokens    Chars  Avg fields  Lang
───────────────────────────────────────────────────────
nosidecar.jsonl     —         —       —           —  —
───────────────────────────────────────────────────────
TOTAL          —         —       —               —

1 of 1 files have no sidecar metadata. Run 'dit meta compute' to fill gaps.
```

- [ ] 数值列显示 `—`（em dash）
- [ ] 底部显示无 sidecar 警告，提示运行 `dit meta compute`

### 12.5 search：指定不存在的 ref

```bash
dit search "hello" --ref nonexistent-branch
```

预期：

```
fatal: ref 'nonexistent-branch' not found
```

- [ ] 退出码为 1
- [ ] 错误消息含 `not found`

### 12.6 stats：对比两个相同 commit

```bash
dit stats --compare $COMMIT_HEAD $COMMIT_HEAD
```

预期：

```
Stats delta: a1b2c3d4 -> a1b2c3d4

...（delta 全为 0）
TOTAL    +0    +0    +0
```

或：

```
No files with sidecars on both sides.
```

- [ ] 退出码为 0，不报错

### 12.7 API validate：规则文件未提交时使用默认规则

```bash
# 确认 .ditvalidate.yaml 未在 COMMIT_HEAD 的 tree 中（只在工作目录）
curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"ref": "heads/main"}' \
  "$BASE/$REPO/validate" | python3 -c "
import json,sys; d=json.load(sys.stdin)
# 默认规则下无违规
print('status:', d['status'])
print('violations:', len(d['violations']))
"
```

- [ ] 规则文件未在 tree 中时，使用默认（空）规则，`status: pass`

---

## 小结：指令速查

| 功能 | CLI 命令 | API 端点 |
|---|---|---|
| 导出 JSONL | `dit export --output <dir>` | `GET /export/{commit}/{file}?format=jsonl` |
| 导出 CSV | `dit export --format csv` | `GET /export/{commit}/{file}?format=csv` |
| 导出含元数据 | `dit export --include-meta` | — |
| 仓库统计 | `dit stats` | `GET /stats/{commit}` |
| 统计对比 | `dit stats --compare <c1> <c2>` | — |
| 文本搜索 | `dit search "关键词"` | `POST /search` |
| 字段搜索 | `dit search "词" --field instruction` | `POST /search` (field 参数) |
| 数据验证 | `dit validate` | `POST /validate` |
| 上报 CI 结果 | — | `POST /checks` |
| 查询 CI 结果 | — | `GET /checks/{commit}` |
