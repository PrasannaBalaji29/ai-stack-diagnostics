"""Compares the libraries the code imports with what is installed and what is declared.

Finds: imported but not installed, used but missing from requirements, and standard-library
modules that were removed in newer Python versions (distutils, imp, cgi, ...). Read-only.
"""

from __future__ import annotations

import re
from pathlib import Path

from packaging.utils import canonicalize_name

from diagnostics.checks.python_env import _find_venvs, _read_cfg, _site_packages
from diagnostics.models import Finding, Severity
from diagnostics.requirements import collect_requirements
from diagnostics.safety import read_text
from diagnostics.scan import (
    REMOVED_STDLIB,
    STDLIB_NAMES,
    _notebook_source,
    _walk_sources,
    raw_imports,
)
from diagnostics.venvinfo import load_dists, module_index, python_version_of

CHECK = "imports"
MAX_LISTED = 25
_IDENT_RE = re.compile(r"^[A-Za-z0-9_]+$")
# import name -> PyPI package name, for the well-known cases where they differ.
PYPI_NAMES = {
    "cv2": "opencv-python", "PIL": "pillow", "sklearn": "scikit-learn", "yaml": "pyyaml",
    "bs4": "beautifulsoup4", "dotenv": "python-dotenv", "skimage": "scikit-image", "attr": "attrs",
    "serial": "pyserial", "dateutil": "python-dateutil", "jwt": "pyjwt", "fitz": "pymupdf",
    "docx": "python-docx", "pptx": "python-pptx", "magic": "python-magic", "git": "gitpython",
    "grpc": "grpcio", "Crypto": "pycryptodome", "OpenSSL": "pyopenssl", "psycopg2": "psycopg2-binary",
    "MySQLdb": "mysqlclient", "zmq": "pyzmq", "win32com": "pywin32", "usb": "pyusb",
}
_TOOLING = {"setuptools", "pip", "wheel", "pkg_resources", "_distutils_hack"}


def _listing(items: list[str]) -> str:
    shown = items[:MAX_LISTED]
    extra = f" (+{len(items) - len(shown)} more)" if len(items) > len(shown) else ""
    return ", ".join(shown) + extra


def _local_names(project: Path, files: list[Path]) -> set[str]:
    names: set[str] = set()
    try:
        for entry in project.iterdir():
            names.add(entry.stem if entry.is_file() else entry.name)
    except OSError:
        pass
    for path in files:
        names.add(path.stem)
        try:
            for part in path.relative_to(project).parts[:-1]:
                names.add(part)
        except ValueError:
            continue
    return {n.lower() for n in names}


def _pip_names(modules: list[str]) -> list[str]:
    out = []
    for module in modules:
        name = PYPI_NAMES.get(module, module)
        if _IDENT_RE.match(name.replace("-", "_")):  # names come from untrusted code: only safe characters
            out.append(name)
    return out


