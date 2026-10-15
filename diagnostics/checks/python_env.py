import re
from pathlib import Path

from diagnostics.models import Finding, Severity

CHECK = "python_env"
VENV_NAMES = (".venv", "venv", "env")


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _find_venv(project: Path) -> Path | None:
    for name in VENV_NAMES:
        if (project / name / "pyvenv.cfg").is_file():
            return project / name
    return None


def _venv_python_version(venv: Path) -> str | None:
    text = (venv / "pyvenv.cfg").read_text(encoding="utf-8", errors="ignore")
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if key.strip() in ("version", "version_info"):
            return value.strip()
    return None


def _required_packages(req_file: Path) -> set[str]:
    names = set()
    for raw in req_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.split("#")[0].strip()
        if not line or line.startswith(("-", "git+", "http")):
            continue
        match = re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]*", line)
        if match:
            names.add(_normalize(match.group(0)))
    return names


def _installed_packages(venv: Path) -> set[str]:
    site = venv / "Lib" / "site-packages"  # Windows layout
    if not site.is_dir():
        found = list((venv / "lib").glob("python*/site-packages"))  # Linux/Mac
        site = found[0] if found else None
    names = set()
    if site:
        for dist in site.glob("*.dist-info"):
            match = re.match(r"^(.+?)-\d", dist.name)
            if match:
                names.add(_normalize(match.group(1)))
    return names


def run(project: Path) -> list[Finding]:
    venv = _find_venv(project)
    if venv is None:
        return [Finding(
            check=CHECK, severity=Severity.warning,
            title="No virtual environment found",
            detail="Looked for .venv, venv and env inside the project folder.",
            fix="python -m venv .venv",
        )]

    findings = [Finding(
        check=CHECK, severity=Severity.ok,
        title=f"Virtual environment found ({venv.name})",
        detail=f"Python version: {_venv_python_version(venv) or 'unknown'}",
    )]

    req_file = project / "requirements.txt"
    if not req_file.is_file():
        findings.append(Finding(
            check=CHECK, severity=Severity.info,
            title="No requirements.txt",
            detail="Cannot compare installed packages without one.",
            fix="pip freeze > requirements.txt",
        ))
        return findings

    missing = sorted(_required_packages(req_file) - _installed_packages(venv))
    if missing:
        findings.append(Finding(
            check=CHECK, severity=Severity.critical,
            title=f"{len(missing)} package(s) not installed",
            detail="Missing from the venv: " + ", ".join(missing),
            fix="pip install -r requirements.txt",
        ))
    else:
        findings.append(Finding(
            check=CHECK, severity=Severity.ok,
            title="All requirements are installed",
            detail="Every package in requirements.txt is in the venv.",
        ))
    return findings