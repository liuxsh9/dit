import os
from pathlib import Path

from typer.testing import CliRunner

from dit.cli.main import app

runner = CliRunner()


class TestInit:
    def test_init_creates_dit_dir(self, tmp_path: Path):
        os.chdir(tmp_path)
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0
        assert (tmp_path / ".dit").is_dir()
        assert (tmp_path / ".dit" / "HEAD").exists()
        assert (tmp_path / ".dit" / "refs" / "heads").is_dir()
        assert (tmp_path / ".dit" / "objects").is_dir()

    def test_init_already_exists(self, tmp_path: Path):
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0
        assert "already" in result.stdout.lower() or "initialized" in result.stdout.lower()


import json


class TestAdd:
    def test_add_single_file(self, tmp_path: Path):
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])
        fp = tmp_path / "coding.jsonl"
        fp.write_text(json.dumps({"messages": [{"role": "user", "content": "hi"}]}) + "\n")
        result = runner.invoke(app, ["add", "coding.jsonl"])
        assert result.exit_code == 0

        idx_path = tmp_path / ".dit" / "index"
        assert idx_path.exists()
        idx = json.loads(idx_path.read_text())
        assert "coding.jsonl" in idx

    def test_add_dot(self, tmp_path: Path):
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])
        (tmp_path / "a.jsonl").write_text('{"x":1}\n')
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "b.jsonl").write_text('{"y":2}\n')
        result = runner.invoke(app, ["add", "."])
        assert result.exit_code == 0
        idx = json.loads((tmp_path / ".dit" / "index").read_text())
        assert "a.jsonl" in idx
        assert "sub/b.jsonl" in idx

    def test_add_nonexistent_file(self, tmp_path: Path):
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])
        result = runner.invoke(app, ["add", "nope.jsonl"])
        assert result.exit_code != 0


class TestCommit:
    def _setup_staged(self, tmp_path: Path):
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])
        fp = tmp_path / "data.jsonl"
        fp.write_text('{"messages":[{"role":"user","content":"test"}]}\n')
        runner.invoke(app, ["add", "data.jsonl"])

    def test_commit_creates_commit(self, tmp_path: Path):
        self._setup_staged(tmp_path)
        result = runner.invoke(app, ["commit", "-m", "initial"])
        assert result.exit_code == 0
        assert "initial" in result.stdout

        head_ref = (tmp_path / ".dit" / "refs" / "heads" / "main").read_text().strip()
        assert len(head_ref) == 64

    def test_commit_clears_index(self, tmp_path: Path):
        self._setup_staged(tmp_path)
        runner.invoke(app, ["commit", "-m", "first"])
        idx = json.loads((tmp_path / ".dit" / "index").read_text())
        assert idx == {}

    def test_commit_nothing_staged(self, tmp_path: Path):
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])
        result = runner.invoke(app, ["commit", "-m", "empty"])
        assert result.exit_code != 0

    def test_second_commit_has_parent(self, tmp_path: Path):
        self._setup_staged(tmp_path)
        runner.invoke(app, ["commit", "-m", "first"])
        first_hash = (tmp_path / ".dit" / "refs" / "heads" / "main").read_text().strip()

        (tmp_path / "data.jsonl").write_text('{"messages":[{"role":"user","content":"updated"}]}\n')
        runner.invoke(app, ["add", "data.jsonl"])
        runner.invoke(app, ["commit", "-m", "second"])
        second_hash = (tmp_path / ".dit" / "refs" / "heads" / "main").read_text().strip()

        assert first_hash != second_hash
        from dit.core.store import ObjectStore
        from dit.core.objects import deserialize_commit
        store = ObjectStore(tmp_path / ".dit" / "objects")
        commit_data = store.read("commits", second_hash)
        commit_obj = deserialize_commit(commit_data)
        assert commit_obj.parent_hashes == [first_hash]


class TestLog:
    def test_log_shows_commits(self, tmp_path: Path):
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])
        (tmp_path / "a.jsonl").write_text('{"x":1}\n')
        runner.invoke(app, ["add", "."])
        runner.invoke(app, ["commit", "-m", "first commit"])
        (tmp_path / "a.jsonl").write_text('{"x":1}\n{"y":2}\n')
        runner.invoke(app, ["add", "."])
        runner.invoke(app, ["commit", "-m", "second commit"])

        result = runner.invoke(app, ["log"])
        assert result.exit_code == 0
        assert "second commit" in result.stdout
        assert "first commit" in result.stdout
        assert result.stdout.index("second") < result.stdout.index("first")

    def test_log_empty_repo(self, tmp_path: Path):
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])
        result = runner.invoke(app, ["log"])
        assert result.exit_code == 0
        assert "no commits" in result.stdout.lower()


