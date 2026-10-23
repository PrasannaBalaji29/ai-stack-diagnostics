"""GPU checks for libraries other than PyTorch: TensorFlow, JAX, ONNX Runtime, CuPy.

Read-only and offline. It never imports these libraries (slow, and it can crash). It only looks at the
package names and versions recorded in the venv and compares them with the GPU facts that gpu.py read.

The rules come from the official install guides of each library:
- TensorFlow: no GPU on native Windows after version 2.10 (use WSL2); 2.15 and newer are built for CUDA 12.
- JAX: NVIDIA GPU wheels are Linux only; the CUDA 12 plugin needs a driver for CUDA 12, the CUDA 13 plugin one for 13.
- ONNX Runtime: onnxruntime-gpu is built for CUDA 11 (before 1.19), CUDA 12 (1.19 to 1.26) or CUDA 13 (1.27+),
  and it breaks when the CPU package is installed next to it.
- CuPy: the package name carries the CUDA version (cupy-cuda12x).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from packaging.version import Version

from diagnostics.models import Finding, Severity

if TYPE_CHECKING:
    from diagnostics.checks.gpu import GpuInfo

CHECK = "gpu"
Installed = dict[str, Version | None]

_TF_PACKAGES = ("tensorflow", "tensorflow-cpu", "tensorflow-gpu")
_ORT_PACKAGES = ("onnxruntime", "onnxruntime-gpu", "onnxruntime-directml", "onnxruntime-openvino", "onnxruntime-rocm")
_JAX_PLUGIN_RE = re.compile(r"^jax-cuda(\d+)-plugin$")
_CUPY_RE = re.compile(r"^cupy-cuda(\d{2,3})x?$")
_NVIDIA_WHEEL_RE = re.compile(r"^nvidia-.+-cu(\d+)$")


def _fmt(cuda: tuple[int, int]) -> str:
    return f"{cuda[0]}.{cuda[1]}"


def _release(version: Version | None) -> tuple[int, ...]:
    return version.release if version is not None else ()


def _driver_text(gpu: GpuInfo) -> str:
    return f"CUDA {_fmt(gpu.cuda)}" if gpu.cuda else "an unknown CUDA version"


def _driver_too_old(gpu: GpuInfo, needed_major: int) -> bool:
    return gpu.state == "ok" and gpu.cuda is not None and gpu.cuda[0] < needed_major


# --------------------------------------------------------------- TensorFlow


def _tensorflow(installed: Installed, gpu: GpuInfo, platform: str) -> list[Finding]:
    present = [name for name in _TF_PACKAGES if name in installed]
    apple = [name for name in ("tensorflow-macos", "tensorflow-metal") if name in installed]
    if not present and not apple:
        return []
    out: list[Finding] = []
    if apple and not present:
        return [Finding(check=CHECK, severity=Severity.info, title="TensorFlow for Apple silicon",
                        detail="tensorflow-macos / tensorflow-metal use the Apple GPU. This check only compares NVIDIA drivers.")]

    if len(present) > 1:
        out.append(Finding(
            check=CHECK, severity=Severity.warning, title="More than one TensorFlow package is installed",
            detail=f"Installed: {', '.join(present)}. They all provide the same 'tensorflow' module and overwrite "
                   "each other's files, which causes missing-GPU and import errors.",
            fix="pip uninstall -y tensorflow tensorflow-cpu tensorflow-gpu  then install only one of them",
        ))
    if "tensorflow-gpu" in installed:
        out.append(Finding(
            check=CHECK, severity=Severity.warning, title="tensorflow-gpu is a retired package",
            detail="The separate tensorflow-gpu package is no longer updated. The normal 'tensorflow' package replaced it.",
            fix='pip uninstall -y tensorflow-gpu  then: pip install "tensorflow[and-cuda]" (Linux or WSL2)',
        ))

    main = "tensorflow" if "tensorflow" in installed else present[0]
    version = installed.get(main)
    rel = _release(version)
    label = f"{main} {version}" if version else main

    if main == "tensorflow-cpu":
        if gpu.state == "ok":
            out.append(Finding(
                check=CHECK, severity=Severity.info, title="TensorFlow is the CPU-only package",
                detail=f"{label} never uses the NVIDIA GPU. That is fine if you chose it on purpose.",
                fix='For the GPU on Linux or WSL2: pip install "tensorflow[and-cuda]"',
            ))
        return out

    if gpu.state == "ok" and platform == "win32" and rel[:2] >= (2, 11):
        out.append(Finding(
            check=CHECK, severity=Severity.warning, title="TensorFlow cannot use your NVIDIA GPU on native Windows",
            detail=f"{label}: TensorFlow stopped supporting GPUs on native Windows after version 2.10, so it runs on the "
                   "CPU here even though a GPU is present.",
            fix='Run the project inside WSL2 and install: pip install "tensorflow[and-cuda]"  (or use PyTorch on Windows)',
        ))
    elif gpu.state == "ok" and platform != "win32" and rel[:2] >= (2, 15) and _driver_too_old(gpu, 12):
        out.append(Finding(
            check=CHECK, severity=Severity.warning, title="NVIDIA driver is too old for this TensorFlow",
            detail=f"{label} is built for CUDA 12, but the driver only supports {_driver_text(gpu)}. "
                   "TensorFlow will not find the GPU.",
            fix="Update the NVIDIA driver",
        ))
    elif gpu.state == "ok" and platform != "win32" and rel[:2] >= (2, 15):
        out.append(Finding(
            check=CHECK, severity=Severity.ok, title="TensorFlow build matches the driver",
            detail=f"{label} is built for CUDA 12 and the driver supports {_driver_text(gpu)}.",
        ))
    elif gpu.state == "none":
        out.append(Finding(
            check=CHECK, severity=Severity.info, title="TensorFlow will run on the CPU",
            detail=f"{label} found, but no NVIDIA GPU or driver is present on this machine.",
        ))
    return out


# --------------------------------------------------------------------- JAX


def _jax(installed: Installed, gpu: GpuInfo, platform: str) -> list[Finding]:
    if "jax" not in installed:
        return []
    out: list[Finding] = []
    plugins = sorted((int(m.group(1)), name) for name in installed if (m := _JAX_PLUGIN_RE.match(name)))
    rocm = any(name.startswith("jax-rocm") for name in installed)
    version = installed.get("jax")
    label = f"jax {version}" if version else "jax"

    if len({major for major, _ in plugins}) > 1:
        out.append(Finding(
            check=CHECK, severity=Severity.warning, title="JAX CUDA 12 and CUDA 13 plugins are both installed",
            detail=f"Installed: {', '.join(name for _, name in plugins)}. Use only one.",
            fix='pip uninstall -y jax-cuda12-plugin jax-cuda12-pjrt jax-cuda13-plugin jax-cuda13-pjrt  then: pip install -U "jax[cuda13]"',
        ))
    if rocm:
        out.append(Finding(check=CHECK, severity=Severity.info, title="JAX has an AMD (ROCm) plugin",
                           detail="This check only compares NVIDIA drivers."))
        return out

    if plugins:
        major = plugins[-1][0]
        if platform == "win32":
            out.append(Finding(
                check=CHECK, severity=Severity.warning, title="JAX CUDA wheels do not work on native Windows",
                detail=f"{label} has a CUDA {major} plugin, but JAX only ships NVIDIA GPU support for Linux. "
                       "It will run on the CPU.",
                fix="Run the project inside WSL2, or use the CPU-only 'jax' package",
            ))
        elif gpu.state == "none":
            out.append(Finding(
                check=CHECK, severity=Severity.info, title="JAX CUDA plugin installed but no NVIDIA GPU found",
                detail=f"{label} will run on the CPU on this machine.",
            ))
        elif _driver_too_old(gpu, major):
            need = "525" if major == 12 else "580"
            out.append(Finding(
                check=CHECK, severity=Severity.warning, title=f"NVIDIA driver is too old for the JAX CUDA {major} plugin",
                detail=f"{label} needs a driver for CUDA {major} (version {need} or newer on Linux), but this driver "
                       f"supports {_driver_text(gpu)}. JAX will fall back to the CPU.",
                fix="Update the NVIDIA driver, or install the plugin for the older CUDA version: "
                    'pip install -U "jax[cuda12]"',
            ))
        elif gpu.state == "ok":
            out.append(Finding(check=CHECK, severity=Severity.ok, title="JAX CUDA plugin matches the driver",
                               detail=f"{label} uses the CUDA {major} plugin and the driver supports {_driver_text(gpu)}."))
        return out

    # no GPU plugin at all
    if gpu.state == "ok":
        if platform == "win32":
            out.append(Finding(
                check=CHECK, severity=Severity.info, title="JAX runs on the CPU on native Windows",
                detail=f"{label} has no NVIDIA GPU support on native Windows. An NVIDIA GPU is present but unused.",
                fix="Use WSL2 with: pip install -U \"jax[cuda13]\"",
            ))
        else:
            extra = "cuda13" if gpu.cuda and gpu.cuda[0] >= 13 else "cuda12"
            out.append(Finding(
                check=CHECK, severity=Severity.warning, title="JAX is the CPU-only version but an NVIDIA GPU is present",
                detail=f"{label} has no CUDA plugin installed, so it computes on the CPU.",
                fix=f'pip install -U "jax[{extra}]"',
            ))
    elif gpu.state == "none":
        out.append(Finding(check=CHECK, severity=Severity.info, title="JAX will run on the CPU",
                           detail=f"{label} found, but no NVIDIA GPU or driver is present on this machine."))
    return out


# ------------------------------------------------------------- ONNX Runtime


def _ort_cuda_major(version: Version | None) -> int | None:
    rel = _release(version)
    if not rel:
        return None
    if rel[:2] < (1, 19):
        return 11
    if rel[:2] < (1, 27):
        return 12
    return 13


def _onnxruntime(installed: Installed, gpu: GpuInfo, platform: str) -> list[Finding]:
    present = [name for name in _ORT_PACKAGES if name in installed]
    if not present:
        return []
    out: list[Finding] = []
    if len(present) > 1:
        out.append(Finding(
            check=CHECK, severity=Severity.warning, title="More than one ONNX Runtime package is installed",
            detail=f"Installed: {', '.join(present)}. They share the same 'onnxruntime' module, so the GPU version can "
                   "silently stop working.",
            fix="pip uninstall -y " + " ".join(present) + "  then install only one (for NVIDIA: pip install onnxruntime-gpu)",
        ))
    if "onnxruntime-gpu" in installed:
        version = installed["onnxruntime-gpu"]
        label = f"onnxruntime-gpu {version}" if version else "onnxruntime-gpu"
        major = _ort_cuda_major(version)
        if gpu.state == "none":
            out.append(Finding(
                check=CHECK, severity=Severity.warning, title="onnxruntime-gpu is installed but no NVIDIA GPU was found",
                detail=f"{label} cannot start its CUDA provider here and falls back to the CPU with warnings.",
                fix="pip uninstall -y onnxruntime-gpu  then: pip install onnxruntime",
            ))
        elif major is not None and _driver_too_old(gpu, major):
            out.append(Finding(
                check=CHECK, severity=Severity.warning, title="NVIDIA driver is too old for this onnxruntime-gpu",
                detail=f"{label} is built for CUDA {major}, but the driver only supports {_driver_text(gpu)}.",
                fix="Update the NVIDIA driver, or install an older onnxruntime-gpu built for your CUDA version",
            ))
        elif gpu.state == "ok" and major is not None:
            out.append(Finding(
                check=CHECK, severity=Severity.ok, title="onnxruntime-gpu build matches the driver",
                detail=f"{label} is built for CUDA {major} and the driver supports {_driver_text(gpu)}. "
                       "It also needs the CUDA and cuDNN libraries installed (pip install onnxruntime-gpu[cuda,cudnn]).",
            ))
    elif "onnxruntime" in installed and gpu.state == "ok":
        if platform == "win32":
            fix = "For any Windows GPU: pip install onnxruntime-directml   For NVIDIA CUDA: pip install onnxruntime-gpu"
        else:
            fix = "pip uninstall -y onnxruntime  then: pip install onnxruntime-gpu"
        out.append(Finding(
            check=CHECK, severity=Severity.warning, title="ONNX Runtime is the CPU-only package but an NVIDIA GPU is present",
            detail="onnxruntime runs models on the CPU only.", fix=fix,
        ))
    return out


# -------------------------------------------------------------------- CuPy


def _cupy(installed: Installed, gpu: GpuInfo) -> list[Finding]:
    found = sorted((m.group(1), name) for name in installed if (m := _CUPY_RE.match(name)))
    if not found:
        return []
    out: list[Finding] = []
    majors = {int(digits[:2]) for digits, _ in found}
    names = ", ".join(name for _, name in found)
    if len(found) > 1:
        out.append(Finding(
            check=CHECK, severity=Severity.warning, title="More than one CuPy package is installed",
            detail=f"Installed: {names}. Each CuPy package is built for one CUDA version; keep only one.",
            fix="pip uninstall -y " + " ".join(name for _, name in found) + "  then install the one matching your CUDA",
        ))
    major = max(majors)
    if gpu.state == "none":
        out.append(Finding(check=CHECK, severity=Severity.warning, title="CuPy is installed but no NVIDIA GPU was found",
                           detail=f"{names} needs an NVIDIA GPU and driver to work."))
    elif _driver_too_old(gpu, major):
        out.append(Finding(
            check=CHECK, severity=Severity.warning, title="NVIDIA driver is too old for this CuPy",
            detail=f"{names} is built for CUDA {major}, but the driver only supports {_driver_text(gpu)}.",
            fix="Update the NVIDIA driver, or install the CuPy package for your CUDA version",
        ))
    elif gpu.state == "ok":
        out.append(Finding(check=CHECK, severity=Severity.ok, title="CuPy build matches the driver",
                           detail=f"{names} is built for CUDA {major} and the driver supports {_driver_text(gpu)}."))
    return out


# ------------------------------------------------- mixed CUDA wheels, flash-attn


def _mixed_cuda_wheels(installed: Installed) -> list[Finding]:
    by_major: dict[int, list[str]] = {}
    for name in installed:
        match = _NVIDIA_WHEEL_RE.match(name)
        if match:
            by_major.setdefault(int(match.group(1)), []).append(name)
    if len(by_major) < 2:
        return []
    listing = "; ".join(f"CUDA {major}: {', '.join(sorted(names)[:3])}{' ...' if len(names) > 3 else ''}"
                        for major, names in sorted(by_major.items()))
    return [Finding(
        check=CHECK, severity=Severity.warning, title="NVIDIA libraries for different CUDA versions are installed together",
        detail=f"{listing}. Mixing them in one environment causes 'cannot load library' and wrong-version errors "
               "(usually from two libraries pulling different CUDA versions).",
        fix="Create a fresh virtual environment and install only one CUDA flavour of each library",
    )]


def _flash_attn(installed: Installed, gpu: GpuInfo, torch_is_cpu: bool) -> list[Finding]:
    if "flash-attn" not in installed:
        return []
    if gpu.state == "none" or torch_is_cpu:
        return [Finding(
            check=CHECK, severity=Severity.warning, title="flash-attn is installed but this setup cannot use a GPU",
            detail="flash-attn only works with an NVIDIA GPU and a CUDA build of PyTorch.",
            fix="Install a CUDA build of PyTorch on a machine with an NVIDIA GPU, or remove flash-attn",
        )]
    return []


# --------------------------------------------------------------------- run


def check(installed: Installed, gpu: GpuInfo, platform: str, torch_is_cpu: bool = False) -> list[Finding]:
    """All non-PyTorch GPU findings for the packages installed in one venv."""
    findings: list[Finding] = []
    findings += _tensorflow(installed, gpu, platform)
    findings += _jax(installed, gpu, platform)
    findings += _onnxruntime(installed, gpu, platform)
    findings += _cupy(installed, gpu)
    findings += _mixed_cuda_wheels(installed)
    findings += _flash_attn(installed, gpu, torch_is_cpu)
    return findings
