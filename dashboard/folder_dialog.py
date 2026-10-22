"""Opens the operating system's own "choose a folder" window on the user's computer.

The window is shown by a separate, fixed helper program (no user input is ever passed to it), so a
crash or hang in the GUI toolkit cannot take the dashboard down. The dashboard only receives the path
the user clicked; that path is validated again before it is used for anything.
"""

from __future__ import annotations

import os
import subprocess
import sys

TIMEOUT_SECONDS = 180
MAX_PATH_CHARS = 1000

HELPER_CODE = """
import sys
try:
    import tkinter as tk
    from tkinter import filedialog
except Exception:
    print("UNAVAILABLE")
    sys.exit(0)
try:
    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    chosen = filedialog.askdirectory(title="Choose a project folder", mustexist=True)
    root.destroy()
except Exception:
    print("UNAVAILABLE")
    sys.exit(0)
print("PATH:" + chosen if chosen else "CANCELLED")
"""


def choose_folder() -> dict:
    """Returns {"path": str}, {"cancelled": True} or {"error": str}."""
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # no black console box; the dialog itself still shows
    try:
        done = subprocess.run(  # noqa: S603 - our own interpreter, a fixed script, no user input
            [sys.executable, "-c", HELPER_CODE], capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=TIMEOUT_SECONDS, stdin=subprocess.DEVNULL, check=False, **kwargs,
        )
    except subprocess.TimeoutExpired:
        return {"cancelled": True}
    except OSError:
        return {"error": "The folder window could not be opened."}
    lines = [ln.strip() for ln in done.stdout.splitlines() if ln.strip()]
    last = lines[-1] if lines else ""
    if last == "UNAVAILABLE" or not last:
        return {"error": "The folder window is not available here. Type or paste the folder path instead."}
    if last == "CANCELLED":
        return {"cancelled": True}
    if last.startswith("PATH:"):
        path = os.path.normpath(last[5:])
        if 0 < len(path) <= MAX_PATH_CHARS and not any(ord(ch) < 32 for ch in path):
            return {"path": path}
    return {"error": "The folder window returned something unexpected."}
