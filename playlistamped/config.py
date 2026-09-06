"""Configuration: a TOML file in the platform config dir, overridable by env.

Paths come from ``platformdirs``, so this lands in ``~/.config/playlistamped``
on Linux, ``~/Library/Application Support`` on macOS and ``%LOCALAPPDATA%`` on
Windows without any per-platform code here.
"""

from __future__ import annotations

import os
import shutil
import tomllib
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from platformdirs import user_cache_dir, user_config_dir

APP = "playlistamped"
# The project was called this before the rename. An install that upgrades
# should not have to sign in again or re-download a large library index.
LEGACY_APP = "playlistamp"


def config_dir() -> Path:
    return Path(user_config_dir(APP, appauthor=False))


def cache_dir() -> Path:
    return Path(user_cache_dir(APP, appauthor=False))


def config_path() -> Path:
    return config_dir() / "config.toml"


def migrate_legacy() -> bool:
    """Adopt settings and caches written under the old name. Returns True if
    anything moved."""
    moved = False
    for legacy, current in (
        (Path(user_config_dir(LEGACY_APP, appauthor=False)), config_dir()),
        (Path(user_cache_dir(LEGACY_APP, appauthor=False)), cache_dir()),
    ):
        if legacy == current or not legacy.exists() or current.exists():
            continue
        try:
            current.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(legacy), str(current))
            moved = True
        except OSError:
            pass  # a failed migration just means a fresh start, not a crash
    return moved


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

    def can_switch_servers(self) -> bool:
        """Whether we hold plex.tv credentials, which is what lets the user
        move to another server without signing in again."""
        return bool(self.account_token or (self.username and self.password))


def _quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def load() -> Config:
    """Read config.toml if present, then apply environment overrides."""
    migrate_legacy()

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

    lines = ["# playlistamped configuration", "", "[plex]"]
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

    # Owner-only: the file holds a Plex token. No-op on filesystems without
    # POSIX permissions, which is why the failure is swallowed.
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def sign_out(*, forget_decisions: bool = False) -> None:
    """Remove the stored credentials and cached library.

    The library index and playlist caches belong to the server being left, so
    they go too. Review decisions survive unless explicitly dropped; saved
    matches are reused only on their original server and library.
    """
    path = config_path()
    if path.exists():
        path.unlink()

    root = cache_dir()
    for name in ("index", "youtube", "spotify"):
        shutil.rmtree(root / name, ignore_errors=True)
    if forget_decisions:
        decisions = root / "decisions.json"
        if decisions.exists():
            decisions.unlink()
