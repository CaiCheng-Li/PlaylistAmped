"""Configuration: a TOML file in the platform config dir, overridable by env."""

from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from platformdirs import user_cache_dir, user_config_dir

APP = "playlistamp"


def config_dir() -> Path:
    return Path(user_config_dir(APP, appauthor=False))


def cache_dir() -> Path:
    return Path(user_cache_dir(APP, appauthor=False))


def config_path() -> Path:
    return config_dir() / "config.toml"


@dataclass
class Config:
    """Connection settings and matcher thresholds.

    Any one of these is enough to connect:

    * ``baseurl`` + ``token`` -- direct and fastest, but needs an address.
    * ``account_token`` + ``server_name`` -- signs in to plex.tv and lets it
      discover the server, so it works with no idea of the IP and from off the
      server's network. ``baseurl``/``token`` double as a cache for this path.
    * ``username`` + ``password`` + ``server_name`` -- same, for accounts
      without SSO or two-factor.
    """

    baseurl: str = ""
    token: str = ""
    account_token: str = ""
    username: str = ""
    password: str = ""
    server_name: str = ""
    section: str = "Music"
    auto_accept: float = 88.0
    review_floor: float = 65.0

    def has_connection(self) -> bool:
        return (
            bool(self.token and self.baseurl)
            or bool(self.account_token and self.server_name)
            or bool(self.username and self.password and self.server_name)
        )


def _quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def load() -> Config:
    """Read config.toml if present, then apply environment overrides."""
    cfg = Config()
    path = config_path()
    if path.exists():
        with path.open("rb") as handle:
            data = tomllib.load(handle)
        known = {f.name for f in fields(Config)}
        merged = {**data.get("plex", {}), **data.get("match", {})}
        for key, value in merged.items():
            if key in known:
                setattr(cfg, key, value)

    for env, attr in (
        ("PLEX_URL", "baseurl"),
        ("PLEX_TOKEN", "token"),
        ("PLEX_SECTION", "section"),
    ):
        if os.environ.get(env):
            setattr(cfg, attr, os.environ[env])
    return cfg


def save(cfg: Config) -> Path:
    """Write config.toml, readable only by the current user where supported."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(cfg)

    lines = ["# playlistamp configuration", "", "[plex]"]
    for key in (
        "baseurl",
        "token",
        "account_token",
        "username",
        "password",
        "server_name",
        "section",
    ):
        lines.append(f"{key} = {_quote(str(data[key]))}")
    lines += ["", "[match]"]
    for key in ("auto_accept", "review_floor"):
        lines.append(f"{key} = {float(data[key])}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    try:  # best effort; not supported on all filesystems
        path.chmod(0o600)
    except OSError:
        pass
    return path
