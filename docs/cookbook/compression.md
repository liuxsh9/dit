# Zstd 压缩与存储

Dit 使用 Zstandard (zstd) 压缩所有对象。压缩对用户完全透明——写入时自动压缩，读取时自动解压，无需手动操作。

## 对象存储布局

所有对象存储在 `.dit/objects/` 目录下，按类型和 hash 前缀分片：

```text
.dit/objects/
├── commits/
│   └── a3/
│       └── f1/
│           └── a3f1...完整sha256hash
├── trees/
├── manifests/
├── rows/
├── sidecars/
├── blobs/
└── tmp/
```

完整路径格式：

```text
.dit/objects/<type>/<hash[0:2]>/<hash[2:4]>/<hash>
```

Dit 共有 6 种对象类型：

| 类型 | 说明 |
|------|------|
| `row` | 单条 JSONL 训练数据 |
| `manifest` | 文件的行索引（记录该文件包含哪些 row） |
| `tree` | 目录结构快照 |
| `commit` | 提交记录（指向 tree + 父提交） |
| `sidecar` | 文件级元数据（token 统计等） |
| `blob` | 非 JSONL 的二进制文件 |

## 写入机制

对象写入采用原子操作，保证断电或进程崩溃时不会产生损坏的文件：

1. 计算数据的 SHA-256 hash
2. 如果目标路径已存在，跳过（内容寻址天然去重）
3. 用 zstd 压缩数据
4. 写入临时文件 `.dit/objects/tmp/<uuid>`
5. 通过 `os.replace()` 原子重命名到最终路径

```bash
# 正常使用时你不需要关心压缩细节，dit add/commit 会自动处理
dit add train.jsonl
dit commit -m "add training data"
```

## 查看磁盘占用

使用 `dit stats` 查看仓库的存储统计：

```bash
# 查看当前分支的数据统计
dit stats

# 查看指定分支
dit stats --ref feature-v2

# 对比两个版本的数据变化
dit stats --compare main feature-v2

# JSON 格式输出，方便脚本处理
dit stats --format json
```

也可以直接查看对象目录的磁盘占用：

```bash
# 查看各类型对象的磁盘占用
du -sh .dit/objects/*/

# 典型输出
# 156K    .dit/objects/commits/
# 2.1M    .dit/objects/manifests/
# 89M     .dit/objects/rows/
# 1.4M    .dit/objects/sidecars/
# 204K    .dit/objects/trees/
# 4K      .dit/objects/tmp/
```

## 压缩效果

Zstd 对 JSONL 训练数据的压缩率通常在 2:1 到 6:1 之间。实际效果取决于数据内容：

- 结构化对话数据（重复的 JSON key）：压缩率较高
- 包含大量代码或自然语言文本：压缩率中等
- 已经是 base64 编码的二进制内容：压缩率较低

## 注意事项

- 压缩和解压对用户完全透明，所有 dit 命令自动处理
- 对象一旦写入不可修改（immutable），相同内容只存储一份
- `tmp/` 目录用于原子写入的中间文件，正常情况下为空；残留文件会被 `dit gc` 清理
- 不要手动修改 `.dit/objects/` 下的文件，否则会导致 hash 校验失败
- 如果怀疑对象损坏，使用 `dit fsck` 进行完整性检查
