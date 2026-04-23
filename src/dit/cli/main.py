import os
import time
from datetime import datetime, timezone
from pathlib import Path

import typer

from dit.core.index import StagingIndex
from dit.core.objects import (
    Tree,
    TreeEntry,
    Commit,
    serialize_manifest,
    serialize_tree,
    serialize_commit,
    deserialize_commit,
    deserialize_tree,
)
from dit.core.refs import RefStore
from dit.core.store import ObjectStore
from dit.core.workspace import build_manifest_for_file, find_jsonl_files

app = typer.Typer(name="dit", help="Git-like version control for SFT training data.")


def find_repo_root() -> Path:
    cwd = Path.cwd()
    p = cwd
    while True:
        if (p / ".datahub").is_dir():
            return p
        if p.parent == p:
            break
        p = p.parent
    typer.echo("fatal: not a dit repository", err=True)
    raise typer.Exit(1)


def get_dot(repo_root: Path) -> Path:
    return repo_root / ".datahub"


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context):
    """Git-like version control for SFT training data."""
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


@app.command()
def version():
    """Print dit version."""
    from dit import __version__
    typer.echo(f"dit {__version__}")


@app.command()
def init():
    """Initialize a new dit repository in the current directory."""
    cwd = Path.cwd()
    dot = cwd / ".datahub"
    if dot.exists():
        typer.echo(f"Already initialized dit repository in {cwd}")
        return
    dot.mkdir()
    (dot / "objects").mkdir()
    RefStore(dot).init()
    typer.echo(f"Initialized empty dit repository in {cwd}")


@app.command()
def add(paths: list[str] = typer.Argument(..., help="Files or directories to stage")):
    """Stage JSONL files for the next commit."""
    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    index = StagingIndex(dot / "index")

    for path_str in paths:
        target = Path(path_str).resolve()
        if path_str == ".":
            files = find_jsonl_files(repo_root)
        elif target.is_dir():
            files = find_jsonl_files(target)
        elif target.is_file() and target.suffix == ".jsonl":
            files = [target]
        else:
            typer.echo(f"fatal: pathspec '{path_str}' did not match any jsonl files", err=True)
            raise typer.Exit(1)

        for fp in files:
            manifest, row_data = build_manifest_for_file(fp)
            for rh, data in row_data.items():
                store.write("rows", data)
            manifest_bytes = serialize_manifest(manifest)
            manifest_hash = store.write("manifests", manifest_bytes)
            rel = str(fp.relative_to(repo_root))
            index.stage(rel, manifest_hash)
            typer.echo(f"  staged {rel} ({len(manifest.entries)} rows)")


if __name__ == "__main__":
    app()


@app.command()
def commit(message: str = typer.Option(..., "-m", help="Commit message")):
    """Create a commit from staged files."""
    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    index = StagingIndex(dot / "index")
    refs = RefStore(dot)

    staged = index.entries()
    if not staged:
        typer.echo("nothing to commit (staging area is empty)", err=True)
        raise typer.Exit(1)

    # Build tree from staged manifests + any existing tree entries from HEAD
    head_commit_hash = refs.resolve_head()
    existing_tree_entries: dict[str, TreeEntry] = {}
    if head_commit_hash:
        commit_data = store.read("commits", head_commit_hash)
        old_commit = deserialize_commit(commit_data)
        tree_data = store.read("trees", old_commit.tree_hash)
        old_tree = deserialize_tree(tree_data)
        for e in old_tree.entries:
            existing_tree_entries[e.name] = e

    # Merge staged files into tree
    for rel_path, manifest_hash in staged.items():
        existing_tree_entries[rel_path] = TreeEntry(
            name=rel_path, obj_type="manifest", obj_hash=manifest_hash
        )

    tree = Tree(entries=list(existing_tree_entries.values()))
    tree_bytes = serialize_tree(tree)
    tree_hash = store.write("trees", tree_bytes)

    parent_hashes = [head_commit_hash] if head_commit_hash else []
    c = Commit(
        tree_hash=tree_hash,
        parent_hashes=parent_hashes,
        author=_get_author(),
        message=message,
        timestamp=int(time.time()),
    )
    commit_bytes = serialize_commit(c)
    commit_hash = store.write("commits", commit_bytes)

    branch = refs.current_branch()
    refs.set_branch(branch, commit_hash)
    index.clear()
    typer.echo(f"[{branch} {commit_hash[:8]}] {message}")


@app.command()
def log():
    """Show commit history."""
    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)

    commit_hash = refs.resolve_head()
    if not commit_hash:
        typer.echo("No commits yet.")
        return

    while commit_hash:
        data = store.read("commits", commit_hash)
        c = deserialize_commit(data)
        ts = datetime.fromtimestamp(c.timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        typer.echo(f"commit {commit_hash}")
        typer.echo(f"Author: {c.author}")
        typer.echo(f"Date:   {ts}")
        typer.echo(f"\n    {c.message}\n")
        commit_hash = c.parent_hashes[0] if c.parent_hashes else None


def _get_author() -> str:
    return os.environ.get("DIT_AUTHOR", os.environ.get("USER", "unknown"))
