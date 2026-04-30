import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from contextlib import contextmanager
from typing import Optional

import httpx
import typer

from dit.core.index import StagingIndex
from dit.core.diff import diff_manifests
from dit.core.objects import (
    Tree,
    TreeEntry,
    Commit,
    Manifest,
    object_hash,
    serialize_manifest,
    serialize_tree,
    serialize_commit,
    deserialize_commit,
    deserialize_tree,
    deserialize_manifest,
    deserialize_blob,
)
from dit.core.refs import RefStore
from dit.core.store import ObjectStore
from dit.core.stat_cache import StatCache
from dit.core.workspace import build_manifest_for_file, build_manifest_for_file_streaming, find_jsonl_files
from dit.cli.style import (
    hash_str, branch_str, added_str, removed_str, modified_str,
    refreshed_str, header_str, dim_str, warn_str, error_str,
    success_str, info_str,
)

app = typer.Typer(name="dit", help="Git-like version control for SFT training data.")


@contextmanager
def remote_error_boundary(action: str):
    """Translate httpx errors into git-style CLI failures without tracebacks."""
    try:
        yield
    except httpx.HTTPStatusError as exc:
        detail = ""
        try:
            payload = exc.response.json()
            if isinstance(payload, dict):
                detail = payload.get("detail", "") or payload.get("message", "")
        except Exception:
            detail = exc.response.text.strip()

        status = exc.response.status_code
        message = f"error: {action} failed ({status})"
        if detail:
            message += f": {detail}"
        else:
            message += f": {exc.response.reason_phrase}"
        typer.echo(message, err=True)
        raise typer.Exit(1)
    except httpx.RequestError as exc:
        typer.echo(f"error: {action} failed: {exc}", err=True)
        raise typer.Exit(1)


def _batch_download_objects(rc, store, obj_type: str, hashes: list[str]) -> int:
    """Download objects in batches of 200. Returns count downloaded.

    Falls back to individual downloads if the remote client does not
    support batch-download (e.g. old server or mocked client).
    """
    BATCH = 200
    downloaded = 0
    for i in range(0, len(hashes), BATCH):
        chunk = hashes[i : i + BATCH]
        use_batch = hasattr(rc, "download_batch") and callable(getattr(rc, "download_batch", None))
        batch_ok = False
        if use_batch:
            try:
                with remote_error_boundary(f"batch-download {obj_type}"):
                    results = rc.download_batch(obj_type, chunk)
                if isinstance(results, dict):
                    for h, data in results.items():
                        store.write(obj_type, data)
                        downloaded += 1
                    batch_ok = True
            except (AttributeError, TypeError):
                pass
        if not batch_ok:
            for h in chunk:
                with remote_error_boundary(f"download {obj_type}"):
                    data = rc.download_object(obj_type, h)
                if data:
                    store.write(obj_type, data)
                    downloaded += 1
    return downloaded


def find_repo_root() -> Path:
    cwd = Path.cwd()
    p = cwd
    while True:
        if (p / ".dit").is_dir():
            return p
        if p.parent == p:
            break
        p = p.parent
    typer.echo("fatal: not a dit repository", err=True)
    raise typer.Exit(1)


def get_dot(repo_root: Path) -> Path:
    return repo_root / ".dit"


def resolve_commit_hash(dot: Path, commit_hash: str) -> str | None:
    """Resolve a full or abbreviated commit hash to a unique stored commit."""
    commits_dir = dot / "objects" / "commits"
    if not commits_dir.exists():
        return None

    if len(commit_hash) == 64:
        return commit_hash if ObjectStore(dot / "objects").exists("commits", commit_hash) else None

    matches = sorted(
        entry.name
        for entry in commits_dir.glob("*/*/*")
        if entry.is_file() and entry.name.startswith(commit_hash)
    )
    if len(matches) == 1:
        return matches[0]
    return None


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
    dot = cwd / ".dit"
    if dot.exists():
        typer.echo(f"Already initialized dit repository in {cwd}")
        return
    dot.mkdir()
    (dot / "objects").mkdir()
    RefStore(dot).init()
    typer.echo(f"Initialized empty dit repository in {cwd}")


@app.command()
def add(paths: list[str] = typer.Argument(..., help="Files or directories to stage")):
    """Stage JSONL and other files for the next commit."""
    from dit.core.workspace import find_all_files, build_blob_for_file
    from dit.core.objects import serialize_blob
    from dit.core.sparse import load_sparse_paths, is_in_sparse_set

    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    index = StagingIndex(dot / "index")
    refs = RefStore(dot)

    sparse_paths = load_sparse_paths(dot)

    tracked_paths: set[str] = set()
    head_hash = refs.resolve_head()
    if head_hash:
        from dit.core.tree_walker import flatten_tree
        head_commit = deserialize_commit(store.read("commits", head_hash))
        tracked_paths = set(flatten_tree(store, head_commit.tree_hash).keys())

    for path_str in paths:
        target = Path(path_str).resolve()
        if path_str == ".":
            jsonl_files, blob_files = find_all_files(repo_root)
        elif target.is_dir():
            jsonl_files, blob_files = find_all_files(target)
        elif target.is_file() and target.suffix == ".jsonl":
            jsonl_files, blob_files = [target], []
        elif target.is_file():
            jsonl_files, blob_files = [], [target]
        else:
            rel_path = path_str if not Path(path_str).is_absolute() else None
            if rel_path is not None:
                rel_path = str(Path(rel_path))
            if rel_path and rel_path in tracked_paths:
                if sparse_paths is not None and not is_in_sparse_set(rel_path, sparse_paths):
                    typer.echo(
                        f"error: '{rel_path}' is not checked out.\n"
                        f"  Use 'dit sparse-checkout add {rel_path}' to fetch it first.",
                        err=True,
                    )
                    raise typer.Exit(1)
                index.stage_delete(rel_path)
                typer.echo(f"  staged deletion {rel_path}")
                continue
            typer.echo(f"fatal: pathspec '{path_str}' did not match any files", err=True)
            raise typer.Exit(1)

        stat_cache = StatCache(dot / "stat-cache")
        for fp in jsonl_files:
            try:
                rel = str(fp.relative_to(repo_root))
            except ValueError:
                typer.echo(f"fatal: '{fp}' is outside the repository", err=True)
                raise typer.Exit(1)
            try:
                manifest = build_manifest_for_file_streaming(fp, store)
            except ValueError as exc:
                typer.echo(f"error: {exc}", err=True)
                raise typer.Exit(1)
            manifest_bytes = serialize_manifest(manifest)
            manifest_hash = store.write("manifests", manifest_bytes)
            index.stage(rel, manifest_hash, obj_type="manifest")
            stat_cache.update(rel, fp, manifest_hash)
            typer.echo(f"  staged {rel} ({len(manifest.entries)} rows)")

        for fp in blob_files:
            try:
                rel = str(fp.relative_to(repo_root))
            except ValueError:
                typer.echo(f"fatal: '{fp}' is outside the repository", err=True)
                raise typer.Exit(1)
            content = build_blob_for_file(fp)
            blob_bytes = serialize_blob(content)
            blob_hash = store.write("blobs", blob_bytes)
            index.stage(rel, blob_hash, obj_type="blob")
            typer.echo(f"  staged {rel} (blob)")


@app.command()
def diff():
    """Show changes between working directory and HEAD."""
    from dit.core.sparse import load_sparse_paths, is_in_sparse_set

    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)

    sparse_paths = load_sparse_paths(dot)

    stat_cache = StatCache(dot / "stat-cache")
    current_files: dict[str, Manifest] = {}
    for fp in find_jsonl_files(repo_root):
        rel = str(fp.relative_to(repo_root))
        manifest, _ = build_manifest_for_file(fp)
        current_hash = object_hash(serialize_manifest(manifest))
        stat_cache.update(rel, fp, current_hash)
        current_files[rel] = manifest

    head_files: dict[str, Manifest] = {}
    head_hash = refs.resolve_head()
    if head_hash:
        from dit.core.tree_walker import flatten_tree
        commit_data = store.read("commits", head_hash)
        head_commit = deserialize_commit(commit_data)
        flat = flatten_tree(store, head_commit.tree_hash)
        for path, (obj_type, obj_hash, _sidecar) in flat.items():
            if obj_type == "manifest":
                if sparse_paths is not None and not is_in_sparse_set(path, sparse_paths):
                    continue
                m_data = store.read("manifests", obj_hash)
                if m_data:
                    head_files[path] = deserialize_manifest(m_data)

    all_files = sorted(set(list(current_files.keys()) + list(head_files.keys())))
    any_changes = False

    for rel in all_files:
        old_m = head_files.get(rel, Manifest(entries=[]))
        new_m = current_files.get(rel, Manifest(entries=[]))
        result = diff_manifests(old_m, new_m)

        if not result.added and not result.removed and not result.refreshed:
            continue

        any_changes = True
        old_count = len(old_m.entries)
        new_count = len(new_m.entries)

        if rel not in head_files:
            typer.echo(f"{rel}: {added_str('new file')} ({new_count} rows)")
        elif rel not in current_files:
            typer.echo(f"{rel}: {removed_str('deleted')} ({old_count} rows)")
        else:
            typer.echo(f"{rel}: {old_count} → {new_count} rows ({result.summary()})")

        if result.refreshed:
            typer.echo(f"  {refreshed_str(f'Likely refreshed: {len(result.refreshed)} rows')}")

    if not any_changes:
        typer.echo(dim_str("No changes."))


@app.command()
def commit(message: str = typer.Option(..., "-m", help="Commit message")):
    """Create a commit from staged files."""
    from dit.core.tree_builder import build_nested_tree

    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    index = StagingIndex(dot / "index")
    refs = RefStore(dot)

    staged_typed = index.entries_typed()
    if not staged_typed:
        typer.echo("nothing to commit (staging area is empty)", err=True)
        raise typer.Exit(1)

    head_commit_hash = refs.resolve_head()
    existing_entries: dict[str, tuple[str, str]] = {}
    if head_commit_hash:
        from dit.core.tree_walker import flatten_tree
        commit_data = store.read("commits", head_commit_hash)
        old_commit = deserialize_commit(commit_data)
        existing_entries = flatten_tree(store, old_commit.tree_hash)

    merged = dict(existing_entries)
    for rel_path, (obj_type, obj_hash) in staged_typed.items():
        if obj_type == "delete":
            merged.pop(rel_path, None)
        else:
            merged[rel_path] = (obj_type, obj_hash)

    tree_hash = build_nested_tree(store, merged)

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
    typer.echo(f"[{branch_str(branch)} {hash_str(commit_hash[:8])}] {message}")


