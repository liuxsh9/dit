import typer


def hash_str(h: str) -> str:
    return typer.style(h, fg=typer.colors.YELLOW)

def branch_str(b: str) -> str:
    return typer.style(b, fg=typer.colors.CYAN, bold=True)

def added_str(text: str) -> str:
    return typer.style(text, fg=typer.colors.GREEN)

def removed_str(text: str) -> str:
    return typer.style(text, fg=typer.colors.RED)

def modified_str(text: str) -> str:
    return typer.style(text, fg=typer.colors.YELLOW)

def refreshed_str(text: str) -> str:
    return typer.style(text, fg=typer.colors.MAGENTA)

def header_str(text: str) -> str:
    return typer.style(text, bold=True)

def dim_str(text: str) -> str:
    return typer.style(text, dim=True)

def warn_str(text: str) -> str:
    return typer.style(text, fg=typer.colors.YELLOW, bold=True)

def error_str(text: str) -> str:
    return typer.style(text, fg=typer.colors.RED, bold=True)

def success_str(text: str) -> str:
    return typer.style(text, fg=typer.colors.GREEN, bold=True)

def info_str(text: str) -> str:
    return typer.style(text, fg=typer.colors.BLUE)
