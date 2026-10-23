"""GPU checks for TensorFlow, JAX, ONNX Runtime, CuPy, mixed CUDA libraries, and GPU memory."""

import stat
import sys

import pytest
from packaging.version import Version

from diagnostics.checks import gpu, gpu_frameworks
from diagnostics.checks.gpu import GpuInfo
from tests.conftest import by_severity, titles

NO_GPU = GpuInfo(state="none")


def good_gpu(cuda=(12, 4), memory=None):
    return GpuInfo(state="ok", names=["NVIDIA GeForce RTX 4060"], driver="551.61", cuda=cuda, memory=memory or [])


@pytest.fixture
def run_with(monkeypatch, project, make_venv):
    def _run(gpu_info, packages, platform="linux", requirements=""):
        monkeypatch.setattr(gpu, "read_gpu", lambda *a, **k: gpu_info)
        monkeypatch.setattr(sys, "platform", platform)
        make_venv(project, packages=packages)
        (project / "requirements.txt").write_text(requirements)
        return gpu.run(project)
    return _run


def one(findings, title):
    hits = [f for f in findings if f.title == title]
    assert len(hits) == 1, f"{title!r} not found once in {titles(findings)}"
    return hits[0]


# ---------------------------------------------------------------- TensorFlow

def test_tensorflow_on_native_windows_cannot_use_the_gpu(run_with):
    f = run_with(good_gpu(), {"tensorflow": "2.16.1"}, platform="win32")
    hit = one(f, "TensorFlow cannot use your NVIDIA GPU on native Windows")
    assert hit.severity.value == "warning" and "WSL2" in hit.fix


def test_old_tensorflow_on_windows_is_not_flagged(run_with):
    f = run_with(good_gpu(), {"tensorflow": "2.10.1"}, platform="win32")
    assert not by_severity(f, "warning")


def test_tensorflow_needs_a_cuda12_driver_on_linux(run_with):
    f = run_with(good_gpu((11, 8)), {"tensorflow": "2.16.1"})
    assert "NVIDIA driver is too old for this TensorFlow" in titles(f)


def test_tensorflow_matching_driver_is_ok(run_with):
    f = run_with(good_gpu((12, 4)), {"tensorflow": "2.16.1"})
    assert one(f, "TensorFlow build matches the driver").severity.value == "ok"


def test_tensorflow_before_215_is_not_compared_with_cuda12(run_with):
    f = run_with(good_gpu((11, 8)), {"tensorflow": "2.13.0"})
    assert not by_severity(f, "warning")


def test_two_tensorflow_packages_conflict(run_with):
    f = run_with(good_gpu(), {"tensorflow": "2.16.1", "tensorflow-cpu": "2.16.1"})
    assert "More than one TensorFlow package is installed" in titles(f)


def test_tensorflow_gpu_package_is_retired(run_with):
    f = run_with(good_gpu(), {"tensorflow-gpu": "2.10.0"}, platform="win32")
    assert "tensorflow-gpu is a retired package" in titles(f)


def test_tensorflow_cpu_package_with_a_gpu_is_info(run_with):
    f = run_with(good_gpu(), {"tensorflow-cpu": "2.16.1"})
    assert one(f, "TensorFlow is the CPU-only package").severity.value == "info"
    assert not by_severity(f, "warning")


def test_tensorflow_without_a_gpu_runs_on_the_cpu(run_with):
    f = run_with(NO_GPU, {"tensorflow": "2.16.1"})
    assert "TensorFlow will run on the CPU" in titles(f) and not by_severity(f, "warning")


def test_apple_tensorflow_is_not_compared(run_with):
    f = run_with(NO_GPU, {"tensorflow-macos": "2.13.0", "tensorflow-metal": "1.0.1"}, platform="darwin")
    assert "TensorFlow for Apple silicon" in titles(f)


# ---------------------------------------------------------------------- JAX

