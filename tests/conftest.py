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


@pytest.fixture
def make_dist():
    """Write a realistic installed package (METADATA, top_level.txt, RECORD) into a venv."""
    def _make(venv: Path, name: str, version: str, requires=(), modules=(), requires_python: str | None = None,
              with_top_level: bool = True, editable: bool = False) -> None:
        site_dirs = list(venv.glob("lib/python*/site-packages")) + list((venv / "Lib").glob("site-packages"))
        site = site_dirs[0]
        info = site / f"{name.replace('-', '_')}-{version}.dist-info"
        info.mkdir(exist_ok=True)
        lines = ["Metadata-Version: 2.1", f"Name: {name}", f"Version: {version}"]
        if requires_python:
            lines.append(f"Requires-Python: {requires_python}")
        lines += [f"Requires-Dist: {r}" for r in requires]
        (info / "METADATA").write_text("\n".join(lines) + "\n")
        mods = list(modules) or [name.replace("-", "_")]
        if with_top_level:
            (info / "top_level.txt").write_text("\n".join(mods) + "\n")
        (info / "RECORD").write_text("\n".join(f"{m}/__init__.py,sha256=x,1" for m in mods) + "\n")
        if editable:
            (info / "direct_url.json").write_text('{"url":"file:///x","dir_info":{"editable":true}}')
    return _make


@pytest.fixture(autouse=True)
def _no_real_graphics_adapters(monkeypatch):
    """Tests must not depend on the graphics cards of the machine running them."""
    from diagnostics.checks import gpu
    monkeypatch.setattr(gpu, "read_adapters", lambda: [])
