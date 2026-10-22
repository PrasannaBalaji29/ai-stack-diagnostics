import json
import subprocess
import sys

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from dashboard import app as appmod  # noqa: E402
from dashboard import folder_dialog  # noqa: E402
from diagnostics import diagnose as D  # noqa: E402

TOKEN = "stream-token"
BASE = "http://127.0.0.1:8765"
HEADERS = {"X-Dashboard-Token": TOKEN}


@pytest.fixture
def client():
    return TestClient(appmod.create_app(token=TOKEN), base_url=BASE)


def stream(client, path, headers=HEADERS):
    r = client.post("/api/diagnose/stream", json={"path": path}, headers=headers)
    return r, [json.loads(line) for line in r.text.splitlines() if line.strip()] if r.headers.get("content-type", "").startswith("application/x-ndjson") else []


# ---- diagnose_events (the source of the stream)

def test_events_come_in_a_sensible_order(project, make_venv):
    make_venv(project)
    (project / "app.py").write_text("import os\n")
    events = list(D.diagnose_events(project))
    kinds = [e["type"] for e in events]
    assert kinds[0] == "start" and kinds[1] == "scanned" and kinds[-1] == "result"
    starts = [e["check"] for e in events if e["type"] == "check_start"]
    dones = [e["check"] for e in events if e["type"] == "check_done"]
    assert starts == dones == events[1]["checks"] and starts
    for i, e in enumerate(events):
        if e["type"] == "check_done":
            assert events[i - 1]["type"] == "check_start" and events[i - 1]["check"] == e["check"]
            assert isinstance(e["ms"], int) and e["ms"] >= 0 and e["status"] in ("ok", "info", "warning", "error", "critical")
    assert all(e["t"].endswith("+00:00") or "T" in e["t"] for e in events)
    json.dumps(events)


def test_event_status_matches_the_final_report(project, make_venv):
    make_venv(project, version="3.9.7")
    (project / "pyproject.toml").write_text('[project]\nname="x"\nrequires-python=">=3.10"\n')
    events = list(D.diagnose_events(project))
    final = events[-1]["result"]
    for e in events:
        if e["type"] == "check_done":
            assert final["by_check"][e["check"]]["status"] == e["status"]


def test_diagnose_still_returns_the_plain_report(project, make_venv):
    make_venv(project)
    assert D.diagnose(project)["status"] in ("healthy", "needs attention", "broken")


def test_facts_describe_the_project(project, make_venv, make_dist):
    venv = make_venv(project, version="3.13.0")
    make_dist(venv, "requests", "2.32.0")
    make_dist(venv, "numpy", "2.1.0")
    (project / "app.py").write_text("import requests\nimport cv2\n")
    facts = D.diagnose(project)["facts"]
    assert facts["venv_python"] == "3.13.0" and facts["installed_packages"] == 2 and facts["has_venv"] is True
    assert facts["venv_name"] == ".venv" and facts["python_files_scanned"] == 1 and "cv2" in facts["other_libraries"]


def test_facts_without_a_venv(project):
    facts = D.diagnose(project)["facts"]
    assert facts["has_venv"] is False and facts["venv_python"] is None and facts["installed_packages"] is None


# ---- /api/diagnose/stream

def test_stream_returns_lines_then_a_result(client, project, make_venv):
    make_venv(project)
    (project / "app.py").write_text("import os\n")
    r, events = stream(client, str(project))
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/x-ndjson")
    assert events[0]["type"] == "start" and events[-1]["type"] == "result"
    result = events[-1]["result"]
    assert isinstance(result["elapsed_ms"], int) and result["checked_at"].endswith("+00:00") and result["facts"]["has_venv"]
    assert "content-security-policy" in r.headers and r.headers["cache-control"] == "no-store"


def test_stream_result_matches_the_plain_endpoint(client, project, make_venv):
    make_venv(project, version="3.9.7")
    (project / "pyproject.toml").write_text('[project]\nname="x"\nrequires-python=">=3.10"\n')
    (project / "app.py").write_text("import cv2\n")
    streamed = stream(client, str(project))[1][-1]["result"]
    plain = client.post("/api/diagnose", json={"path": str(project)}, headers=HEADERS).json()
    for key in ("status", "score", "summary", "fix_first", "counts", "checks_run", "by_check", "facts"):
        assert streamed[key] == plain[key], key


def test_stream_requires_the_token(client, tmp_path):
    for headers in ({}, {"X-Dashboard-Token": "wrong"}):
        assert client.post("/api/diagnose/stream", json={"path": str(tmp_path)}, headers=headers).status_code == 401


@pytest.mark.parametrize("bad", ["/definitely/not/here", "\x00", "/", "a\nb"])
def test_stream_bad_paths_are_plain_400s(client, bad):
    r, _ = stream(client, bad)
    assert r.status_code == 400 and isinstance(r.json()["error"], str)


