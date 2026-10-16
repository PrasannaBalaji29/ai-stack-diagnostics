"""Reads dependencies from requirements.txt (with -r includes) and pyproject.toml."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement

from diagnostics.safety import read_text, safe_file

try:  # Python 3.11+
    import tomllib
except ImportError:  # pragma: no cover
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        tomllib = None  # type: ignore[assignment]

MAX_INCLUDE_DEPTH = 5
_INCLUDE_RE = re.compile(r"^(?:-r|--requirement)(?:\s+|=)(.+)$")


@dataclass
class RequirementSet:
    requirements: list[Requirement] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)


def _requirement_lines(text: str):
    buffer = ""
    for raw in text.splitlines():
        stripped = raw.rstrip()
        if stripped.endswith("\\"):
            buffer += stripped[:-1] + " "
            continue
        line = (buffer + raw).strip()
        buffer = ""
        line = re.sub(r"\s+#.*$", "", line)
        line = re.split(r"\s+--", line)[0].strip()
        if line and not line.startswith("#"):
            yield line
    if buffer.strip():
        yield buffer.strip()


def _load_file(project: Path, path: Path, seen: set[Path], depth: int, out: RequirementSet) -> None:
    if path in seen:
        return
    seen.add(path)
    if depth > MAX_INCLUDE_DEPTH:
        out.notes.append(f"Stopped following -r includes at depth {MAX_INCLUDE_DEPTH}.")
        return
    text = read_text(path)
    if text is None:
        out.notes.append(f"Could not read {path.name}.")
        return
    out.sources.append(path.name)
    invalid = 0
    for line in _requirement_lines(text):
        include = _INCLUDE_RE.match(line)
        if include:
            target = safe_file(project, path.parent / include.group(1).strip())
            if target is None:
                out.notes.append(f"Skipped include '{include.group(1).strip()}' (missing or outside the project).")
            else:
                _load_file(project, target, seen, depth + 1, out)
            continue
        if line.startswith("-") or line.startswith((".", "/", "~")) or "://" in line.split(" @ ")[0]:
            continue  # -e, -c, --index-url, local paths, bare URLs
        try:
            out.requirements.append(Requirement(line))
        except InvalidRequirement:
            invalid += 1
    if invalid:
        out.notes.append(f"{invalid} line(s) in {path.name} could not be parsed and were skipped.")


def _load_pyproject(project: Path, out: RequirementSet) -> None:
    path = safe_file(project, "pyproject.toml")
    if path is None:
        return
    if tomllib is None:
        out.notes.append("pyproject.toml found but no TOML parser is available (Python 3.11+ or tomli).")
        return
    text = read_text(path)
    try:
        data = tomllib.loads(text or "")
    except Exception:  # noqa: BLE001
        out.notes.append("pyproject.toml could not be parsed.")
        return
    out.sources.append("pyproject.toml")
    for dep in data.get("project", {}).get("dependencies", []) or []:
        try:
            out.requirements.append(Requirement(dep))
        except InvalidRequirement:
            out.notes.append(f"Skipped unparsable pyproject dependency: {dep!r}")
    poetry = data.get("tool", {}).get("poetry", {}).get("dependencies", {}) or {}
    for name in poetry:
        if name.lower() != "python":
            try:
                out.requirements.append(Requirement(name))
            except InvalidRequirement:
                pass


def collect_requirements(project: Path) -> RequirementSet:
    out = RequirementSet()
    req = safe_file(project, "requirements.txt")
    if req is not None:
        _load_file(project, req, set(), 0, out)
    _load_pyproject(project, out)
    return out
