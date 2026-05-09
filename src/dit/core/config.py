import tomllib
from pathlib import Path
import tomli_w


def load_config(dot_dit: Path) -> dict:
    """Load .dit/config TOML, return empty dict if missing."""
    config_path = dot_dit / "config"
    if not config_path.exists():
        return {}
    with open(config_path, "rb") as f:
        return tomllib.load(f)


def save_config(dot_dit: Path, config: dict) -> None:
    """Write config dict to .dit/config as TOML."""
    config_path = dot_dit / "config"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "wb") as f:
        tomli_w.dump(config, f)


def get_remote(dot_dit: Path, name: str) -> dict | None:
    """Get remote config {url, token} or None."""
    config = load_config(dot_dit)
    remotes = config.get("remote", {})
    return remotes.get(name)


def set_remote(dot_dit: Path, name: str, url: str, token: str = "") -> None:
    """Set remote URL and optional token."""
    config = load_config(dot_dit)
    if "remote" not in config:
        config["remote"] = {}
    config["remote"][name] = {"url": url, "token": token}
    save_config(dot_dit, config)


def get_user_identity(dot_dit: Path) -> dict[str, str]:
    """Get configured local user identity fields."""
    config = load_config(dot_dit)
    user = config.get("user", {})
    if not isinstance(user, dict):
        return {}
    return {k: v for k, v in user.items() if k in {"name", "email"} and isinstance(v, str)}


def set_user_identity(dot_dit: Path, *, name: str | None = None, email: str | None = None) -> None:
    """Set local user identity fields in .dit/config."""
    config = load_config(dot_dit)
    user = config.get("user", {})
    if not isinstance(user, dict):
        user = {}
    if name is not None:
        user["name"] = name
    if email is not None:
        user["email"] = email
    config["user"] = user
    save_config(dot_dit, config)


def remove_remote(dot_dit: Path, name: str) -> bool:
    """Remove remote, return True if existed."""
    config = load_config(dot_dit)
    remotes = config.get("remote", {})
    if name not in remotes:
        return False
    del remotes[name]
    save_config(dot_dit, config)
    return True
