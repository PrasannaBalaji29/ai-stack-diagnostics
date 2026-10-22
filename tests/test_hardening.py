"""Nasty-input tests: every tool must return findings or a clean error, never raise or hang."""

import json
import os
import sys

import pytest

import server
from diagnostics.checks import gpu

ALL_PROJECT_TOOLS = (server.check_python_env, server.check_env_and_keys, server.check_gpu,
                     server.check_imports, server.check_compat, server.diagnose)


def _assert_clean(result):
    json.dumps(result)  # must be JSON-serialisable
    assert isinstance(result, (list, dict)) and result


@pytest.mark.parametrize("bad_path", ["", "   ", "\x00", "a" * 5000, "/definitely/not/here", "C:\\no\\such\\dir", "~nobody/x", "..", "../../etc"])
def test_garbage_paths_never_raise(bad_path):
    for tool in ALL_PROJECT_TOOLS:
        _assert_clean(tool(bad_path))
    _assert_clean(server.scan_project(bad_path))


def test_path_is_a_file_not_a_folder(project):
    f = project / "file.txt"
    f.write_text("x")
    for tool in ALL_PROJECT_TOOLS:
        _assert_clean(tool(str(f)))


def test_folder_with_spaces_and_unicode(tmp_path, make_venv):
    folder = tmp_path / "मेरा project ñ"
    folder.mkdir()
    make_venv(folder, packages={"torch": "2.5.1+cu121"})
    (folder / "requirements.txt").write_text("torch==2.5.1\n", encoding="utf-8")
    for tool in ALL_PROJECT_TOOLS:
        _assert_clean(tool(str(folder)))


def test_corrupt_venv_files(project):
    venv = project / ".venv"
    venv.mkdir()
    (venv / "pyvenv.cfg").write_bytes(b"\xff\xfe\x00garbage\x00\x00")
    site = venv / "Lib" / "site-packages"
    site.mkdir(parents=True)
    for name in ("torch-.dist-info", "torch-abc.dist-info", "-1.0.dist-info", "torch-2.5.1+.dist-info", "torch-2.5.1+cu1 21.dist-info"):
        (site / name).mkdir()
    (project / "requirements.txt").write_bytes(b"\x00\x01\xff torch ===\n-r\n--index-url\n")
    for tool in ALL_PROJECT_TOOLS:
        _assert_clean(tool(str(project)))


def test_requirements_is_a_directory(project):
    (project / "requirements.txt").mkdir()
    (project / "pyproject.toml").mkdir()
    for tool in ALL_PROJECT_TOOLS:
        _assert_clean(tool(str(project)))


def test_nightly_and_dev_torch_tags(monkeypatch, project, make_venv):
    monkeypatch.setattr(gpu, "read_gpu", lambda *a, **k: gpu.GpuInfo(state="ok", names=["G"], driver="1", cuda=(12, 8)))
    make_venv(project, packages={"torch": "2.6.0.dev20241112+cu121"})
    (project / "requirements.txt").write_text("torch\n")
    titles = [f.title for f in gpu.run(project)]
    assert "PyTorch CUDA build matches the driver" in titles


@pytest.mark.skipif(sys.platform == "win32", reason="shell-script fake")
@pytest.mark.parametrize("body,expected_state", [
    ('#!/bin/sh\necho "x"\n', "failed"),  # exits 0 but lists nothing useful
    ('#!/bin/sh\nexit 0\n', "failed"),  # empty output
    ('#!/bin/sh\nif [ "$1" = "--query-gpu=name,driver_version" ]; then echo "RTX 4090, 560.1"; else echo "CUDA Version: N/A"; fi\n', "ok"),
    ('#!/bin/sh\nif [ "$1" = "--query-gpu=name,driver_version" ]; then echo "GPU ✓ ñ, 1"; else echo "CUDA Version: 12.6"; fi\n', "ok"),
    ('#!/bin/sh\nyes "CUDA Version: 99.9" | head -c 5000000\n', "failed"),  # huge output must not hang or blow memory
])
def test_weird_nvidia_smi_outputs(monkeypatch, tmp_path, body, expected_state):
    from tests.test_gpu import fake_smi
    monkeypatch.setattr(gpu, "_nvidia_smi_path", lambda *a, **k: fake_smi(tmp_path, body))
    info = gpu.read_gpu()
    assert info.state == expected_state
    if info.state == "ok" and info.cuda is None:
        # "CUDA Version: N/A" must degrade to "cannot compare", never crash
        assert gpu._compare(gpu.classify_torch(__import__("packaging.version", fromlist=["Version"]).Version("2.5.1+cu121")), info).severity.value == "info"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_nvidia_smi_not_executable(monkeypatch, tmp_path):
    exe = tmp_path / "nvidia-smi"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o644)
    monkeypatch.setattr(gpu, "_nvidia_smi_path", lambda *a, **k: str(exe))
    assert gpu.read_gpu().state == "failed"


def test_tools_do_not_modify_the_project(project, make_venv):
    make_venv(project, packages={"torch": "2.5.1+cpu"})
    (project / "requirements.txt").write_text("torch\n")
    (project / ".env").write_text("OPENAI_API_KEY=sk-secret-value-123456789\n")
    before = sorted((str(p.relative_to(project)), p.stat().st_mtime_ns, p.stat().st_size) for p in project.rglob("*") if p.is_file())
    for tool in ALL_PROJECT_TOOLS:
        tool(str(project))
    server.scan_project(str(project))
    after = sorted((str(p.relative_to(project)), p.stat().st_mtime_ns, p.stat().st_size) for p in project.rglob("*") if p.is_file())
    assert before == after


def test_secret_values_never_returned(project):
    (project / ".env").write_text("OPENAI_API_KEY=sk-secret-value-123456789\n")
    (project / ".env.example").write_text("OPENAI_API_KEY=\n")
    blob = json.dumps([server.check_env_and_keys(str(project)), server.scan_project(str(project)), server.check_gpu(str(project))])
    assert "sk-secret-value" not in blob


def test_no_tool_hangs_on_a_big_project(project):
    for i in range(300):
        d = project / f"pkg{i}"
        d.mkdir()
        (d / "m.py").write_text("import torch\nimport os\n" * 50)
    import time
    start = time.time()
    for tool in ALL_PROJECT_TOOLS:
        tool(str(project))
    server.scan_project(str(project))
    assert time.time() - start < 20


def test_only_nvidia_smi_is_ever_executed(monkeypatch, project):
    calls = []
    real_run = gpu.subprocess.run

    def spy(cmd, *a, **k):
        calls.append(cmd)
        return real_run(cmd, *a, **k)

    monkeypatch.setattr(gpu.subprocess, "run", spy)
    monkeypatch.setattr(gpu, "_nvidia_smi_path", lambda *a, **k: os.path.join(str(project), "nvidia-smi"))
    gpu.read_gpu()
    assert all(os.path.basename(c[0]) == "nvidia-smi" for c in calls)
    assert all(a.startswith("--") or a == "" for c in calls for a in c[1:])


def test_a_too_long_project_path_is_a_clean_error():
    from diagnostics.safety import ProjectPathError, resolve_project_path
    with pytest.raises(ProjectPathError):
        resolve_project_path("a" * 5000)