def test_cpu_jax_with_a_gpu_on_linux_warns_with_the_right_extra(run_with):
    f = run_with(good_gpu((12, 4)), {"jax": "0.6.0", "jaxlib": "0.6.0"})
    hit = one(f, "JAX is the CPU-only version but an NVIDIA GPU is present")
    assert hit.severity.value == "warning" and "jax[cuda12]" in hit.fix


def test_cpu_jax_suggests_cuda13_for_a_new_driver(run_with):
    f = run_with(good_gpu((13, 0)), {"jax": "0.8.0"})
    assert "jax[cuda13]" in one(f, "JAX is the CPU-only version but an NVIDIA GPU is present").fix


def test_jax_on_native_windows_is_info(run_with):
    f = run_with(good_gpu(), {"jax": "0.6.0"}, platform="win32")
    assert one(f, "JAX runs on the CPU on native Windows").severity.value == "info"
    assert not by_severity(f, "warning")


def test_jax_cuda_plugin_matching_driver(run_with):
    f = run_with(good_gpu((12, 4)), {"jax": "0.6.0", "jax-cuda12-plugin": "0.6.0"})
    assert "JAX CUDA plugin matches the driver" in titles(f)


def test_jax_cuda13_plugin_with_old_driver(run_with):
    f = run_with(good_gpu((12, 4)), {"jax": "0.8.0", "jax-cuda13-plugin": "0.8.0"})
    hit = one(f, "NVIDIA driver is too old for the JAX CUDA 13 plugin")
    assert "580" in hit.detail


def test_jax_cuda_plugin_on_windows_warns(run_with):
    f = run_with(good_gpu(), {"jax": "0.6.0", "jax-cuda12-plugin": "0.6.0"}, platform="win32")
    assert "JAX CUDA wheels do not work on native Windows" in titles(f)


def test_both_jax_plugins_installed(run_with):
    f = run_with(good_gpu((13, 0)), {"jax": "0.8.0", "jax-cuda12-plugin": "0.8.0", "jax-cuda13-plugin": "0.8.0"})
    assert "JAX CUDA 12 and CUDA 13 plugins are both installed" in titles(f)


def test_jax_without_a_gpu_is_info(run_with):
    f = run_with(NO_GPU, {"jax": "0.6.0"})
    assert "JAX will run on the CPU" in titles(f) and not by_severity(f, "warning")


def test_jax_rocm_is_not_compared(run_with):
    f = run_with(good_gpu(), {"jax": "0.6.0", "jax-rocm7-plugin": "0.6.0"})
    assert "JAX has an AMD (ROCm) plugin" in titles(f)


# -------------------------------------------------------------- ONNX Runtime

def test_cpu_and_gpu_onnxruntime_together(run_with):
    f = run_with(good_gpu(), {"onnxruntime": "1.20.0", "onnxruntime-gpu": "1.20.0"})
    hit = one(f, "More than one ONNX Runtime package is installed")
    assert "onnxruntime-gpu" in hit.fix and hit.severity.value == "warning"


def test_cpu_onnxruntime_with_a_gpu_on_windows_suggests_directml(run_with):
    f = run_with(good_gpu(), {"onnxruntime": "1.20.0"}, platform="win32")
    hit = one(f, "ONNX Runtime is the CPU-only package but an NVIDIA GPU is present")
    assert "directml" in hit.fix and "onnxruntime-gpu" in hit.fix


def test_cpu_onnxruntime_without_a_gpu_is_fine(run_with):
    f = run_with(NO_GPU, {"onnxruntime": "1.20.0"})
    assert not by_severity(f, "warning")


def test_onnxruntime_gpu_without_a_gpu_warns(run_with):
    f = run_with(NO_GPU, {"onnxruntime-gpu": "1.20.0"})
    assert "onnxruntime-gpu is installed but no NVIDIA GPU was found" in titles(f)