@app.command()
def log(
    oneline: bool = typer.Option(False, "--oneline", help="Show each commit on a single line."),
    format: str = typer.Option("table", "--format", help="Output format: table or json."),
):
    """Show commit history."""
    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)

    commit_hash = refs.resolve_head()
    if not commit_hash:
        typer.echo("No commits yet.")
        return

    if format not in {"table", "json"}:
        typer.echo("fatal: unsupported log format (expected 'table' or 'json')", err=True)
        raise typer.Exit(1)

    if format == "json":
        commits = []
        while commit_hash:
            data = store.read("commits", commit_hash)
            c = deserialize_commit(data)
            commits.append(
                {
                    "hash": commit_hash,
                    "commit_hash": commit_hash,
                    "author": c.author,
                    "message": c.message,
                    "timestamp": c.timestamp,
                    "parent_hashes": c.parent_hashes,
                }
            )
            commit_hash = c.parent_hashes[0] if c.parent_hashes else None
        typer.echo(json.dumps(commits, indent=2))
        return

    while commit_hash:
        data = store.read("commits", commit_hash)
        c = deserialize_commit(data)
        if oneline:
            typer.echo(f"{hash_str(commit_hash[:8])} {c.message}")
            commit_hash = c.parent_hashes[0] if c.parent_hashes else None
            continue
        ts = datetime.fromtimestamp(c.timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        typer.echo(f"commit {hash_str(commit_hash)}")
        typer.echo(f"{header_str('Author:')} {c.author}")
        typer.echo(f"Date:   {ts}")
        typer.echo(f"\n    {c.message}\n")
        commit_hash = c.parent_hashes[0] if c.parent_hashes else None


@app.command()
def status():
    """Show working directory status."""
    from dit.core.sparse import load_sparse_paths, is_in_sparse_set

    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    index = StagingIndex(dot / "index")
    refs = RefStore(dot)

    branch = refs.current_branch() or "HEAD"
    sparse_paths = load_sparse_paths(dot)
    if sparse_paths is not None:
        total_files = 0
        head_hash_tmp = refs.resolve_head()
        if head_hash_tmp:
            from dit.core.tree_walker import flatten_tree as ft
            cd = store.read("commits", head_hash_tmp)
            hc = deserialize_commit(cd)
            total_files = len(ft(store, hc.tree_hash))
        typer.echo(f"On branch {branch_str(branch)} (sparse checkout: {len(sparse_paths)}/{total_files} files)")
    else:
        typer.echo(f"On branch {branch_str(branch)}")

    staged = index.entries()
    staged_typed = index.entries_typed()
    if staged:
        typer.echo(header_str("\nStaged files:"))
        for rel, (obj_type, _obj_hash) in sorted(staged_typed.items()):
            if obj_type == "delete":
                typer.echo(f"  {removed_str('deleted:')}  {rel}")
            else:
                typer.echo(f"  {added_str(rel)}")

    head_manifests: dict[str, str] = {}
    head_hash = refs.resolve_head()
    if head_hash:
        from dit.core.tree_walker import flatten_tree
        commit_data = store.read("commits", head_hash)
        head_commit = deserialize_commit(commit_data)
        flat = flatten_tree(store, head_commit.tree_hash)
        head_manifests = {k: h for k, (t, h, _sc) in flat.items() if t == "manifest"}

    current_files = find_jsonl_files(repo_root)
    current_rels = {str(f.relative_to(repo_root)) for f in current_files}
    head_rels = set(head_manifests.keys())

    if sparse_paths is not None:
        head_rels = {r for r in head_rels if is_in_sparse_set(r, sparse_paths)}

    stat_cache = StatCache(dot / "stat-cache")
    modified = []
    new_files = []
    deleted = sorted(rel for rel in (head_rels - current_rels) if staged_typed.get(rel, ("", ""))[0] != "delete")

    for fp in current_files:
        rel = str(fp.relative_to(repo_root))
        if rel not in head_manifests:
            new_files.append(rel)
        else:
            cached_hash = stat_cache.get_manifest_hash(rel, fp)
            if cached_hash is not None:
                if cached_hash != head_manifests[rel]:
                    modified.append(rel)
            else:
                manifest, _ = build_manifest_for_file(fp)
                current_hash = object_hash(serialize_manifest(manifest))
                stat_cache.update(rel, fp, current_hash)
                if current_hash != head_manifests[rel]:
                    modified.append(rel)

    modified = [r for r in modified if r not in staged]
    new_files = [r for r in new_files if r not in staged]

    has_changes = modified or new_files or deleted
    if not staged and not has_changes:
        typer.echo(dim_str("\nNothing to commit, working directory clean."))
        return

    if modified or new_files or deleted:
        typer.echo(header_str("\nUnstaged changes:"))
        for rel in sorted(modified):
            typer.echo(f"  {modified_str('modified:')} {rel}")
        for rel in sorted(new_files):
            typer.echo(f"  {added_str('new file:')} {rel}")
        for rel in deleted:
            typer.echo(f"  {removed_str('deleted:')}  {rel}")


@app.command()
def branch(
    name: Optional[str] = typer.Argument(None, help="Branch name to create"),
    delete: str = typer.Option("", "-d", help="Branch name to delete"),
):
    """List, create, or delete branches."""
    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    refs = RefStore(dot)

    if delete:
        current = refs.current_branch()
        if delete == current:
            typer.echo(f"error: cannot delete current branch '{delete}'", err=True)
            raise typer.Exit(1)
        if not refs.delete_branch(delete):
            typer.echo(f"error: branch '{delete}' not found", err=True)
            raise typer.Exit(1)
        typer.echo(f"Deleted branch '{delete}'.")
        return

    if name is not None:
        if refs.get_branch(name) is not None:
            typer.echo(f"fatal: branch '{name}' already exists", err=True)
            raise typer.Exit(1)
        head_hash = refs.resolve_head()
        if head_hash is None:
            typer.echo("fatal: no commits yet", err=True)
            raise typer.Exit(1)
        refs.set_branch(name, head_hash)
        typer.echo(f"Created branch '{name}' at {head_hash[:8]}.")
        return

    # List branches
    current = refs.current_branch()
    branches = refs.list_branches()
    for bname in sorted(branches.keys()):
        prefix = "* " if bname == current else "  "
        typer.echo(f"{prefix}{bname} {branches[bname][:8]}")


@app.command()
def tag(
    name: Optional[str] = typer.Argument(None, help="Tag name to create"),
    delete: str = typer.Option("", "-d", help="Tag name to delete"),
):
    """List, create, or delete tags."""
    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    refs = RefStore(dot)

    if delete:
        if not refs.delete_tag(delete):
            typer.echo(f"error: tag '{delete}' not found", err=True)
            raise typer.Exit(1)
        typer.echo(f"Deleted tag '{delete}'.")
        return

    if name is not None:
        if refs.get_tag(name) is not None:
            typer.echo(f"fatal: tag '{name}' already exists", err=True)
            raise typer.Exit(1)
        head_hash = refs.resolve_head()
        if head_hash is None:
            typer.echo("fatal: no commits yet", err=True)
            raise typer.Exit(1)
        refs.set_tag(name, head_hash)
        typer.echo(f"Created tag '{name}' at {head_hash[:8]}.")
        return

    # List tags
    tags = refs.list_tags()
    if not tags:
        typer.echo("No tags.")
        return
    for tname in sorted(tags.keys()):
        typer.echo(f"  {tname} {tags[tname][:8]}")


def _has_uncommitted_changes(repo_root: Path, dot: Path, store: ObjectStore, refs: RefStore) -> bool:
    from dit.core.tree_walker import flatten_tree
    from dit.core.sparse import is_sparse, load_sparse_paths, is_in_sparse_set

    head_hash = refs.resolve_head()
    if head_hash is None:
        return len(find_jsonl_files(repo_root)) > 0

    commit_data = store.read("commits", head_hash)
    head_commit = deserialize_commit(commit_data)
    flat = flatten_tree(store, head_commit.tree_hash)
    head_manifests = {
        path: obj_hash
        for path, (obj_type, obj_hash, _sc) in flat.items()
        if obj_type == "manifest"
    }

    sparse_paths = load_sparse_paths(dot) if is_sparse(dot) else None

    current_files = find_jsonl_files(repo_root)
    current_rels = {str(f.relative_to(repo_root)) for f in current_files}
    head_rels = set(head_manifests.keys())

    if sparse_paths is not None:
        head_rels = {r for r in head_rels if is_in_sparse_set(r, sparse_paths)}

    if current_rels != head_rels:
        return True

    stat_cache = StatCache(dot / "stat-cache")
    for fp in current_files:
        rel = str(fp.relative_to(repo_root))
        if rel in head_manifests:
            cached_hash = stat_cache.get_manifest_hash(rel, fp)
            if cached_hash is not None:
                if cached_hash != head_manifests[rel]:
                    return True
            else:
                manifest, _ = build_manifest_for_file(fp)
                current_hash = object_hash(serialize_manifest(manifest))
                stat_cache.update(rel, fp, current_hash)
                if current_hash != head_manifests[rel]:
                    return True

    return False


def _materialize_tree(repo_root: Path, store: ObjectStore, tree_hash: str, old_tree_hash: str | None = None, sparse_paths: set[str] | None = None):
    """Materialize working directory from tree, optimizing by skipping unchanged files."""
    from dit.core.workspace import materialize_file
    from dit.core.tree_walker import flatten_tree
    from dit.core.sparse import is_in_sparse_set

    new_flat = flatten_tree(store, tree_hash)
    new_manifests = {path: obj_hash for path, (obj_type, obj_hash, _sc) in new_flat.items() if obj_type == "manifest"}
    new_blobs = {path: obj_hash for path, (obj_type, obj_hash, _sc) in new_flat.items() if obj_type == "blob"}

    if sparse_paths is not None:
        new_manifests = {p: h for p, h in new_manifests.items() if is_in_sparse_set(p, sparse_paths)}
        new_blobs = {p: h for p, h in new_blobs.items() if is_in_sparse_set(p, sparse_paths)}

    old_manifests: dict[str, str] = {}
    old_blobs: dict[str, str] = {}
    if old_tree_hash:
        old_flat = flatten_tree(store, old_tree_hash)
        old_manifests = {path: obj_hash for path, (obj_type, obj_hash, _sc) in old_flat.items() if obj_type == "manifest"}
        old_blobs = {path: obj_hash for path, (obj_type, obj_hash, _sc) in old_flat.items() if obj_type == "blob"}

    for name, mhash in new_manifests.items():
        if old_manifests.get(name) != mhash:
            m_data = store.read("manifests", mhash)
            manifest = deserialize_manifest(m_data)
            materialize_file(repo_root, name, manifest, store)

    for name, bhash in new_blobs.items():
        if old_blobs.get(name) != bhash:
            blob_data = store.read("blobs", bhash)
            if blob_data is not None:
                dest = repo_root / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(deserialize_blob(blob_data))

    old_all = set(old_manifests) | set(old_blobs)
    new_all = set(new_manifests) | set(new_blobs)
    for name in old_all:
        if name not in new_all:
            file_path = repo_root / name
            if file_path.exists():
                file_path.unlink()
                parent = file_path.parent
                while parent != repo_root and not any(parent.iterdir()):
                    parent.rmdir()
                    parent = parent.parent


@app.command()
def checkout(
    target: str = typer.Argument(..., help="Branch name to checkout"),
    create: bool = typer.Option(False, "-b", help="Create a new branch and switch to it"),
):
    """Switch branches or create a new branch."""
    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)
    index = StagingIndex(dot / "index")

    if create:
        if refs.get_branch(target) is not None:
            typer.echo(f"fatal: branch '{target}' already exists", err=True)
            raise typer.Exit(1)
        head_hash = refs.resolve_head()
        if head_hash is None:
            typer.echo("fatal: no commits yet", err=True)
            raise typer.Exit(1)
        refs.set_branch(target, head_hash)
        refs.head_file.write_text(f"ref:{target}\n")
        typer.echo(f"Switched to new branch '{target}'.")
        return

    target_hash = refs.get_branch(target)
    if target_hash is None:
        typer.echo(f"error: branch '{target}' not found", err=True)
        raise typer.Exit(1)

    if index.entries():
        typer.echo("error: staging area is not empty — please commit or reset first", err=True)
        raise typer.Exit(1)

    if _has_uncommitted_changes(repo_root, dot, store, refs):
        typer.echo("error: working directory has uncommitted changes", err=True)
        raise typer.Exit(1)

    current_hash = refs.resolve_head()
    current_commit = deserialize_commit(store.read("commits", current_hash)) if current_hash else None
    target_commit = deserialize_commit(store.read("commits", target_hash))

    old_tree_hash = current_commit.tree_hash if current_commit else None
    from dit.core.sparse import is_sparse, load_sparse_paths
    sp = load_sparse_paths(dot) if is_sparse(dot) else None
    _materialize_tree(repo_root, store, target_commit.tree_hash, old_tree_hash, sparse_paths=sp)

    refs.head_file.write_text(f"ref:{target}\n")
    typer.echo(f"Switched to branch '{target}'.")


@app.command()
def reset(
    paths: list[str] = typer.Argument(None, help="Files to unstage"),
    hard: bool = typer.Option(False, "--hard", help="Reset working directory to HEAD"),
):
    """Unstage files or reset working directory to HEAD."""
    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    index = StagingIndex(dot / "index")
    refs = RefStore(dot)

    if hard:
        index.clear()
        head_hash = refs.resolve_head()
        if head_hash:
            head_commit = deserialize_commit(store.read("commits", head_hash))
            _materialize_tree(repo_root, store, head_commit.tree_hash)
            # Remove files not in HEAD
            from dit.core.tree_walker import flatten_tree
            flat = flatten_tree(store, head_commit.tree_hash)
            head_paths = set(flat.keys())
            for fp in find_jsonl_files(repo_root):
                rel = str(fp.relative_to(repo_root))
                if rel not in head_paths:
                    fp.unlink()
        else:
            # No commits: remove all JSONL files
            for fp in find_jsonl_files(repo_root):
                fp.unlink()
        typer.echo("HEAD is now at " + (refs.resolve_head() or "(empty)")[:8])
        return

    # Soft reset
    if paths:
        for p in paths:
            index.unstage(p)
            typer.echo(f"  unstaged {p}")
    else:
        index.clear()
        typer.echo("Staging area cleared.")


@app.command()
def switch(
    target: str = typer.Argument(..., help="Branch name to switch to"),
):
    """Switch to an existing branch."""
    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)
    index = StagingIndex(dot / "index")

    target_hash = refs.get_branch(target)
    if target_hash is None:
        typer.echo(f"error: branch '{target}' not found", err=True)
        raise typer.Exit(1)

    if index.entries():
        typer.echo("error: staging area is not empty — please commit or reset first", err=True)
        raise typer.Exit(1)

    if _has_uncommitted_changes(repo_root, dot, store, refs):
        typer.echo("error: working directory has uncommitted changes", err=True)
        raise typer.Exit(1)

    current_hash = refs.resolve_head()
    current_commit = deserialize_commit(store.read("commits", current_hash)) if current_hash else None
    target_commit = deserialize_commit(store.read("commits", target_hash))

    old_tree_hash = current_commit.tree_hash if current_commit else None
    from dit.core.sparse import is_sparse, load_sparse_paths
    sp = load_sparse_paths(dot) if is_sparse(dot) else None
    _materialize_tree(repo_root, store, target_commit.tree_hash, old_tree_hash, sparse_paths=sp)

    refs.head_file.write_text(f"ref:{target}\n")
    typer.echo(f"Switched to branch '{target}'.")


