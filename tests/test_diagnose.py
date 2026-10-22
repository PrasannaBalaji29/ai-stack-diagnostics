import json

import pytest

import server
from diagnostics import diagnose as D
from diagnostics.models import Finding, Severity


def write(project, name, text):
    (project / name).write_text(text, encoding="utf-8")


def test_clean_project_is_healthy(project, make_venv, make_dist):
    venv = make_venv(project, version="3.13.0")
    make_dist(venv, "requests", "2.32.0")
    write(project, "app.py", "import requests\nimport os\n")
    write(project, "requirements.txt", "requests\n")
    r = D.diagnose(project)
    assert r["status"] == "healthy" and r["score"] == 100 and r["fix_first"] == []
    assert "compat" in r["checks_run"] and "imports" in r["checks_run"]
    assert r["passed"]


def test_broken_project_orders_problems_by_severity(project, make_venv):
    make_venv(project, version="3.9.7")
    write(project, "pyproject.toml", '[project]\nname="x"\nrequires-python=">=3.10"\n')
    write(project, "app.py", "import cv2\n")
    r = D.diagnose(project)
    assert r["status"] == "broken"
    steps = r["fix_first"]
    assert [s["step"] for s in steps] == list(range(1, len(steps) + 1))
    severities = [s["severity"] for s in steps]
    assert severities == sorted(severities, key=lambda x: ["critical", "error", "warning"].index(x))
    assert steps[0]["check"] == "compat"  # Python version problem comes before library problems
    assert any("root problem" in n for n in r["notes"])


def test_score_and_status_rules():
    assert D._status({"critical": 0, "error": 0, "warning": 0}) == "healthy"
    assert D._status({"critical": 0, "error": 0, "warning": 1}) == "needs attention"
    assert D._status({"critical": 0, "error": 1, "warning": 0}) == "needs attention"
    assert D._status({"critical": 1, "error": 0, "warning": 0}) == "broken"
    assert D._score({"critical": 1, "error": 1, "warning": 1}) == 50
    assert D._score({"critical": 9, "error": 9, "warning": 9}) == 0


def test_a_crashing_check_never_breaks_the_report(project, make_venv, monkeypatch):
    make_venv(project)
    write(project, "app.py", "import os\n")

    def boom(p, s):
        raise RuntimeError("kaboom")

    monkeypatch.setitem(D._RUNNERS, "check_imports", ("imports", boom))
    r = D.diagnose(project)
    assert any("Check failed unexpectedly" in s["title"] for s in r["fix_first"])
    assert "compat" in r["checks_run"]  # the other checks still ran
    json.dumps(r)


def test_a_crashing_scan_falls_back(project, make_venv, monkeypatch):
    make_venv(project)

    def boom(p):
        raise RuntimeError("scan exploded")

    monkeypatch.setattr(D, "scan", boom)
    r = D.diagnose(project)
    assert r["checks_run"] == ["python_env"] and r["status"] in ("healthy", "needs attention", "broken")


def test_no_venv_project(project):
    write(project, "app.py", "import os\n")
    r = D.diagnose(project)
    assert r["checks_run"] == ["python_env", "imports"]
    assert r["status"] in ("healthy", "needs attention", "broken")


def test_empty_folder(project):
    r = D.diagnose(project)
    assert r["checks_run"] == ["python_env"]
    json.dumps(r)


def test_torch_project_runs_gpu_check(project, make_venv, make_dist, monkeypatch):
    from diagnostics.checks import gpu
    monkeypatch.setattr(gpu, "read_gpu", lambda *a, **k: gpu.GpuInfo(state="none"))
    venv = make_venv(project, version="3.13.0")
    make_dist(venv, "torch", "2.5.1+cpu")
    write(project, "requirements.txt", "torch\n")
    r = D.diagnose(project)
    assert "gpu" in r["checks_run"]
    assert r["checks_run"].index("gpu") == len(r["checks_run"]) - 1  # hardware is last


def test_many_problems_are_capped_with_a_note(project, monkeypatch):
    many = [Finding(check="imports", severity=Severity.warning, title=f"problem {i}", detail=f"d{i}") for i in range(30)]
    monkeypatch.setitem(D._RUNNERS, "check_python_env", ("python_env", lambda p, s: many))
    r = D.diagnose(project)
    assert len(r["fix_first"]) == D.MAX_FIX_STEPS
    assert any("lower-priority" in n for n in r["notes"])
    assert r["counts"]["warning"] == 30 and r["score"] == 0


def test_duplicate_findings_are_merged(project, monkeypatch):
    twin = Finding(check="imports", severity=Severity.warning, title="same", detail="same")
    monkeypatch.setitem(D._RUNNERS, "check_python_env", ("python_env", lambda p, s: [twin, twin, twin]))
    assert D.diagnose(project)["counts"]["warning"] == 1


def test_report_is_deterministic_and_json_safe(project, make_venv):
    make_venv(project, version="3.9.7")
    write(project, "app.py", "import cv2\nimport yaml\n")
    first, second = D.diagnose(project), D.diagnose(project)
    assert first == second
    json.dumps(first)


def test_never_modifies_the_project(project, make_venv):
    make_venv(project)
    write(project, "app.py", "import cv2\n")
    write(project, ".env", "OPENAI_API_KEY=sk-super-secret-1234567890\n")
    before = sorted((str(p), p.stat().st_mtime_ns) for p in project.rglob("*") if p.is_file())
    out = D.diagnose(project)
    after = sorted((str(p), p.stat().st_mtime_ns) for p in project.rglob("*") if p.is_file())
    assert before == after and "sk-super-secret" not in json.dumps(out)


@pytest.mark.parametrize("bad", ["", "/definitely/not/here", "\x00", "a" * 5000])
def test_server_tool_bad_paths(bad):
    out = server.diagnose(bad)
    assert "error" in out and isinstance(out["error"], str)


def test_server_tool_registered_and_works(project, make_venv):
    make_venv(project)
    out = server.diagnose(str(project))
    assert out["status"] in ("healthy", "needs attention", "broken") and "fix_first" in out


def test_by_check_groups_findings_with_worst_status(project, make_venv):
    make_venv(project, version="3.9.7")
    write(project, "pyproject.toml", '[project]\nname="x"\nrequires-python=">=3.10"\n')
    write(project, "app.py", "import cv2\n")
    r = D.diagnose(project)
    assert r["all_checks"] == ["compat", "python_env", "imports", "env_keys", "ollama", "ports", "gpu"]
    assert set(r["by_check"]) == set(r["checks_run"])
    assert r["by_check"]["compat"]["status"] == "critical"
    assert r["by_check"]["imports"]["status"] == "critical"
    for entry in r["by_check"].values():
        assert entry["findings"] and set(entry["findings"][0]) == {"severity", "title", "detail", "fix"}


def test_healthy_project_by_check_all_ok(project, make_venv, make_dist):
    venv = make_venv(project, version="3.13.0")
    make_dist(venv, "requests", "2.32.0")
    write(project, "app.py", "import requests\n")
    write(project, "requirements.txt", "requests\n")
    r = D.diagnose(project)
    assert {v["status"] for v in r["by_check"].values()} <= {"ok", "info"}  # nothing needs fixing
