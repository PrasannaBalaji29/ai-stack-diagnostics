"""Start the dashboard:  python -m dashboard [project-folder] [--port 8765] [--no-browser]"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import webbrowser

import uvicorn

from dashboard.app import create_app

HOST = "127.0.0.1"  # fixed on purpose: there is no option to listen on other network interfaces


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex((HOST, port)) != 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m dashboard", description="Local, read-only AI stack dashboard.")
    parser.add_argument("path", nargs="?", default="", help="project folder to pre-fill in the page")
    parser.add_argument("--port", type=int, default=8765, help="port on localhost (default 8765)")
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser automatically")
    args = parser.parse_args(argv)

    if not 1024 <= args.port <= 65535:
        print("Choose a port between 1024 and 65535.", file=sys.stderr)
        return 2
    if not _port_is_free(args.port):
        print(f"Port {args.port} is already in use. Try: python -m dashboard --port {args.port + 1}", file=sys.stderr)
        return 2

    url = f"http://{HOST}:{args.port}"
    print(f"AI Stack Diagnostics dashboard: {url}  (local only, read-only. Press Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    uvicorn.run(
        create_app(default_path=args.path), host=HOST, port=args.port, log_level="warning",
        server_header=False, date_header=False, proxy_headers=False,  # never trust X-Forwarded-* from anyone
        limit_concurrency=64, timeout_keep_alive=5, h11_max_incomplete_event_size=16_384,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