@app.command()
def merge(
    source: str = typer.Argument("", help="Branch to merge into current branch"),
    continue_merge: bool = typer.Option(False, "--continue", help="Continue after resolving conflicts"),
    abort: bool = typer.Option(False, "--abort", help="Abort current merge"),
    message: str = typer.Option("", "-m", help="Merge commit message"),
):
    """Merge a branch into the current branch."""
    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)
    index = StagingIndex(dot / "index")

    merge_head_file = dot / "MERGE_HEAD"
    merge_msg_file = dot / "MERGE_MSG"
    conflicts_file = dot / "conflicts.json"

    if abort:
        if not merge_head_file.exists():
            typer.echo("error: no merge in progress", err=True)
            raise typer.Exit(1)
        conflicts_data = json.loads(conflicts_file.read_text()) if conflicts_file.exists() else {}
        ours_hash = conflicts_data.get("ours_commit") or refs.resolve_head()
        if ours_hash:
            ours_commit = deserialize_commit(store.read("commits", ours_hash))
            _materialize_tree(repo_root, store, ours_commit.tree_hash)
        merge_head_file.unlink(missing_ok=True)
        merge_msg_file.unlink(missing_ok=True)
        conflicts_file.unlink(missing_ok=True)
        index.clear()
        typer.echo("Merge aborted.")
        return

    if continue_merge:
        if not merge_head_file.exists():
            typer.echo("error: no merge in progress", err=True)
            raise typer.Exit(1)
        staged = index.entries()
        if not staged:
            typer.echo("error: nothing staged — resolve conflicts and dit add first", err=True)
            raise typer.Exit(1)
        theirs_hash = merge_head_file.read_text().strip()
        ours_hash = refs.resolve_head()
        merge_msg = message or (merge_msg_file.read_text().strip() if merge_msg_file.exists() else "merge commit")
        head_commit_hash = refs.resolve_head()
        existing_tree_entries: dict[str, TreeEntry] = {}
        if head_commit_hash:
            commit_data = store.read("commits", head_commit_hash)
            old_commit = deserialize_commit(commit_data)
            tree_data = store.read("trees", old_commit.tree_hash)
            old_tree = deserialize_tree(tree_data)
            for e in old_tree.entries:
                existing_tree_entries[e.name] = e
        for rel_path, manifest_hash in staged.items():
            existing_tree_entries[rel_path] = TreeEntry(
                name=rel_path, obj_type="manifest", obj_hash=manifest_hash
            )
        tree = Tree(entries=list(existing_tree_entries.values()))
        tree_bytes = serialize_tree(tree)
        tree_hash = store.write("trees", tree_bytes)
        c = Commit(
            tree_hash=tree_hash,
            parent_hashes=[ours_hash, theirs_hash],
            author=_get_author(),
            message=merge_msg,
            timestamp=int(time.time()),
        )
        commit_bytes = serialize_commit(c)
        commit_hash = store.write("commits", commit_bytes)
        branch_name = refs.current_branch()
        refs.set_branch(branch_name, commit_hash)
        index.clear()
        merge_head_file.unlink(missing_ok=True)
        merge_msg_file.unlink(missing_ok=True)
        conflicts_file.unlink(missing_ok=True)
        typer.echo(f"[{branch_name} {commit_hash[:8]}] {merge_msg}")
        return

    # Normal merge
    if not source:
        typer.echo("error: specify a branch to merge", err=True)
        raise typer.Exit(1)

    current_branch = refs.current_branch()
    if source == current_branch:
        typer.echo("error: cannot merge a branch into itself", err=True)
        raise typer.Exit(1)

    theirs_hash = refs.get_branch(source)
    if theirs_hash is None:
        typer.echo(f"error: branch '{source}' not found", err=True)
        raise typer.Exit(1)

    if merge_head_file.exists():
        typer.echo("error: merge already in progress (use --continue or --abort)", err=True)
        raise typer.Exit(1)

    staged = index.entries()
    if staged:
        typer.echo("error: staging area is not empty — please commit first", err=True)
        raise typer.Exit(1)

    ours_hash = refs.resolve_head()
    if ours_hash is None:
        typer.echo("fatal: no commits on current branch", err=True)
        raise typer.Exit(1)

    from dit.core.merge_base import find_merge_base
    base_hash = find_merge_base(store, ours_hash, theirs_hash)

    # Already up to date (same tip)
    if ours_hash == theirs_hash:
        typer.echo(dim_str("Already up to date."))
        return

    # Fast-forward
    if base_hash == ours_hash:
        theirs_commit = deserialize_commit(store.read("commits", theirs_hash))
        ours_commit = deserialize_commit(store.read("commits", ours_hash))
        _materialize_tree(repo_root, store, theirs_commit.tree_hash, ours_commit.tree_hash)
        refs.set_branch(current_branch, theirs_hash)
        typer.echo(f"Fast-forward to {hash_str(theirs_hash[:8])}.")
        return

    # Already up to date
    if base_hash == theirs_hash:
        typer.echo(dim_str("Already up to date."))
        return

    # Three-way merge
    from dit.core.merge import three_way_merge
    merge_result = three_way_merge(store, base_hash, ours_hash, theirs_hash)

    if merge_result.conflicts:
        ours_commit = deserialize_commit(store.read("commits", ours_hash))
        conflict_paths = {c.file_path for c in merge_result.conflicts}
        for path, mhash in merge_result.merged_tree_entries.items():
            if path in conflict_paths:
                continue
            m_data = store.read("manifests", mhash)
            manifest = deserialize_manifest(m_data)
            from dit.core.workspace import materialize_file
            materialize_file(repo_root, path, manifest, store)
        merge_head_file.write_text(theirs_hash + "\n")
        merge_msg = message or f"Merge branch '{source}' into {current_branch}"
        merge_msg_file.write_text(merge_msg + "\n")
        conflict_data = {
            "base_commit": base_hash,
            "ours_commit": ours_hash,
            "theirs_commit": theirs_hash,
            "conflicts": [
                {"file_path": c.file_path, "conflict_type": c.conflict_type}
                for c in merge_result.conflicts
            ],
        }
        conflicts_file.write_text(json.dumps(conflict_data, indent=2))
        typer.echo(f"{error_str('CONFLICT:')} {len(merge_result.conflicts)} file(s) have conflicts.")
        for c in merge_result.conflicts:
            typer.echo(f"  {removed_str(c.file_path)} ({c.conflict_type})")
        typer.echo("\nResolve conflicts, then: dit add <files> && dit merge --continue")
        raise typer.Exit(1)

    # No conflicts — create merge commit
    from dit.core.tree_walker import flatten_tree
    ours_commit_obj = deserialize_commit(store.read("commits", ours_hash))
    theirs_commit_obj = deserialize_commit(store.read("commits", theirs_hash))
    target_flat = flatten_tree(store, ours_commit_obj.tree_hash)
    source_flat = flatten_tree(store, theirs_commit_obj.tree_hash)
    sidecar_lookup: dict[str, str | None] = {}
    for path, (_t, _h, sc) in source_flat.items():
        if sc is not None:
            sidecar_lookup[path] = sc
    for path, (_t, _h, sc) in target_flat.items():
        if sc is not None:
            sidecar_lookup[path] = sc

    from dit.core.tree_builder import build_nested_tree
    staged_for_tree: dict[str, tuple[str, str, str | None]] = {
        name: ("manifest", mhash, sidecar_lookup.get(name))
        for name, mhash in merge_result.merged_tree_entries.items()
    }
    tree_hash = build_nested_tree(store, staged_for_tree)

    merge_msg = message or f"Merge branch '{source}' into {current_branch}"
    c = Commit(
        tree_hash=tree_hash,
        parent_hashes=[ours_hash, theirs_hash],
        author=_get_author(),
        message=merge_msg,
        timestamp=int(time.time()),
    )
    commit_bytes = serialize_commit(c)
    commit_hash = store.write("commits", commit_bytes)

    ours_commit = deserialize_commit(store.read("commits", ours_hash))
    _materialize_tree(repo_root, store, tree_hash, ours_commit.tree_hash)
    refs.set_branch(current_branch, commit_hash)
    typer.echo(f"Merge made: [{current_branch} {commit_hash[:8]}] {merge_msg}")


@app.command("cherry-pick")
def cherry_pick(
    commit_hash: str = typer.Argument("", help="Commit hash to cherry-pick"),
    continue_pick: bool = typer.Option(False, "--continue", help="Continue after resolving conflicts"),
    abort: bool = typer.Option(False, "--abort", help="Abort current cherry-pick"),
    message: str = typer.Option("", "-m", help="Override commit message"),
):
    """Apply a single commit to the current branch."""
    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)
    index = StagingIndex(dot / "index")

    cherry_pick_head_file = dot / "CHERRY_PICK_HEAD"
    merge_head_file = dot / "MERGE_HEAD"
    merge_msg_file = dot / "MERGE_MSG"
    conflicts_file = dot / "conflicts.json"

    if abort:
        if not cherry_pick_head_file.exists():
            typer.echo("error: no cherry-pick in progress", err=True)
            raise typer.Exit(1)
        conflicts_data = json.loads(conflicts_file.read_text()) if conflicts_file.exists() else {}
        ours_hash = conflicts_data.get("ours_commit") or refs.resolve_head()
        if ours_hash:
            ours_commit = deserialize_commit(store.read("commits", ours_hash))
            _materialize_tree(repo_root, store, ours_commit.tree_hash)
        cherry_pick_head_file.unlink(missing_ok=True)
        merge_msg_file.unlink(missing_ok=True)
        conflicts_file.unlink(missing_ok=True)
        index.clear()
        typer.echo("Cherry-pick aborted.")
        return

    if continue_pick:
        if not cherry_pick_head_file.exists():
            typer.echo("error: no cherry-pick in progress", err=True)
            raise typer.Exit(1)
        staged = index.entries()
        if not staged:
            typer.echo("error: nothing staged — resolve conflicts and dit add first", err=True)
            raise typer.Exit(1)
        pick_msg = message or (merge_msg_file.read_text().strip() if merge_msg_file.exists() else "cherry-pick commit")
        head_commit_hash = refs.resolve_head()
        existing_tree_entries: dict[str, TreeEntry] = {}
        if head_commit_hash:
            commit_data = store.read("commits", head_commit_hash)
            old_commit = deserialize_commit(commit_data)
            tree_data = store.read("trees", old_commit.tree_hash)
            old_tree = deserialize_tree(tree_data)
            for e in old_tree.entries:
                existing_tree_entries[e.name] = e
        for rel_path, manifest_hash in staged.items():
            existing_tree_entries[rel_path] = TreeEntry(
                name=rel_path, obj_type="manifest", obj_hash=manifest_hash
            )
        tree = Tree(entries=list(existing_tree_entries.values()))
        tree_bytes = serialize_tree(tree)
        tree_hash = store.write("trees", tree_bytes)
        c = Commit(
            tree_hash=tree_hash,
            parent_hashes=[head_commit_hash],
            author=_get_author(),
            message=pick_msg,
            timestamp=int(time.time()),
        )
        commit_bytes = serialize_commit(c)
        new_hash = store.write("commits", commit_bytes)
        branch_name = refs.current_branch()
        refs.set_branch(branch_name, new_hash)
        index.clear()
        cherry_pick_head_file.unlink(missing_ok=True)
        merge_msg_file.unlink(missing_ok=True)
        conflicts_file.unlink(missing_ok=True)
        typer.echo(f"[{branch_name} {new_hash[:8]}] {pick_msg}")
        return

    # Normal cherry-pick
    if not commit_hash:
        typer.echo("error: specify a commit hash to cherry-pick", err=True)
        raise typer.Exit(1)

    if merge_head_file.exists():
        typer.echo("error: merge in progress — finish or abort it first", err=True)
        raise typer.Exit(1)

    if cherry_pick_head_file.exists():
        typer.echo("error: cherry-pick already in progress (use --continue or --abort)", err=True)
        raise typer.Exit(1)

    resolved_commit_hash = resolve_commit_hash(dot, commit_hash)
    target_data = store.read("commits", resolved_commit_hash) if resolved_commit_hash else None
    if target_data is None:
        typer.echo(f"error: commit '{commit_hash[:8]}' not found", err=True)
        raise typer.Exit(1)

    target_commit = deserialize_commit(target_data)
    if not target_commit.parent_hashes:
        typer.echo("error: cannot cherry-pick a root commit", err=True)
        raise typer.Exit(1)

    base_hash = target_commit.parent_hashes[0]
    ours_hash = refs.resolve_head()

    from dit.core.merge import three_way_merge
    merge_result = three_way_merge(store, base_hash, ours_hash, resolved_commit_hash)

    if merge_result.conflicts:
        conflict_paths = {c.file_path for c in merge_result.conflicts}
        for path, mhash in merge_result.merged_tree_entries.items():
            if path in conflict_paths:
                continue
            m_data = store.read("manifests", mhash)
            manifest = deserialize_manifest(m_data)
            from dit.core.workspace import materialize_file
            materialize_file(repo_root, path, manifest, store)
        cherry_pick_head_file.write_text(resolved_commit_hash + "\n")
        pick_msg = message or f"cherry-pick: {target_commit.message}"
        merge_msg_file.write_text(pick_msg + "\n")
        conflict_data = {
            "base_commit": base_hash,
            "ours_commit": ours_hash,
            "theirs_commit": resolved_commit_hash,
            "conflicts": [
                {"file_path": c.file_path, "conflict_type": c.conflict_type}
                for c in merge_result.conflicts
            ],
        }
        conflicts_file.write_text(json.dumps(conflict_data, indent=2))
        typer.echo(f"{error_str('CONFLICT:')} {len(merge_result.conflicts)} file(s) have conflicts.")
        for c in merge_result.conflicts:
            typer.echo(f"  {removed_str(c.file_path)} ({c.conflict_type})")
        typer.echo("\nResolve conflicts, then: dit add <files> && dit cherry-pick --continue")
        raise typer.Exit(1)

    # No conflicts
    from dit.core.tree_walker import flatten_tree as _flatten_tree
    ours_commit_obj = deserialize_commit(store.read("commits", ours_hash))
    theirs_commit_obj = deserialize_commit(store.read("commits", resolved_commit_hash))
    target_flat = _flatten_tree(store, ours_commit_obj.tree_hash)
    source_flat = _flatten_tree(store, theirs_commit_obj.tree_hash)
    cp_sidecar_lookup: dict[str, str | None] = {}
    for path, (_t, _h, sc) in source_flat.items():
        if sc is not None:
            cp_sidecar_lookup[path] = sc
    for path, (_t, _h, sc) in target_flat.items():
        if sc is not None:
            cp_sidecar_lookup[path] = sc

    merged_tree_entries = [
        TreeEntry(name=name, obj_type="manifest", obj_hash=mhash, sidecar_hash=cp_sidecar_lookup.get(name))
        for name, mhash in merge_result.merged_tree_entries.items()
    ]
    tree = Tree(entries=merged_tree_entries)
    tree_bytes = serialize_tree(tree)
    tree_hash = store.write("trees", tree_bytes)

    pick_msg = message or f"cherry-pick: {target_commit.message}"
    c = Commit(
        tree_hash=tree_hash,
        parent_hashes=[ours_hash],
        author=_get_author(),
        message=pick_msg,
        timestamp=int(time.time()),
    )
    commit_bytes = serialize_commit(c)
    new_hash = store.write("commits", commit_bytes)

    branch_name = refs.current_branch()
    ours_commit = deserialize_commit(store.read("commits", ours_hash))
    _materialize_tree(repo_root, store, tree_hash, ours_commit.tree_hash)
    refs.set_branch(branch_name, new_hash)
    typer.echo(f"[{branch_name} {new_hash[:8]}] {pick_msg}")


