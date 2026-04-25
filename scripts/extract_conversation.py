#!/usr/bin/env python3
"""从 Claude Code 会话文件中提取完整对话，格式化为 Markdown。"""

import json
import sys
from pathlib import Path

SESSION_FILE = Path.home() / ".claude/projects/-Users-lxs-code-datahub/b2ba79b4-d903-4a84-a64f-4978d689a637.jsonl"
OUTPUT_FILE = Path(__file__).parent.parent / "docs/superpowers/specs/2026-04-22-datahub-design-process.md"


def extract_text(content) -> str:
    """从 message content 中提取纯文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                texts.append(item.get("text", ""))
        return "\n".join(texts)
    return str(content)


def should_skip(text: str) -> bool:
    """跳过无实质内容的消息。"""
    stripped = text.strip()
    if len(stripped) < 2:
        return True
    # 跳过 skill 加载消息
    if "Base directory for this skill" in stripped:
        return True
    # 跳过纯中断提示
    if stripped == "[Request interrupted by user]":
        return True
    # 跳过纯操作性 assistant 消息（没有设计内容）
    skip_phrases = [
        "Continue",
        "Let me read the full file",
        "Let me start by brainstorming",
        "Now I have the complete conversation",
        "开始写正式 spec 文档",
    ]
    if stripped in skip_phrases or any(stripped.startswith(p) for p in skip_phrases):
        return True
    return False


# 设计讨论的最后一条有效用户消息（"确认！"之后是写文档的操作性对话）
CUTOFF_KEYWORDS = ["可以，写 plans 之前"]


def main():
    messages = []
    with open(SESSION_FILE, "r") as f:
        for line in f:
            obj = json.loads(line.strip())
            msg_type = obj.get("type", "")
            if msg_type not in ("user", "assistant"):
                continue

            msg = obj.get("message", {})
            content = msg.get("content", "") if isinstance(msg, dict) else ""
            text = extract_text(content)

            if should_skip(text):
                continue

            messages.append({"role": msg_type, "text": text})

    # 截断到设计讨论结束（"确认！" 后的 spec 写入/对话提取等操作性内容不保留）
    cutoff_idx = len(messages)
    for i, msg in enumerate(messages):
        if msg["role"] == "user" and any(kw in msg["text"] for kw in CUTOFF_KEYWORDS):
            cutoff_idx = i  # 不包含这条
            break
    messages = messages[:cutoff_idx]

    # 构建 Markdown
    lines = [
        "# Dit 设计过程完整记录",
        "",
        "> 本文档由脚本从 Claude Code 会话历史中自动提取，保留了设计过程中的完整对话内容。",
        "",
        "---",
        "",
    ]

    turn_num = 0
    for msg in messages:
        role = msg["role"]
        text = msg["text"]

        if role == "user":
            turn_num += 1
            lines.append(f"## 用户 (Turn {turn_num})")
            lines.append("")
            lines.append(text)
            lines.append("")
            lines.append("---")
            lines.append("")
        else:
            lines.append(f"### 设计师回复")
            lines.append("")
            lines.append(text)
            lines.append("")
            lines.append("---")
            lines.append("")

    output = "\n".join(lines)
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(output, encoding="utf-8")
    print(f"✅ 已写入 {OUTPUT_FILE}")
    print(f"   共 {turn_num} 轮用户消息，{len(messages)} 条总消息")
    print(f"   文件大小: {len(output):,} 字符")


if __name__ == "__main__":
    main()
