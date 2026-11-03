"""The demo projects must always give a clear green and a clear red result."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from diagnostics.diagnose import diagnose

SCRIPT = Path(__file__).resolve().parent.parent / "demos" / "make_demos.py"


def _build(tmp_path: Path):
    spec = importlib.util.spec_from_file_location("make_demos", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.OUT = tmp_path / "ai-stack-demos"
    module.build_healthy()
    module.build_broken()
    return module.OUT


def test_healthy_demo_is_clean(tmp_path):
    result = diagnose((_build(tmp_path) / "demo-healthy").resolve())
    assert result["score"] == 100
    assert result["status"] == "healthy"
    assert not result["fix_first"]


def test_broken_demo_has_the_three_planted_problems(tmp_path):
    result = diagnose((_build(tmp_path) / "demo-broken").resolve())
    titles = " | ".join(item["title"] for item in result["fix_first"])
    assert result["status"] == "broken"
    assert "Venv Python version does not match" in titles
    assert "not installed" in titles
    assert "missing from .env" in titles


def test_default_output_is_outside_the_repo():
    spec = importlib.util.spec_from_file_location("make_demos", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    repo = SCRIPT.parent.parent
    assert repo not in module.OUT.resolve().parents and module.OUT.resolve() != repo
