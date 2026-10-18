import stat
import sys

import pytest
from packaging.version import Version

from diagnostics.checks import gpu
from diagnostics.checks.gpu import GpuInfo, classify_torch
from tests.conftest import by_severity, titles

NO_GPU = GpuInfo(state="none")


def good_gpu(cuda=(12, 4)):
    return GpuInfo(state="ok", names=["NVIDIA GeForce RTX 4060"], driver="551.61", cuda=cuda)


@pytest.fixture
def run_with(monkeypatch, project, make_venv):
    def _run(gpu_info, packages=None, requirements=None, venv=True):
        monkeypatch.setattr(gpu, "read_gpu", lambda: gpu_info)
        monkeypatch.setattr(sys, "platform", "win32")  # default PyPI behaviour is tested separately
        if venv:
            make_venv(project, packages=packages or {})
        if requirements is not None:
            (project / "requirements.txt").write_text(requirements)
        return gpu.run(project)
    return _run


# ---- classify_torch

@pytest.mark.parametrize("text,platform,kind,cuda", [
    ("2.5.1+cu121", "win32", "cuda", (12, 1)),
    ("2.1.0+cu118", "linux", "cuda", (11, 8)),
    ("2.7.0+cu128", "linux", "cuda", (12, 8)),
    ("1.8.0+cu90", "linux", "cuda", (9, 0)),
    ("2.5.1+cpu", "linux", "cpu", None),
    ("2.5.1", "win32", "cpu", None),
    ("2.5.1", "darwin", "cpu", None),
    ("2.5.1", "linux", "cuda-default", None),
    ("2.4.0+rocm6.1", "linux", "rocm", None),
    ("2.5.0+git1234", "linux", "other", None),
])
def test_classify(text, platform, kind, cuda):
    build = classify_torch(Version(text), platform)
    assert build.kind == kind and build.cuda == cuda


# ---- comparison logic

def test_no_gpu_cpu_torch_is_fine(run_with):
    f = run_with(NO_GPU, {"torch": "2.5.1+cpu"}, "torch\n")
    assert "No NVIDIA GPU or driver found" in titles(f)
    assert "PyTorch CPU build matches this machine" in titles(f)
    assert not by_severity(f, "warning") and not by_severity(f, "critical")


def test_cuda_torch_matching_driver(run_with):
    f = run_with(good_gpu((12, 4)), {"torch": "2.5.1+cu121"}, "torch\n")
    assert "PyTorch CUDA build matches the driver" in titles(f)


def test_driver_newer_than_torch_is_ok(run_with):
    f = run_with(good_gpu((12, 8)), {"torch": "2.1.0+cu118"}, "torch\n")
    assert "PyTorch CUDA build matches the driver" in titles(f)


def test_driver_major_too_old_is_critical(run_with):
    f = run_with(good_gpu((11, 8)), {"torch": "2.5.1+cu121"}, "torch\n")
    crit = by_severity(f, "critical")
    assert len(crit) == 1 and "12.1" in crit[0].detail and "11.8" in crit[0].detail and crit[0].fix


def test_driver_minor_older_is_warning_not_critical(run_with):
    f = run_with(good_gpu((12, 0)), {"torch": "2.5.1+cu124"}, "torch\n")
    assert not by_severity(f, "critical")
    assert "Driver is slightly older than the PyTorch CUDA build" in titles(f)


def test_cpu_torch_with_gpu_warns(run_with):
    f = run_with(good_gpu(), {"torch": "2.5.1"}, "torch\n")  # untagged on win32 = CPU
    warn = by_severity(f, "warning")
    assert warn and "CPU-only" in warn[0].title and "12.4" in warn[0].detail


def test_cuda_torch_without_driver_warns(run_with):
    f = run_with(NO_GPU, {"torch": "2.5.1+cu121"}, "torch\n")
    assert "CUDA build of PyTorch but no NVIDIA driver found" in titles(f)


def test_linux_default_cases(monkeypatch, project, make_venv):
    make_venv(project, packages={"torch": "2.5.1"})
    (project / "requirements.txt").write_text("torch\n")
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(gpu, "read_gpu", lambda: NO_GPU)
    f = gpu.run(project)
    assert "No NVIDIA GPU; PyTorch from PyPI is a CUDA build" in titles(f)
    assert not by_severity(f, "warning")
    monkeypatch.setattr(gpu, "read_gpu", lambda: good_gpu())
    f = gpu.run(project)
    assert "PyTorch CUDA version is not recorded in the install" in titles(f)


def test_rocm_is_not_compared(run_with):
    f = run_with(good_gpu(), {"torch": "2.4.0+rocm6.1"}, "torch\n")
    assert "PyTorch is a ROCm (AMD) build" in titles(f)


def test_unreadable_driver_cuda_is_info(run_with):
    f = run_with(GpuInfo(state="ok", names=["GPU"], driver="1", cuda=None), {"torch": "2.5.1+cu121"}, "torch\n")
    assert "Could not compare PyTorch with the driver" in titles(f)


