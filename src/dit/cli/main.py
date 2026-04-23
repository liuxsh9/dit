import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

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
def diff():
    """Show changes between working directory and HEAD."""
    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)

    current_files: dict[str, Manifest] = {}
    for fp in find_jsonl_files(repo_root):
        rel = str(fp.relative_to(repo_root))
        manifest, _ = build_manifest_for_file(fp)
        current_files[rel] = manifest

    head_files: dict[str, Manifest] = {}
    head_hash = refs.resolve_head()
    if head_hash:
        commit_data = store.read("commits", head_hash)
        head_commit = deserialize_commit(commit_data)
        tree_data = store.read("trees", head_commit.tree_hash)
        tree = deserialize_tree(tree_data)
        for entry in tree.entries:
            if entry.obj_type == "manifest":
                m_data = store.read("manifests", entry.obj_hash)
                head_files[entry.name] = deserialize_manifest(m_data)

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
            typer.echo(f"{rel}: new file ({new_count} rows)")
        elif rel not in current_files:
            typer.echo(f"{rel}: deleted ({old_count} rows)")
        else:
            typer.echo(f"{rel}: {old_count} → {new_count} rows ({result.summary()})")

        if result.refreshed:
            typer.echo(f"  Likely refreshed: {len(result.refreshed)} rows")

    if not any_changes:
        typer.echo("No changes.")


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


@app.command()
def status():
    """Show working directory status."""
    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    index = StagingIndex(dot / "index")
    refs = RefStore(dot)

    branch = refs.current_branch() or "HEAD"
    typer.echo(f"On branch {branch}")

    staged = index.entries()
    if staged:
        typer.echo("\nStaged files:")
        for rel in sorted(staged.keys()):
            typer.echo(f"  {rel}")

    head_manifests: dict[str, str] = {}
    head_hash = refs.resolve_head()
    if head_hash:
        commit_data = store.read("commits", head_hash)
        head_commit = deserialize_commit(commit_data)
        tree_data = store.read("trees", head_commit.tree_hash)
        tree = deserialize_tree(tree_data)
        for entry in tree.entries:
            if entry.obj_type == "manifest":
                head_manifests[entry.name] = entry.obj_hash

    current_files = find_jsonl_files(repo_root)
    current_rels = {str(f.relative_to(repo_root)) for f in current_files}
    head_rels = set(head_manifests.keys())

    modified = []
    new_files = []
    deleted = sorted(head_rels - current_rels)

    for fp in current_files:
        rel = str(fp.relative_to(repo_root))
        if rel not in head_manifests:
            new_files.append(rel)
        else:
            manifest, _ = build_manifest_for_file(fp)
            current_hash = object_hash(serialize_manifest(manifest))
            if current_hash != head_manifests[rel]:
                modified.append(rel)

    has_changes = modified or new_files or deleted
    if not staged and not has_changes:
        typer.echo("\nNothing to commit, working directory clean.")
        return

    if modified or new_files or deleted:
        typer.echo("\nUnstaged changes:")
        for rel in sorted(modified):
            typer.echo(f"  modified: {rel}")
        for rel in sorted(new_files):
            typer.echo(f"  new file: {rel}")
        for rel in deleted:
            typer.echo(f"  deleted:  {rel}")


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


def _has_uncommitted_changes(repo_root: Path, dot: Path, store: ObjectStore, refs: RefStore) -> bool:
    head_hash = refs.resolve_head()
    if head_hash is None:
        return len(find_jsonl_files(repo_root)) > 0

    head_manifests: dict[str, str] = {}
    commit_data = store.read("commits", head_hash)
    head_commit = deserialize_commit(commit_data)
    tree_data = store.read("trees", head_commit.tree_hash)
    tree = deserialize_tree(tree_data)
    for entry in tree.entries:
        if entry.obj_type == "manifest":
            head_manifests[entry.name] = entry.obj_hash

    current_files = find_jsonl_files(repo_root)
    current_rels = {str(f.relative_to(repo_root)) for f in current_files}
    head_rels = set(head_manifests.keys())

    if current_rels != head_rels:
        return True

    for fp in current_files:
        rel = str(fp.relative_to(repo_root))
        if rel in head_manifests:
            manifest, _ = build_manifest_for_file(fp)
            current_hash = object_hash(serialize_manifest(manifest))
            if current_hash != head_manifests[rel]:
                return True

    return False