@app.command()
def serve(
    host: Optional[str] = typer.Option(None, help="Host to bind"),
    port: Optional[int] = typer.Option(None, help="Port to listen on"),
):
    """Start the Dit HTTP API server."""
    try:
        import uvicorn  # noqa: F401
    except ImportError:
        typer.echo(
            "Server dependencies not installed. Run: uv sync --extra server",
            err=True,
        )
        raise typer.Exit(1)

    from dit.server.app import app as fastapi_app
    from dit.server.config import ServerSettings

    import uvicorn as _uvicorn

    settings = ServerSettings()
    resolved_host = host if host is not None else settings.host
    resolved_port = port if port is not None else settings.port

    _uvicorn.run(fastapi_app, host=resolved_host, port=resolved_port)


remote_app = typer.Typer(name="remote", help="Manage remote repositories.")
app.add_typer(remote_app)


@remote_app.callback(invoke_without_command=True)
def remote_main(ctx: typer.Context):
    """Manage remote repositories."""
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


@remote_app.command("add")
def remote_add(
    name: str = typer.Argument(..., help="Remote name (e.g. origin)"),
    url: str = typer.Argument(..., help="Remote URL"),
    token: str = typer.Option("", help="Auth token for this remote"),
):
    """Add a remote."""
    from dit.core.config import set_remote

    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    set_remote(dot, name, url, token)
    typer.echo(f"Remote '{name}' added: {url}")


@remote_app.command("remove")
def remote_remove(
    name: str = typer.Argument(..., help="Remote name to remove"),
):
    """Remove a remote."""
    from dit.core.config import remove_remote

    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    if not remove_remote(dot, name):
        typer.echo(f"fatal: No remote '{name}' found", err=True)
        raise typer.Exit(1)
    typer.echo(f"Remote '{name}' removed.")


@remote_app.command("list")
def remote_list():
    """List configured remotes."""
    from dit.core.config import load_config

    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    config = load_config(dot)
    remotes = config.get("remote", {})
    if not remotes:
        typer.echo("No remotes configured.")
        return
    for rname, rcfg in remotes.items():
        typer.echo(f"{rname}\t{rcfg.get('url', '')}")


auth_app = typer.Typer(name="auth", help="Manage authentication credentials.")
app.add_typer(auth_app)


@auth_app.callback(invoke_without_command=True)
def auth_main(ctx: typer.Context):
    """Manage authentication credentials."""
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


@auth_app.command("set-token")
def auth_set_token(
    token: str = typer.Argument(..., help="Raw API token to store"),
    remote: str = typer.Option("origin", help="Remote name to associate the token with"),
):
    """Store an API token for a remote in .dit/config."""
    from dit.core.config import get_remote, set_remote

    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    existing = get_remote(dot, remote)
    if existing is None:
        typer.echo(f"fatal: Remote '{remote}' not found. Add it first with: dit remote add", err=True)
        raise typer.Exit(1)
    set_remote(dot, remote, existing["url"], token)
    typer.echo(f"Token stored for remote '{remote}'.")


@auth_app.command("login")
def auth_login(
    url: str = typer.Option(..., help="Forgejo base URL, e.g. http://forgejo:3000"),
    token: str = typer.Option(..., help="Forgejo API token"),
):
    """Store Forgejo credentials in .dit/credentials."""
    import json as _json

    try:
        repo_root = find_repo_root()
        creds_path = get_dot(repo_root) / "credentials"
    except SystemExit:
        home_dot = Path.home() / ".dit"
        home_dot.mkdir(parents=True, exist_ok=True)
        creds_path = home_dot / "credentials"

    existing: dict = {}
    if creds_path.exists():
        try:
            existing = _json.loads(creds_path.read_text())
        except Exception:
            existing = {}

    existing["url"] = url.rstrip("/")
    existing["token"] = token
    creds_path.write_text(_json.dumps(existing, indent=2))
    typer.echo(f"Credentials saved to {creds_path}")
    typer.echo(f"Logged in to {url}")


meta_app = typer.Typer(name="meta", help="Manage sidecar metadata.")
app.add_typer(meta_app)


@meta_app.callback(invoke_without_command=True)
def meta_main(ctx: typer.Context):
    """Manage sidecar metadata."""
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


sparse_app = typer.Typer(name="sparse-checkout", help="Manage sparse checkout configuration.")
app.add_typer(sparse_app)


@sparse_app.callback(invoke_without_command=True)
def sparse_main(ctx: typer.Context):
    """Manage sparse checkout configuration."""
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


@sparse_app.command("add")
def sparse_checkout_add(
    paths: list[str] = typer.Argument(..., help="File or directory paths to fetch"),
):
    """Fetch specific files from remote into the working directory."""
    from dit.core.sparse import is_sparse, load_sparse_paths, save_sparse_paths
    from dit.core.tree_walker import flatten_tree
    from dit.core.workspace import materialize_file

    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)

    if not is_sparse(dot):
        typer.echo("error: not a sparse checkout repository", err=True)
        raise typer.Exit(1)

    head_hash = refs.resolve_head()
    if head_hash is None:
        typer.echo("fatal: no commits yet", err=True)
        raise typer.Exit(1)

    commit_data = store.read("commits", head_hash)
    head_commit = deserialize_commit(commit_data)
    flat = flatten_tree(store, head_commit.tree_hash)

    rc = _build_remote_client(dot, "origin")
    sparse_paths = load_sparse_paths(dot) or set()

    for path_str in paths:
        is_dir = path_str.endswith("/")
        matched = {}
        for fpath, (obj_type, obj_hash, sc_hash) in flat.items():
            if obj_type != "manifest":
                continue
            if is_dir and fpath.startswith(path_str):
                matched[fpath] = obj_hash
            elif fpath == path_str:
                matched[fpath] = obj_hash

        if not matched:
            typer.echo(f"error: '{path_str}' not found in tree", err=True)
            raise typer.Exit(1)

        # Phase 1: batch-download missing manifests
        missing_manifests = [
            mh for mh in matched.values() if not store.exists("manifests", mh)
        ]
        if missing_manifests:
            _batch_download_objects(rc, store, "manifests", missing_manifests)

        # Phase 2: collect all missing row hashes across all manifests
        all_missing_rows: list[str] = []
        seen_rows: set[str] = set()
        manifests_by_fpath: dict[str, object] = {}
        for fpath, manifest_hash in matched.items():
            m_data = store.read("manifests", manifest_hash)
            if m_data is None:
                typer.echo(f"error: manifest for '{fpath}' not available", err=True)
                raise typer.Exit(1)
            manifest = deserialize_manifest(m_data)
            manifests_by_fpath[fpath] = manifest
            for entry in manifest.entries:
                if entry.row_hash not in seen_rows and not store.exists("rows", entry.row_hash):
                    all_missing_rows.append(entry.row_hash)
                    seen_rows.add(entry.row_hash)

        # Phase 3: batch-download all missing rows
        if all_missing_rows:
            _batch_download_objects(rc, store, "rows", all_missing_rows)

        # Phase 4: materialize files
        for fpath in matched:
            manifest = manifests_by_fpath[fpath]
            materialize_file(repo_root, fpath, manifest, store)
            typer.echo(f"  fetched {fpath} ({len(manifest.entries)} rows)")

        sparse_paths.add(path_str)

    save_sparse_paths(dot, sparse_paths)


@sparse_app.command("remove")
def sparse_checkout_remove(
    paths: list[str] = typer.Argument(..., help="Paths to remove from sparse set"),
):
    """Remove files from sparse checkout (deletes working copy, keeps objects)."""
    from dit.core.sparse import is_sparse, load_sparse_paths, save_sparse_paths
    from dit.core.tree_walker import flatten_tree

    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)

    if not is_sparse(dot):
        typer.echo("error: not a sparse checkout repository", err=True)
        raise typer.Exit(1)

    sparse_paths = load_sparse_paths(dot) or set()

    head_hash = refs.resolve_head()
    head_commit = deserialize_commit(store.read("commits", head_hash))
    flat = flatten_tree(store, head_commit.tree_hash)

    for path_str in paths:
        sparse_paths.discard(path_str)
        for fpath in flat:
            if fpath == path_str or (path_str.endswith("/") and fpath.startswith(path_str)):
                fp = repo_root / fpath
                if fp.exists():
                    fp.unlink()
                    typer.echo(f"  removed {fpath}")

    save_sparse_paths(dot, sparse_paths)


@sparse_app.command("list")
def sparse_checkout_list():
    """List all files in tree with fetch status."""
    from dit.core.sparse import is_sparse, load_sparse_paths, is_in_sparse_set
    from dit.core.tree_walker import flatten_tree

    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)

    if not is_sparse(dot):
        typer.echo("error: not a sparse checkout repository", err=True)
        raise typer.Exit(1)

    sparse_paths = load_sparse_paths(dot) or set()

    head_hash = refs.resolve_head()
    if head_hash is None:
        typer.echo("No commits yet.")
        return

    commit_data = store.read("commits", head_hash)
    head_commit = deserialize_commit(commit_data)
    flat = flatten_tree(store, head_commit.tree_hash)

    fetched = sum(1 for f in flat if is_in_sparse_set(f, sparse_paths))
    typer.echo(f"Files in tree ({len(flat)} total, {fetched} fetched):")
    for fpath in sorted(flat):
        marker = "[x]" if is_in_sparse_set(fpath, sparse_paths) else "[ ]"
        typer.echo(f"  {marker} {fpath}")


@sparse_app.command("disable")
def sparse_checkout_disable():
    """Convert sparse checkout to full checkout by fetching all missing files."""
    from dit.core.sparse import is_sparse
    from dit.core.tree_walker import flatten_tree
    from dit.core.workspace import materialize_file

    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)

    if not is_sparse(dot):
        typer.echo("error: not a sparse checkout repository", err=True)
        raise typer.Exit(1)

    head_hash = refs.resolve_head()
    if head_hash is None:
        typer.echo("fatal: no commits yet", err=True)
        raise typer.Exit(1)

    commit_data = store.read("commits", head_hash)
    head_commit = deserialize_commit(commit_data)
    flat = flatten_tree(store, head_commit.tree_hash)

    rc = _build_remote_client(dot, "origin")

    for fpath, (obj_type, obj_hash, _sc) in flat.items():
        if obj_type != "manifest":
            continue
        if not store.exists("manifests", obj_hash):
            with remote_error_boundary("sparse-checkout disable"):
                m_data = rc.download_object("manifests", obj_hash)
            if m_data:
                store.write("manifests", m_data)

        m_data = store.read("manifests", obj_hash)
        if m_data is None:
            continue
        manifest = deserialize_manifest(m_data)
        for entry in manifest.entries:
            if not store.exists("rows", entry.row_hash):
                with remote_error_boundary("sparse-checkout disable"):
                    row_data = rc.download_object("rows", entry.row_hash)
                if row_data:
                    store.write("rows", row_data)
        materialize_file(repo_root, fpath, manifest, store)

    (dot / "sparse-checkout").unlink()
    typer.echo(f"Sparse checkout disabled. {len(flat)} file(s) materialized.")


@meta_app.command("compute")
def meta_compute(
    file: Optional[str] = typer.Option(None, "--file", help="Limit to a specific file path"),
):
    """Compute sidecar metadata for manifests that lack it, create a new commit."""
    from dit.core.tree_builder import build_nested_tree
    from dit.core.tree_walker import flatten_tree
    from dit.core.sidecar import compute_sidecar
    from dit.core.objects import serialize_sidecar

    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)

    head_hash = refs.resolve_head()
    if head_hash is None:
        typer.echo("fatal: no commits in this repository", err=True)
        raise typer.Exit(1)

    commit_data = store.read("commits", head_hash)
    head_commit = deserialize_commit(commit_data)

    flat = flatten_tree(store, head_commit.tree_hash)

    computed_count = 0
    updated: dict[str, tuple[str, str, Optional[str]]] = {}

    for path, (obj_type, obj_hash, sidecar_hash) in flat.items():
        if obj_type != "manifest":
            updated[path] = (obj_type, obj_hash, sidecar_hash)
            continue
        if file is not None and path != file.lstrip("/"):
            updated[path] = (obj_type, obj_hash, sidecar_hash)
            continue
        if sidecar_hash is not None:
            updated[path] = (obj_type, obj_hash, sidecar_hash)
            continue

        sidecar = compute_sidecar(store, obj_hash)
        sidecar_bytes = serialize_sidecar(sidecar)
        new_sidecar_hash = store.write("sidecars", sidecar_bytes)

        row_count = len(sidecar.entries)
        typer.echo(f"Computing metadata for {path} ({row_count} rows)... done (sidecar: {new_sidecar_hash[:8]})")
        updated[path] = (obj_type, obj_hash, new_sidecar_hash)
        computed_count += 1

    if computed_count == 0:
        typer.echo("Nothing to compute (all manifests already have sidecar metadata).")
        raise typer.Exit(0)

    new_tree_hash = build_nested_tree(store, updated)

    parent_hashes = [head_hash]
    new_commit = Commit(
        tree_hash=new_tree_hash,
        parent_hashes=parent_hashes,
        author=_get_author(),
        message="meta: compute sidecar metadata",
        timestamp=int(time.time()),
    )
    commit_bytes = serialize_commit(new_commit)
    new_commit_hash = store.write("commits", commit_bytes)

    branch = refs.current_branch()
    refs.set_branch(branch, new_commit_hash)
    typer.echo(f'Created commit: {new_commit_hash[:8]} "meta: compute sidecar metadata"')


