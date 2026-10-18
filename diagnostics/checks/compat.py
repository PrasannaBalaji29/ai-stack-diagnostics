"""Compatibility checks for the project's virtual environment. Read-only.

1. Python version: does the venv's Python satisfy the project's `requires-python`?
2. Do all installed packages support that Python version?
3. Do installed packages conflict with each other, or lack a dependency (like `pip check`)?
"""

from __future__ import annotations

from pathlib import Path

from packaging.utils import canonicalize_name
from packaging.version import Version

from diagnostics.checks.python_env import _find_venvs, _read_cfg, _site_packages
from diagnostics.models import Finding, Severity
from diagnostics.requirements import read_requires_python
from diagnostics.venvinfo import Dist, load_dists, marker_environment, python_version_of

CHECK = "compat"
MAX_LISTED = 20


def _versions(py: tuple[int, int, int]) -> Version:
    return Version(f"{py[0]}.{py[1]}.{py[2]}")


def _python_findings(project: Path, venv: Path, py: tuple[int, int, int]) -> list[Finding]:
    spec = read_requires_python(project)
    label = f"{py[0]}.{py[1]}.{py[2]}"
    if spec is None:
        return [Finding(check=CHECK, severity=Severity.info, title=f"Venv uses Python {label}",
                        detail="The project does not declare a supported Python version (requires-python).")]
    if spec.contains(_versions(py), prereleases=True):
        return [Finding(check=CHECK, severity=Severity.ok, title="Venv Python matches the project",
                        detail=f"The venv uses Python {label}; the project requires {spec}.")]
    return [Finding(
        check=CHECK, severity=Severity.critical, title="Venv Python version does not match the project",
        detail=f"The venv uses Python {label} but the project requires {spec}.",
        fix=f"Create a new venv with a supported Python, for example: python -m venv --clear {venv.name}",
    )]


def _unsupported(dists: dict[str, Dist], py: tuple[int, int, int]) -> list[str]:
    version = _versions(py)
    bad = []
    for dist in dists.values():
        if dist.requires_python is not None and not dist.requires_python.contains(version, prereleases=True):
            bad.append(f"{dist.name} {dist.version or ''} (needs Python {dist.requires_python})".replace("  ", " "))
    return sorted(bad)


def _conflicts(dists: dict[str, Dist], env: dict[str, str]) -> tuple[list[str], list[str]]:
    conflicts: list[str] = []
    missing: list[str] = []
    for dist in dists.values():
        for req in dist.requires:
            try:
                if req.marker is not None and not req.marker.evaluate(env):
                    continue
            except Exception:  # noqa: BLE001, S112 - odd marker: skip rather than guess
                continue
            target = dists.get(_canon(req.name))
            if target is None:
                missing.append(f"{dist.name} needs {req.name}")
            elif target.version is not None and req.specifier and not req.specifier.contains(target.version, prereleases=True):
                conflicts.append(f"{dist.name} needs {req.name}{req.specifier} but {target.version} is installed")
    return sorted(set(conflicts)), sorted(set(missing))


def _canon(name: str) -> str:
    return str(canonicalize_name(name))


def _listing(items: list[str]) -> str:
    shown = items[:MAX_LISTED]
    extra = f" (+{len(items) - len(shown)} more)" if len(items) > len(shown) else ""
    return "; ".join(shown) + extra


def run(project: Path) -> list[Finding]:
    venvs = _find_venvs(project)
    if not venvs:
        return [Finding(check=CHECK, severity=Severity.info, title="No virtual environment found",
                        detail="Compatibility can only be checked against a venv inside the project folder "
                               "(.venv, venv or env).",
                        fix="python -m venv .venv")]
    venv = venvs[0]
    py = python_version_of(_read_cfg(venv))
    dists = load_dists(_site_packages(venv), venv)
    findings: list[Finding] = []

    if py is None:
        findings.append(Finding(check=CHECK, severity=Severity.info, title="Venv Python version unknown",
                                detail="pyvenv.cfg does not say which Python version the venv uses."))
    else:
        findings.extend(_python_findings(project, venv, py))
        bad = _unsupported(dists, py)
        if bad:
            findings.append(Finding(
                check=CHECK, severity=Severity.critical,
                title=f"{len(bad)} installed package(s) do not support Python {py[0]}.{py[1]}",
                detail=_listing(bad),
                fix="Use a Python version these packages support, or upgrade the packages.",
            ))

    if not dists:
        findings.append(Finding(check=CHECK, severity=Severity.info, title="No installed packages found",
                                detail=f"The {venv.name} environment has no installed packages to compare."))
        return findings

    conflicts, missing = _conflicts(dists, marker_environment(py))
    if conflicts:
        findings.append(Finding(
            check=CHECK, severity=Severity.error, title=f"{len(conflicts)} package version conflict(s)",
            detail=_listing(conflicts),
            fix="pip install --upgrade <package>  (or pin a version both sides accept). Run pip check to confirm.",
        ))
    if missing:
        findings.append(Finding(
            check=CHECK, severity=Severity.error, title=f"{len(missing)} dependency(ies) missing",
            detail=_listing(missing), fix="pip install -r requirements.txt",
        ))
    if not conflicts and not missing:
        findings.append(Finding(check=CHECK, severity=Severity.ok, title="No package conflicts found",
                                detail=f"Checked {len(dists)} installed package(s)."))
    return findings