class TestDiff:
    def test_diff_shows_changes(self, tmp_path: Path):
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])
        (tmp_path / "data.jsonl").write_text('{"messages":[{"role":"user","content":"hello"}]}\n')
        runner.invoke(app, ["add", "."])
        runner.invoke(app, ["commit", "-m", "initial"])

        (tmp_path / "data.jsonl").write_text('{"messages":[{"role":"user","content":"world"}]}\n')
        result = runner.invoke(app, ["diff"])
        assert result.exit_code == 0
        assert "+1" in result.stdout
        assert "-1" in result.stdout

    def test_diff_no_changes(self, tmp_path: Path):
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])
        (tmp_path / "data.jsonl").write_text('{"x":1}\n')
        runner.invoke(app, ["add", "."])
        runner.invoke(app, ["commit", "-m", "initial"])
        result = runner.invoke(app, ["diff"])
        assert result.exit_code == 0
        assert "no changes" in result.stdout.lower()

    def test_diff_new_file(self, tmp_path: Path):
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])
        (tmp_path / "old.jsonl").write_text('{"x":1}\n')
        runner.invoke(app, ["add", "."])
        runner.invoke(app, ["commit", "-m", "initial"])
        (tmp_path / "new.jsonl").write_text('{"y":2}\n')
        result = runner.invoke(app, ["diff"])
        assert result.exit_code == 0
        assert "new.jsonl" in result.stdout

    def test_diff_detects_refresh(self, tmp_path: Path):
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])
        row = {"messages": [
            {"role": "user", "content": "implement LRU"},
            {"role": "assistant", "content": "old response"},
        ]}
        (tmp_path / "data.jsonl").write_text(json.dumps(row) + "\n")
        runner.invoke(app, ["add", "."])
        runner.invoke(app, ["commit", "-m", "initial"])

        row["messages"][1]["content"] = "new response"
        (tmp_path / "data.jsonl").write_text(json.dumps(row) + "\n")
        result = runner.invoke(app, ["diff"])
        assert result.exit_code == 0
        assert "refresh" in result.stdout.lower()

    def test_diff_detects_row_reordering(self, tmp_path: Path):
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])
        first = {"messages": [{"role": "user", "content": "first"}]}
        second = {"messages": [{"role": "user", "content": "second"}]}
        (tmp_path / "data.jsonl").write_text(
            json.dumps(first) + "\n" + json.dumps(second) + "\n"
        )
        runner.invoke(app, ["add", "."])
        runner.invoke(app, ["commit", "-m", "initial"])

        (tmp_path / "data.jsonl").write_text(
            json.dumps(second) + "\n" + json.dumps(first) + "\n"
        )
        result = runner.invoke(app, ["diff"])

        assert result.exit_code == 0
        assert "data.jsonl" in result.stdout
        assert "2 → 2 rows" in result.stdout


class TestStatus:
    def test_status_clean(self, tmp_path: Path):
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])
        (tmp_path / "a.jsonl").write_text('{"x":1}\n')
        runner.invoke(app, ["add", "."])
        runner.invoke(app, ["commit", "-m", "init"])
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        assert "clean" in result.stdout.lower() or "nothing" in result.stdout.lower()

    def test_status_staged_files(self, tmp_path: Path):
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])
        (tmp_path / "a.jsonl").write_text('{"x":1}\n')
        runner.invoke(app, ["add", "."])
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        assert "staged" in result.stdout.lower()
        assert "a.jsonl" in result.stdout

    def test_status_modified_file(self, tmp_path: Path):
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])
        (tmp_path / "a.jsonl").write_text('{"x":1}\n')
        runner.invoke(app, ["add", "."])
        runner.invoke(app, ["commit", "-m", "init"])
        (tmp_path / "a.jsonl").write_text('{"x":1}\n{"y":2}\n')
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        assert "modified" in result.stdout.lower()


