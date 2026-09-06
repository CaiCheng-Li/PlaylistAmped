"""Entry point. Everything the program does lives in the web interface, so
this only picks a port and opens it.
"""

from __future__ import annotations

import argparse
import errno
import socket
import sys
import threading
import webbrowser

DEFAULT_PORT = 7391
PORT_ATTEMPTS = 20


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="playlistamped",
        description="Mirror a YouTube Music or Spotify playlist into a Plex playlist for Plexamp.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="interface to bind (default: localhost only)",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="port to serve on")
    parser.add_argument(
        "--no-browser", action="store_true", help="do not open a browser window"
    )
    return parser


def free_port(host: str, start: int, attempts: int = PORT_ATTEMPTS) -> int:
    """Find a usable port, so a second copy or an unrelated service on the
    default port does not stop the app from starting."""
    for candidate in range(start, start + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind((host, candidate))
                return candidate
            except OSError as exc:
                if exc.errno not in (errno.EADDRINUSE, errno.EACCES):
                    raise
    raise SystemExit(
        f"No free port between {start} and {start + attempts - 1}. Pass --port."
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # Track titles carry characters a legacy console codepage cannot encode;
    # without this a single exotic title can abort the process on Windows.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    from .webapp import app

    port = free_port(args.host, args.port)
    url = f"http://{args.host}:{port}"

    print(f"playlistamped is running at {url}")
    print("Press Ctrl+C to stop.")
    if port != args.port:
        print(f"(port {args.port} was busy)")

    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        app.run(host=args.host, port=port, threaded=True)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
