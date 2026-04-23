from pathlib import Path

from dit.core.refs import RefStore


class TestRefStore:
    def test_get_head_default_main(self, tmp_repo: Path):
        dot = tmp_repo / ".datahub"
        dot.mkdir()
        refs = RefStore(dot)
        refs.init()
        assert refs.get_head() == "ref:main"

    def test_get_set_branch(self, tmp_repo: Path):
        dot = tmp_repo / ".datahub"
        dot.mkdir()
        refs = RefStore(dot)
        refs.init()
        refs.set_branch("main", "aa" * 32)
        assert refs.get_branch("main") == "aa" * 32

    def test_get_nonexistent_branch_returns_none(self, tmp_repo: Path):
        dot = tmp_repo / ".datahub"
        dot.mkdir()
        refs = RefStore(dot)
        refs.init()
        assert refs.get_branch("nonexistent") is None

    def test_resolve_head_no_commits(self, tmp_repo: Path):
        dot = tmp_repo / ".datahub"
        dot.mkdir()
        refs = RefStore(dot)
        refs.init()
        assert refs.resolve_head() is None

    def test_resolve_head_with_commit(self, tmp_repo: Path):
        dot = tmp_repo / ".datahub"
        dot.mkdir()
        refs = RefStore(dot)
        refs.init()
        refs.set_branch("main", "cc" * 32)
        assert refs.resolve_head() == "cc" * 32

    def test_current_branch_name(self, tmp_repo: Path):
        dot = tmp_repo / ".datahub"
        dot.mkdir()
        refs = RefStore(dot)
        refs.init()
        assert refs.current_branch() == "main"

    def test_list_branches(self, tmp_repo: Path):
        dot = tmp_repo / ".datahub"
        dot.mkdir()
        refs = RefStore(dot)
        refs.init()
        refs.set_branch("main", "aa" * 32)
        refs.set_branch("dev", "bb" * 32)
        branches = refs.list_branches()
        assert set(branches.keys()) == {"main", "dev"}