class TestEndToEnd:
    def test_full_workflow(self, tmp_path: Path):
        """init → add → commit → modify → diff → add → commit → log"""
        os.chdir(tmp_path)

        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0

        rows = [
            {"messages": [{"role": "user", "content": f"question {i}"}, {"role": "assistant", "content": f"answer {i}"}]}
            for i in range(5)
        ]
        (tmp_path / "data.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))

        result = runner.invoke(app, ["add", "."])
        assert result.exit_code == 0
        result = runner.invoke(app, ["commit", "-m", "add 5 conversations"])
        assert result.exit_code == 0

        result = runner.invoke(app, ["status"])
        assert "clean" in result.stdout.lower() or "nothing" in result.stdout.lower()

        modified_rows = [
            rows[0],
            rows[1],
            {"messages": [{"role": "user", "content": rows[4]["messages"][0]["content"]}, {"role": "assistant", "content": "refreshed answer 4"}]},
            {"messages": [{"role": "user", "content": "brand new question"}, {"role": "assistant", "content": "new answer"}]},
        ]
        (tmp_path / "data.jsonl").write_text("".join(json.dumps(r) + "\n" for r in modified_rows))

        result = runner.invoke(app, ["diff"])
        assert result.exit_code == 0
        assert "data.jsonl" in result.stdout

        runner.invoke(app, ["add", "."])
        result = runner.invoke(app, ["commit", "-m", "remove 2, add 1, refresh 1"])
        assert result.exit_code == 0

        result = runner.invoke(app, ["log"])
        assert "add 5 conversations" in result.stdout
        assert "remove 2, add 1, refresh 1" in result.stdout

    def test_multi_file_workflow(self, tmp_path: Path):
        """Test multiple JSONL files in subdirectories."""
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])

        (tmp_path / "feature-impl").mkdir()
        (tmp_path / "feature-impl" / "coding.jsonl").write_text('{"messages":[{"role":"user","content":"code q"}]}\n')
        (tmp_path / "bug-fix").mkdir()
        (tmp_path / "bug-fix" / "fixes.jsonl").write_text('{"messages":[{"role":"user","content":"fix q"}]}\n')

        runner.invoke(app, ["add", "."])
        result = runner.invoke(app, ["commit", "-m", "multi-dir data"])
        assert result.exit_code == 0

        result = runner.invoke(app, ["status"])
        assert "clean" in result.stdout.lower() or "nothing" in result.stdout.lower()


from dit.core.store import ObjectStore
from dit.core.objects import deserialize_commit, deserialize_tree
from dit.core.refs import RefStore


