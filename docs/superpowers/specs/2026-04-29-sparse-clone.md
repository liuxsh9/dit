# Sparse Clone 设计方案

## 背景

dit 管理的数据集通常在几十 GB 到上百 GB 级别。当前 `dit clone` 会下载全部 commit 历史、所有 tree/manifest 对象、以及每一个 row 对象，然后 materialize 全部文件到工作目录。这对于大数据集完全不可行。

用户的典型场景是：clone 一个项目后，只修改其中一两个 jsonl 文件，改完 add/commit/push。不需要把整个数据集拉到本地。

## 设计目标

1. `dit clone --sparse` 只下载轻量元数据（commits + trees），不下载实际数据（manifests + rows）
2. 本地创建完整的目录结构（空文件夹），让用户能看到项目全貌
3. 用户通过 `dit sparse-checkout add <path>` 按需拉取指定文件
4. `status`、`diff`、`add`、`commit`、`push`、`pull` 等命令正确感知 sparse 状态，不把未拉取的文件当作"已删除"

## 核心概念

### Sparse 配置文件

位置：`.dit/sparse-checkout`

纯文本，每行一个已拉取的路径 pattern（支持文件和目录）：

```
bug-fix/train.jsonl
general/
```

空文件或文件不存在 = 非 sparse 模式（即全量 clone，向后兼容）。

### 判断是否为 sparse 仓库

```python
def is_sparse(dot: Path) -> bool:
    return (dot / "sparse-checkout").exists()
```

只有 `dit clone --sparse` 会创建这个文件。普通 `dit clone` 和 `dit init` 不会创建，行为完全不变。

## 对象模型回顾

dit 的分层对象模型天然适合 sparse：

```
commit → tree → [tree | manifest | blob]
                              ↓
                         manifest → row, row, row ...
```

- **tree 对象**：几 KB，纯索引（文件名 + hash），必须全量下载
- **manifest 对象**：中等大小，记录 jsonl 每行的 row_hash 列表
- **row 对象**：实际数据行，占体积的大头

sparse clone 的切割点在 tree 和 manifest 之间：下载所有 tree，按需下载 manifest + rows。

## 命令变更

### 1. `dit clone --sparse`

```
dit clone --sparse http://server/dataset ./local-dir
```

流程：

1. 下载所有 commits（同现有逻辑，commit 对象很小）
2. 下载所有 tree 对象（递归 `_clone_tree_objects`，但**跳过 manifest 和 row 下载**）
3. 下载所有 sidecar 对象（sidecar 很小，且 `dit stats` 等命令需要它们）
4. 设置 refs、remote config（同现有逻辑）
5. **不调用 `_materialize_tree`**，不生成任何工作目录文件
6. 根据 tree 结构创建空目录骨架
7. 创建 `.dit/sparse-checkout` 文件（初始为空）

输出示例：

```
Cloning http://server/dataset -> ./local-dir (sparse)
  3 commit(s), 12 file(s) in tree
  Use 'dit sparse-checkout add <path>' to fetch files.
```

### 2. `dit sparse-checkout` 子命令组

#### `dit sparse-checkout add <path>...`

拉取指定文件或目录下的所有文件。

```
dit sparse-checkout add bug-fix/train.jsonl
dit sparse-checkout add general/          # 拉取整个目录
```

流程：

1. 解析 HEAD commit 的 tree，找到 path 对应的 manifest hash（如果是目录则递归找到所有 manifest）
2. 从 remote 下载对应的 manifest 对象
3. 从 remote 下载 manifest 引用的所有 row 对象（跳过本地已有的）
4. 调用 `materialize_file` 生成工作目录文件
5. 将 path 追加到 `.dit/sparse-checkout`

#### `dit sparse-checkout remove <path>...`

从 sparse 集合中移除路径，删除工作目录中对应的文件，但保留 .dit/objects 中已下载的对象（惰性清理，可用 `dit gc` 回收）。

```
dit sparse-checkout remove bug-fix/train.jsonl
```

#### `dit sparse-checkout list`

列出当前已拉取的路径，以及 tree 中的完整文件列表（标记哪些已拉取）。

```
dit sparse-checkout list

Files in tree (4 total, 1 fetched):
  [x] bug-fix/train.jsonl      (1,234 rows)
  [ ] bug-fix/eval.jsonl        (500 rows)
  [ ] general/train.jsonl       (8,000 rows)
  [ ] ascendc/train.jsonl       (3,200 rows)
```

行数信息来自 sidecar（clone 时已下载），如果 sidecar 不存在则显示 "? rows"。

