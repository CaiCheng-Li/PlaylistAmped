"""Command line entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console

from . import config as config_mod
from .matcher import Matcher
from .plex_index import PlexError, connect, discover, load_index
from .report import (
    matched_keys,
    print_mapping,
    report_paths,
    summarize,
    write_csv,
    write_wanted,
)
from .review import apply_decisions, load_decisions, review
from .sync import build_summary, sync_playlist
from .sources import PlaylistError, fetch_playlist


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="playlistamp",
        description="Mirror a YouTube Music or Spotify playlist into a Plex playlist for Plexamp.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sync = sub.add_parser("sync", help="mirror a playlist into Plex")
    sync.add_argument(
        "url", help="public YouTube Music or Spotify playlist link (or a YouTube id)"
    )
    sync.add_argument("--name", help="Plex playlist name (default: the playlist's own title)")
    sync.add_argument("--section", help="Plex music library section name")
    sync.add_argument("--auto-accept", type=float, help="score at or above which to match silently")
    sync.add_argument("--review-floor", type=float, help="score below which a track counts as missing")
    sync.add_argument("--yes", action="store_true", help="skip interactive review (unattended run)")
    sync.add_argument("--dry-run", action="store_true", help="show the mapping, write nothing")
    sync.add_argument("--no-reorder", action="store_true", help="do not reorder an existing playlist")
    sync.add_argument("--refresh-index", action="store_true", help="rebuild the Plex library index")
    sync.add_argument(
        "--refresh-playlist", action="store_true", help="refetch the playlist from its source"
    )
    sync.add_argument(
        "--report-dir", type=Path, default=Path.cwd(), help="where to write the CSV report"
    )

    sub.add_parser("config", help="set up the Plex connection")

    ui = sub.add_parser("ui", help="open the web interface")
    ui.add_argument("--port", type=int, default=5000)
    ui.add_argument("--host", default="127.0.0.1")
    ui.add_argument("--no-browser", action="store_true", help="do not open a browser")

    index = sub.add_parser("index", help="inspect or rebuild the local library index")
    index.add_argument("--refresh", action="store_true", help="rebuild from the server")

    return parser


def _sign_in(console: Console, cfg: config_mod.Config) -> bool:
    """Sign in to plex.tv with a linking code and pick a server.

    No address, port or token needed, and it works from anywhere the server is
    reachable -- including off its network, and with Google/Apple sign-in or
    two-factor, which a username and password cannot handle.
    """
    from plexapi.myplex import MyPlexAccount, MyPlexPinLogin

    try:
        pin_login = MyPlexPinLogin()
    except Exception as exc:
        console.print(f"[red]Could not start sign-in: {exc}[/red]")
        return False

    console.print()
    console.print("Open [bold cyan]https://plex.tv/link[/bold cyan] and enter this code:")
    console.print(f"\n    [bold yellow]{pin_login.pin}[/bold yellow]\n")
    console.print("[dim]Waiting for you to link (up to 5 minutes)…[/dim]")

    pin_login.run(timeout=300)
    if not pin_login.waitForLogin():
        console.print("[red]Sign-in was not completed — the code expired or was declined.[/red]")
        return False

    cfg.account_token = pin_login.token
    account = MyPlexAccount(token=cfg.account_token)

    servers = [r for r in account.resources() if "server" in (r.provides or "")]
    if not servers:
        console.print("[red]This account has no Plex servers.[/red]")
        return False

    console.print(f"\n[green]Signed in as[/green] {account.username}")
    if len(servers) == 1:
        cfg.server_name = servers[0].name
        console.print(f"Server: [cyan]{cfg.server_name}[/cyan]")
    else:
        for i, resource in enumerate(servers, start=1):
            owned = "" if resource.owned else " [dim](shared with you)[/dim]"
            console.print(f"  [bold cyan]{i}[/bold cyan]  {resource.name}{owned}")
        choice = console.input("Which server [dim](1)[/dim]: ").strip() or "1"
        if not choice.isdigit() or not 1 <= int(choice) <= len(servers):
            console.print("[red]Not a listed choice.[/red]")
            return False
        cfg.server_name = servers[int(choice) - 1].name

    # Cache whatever address actually worked so later runs skip discovery.
    console.print("[dim]Locating the server…[/dim]")
    try:
        server = discover(cfg)
    except PlexError as exc:
        console.print(f"[red]{exc}[/red]")
        return False
    cfg.baseurl = server._baseurl
    cfg.token = server._token
    return True


def cmd_config(console: Console) -> int:
    cfg = config_mod.load()
    console.print("[bold]Connect to Plex[/bold]\n")
    console.print("  [bold cyan]1[/bold cyan]  Sign in with a code at plex.tv [dim](recommended)[/dim]")
    console.print("     [dim]No IP or token needed; works off the server's network.[/dim]")
    console.print("  [bold cyan]2[/bold cyan]  Enter a server address and token myself")

    choice = console.input("\nChoice [dim](1)[/dim]: ").strip() or "1"

    if choice == "1":
        if not _sign_in(console, cfg):
            return 1
    else:
        baseurl = console.input(
            f"Server URL [dim]({cfg.baseurl or 'http://localhost:32400'})[/dim]: "
        ).strip()
        cfg.baseurl = baseurl or cfg.baseurl or "http://localhost:32400"
        console.print(
            "[dim]Token: open any item in Plex Web → Get Info → View XML, and copy\n"
            "the X-Plex-Token value out of the URL.[/dim]"
        )
        token = console.input(f"Token [dim]({'kept' if cfg.token else 'required'})[/dim]: ").strip()
        if token:
            cfg.token = token
        if not cfg.token:
            console.print("[red]A token is required.[/red]")
            return 1

    try:
        plex = connect(cfg)
    except PlexError as exc:
        console.print(f"[red]{exc}[/red]")
        return 1

    sections = [s.title for s in plex.library.sections() if s.type == "artist"]
    if not sections:
        console.print("[red]This server has no music library.[/red]")
        return 1
    console.print(f"\nMusic libraries: [cyan]{', '.join(sections)}[/cyan]")
    section = console.input(f"Use which [dim]({cfg.section})[/dim]: ").strip()
    cfg.section = section or (cfg.section if cfg.section in sections else sections[0])

    path = config_mod.save(cfg)
    console.print(f"[green]Saved[/green] {path}")
    console.print(f"[dim]Connected to {plex.friendlyName} · {cfg.section}[/dim]")
    return 0


def cmd_index(console: Console, args: argparse.Namespace) -> int:
    cfg = config_mod.load()
    plex = connect(cfg)
    with console.status("Reading the Plex library…"):
        index = load_index(plex, cfg, refresh=args.refresh, on_progress=console.print)
    console.print(
        f"[green]{len(index)}[/green] tracks indexed from the "
        f"[cyan]{index.section}[/cyan] library on [cyan]{plex.friendlyName}[/cyan]"
    )
    return 0


def cmd_sync(console: Console, args: argparse.Namespace) -> int:
    cfg = config_mod.load()
    if args.section:
        cfg.section = args.section
    if args.auto_accept is not None:
        cfg.auto_accept = args.auto_accept
    if args.review_floor is not None:
        cfg.review_floor = args.review_floor

    with console.status("Reading the playlist…"):
        playlist = fetch_playlist(args.url, refresh=args.refresh_playlist)
    console.print(
        f"[bold]{playlist.title}[/bold] [dim]({playlist.source_label})[/dim] — "
        f"{len(playlist.tracks)} track(s)"
        + (f", {len(playlist.unavailable)} unavailable" if playlist.unavailable else "")
    )
    if playlist.is_truncated:
        console.print(
            f"[yellow]Only {len(playlist.tracks)} of {playlist.total_reported} tracks "
            f"could be read.[/yellow] [dim]{playlist.truncated_note}[/dim]"
        )

    plex = connect(cfg)
    with console.status("Reading the Plex library…"):
        index = load_index(plex, cfg, on_progress=console.print)
    console.print(f"[dim]{len(index)} tracks in the {cfg.section} library[/dim]")

    matcher = Matcher(index, auto_accept=cfg.auto_accept, review_floor=cfg.review_floor)
    with console.status("Matching…"):
        results = matcher.match_all(playlist.tracks)

    decisions = load_decisions()
    apply_decisions(results, decisions, index)

    if not args.yes and not args.dry_run:
        review(results, decisions, console)

    summarize(results, console)

    if args.dry_run:
        print_mapping(results, console, playlist.source_label)

    title = args.name or playlist.title
    keys = matched_keys(results)

    outcome = sync_playlist(
        plex,
        title,
        keys,
        summary=build_summary(playlist.url, len(keys), len(results)),
        reorder=not args.no_reorder,
        dry_run=args.dry_run,
    )

    if args.dry_run:
        console.print(f"\n[yellow]Dry run[/yellow] — would sync {outcome.total} track(s) to “{title}”")
    elif outcome.action == "created":
        console.print(f"\n[green]Created[/green] “{title}” with {outcome.total} track(s)")
    elif outcome.action == "updated":
        detail = f"+{outcome.added} / -{outcome.removed}"
        detail += ", reordered" if outcome.reordered else ""
        console.print(f"\n[green]Updated[/green] “{title}” ({detail}, {outcome.total} total)")
    else:
        console.print(f"\n[dim]“{title}” already matches — nothing to do.[/dim]")

    csv_path, wanted_path = report_paths(args.report_dir, playlist.title)
    write_csv(results, csv_path)
    console.print(f"[dim]Report: {csv_path}[/dim]")
    if write_wanted(results, wanted_path):
        console.print(f"[dim]Wanted list: {wanted_path}[/dim]")

    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # Windows consoles and redirected pipes default to a legacy codepage, and
    # track titles routinely carry characters it cannot encode. Without this a
    # single exotic title aborts the whole run.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    console = Console()

    try:
        if args.command == "config":
            return cmd_config(console)
        if args.command == "index":
            return cmd_index(console, args)
        if args.command == "sync":
            return cmd_sync(console, args)
        if args.command == "ui":
            from .webapp import serve

            console.print(
                f"[bold]playlistamp[/bold] → [cyan]http://{args.host}:{args.port}[/cyan]  "
                "[dim](ctrl-c to stop)[/dim]"
            )
            serve(host=args.host, port=args.port, open_browser=not args.no_browser)
            return 0
    except (PlaylistError, PlexError) as exc:
        console.print(f"[red]{exc}[/red]")
        return 1
    except KeyboardInterrupt:
        console.print("\n[dim]Interrupted.[/dim]")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