def run(project: Path) -> list[Finding]:
    py_files, notebooks, truncated = _walk_sources(project)
    if not py_files and not notebooks:
        return [Finding(check=CHECK, severity=Severity.info, title="No Python files to check",
                        detail="No .py or .ipynb files were found in this folder.")]

    found: dict[str, bool] = {}
    for path in py_files:
        for name, only_optional in raw_imports(read_text(path) or "").items():
            found[name] = found.get(name, True) and only_optional
    for path in notebooks:
        for name, only_optional in raw_imports(_notebook_source(path)).items():
            found[name] = found.get(name, True) and only_optional

    local = _local_names(project, [*py_files, *notebooks])
    reqs = collect_requirements(project)
    declared = {str(canonicalize_name(r.name)) for r in reqs.requirements}

    venvs = _find_venvs(project)
    dists = {}
    py_version = None
    if venvs:
        venv = venvs[0]
        dists = load_dists(_site_packages(venv), venv)
        cfg = _read_cfg(venv)
        py_version = python_version_of(cfg)
    index = module_index(dists)

    findings: list[Finding] = []
    if truncated:
        findings.append(Finding(check=CHECK, severity=Severity.info, title="Only part of the project was scanned",
                                detail="The project is large, so only the first files were read."))

    # 1. Standard-library modules that no longer exist in the venv's Python.
    if py_version:
        gone = sorted(
            f"{m} (removed in Python {REMOVED_STDLIB[m][0]}.{REMOVED_STDLIB[m][1]})"
            for m in found
            if m in REMOVED_STDLIB and py_version[:2] >= REMOVED_STDLIB[m]
            and not (m == "distutils" and "setuptools" in dists)  # setuptools still provides distutils
            and m not in index
        )
        if gone:
            findings.append(Finding(
                check=CHECK, severity=Severity.critical, title="Code uses standard-library modules removed from this Python",
                detail=f"The venv uses Python {py_version[0]}.{py_version[1]}. Imports that will fail: {_listing(gone)}.",
                fix="Replace them (for example importlib instead of imp), or use an older Python in the venv.",
            ))

    third_party = sorted(m for m in found if m not in STDLIB_NAMES and m not in REMOVED_STDLIB and m.lower() not in local)
    removed_but_used = {m for m in found if m in REMOVED_STDLIB}
    third_party = [m for m in third_party if m not in removed_but_used]

    if not venvs:
        undeclared = sorted(m for m in third_party
                            if str(canonicalize_name(PYPI_NAMES.get(m, m))) not in declared and m not in _TOOLING)
        findings.append(Finding(check=CHECK, severity=Severity.info, title="No virtual environment found",
                                detail="Installed libraries could not be checked, so imports were compared with the "
                                       "requirements list only."))
        if undeclared and declared:
            findings.append(_undeclared_finding(undeclared, {}))
        return findings

    unresolved = [m for m in third_party if m not in index]
    # A library that is declared but not installed is already reported by check_python_env.
    unresolved = [m for m in unresolved if str(canonicalize_name(PYPI_NAMES.get(m, m))) not in declared]
    required = [m for m in unresolved if not found[m]]
    optional = [m for m in unresolved if found[m]]

    if required:
        fix_names = _pip_names(required)
        findings.append(Finding(
            check=CHECK, severity=Severity.critical,
            title=f"{len(required)} imported librar{'y is' if len(required) == 1 else 'ies are'} not installed",
            detail=f"The code imports these, but nothing in the venv provides them: {_listing(required)}. "
                   "The pip package name can differ from the import name, so confirm it on pypi.org.",
            fix=("pip install " + " ".join(fix_names)) if fix_names else None,
        ))
    if optional:
        findings.append(Finding(
            check=CHECK, severity=Severity.info, title=f"{len(optional)} optional import(s) are not installed",
            detail=f"Imported inside try/except ImportError, so the code handles them being absent: {_listing(optional)}.",
        ))

    resolved = [m for m in third_party if m in index and m not in _TOOLING]
    undeclared = sorted(m for m in resolved if not (index[m] & declared))
    if undeclared and declared:
        findings.append(_undeclared_finding(undeclared, index))
    elif undeclared and not declared:
        findings.append(Finding(
            check=CHECK, severity=Severity.info, title="No requirements file lists the libraries in use",
            detail="There is no requirements.txt or pyproject.toml dependency list, so others cannot reproduce this setup.",
            fix="pip freeze > requirements.txt  (then remove packages you do not use)",
        ))

    if not any(f.severity in (Severity.critical, Severity.warning) for f in findings):
        findings.append(Finding(check=CHECK, severity=Severity.ok, title="Imports match the installed libraries",
                                detail=f"Checked {len(third_party)} third-party import(s) against {len(dists)} installed package(s)."))
    return findings


def _undeclared_finding(undeclared: list[str], index: dict[str, set[str]]) -> Finding:
    pairs = []
    for module in undeclared:
        providers = sorted(index.get(module, set()))
        pairs.append(f"{module} ({providers[0]})" if providers and providers[0] != module else module)
    names = sorted({(sorted(index.get(m, set())) or [PYPI_NAMES.get(m, m)])[0] for m in undeclared})
    return Finding(
        check=CHECK, severity=Severity.warning,
        title=f"{len(undeclared)} library(ies) used in code but missing from requirements",
        detail=f"These work on this machine but someone else installing from the requirements will hit errors: {_listing(pairs)}.",
        fix="Add to requirements.txt: " + ", ".join(names[:MAX_LISTED]),
    )