@meta_app.command("show")
def meta_show(
    file: str = typer.Argument(..., help="File path (e.g. train.jsonl)"),
    format: str = typer.Option("table", "--format", help="Output format: table or json"),
):
    """Display sidecar metadata stats for a file at HEAD."""
    from dit.core.tree_walker import flatten_tree
    from dit.core.objects import deserialize_sidecar

    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)

    head_hash = refs.resolve_head()
    if head_hash is None:
        typer.echo("fatal: no commits in this repository", err=True)
        raise typer.Exit(1)

    commit_data = store.read("commits", head_hash)
    head_commit = deserialize_commit(commit_data)
    flat = flatten_tree(store, head_commit.tree_hash)

    clean = file.lstrip("/")
    if clean not in flat:
        typer.echo(f"fatal: '{file}' not found in current HEAD tree", err=True)
        raise typer.Exit(1)

    obj_type, obj_hash, sidecar_hash = flat[clean]
    if obj_type != "manifest":
        typer.echo(f"fatal: '{file}' is not a manifest file (type={obj_type})", err=True)
        raise typer.Exit(1)

    if sidecar_hash is None:
        typer.echo(
            f"fatal: no sidecar for '{file}' — run 'dit meta compute' first",
            err=True,
        )
        raise typer.Exit(1)

    sidecar_data = store.read("sidecars", sidecar_hash)
    if sidecar_data is None:
        typer.echo(f"fatal: sidecar object {sidecar_hash[:8]} missing from store", err=True)
        raise typer.Exit(1)

    sidecar = deserialize_sidecar(sidecar_data)

    if format == "json":
        import json as _json
        out = {
            "manifest_hash": sidecar.manifest_hash,
            "entries": [
                {
                    "row_hash": e.row_hash,
                    "char_count": e.char_count,
                    "token_estimate": e.token_estimate,
                    "field_count": e.field_count,
                    "lang": e.lang,
                }
                for e in sidecar.entries
            ],
        }
        typer.echo(_json.dumps(out, indent=2))
        return

    # Table format: aggregate stats
    row_count = len(sidecar.entries)
    if row_count == 0:
        typer.echo(f"File: {file} (0 rows)")
        typer.echo("  No data.")
        return

    total_chars = sum(e.char_count for e in sidecar.entries)
    total_tokens = sum(e.token_estimate for e in sidecar.entries)
    avg_fields = sum(e.field_count for e in sidecar.entries) / row_count

    lang_counts: dict[str, int] = {}
    for e in sidecar.entries:
        lang_key = e.lang or "unknown"
        lang_counts[lang_key] = lang_counts.get(lang_key, 0) + 1
    lang_pcts = {
        lang: f"{count / row_count * 100:.0f}%"
        for lang, count in sorted(lang_counts.items(), key=lambda x: -x[1])
    }
    lang_str = ", ".join(f"{lang} ({pct})" for lang, pct in lang_pcts.items())

    typer.echo(f"File: {file} ({row_count} rows)")
    typer.echo(f"Sidecar: {sidecar_hash[:8]}")
    typer.echo("")
    typer.echo(f"  Total chars:    {total_chars:,}")
    typer.echo(f"  Token estimate: {total_tokens:,}")
    typer.echo(f"  Avg fields/row: {avg_fields:.1f}")
    typer.echo(f"  Languages:      {lang_str}")


@meta_app.command("diff")
def meta_diff(
    commit1: str = typer.Argument(..., help="Old commit hash"),
    commit2: str = typer.Argument(..., help="New commit hash"),
    file: Optional[str] = typer.Option(None, "--file", help="Limit diff to this file"),
):
    """Compare sidecar stats between two commits."""
    from dit.core.tree_walker import flatten_tree
    from dit.core.objects import deserialize_sidecar

    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")

    def _load_sidecars(commit_hash: str) -> dict:
        """Return path -> Sidecar for all manifest entries in the commit."""
        commit_data = store.read("commits", commit_hash)
        if commit_data is None:
            typer.echo(f"fatal: commit {commit_hash[:8]} not found", err=True)
            raise typer.Exit(1)
        commit = deserialize_commit(commit_data)
        flat = flatten_tree(store, commit.tree_hash)
        result = {}
        for path, (obj_type, obj_hash, sidecar_hash) in flat.items():
            if obj_type == "manifest" and sidecar_hash is not None:
                sc_data = store.read("sidecars", sidecar_hash)
                if sc_data is not None:
                    result[path] = deserialize_sidecar(sc_data)
        return result

    old_sidecars = _load_sidecars(commit1)
    new_sidecars = _load_sidecars(commit2)

    all_paths = sorted(set(old_sidecars) | set(new_sidecars))
    if file is not None:
        clean = file.lstrip("/")
        all_paths = [p for p in all_paths if p == clean]

    any_output = False
    for path in all_paths:
        old_sc = old_sidecars.get(path)
        new_sc = new_sidecars.get(path)

        def _summary(sc):
            if sc is None:
                return {"rows": 0, "tokens": 0, "langs": {}}
            rows = len(sc.entries)
            tokens = sum(e.token_estimate for e in sc.entries)
            lc: dict[str, int] = {}
            for e in sc.entries:
                k = e.lang or "unknown"
                lc[k] = lc.get(k, 0) + 1
            lang_pcts = {k: f"{v / rows * 100:.0f}%" for k, v in lc.items()} if rows > 0 else {}
            return {"rows": rows, "tokens": tokens, "langs": lang_pcts}

        os_ = _summary(old_sc)
        ns = _summary(new_sc)

        if os_ == ns:
            continue

        any_output = True
        typer.echo(f"{path}:")

        row_delta = ns["rows"] - os_["rows"]
        sign = "+" if row_delta >= 0 else ""
        typer.echo(f"  Rows:           {os_['rows']} → {ns['rows']} ({sign}{row_delta})")

        tok_delta = ns["tokens"] - os_["tokens"]
        sign = "+" if tok_delta >= 0 else ""
        typer.echo(f"  Token estimate: {os_['tokens']:,} → {ns['tokens']:,} ({sign}{tok_delta:,})")

        if os_["langs"] != ns["langs"]:
            old_lang_str = ", ".join(f"{lang} {pct}" for lang, pct in os_["langs"].items())
            new_lang_str = ", ".join(f"{lang} {pct}" for lang, pct in ns["langs"].items())
            typer.echo(f"  Languages:      {old_lang_str} → {new_lang_str}")

    if not any_output:
        typer.echo("No metadata differences.")


def _build_remote_client(dot: Path, remote_name: str = "origin") -> "RemoteClient":  # noqa: F821
    from dit.core.config import get_remote
    from dit.core.remote import RemoteClient

    cfg = get_remote(dot, remote_name)
    if cfg is None:
        typer.echo(f"fatal: remote '{remote_name}' not configured", err=True)
        raise typer.Exit(1)

    url: str = cfg["url"]
    token: str = cfg.get("token", "")

    base_url, repo_name = _remote_parts_from_url(url)

    return RemoteClient(base_url=base_url, token=token, repo=repo_name)


def _remote_parts_from_url(url: str) -> tuple[str, str]:
    from urllib.parse import urlparse

    clean_url = url.rstrip("/")
    parsed = urlparse(clean_url)
    path_parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(path_parts) >= 2:
        return f"{parsed.scheme}://{parsed.netloc}", "/".join(path_parts)
    if len(path_parts) == 1:
        return f"{parsed.scheme}://{parsed.netloc}", path_parts[0]
    return clean_url.rsplit("/", 1)[0], clean_url.rsplit("/", 1)[-1]


@app.command()
def push(
    remote: str = typer.Option("origin", help="Remote name"),
    branch: str = typer.Option("main", help="Branch name to push"),
):
    """Push local commits to the remote server."""
    from dit.core.walker import walk_commit_objects, walk_commit_objects_since, is_ancestor

    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)

    local_hash = refs.get_branch(branch)
    if local_hash is None:
        typer.echo(f"fatal: branch '{branch}' does not exist locally", err=True)
        raise typer.Exit(1)

    rc = _build_remote_client(dot, remote)

    with remote_error_boundary("push"):
        remote_hash = rc.get_ref("heads", branch)

    if remote_hash is not None:
        if not is_ancestor(store, remote_hash, local_hash):
            typer.echo(
                "error: push rejected — local branch is not a descendant of remote.\n"
                "  Pull first: dit pull",
                err=True,
            )
            raise typer.Exit(1)

    if remote_hash is not None:
        new_objects = walk_commit_objects_since(store, local_hash, stop_at=remote_hash)
    else:
        new_objects = walk_commit_objects(store, local_hash)

    upload_order = ["rows", "manifests", "sidecars", "blobs", "trees", "commits"]
    to_upload: dict[str, list[str]] = {}
    for obj_type in upload_order:
        hashes = list(new_objects.get(obj_type, set()))
        if not hashes:
            to_upload[obj_type] = []
            continue
        with remote_error_boundary("push"):
            exists = rc.batch_exists(obj_type, hashes)
        to_upload[obj_type] = [h for h in hashes if not exists.get(h, False)]

    uploaded = 0
    BATCH_SIZE = 100
    MAX_BATCH_BYTES = 10 * 1024 * 1024  # 10MB

    for obj_type in upload_order:
        hashes = to_upload[obj_type]
        batch: list[tuple[str, bytes]] = []
        batch_bytes = 0
        for hash_hex in hashes:
            data = store.read(obj_type, hash_hex)
            if data is None:
                typer.echo(f"warning: local object {obj_type}/{hash_hex} missing in store", err=True)
                continue
            batch.append((hash_hex, data))
            batch_bytes += len(data)
            if len(batch) >= BATCH_SIZE or batch_bytes >= MAX_BATCH_BYTES:
                with remote_error_boundary("push"):
                    rc.upload_batch(obj_type, batch)
                uploaded += len(batch)
                batch = []
                batch_bytes = 0
        if batch:
            with remote_error_boundary("push"):
                rc.upload_batch(obj_type, batch)
            uploaded += len(batch)

    with remote_error_boundary("push"):
        ok = rc.cas_ref("heads", branch, old=remote_hash, new=local_hash)
    if not ok:
        typer.echo(
            "error: remote ref was updated by another push — pull and retry",
            err=True,
        )
        raise typer.Exit(1)

    typer.echo(f"Pushed {added_str(str(uploaded))} new objects to {branch_str(f'{remote}/{branch}')} ({hash_str(local_hash[:8])})")


def _clone_tree_objects(
    rc,
    store: ObjectStore,
    tree_hash: str,
    manifest_hashes: set,
    sparse: bool = False,
):
    """Recursively download all manifest, sidecar, and subtree objects for a tree hash."""
    from dit.core.objects import deserialize_tree

    # Phase 1: walk tree recursively, collecting manifest & sidecar hashes
    pending_manifests: list[str] = []
    pending_sidecars: list[str] = []
    pending_blobs: list[str] = []

    def _walk_tree(th: str) -> None:
        tree_data = rc.download_object("trees", th)
        if not tree_data:
            return
        store.write("trees", tree_data)
        tree = deserialize_tree(tree_data)

        for entry in tree.entries:
            if entry.obj_type == "manifest":
                if not sparse:
                    pending_manifests.append(entry.obj_hash)
                    manifest_hashes.add(entry.obj_hash)
                if entry.sidecar_hash and not store.exists("sidecars", entry.sidecar_hash):
                    pending_sidecars.append(entry.sidecar_hash)
            elif entry.obj_type == "tree":
                _walk_tree(entry.obj_hash)
            elif entry.obj_type == "blob":
                if not sparse:
                    pending_blobs.append(entry.obj_hash)

    _walk_tree(tree_hash)

    # Phase 2: batch-download manifests
    if pending_manifests:
        _batch_download_objects(rc, store, "manifests", pending_manifests)

    # Phase 3: batch-download sidecars
    if pending_sidecars:
        downloaded_sc = _batch_download_objects(rc, store, "sidecars", pending_sidecars)
        not_found = len(pending_sidecars) - downloaded_sc
        if not_found > 0:
            for sh in pending_sidecars:
                if not store.exists("sidecars", sh):
                    typer.echo(
                        f"  warning: sidecar {sh[:8]} not found on remote (skipped)",
                        err=True,
                    )

    # Phase 4: batch-download blobs
    if pending_blobs:
        _batch_download_objects(rc, store, "blobs", pending_blobs)


