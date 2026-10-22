"""Starts the real dashboard, runs the browser-engine test against it, stops the server.

Usage (from the project root):  python tests/frontend/run_e2e.py
Needs Node.js and `npm ci --prefix tests/frontend` first.
"""
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent


def main() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with tempfile.TemporaryDirectory() as fixtures:
        subprocess.run([sys.executable, str(HERE / "make_fixtures.py"), fixtures], check=True)
        server = subprocess.Popen(  # noqa: S603
            [sys.executable, "-m", "dashboard", "--no-browser", "--port", str(port)],
            cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT)},
        )
        try:
            for _ in range(100):
                try:
                    socket.create_connection(("127.0.0.1", port), timeout=0.3).close()
                    break
                except OSError:
                    time.sleep(0.1)
            node = "node.exe" if sys.platform == "win32" else "node"
            return subprocess.run([node, "e2e.mjs", f"http://127.0.0.1:{port}", fixtures], cwd=HERE, check=False).returncode
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()


if __name__ == "__main__":
    sys.exit(main())
