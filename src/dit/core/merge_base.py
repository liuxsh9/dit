# src/dit/core/merge_base.py
from __future__ import annotations

from collections import deque

from dit.core.objects import deserialize_commit
from dit.core.store import ObjectStore


def find_merge_base(store: ObjectStore, hash_a: str, hash_b: str) -> str | None:
    if hash_a == hash_b:
        return hash_a

    ancestors_a: set[str] = {hash_a}
    ancestors_b: set[str] = {hash_b}
    queue_a: deque[str] = deque([hash_a])
    queue_b: deque[str] = deque([hash_b])

    while queue_a or queue_b:
        if queue_a:
            current = queue_a.popleft()
            if current in ancestors_b:
                return current
            commit_data = store.read("commits", current)
            if commit_data is not None:
                commit = deserialize_commit(commit_data)
                for parent in commit.parent_hashes:
                    if parent not in ancestors_a:
                        ancestors_a.add(parent)
                        queue_a.append(parent)
                        if parent in ancestors_b:
                            return parent

        if queue_b:
            current = queue_b.popleft()
            if current in ancestors_a:
                return current
            commit_data = store.read("commits", current)
            if commit_data is not None:
                commit = deserialize_commit(commit_data)
                for parent in commit.parent_hashes:
                    if parent not in ancestors_b:
                        ancestors_b.add(parent)
                        queue_b.append(parent)
                        if parent in ancestors_a:
                            return parent

    return None