@app.command()
def clone(
    url: str = typer.Argument(..., help="Remote URL (http://host:port/repo-name)"),
    dest: str = typer.Argument("", help="Destination directory (default: repo name)"),
    token: str = typer.Option("", help="Auth token"),
    branch: str = typer.Option("main", help="Branch to clone"),
    sparse: bool = typer.Option(False, "--sparse", help="Sparse clone: download metadata only, fetch files on demand"),
):
    """Clone a remote repository into a new directory."""
    from dit.core.config import set_remote
    from dit.core.remote import RemoteClient
    from dit.core.objects import deserialize_commit, deserialize_manifest
    from dit.core.sparse import save_sparse_paths
    from dit.core.tree_walker import flatten_tree

    base_url, repo_name = _remote_parts_from_url(url)

    dest_dir = Path(dest) if dest else Path.cwd() / repo_name
    if dest_dir.exists() and any(dest_dir.iterdir()):
        typer.echo(f"fatal: destination '{dest_dir}' already exists and is not empty", err=True)
        raise typer.Exit(1)
    dest_dir.mkdir(parents=True, exist_ok=True)

    dot = dest_dir / ".dit"
    dot.mkdir()
    (dot / "objects").mkdir()
    refs = RefStore(dot)
    refs.init()
    store = ObjectStore(dot / "objects")

    rc = RemoteClient(base_url=base_url, token=token, repo=repo_name)

    with remote_error_boundary("clone"):
        remote_hash = rc.get_ref("heads", branch)
    if remote_hash is None:
        typer.echo(f"fatal: remote branch '{branch}' not found", err=True)
        raise typer.Exit(1)

    typer.echo(f"Cloning {url} -> {dest_dir}{' (sparse)' if sparse else ''}")

    commits_to_fetch: list[str] = []
    queue = [remote_hash]
    visited: set[str] = set()

    while queue:
        chash = queue.pop()
        if chash in visited:
            continue
        visited.add(chash)
        with remote_error_boundary("clone"):
            data = rc.download_object("commits", chash)
        if data is None:
            typer.echo(f"warning: commit {chash} not found on remote", err=True)
            continue
        store.write("commits", data)
        commits_to_fetch.append(chash)
        commit = deserialize_commit(data)
        queue.extend(commit.parent_hashes)

    manifest_hashes: set[str] = set()
    for chash in commits_to_fetch:
        commit_data = store.read("commits", chash)
        commit = deserialize_commit(commit_data)
        with remote_error_boundary("clone"):
            _clone_tree_objects(rc, store, commit.tree_hash, manifest_hashes, sparse=sparse)

    if not sparse:
        # Collect all missing row hashes from all manifests, then batch-download
        all_row_hashes: list[str] = []
        seen_rows: set[str] = set()
        for mhash in manifest_hashes:
            m_data = store.read("manifests", mhash)
            if m_data is None:
                continue
            manifest = deserialize_manifest(m_data)
            for entry in manifest.entries:
                if entry.row_hash not in seen_rows and not store.exists("rows", entry.row_hash):
                    all_row_hashes.append(entry.row_hash)
                    seen_rows.add(entry.row_hash)
        if all_row_hashes:
            _batch_download_objects(rc, store, "rows", all_row_hashes)

    refs.set_branch(branch, remote_hash)
    refs.head_file.write_text(f"ref:{branch}\n")
    set_remote(dot, "origin", url, token)

    head_commit_data = store.read("commits", remote_hash)
    head_commit = deserialize_commit(head_commit_data)

    if sparse:
        save_sparse_paths(dot, set())
        flat = flatten_tree(store, head_commit.tree_hash)
        for path in flat:
            parent = (dest_dir / path).parent
            parent.mkdir(parents=True, exist_ok=True)
        typer.echo(f"Sparse clone complete. {len(commits_to_fetch)} commit(s), {len(flat)} file(s) in tree.")
        typer.echo("  Use 'dit sparse-checkout add <path>' to fetch files.")
    else:
        _materialize_tree(dest_dir, store, head_commit.tree_hash)
        typer.echo(f"Clone complete. {len(commits_to_fetch)} commit(s).")


def _fetch_tree_objects(
    rc,
    store: ObjectStore,
    tree_hash: str,
    manifest_hashes: set,
) -> int:
    """Recursively download manifest, sidecar, row, and subtree objects. Returns count downloaded."""
    from dit.core.objects import deserialize_tree, deserialize_manifest

    downloaded = 0

    # Phase 1: walk tree, collecting missing manifest/sidecar hashes
    pending_manifests: list[str] = []
    pending_sidecars: list[str] = []
    pending_blobs: list[str] = []

    def _walk(th: str) -> int:
        nonlocal downloaded
        count = 0
        if store.exists("trees", th):
            tree_data = store.read("trees", th)
            if tree_data is None:
                return 0
            tree = deserialize_tree(tree_data)
        else:
            tree_data = rc.download_object("trees", th)
            if not tree_data:
                return 0
            store.write("trees", tree_data)
            count += 1
            tree = deserialize_tree(tree_data)

        for entry in tree.entries:
            if entry.obj_type == "manifest":
                if not store.exists("manifests", entry.obj_hash):
                    pending_manifests.append(entry.obj_hash)
                    manifest_hashes.add(entry.obj_hash)
                if entry.sidecar_hash and not store.exists("sidecars", entry.sidecar_hash):
                    pending_sidecars.append(entry.sidecar_hash)
            elif entry.obj_type == "tree":
                count += _walk(entry.obj_hash)
            elif entry.obj_type == "blob":
                if not store.exists("blobs", entry.obj_hash):
                    pending_blobs.append(entry.obj_hash)
        return count

    downloaded += _walk(tree_hash)

    # Phase 2: batch-download manifests
    if pending_manifests:
        downloaded += _batch_download_objects(rc, store, "manifests", pending_manifests)

    # Phase 3: collect missing row hashes from newly downloaded manifests
    pending_rows: list[str] = []
    seen_rows: set[str] = set()
    for mhash in pending_manifests:
        m_data = store.read("manifests", mhash)
        if m_data is None:
            continue
        m = deserialize_manifest(m_data)
        for me in m.entries:
            if me.row_hash not in seen_rows and not store.exists("rows", me.row_hash):
                pending_rows.append(me.row_hash)
                seen_rows.add(me.row_hash)

    # Phase 4: batch-download rows
    if pending_rows:
        downloaded += _batch_download_objects(rc, store, "rows", pending_rows)

    # Phase 5: batch-download sidecars
    if pending_sidecars:
        sc_count = _batch_download_objects(rc, store, "sidecars", pending_sidecars)
        downloaded += sc_count
        if sc_count < len(pending_sidecars):
            for sh in pending_sidecars:
                if not store.exists("sidecars", sh):
                    typer.echo(
                        f"  warning: sidecar {sh[:8]} not found on remote (skipped)",
                        err=True,
                    )

    # Phase 6: batch-download blobs
    if pending_blobs:
        downloaded += _batch_download_objects(rc, store, "blobs", pending_blobs)

    return downloaded


def _fetch_objects_since(
    rc: "RemoteClient",  # noqa: F821
    store: ObjectStore,
    remote_hash: str,
    stop_at: str | None,
) -> tuple[int, set[str]]:
    from dit.core.objects import deserialize_commit

    downloaded = 0
    manifest_hashes: set[str] = set()
    queue = [remote_hash]
    visited: set[str] = set()

    while queue:
        chash = queue.pop()
        if chash in visited:
            continue
        if chash == stop_at:
            continue
        visited.add(chash)

        if store.exists("commits", chash):
            continue

        data = rc.download_object("commits", chash)
        if data is None:
            continue
        store.write("commits", data)
        downloaded += 1
        commit = deserialize_commit(data)

        downloaded += _fetch_tree_objects(rc, store, commit.tree_hash, manifest_hashes)

        queue.extend(commit.parent_hashes)

    return downloaded, manifest_hashes


@app.command()
def fetch(
    remote: str = typer.Option("origin", help="Remote name"),
    branch: str = typer.Option("main", help="Branch to fetch"),
):
    """Download new objects from the remote (does not update local branch)."""
    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)

    rc = _build_remote_client(dot, remote)
    with remote_error_boundary("fetch"):
        remote_hash = rc.get_ref("heads", branch)
    if remote_hash is None:
        typer.echo(f"  remote branch '{branch}' not found")
        return

    local_hash = refs.get_branch(branch)
    if local_hash == remote_hash:
        typer.echo("Already up to date.")
        return

    with remote_error_boundary("fetch"):
        count, _ = _fetch_objects_since(rc, store, remote_hash, stop_at=local_hash)
    typer.echo(f"Fetched {count} new objects from {remote}/{branch}")


@app.command()
def pull(
    remote: str = typer.Option("origin", help="Remote name"),
    branch: str = typer.Option("main", help="Branch to pull"),
):
    """Fetch from remote + fast-forward local branch + materialize changed files."""
    from dit.core.objects import deserialize_commit
    from dit.core.walker import is_ancestor

    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)

    if _has_uncommitted_changes(repo_root, dot, store, refs):
        typer.echo("error: You have uncommitted changes. Commit or reset them before pulling.", err=True)
        raise typer.Exit(1)

    rc = _build_remote_client(dot, remote)
    with remote_error_boundary("pull"):
        remote_hash = rc.get_ref("heads", branch)
    if remote_hash is None:
        typer.echo(f"  remote branch '{branch}' not found")
        return

    local_hash = refs.get_branch(branch)
    if local_hash == remote_hash:
        typer.echo("Already up to date.")
        return

    with remote_error_boundary("pull"):
        count, _ = _fetch_objects_since(rc, store, remote_hash, stop_at=local_hash)

    if local_hash is not None and not is_ancestor(store, local_hash, remote_hash):
        typer.echo(
            "error: pull would not be a fast-forward.\n"
            "  Local and remote have diverged. Resolve manually.",
            err=True,
        )
        raise typer.Exit(1)

    refs.set_branch(branch, remote_hash)

    head_commit_data = store.read("commits", remote_hash)
    head_commit = deserialize_commit(head_commit_data)
    old_tree_hash = None
    if local_hash:
        old_commit_data = store.read("commits", local_hash)
        if old_commit_data:
            old_tree_hash = deserialize_commit(old_commit_data).tree_hash
    from dit.core.sparse import is_sparse, load_sparse_paths
    sp = load_sparse_paths(dot) if is_sparse(dot) else None
    _materialize_tree(repo_root, store, head_commit.tree_hash, old_tree_hash, sparse_paths=sp)

    typer.echo(f"Pulled {count} new objects. Now at {remote_hash[:8]}.")


@app.command()
def export(
    ref: str = typer.Option("main", "--ref", help="Branch name or commit hash to export from"),
    file: Optional[str] = typer.Option(None, "--file", help="Export only this file path"),
    format: str = typer.Option("jsonl", "--format", help="Output format: jsonl or csv"),
    include_meta: bool = typer.Option(False, "--include-meta", help="Write .meta.json alongside each file"),
    output: str = typer.Option(".", "--output", help="Local directory to write exported files"),
):
    """Export files from a commit to a local directory."""
    from dit.core.export import export_commit

    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)

    commit_hash = refs.get_branch(ref)
    if commit_hash is None:
        if len(ref) == 64 and all(c in "0123456789abcdef" for c in ref):
            commit_hash = ref
        else:
            typer.echo(f"fatal: ref '{ref}' not found", err=True)
            raise typer.Exit(1)

    output_dir = Path(output)
    output_dir.mkdir(parents=True, exist_ok=True)

    typer.echo(f"Exporting from {ref} (commit {commit_hash[:8]})")

    try:
        report = export_commit(
            store,
            commit_hash,
            output_dir,
            file_filter=file,
            fmt=format,
            include_meta=include_meta,
        )
    except FileNotFoundError as exc:
        typer.echo(f"fatal: {exc}", err=True)
        raise typer.Exit(1)
    except ValueError as exc:
        typer.echo(f"fatal: {exc}", err=True)
        raise typer.Exit(1)

    for entry in report:
        rows = entry["rows"]
        row_word = "row" if rows == 1 else "rows"
        typer.echo(f"  {entry['path']} ({rows} {row_word})... done")
        if include_meta:
            meta_path = entry["path"] + ".meta.json"
            if (output_dir / meta_path).exists():
                typer.echo(f"  {meta_path}... done")

    file_word = "file" if len(report) == 1 else "files"
    typer.echo(f"Exported {len(report)} {file_word} to {output_dir}/")