def _materialize_tree(repo_root: Path, store: ObjectStore, tree_hash: str, old_tree_hash: str | None = None):
    """Materialize working directory from tree, optimizing by skipping unchanged files."""
    from dit.core.workspace import materialize_file

    tree_data = store.read("trees", tree_hash)
    tree = deserialize_tree(tree_data)
    new_files = {e.name: e.obj_hash for e in tree.entries if e.obj_type == "manifest"}

    old_files: dict[str, str] = {}
    if old_tree_hash:
        old_tree_data = store.read("trees", old_tree_hash)
        old_tree = deserialize_tree(old_tree_data)
        old_files = {e.name: e.obj_hash for e in old_tree.entries if e.obj_type == "manifest"}

    # Materialize changed or new files
    for name, mhash in new_files.items():
        if old_files.get(name) != mhash:
            m_data = store.read("manifests", mhash)
            manifest = deserialize_manifest(m_data)
            materialize_file(repo_root, name, manifest, store)

    # Remove files that exist in old but not in new
    for name in old_files:
        if name not in new_files:
            file_path = repo_root / name
            if file_path.exists():
                file_path.unlink()
                # Clean up empty parent directories
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

    if _has_uncommitted_changes(repo_root, dot, store, refs):
        typer.echo("error: working directory has uncommitted changes", err=True)
        raise typer.Exit(1)

    if index.entries():
        typer.echo("error: staging area is not empty — please commit or reset first", err=True)
        raise typer.Exit(1)

    current_hash = refs.resolve_head()
    current_commit = deserialize_commit(store.read("commits", current_hash)) if current_hash else None
    target_commit = deserialize_commit(store.read("commits", target_hash))

    old_tree_hash = current_commit.tree_hash if current_commit else None
    _materialize_tree(repo_root, store, target_commit.tree_hash, old_tree_hash)

    refs.head_file.write_text(f"ref:{target}\n")
    typer.echo(f"Switched to branch '{target}'.")


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

    if _has_uncommitted_changes(repo_root, dot, store, refs):
        typer.echo("error: working directory has uncommitted changes", err=True)
        raise typer.Exit(1)

    if index.entries():
        typer.echo("error: staging area is not empty — please commit or reset first", err=True)
        raise typer.Exit(1)

    current_hash = refs.resolve_head()
    current_commit = deserialize_commit(store.read("commits", current_hash)) if current_hash else None
    target_commit = deserialize_commit(store.read("commits", target_hash))

    old_tree_hash = current_commit.tree_hash if current_commit else None
    _materialize_tree(repo_root, store, target_commit.tree_hash, old_tree_hash)

    refs.head_file.write_text(f"ref:{target}\n")
    typer.echo(f"Switched to branch '{target}'.")


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", help="Host to bind"),
    port: int = typer.Option(8000, help="Port to listen on"),
):
    """Start the DataHub HTTP API server."""
    try:
        import uvicorn  # noqa: F401
    except ImportError:
        typer.echo(
            "Server dependencies not installed. Run: uv sync --extra server",
            err=True,
        )
        raise typer.Exit(1)

    from dit.server.app import app as fastapi_app

    import uvicorn as _uvicorn

    _uvicorn.run(fastapi_app, host=host, port=port)


remote_app = typer.Typer(name="remote", help="Manage remote repositories.")
app.add_typer(remote_app)


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


@auth_app.command("set-token")
def auth_set_token(
    token: str = typer.Argument(..., help="Raw API token to store"),
    remote: str = typer.Option("origin", help="Remote name to associate the token with"),
):
    """Store an API token for a remote in .datahub/config."""
    from dit.core.config import get_remote, set_remote

    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    existing = get_remote(dot, remote)
    if existing is None:
        typer.echo(f"fatal: Remote '{remote}' not found. Add it first with: dit remote add", err=True)
        raise typer.Exit(1)
    set_remote(dot, remote, existing["url"], token)
    typer.echo(f"Token stored for remote '{remote}'.")


def _build_remote_client(dot: Path, remote_name: str = "origin") -> "RemoteClient":
    from dit.core.config import get_remote
    from dit.core.remote import RemoteClient

    cfg = get_remote(dot, remote_name)
    if cfg is None:
        typer.echo(f"fatal: remote '{remote_name}' not configured", err=True)
        raise typer.Exit(1)
    url: str = cfg["url"]
    token: str = cfg.get("token", "")
    repo_name = url.rstrip("/").rsplit("/", 1)[-1]
    base_url = url.rstrip("/").rsplit("/", 1)[0]
    return RemoteClient(base_url=base_url, token=token, repo=repo_name)