#### `dit sparse-checkout disable`

转为全量模式：下载所有缺失的 manifest + rows，materialize 全部文件，删除 `.dit/sparse-checkout` 文件。

### 3. `dit clone`（无 --sparse）

行为完全不变，向后兼容。不创建 `.dit/sparse-checkout` 文件。

## 现有命令的 sparse 适配

### `dit status`

当前逻辑：扫描工作目录所有 jsonl，与 HEAD tree 对比，不在工作目录的文件报告为 "deleted"。

sparse 适配：

```python
# 在计算 deleted 时，排除不在 sparse-checkout 集合中的文件
sparse_paths = load_sparse_checkout(dot)  # None 表示非 sparse 模式
if sparse_paths is not None:
    head_rels = {r for r in head_rels if is_in_sparse_set(r, sparse_paths)}
```

输出增加 sparse 提示：

```
On branch main (sparse checkout: 1/4 files)

Unstaged changes:
  modified: bug-fix/train.jsonl
```

### `dit diff`

同 status，只对 sparse 集合内的文件计算 diff。未拉取的文件不参与 diff。

### `dit add`

当前逻辑已经是按路径 add，不需要改动。用户只能 add 工作目录中存在的文件，而 sparse 模式下未拉取的文件不在工作目录中，自然不会被 add。

如果用户尝试 `dit add` 一个未拉取的路径，给出提示：

```
error: 'general/train.jsonl' is not checked out.
  Use 'dit sparse-checkout add general/train.jsonl' to fetch it first.
```

### `dit commit`

不需要改动。commit 构建 tree 时，对于 sparse 模式下未拉取的文件，保留 HEAD tree 中的原始 entry（manifest hash 不变）。

关键逻辑：当前 commit 是从 staging index + HEAD tree 合并构建新 tree。sparse 模式下，未拉取的文件不会出现在 staging index 中，也不会被扫描到工作目录变更，所以它们的 tree entry 会原样保留。这正是期望的行为。

### `dit push`

不需要改动。push 上传的是 objects store 中的对象。sparse 模式下，未拉取文件的 manifest 和 row 对象不在本地 store 中，但它们在 remote 上已经存在，push 时 remote 会跳过已有对象。

需要注意：push 时如果 remote 发现某些对象缺失（理论上不会，因为未修改的文件 hash 不变），应该给出明确错误而不是静默失败。

### `dit pull`

当前逻辑：fetch 新对象 → fast-forward → `_materialize_tree` 全部文件。

sparse 适配：`_materialize_tree` 只 materialize sparse 集合内的文件。

```python
def _materialize_tree(repo_root, store, tree_hash, old_tree_hash=None, sparse_paths=None):
    new_flat = flatten_tree(store, tree_hash)
    for name, mhash in new_manifests.items():
        if sparse_paths is not None and not is_in_sparse_set(name, sparse_paths):
            continue  # 跳过未拉取的文件
        # ... 现有 materialize 逻辑
```

pull 时也需要按需下载 sparse 集合内文件的新 manifest + rows（如果远端有更新）。

### `dit checkout`（切换分支）

sparse 适配：切换分支时，只 materialize sparse 集合内的文件。sparse 配置跨分支保持不变（和 git sparse-checkout 行为一致）。

## 核心模块

### `src/dit/core/sparse.py`（新文件）

```python
"""Sparse checkout configuration management."""
from pathlib import Path


def is_sparse(dot: Path) -> bool:
    """Check if this is a sparse checkout repository."""
    return (dot / "sparse-checkout").exists()


def load_sparse_paths(dot: Path) -> set[str] | None:
    """Load sparse checkout paths. Returns None if not sparse mode."""
    sc_file = dot / "sparse-checkout"
    if not sc_file.exists():
        return None
    paths = set()
    for line in sc_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            paths.add(line)
    return paths


def save_sparse_paths(dot: Path, paths: set[str]) -> None:
    """Write sparse checkout paths to config file."""
    sc_file = dot / "sparse-checkout"
    sc_file.write_text("\n".join(sorted(paths)) + "\n")


def is_in_sparse_set(file_path: str, sparse_paths: set[str]) -> bool:
    """Check if a file path matches the sparse checkout set.

    Supports exact file matches and directory prefixes (ending with /).
    """
    if file_path in sparse_paths:
        return True
    for sp in sparse_paths:
        if sp.endswith("/") and file_path.startswith(sp):
            return True
    return False
```

### `_clone_tree_objects` 修改

增加 `sparse` 参数，sparse 模式下只下载 tree 和 sidecar，跳过 manifest：

