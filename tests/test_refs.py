from pathlib import Path

from dit.core.refs import RefStore


class TestRefStore:
    def test_get_head_default_main(self, tmp_repo: Path):
        dot = tmp_repo / ".dit"
        dot.mkdir()
        refs = RefStore(dot)
        refs.init()
        assert refs.get_head() == "ref:main"

    def test_get_set_branch(self, tmp_repo: Path):
        dot = tmp_repo / ".dit"
        dot.mkdir()
        refs = RefStore(dot)
        refs.init()
        refs.set_branch("main", "aa" * 32)
        assert refs.get_branch("main") == "aa" * 32

    def test_get_nonexistent_branch_returns_none(self, tmp_repo: Path):
        dot = tmp_repo / ".dit"
        dot.mkdir()
        refs = RefStore(dot)
        refs.init()
        assert refs.get_branch("nonexistent") is None

    def test_resolve_head_no_commits(self, tmp_repo: Path):
        dot = tmp_repo / ".dit"
        dot.mkdir()
        refs = RefStore(dot)
        refs.init()
        assert refs.resolve_head() is None

    def test_resolve_head_with_commit(self, tmp_repo: Path):
        dot = tmp_repo / ".dit"
        dot.mkdir()
        refs = RefStore(dot)
        refs.init()
        refs.set_branch("main", "cc" * 32)
        assert refs.resolve_head() == "cc" * 32

    def test_current_branch_name(self, tmp_repo: Path):
        dot = tmp_repo / ".dit"
        dot.mkdir()
        refs = RefStore(dot)
        refs.init()
        assert refs.current_branch() == "main"

    def test_list_branches(self, tmp_repo: Path):
        dot = tmp_repo / ".dit"
        dot.mkdir()
        refs = RefStore(dot)
        refs.init()
        refs.set_branch("main", "aa" * 32)
        refs.set_branch("dev", "bb" * 32)
        branches = refs.list_branches()
        assert set(branches.keys()) == {"main", "dev"}


class TestDeleteBranch:
    def test_delete_existing_branch(self, tmp_path: Path):
        dot = tmp_path / ".dit"
        refs = RefStore(dot)
        refs.init()
        refs.set_branch("feature", "a" * 64)
        assert refs.delete_branch("feature") is True
        assert refs.get_branch("feature") is None

    def test_delete_nonexistent_branch(self, tmp_path: Path):
        dot = tmp_path / ".dit"
        refs = RefStore(dot)
        refs.init()
        assert refs.delete_branch("nope") is False

    def test_delete_branch_does_not_affect_others(self, tmp_path: Path):
        dot = tmp_path / ".dit"
        refs = RefStore(dot)
        refs.init()
        refs.set_branch("keep", "a" * 64)
        refs.set_branch("remove", "b" * 64)
        refs.delete_branch("remove")
        assert refs.get_branch("keep") == "a" * 64


class TestTags:
    def test_set_and_get_tag(self, tmp_path: Path):
        dot = tmp_path / ".dit"
        refs = RefStore(dot)
        refs.init()
        refs.set_tag("v1.0", "a" * 64)
        assert refs.get_tag("v1.0") == "a" * 64

    def test_get_nonexistent_tag(self, tmp_path: Path):
        dot = tmp_path / ".dit"
        refs = RefStore(dot)
        refs.init()
        assert refs.get_tag("nope") is None

    def test_delete_tag(self, tmp_path: Path):
        dot = tmp_path / ".dit"
        refs = RefStore(dot)
        refs.init()
        refs.set_tag("v1.0", "a" * 64)
        assert refs.delete_tag("v1.0") is True
        assert refs.get_tag("v1.0") is None

    def test_delete_nonexistent_tag(self, tmp_path: Path):
        dot = tmp_path / ".dit"
        refs = RefStore(dot)
        refs.init()
        assert refs.delete_tag("nope") is False

    def test_list_tags(self, tmp_path: Path):
        dot = tmp_path / ".dit"
        refs = RefStore(dot)
        refs.init()
        refs.set_tag("v1.0", "a" * 64)
        refs.set_tag("v2.0", "b" * 64)
        tags = refs.list_tags()
        assert tags == {"v1.0": "a" * 64, "v2.0": "b" * 64}

    def test_list_tags_empty(self, tmp_path: Path):
        dot = tmp_path / ".dit"
        refs = RefStore(dot)
        refs.init()
        assert refs.list_tags() == {}


class TestAtomicWrite:
    """Verify that ref writes use atomic write-tmp-then-rename pattern."""

    def test_set_branch_no_partial_write_on_rename_failure(self, tmp_path: Path):
        """If os.replace fails, the original ref file must remain intact."""
        from unittest.mock import patch

        dot = tmp_path / ".dit"
        refs = RefStore(dot)
        refs.init()

        # Write an initial value
        refs.set_branch("main", "aa" * 32)
        assert refs.get_branch("main") == "aa" * 32

        # Simulate os.replace failure mid-write

        def failing_replace(src, dst):
            raise OSError("disk error")

        with patch("os.replace", side_effect=failing_replace):
            try:
                refs.set_branch("main", "bb" * 32)
            except OSError:
                pass

        # Original value must still be intact
        assert refs.get_branch("main") == "aa" * 32

        # No temp files should be left behind
        tmp_files = list(refs.refs_dir.glob(".tmp-*"))
        assert tmp_files == []

    def test_set_tag_atomic(self, tmp_path: Path):
        """set_tag should also use atomic writes."""
        from unittest.mock import patch

        dot = tmp_path / ".dit"
        refs = RefStore(dot)
        refs.init()

        refs.set_tag("v1.0", "aa" * 32)

        def failing_replace(src, dst):
            raise OSError("disk error")

        with patch("os.replace", side_effect=failing_replace):
            try:
                refs.set_tag("v1.0", "bb" * 32)
            except OSError:
                pass

        assert refs.get_tag("v1.0") == "aa" * 32
        tmp_files = list(refs.tags_dir.glob(".tmp-*"))
        assert tmp_files == []

    def test_set_head_atomic(self, tmp_path: Path):
        """set_head should use atomic writes."""
        from unittest.mock import patch

        dot = tmp_path / ".dit"
        refs = RefStore(dot)
        refs.init()
        assert refs.get_head() == "ref:main"

        def failing_replace(src, dst):
            raise OSError("disk error")

        with patch("os.replace", side_effect=failing_replace):
            try:
                refs.set_head("ref:dev")
            except OSError:
                pass

        # HEAD must still point to main
        assert refs.get_head() == "ref:main"

    def test_atomic_write_succeeds_normally(self, tmp_path: Path):
        """Normal atomic write should work end-to-end with no leftover temps."""
        dot = tmp_path / ".dit"
        refs = RefStore(dot)
        refs.init()

        refs.set_branch("main", "cc" * 32)
        assert refs.get_branch("main") == "cc" * 32

        # No temp files left
        tmp_files = list(refs.refs_dir.glob(".tmp-*"))
        assert tmp_files == []
