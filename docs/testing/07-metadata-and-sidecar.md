# DataHub 手动测试指南 07：元数据与 Sidecar

本指南覆盖 DataHub Sidecar 元数据的完整工作流：计算元数据、查看聚合统计、对比两个提交之间的差异。每个操作均提供 CLI（`dit` 命令）和 REST API（`curl`）两种测试路径。

**Sidecar 简介**：Sidecar 是与 manifest 并存的附属对象，按行存储 `char_count`（字符数）、`token_estimate`（估算 token 数，`= char_count // 4`）、`field_count`（顶层字段数）、`lang`（语言：`en` / `zh` / `ru` / `ar` / `null`）五个字段。Sidecar 的哈希随 tree entry 一起存储，实现内容寻址。

**前置条件**：已完成**指南 01**（本地仓库已有至少一个 commit，含 `.jsonl` 文件）。

---

## 目录

1. [前置条件：准备含多样数据的本地仓库](#1-前置条件准备含多样数据的本地仓库)
2. [计算元数据（meta compute）](#2-计算元数据meta-compute)
3. [查看元数据（meta show）](#3-查看元数据meta-show)
4. [元数据差异（meta diff）](#4-元数据差异meta-diff)
5. [API：计算元数据](#5-api计算元数据)
6. [API：查看摘要（summary）](#6-api查看摘要summary)
7. [API：查看详情（完整条目）](#7-api查看详情完整条目)
8. [API：元数据差异](#8-api元数据差异)
9. [多轮对话样本验证](#9-多轮对话样本验证)
10. [边界场景](#10-边界场景)

---

## 1. 前置条件：准备含多样数据的本地仓库

本节构建一个包含三种样本类型的仓库，后续各节均在此基础上操作。

### 1.1 初始化仓库

```bash
mkdir -p ~/dit-meta-test && cd ~/dit-meta-test
dit init
```

验证清单：
- [ ] 输出包含 `Initialized empty DataHub repository`
- [ ] `.datahub/` 目录已创建

### 1.2 写入单轮英文样本

```bash
cat > train_en.jsonl << 'EOF'
{"instruction": "What is the capital of France?", "response": "The capital of France is Paris. It is located in the northern part of the country and serves as the political, economic, and cultural center."}
{"instruction": "Explain Python list comprehension.", "response": "A list comprehension provides a concise way to create lists. It consists of brackets containing an expression followed by a for clause, then optionally more for or if clauses."}
{"instruction": "What is machine learning?", "response": "Machine learning is a subset of artificial intelligence that enables systems to learn and improve from experience without being explicitly programmed."}
EOF
```

### 1.3 写入中文样本

```bash
cat > train_zh.jsonl << 'EOF'
{"instruction": "请介绍一下深度学习的基本概念。", "response": "深度学习是机器学习的一个分支，通过构建多层神经网络来学习数据的表示。它在图像识别、自然语言处理等领域取得了突破性进展。"}
{"instruction": "什么是卷积神经网络？", "response": "卷积神经网络（CNN）是一种专为处理网格结构数据（如图像）设计的深度学习模型，通过卷积层、池化层和全连接层的组合来提取特征。"}
EOF
```

### 1.4 写入多轮对话样本（用于第 9 节验证）

```bash
cat > chat_multi.jsonl << 'EOF'
{"messages": [{"role": "system", "content": "You are a helpful coding assistant."}, {"role": "user", "content": "How do I read a file in Python?"}, {"role": "assistant", "content": "You can use the built-in open() function to read a file in Python."}, {"role": "user", "content": "Can you show me an example?"}, {"role": "assistant", "content": "Sure! Here is an example: with open('file.txt', 'r') as f: content = f.read()"}]}
{"messages": [{"role": "user", "content": "What is 2 + 2?"}, {"role": "assistant", "content": "2 + 2 equals 4."}]}
EOF
```

### 1.5 提交初始数据

```bash
dit add train_en.jsonl
dit add train_zh.jsonl
dit add chat_multi.jsonl
dit commit -m "initial: add multi-type training data"
```

验证清单：
- [ ] 三个文件均成功 `add`，无报错
- [ ] `commit` 输出包含 commit 哈希（如 `[main abc12345]`）
- [ ] `dit log` 显示一条提交记录

```bash
dit log
```

记录初始提交哈希（后续步骤使用）：

```bash
export COMMIT1=$(dit log --format json | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[0]['hash'])")
echo "初始提交: $COMMIT1"
```

---

## 2. 计算元数据（meta compute）

`meta compute` 扫描当前 HEAD 下所有缺少 sidecar 的 manifest，逐行计算元数据，并创建一个新的 meta commit。

### 2.1 确认计算前无 sidecar

```bash
dit meta show train_en.jsonl
```

预期输出（因为尚未计算）：
```
fatal: no sidecar for 'train_en.jsonl' — run 'dit meta compute' first
```

验证清单：
- [ ] 退出码为 1，提示缺少 sidecar

### 2.2 执行计算（全部文件）

```bash
dit meta compute
```

预期输出示例：
```
Computing metadata for chat_multi.jsonl (2 rows)... done (sidecar: a1b2c3d4)
Computing metadata for train_en.jsonl (3 rows)... done (sidecar: e5f6a7b8)
Computing metadata for train_zh.jsonl (2 rows)... done (sidecar: c9d0e1f2)
Created commit: 3f4a5b6c "meta: compute sidecar metadata"
```

验证清单：
- [ ] 三个文件均输出 `done (sidecar: xxxxxxxx)`
- [ ] 末行显示新创建的 commit 哈希和消息 `"meta: compute sidecar metadata"`
- [ ] `dit log` 现在比之前多出一条 commit 记录

```bash
dit log
```

记录 meta commit 哈希：

```bash
export COMMIT2=$(dit log --format json | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[0]['hash'])")
echo "Meta commit: $COMMIT2"
```

### 2.3 针对单个文件计算

先回退到 `$COMMIT1`（无 sidecar 状态）再测试，或另开一个仓库。若不想回退，此步骤可跳过，直接在 2.4 验证幂等性。

```bash
# 仅对 train_en.jsonl 计算（在 meta commit 后再加一行数据的场景下测试）
dit meta compute --file train_en.jsonl
```

预期：只有 `train_en.jsonl` 被处理，其他文件因已有 sidecar 而跳过。

验证清单：
- [ ] 输出只出现 `train_en.jsonl` 一行
- [ ] 新增一个 meta commit

### 2.4 幂等性验证

```bash
dit meta compute
```

预期输出：
```
Nothing to compute (all manifests already have sidecar metadata).
```

验证清单：
- [ ] 输出为 `Nothing to compute...`
- [ ] 退出码为 0
- [ ] `dit log` 的提交数量未增加

---

## 3. 查看元数据（meta show）

`meta show` 读取 HEAD 指向的 sidecar，展示文件的聚合统计信息。

### 3.1 表格格式（默认）

```bash
dit meta show train_en.jsonl
```

预期输出示例：
```
File: train_en.jsonl (3 rows)
Sidecar: e5f6a7b8

  Total chars:    <N>
  Token estimate: <N/4 左右>
  Avg fields/row: 2.0
  Languages:      en (100%)
```

验证清单：
- [ ] 第一行显示文件名和行数（3 rows）
- [ ] `Sidecar:` 后跟 8 位哈希前缀
- [ ] `Total chars` 大于 0
- [ ] `Token estimate` 约等于 `Total chars / 4`（整除取整）
- [ ] `Avg fields/row` 为 2.0（每行有 `instruction` 和 `response` 两个字段）
- [ ] `Languages` 显示 `en (100%)`

### 3.2 验证中文文件的语言检测

```bash
dit meta show train_zh.jsonl
```

验证清单：
- [ ] 行数为 2
- [ ] `Languages` 显示 `zh (100%)`
- [ ] `Avg fields/row` 为 2.0

### 3.3 JSON 格式输出

```bash
dit meta show train_en.jsonl --format json
```

预期输出（JSON 结构）：
```json
{
  "manifest_hash": "<64位哈希>",
  "entries": [
    {
      "row_hash": "<64位哈希>",
      "char_count": <整数>,
      "token_estimate": <整数>,
      "field_count": 2,
      "lang": "en"
    },
    ...
  ]
}
```

验证清单：
- [ ] 输出为合法 JSON
- [ ] `entries` 数组长度为 3（与 `train_en.jsonl` 行数一致）
- [ ] 每个 entry 包含 `row_hash`、`char_count`、`token_estimate`、`field_count`、`lang`
- [ ] `token_estimate` 值等于对应 `char_count // 4`（可手动验算）

```bash
# 手动验算第一行
dit meta show train_en.jsonl --format json | python3 -c "
import sys, json
data = json.load(sys.stdin)
for e in data['entries']:
    expected = e['char_count'] // 4
    assert e['token_estimate'] == expected, f\"mismatch: {e['token_estimate']} != {expected}\"
    print(f\"row_hash={e['row_hash'][:8]} chars={e['char_count']} tokens={e['token_estimate']} (✓ = chars//4)\")
print('所有行 token_estimate 验算通过')
"
```

验证清单：
- [ ] 脚本输出 `所有行 token_estimate 验算通过`，无断言错误

### 3.4 未计算 sidecar 时的错误处理

在没有运行 `meta compute` 的仓库中（或指定尚未计算 sidecar 的文件）：

```bash
# 若仓库中有未计算 sidecar 的文件，执行：
dit meta show <未计算文件名>
```

预期：
```
fatal: no sidecar for '<文件名>' — run 'dit meta compute' first
```

验证清单：
- [ ] 退出码为 1
- [ ] 错误信息提示运行 `dit meta compute`

---

## 4. 元数据差异（meta diff）

`meta diff` 对比两个 commit 之间各文件的 sidecar 统计变化，仅输出有差异的文件。

### 4.1 添加新数据并创建第二个 meta commit

```bash
# 向 train_en.jsonl 追加一行
echo '{"instruction": "What is a neural network?", "response": "A neural network is a computational model inspired by the structure of the human brain, consisting of interconnected nodes organized in layers."}' >> train_en.jsonl

dit add train_en.jsonl
dit commit -m "data: add neural network sample"
```

此时新 commit 的 `train_en.jsonl` sidecar 已失效（manifest 更新后 sidecar_hash 被清除），需重新计算：

```bash
dit meta compute
```

记录新 meta commit 哈希：

```bash
export COMMIT3=$(dit log --format json | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[0]['hash'])")
echo "新 meta commit: $COMMIT3"
```

### 4.2 执行 meta diff

```bash
dit meta diff $COMMIT2 $COMMIT3
```

预期输出示例：
```
train_en.jsonl:
  Rows:           3 → 4 (+1)
  Token estimate: 120 → 160 (+40)
```

验证清单：
- [ ] 只显示 `train_en.jsonl`（`train_zh.jsonl` 和 `chat_multi.jsonl` 无变化，不输出）
- [ ] `Rows` 从 3 增加到 4（`+1`）
- [ ] `Token estimate` 数值增加（`+` 符号）
- [ ] `train_zh.jsonl` 和 `chat_multi.jsonl` 不出现在输出中

### 4.3 针对单个文件的 diff

```bash
dit meta diff $COMMIT2 $COMMIT3 --file train_en.jsonl
```

验证清单：
- [ ] 输出与 4.2 相同，仅显示 `train_en.jsonl`

```bash
# 针对未变化文件的 diff（应无输出）
dit meta diff $COMMIT2 $COMMIT3 --file train_zh.jsonl
```

预期输出：
```
No metadata differences.
```

验证清单：
- [ ] 输出 `No metadata differences.`

### 4.4 无差异时的输出

```bash
dit meta diff $COMMIT3 $COMMIT3
```

预期输出：
```
No metadata differences.
```

验证清单：
- [ ] 相同 commit 的 diff 结果为空

---

## 5. API：计算元数据

以下步骤通过 REST API 测试 `POST /api/v1/repos/{repo}/meta/compute`。

### 5.1 环境准备

```bash
export TOKEN="<你的 admin token>"
export BASE="http://localhost:8000"
export REPO="meta-api-test"
```

### 5.2 创建服务端测试仓库并推送初始数据

```bash
# 创建仓库
curl -s -X POST "$BASE/api/v1/repos" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d "{\"name\": \"$REPO\"}" | python3 -m json.tool
```

验证清单：
- [ ] 返回 HTTP 201，`name` 与 `$REPO` 一致

使用 `dit` 推送数据（假设本地仓库已在前面章节创建好）：

```bash
cd ~/dit-meta-test
dit remote add origin "$BASE/api/v1/repos/$REPO"
dit push origin main
```

### 5.3 计算全部文件的 sidecar

```bash
curl -s -X POST "$BASE/api/v1/repos/$REPO/meta/compute" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{}' | python3 -m json.tool
```

预期输出：
```json
{
    "commit_hash": "<64位新提交哈希>",
    "sidecars": [
        {
            "file": "chat_multi.jsonl",
            "sidecar_hash": "<64位哈希>"
        },
        {
            "file": "train_en.jsonl",
            "sidecar_hash": "<64位哈希>"
        },
        {
            "file": "train_zh.jsonl",
            "sidecar_hash": "<64位哈希>"
        }
    ]
}
```

记录此 commit 哈希：

```bash
export META_COMMIT=$(curl -s -X POST "$BASE/api/v1/repos/$REPO/meta/compute" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{}' | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('commit_hash',''))")
# 若已计算则 commit_hash 为原 HEAD
echo "Meta commit: $META_COMMIT"
```

验证清单：
- [ ] 返回 HTTP 200
- [ ] `commit_hash` 非空，为 64 位十六进制字符串
- [ ] `sidecars` 数组包含三个条目（`chat_multi.jsonl`、`train_en.jsonl`、`train_zh.jsonl`）
- [ ] 每个条目有 `file` 和 `sidecar_hash` 字段

### 5.4 针对单个文件计算

若仓库中还有未计算 sidecar 的文件，可限定：

```bash
curl -s -X POST "$BASE/api/v1/repos/$REPO/meta/compute" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"file": "train_en.jsonl"}' | python3 -m json.tool
```

验证清单：
- [ ] `sidecars` 仅包含 `train_en.jsonl` 一项（如果其他文件已有 sidecar）

### 5.5 幂等性验证

```bash
curl -s -X POST "$BASE/api/v1/repos/$REPO/meta/compute" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{}' | python3 -m json.tool
```

预期：
```json
{
    "commit_hash": "<与当前 HEAD 相同的哈希>",
    "sidecars": []
}
```

验证清单：
- [ ] `sidecars` 为空数组 `[]`
- [ ] `commit_hash` 与当前 HEAD 相同（无新 commit 产生）

### 5.6 错误场景：仓库不存在

```bash
curl -s -X POST "$BASE/api/v1/repos/no-such-repo/meta/compute" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{}' | python3 -m json.tool
```

验证清单：
- [ ] 返回 HTTP 404

### 5.7 错误场景：仓库无提交

```bash
# 创建空仓库
curl -s -X POST "$BASE/api/v1/repos" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"name": "empty-meta-repo"}' | python3 -m json.tool

curl -s -X POST "$BASE/api/v1/repos/empty-meta-repo/meta/compute" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{}' | python3 -m json.tool
```

验证清单：
- [ ] 返回 HTTP 400，`detail` 提示无提交（`no commits` 或 `no heads/main ref`）

---

## 6. API：查看摘要（summary）

`GET /api/v1/repos/{repo}/meta/{commit_hash}/{file_path}/summary` 返回单个文件的聚合统计。

### 6.1 获取文件摘要

```bash
# 使用第 5 节记录的 META_COMMIT
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/meta/$META_COMMIT/train_en.jsonl/summary" \
     | python3 -m json.tool
```

预期输出：
```json
{
    "row_count": 3,
    "char_count": <整数>,
    "token_estimate": <整数>,
    "avg_fields": 2.0,
    "lang_distribution": {
        "en": 3
    }
}
```

验证清单：
- [ ] 返回 HTTP 200
- [ ] `row_count` 为 3
- [ ] `char_count` 大于 0
- [ ] `token_estimate` 等于 `char_count // 4`（可用以下命令验算）

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/meta/$META_COMMIT/train_en.jsonl/summary" \
     | python3 -c "
import sys, json
d = json.load(sys.stdin)
expected = d['char_count'] // 4
actual = d['token_estimate']
assert actual == expected, f'token_estimate mismatch: {actual} != {expected}'
print(f\"char_count={d['char_count']}, token_estimate={d['token_estimate']} (✓ = chars//4)\")
print(f\"lang_distribution={d['lang_distribution']}\")
"
```

验证清单：
- [ ] `avg_fields` 为 2.0
- [ ] `lang_distribution` 中 `"en": 3`

### 6.2 中文文件的语言分布

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/meta/$META_COMMIT/train_zh.jsonl/summary" \
     | python3 -m json.tool
```

验证清单：
- [ ] `row_count` 为 2
- [ ] `lang_distribution` 中 `"zh": 2`

### 6.3 错误场景：commit 不存在

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/meta/$( python3 -c "print('z'*64)")/train_en.jsonl/summary" \
     | python3 -m json.tool
```

验证清单：
- [ ] 返回 HTTP 404，`detail` 为 `"Commit not found"`

### 6.4 错误场景：文件无 sidecar

```bash
# 若存在未计算 sidecar 的文件，使用其 commit hash 和文件名测试
# 以下用 no-sidecar 仓库演示
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/no-such-repo/meta/$(python3 -c "print('a'*64)")/train.jsonl/summary" \
     | python3 -m json.tool
```

验证清单：
- [ ] 返回 HTTP 404

---

## 7. API：查看详情（完整条目）

`GET /api/v1/repos/{repo}/meta/{commit_hash}/{file_path}` 返回 sidecar 的所有逐行条目。

### 7.1 获取完整条目列表

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/meta/$META_COMMIT/train_en.jsonl" \
     | python3 -m json.tool
```

预期输出结构：
```json
{
    "commit_hash": "<64位哈希>",
    "path": "train_en.jsonl",
    "manifest_hash": "<64位哈希>",
    "entries": [
        {
            "row_hash": "<64位哈希>",
            "char_count": <整数>,
            "token_estimate": <整数>,
            "field_count": 2,
            "lang": "en"
        },
        ...
    ]
}
```

验证清单：
- [ ] 返回 HTTP 200
- [ ] `entries` 数组长度为 3（与 `train_en.jsonl` 行数一致）
- [ ] 每个 entry 包含 `row_hash`（64 位）、`char_count`、`token_estimate`、`field_count`、`lang`
- [ ] 所有 entry 的 `lang` 为 `"en"`
- [ ] 所有 entry 的 `field_count` 为 2

### 7.2 逐行 token_estimate 验算

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/meta/$META_COMMIT/train_en.jsonl" \
     | python3 -c "
import sys, json
data = json.load(sys.stdin)
for i, e in enumerate(data['entries']):
    expected = e['char_count'] // 4
    status = '✓' if e['token_estimate'] == expected else '✗'
    print(f\"row {i}: chars={e['char_count']}, tokens={e['token_estimate']} (expected {expected}) {status}\")
"
```

验证清单：
- [ ] 所有行的 `token_estimate` 等于 `char_count // 4`，标记为 `✓`

### 7.3 摘要与详情一致性验证

```bash
# 从详情中汇总，与摘要比较
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/meta/$META_COMMIT/train_en.jsonl" \
     | python3 -c "
import sys, json
data = json.load(sys.stdin)
entries = data['entries']
total_chars = sum(e['char_count'] for e in entries)
total_tokens = sum(e['token_estimate'] for e in entries)
avg_fields = sum(e['field_count'] for e in entries) / len(entries)
print(f'从详情汇总: char_count={total_chars}, token_estimate={total_tokens}, avg_fields={avg_fields:.2f}')
print('请与 /summary 接口返回的值比较，应一致。')
"
```

验证清单：
- [ ] 详情汇总的 `char_count`、`token_estimate`、`avg_fields` 与 `/summary` 接口的返回值完全一致

---

## 8. API：元数据差异

`GET /api/v1/repos/{repo}/meta/diff/{old_commit}/{new_commit}` 返回两个 commit 之间各文件的 sidecar 统计变化。

### 8.1 创建第二个数据 commit 并重新计算 sidecar

```bash
# 若本地已有新数据，推送并在服务端计算
# 假设已在本地追加一行到 train_en.jsonl 并执行了 dit push

# 先获取当前 HEAD（旧 commit）
export OLD_COMMIT=$META_COMMIT
echo "旧 commit: $OLD_COMMIT"

# 推送新数据（若尚未推送）
cd ~/dit-meta-test
dit push origin main

# 在服务端计算新 sidecar
curl -s -X POST "$BASE/api/v1/repos/$REPO/meta/compute" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{}' | python3 -m json.tool

# 获取新 HEAD
export NEW_COMMIT=$(curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/refs" \
     | python3 -c "import sys,json; refs=json.load(sys.stdin); print([r for r in refs if r['name']=='heads/main'][0]['target_hash'])")
echo "新 commit: $NEW_COMMIT"
```

### 8.2 执行 meta diff

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/meta/diff/$OLD_COMMIT/$NEW_COMMIT" \
     | python3 -m json.tool
```

预期输出结构：
```json
{
    "old_commit": "<旧 commit 哈希>",
    "new_commit": "<新 commit 哈希>",
    "files": [
        {
            "path": "train_en.jsonl",
            "old_stats": {
                "row_count": 3,
                "char_count": <整数>,
                "token_estimate": <整数>,
                "avg_fields": 2.0,
                "lang_distribution": {"en": 3}
            },
            "new_stats": {
                "row_count": 4,
                "char_count": <更大整数>,
                "token_estimate": <更大整数>,
                "avg_fields": 2.0,
                "lang_distribution": {"en": 4}
            },
            "delta": {
                "row_count": 1,
                "char_count": <整数>,
                "token_estimate": <整数>
            }
        }
    ]
}
```

验证清单：
- [ ] 返回 HTTP 200
- [ ] `old_commit` 和 `new_commit` 分别对应传入的哈希
- [ ] `files` 数组仅包含 `train_en.jsonl`（其他文件无变化，不出现）
- [ ] `delta.row_count` 为 1
- [ ] `delta.char_count` 和 `delta.token_estimate` 均大于 0
- [ ] `new_stats.row_count` = `old_stats.row_count` + `delta.row_count`

### 8.3 文件过滤参数

```bash
# 只看 train_en.jsonl 的差异
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/meta/diff/$OLD_COMMIT/$NEW_COMMIT?file=train_en.jsonl" \
     | python3 -m json.tool
```

验证清单：
- [ ] `files` 数组仅包含 `train_en.jsonl`

```bash
# 过滤到无变化的文件，应返回空 files 数组
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/meta/diff/$OLD_COMMIT/$NEW_COMMIT?file=train_zh.jsonl" \
     | python3 -m json.tool
```

验证清单：
- [ ] `files` 为空数组 `[]`

### 8.4 错误场景：commit 不存在

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/meta/diff/$(python3 -c "print('z'*64)")/$(python3 -c "print('y'*64)")" \
     | python3 -m json.tool
```

验证清单：
- [ ] 返回 HTTP 404

---

## 9. 多轮对话样本验证

Sidecar 当前记录的字段（`char_count`、`token_estimate`、`field_count`、`lang`）是基于原始 JSON 行的统计。本节验证对 `messages` 格式的多轮对话数据，统计逻辑依然正确。

### 9.1 查看 chat_multi.jsonl 的 JSON 格式 sidecar

```bash
dit meta show chat_multi.jsonl --format json
```

预期输出要点：
- 两个 entry，分别对应两行对话
- 第一行（含 system prompt）的 `char_count` 显著大于第二行（仅两轮）
- `field_count` 均为 1（每行 JSON 只有顶层 `messages` 字段）
- `lang` 应为 `"en"`（最长字符串为英文）

验证清单：
- [ ] `entries` 数组长度为 2
- [ ] 两个 entry 的 `field_count` 均为 1
- [ ] 第一行（含 5 条 messages）的 `char_count` 大于第二行（含 2 条 messages）
- [ ] 两个 entry 的 `lang` 均为 `"en"`

### 9.2 手动验算 char_count

```bash
# 提取第一行原始 JSON 的字符长度
python3 -c "
import json
with open('$HOME/dit-meta-test/chat_multi.jsonl') as f:
    lines = f.readlines()
for i, line in enumerate(lines):
    stripped = line.rstrip('\n')
    parsed = json.loads(stripped)
    # dit 存储时使用 sort_keys=True, separators=(',', ':')
    canonical = json.dumps(parsed, separators=(',',':'), sort_keys=True)
    print(f'row {i}: len={len(canonical)}, token_estimate={len(canonical)//4}')
"
```

将上述输出与 `dit meta show chat_multi.jsonl --format json` 的 entry 比较：

验证清单：
- [ ] 脚本输出的 `len` 与 sidecar entry 的 `char_count` 一致
- [ ] 脚本输出的 `token_estimate` 与 sidecar entry 的 `token_estimate` 一致

### 9.3 API 端的多轮对话验证

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/meta/$META_COMMIT/chat_multi.jsonl/summary" \
     | python3 -m json.tool
```

验证清单：
- [ ] `row_count` 为 2
- [ ] `avg_fields` 为 1.0（每行仅有 `messages` 一个顶层字段）
- [ ] `lang_distribution` 中 `"en": 2`

---

## 10. 边界场景

### 10.1 对已有 sidecar 的文件重复计算（幂等性，CLI）

```bash
dit meta compute
```

预期：
```
Nothing to compute (all manifests already have sidecar metadata).
```

验证清单：
- [ ] 退出码为 0
- [ ] `dit log` 提交总数未增加

### 10.2 空文件的 sidecar 摘要（API）

```bash
# 创建并提交空 JSONL 文件
cd ~/dit-meta-test
touch empty.jsonl
dit add empty.jsonl
dit commit -m "add empty jsonl"
dit meta compute
```

```bash
dit meta show empty.jsonl
```

预期输出：
```
File: empty.jsonl (0 rows)
  No data.
```

验证清单：
- [ ] 输出 `0 rows` 和 `No data.`，不报错

```bash
# API 摘要
EMPTY_COMMIT=$(dit log --format json | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[0]['hash'])")
# 推送并在服务端计算后：
curl -s -H "Authorization: Bearer $TOKEN" \
     "$BASE/api/v1/repos/$REPO/meta/$EMPTY_COMMIT/empty.jsonl/summary" \
     | python3 -m json.tool
```

预期：
```json
{
    "row_count": 0,
    "char_count": 0,
    "token_estimate": 0,
    "avg_fields": 0.0,
    "lang_distribution": {}
}
```

验证清单：
- [ ] 所有数值均为 0，`lang_distribution` 为空对象

### 10.3 非 JSONL 文件（文本类型）

DataHub 将 blob 文件（非 manifest 类型）存入 tree 但不会为其计算 sidecar，`meta show` 会报错。

```bash
# 添加一个纯文本文件
echo "This is a plain text file." > readme.txt
dit add readme.txt
dit commit -m "add readme"
dit meta show readme.txt
```

预期：
```
fatal: 'readme.txt' is not a manifest file (type=blob)
```

验证清单：
- [ ] 退出码为 1
- [ ] 错误提示 `is not a manifest file`

### 10.4 show 不存在的文件

```bash
dit meta show nonexistent.jsonl
```

预期：
```
fatal: 'nonexistent.jsonl' not found in current HEAD tree
```

验证清单：
- [ ] 退出码为 1
- [ ] 错误提示 `not found in current HEAD tree`

### 10.5 diff 时 commit 哈希不存在（CLI）

```bash
dit meta diff $(python3 -c "print('a'*64)") $(python3 -c "print('b'*64)")
```

预期：
```
fatal: commit aaaaaaaa not found
```

验证清单：
- [ ] 退出码为 1
- [ ] 错误信息包含 `not found`

### 10.6 语言检测边界：字符串过短

当所有字段中最长的字符串长度不足 10 时，`lang` 应为 `null`。

```bash
cat > short_fields.jsonl << 'EOF'
{"a": "Hi", "b": 42}
EOF
dit add short_fields.jsonl
dit commit -m "add short fields"
dit meta compute
dit meta show short_fields.jsonl --format json
```

预期：第一个（也是唯一）entry 的 `lang` 为 `null`。

验证清单：
- [ ] `entries[0].lang` 为 `null`
- [ ] `entries[0].field_count` 为 2（`a` 和 `b` 两个字段）

---

## 验证小结

完成本指南后，应已验证：

| 功能 | CLI | API |
|------|-----|-----|
| 计算 sidecar（全部文件） | `dit meta compute` | `POST /meta/compute` |
| 计算 sidecar（单文件） | `dit meta compute --file` | `POST /meta/compute` + `file` 字段 |
| 计算幂等性 | "Nothing to compute" | `sidecars: []` |
| 查看聚合统计（表格） | `dit meta show` | `GET /meta/{commit}/{file}/summary` |
| 查看逐行详情（JSON） | `dit meta show --format json` | `GET /meta/{commit}/{file}` |
| 两 commit 间差异 | `dit meta diff` | `GET /meta/diff/{old}/{new}` |
| 差异文件过滤 | `--file` 选项 | `?file=` 查询参数 |
| token 计算公式 | `char_count // 4` | 同左 |
| 语言检测：中文 | `lang: "zh"` | 同左 |
| 语言检测：英文 | `lang: "en"` | 同左 |
| 语言检测：过短字符串 | `lang: null` | 同左 |
| 空文件 | `0 rows / No data` | `row_count: 0` |
| 无 sidecar 时的错误 | exit 1 | HTTP 404 |
| 仓库/commit 不存在 | exit 1 | HTTP 404 |
