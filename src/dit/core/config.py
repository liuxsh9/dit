import tomllib
from pathlib import Path
import tomli_w


def load_config(dot_datahub: Path) -> dict:
    """Load .datahub/config TOML, return empty dict if missing."""
    config_path = dot_datahub / "config"
    if not config_path.exists():
        return {}
    with open(config_path, "rb") as f:
        return tomllib.load(f)


def save_config(dot_datahub: Path, config: dict) -> None:
    """Write config dict to .datahub/config as TOML."""
    config_path = dot_datahub / "config"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "wb") as f:
        tomli_w.dump(config, f)


def get_remote(dot_datahub: Path, name: str) -> dict | None:
    """Get remote config {url, token} or None."""
    config = load_config(dot_datahub)
    remotes = config.get("remote", {})
    return remotes.get(name)


def set_remote(dot_datahub: Path, name: str, url: str, token: str = "") -> None:
    """Set remote URL and optional token."""
    config = load_config(dot_datahub)
    if "remote" not in config:
        config["remote"] = {}
    config["remote"][name] = {"url": url, "token": token}
    save_config(dot_datahub, config)


def remove_remote(dot_datahub: Path, name: str) -> bool:
    """Remove remote, return True if existed."""
    config = load_config(dot_datahub)
    remotes = config.get("remote", {})
    if name not in remotes:
        return False
    del remotes[name]
    save_config(dot_datahub, config)
    return True
