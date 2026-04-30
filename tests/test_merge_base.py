# tests/test_merge_base.py
"""Tests for BFS merge-base algorithm."""
import time

from dit.core.merge_base import find_merge_base
from dit.core.objects import Commit, Tree, serialize_commit, serialize_tree
from dit.core.store import ObjectStore


def _make_commit(store: ObjectStore, parent_hashes: list[str], msg: str = "c") -> str:
    tree = Tree(entries=[])
    tree_bytes = serialize_tree(tree)
    tree_hash = store.write("trees", tree_bytes)
    commit = Commit(
        tree_hash=tree_hash,
        parent_hashes=parent_hashes,
        author="test",
        message=msg,
        timestamp=int(time.time()),
    )
    commit_bytes = serialize_commit(commit)
    return store.write("commits", commit_bytes)


class TestFindMergeBase:
    def test_linear_history(self, tmp_path):
        """A -- B -- C: merge_base(B, C) = B"""
        store = ObjectStore(tmp_path / "objects")
        a = _make_commit(store, [], "a")
        b = _make_commit(store, [a], "b")
        c = _make_commit(store, [b], "c")
        assert find_merge_base(store, b, c) == b

    def test_same_commit(self, tmp_path):
        store = ObjectStore(tmp_path / "objects")
        a = _make_commit(store, [], "a")
        assert find_merge_base(store, a, a) == a

    def test_a_is_ancestor_of_b(self, tmp_path):
        """A -- B -- C: merge_base(A, C) = A (fast-forward)"""
        store = ObjectStore(tmp_path / "objects")
        a = _make_commit(store, [], "a")
        b = _make_commit(store, [a], "b")
        c = _make_commit(store, [b], "c")
        assert find_merge_base(store, a, c) == a

    def test_b_is_ancestor_of_a(self, tmp_path):
        """A -- B -- C: merge_base(C, A) = A"""
        store = ObjectStore(tmp_path / "objects")
        a = _make_commit(store, [], "a")
        b = _make_commit(store, [a], "b")
        c = _make_commit(store, [b], "c")
        assert find_merge_base(store, c, a) == a

    def test_diamond(self, tmp_path):
        """
        A -- B -- D
         \\       /
          -- C --
        merge_base(D, C) = A  (or merge_base(B, C) = A)
        """
        store = ObjectStore(tmp_path / "objects")
        a = _make_commit(store, [], "a")
        b = _make_commit(store, [a], "b")
        c = _make_commit(store, [a], "c")
        d = _make_commit(store, [b, c], "d")
        assert find_merge_base(store, b, c) == a
        assert find_merge_base(store, d, c) == c

    def test_no_common_ancestor(self, tmp_path):
        """Two independent histories."""
        store = ObjectStore(tmp_path / "objects")
        a = _make_commit(store, [], "a")
        b = _make_commit(store, [], "b")
        assert find_merge_base(store, a, b) is None

    def test_longer_diverged(self, tmp_path):
        """
        A -- B -- C -- D (branch1)
         \\
          -- E -- F     (branch2)
        merge_base(D, F) = A
        """
        store = ObjectStore(tmp_path / "objects")
        a = _make_commit(store, [], "a")
        b = _make_commit(store, [a], "b")
        c = _make_commit(store, [b], "c")
        d = _make_commit(store, [c], "d")
        e = _make_commit(store, [a], "e")
        f = _make_commit(store, [e], "f")
        assert find_merge_base(store, d, f) == a

    def test_criss_cross(self, tmp_path):
        """
        A -- B -- D
         \\   \\X  /
          -- C -- E
        B and C both merge each other: D=merge(B,C), E=merge(C,B)
        merge_base(D, E) should return B or C (both valid).
        """
        store = ObjectStore(tmp_path / "objects")
        a = _make_commit(store, [], "a")
        b = _make_commit(store, [a], "b")
        c = _make_commit(store, [a], "c")
        d = _make_commit(store, [b, c], "d")
        e = _make_commit(store, [c, b], "e")
        result = find_merge_base(store, d, e)
        assert result in (b, c)
