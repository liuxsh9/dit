# 稀疏克隆

Dit 管理的训练数据集通常在几十 GB 到上百 GB 级别。全量克隆需要下载所有数据行对象并物化到本地，对于只需修改其中一两个文件的场景完全不必要。稀疏克隆（sparse clone）只下载轻量元数据，让你按需拉取要编辑的文件。

## 工作原理

Dit 的对象模型分层存储：

```
commit → tree → manifest → row, row, row ...
```

- **tree 对象**：几 KB，纯索引（文件名 + hash）
- **manifest 对象**：中等大小，记录每行的 row_hash 列表
- **row 对象**：实际数据行，占体积的大头

稀疏克隆的切割点在 tree 和 manifest 之间：下载所有 tree（看到完整目录结构），按需下载 manifest + rows（只拉取要编辑的文件）。

## 完整工作流

### 1. 稀疏克隆

```bash
dit clone --sparse http://server:8000/sft-code-data ./sft-code --token "dit_xxx"
```

输出示例：

```
Cloning http://server:8000/sft-code-data -> ./sft-code (sparse)
  15 commit(s), 24 file(s) in tree (estimated 47.2 GB)
  Use 'dit sparse-checkout add <path>' to fetch files.
```

克隆完成后，本地只有空目录骨架，没有实际数据文件：

```bash
ls sft-code/
# bug-fix/  general/  ascendc/  feature-impl/
```

### 2. 查看可拉取的文件

```bash
dit sparse-checkout list
```

输出示例：

```
Files in tree (24 total, 0 fetched):
  [ ] bug-fix/train.jsonl           (12,340 rows)
  [ ] bug-fix/eval.jsonl            (500 rows)
  [ ] general/train.jsonl           (45,000 rows)
  [ ] general/eval.jsonl            (2,100 rows)
  [ ] ascendc/train.jsonl           (3,200 rows)
```

`[x]` 表示已拉取，`[ ]` 表示未拉取。行数信息来自 sidecar 元数据（克隆时已下载）。

### 3. 拉取指定文件

拉取单个文件：

```bash
dit sparse-checkout add bug-fix/train.jsonl
```

输出示例：

```
Fetching bug-fix/train.jsonl (12,340 rows)...
  Downloaded 12,340 row objects (2.1 GB)
  Done.
```

拉取整个目录：

```bash
dit sparse-checkout add general/
```

可以一次拉取多个路径：

```bash
dit sparse-checkout add bug-fix/train.jsonl bug-fix/eval.jsonl
```

### 4. 正常编辑和提交

拉取文件后，后续的 add/commit/push 流程与全量克隆完全一致：

```bash
dit status
# On branch main (sparse checkout: 1/24 files)
# Nothing to commit, working directory clean.

# 编辑数据
vim bug-fix/train.jsonl

dit add bug-fix/train.jsonl
dit commit -m "fix: 移除 50 条低质量样本"
dit push
```

`dit status` 会提示当前处于 sparse 模式以及已拉取的文件数量。未拉取的文件不会被误报为"已删除"。

### 5. 移除不再需要的文件

编辑完成后，可以从工作目录中移除文件以释放磁盘空间：

```bash
dit sparse-checkout remove bug-fix/train.jsonl
```

这会删除工作目录中的文件，但保留 `.dit/objects` 中已下载的对象（可用 `dit gc` 回收）。

### 6. 转为全量克隆

如果后续需要访问所有文件，可以禁用 sparse 模式：

```bash
dit sparse-checkout disable
```

这会下载所有缺失的 manifest 和 row 对象，物化全部文件到工作目录，并删除 `.dit/sparse-checkout` 配置文件。此后仓库行为与普通全量克隆完全一致。

## 与其他命令的交互

| 命令 | sparse 模式下的行为 |
|------|-------------------|
| `dit status` | 只检查已拉取文件的变更，未拉取文件不报告为 deleted |
| `dit diff` | 只对已拉取文件计算 diff |
| `dit add` | 只能 add 已拉取的文件；尝试 add 未拉取文件会提示先 sparse-checkout add |
| `dit commit` | 未拉取文件的 tree entry 原样保留，不受影响 |
| `dit push` | 正常工作，未修改文件的对象在远端已存在 |
| `dit pull` | 只下载和物化已拉取文件的更新 |

## 注意事项

- 只有 `dit clone --sparse` 会创建 sparse 仓库，普通 `dit clone` 和 `dit init` 不受影响
- sparse 配置存储在 `.dit/sparse-checkout` 文件中，每行一个路径
- 目录路径以 `/` 结尾（如 `general/`），表示拉取该目录下所有文件
- sparse 配置跨分支保持不变，切换分支时只物化已拉取的文件
- 如果 sidecar 不存在，`sparse-checkout list` 中行数显示为 `? rows`