```python
def _clone_tree_objects(rc, store, tree_hash, manifest_hashes, sparse=False):
    tree_data = rc.download_object("trees", tree_hash)
    if not tree_data:
        return
    store.write("trees", tree_data)
    tree = deserialize_tree(tree_data)

    for entry in tree.entries:
        if entry.obj_type == "manifest":
            if not sparse:
                m_data = rc.download_object("manifests", entry.obj_hash)
                if m_data:
                    store.write("manifests", m_data)
                    manifest_hashes.add(entry.obj_hash)
            # sidecar 始终下载（很小，stats 需要）
            if entry.sidecar_hash and not store.exists("sidecars", entry.sidecar_hash):
                sc_data = rc.download_object("sidecars", entry.sidecar_hash)
                if sc_data:
                    store.write("sidecars", sc_data)
        elif entry.obj_type == "tree":
            _clone_tree_objects(rc, store, entry.obj_hash, manifest_hashes, sparse=sparse)
```

### `_create_directory_skeleton`（新函数）

sparse clone 后创建空目录结构：

```python
def _create_directory_skeleton(repo_root: Path, store: ObjectStore, tree_hash: str):
    """Create empty directory structure from tree without materializing files."""
    flat = flatten_tree(store, tree_hash)
    for path in flat:
        parent = (repo_root / path).parent
        parent.mkdir(parents=True, exist_ok=True)
```

## 数据流总结

### 全量 clone（现有行为，不变）

```
clone → 下载 commits → 下载 trees + manifests + sidecars
      → 下载 rows → materialize 全部文件
```

### sparse clone（新增）

```
clone --sparse → 下载 commits → 下载 trees + sidecars（跳过 manifests/rows）
               → 创建目录骨架 → 写 .dit/sparse-checkout

sparse-checkout add <path> → 从 tree 解析 manifest hash
                            → 下载 manifest + rows → materialize 文件
                            → 更新 .dit/sparse-checkout
```

## 用户体验示例

```bash
# 1. sparse clone，只拉元数据
$ dit clone --sparse http://server/sft-code-data ./sft-code
Cloning http://server/sft-code-data -> ./sft-code (sparse)
  15 commit(s), 24 file(s) in tree (estimated 47.2 GB)
  Use 'dit sparse-checkout add <path>' to fetch files.

# 2. 查看项目结构
$ ls sft-code/
bug-fix/  general/  ascendc/  feature-impl/

# 3. 查看有哪些文件可以拉取
$ dit sparse-checkout list
Files in tree (24 total, 0 fetched):
  [ ] bug-fix/train.jsonl           (12,340 rows, ~2.1 GB)
  [ ] bug-fix/eval.jsonl            (500 rows, ~85 MB)
  [ ] general/train.jsonl           (45,000 rows, ~8.3 GB)
  ...

# 4. 拉取要修改的文件
$ dit sparse-checkout add bug-fix/train.jsonl
Fetching bug-fix/train.jsonl (12,340 rows)...
  Downloaded 12,340 row objects (2.1 GB)
  Done.

# 5. 正常的 dit 工作流
$ dit status
On branch main (sparse checkout: 1/24 files)

Nothing to commit, working directory clean.

$ vim bug-fix/train.jsonl   # 编辑数据
$ dit status
On branch main (sparse checkout: 1/24 files)

Unstaged changes:
  modified: bug-fix/train.jsonl

$ dit add bug-fix/train.jsonl
$ dit commit -m "fix: remove 50 low-quality samples from bug-fix"
$ dit push
```

## 不做的事情

1. **不做 shallow clone**（限制 commit 历史深度）—— commit 对象很小，全量下载没问题
2. **不做 pattern/glob 匹配** —— 第一版只支持精确路径和目录前缀，够用
3. **不做自动 sparse** —— 不会根据文件大小自动决定是否拉取，完全由用户控制
4. **不做 partial push** —— push 始终上传所有本地变更的对象，不受 sparse 影响

## 实现优先级

1. **P0**: `dit clone --sparse` + `dit sparse-checkout add/list`（核心能力）
2. **P0**: `status`/`diff` 的 sparse 适配（不适配会误报大量 deleted）
3. **P1**: `pull` 的 sparse 适配（只下载和 materialize sparse 集合内的文件）
4. **P1**: `checkout`（切换分支）的 sparse 适配
5. **P2**: `sparse-checkout remove` / `sparse-checkout disable`
6. **P2**: `sparse-checkout list` 显示文件大小估算（需要 sidecar 数据）