@pytest.mark.parametrize("version,major", [("1.18.1", 11), ("1.19.0", 12), ("1.26.0", 12), ("1.27.0", 13), ("2.0.0", 13)])
def test_onnxruntime_cuda_major_by_version(version, major):
    assert gpu_frameworks._ort_cuda_major(Version(version)) == major


def test_onnxruntime_gpu_too_old_driver(run_with):
    f = run_with(good_gpu((11, 8)), {"onnxruntime-gpu": "1.20.0"})
    assert "NVIDIA driver is too old for this onnxruntime-gpu" in titles(f)


def test_new_onnxruntime_gpu_needs_a_cuda13_driver(run_with):
    f = run_with(good_gpu((12, 4)), {"onnxruntime-gpu": "1.27.0"})
    assert "built for CUDA 13" in one(f, "NVIDIA driver is too old for this onnxruntime-gpu").detail


def test_old_onnxruntime_gpu_works_with_an_old_driver(run_with):
    f = run_with(good_gpu((11, 8)), {"onnxruntime-gpu": "1.18.1"})
    assert "onnxruntime-gpu build matches the driver" in titles(f)


# -------------------------------------------------------------------- CuPy

def test_cupy_with_an_old_driver(run_with):
    f = run_with(good_gpu((11, 8)), {"cupy-cuda12x": "13.3.0"})
    assert "NVIDIA driver is too old for this CuPy" in titles(f)


def test_cupy_matching_driver(run_with):
    f = run_with(good_gpu((12, 4)), {"cupy-cuda12x": "13.3.0"})
    assert "CuPy build matches the driver" in titles(f)


def test_two_cupy_packages(run_with):
    f = run_with(good_gpu(), {"cupy-cuda11x": "13.3.0", "cupy-cuda12x": "13.3.0"})
    assert "More than one CuPy package is installed" in titles(f)


def test_cupy_without_a_gpu(run_with):
    f = run_with(NO_GPU, {"cupy-cuda12x": "13.3.0"})
    assert "CuPy is installed but no NVIDIA GPU was found" in titles(f)


# ------------------------------------------------- mixed CUDA wheels, flash-attn

def test_cuda11_and_cuda12_nvidia_wheels_together(run_with):
    f = run_with(good_gpu(), {"nvidia-cudnn-cu11": "8.9.2", "nvidia-cublas-cu12": "12.1.3"})
    hit = one(f, "NVIDIA libraries for different CUDA versions are installed together")
    assert "CUDA 11" in hit.detail and "CUDA 12" in hit.detail


def test_only_one_cuda_flavour_is_fine(run_with):
    f = run_with(good_gpu(), {"nvidia-cudnn-cu12": "9.1.0", "nvidia-cublas-cu12": "12.1.3"})
    assert not by_severity(f, "warning")


def test_flash_attn_without_a_gpu(run_with):
    f = run_with(NO_GPU, {"flash-attn": "2.5.0"})
    assert "flash-attn is installed but this setup cannot use a GPU" in titles(f)


def test_flash_attn_with_cpu_torch(run_with):
    f = run_with(good_gpu(), {"flash-attn": "2.5.0", "torch": "2.5.1+cpu"}, requirements="torch\n")
    assert "flash-attn is installed but this setup cannot use a GPU" in titles(f)


def test_flash_attn_with_cuda_torch_is_fine(run_with):
    f = run_with(good_gpu(), {"flash-attn": "2.5.0", "torch": "2.5.1+cu121"}, requirements="torch\n")
    assert "flash-attn is installed but this setup cannot use a GPU" not in titles(f)


# ------------------------------------------------------------ project summary

def test_tensorflow_only_project_does_not_say_it_has_no_pytorch(run_with):
    f = run_with(good_gpu(), {"tensorflow": "2.16.1"}, requirements="tensorflow\n")
    assert "Project does not use PyTorch" not in titles(f)