@app.command()
def stats(
    path: str = typer.Argument("", help="Optional path filter: file name or directory prefix"),
    ref: str = typer.Option("main", "--ref", help="Branch name or commit hash to inspect"),
    compare: tuple[str, str] = typer.Option((None, None), "--compare", help="Compare two refs: --compare REF1 REF2"),
    format: str = typer.Option("table", "--format", help="Output format: table or json"),
):
    """Show repo-level stats aggregated from sidecar metadata."""
    import json as _json
    from dit.core.stats import repo_stats, compare_stats

    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)

    comparing = compare[0] is not None and compare[1] is not None

    if comparing:
        commit1, commit2 = compare
        resolved1 = resolve_commit_hash(dot, commit1) or refs.get_branch(commit1)
        resolved2 = resolve_commit_hash(dot, commit2) or refs.get_branch(commit2)
        if resolved1 is None:
            typer.echo(f"fatal: cannot resolve '{commit1}'", err=True)
            raise typer.Exit(1)
        if resolved2 is None:
            typer.echo(f"fatal: cannot resolve '{commit2}'", err=True)
            raise typer.Exit(1)
        try:
            result = compare_stats(store, resolved1, resolved2, path_prefix=path or None)
        except FileNotFoundError as exc:
            typer.echo(f"fatal: {exc}", err=True)
            raise typer.Exit(1)

        if format == "json":
            typer.echo(_json.dumps(result, indent=2))
            return

        typer.echo(f"Stats delta: {resolved1[:8]} -> {resolved2[:8]}")
        typer.echo("")
        if not result["files"]:
            typer.echo("No files with sidecars on both sides.")
            return

        col_file = max(len(f["path"]) for f in result["files"])
        col_file = max(col_file, 4)
        header = f"{'File':<{col_file}}  {'Rows (delta)':>20}  {'Tokens (delta)':>18}  {'Chars (delta)':>18}"
        typer.echo(header)
        typer.echo("-" * len(header))

        for f in result["files"]:
            delta = f["delta"]
            row_sign = "+" if delta["row_count"] >= 0 else ""
            tok_sign = "+" if delta["token_estimate"] >= 0 else ""
            char_sign = "+" if delta["char_count"] >= 0 else ""
            rows_str = f"{f['old']['row_count']} -> {f['new']['row_count']} ({row_sign}{delta['row_count']})"
            old_tok = _fmt_tokens(f["old"]["token_estimate"])
            new_tok = _fmt_tokens(f["new"]["token_estimate"])
            delta_tok = _fmt_tokens(abs(delta["token_estimate"]))
            tok_str = f"{old_tok} -> {new_tok} ({tok_sign}{delta_tok})"
            char_str = f"{_fmt_chars(f['old']['char_count'])} -> {_fmt_chars(f['new']['char_count'])} ({char_sign}{_fmt_chars(delta['char_count'])})"
            typer.echo(f"{f['path']:<{col_file}}  {rows_str:>20}  {tok_str:>18}  {char_str:>18}")

        typer.echo("-" * len(header))
        td = result["totals_delta"]
        row_sign = "+" if td["row_count"] >= 0 else ""
        tok_sign = "+" if td["token_estimate"] >= 0 else ""
        char_sign = "+" if td["char_count"] >= 0 else ""
        typer.echo(f"{'TOTAL':<{col_file}}  {row_sign}{td['row_count']:>19}  {tok_sign}{_fmt_tokens(abs(td['token_estimate'])):>17}  {char_sign}{_fmt_chars(td['char_count']):>17}")
        return

    # Single-ref mode
    commit_hash = refs.get_branch(ref)
    if commit_hash is None:
        if len(ref) == 64 and all(c in "0123456789abcdef" for c in ref):
            commit_hash = ref
        else:
            typer.echo(f"fatal: ref '{ref}' not found", err=True)
            raise typer.Exit(1)

    if commit_hash is None:
        typer.echo(f"fatal: no commits on branch '{ref}'", err=True)
        raise typer.Exit(1)

    try:
        result = repo_stats(store, commit_hash, path_prefix=path or None)
    except FileNotFoundError as exc:
        typer.echo(f"fatal: {exc}", err=True)
        raise typer.Exit(1)

    if format == "json":
        typer.echo(_json.dumps(result, indent=2))
        return

    # Table mode
    path_filter_str = f" \u2014 {path}" if path else ""
    typer.echo(f"Repo stats at {ref} (commit {commit_hash[:8]}){path_filter_str}")
    typer.echo("")

    if not result["files"]:
        typer.echo("No manifest files found.")
        return

    col_file = max(len(f["path"]) for f in result["files"])
    col_file = max(col_file, 4)
    header = f"{'File':<{col_file}}  {'Rows':>8}  {'Tokens':>10}  {'Chars':>10}  {'Avg fields':>10}  Lang"
    sep = "\u2500" * len(header)
    typer.echo(header)
    typer.echo(sep)

    for f in result["files"]:
        if f["has_sidecar"]:
            rows_str = f"{f['row_count']:,}"
            tok_str = _fmt_tokens(f["token_estimate"])
            char_str = _fmt_chars(f["char_count"])
            avg_str = f"{f['avg_fields']:.1f}"
            lang_str = _fmt_lang(f["lang_distribution"])
        else:
            rows_str = tok_str = char_str = avg_str = lang_str = "\u2014"
        typer.echo(f"{f['path']:<{col_file}}  {rows_str:>8}  {tok_str:>10}  {char_str:>10}  {avg_str:>10}  {lang_str}")

    typer.echo(sep)
    totals = result["totals"]
    if totals["files_with_sidecar"]:
        tot_rows = f"{totals['row_count']:,}"
        tot_tok = _fmt_tokens(totals["token_estimate"])
        tot_char = _fmt_chars(totals["char_count"])
        tot_lang = _fmt_lang(totals["lang_distribution"])
    else:
        tot_rows = tot_tok = tot_char = tot_lang = "\u2014"
    typer.echo(f"{'TOTAL':<{col_file}}  {tot_rows:>8}  {tot_tok:>10}  {tot_char:>10}  {'':>10}  {tot_lang}")

    missing = totals["file_count"] - totals["files_with_sidecar"]
    if missing > 0:
        typer.echo("")
        typer.echo(f"{missing} of {totals['file_count']} files have no sidecar metadata. Run 'dit meta compute' to fill gaps.")


@app.command()
def search(
    query: str = typer.Argument(..., help="Substring to match (case-insensitive)"),
    path: str = typer.Argument("", help="Optional file name or directory prefix to restrict the scan"),
    ref: str = typer.Option("main", "--ref", help="Branch name or commit hash to search"),
    field: Optional[str] = typer.Option(None, "--field", help="Dot-notation field path to match within"),
    limit: int = typer.Option(50, "--limit", help="Maximum number of matches to return"),
    format: str = typer.Option("table", "--format", help="Output format: table or json"),
):
    """Search for rows matching QUERY in a commit."""
    import json as _json
    from dit.core.search import search_rows

    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)

    commit_hash = refs.get_branch(ref)
    if commit_hash is None:
        if len(ref) == 64 and all(c in "0123456789abcdef" for c in ref):
            commit_hash = ref
        else:
            typer.echo(f"fatal: ref '{ref}' not found", err=True)
            raise typer.Exit(1)

    if commit_hash is None:
        typer.echo(f"fatal: no commits on branch '{ref}'", err=True)
        raise typer.Exit(1)

    try:
        result = search_rows(
            store,
            commit_hash,
            query,
            path_prefix=path or None,
            field_path=field,
            limit=limit,
        )
    except FileNotFoundError as exc:
        typer.echo(f"fatal: {exc}", err=True)
        raise typer.Exit(1)

    if format == "json":
        typer.echo(_json.dumps(result, indent=2))
        return

    # Table format header
    ref_display = f"heads/{ref}" if refs.get_branch(ref) else ref
    if field:
        typer.echo(f'Searching {branch_str(ref_display)} (commit {hash_str(commit_hash[:8])}) for "{query}" in field {field}')
    elif path:
        typer.echo(f'Searching {branch_str(ref_display)} (commit {hash_str(commit_hash[:8])}) for "{query}" in {path}')
    else:
        typer.echo(f'Searching {branch_str(ref_display)} (commit {hash_str(commit_hash[:8])}) for "{query}"')
    typer.echo("")

    matches = result["matches"]

    if not matches:
        typer.echo("0 matches")
        typer.echo(f"(scanned {result['total_scanned']} rows)")
        return

    # Column widths
    col_file = max(len(m["file"]) for m in matches)
    col_file = max(col_file, 4)
    sep = "\u2500" * (col_file + 8 + 50)

    header = f"{'File':<{col_file}}  {'Row':>5}  Excerpt"
    typer.echo(header_str(header))
    typer.echo(dim_str(sep))

    for m in matches:
        excerpt = m["highlight"].replace("\n", " ")
        typer.echo(f"{m['file']:<{col_file}}  {m['row_index']:>5}  {excerpt}")

    typer.echo(dim_str(sep))

    match_word = "match" if len(matches) == 1 else "matches"
    typer.echo(header_str(f"{len(matches)} {match_word} (scanned {result['total_scanned']} rows)"))

    if result["limit_reached"]:
        typer.echo("Limit reached. Pass --limit N to see more.")


@app.command()
def validate(
    ref: str = typer.Option("main", "--ref", help="Branch name or commit hash to validate"),
    format: str = typer.Option("table", "--format", help="Output format: table or json"),
):
    """Validate all JSONL rows in a commit against .ditvalidate.yaml rules."""
    import json as _json
    from dit.core.validate import load_rules, validate_commit

    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)

    # Resolve ref to commit hash
    commit_hash = refs.get_branch(ref)
    if commit_hash is None:
        if len(ref) == 64 and all(c in "0123456789abcdef" for c in ref):
            commit_hash = ref
        else:
            typer.echo(f"fatal: ref '{ref}' not found", err=True)
            raise typer.Exit(1)

    rules = load_rules(repo_root)

    try:
        result = validate_commit(store, commit_hash, rules)
    except FileNotFoundError as exc:
        typer.echo(f"fatal: {exc}", err=True)
        raise typer.Exit(1)

    if format == "json":
        typer.echo(_json.dumps(result, indent=2))
        raise typer.Exit(0 if result["status"] == "pass" else 1)

    # Table format
    ref_display = f"heads/{ref}" if refs.get_branch(ref) else ref
    typer.echo(f"Validating {ref_display} (commit {commit_hash[:8]})")

    rf = rules["required_fields"]
    fk = rules["forbidden_keywords"]
    mx = rules["max_row_chars"]
    rules_parts = []
    if rf:
        rules_parts.append(f"required_fields=[{', '.join(rf)}]")
    if fk:
        rules_parts.append(f"forbidden_keywords={len(fk)}")
    if mx is not None:
        rules_parts.append(f"max_row_chars={mx}")
    if rules["min_row_chars"] is not None:
        rules_parts.append(f"min_row_chars={rules['min_row_chars']}")
    if rules_parts:
        typer.echo("Rules: " + "  ".join(rules_parts))
    typer.echo("")

    violations = result["violations"]
    checked = result["checked_rows"]

    if not violations:
        n_files = _count_result_files(store, commit_hash)
        typer.echo(f"Checked {checked} rows across {n_files} files.")
        typer.echo("PASS")
        raise typer.Exit(0)

    # Count unique files for summary
    n_files = _count_result_files(store, commit_hash)

    typer.echo(f"FAIL \u2014 {len(violations)} violation(s)")
    typer.echo("")

    col_file = max((len(v["file"]) for v in violations), default=4)
    col_file = max(col_file, 4)
    col_rule = max((len(v["rule"]) for v in violations), default=4)
    col_rule = max(col_rule, 4)
    header = f"{'File':<{col_file}}   {'Row':>5}   {'Rule':<{col_rule}}   Detail"
    sep = "\u2500" * max(len(header), 80)
    typer.echo(header)
    typer.echo(sep)
    for v in violations:
        typer.echo(f"{v['file']:<{col_file}}   {v['row_index']:>5}   {v['rule']:<{col_rule}}   {v['detail']}")
    typer.echo(sep)
    typer.echo(f"Checked {checked} rows across {n_files} files.")
    raise typer.Exit(1)