def test_failed_nvidia_smi_warns_for_cuda_torch(run_with):
    f = run_with(GpuInfo(state="failed", error="couldn't communicate with the NVIDIA driver"),
                 {"torch": "2.5.1+cu121"}, "torch\n")
    assert by_severity(f, "warning") and "nvidia-smi is present but failed" in titles(f)


def test_failed_nvidia_smi_is_info_without_torch(run_with):
    f = run_with(GpuInfo(state="failed", error="x"), {}, "requests\n")
    assert not by_severity(f, "warning")


def test_torch_family_mismatch(run_with):
    f = run_with(good_gpu(), {"torch": "2.5.1+cu121", "torchvision": "0.20.1+cpu"}, "torch\ntorchvision\n")
    assert "torch, torchvision and torchaudio use different builds" in titles(f)


def test_torch_family_same_build_no_warning(run_with):
    f = run_with(good_gpu(), {"torch": "2.5.1+cu121", "torchvision": "0.20.1+cu121"}, "torch\n")
    assert "torch, torchvision and torchaudio use different builds" not in titles(f)


def test_declared_but_not_installed(run_with):
    f = run_with(NO_GPU, {"requests": "2.31.0"}, "torch\n")
    assert "PyTorch is declared but not installed" in titles(f)


def test_no_venv_declared(run_with):
    f = run_with(NO_GPU, requirements="torch\n", venv=False)
    assert "PyTorch is declared but not installed" in titles(f)


def test_project_without_torch(run_with):
    f = run_with(NO_GPU, {"requests": "2.31.0"}, "requests\n")
    assert "Project does not use PyTorch" in titles(f)


def test_empty_folder(run_with):
    assert run_with(NO_GPU, venv=False)


def test_many_gpus_are_capped(run_with):
    many = GpuInfo(state="ok", names=[f"GPU {i}" for i in range(20)], driver="1", cuda=(12, 4))
    f = run_with(many, {}, "")
    assert "+12 more" in f[0].detail


# ---- real subprocess handling with a fake nvidia-smi (POSIX only)

SMI_OK = """#!/bin/sh
if [ "$1" = "--query-gpu=name,driver_version" ]; then
  echo "NVIDIA GeForce RTX 3050 Laptop GPU, 551.61"
else
  echo "| NVIDIA-SMI 551.61   Driver Version: 551.61   CUDA Version: 12.4 |"
fi
"""


def fake_smi(tmp_path, body):
    exe = tmp_path / "nvidia-smi"
    exe.write_text(body)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return str(exe)


posix_only = pytest.mark.skipif(sys.platform == "win32", reason="uses a shell-script fake")


@posix_only
def test_read_gpu_parses_real_output(monkeypatch, tmp_path):
    monkeypatch.setattr(gpu, "_nvidia_smi_path", lambda: fake_smi(tmp_path, SMI_OK))
    info = gpu.read_gpu()
    assert info.state == "ok" and info.cuda == (12, 4) and info.driver == "551.61"
    assert info.names == ["NVIDIA GeForce RTX 3050 Laptop GPU"]


@posix_only
def test_read_gpu_driver_error(monkeypatch, tmp_path):
    body = "#!/bin/sh\necho \"NVIDIA-SMI has failed because it couldn't communicate with the NVIDIA driver.\"\nexit 9\n"
    monkeypatch.setattr(gpu, "_nvidia_smi_path", lambda: fake_smi(tmp_path, body))
    info = gpu.read_gpu()
    assert info.state == "failed" and "couldn't communicate" in info.error


@posix_only
def test_read_gpu_timeout(monkeypatch, tmp_path):
    monkeypatch.setattr(gpu, "TIMEOUT", 0.3)
    monkeypatch.setattr(gpu, "_nvidia_smi_path", lambda: fake_smi(tmp_path, "#!/bin/sh\nsleep 5\n"))
    info = gpu.read_gpu()
    assert info.state == "failed" and "did not answer" in info.error


@posix_only
def test_read_gpu_garbage_output(monkeypatch, tmp_path):
    monkeypatch.setattr(gpu, "_nvidia_smi_path", lambda: fake_smi(tmp_path, "#!/bin/sh\nprintf '\\377\\376 garbage'\n"))
    assert gpu.read_gpu().state in ("ok", "failed")  # must not raise


def test_read_gpu_not_installed(monkeypatch):
    monkeypatch.setattr(gpu, "_nvidia_smi_path", lambda: None)
    assert gpu.read_gpu().state == "none"


def test_read_gpu_cannot_execute(monkeypatch, tmp_path):
    monkeypatch.setattr(gpu, "_nvidia_smi_path", lambda: str(tmp_path / "missing-binary"))
    info = gpu.read_gpu()
    assert info.state == "failed" and "could not run" in info.error
