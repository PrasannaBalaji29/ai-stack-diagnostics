"""Fuzzing: random and hostile input must never crash a parser, hang it, or escape the project."""

import importlib
import json
import pkgutil
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

import diagnostics  # noqa: E402
from diagnostics import venvinfo  # noqa: E402
from diagnostics.diagnose import diagnose  # noqa: E402
from diagnostics.requirements import collect_requirements, read_requires_python  # noqa: E402
from diagnostics.safety import ProjectPathError, read_text, resolve_project_path  # noqa: E402
from diagnostics.scan import raw_imports  # noqa: E402

FUZZ = settings(max_examples=150, deadline=3000, suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
text = st.text(max_size=400)
blobs = st.binary(max_size=600)


@FUZZ
@given(text)
def test_resolve_project_path_only_returns_a_path_or_a_clean_error(value):
    try:
        assert isinstance(resolve_project_path(value), Path)
    except ProjectPathError:
        pass


@FUZZ
@given(text)
def test_raw_imports_never_raises(source):
    assert isinstance(raw_imports(source), dict)


@FUZZ
@given(st.lists(st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=60), max_size=40))
def test_record_parser_never_raises(rows):
    assert isinstance(venvinfo._modules_from_record("\n".join(rows)), set)


@FUZZ
@given(st.dictionaries(text, text, max_size=6))
def test_python_version_reader_never_raises(cfg):
    venvinfo.python_version_of(cfg)


@FUZZ
@given(blobs, blobs)
def test_requirement_readers_survive_binary_garbage(req_bytes, toml_bytes):
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp)
        (project / "requirements.txt").write_bytes(req_bytes)
        (project / "pyproject.toml").write_bytes(toml_bytes)
        collect_requirements(project)
        read_requires_python(project)


@FUZZ
@given(blobs, blobs, blobs)
def test_full_diagnosis_survives_garbage_files(req_bytes, toml_bytes, py_bytes):
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp)
        (project / "requirements.txt").write_bytes(req_bytes)
        (project / "pyproject.toml").write_bytes(toml_bytes)
        (project / "app.py").write_bytes(py_bytes)
        (project / ".env").write_bytes(req_bytes)
        (project / ".env.example").write_bytes(toml_bytes)
        report = diagnose(project)
        json.dumps(report)
        assert report["status"] in ("healthy", "needs attention", "broken")


@FUZZ
@given(blobs)
def test_read_text_never_raises_on_binary(data):
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "x.txt"
        f.write_bytes(data)
        assert read_text(f) is None or isinstance(read_text(f), str)


# ---- ReDoS: every compiled regex in the package must stay fast on adversarial input

def _all_patterns():
    found = []
    for module_info in pkgutil.walk_packages(diagnostics.__path__, "diagnostics."):
        module = importlib.import_module(module_info.name)
        for name, value in vars(module).items():
            if isinstance(value, re.Pattern):
                found.append((f"{module_info.name}.{name}", value))
    return found


CHILD = r"""
import importlib, sys, time
mod, attr = sys.argv[1], sys.argv[2]
pattern = getattr(importlib.import_module(mod), attr)
bases = ["a", "1", "-", ".", "a-", "1.", "(", "a=", " ", "\t", "=", "cu1", "a-1", "0.", "x,", "[", "_", "A"]
sizes = [28, 5000, 60000]
tails = ["!", "\x00", "\n!", " !", "-", ":", ","]
evil = [b * (n // len(b)) + t for b in bases for n in sizes for t in tails]
evil += ["CUDA Version: " + "9" * 60000 + "x", "cu" + "1" * 60000 + "x", "a-1" + "a" * 60000 + ".dist-info!"]
worst = 0.0
for text in evil:
    start = time.perf_counter()
    pattern.search(text); pattern.match(text); pattern.fullmatch(text)
    worst = max(worst, time.perf_counter() - start)
print(worst)
"""
TIME_LIMIT = 20  # seconds for ALL adversarial inputs against one regex (normal regexes need well under 1s)


def test_every_regex_in_the_project_is_found():
    assert len(_all_patterns()) >= 5  # guards against this test silently checking nothing


@pytest.mark.parametrize("name,pattern", _all_patterns(), ids=lambda v: v if isinstance(v, str) else "")
def test_no_regex_can_be_made_to_hang(name, pattern):
    module, _, attr = name.rpartition(".")
    try:
        done = subprocess.run([sys.executable, "-c", CHILD, module, attr], capture_output=True, text=True, timeout=TIME_LIMIT, check=True)
    except subprocess.TimeoutExpired:
        pytest.fail(f"{name} hangs on adversarial input (ReDoS)")
    assert float(done.stdout.strip()) < 2.0, f"{name} is slow on adversarial input"
