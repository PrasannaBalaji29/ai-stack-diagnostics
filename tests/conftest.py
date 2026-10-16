import sys
from pathlib import Path

import pytest


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def make_venv():
    def _make(root: Path, name: str = ".venv", packages: dict[str, str] | None = None,
              version: str = "3.13.0", home: str | None = None, windows_layout: bool = False) -> Path:
        venv = root / name
        venv.mkdir()
        (venv / "pyvenv.cfg").write_text(f"home = {home or sys.prefix}\nversion = {version}\n")
        if windows_layout:
            site = venv / "Lib" / "site-packages"
            (venv / "Scripts").mkdir()
            (venv / "Scripts" / "python.exe").write_text("")
        else:
            site = venv / "lib" / "python3.13" / "site-packages"
            (venv / "bin").mkdir()
            (venv / "bin" / "python").write_text("")
        site.mkdir(parents=True)
        for pkg, ver in (packages or {}).items():
            (site / f"{pkg.replace('-', '_')}-{ver}.dist-info").mkdir()
        return venv
    return _make


def titles(findings):
    return [f.title for f in findings]


def by_severity(findings, severity):
    return [f for f in findings if f.severity.value == severity]