class TestNestedTreeCommit:
    def test_add_and_commit_nested(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from typer.testing import CliRunner
        from dit.cli.main import app
        runner = CliRunner()

        runner.invoke(app, ["init"])

        (tmp_path / "train").mkdir()
        (tmp_path / "eval").mkdir()
        (tmp_path / "train" / "sft.jsonl").write_text(
            '{"messages": [{"role": "user", "content": "hi"}]}\n'
        )
        (tmp_path / "eval" / "bench.jsonl").write_text(
            '{"messages": [{"role": "user", "content": "test"}]}\n'
        )

        result = runner.invoke(app, ["add", "."])
        assert result.exit_code == 0

        result = runner.invoke(app, ["commit", "-m", "nested commit"])
        assert result.exit_code == 0

        dot = tmp_path / ".dit"
        store = ObjectStore(dot / "objects")
        refs = RefStore(dot)
        commit_hash = refs.resolve_head()
        assert commit_hash is not None

        commit_data = store.read("commits", commit_hash)
        commit = deserialize_commit(commit_data)
        root_tree = deserialize_tree(store.read("trees", commit.tree_hash))

        entry_names = {e.name for e in root_tree.entries}
        assert "train" in entry_names, f"Expected 'train' in root tree, got {entry_names}"
        assert "eval" in entry_names, f"Expected 'eval' in root tree, got {entry_names}"
        assert "train/sft.jsonl" not in entry_names, "Root tree must not have flat slash paths"

        train_entry = next(e for e in root_tree.entries if e.name == "train")
        assert train_entry.obj_type == "tree"

    def test_blob_files_staged(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from typer.testing import CliRunner
        from dit.cli.main import app
        runner = CliRunner()

        runner.invoke(app, ["init"])
        (tmp_path / "README.md").write_text("# My Dataset\n")
        (tmp_path / "data.jsonl").write_text(
            '{"messages": [{"role": "user", "content": "q"}]}\n'
        )

        result = runner.invoke(app, ["add", "."])
        assert result.exit_code == 0

        result = runner.invoke(app, ["commit", "-m", "with readme"])
        assert result.exit_code == 0

        dot = tmp_path / ".dit"
        store = ObjectStore(dot / "objects")
        refs = RefStore(dot)
        commit_hash = refs.resolve_head()
        commit = deserialize_commit(store.read("commits", commit_hash))
        root_tree = deserialize_tree(store.read("trees", commit.tree_hash))
        entry_map = {e.name: e for e in root_tree.entries}

        assert "README.md" in entry_map
        assert entry_map["README.md"].obj_type == "blob"
        assert "data.jsonl" in entry_map
        assert entry_map["data.jsonl"].obj_type == "manifest"


class TestNestedTreeDiffStatus:
    def _init_nested_repo(self, tmp_path, runner, app):
        runner.invoke(app, ["init"])
        (tmp_path / "train").mkdir()
        (tmp_path / "train" / "sft.jsonl").write_text(
            '{"messages": [{"role": "user", "content": "hello"}]}\n'
        )
        runner.invoke(app, ["add", "."])
        runner.invoke(app, ["commit", "-m", "initial"])

    def test_diff_nested_no_changes(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from typer.testing import CliRunner
        from dit.cli.main import app
        runner = CliRunner()
        self._init_nested_repo(tmp_path, runner, app)
        result = runner.invoke(app, ["diff"])
        assert result.exit_code == 0
        assert "No changes" in result.output

    def test_diff_nested_shows_change(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from typer.testing import CliRunner
        from dit.cli.main import app
        runner = CliRunner()
        self._init_nested_repo(tmp_path, runner, app)
        (tmp_path / "train" / "sft.jsonl").write_text(
            '{"messages": [{"role": "user", "content": "hello"}]}\n'
            '{"messages": [{"role": "user", "content": "world"}]}\n'
        )
        result = runner.invoke(app, ["diff"])
        assert result.exit_code == 0
        assert "train/sft.jsonl" in result.output

    def test_status_nested(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from typer.testing import CliRunner
        from dit.cli.main import app
        runner = CliRunner()
        self._init_nested_repo(tmp_path, runner, app)
        (tmp_path / "train" / "sft.jsonl").write_text(
            '{"messages": [{"role": "user", "content": "modified"}]}\n'
        )
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        assert "train/sft.jsonl" in result.output


class TestMeta:
    def _make_repo_with_data(self, tmp_path):
        """Helper: init repo, add two JSONL files, commit them, return dot path."""
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])
        (tmp_path / "train.jsonl").write_text(
            '{"messages": [{"role": "user", "content": "hello world"}]}\n'
            '{"messages": [{"role": "user", "content": "foo bar baz"}]}\n'
        )
        (tmp_path / "eval.jsonl").write_text(
            '{"messages": [{"role": "user", "content": "test"}]}\n'
        )
        runner.invoke(app, ["add", "."])
        runner.invoke(app, ["commit", "-m", "initial data"])
        return tmp_path / ".dit"

    def test_meta_compute_all(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot = self._make_repo_with_data(tmp_path)

        result = runner.invoke(app, ["meta", "compute"])
        assert result.exit_code == 0, result.output
        assert "train.jsonl" in result.output
        assert "eval.jsonl" in result.output
        assert "Created commit" in result.output

        store = ObjectStore(dot / "objects")
        refs = RefStore(dot)
        head = refs.resolve_head()
        commit_data = store.read("commits", head)
        commit = deserialize_commit(commit_data)

        from dit.core.tree_walker import flatten_tree
        flat = flatten_tree(store, commit.tree_hash)
        for path, (obj_type, obj_hash, sidecar_hash) in flat.items():
            if obj_type == "manifest":
                assert sidecar_hash is not None, f"sidecar_hash missing for {path}"
                assert store.read("sidecars", sidecar_hash) is not None

    def test_meta_compute_single_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot = self._make_repo_with_data(tmp_path)

        result = runner.invoke(app, ["meta", "compute", "--file", "train.jsonl"])
        assert result.exit_code == 0, result.output
        assert "train.jsonl" in result.output
        assert "eval.jsonl" not in result.output
        assert "Created commit" in result.output

    def test_meta_compute_idempotent(self, tmp_path, monkeypatch):
        """Running meta compute twice produces identical commit trees (no duplicate objects)."""
        monkeypatch.chdir(tmp_path)
        dot = self._make_repo_with_data(tmp_path)

        runner.invoke(app, ["meta", "compute"])
        store = ObjectStore(dot / "objects")
        refs = RefStore(dot)
        h1 = refs.resolve_head()

        result2 = runner.invoke(app, ["meta", "compute"])
        assert result2.exit_code == 0
        h2 = refs.resolve_head()
        assert h1 == h2, "Second meta compute should be a no-op (same HEAD)"

    def test_meta_compute_no_commits(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(app, ["init"])
        result = runner.invoke(app, ["meta", "compute"])
        assert result.exit_code != 0
        assert "No commits" in result.output or "fatal" in result.output.lower()

    def test_meta_show_table(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot = self._make_repo_with_data(tmp_path)
        runner.invoke(app, ["meta", "compute"])

        result = runner.invoke(app, ["meta", "show", "train.jsonl"])
        assert result.exit_code == 0, result.output
        assert "train.jsonl" in result.output
        assert "Total chars" in result.output or "total" in result.output.lower()
        assert "Token estimate" in result.output or "token" in result.output.lower()

    def test_meta_show_json(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot = self._make_repo_with_data(tmp_path)
        runner.invoke(app, ["meta", "compute"])

        result = runner.invoke(app, ["meta", "show", "train.jsonl", "--format", "json"])
        assert result.exit_code == 0, result.output
        import json
        data = json.loads(result.output)
        assert "manifest_hash" in data
        assert "entries" in data
        assert len(data["entries"]) == 2

    def test_meta_show_no_sidecar(self, tmp_path, monkeypatch):
        """meta show on a file without sidecar exits with error."""
        monkeypatch.chdir(tmp_path)
        dot = self._make_repo_with_data(tmp_path)

        result = runner.invoke(app, ["meta", "show", "train.jsonl"])
        assert result.exit_code != 0
        assert "no sidecar" in result.output.lower() or "not found" in result.output.lower()

    def test_meta_show_file_not_in_tree(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot = self._make_repo_with_data(tmp_path)
        runner.invoke(app, ["meta", "compute"])

        result = runner.invoke(app, ["meta", "show", "nonexistent.jsonl"])
        assert result.exit_code != 0

    def _make_two_commits_with_sidecars(self, tmp_path):
        """Helper: create initial commit + meta compute, then add rows + second meta compute.
        Returns (dot, commit1_hash, commit2_hash)."""
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])
        (tmp_path / "train.jsonl").write_text(
            '{"messages": [{"role": "user", "content": "hello"}]}\n'
        )
        runner.invoke(app, ["add", "."])
        runner.invoke(app, ["commit", "-m", "v1"])
        runner.invoke(app, ["meta", "compute"])

        dot = tmp_path / ".dit"
        refs = RefStore(dot)
        commit1_hash = refs.resolve_head()

        # Add more rows
        (tmp_path / "train.jsonl").write_text(
            '{"messages": [{"role": "user", "content": "hello"}]}\n'
            '{"messages": [{"role": "user", "content": "world"}]}\n'
        )
        runner.invoke(app, ["add", "."])
        runner.invoke(app, ["commit", "-m", "v2"])
        runner.invoke(app, ["meta", "compute"])
        commit2_hash = refs.resolve_head()

        return dot, commit1_hash, commit2_hash

    def test_meta_diff_shows_changes(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot, c1, c2 = self._make_two_commits_with_sidecars(tmp_path)

        result = runner.invoke(app, ["meta", "diff", c1, c2])
        assert result.exit_code == 0, result.output
        assert "train.jsonl" in result.output
        # Should show row count change: 1 → 2
        assert "1" in result.output
        assert "2" in result.output

    def test_meta_diff_with_file_filter(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot, c1, c2 = self._make_two_commits_with_sidecars(tmp_path)

        result = runner.invoke(app, ["meta", "diff", c1, c2, "--file", "train.jsonl"])
        assert result.exit_code == 0, result.output
        assert "train.jsonl" in result.output

    def test_meta_diff_invalid_commit(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner.invoke(app, ["init"])
        (tmp_path / "train.jsonl").write_text('{"a": 1}\n')
        runner.invoke(app, ["add", "."])
        runner.invoke(app, ["commit", "-m", "v1"])
        runner.invoke(app, ["meta", "compute"])

        result = runner.invoke(app, ["meta", "diff", "z" * 64, "z" * 64])
        assert result.exit_code != 0
