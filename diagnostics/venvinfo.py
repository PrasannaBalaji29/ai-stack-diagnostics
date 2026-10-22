"""Reads installed-package metadata straight from a venv's site-packages. Read-only.

Nothing is imported or executed: only METADATA, top_level.txt and RECORD are parsed as text.
"""

from __future__ import annotations

import csv
import io
import os
import re
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from email.parser import Parser
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from diagnostics.safety import read_text

MAX_DISTS = 5000
LOAD_WORKERS = 8          # reading many small files is waiting time, so threads help a lot (especially on Windows)
CACHE_SIZE = 8            # remembered environments (live mode re-scans the same one every few seconds)
MAX_METADATA_BYTES = 256_000
MAX_RECORD_BYTES = 2_000_000
_DIST_DIR_RE = re.compile(r"^(?P<n>.+?)-(?P<v>\d[^-]*?)\.dist-info$", re.IGNORECASE)
_NOT_MODULES = {"", "..", ".", "__pycache__", "bin", "Scripts", "include", "share", "etc", "lib", "man"}


@dataclass
class Dist:
    name: str  # canonical name
    version: Version | None
    requires: list[Requirement] = field(default_factory=list)
    requires_python: SpecifierSet | None = None
    modules: set[str] = field(default_factory=set)  # top-level import names it provides
    editable: bool = False


def python_version_of(cfg: dict[str, str]) -> tuple[int, int, int] | None:
    match = re.match(r"(\d+)\.(\d+)(?:\.(\d+))?", cfg.get("version") or cfg.get("version_info") or "")
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)


def marker_environment(py: tuple[int, int, int] | None) -> dict[str, str]:
    """Marker values for the venv: OS values from this machine, Python values from the venv."""
    env: dict[str, str] = {k: str(v) for k, v in default_environment().items()}
    if py:
        env["python_version"] = f"{py[0]}.{py[1]}"
        env["python_full_version"] = f"{py[0]}.{py[1]}.{py[2]}"
    env["extra"] = ""
    return env


def _inside(path: Path, root: Path) -> Path | None:
    """Resolve path and return it only if it stays inside root (no symlink escapes)."""
    try:
        resolved = path.resolve()
        return resolved if resolved.is_relative_to(root) else None
    except (OSError, RuntimeError, ValueError):
        return None


def _read(path: Path, root: Path, limit: int) -> str | None:
    safe = _inside(path, root)
    return read_text(safe, limit) if safe is not None and safe.is_file() else None


def _modules_from_record(text: str) -> set[str]:
    modules: set[str] = set()
    try:
        for row in csv.reader(io.StringIO(text)):
            if not row:
                continue
            parts = row[0].replace("\\", "/").split("/")
            first = parts[0]
            if first in _NOT_MODULES or first.endswith((".dist-info", ".data", ".egg-info", ".pth")):
                continue
            if len(parts) == 1:
                if first.endswith(".py"):
                    modules.add(first[:-3])
                elif first.endswith((".so", ".pyd")):
                    modules.add(first.split(".")[0])
            else:
                modules.add(first)
    except csv.Error:
        pass
    return modules


def _load_one(entry: Path, root: Path) -> Dist | None:
    match = _DIST_DIR_RE.match(entry.name)
    if not match:
        return None
    metadata = _read(entry / "METADATA", root, MAX_METADATA_BYTES)
    if metadata is None:  # unreadable or missing metadata: still record that the package exists
        try:
            fallback_version: Version | None = Version(match.group("v"))
        except InvalidVersion:
            fallback_version = None
        name = str(canonicalize_name(match.group("n")))
        return Dist(name=name, version=fallback_version, modules={name.replace("-", "_")})
    try:
        headers = Parser().parsestr(metadata, headersonly=True)
        name = str(canonicalize_name(headers.get("Name") or match.group("n")))
        try:
            version: Version | None = Version(headers.get("Version") or match.group("v"))
        except InvalidVersion:
            version = None
        requires: list[Requirement] = []
        for raw in headers.get_all("Requires-Dist") or []:
            try:
                requires.append(Requirement(raw))
            except InvalidRequirement:
                continue
        requires_python = None
        if headers.get("Requires-Python"):
            try:
                requires_python = SpecifierSet(headers["Requires-Python"].strip())
            except InvalidSpecifier:
                requires_python = None
    except Exception:  # noqa: BLE001 - one broken package must never stop the scan
        return None

    modules: set[str] = set()
    top_level = _read(entry / "top_level.txt", root, MAX_METADATA_BYTES)
    if top_level:
        modules |= {line.strip() for line in top_level.splitlines() if line.strip()}
    record = _read(entry / "RECORD", root, MAX_RECORD_BYTES)
    if record and not modules:
        modules |= _modules_from_record(record)
    editable = _read(entry / "direct_url.json", root, 20_000)
    is_editable = bool(editable and '"editable"' in editable and "true" in editable.lower())
    if is_editable or not modules:
        # An editable or odd install does not list its files; assume it provides its own name.
        modules.add(name.replace("-", "_"))
    return Dist(name=name, version=version, requires=requires, requires_python=requires_python,
                modules=modules, editable=is_editable)


_cache: OrderedDict[tuple, dict[str, Dist]] = OrderedDict()
_cache_lock = threading.Lock()


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _dist_entries(site_packages: list[Path]) -> list[tuple[Path, int]]:
    """The .dist-info folders (with their modification times), in a stable order."""
    found: list[tuple[Path, int]] = []
    for site in site_packages:
        try:
            with os.scandir(site) as it:
                for entry in it:
                    if entry.name.endswith(".dist-info"):
                        try:
                            if entry.is_dir():
                                # A real stat of the path, not entry.stat(): on Windows the time in a
                                # directory listing can lag behind and change between two scans of an
                                # unchanged venv, which made the cache miss every other time.
                                found.append((Path(entry.path), os.stat(entry.path).st_mtime_ns))
                        except OSError:
                            continue
        except OSError:
            continue
    return sorted(found, key=lambda item: item[0].name)


def load_dists(site_packages: list[Path], venv: Path) -> dict[str, Dist]:
    """Reads every installed package once. The result is cached until the list of installed
    packages (names and folder times) changes, so one scan, or repeated live-mode scans, read the
    files a single time instead of once per check."""
    root = venv.resolve()
    entries = _dist_entries(site_packages)[:MAX_DISTS]
    key = (str(root), tuple((str(path), mtime) for path, mtime in entries))
    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None:
            _cache.move_to_end(key)
            return dict(cached)  # a copy: callers can never change what is cached
    if entries:
        with ThreadPoolExecutor(max_workers=min(LOAD_WORKERS, len(entries))) as pool:
            loaded = list(pool.map(lambda item: _load_one(item[0], root), entries))
    else:
        loaded = []
    dists: dict[str, Dist] = {}
    for dist in loaded:
        if dist is not None:
            dists[dist.name] = dist
    with _cache_lock:
        _cache[key] = dists
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)
    return dict(dists)


def module_index(dists: dict[str, Dist]) -> dict[str, set[str]]:
    """import name -> names of the installed distributions that provide it."""
    index: dict[str, set[str]] = {}
    for dist in dists.values():
        for module in dist.modules:
            index.setdefault(module, set()).add(dist.name)
    return index