def test_stream_invalid_input_is_422(client):
    assert client.post("/api/diagnose/stream", json={"path": ""}, headers=HEADERS).status_code == 422


def test_stream_overload_is_429(project, monkeypatch):
    monkeypatch.setattr(appmod, "MAX_CONCURRENT", 0)
    c = TestClient(appmod.create_app(token=TOKEN), base_url=BASE)
    assert stream(c, str(project))[0].status_code == 429


def test_the_slot_is_released_after_each_stream(project, monkeypatch):
    monkeypatch.setattr(appmod, "MAX_CONCURRENT", 1)
    c = TestClient(appmod.create_app(token=TOKEN), base_url=BASE)
    for _ in range(3):
        assert stream(c, str(project))[0].status_code == 200


def test_a_crash_becomes_an_error_event_without_details(client, project, monkeypatch):
    def boom(_):
        yield {"type": "start", "t": "x", "project": "p"}
        raise RuntimeError("SECRET INTERNAL DETAIL")
    monkeypatch.setattr(appmod, "diagnose_events", boom)
    r, events = stream(client, str(project))
    assert events[-1]["type"] == "error" and "SECRET INTERNAL DETAIL" not in r.text


def test_a_slow_diagnosis_times_out_with_an_error_event(client, project, monkeypatch):
    import time

    def slow(_):
        yield {"type": "start", "t": "x", "project": "p"}
        time.sleep(1.5)
        yield {"type": "result", "t": "x", "result": {}}
    monkeypatch.setattr(appmod, "diagnose_events", slow)
    monkeypatch.setattr(appmod, "DIAGNOSE_TIMEOUT_SECONDS", 0.3)
    r, events = stream(client, str(project))
    assert events[-1]["type"] == "error" and "too long" in events[-1]["error"]


def test_secrets_never_appear_in_the_stream(client, project):
    (project / ".env").write_text("OPENAI_API_KEY=sk-super-secret-1234567890\n")
    (project / ".env.example").write_text("OPENAI_API_KEY=\n")
    (project / "app.py").write_text("import openai\n")
    assert "sk-super-secret" not in stream(client, str(project))[0].text


# ---- /api/system

def test_system_info_needs_the_token_and_hides_personal_details(client):
    assert client.get("/api/system").status_code == 401
    info = client.get("/api/system", headers=HEADERS).json()
    assert info["python"].count(".") >= 1 and "fastapi" in info["packages"] and info["listening"].startswith("127.0.0.1")
    import getpass
    import platform
    text = json.dumps(info)
    assert platform.node() not in text and getpass.getuser() not in text  # no computer name, no user name


# ---- /api/browse (the real window is never opened in tests)

def test_browse_needs_the_token(client):
    assert client.post("/api/browse", json={}).status_code == 401


@pytest.mark.parametrize("answer", [{"path": "C:\\proj"}, {"cancelled": True}, {"error": "no window"}])
def test_browse_passes_the_helper_answer_through(client, monkeypatch, answer):
    monkeypatch.setattr(appmod, "choose_folder", lambda: answer)
    r = client.post("/api/browse", json={}, headers=HEADERS)
    assert r.status_code == 200 and r.json() == answer


def test_helper_script_is_valid_python_and_takes_no_input():
    compile(folder_dialog.HELPER_CODE, "<helper>", "exec")
    assert "sys.argv" not in folder_dialog.HELPER_CODE and "input(" not in folder_dialog.HELPER_CODE


def _fake_run(stdout="", exc=None):
    def run(cmd, **kw):
        assert cmd == [sys.executable, "-c", folder_dialog.HELPER_CODE]  # exactly this program, nothing else
        if exc:
            raise exc
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")
    return run


@pytest.mark.parametrize("stdout,expected_key", [
    ("PATH:C:/Users/x/proj\n", "path"), ("CANCELLED\n", "cancelled"), ("UNAVAILABLE\n", "error"), ("", "error"),
    ("garbage\n", "error"), ("PATH:" + "a" * 2000 + "\n", "error"), ("PATH:a\x1bb\n", "error"), ("noise\nPATH:/home/x\n", "path"),
])
def test_choose_folder_interprets_every_answer(monkeypatch, stdout, expected_key):
    monkeypatch.setattr(folder_dialog.subprocess, "run", _fake_run(stdout))
    assert expected_key in folder_dialog.choose_folder()


def test_choose_folder_survives_timeouts_and_missing_python(monkeypatch):
    monkeypatch.setattr(folder_dialog.subprocess, "run", _fake_run(exc=subprocess.TimeoutExpired("x", 1)))
    assert folder_dialog.choose_folder() == {"cancelled": True}
    monkeypatch.setattr(folder_dialog.subprocess, "run", _fake_run(exc=OSError("nope")))
    assert "error" in folder_dialog.choose_folder()
