# 三方合并

Dit 的合并操作在行级别进行，而非文件级别。当两个分支各自修改了同一文件的不同行时，dit 能自动合并；只有当同一行在两个分支上被不同地修改时，才会产生冲突。

本文介绍分支创建、切换、合并和 cherry-pick 的完整工作流。

---

## 创建与切换分支

```bash
# 查看当前分支
dit branch

# 创建新分支
dit branch feature/add-math-data

# 切换到新分支
dit checkout feature/add-math-data

# 创建并切换（快捷方式）
dit checkout -b feature/add-math-data
```

`dit branch` 列出所有分支，当前分支前有 `*` 标记：

```
* main
  feature/add-math-data
```

---

## 快进合并 (Fast-forward)

当 main 没有新提交，feature 分支是 main 的直接后裔时，dit 直接移动分支指针，不创建合并提交。

```bash
# 在 feature 分支上工作
dit checkout -b feature/new-samples

cat >> train.jsonl << 'EOF'
{"messages": [{"role": "user", "content": "什么是递归？"}, {"role": "assistant", "content": "递归是函数调用自身的编程技巧，需要基准条件防止无限递归。"}]}
EOF

dit add train.jsonl
dit commit -m "添加递归样本"

# 切回 main 并合并
dit checkout main
dit merge feature/new-samples
```

输出：

```
Fast-forward to a1b2c3d4.
```

快进合并的特点：
- 不创建新的合并提交
- main 指针直接移动到 feature 分支的最新提交
- `dit log` 中看不到合并记录，历史是线性的

---

## 三方合并 (Divergent Merge)

当两个分支从同一基点各自发展时，dit 执行三方合并，创建一个有两个父提交的合并提交。

```bash
# 从 main 创建两个分支
dit checkout -b branch-a
cat >> train.jsonl << 'EOF'
{"messages": [{"role": "user", "content": "什么是闭包？"}, {"role": "assistant", "content": "闭包是引用了外部作用域变量的内部函数。"}]}
EOF
dit add train.jsonl
dit commit -m "branch-a: 添加闭包样本"

dit checkout main
dit checkout -b branch-b
cat > eval.jsonl << 'EOF'
{"messages": [{"role": "user", "content": "如何排序列表？"}, {"role": "assistant", "content": "使用 list.sort() 原地排序或 sorted() 返回新列表。"}]}
EOF
dit add eval.jsonl
dit commit -m "branch-b: 添加排序评估样本"

# 切回 main，先合并 branch-a
dit checkout main
dit merge branch-a

# 再合并 branch-b（此时 main 已前进，需要三方合并）
dit merge branch-b
```

输出：

```
Merge branch 'branch-b' into main
```

三方合并的过程：
1. 找到两个分支的共同祖先（merge base）
2. 分别计算 base→main 和 base→branch-b 的行级差异
3. 将两边的变更合并到一起
4. 创建一个有两个父提交的合并提交

---

## 冲突检测

当同一行在两个分支上被不同地修改时，dit 会报告冲突。

```bash
# 两个分支修改同一条样本的回答
dit checkout -b improve-v1
# 修改 train.jsonl 第一行的 assistant 回答
dit add train.jsonl
dit commit -m "v1: 改进 GIL 回答"

dit checkout main
dit checkout -b improve-v2
# 以不同方式修改同一行的 assistant 回答
dit add train.jsonl
dit commit -m "v2: 改进 GIL 回答（另一版本）"

# 合并时产生冲突
dit checkout main
dit merge improve-v1
dit merge improve-v2
```

冲突时 dit 会报告哪些文件的哪些行存在冲突，合并进入暂停状态。

解决冲突的流程：
1. 手动编辑冲突文件，选择保留哪个版本的行
2. `dit add <file>` 标记冲突已解决
3. `dit commit` 完成合并提交

放弃合并：

```bash
dit merge --abort
```

---

## Cherry-pick

从其他分支摘取单个提交应用到当前分支：

```bash
# 查看目标分支的提交历史
dit checkout feature/experiments
dit log --oneline

# 切回 main，摘取指定提交
dit checkout main
dit cherry-pick abc1234
```

cherry-pick 会将指定提交引入的行级变更应用到当前分支，并创建一个新提交。如果变更与当前分支冲突，处理方式与 merge 冲突相同。

---

## 小贴士

- dit 的合并粒度是行（row），不是文件。两个分支修改同一文件的不同行不会冲突
- 冲突的判定标准：同一 row_hash 在两个分支上被不同地修改（删除、替换为不同内容）
- 快进合并不创建合并提交，历史更干净；三方合并会创建合并提交，保留分支拓扑
- `dit branch -d <name>` 删除已合并的分支，`-D` 强制删除未合并的分支
- 合并前建议先 `dit status` 确认工作目录干净，避免未提交的修改干扰合并