@app.command()
def push(
    remote: str = typer.Option("origin", help="Remote name"),
    branch: str = typer.Option("main", help="Branch name to push"),
):
    """Push local commits to the remote server."""
    from dit.core.walker import walk_commit_objects, is_ancestor

    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)

    local_hash = refs.get_branch(branch)
    if local_hash is None:
        typer.echo(f"fatal: branch '{branch}' does not exist locally", err=True)
        raise typer.Exit(1)

    rc = _build_remote_client(dot, remote)

    remote_hash = rc.get_ref("heads", branch)

    if remote_hash is not None:
        if not is_ancestor(store, remote_hash, local_hash):
            typer.echo(
                "error: push rejected — local branch is not a descendant of remote.\n"
                "  Pull first: dit pull",
                err=True,
            )
            raise typer.Exit(1)

    local_objects = walk_commit_objects(store, local_hash)

    if remote_hash is not None:
        remote_objects = walk_commit_objects(store, remote_hash)
        new_objects: dict[str, set[str]] = {
            obj_type: local_objects[obj_type] - remote_objects[obj_type]
            for obj_type in local_objects
        }
    else:
        new_objects = local_objects

    upload_order = ["rows", "manifests", "trees", "commits"]
    to_upload: dict[str, list[str]] = {}
    for obj_type in upload_order:
        hashes = list(new_objects.get(obj_type, set()))
        if not hashes:
            to_upload[obj_type] = []
            continue
        exists = rc.batch_exists(obj_type, hashes)
        to_upload[obj_type] = [h for h in hashes if not exists.get(h, False)]

    total = sum(len(v) for v in to_upload.values())
    uploaded = 0
    for obj_type in upload_order:
        for hash_hex in to_upload[obj_type]:
            data = store.read(obj_type, hash_hex)
            if data is None:
                typer.echo(f"warning: local object {obj_type}/{hash_hex} missing in store", err=True)
                continue
            rc.upload_object(obj_type, hash_hex, data)
            uploaded += 1

    ok = rc.cas_ref("heads", branch, old=remote_hash, new=local_hash)
    if not ok:
        typer.echo(
            "error: remote ref was updated by another push — pull and retry",
            err=True,
        )
        raise typer.Exit(1)

    typer.echo(f"Pushed {uploaded} new objects to {remote}/{branch} ({local_hash[:8]})")


@app.command()
def clone(
    url: str = typer.Argument(..., help="Remote URL (http://host:port/repo-name)"),
    dest: str = typer.Argument("", help="Destination directory (default: repo name)"),
    token: str = typer.Option("", help="Auth token"),
    branch: str = typer.Option("main", help="Branch to clone"),
):
    """Clone a remote repository into a new directory."""
    from dit.core.config import set_remote
    from dit.core.remote import RemoteClient
    from dit.core.workspace import materialize_file
    from dit.core.objects import deserialize_commit, deserialize_tree, deserialize_manifest

    clean_url = url.rstrip("/")
    repo_name = clean_url.rsplit("/", 1)[-1]
    base_url = clean_url.rsplit("/", 1)[0]

    dest_dir = Path(dest) if dest else Path.cwd() / repo_name
    if dest_dir.exists() and any(dest_dir.iterdir()):
        typer.echo(f"fatal: destination '{dest_dir}' already exists and is not empty", err=True)
        raise typer.Exit(1)
    dest_dir.mkdir(parents=True, exist_ok=True)

    dot = dest_dir / ".datahub"
    dot.mkdir()
    (dot / "objects").mkdir()
    refs = RefStore(dot)
    refs.init()
    store = ObjectStore(dot / "objects")

    rc = RemoteClient(base_url=base_url, token=token, repo=repo_name)

    remote_hash = rc.get_ref("heads", branch)
    if remote_hash is None:
        typer.echo(f"fatal: remote branch '{branch}' not found", err=True)
        raise typer.Exit(1)

    typer.echo(f"Cloning {url} -> {dest_dir}")

    commits_to_fetch: list[str] = []
    queue = [remote_hash]
    visited: set[str] = set()

    while queue:
        chash = queue.pop()
        if chash in visited:
            continue
        visited.add(chash)
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

        tree_data = rc.download_object("trees", commit.tree_hash)
        if tree_data:
            store.write("trees", tree_data)
            tree = deserialize_tree(tree_data)
            for entry in tree.entries:
                if entry.obj_type == "manifest":
                    m_data = rc.download_object("manifests", entry.obj_hash)
                    if m_data:
                        store.write("manifests", m_data)
                        manifest_hashes.add(entry.obj_hash)

    for mhash in manifest_hashes:
        m_data = store.read("manifests", mhash)
        if m_data is None:
            continue
        manifest = deserialize_manifest(m_data)
        for entry in manifest.entries:
            if not store.exists("rows", entry.row_hash):
                row_data = rc.download_object("rows", entry.row_hash)
                if row_data:
                    store.write("rows", row_data)

    refs.set_branch(branch, remote_hash)
    refs.head_file.write_text(f"ref:{branch}\n")
    set_remote(dot, "origin", url, token)

    head_commit_data = store.read("commits", remote_hash)
    head_commit = deserialize_commit(head_commit_data)
    tree_data = store.read("trees", head_commit.tree_hash)
    tree = deserialize_tree(tree_data)
    for entry in tree.entries:
        if entry.obj_type == "manifest":
            m_data = store.read("manifests", entry.obj_hash)
            manifest = deserialize_manifest(m_data)
            materialize_file(dest_dir, entry.name, manifest, store)
            typer.echo(f"  {entry.name}")

    typer.echo(f"Clone complete. {len(commits_to_fetch)} commit(s).")