@app.command()
def blame(
    file: str = typer.Argument(..., help="File path (e.g. train.jsonl)"),
    ref: str = typer.Option("main", "--ref", help="Branch name or commit hash"),
    row: Optional[int] = typer.Option(None, "--row", help="Show history for a specific row index"),
    format: str = typer.Option("table", "--format", help="Output format: table or json"),
):
    """Show which commit introduced each row in a file."""
    import json as _json
    from datetime import datetime, timezone
    from dit.core.blame import blame_file as _blame_file, row_history as _row_history

    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)

    commit_hash = refs.get_branch(ref)
    if commit_hash is None:
        if len(ref) == 64 and all(c in "0123456789abcdef" for c in ref):
            commit_hash = ref
        else:
            typer.echo(f"fatal: ref '{ref}' not found", err=True)
            raise typer.Exit(1)

    if commit_hash is None:
        typer.echo(f"fatal: no commits on branch '{ref}'", err=True)
        raise typer.Exit(1)

    try:
        if row is not None:
            result = _row_history(store, commit_hash, file, row)

            if format == "json":
                typer.echo(_json.dumps(result, indent=2))
                return

            ref_display = f"heads/{ref}" if refs.get_branch(ref) else ref
            typer.echo(f"History for {file} row {row} at {branch_str(ref_display)}")
            typer.echo("")

            events = result["events"]
            if not events:
                typer.echo("No events found.")
                return

            col_commit = 9
            col_author = max(len(e["author"]) for e in events)
            col_author = max(col_author, 6)
            header = f"  {'Commit':<{col_commit}}  {'Author':<{col_author}}  {'Date':<21}  Event     Content"
            sep = "\u2500" * max(len(header), 80)
            typer.echo(header_str(header))
            typer.echo(dim_str(sep))
            for e in events:
                ts = datetime.fromtimestamp(e["timestamp"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                preview = e.get("content_preview", "")[:50]
                typer.echo(f"  {hash_str(e['commit_hash'][:7]):<{col_commit + 9}}  {e['author']:<{col_author}}  {ts:<21}  {e['event']:<8}  {preview}")
            typer.echo(dim_str(sep))

            qfp = result.get("query_fingerprint")
            if qfp:
                typer.echo(f"{len(events)} events (query_fingerprint: {qfp[:8]}...{qfp[-4:]})")
            else:
                typer.echo(f"{len(events)} events")

        else:
            result = _blame_file(store, commit_hash, file)

            if format == "json":
                typer.echo(_json.dumps(result, indent=2))
                return

            ref_display = f"heads/{ref}" if refs.get_branch(ref) else ref
            typer.echo(f"Blame for {file} at {branch_str(ref_display)} (commit {hash_str(commit_hash[:8])})")
            typer.echo("")

            entries = result["entries"]
            if not entries:
                typer.echo("No rows.")
                return

            col_author = max(len(e["author"]) for e in entries)
            col_author = max(col_author, 6)
            header = f" {'Row':>4}  {'Commit':<9}  {'Author':<{col_author}}  {'Date':<21}  Content"
            sep = "\u2500" * max(len(header) + 40, 80)
            typer.echo(header_str(header))
            typer.echo(dim_str(sep))
            for e in entries:
                ts = datetime.fromtimestamp(e["timestamp"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                preview = e.get("content_preview", "")[:60]
                typer.echo(f" {e['row_index']:>4}  {hash_str(e['commit_hash'][:7]):<{9 + 9}}  {e['author']:<{col_author}}  {ts:<21}  {preview}")
            typer.echo(dim_str(sep))

            s = result["summary"]
            typer.echo(f"{s['total_rows']} rows, {s['unique_commits']} commits, {s['unique_authors']} authors")

    except FileNotFoundError as exc:
        typer.echo(f"fatal: {exc}", err=True)
        raise typer.Exit(1)
    except IndexError as exc:
        typer.echo(f"fatal: {exc}", err=True)
        raise typer.Exit(1)


@app.command()
def gc(
    grace: int = typer.Option(24, "--grace", help="Grace period in hours"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Count without deleting"),
    format: str = typer.Option("table", "--format", help="Output format: table or json"),
):
    """Remove unreachable objects from the object store."""
    import json as _json
    from dit.core.gc import gc as run_gc

    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)

    ref_hashes = []
    for _name, h in refs.list_branches().items():
        ref_hashes.append(h)
    for _name, h in refs.list_tags().items():
        ref_hashes.append(h)

    index = StagingIndex(dot / "index")
    index_entries = index.entries_typed()

    result = run_gc(
        store,
        ref_hashes,
        index_entries=index_entries if index_entries else None,
        grace_seconds=grace * 3600,
        dry_run=dry_run,
    )

    if format == "json":
        typer.echo(_json.dumps({
            "live_counts": result.live_counts,
            "deleted_counts": result.deleted_counts,
            "skipped_counts": result.skipped_counts,
            "total_scanned": result.total_scanned,
            "total_deleted": result.total_deleted,
            "tmp_deleted": result.tmp_deleted,
            "errors": result.errors,
        }, indent=2))
        return

    mode = "dry run" if dry_run else "cleanup"
    typer.echo(f"Garbage collection ({mode}) \u2014 grace period: {grace}h")
    typer.echo("")

    if dry_run:
        header = f"{'Object type':<14} {'Live':>6} {'Unreachable':>13} {'Would delete':>14}"
        sep = "\u2500" * len(header)
        typer.echo(header)
        typer.echo(sep)
        total_live = 0
        total_unreachable = 0
        total_would_delete = 0
        for obj_type in ["commits", "trees", "manifests", "rows", "sidecars", "blobs"]:
            live = result.live_counts.get(obj_type, 0)
            deleted = result.deleted_counts.get(obj_type, 0)
            skipped = result.skipped_counts.get(obj_type, 0)
            unreachable = deleted + skipped
            total_live += live
            total_unreachable += unreachable
            total_would_delete += deleted
            typer.echo(f"{obj_type:<14} {live:>6} {unreachable:>13} {deleted:>14}")
        typer.echo(sep)
        typer.echo(f"{'TOTAL':<14} {total_live:>6} {total_unreachable:>13} {total_would_delete:>14}")
        typer.echo("")
        if result.tmp_deleted > 0:
            typer.echo(f"{result.tmp_deleted} stale tmp file(s) would be deleted.")
        total_skipped = sum(result.skipped_counts.values())
        if total_skipped > 0:
            typer.echo(f"{total_skipped} unreachable object(s) within grace period (skipped).")
    else:
        total_skipped = sum(result.skipped_counts.values())
        if result.total_deleted == 0 and result.tmp_deleted == 0:
            if total_skipped > 0:
                typer.echo(f"{total_skipped} unreachable object(s) within grace period (skipped).")
            else:
                typer.echo("No unreachable objects found.")
            return

        parts = []
        for obj_type in ["commits", "trees", "manifests", "rows", "sidecars", "blobs"]:
            count = result.deleted_counts.get(obj_type, 0)
            if count > 0:
                singular = obj_type.rstrip("s") if count == 1 else obj_type
                parts.append(f"{count} {singular}")
        if parts:
            typer.echo(f"Deleted {result.total_deleted} unreachable object(s) ({', '.join(parts)}).")
        if result.tmp_deleted > 0:
            typer.echo(f"Deleted {result.tmp_deleted} stale tmp file(s).")
        if total_skipped > 0:
            typer.echo(f"{total_skipped} unreachable object(s) within grace period (skipped).")

    if result.errors:
        for err in result.errors:
            typer.echo(f"warning: {err}", err=True)


@app.command()
def fsck(
    no_hash_check: bool = typer.Option(False, "--no-hash-check", help="Skip hash verification"),
    no_graph_check: bool = typer.Option(False, "--no-graph-check", help="Skip graph verification"),
    format: str = typer.Option("table", "--format", help="Output format: table or json"),
):
    """Verify object store integrity."""
    import json as _json
    from dit.core.fsck import fsck as run_fsck

    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)

    branches = refs.list_branches()
    tags = refs.list_tags()
    ref_hashes = list(branches.values()) + list(tags.values())

    result = run_fsck(
        store,
        ref_hashes,
        check_hashes=not no_hash_check,
        check_graph=not no_graph_check,
    )

    if format == "json":
        typer.echo(_json.dumps({
            "checked_objects": result.checked_objects,
            "errors": [{"severity": e.severity, "obj_type": e.obj_type, "obj_hash": e.obj_hash, "message": e.message} for e in result.errors],
            "warnings": [{"severity": w.severity, "obj_type": w.obj_type, "obj_hash": w.obj_hash, "message": w.message} for w in result.warnings],
            "total_checked": result.total_checked,
            "total_errors": result.total_errors,
            "total_warnings": result.total_warnings,
        }, indent=2))
        raise typer.Exit(1 if result.total_errors > 0 else 0)

    typer.echo("Object store integrity check")
    typer.echo("")

    if not no_hash_check:
        typer.echo("Hash verification:")
        for obj_type in ["commits", "trees", "manifests", "rows", "sidecars", "blobs"]:
            count = result.checked_objects.get(obj_type, 0)
            type_errors = [e for e in result.errors if e.obj_type == obj_type and ("hash" in e.message.lower() or "corrupt" in e.message.lower())]
            status = f"✗ {len(type_errors)} error(s)" if type_errors else "✓"
            typer.echo(f"  {obj_type:<14} {count:>4}  {status}")
        typer.echo("")

    if not no_graph_check:
        typer.echo("Graph verification:")
        typer.echo(f"  Refs checked: {len(ref_hashes)} ({len(branches)} branch(es), {len(tags)} tag(s))")
        commits_count = result.checked_objects.get("commits", 0)
        if commits_count > 0:
            typer.echo(f"  Commits reachable: {commits_count}")
        graph_errors = [e for e in result.errors if "missing" in e.message.lower() or "dangling" in e.message.lower()]
        if graph_errors:
            typer.echo(f"  {len(graph_errors)} missing or dangling reference(s) found")
        else:
            typer.echo("  All references valid ✓")
        typer.echo("")

    if result.total_errors > 0:
        typer.echo(f"ERRORS ({result.total_errors}):")
        for e in result.errors:
            typer.echo(f"  [{e.obj_type}] {e.obj_hash[:16]}...: {e.message}")
        typer.echo("")

    if result.total_warnings > 0:
        typer.echo(f"WARNINGS ({result.total_warnings}):")
        for w in result.warnings:
            typer.echo(f"  [{w.obj_type}] {w.obj_hash[:16]}...: {w.message}")
        typer.echo("")

    if result.total_errors == 0 and result.total_warnings == 0:
        typer.echo(f"✓ No issues found. {result.total_checked} objects checked.")
    else:
        typer.echo(f"✗ {result.total_errors} error(s), {result.total_warnings} warning(s). {result.total_checked} objects checked.")

    raise typer.Exit(1 if result.total_errors > 0 else 0)


def _count_result_files(store, commit_hash: str) -> int:
    """Count manifest files in a commit (used for validate summary line)."""
    from dit.core.objects import deserialize_commit
    from dit.core.tree_walker import flatten_tree
    try:
        commit_data = store.read("commits", commit_hash)
        if commit_data is None:
            return 0
        commit = deserialize_commit(commit_data)
        flat = flatten_tree(store, commit.tree_hash)
        return sum(1 for _, (t, _, _) in flat.items() if t == "manifest")
    except Exception:
        return 0


def _fmt_tokens(n: int | None) -> str:
    if n is None:
        return "\u2014"
    if n >= 1_000_000:
        return f"~{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"~{n / 1_000:.0f}K"
    return str(n)


def _fmt_chars(n: int | None) -> str:
    if n is None:
        return "\u2014"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


def _fmt_lang(dist: dict | None) -> str:
    if not dist:
        return "\u2014"
    top_lang, top_count = max(dist.items(), key=lambda kv: kv[1])
    total = sum(dist.values())
    pct = round(top_count / total * 100) if total > 0 else 0
    return f"{top_lang} {pct}%"


def _get_author() -> str:
    return os.environ.get("DIT_AUTHOR", os.environ.get("USER", "unknown"))


@app.command()
def dedup(
    ref: str = typer.Option("main", "--ref", help="Branch name or commit hash"),
    path: Optional[str] = typer.Option(None, "--path", help="Path prefix filter"),
    format: str = typer.Option("table", "--format", help="Output format: table or json"),
    exact_only: bool = typer.Option(False, "--exact-only", help="Only show exact duplicates"),
    query_only: bool = typer.Option(False, "--query-only", help="Only show query duplicates"),
):
    """Detect duplicate rows across files."""
    import json as _json
    from dit.core.dedup import detect_duplicates

    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    refs_store = RefStore(dot)

    commit_hash = refs_store.get_branch(ref)
    if commit_hash is None:
        if len(ref) == 64 and all(c in "0123456789abcdef" for c in ref):
            commit_hash = ref
        else:
            typer.echo(f"fatal: ref '{ref}' not found", err=True)
            raise typer.Exit(1)

    try:
        result = detect_duplicates(store, commit_hash, path_prefix=path)
    except FileNotFoundError as exc:
        typer.echo(f"fatal: {exc}", err=True)
        raise typer.Exit(1)

    if exact_only:
        result["query_duplicates"] = []
        result["summary"]["query_dup_groups"] = 0
        result["summary"]["query_dup_rows"] = 0
        if not result["exact_duplicates"]:
            result["summary"]["severity"] = "clean"
    if query_only:
        result["exact_duplicates"] = []
        result["summary"]["exact_dup_groups"] = 0
        result["summary"]["exact_dup_rows"] = 0
        if result["summary"]["severity"] == "warning" and not result["exact_duplicates"]:
            result["summary"]["severity"] = "info" if result["query_duplicates"] else "clean"

    severity = result["summary"]["severity"]
    exit_code = 1 if severity == "warning" else 0

    if format == "json":
        typer.echo(_json.dumps(result, indent=2))
        raise typer.Exit(exit_code)

    s = result["summary"]
    ref_display = f"heads/{ref}" if refs_store.get_branch(ref) else ref
    typer.echo(f"Duplicate detection for {branch_str(ref_display)} (commit {hash_str(commit_hash[:8])})")
    typer.echo("")

    if severity == "clean":
        typer.echo(success_str(f"No duplicates found. {s['total_rows']} rows across {s['total_files']} files."))
        raise typer.Exit(0)

    if result["exact_duplicates"]:
        typer.echo(warn_str(f"⚠ EXACT DUPLICATES ({s['exact_dup_groups']} groups, {s['exact_dup_rows']} rows) \u2014 identical content"))
        typer.echo(dim_str("\u2500" * 60))
        typer.echo("  row_hash    Count  Files")
        for group in result["exact_duplicates"]:
            file_counts: dict[str, int] = {}
            for occ in group["occurrences"]:
                file_counts[occ["file"]] = file_counts.get(occ["file"], 0) + 1
            files_str = ", ".join(f"{f} (\u00d7{c})" for f, c in file_counts.items())
            typer.echo(f"  {hash_str(group['row_hash'][:8])}    {group['count']:>3}x   {files_str}")
        typer.echo("")

    if result["query_duplicates"]:
        typer.echo(info_str(f"ℹ QUERY DUPLICATES ({s['query_dup_groups']} groups, {s['query_dup_rows']} rows) \u2014 same query, different response"))
        typer.echo(dim_str("\u2500" * 60))
        typer.echo("  fingerprint  Variants  Files")
        for group in result["query_duplicates"]:
            file_counts: dict[str, int] = {}
            for occ in group["occurrences"]:
                file_counts[occ["file"]] = file_counts.get(occ["file"], 0) + 1
            files_str = ", ".join(f"{f} (\u00d7{c})" for f, c in file_counts.items())
            typer.echo(f"  {group['query_fingerprint'][:8]}    {len(group['row_hashes'])} variants   {files_str}")
        typer.echo("")

    typer.echo(f"Summary: {s['total_rows']} rows across {s['total_files']} files")
    if s["exact_dup_groups"] > 0:
        typer.echo(f"  Exact duplicates: {s['exact_dup_groups']} groups ({s['exact_dup_rows']} rows) {warn_str('WARNING')}")
    if s["query_dup_groups"] > 0:
        typer.echo(f"  Query duplicates: {s['query_dup_groups']} groups ({s['query_dup_rows']} rows) {info_str('INFO')}")

    raise typer.Exit(exit_code)


if __name__ == "__main__":
    app()
