"""Checks the project's virtual environment against its declared requirements."""

from __future__ import annotations

import re
from pathlib import Path

from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from diagnostics.models import Finding, Severity
from diagnostics.requirements import collect_requirements
from diagnostics.safety import read_text

CHECK = "python_env"
PREFERRED_VENV_NAMES = (".venv", "venv", "env")
_DIST_RE = re.compile(r"^(?P<name>.+?)-(?P<version>\d[^-]*?)\.(?:dist-info|egg-info)$", re.IGNORECASE)


def _find_venvs(project: Path) -> list[Path]:
    try:
        candidates = [p for p in project.iterdir() if p.is_dir() and (p / "pyvenv.cfg").is_file()]
    except OSError:
        return []
    rank = {name: i for i, name in enumerate(PREFERRED_VENV_NAMES)}
    return sorted(candidates, key=lambda p: (rank.get(p.name, 99), p.name))


def _read_cfg(venv: Path) -> dict[str, str]:
    cfg = {}
    for line in (read_text(venv / "pyvenv.cfg") or "").splitlines():
        key, sep, value = line.partition("=")
        if sep:
            cfg[key.strip().lower()] = value.strip()
    return cfg


def _python_version(cfg: dict[str, str]) -> str | None:
    return cfg.get("version") or cfg.get("version_info")


def _marker_env(version: str | None) -> dict[str, str]:
    match = re.match(r"(\d+)\.(\d+)(?:\.(\d+))?", version or "")
    if not match:
        return {}
    major, minor, patch = match.group(1), match.group(2), match.group(3) or "0"
    return {"python_version": f"{major}.{minor}", "python_full_version": f"{major}.{minor}.{patch}"}


def _site_packages(venv: Path) -> list[Path]:
    found = [venv / "Lib" / "site-packages"]  # Windows
    for pattern in ("lib/python*/site-packages", "lib64/python*/site-packages"):  # Linux / macOS
        found.extend(venv.glob(pattern))
    unique, seen = [], set()
    for path in found:
        if path.is_dir() and path.resolve() not in seen:
            seen.add(path.resolve())
            unique.append(path)
    return unique


def _installed(venv: Path) -> dict[str, Version | None]:
    packages: dict[str, Version | None] = {}
    for site in _site_packages(venv):
        try:
            entries = list(site.iterdir())
        except OSError:
            continue
        for entry in entries:
            match = _DIST_RE.match(entry.name)
            if not match:
                continue
            try:
                version: Version | None = Version(match.group("version"))
            except InvalidVersion:
                version = None
            packages[str(canonicalize_name(match.group("name")))] = version
    return packages


def _has_interpreter(venv: Path) -> bool:
    return any((venv / sub).is_file() for sub in ("Scripts/python.exe", "bin/python", "bin/python3"))


def run(project: Path) -> list[Finding]:
    venvs = _find_venvs(project)
    reqs = collect_requirements(project)
    findings: list[Finding] = []

    if not venvs:
        findings.append(Finding(
            check=CHECK, severity=Severity.warning, title="No virtual environment found",
            detail="Looked for a folder with pyvenv.cfg inside the project (.venv, venv, env, ...). "
                   "Conda environments are not inspected.",
            fix="python -m venv .venv",
        ))
        if reqs.notes:
            findings.append(Finding(check=CHECK, severity=Severity.info, title="Requirement notes", detail=" ".join(reqs.notes)))
        return findings

    venv = venvs[0]
    cfg = _read_cfg(venv)
    version = _python_version(cfg)
    findings.append(Finding(
        check=CHECK, severity=Severity.ok, title=f"Virtual environment found ({venv.name})",
        detail=f"Python version: {version or 'unknown'}"
               + (f". Other environments also present: {', '.join(v.name for v in venvs[1:])}" if len(venvs) > 1 else ""),
    ))

    home = cfg.get("home")
    if home and not Path(home).is_dir():
        findings.append(Finding(
            check=CHECK, severity=Severity.critical, title="Virtual environment points to a missing Python",
            detail=f"The base Python it was created from no longer exists: {home}. "
                   "This happens after uninstalling or upgrading Python.",
            fix=f"python -m venv --clear {venv.name}",
        ))
    elif not _has_interpreter(venv):
        findings.append(Finding(
            check=CHECK, severity=Severity.warning, title="Virtual environment has no Python executable",
            detail="It may have been created on another OS or is incomplete.",
            fix=f"python -m venv --clear {venv.name}",
        ))

    if not reqs.requirements:
        detail = "No requirements.txt or pyproject.toml dependencies were found, so installed packages cannot be compared."
        if reqs.notes:
            detail += " " + " ".join(reqs.notes)
        findings.append(Finding(check=CHECK, severity=Severity.info, title="No declared requirements", detail=detail, fix="pip freeze > requirements.txt"))
        return findings

    marker_env = _marker_env(version)
    installed = _installed(venv)
    missing: list[str] = []
    mismatched: list[str] = []
    checked = 0
    for req in reqs.requirements:
        if req.marker is not None:
            try:
                if not req.marker.evaluate(marker_env):
                    continue
            except Exception:  # noqa: BLE001 - if unsure, still check the package
                pass
        checked += 1
        name = str(canonicalize_name(req.name))
        if name not in installed:
            missing.append(req.name)
            continue
        have = installed[name]
        if have is not None and not req.url and len(req.specifier) and not req.specifier.contains(have, prereleases=True):
            mismatched.append(f"{req.name} (installed {have}, needs {req.specifier})")

    if missing:
        findings.append(Finding(
            check=CHECK, severity=Severity.critical, title=f"{len(missing)} package(s) not installed",
            detail="Missing from the venv: " + ", ".join(sorted(set(missing), key=str.lower)),
            fix="pip install -r requirements.txt",
        ))
    if mismatched:
        findings.append(Finding(
            check=CHECK, severity=Severity.warning, title=f"{len(mismatched)} package(s) at the wrong version",
            detail="; ".join(sorted(mismatched)), fix="pip install -r requirements.txt",
        ))
    if not missing and not mismatched:
        findings.append(Finding(
            check=CHECK, severity=Severity.ok, title="All requirements are installed",
            detail=f"{checked} requirement(s) checked from {', '.join(reqs.sources)}.",
        ))
    if reqs.notes:
        findings.append(Finding(check=CHECK, severity=Severity.info, title="Requirement notes", detail=" ".join(reqs.notes)))
    return findings
