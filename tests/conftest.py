import json
from pathlib import Path

import pytest


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    """Create a temporary directory to act as a dit repository root."""
    return tmp_path


@pytest.fixture
def sample_conversation() -> dict:
    """A minimal OpenAI-format conversation for testing."""
    return {
        "messages": [
            {"role": "user", "content": "Implement an LRU cache in Python"},
            {"role": "assistant", "content": "Here's an LRU cache implementation..."},
        ]
    }


@pytest.fixture
def sample_jsonl(tmp_repo: Path, sample_conversation: dict) -> Path:
    """Create a sample JSONL file with 3 conversations."""
    convos = [
        sample_conversation,
        {
            "messages": [
                {"role": "user", "content": "Explain Python GIL"},
                {"role": "assistant", "content": "The GIL is..."},
            ]
        },
        {
            "messages": [
                {"role": "user", "content": "Write a bubble sort"},
                {"role": "assistant", "content": "def bubble_sort(arr):..."},
            ]
        },
    ]
    fp = tmp_repo / "coding.jsonl"
    with open(fp, "w") as f:
        for c in convos:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    return fp
