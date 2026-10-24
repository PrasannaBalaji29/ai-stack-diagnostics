"""Path validation and safe file reading. All file access goes through here."""

from __future__ import annotations

import codecs
import os
from pathlib import Path

MAX_READ_BYTES = 1_000_000


class ProjectPathError(ValueError):
    """The project path is missing, unsafe, or unusable.

    `code` is a short fixed word (empty, missing, drive_root ...). Screens that face a browser show a fixed
    sentence for the code and never the exception text.
    """

    def __init__(self, message: str, code: str = "invalid") -> None:
        super().__init__(message)
        self.code = code


def resolve_project_path(path: str) -> Path:
    if not isinstance(path, str) or not path.strip():
        raise ProjectPathError("The project path is empty.", "empty")
    cleaned = path.strip().strip("\"'")
    if any(ord(ch) < 32 for ch in cleaned):
        raise ProjectPathError("The project path contains invalid (control) characters.", "control_chars")
    try:
        project = Path(cleaned).expanduser().resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise ProjectPathError(f"Cannot resolve the path: {exc}", "unresolvable") from exc
    try:
        exists, is_dir = project.exists(), project.is_dir()
    except OSError as exc:  # e.g. a path whose name is too long for the file system
        raise ProjectPathError(f"Cannot read the path: {exc}", "unreadable") from exc
    if not exists:
        raise ProjectPathError(f"Path does not exist: {project}", "missing")
    if not is_dir:
        raise ProjectPathError(f"Not a directory: {project}", "not_dir")
    if project == Path(project.anchor):
        raise ProjectPathError("Refusing to scan a drive root. Pick a project folder.", "drive_root")
    try:
        if project == Path.home().resolve():
            raise ProjectPathError("Refusing to scan the whole home folder. Pick a project folder.", "home")
    except RuntimeError:
        pass
    if not os.access(project, os.R_OK | os.X_OK):
        raise ProjectPathError(f"No permission to read: {project}", "no_permission")
    return project


def safe_file(project: Path, candidate: str | Path) -> Path | None:
    """Return the resolved file path only if it is a real file inside the project."""
    try:
        path = Path(candidate)
        if not path.is_absolute():
            path = project / path
        resolved = path.resolve()
        if not resolved.is_relative_to(project.resolve()):
            return None  # outside the project (e.g. a symlink escape or ../)
        return resolved if resolved.is_file() else None
    except (OSError, RuntimeError, ValueError):
        return None


def read_text(path: Path, limit: int = MAX_READ_BYTES) -> str | None:
    """Read a text file, handling UTF-8, UTF-8 BOM and UTF-16 (Windows PowerShell)."""
    try:
        with path.open("rb") as fh:
            raw = fh.read(limit)
    except OSError:
        return None
    if raw.startswith(codecs.BOM_UTF8):
        return raw[len(codecs.BOM_UTF8):].decode("utf-8", errors="replace")
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return raw.decode("utf-16", errors="replace")
    if len(raw) >= 4 and raw[1:2] == b"\x00" and raw[3:4] == b"\x00":
        return raw.decode("utf-16-le", errors="replace")  # UTF-16 without a BOM
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")