def test_project_with_no_gpu_library_still_says_so(run_with):
    f = run_with(good_gpu(), {"requests": "2.31.0"}, requirements="requests\n")
    assert "Project does not use PyTorch" in titles(f)


def test_every_finding_belongs_to_the_gpu_check(run_with):
    f = run_with(good_gpu((11, 8)), {"tensorflow": "2.16.1", "tensorflow-cpu": "2.16.1", "jax": "0.6.0",
                                      "onnxruntime": "1.20.0", "onnxruntime-gpu": "1.20.0", "cupy-cuda12x": "13"})
    assert f and {x.check for x in f} == {"gpu"}


def test_unparseable_versions_do_not_crash():
    installed = {"tensorflow": None, "jax": None, "onnxruntime-gpu": None, "cupy-cuda12x": None}
    assert gpu_frameworks.check(installed, good_gpu(), "linux")  # must not raise


# ---------------------------------------------------------------- GPU memory

def test_memory_is_shown_in_the_gpu_summary(run_with):
    f = run_with(good_gpu(memory=[(8192, 1024)]), {})
    assert "8.0 GB memory, 1.0 GB in use" in f[0].detail


def test_almost_full_memory_warns(run_with):
    f = run_with(good_gpu(memory=[(8192, 7800)]), {})
    hit = one(f, "GPU memory is almost full")
    assert hit.severity.value == "warning" and "95%" in hit.detail and hit.fix


def test_half_full_memory_is_fine(run_with):
    f = run_with(good_gpu(memory=[(8192, 4096)]), {})
    assert "GPU memory is almost full" not in titles(f)


def test_a_tiny_gpu_is_never_reported_as_full(run_with):
    f = run_with(good_gpu(memory=[(512, 500)]), {})
    assert "GPU memory is almost full" not in titles(f)


def test_unknown_memory_gives_no_memory_finding(run_with):
    f = run_with(good_gpu(memory=[]), {})
    assert "GPU memory is almost full" not in titles(f)


SMI = """#!/bin/sh
case "$1" in
  --query-gpu=name,driver_version) echo "NVIDIA GeForce RTX 3050 Laptop GPU, 551.61";;
  --query-gpu=memory.total,memory.used) MEMORY_LINE;;
  *) echo "| NVIDIA-SMI 551.61   Driver Version: 551.61   CUDA Version: 12.4 |";;
esac
"""
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="uses a shell-script fake")


def _fake_smi(tmp_path, memory_line):
    exe = tmp_path / "nvidia-smi"
    exe.write_text(SMI.replace("MEMORY_LINE", memory_line))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return str(exe)


@posix_only
def test_read_gpu_reads_memory(monkeypatch, tmp_path):
    monkeypatch.setattr(gpu, "_nvidia_smi_path", lambda *a, **k: _fake_smi(tmp_path, 'echo "4096, 3900"'))
    info = gpu.read_gpu()
    assert info.state == "ok" and info.memory == [(4096, 3900)]


@posix_only
@pytest.mark.parametrize("line", ['echo "[N/A], [N/A]"', 'echo "garbage"', "exit 3", "printf ''", "sleep 0.1; echo ''"])
def test_unreadable_memory_never_breaks_the_gpu_check(monkeypatch, tmp_path, line):
    monkeypatch.setattr(gpu, "_nvidia_smi_path", lambda *a, **k: _fake_smi(tmp_path, line))
    info = gpu.read_gpu()
    assert info.state == "ok" and info.memory == [] and info.cuda == (12, 4)


@posix_only
def test_memory_query_timeout_does_not_fail_the_gpu(monkeypatch, tmp_path):
    monkeypatch.setattr(gpu, "TIMEOUT", 0.5)
    monkeypatch.setattr(gpu, "_nvidia_smi_path", lambda *a, **k: _fake_smi(tmp_path, "sleep 5"))
    info = gpu.read_gpu()
    assert info.state == "ok" and info.memory == []
