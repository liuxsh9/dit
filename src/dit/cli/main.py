from pathlib import Path

import typer

from dit.core.refs import RefStore

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


if __name__ == "__main__":
    app()