def _fetch_objects_since(
    rc: "RemoteClient",
    store: ObjectStore,
    remote_hash: str,
    stop_at: str | None,
) -> tuple[int, set[str]]:
    from dit.core.objects import deserialize_commit, deserialize_tree, deserialize_manifest

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

        if not store.exists("trees", commit.tree_hash):
            tree_data = rc.download_object("trees", commit.tree_hash)
            if tree_data:
                store.write("trees", tree_data)
                downloaded += 1
                tree = deserialize_tree(tree_data)
                for entry in tree.entries:
                    if entry.obj_type == "manifest" and not store.exists("manifests", entry.obj_hash):
                        m_data = rc.download_object("manifests", entry.obj_hash)
                        if m_data:
                            store.write("manifests", m_data)
                            downloaded += 1
                            manifest_hashes.add(entry.obj_hash)
                            m = deserialize_manifest(m_data)
                            for me in m.entries:
                                if not store.exists("rows", me.row_hash):
                                    row_data = rc.download_object("rows", me.row_hash)
                                    if row_data:
                                        store.write("rows", row_data)
                                        downloaded += 1

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
    remote_hash = rc.get_ref("heads", branch)
    if remote_hash is None:
        typer.echo(f"  remote branch '{branch}' not found")
        return

    local_hash = refs.get_branch(branch)
    if local_hash == remote_hash:
        typer.echo("Already up to date.")
        return

    count, _ = _fetch_objects_since(rc, store, remote_hash, stop_at=local_hash)
    typer.echo(f"Fetched {count} new objects from {remote}/{branch}")


@app.command()
def pull(
    remote: str = typer.Option("origin", help="Remote name"),
    branch: str = typer.Option("main", help="Branch to pull"),
):
    """Fetch from remote + fast-forward local branch + materialize changed files."""
    from dit.core.objects import deserialize_commit, deserialize_tree, deserialize_manifest
    from dit.core.walker import is_ancestor
    from dit.core.workspace import materialize_file

    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)

    rc = _build_remote_client(dot, remote)
    remote_hash = rc.get_ref("heads", branch)
    if remote_hash is None:
        typer.echo(f"  remote branch '{branch}' not found")
        return

    local_hash = refs.get_branch(branch)
    if local_hash == remote_hash:
        typer.echo("Already up to date.")
        return

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
    tree_data = store.read("trees", head_commit.tree_hash)
    tree = deserialize_tree(tree_data)
    for entry in tree.entries:
        if entry.obj_type == "manifest":
            m_data = store.read("manifests", entry.obj_hash)
            manifest = deserialize_manifest(m_data)
            materialize_file(repo_root, entry.name, manifest, store)

    typer.echo(f"Pulled {count} new objects. Now at {remote_hash[:8]}.")


def _get_author() -> str:
    return os.environ.get("DIT_AUTHOR", os.environ.get("USER", "unknown"))
