"""Checks whether the project's GPU libraries match the machine's NVIDIA driver, and how full the GPU memory is.

Read-only. It never imports torch, TensorFlow and so on (that is slow and can crash). It reads the
versions recorded in the venv (for example torch 2.5.1+cu121) and compares the CUDA version they need with
the highest CUDA version the NVIDIA driver supports. PyTorch is checked here; TensorFlow, JAX, ONNX Runtime,
CuPy and mixed CUDA libraries are checked in gpu_frameworks.py; AMD, Intel and Apple graphics in gpu_vendors.py.

The only program it runs is nvidia-smi, with fixed arguments, no shell, and a timeout.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from packaging.utils import canonicalize_name
from packaging.version import Version

from diagnostics.checks import gpu_frameworks, gpu_vendors
from diagnostics.checks.python_env import _find_venvs, _installed
from diagnostics.models import Finding, Severity
from diagnostics.requirements import collect_requirements

CHECK = "gpu"
TIMEOUT = 8.0
MAX_OUTPUT_CHARS = 20_000
MAX_GPUS_SHOWN = 8
PYTORCH_INSTALL_PAGE = "https://pytorch.org/get-started/locally/"
PYTORCH_OLD_VERSIONS_PAGE = "https://pytorch.org/get-started/previous-versions/"

_CUDA_RE = re.compile(r"CUDA Version:\s*(\d+)\.(\d+)")
_CU_TAG_RE = re.compile(r"^cu(\d{2,3})$")
_DRIVER_RE = re.compile(r"^\d+(?:\.\d+)*$")
_MEMORY_RE = re.compile(r"^\s*(\d+)\s*,\s*(\d+)\s*$")
MEMORY_FULL_PERCENT = 90
MIN_MEMORY_MIB_TO_REPORT = 1024
_TORCH_FAMILY = ("torch", "torchvision", "torchaudio")

CudaVersion = tuple[int, int]


# ---------------------------------------------------------------- nvidia-smi


@dataclass
class GpuInfo:
    state: str  # "none" (not installed), "ok", "failed"
    names: list[str] = field(default_factory=list)
    driver: str | None = None
    cuda: CudaVersion | None = None
    error: str | None = None
    memory: list[tuple[int, int]] = field(default_factory=list)  # (total MiB, used MiB) for each GPU


def _is_inside(path: Path, folder: Path) -> bool:
    try:
        return path.resolve().is_relative_to(folder.resolve())
    except (OSError, RuntimeError, ValueError):
        return True  # cannot tell: treat as unsafe


def _nvidia_smi_path(avoid: Path | None = None) -> str | None:
    """Find nvidia-smi without ever picking a program planted in the scanned project or current folder.

    Known system locations come first. The PATH search accepts only absolute PATH entries, ignores
    the current folder and anything inside `avoid` (the project being scanned), and on Windows
    accepts only .exe files.
    """
    names = ["nvidia-smi.exe"] if sys.platform == "win32" else ["nvidia-smi"]
    if sys.platform == "win32":
        root = os.environ.get("SYSTEMROOT", r"C:\Windows")
        program_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        for known in (
            os.path.join(root, "System32", "nvidia-smi.exe"),
            os.path.join(program_files, "NVIDIA Corporation", "NVSMI", "nvidia-smi.exe"),
        ):
            if os.path.isfile(known):
                return known
    try:
        here = Path.cwd()
    except OSError:
        here = None
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry or not os.path.isabs(entry):
            continue
        folder = Path(entry)
        if (here is not None and _is_inside(folder, here)) or (avoid is not None and _is_inside(folder, avoid)):
            continue
        for name in names:
            candidate = folder / name
            try:
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    return str(candidate)
            except OSError:
                continue
    return None


def _run_smi(exe: str, args: list[str]) -> subprocess.CompletedProcess:
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # no console flash
    return subprocess.run(  # noqa: S603 - fixed program, fixed arguments, no shell
        [exe, *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=TIMEOUT, stdin=subprocess.DEVNULL, check=False, **kwargs,
    )


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()[:200]
    return ""


def _read_memory(exe: str) -> list[tuple[int, int]]:
    """GPU memory is a bonus: any problem here just means 'unknown', never a failed check."""
    try:
        result = _run_smi(exe, ["--query-gpu=memory.total,memory.used", "--format=csv,noheader,nounits"])
    except (subprocess.TimeoutExpired, OSError):
        return []
    if result.returncode != 0:
        return []
    memory: list[tuple[int, int]] = []
    for line in result.stdout[:MAX_OUTPUT_CHARS].splitlines():
        match = _MEMORY_RE.match(line)
        if match:
            memory.append((int(match.group(1)), int(match.group(2))))
    return memory[:MAX_GPUS_SHOWN]


def read_gpu(avoid: Path | None = None) -> GpuInfo:
    exe = _nvidia_smi_path(avoid)
    if exe is None:
        return GpuInfo(state="none")
    try:
        query = _run_smi(exe, ["--query-gpu=name,driver_version", "--format=csv,noheader"])
        if query.returncode != 0:
            message = _first_line(query.stdout) or _first_line(query.stderr) or f"exit code {query.returncode}"
            return GpuInfo(state="failed", error=message)
        names: list[str] = []
        driver: str | None = None
        for line in query.stdout[:MAX_OUTPUT_CHARS].splitlines():
            name, sep, drv = line.rpartition(",")  # rpartition: GPU names can contain commas
            if sep and name.strip() and _DRIVER_RE.match(drv.strip()):
                names.append(name.strip())
                driver = driver or drv.strip()
        if not names:
            return GpuInfo(state="failed", error="nvidia-smi gave output that does not look like a GPU list")
        cuda: CudaVersion | None = None
        full = _run_smi(exe, [])
        match = _CUDA_RE.search(full.stdout[:MAX_OUTPUT_CHARS]) if full.returncode == 0 else None
        if match:
            cuda = (int(match.group(1)), int(match.group(2)))
        return GpuInfo(state="ok", names=names, driver=driver, cuda=cuda, memory=_read_memory(exe))
    except subprocess.TimeoutExpired:
        return GpuInfo(state="failed", error=f"nvidia-smi did not answer within {TIMEOUT:.0f} seconds")
    except OSError as exc:
        return GpuInfo(state="failed", error=f"could not run nvidia-smi ({type(exc).__name__})")


# -------------------------------------------------------------------- torch


@dataclass
class TorchBuild:
    version: Version
    kind: str  # "cpu", "cuda", "cuda-default", "rocm", "other"
    cuda: CudaVersion | None = None
    tag: str | None = None


def classify_torch(version: Version, platform: str | None = None) -> TorchBuild:
    platform = platform or sys.platform
    tag = (version.local or "").lower() or None
    if tag is None:
        # PyPI ships CPU-only wheels for Windows and macOS, and a CUDA wheel for Linux.
        kind = "cuda-default" if platform.startswith("linux") else "cpu"
        return TorchBuild(version, kind)
    if tag == "cpu":
        return TorchBuild(version, "cpu", tag=tag)
    match = _CU_TAG_RE.match(tag)
    if match:
        digits = match.group(1)
        return TorchBuild(version, "cuda", cuda=(int(digits[:-1]), int(digits[-1])), tag=tag)
    if tag.startswith("rocm"):
        return TorchBuild(version, "rocm", tag=tag)
    if tag.startswith("xpu"):
        return TorchBuild(version, "xpu", tag=tag)
    return TorchBuild(version, "other", tag=tag)


def _fmt(cuda: CudaVersion) -> str:
    return f"{cuda[0]}.{cuda[1]}"


# ----------------------------------------------------------------- findings


def _gpu_finding(gpu: GpuInfo, torch_is_cuda: bool) -> Finding:
    if gpu.state == "none":
        return Finding(
            check=CHECK, severity=Severity.info, title="No NVIDIA GPU or driver found",
            detail="nvidia-smi is not installed or not on this machine. This is normal for CPU-only, AMD, "
                   "Intel, or Apple machines. A CUDA build of PyTorch would only run on the CPU here.",
        )
    if gpu.state == "failed":
        return Finding(
            check=CHECK, severity=Severity.warning if torch_is_cuda else Severity.info,
            title="nvidia-smi is present but failed",
            detail=f"Message: {gpu.error}. The NVIDIA driver may be missing, crashed, or need a reboot.",
            fix="Reinstall or update the NVIDIA driver, then reboot.",
        )
    shown = gpu.names[:MAX_GPUS_SHOWN]
    extra = f" (+{len(gpu.names) - len(shown)} more)" if len(gpu.names) > len(shown) else ""
    cuda_text = f"supports up to CUDA {_fmt(gpu.cuda)}" if gpu.cuda else "CUDA version could not be read"
    memory_text = ""
    if gpu.memory:
        parts = [f"{_gb(total)} GB memory, {_gb(used)} GB in use" for total, used in gpu.memory[:MAX_GPUS_SHOWN]]
        memory_text = f" Memory: {'; '.join(parts)}."
    return Finding(
        check=CHECK, severity=Severity.ok, title=f"NVIDIA GPU detected ({len(gpu.names)})",
        detail=f"{'; '.join(shown)}{extra}. Driver {gpu.driver or 'unknown'} {cuda_text}.{memory_text}",
    )


def _gb(mib: int) -> str:
    return f"{mib / 1024:.1f}"


def _memory_finding(gpu: GpuInfo) -> Finding | None:
    """Warn when the GPU memory is nearly full: models then fail to load or run out of memory."""
    if gpu.state != "ok":
        return None
    full = [
        (index, total, used) for index, (total, used) in enumerate(gpu.memory)
        if total >= MIN_MEMORY_MIB_TO_REPORT and used * 100 >= total * MEMORY_FULL_PERCENT
    ]
    if not full:
        return None
    parts = [f"GPU {index}: {_gb(used)} of {_gb(total)} GB in use ({used * 100 // total}%)" for index, total, used in full]
    return Finding(
        check=CHECK, severity=Severity.warning, title="GPU memory is almost full",
        detail=f"{'; '.join(parts)}. Another program (a game, Ollama, another Python process or a notebook) is "
               "holding it, so loading a model can fail with 'CUDA out of memory'.",
        fix="Close programs that use the GPU (see which with: nvidia-smi), or restart Ollama / the notebook kernel",
    )


def _compare(torch: TorchBuild, gpu: GpuInfo) -> Finding:
    label = f"torch {torch.version}"
    if torch.kind == "cpu":
        if gpu.state == "ok":
            driver = f" Your driver supports up to CUDA {_fmt(gpu.cuda)}." if gpu.cuda else ""
            return Finding(
                check=CHECK, severity=Severity.warning, title="PyTorch is a CPU-only build but an NVIDIA GPU is present",
                detail=f"{label} cannot use the GPU, so models run on the CPU.{driver}",
                fix=f"Install a CUDA build of PyTorch: {PYTORCH_INSTALL_PAGE}",
            )
        return Finding(check=CHECK, severity=Severity.ok, title="PyTorch CPU build matches this machine",
                       detail=f"{label} is CPU-only and no usable NVIDIA GPU was found, so nothing is mismatched.")
    if torch.kind == "rocm":
        return Finding(check=CHECK, severity=Severity.info, title="PyTorch is a ROCm (AMD) build",
                       detail=f"{label} targets AMD GPUs. This check only compares NVIDIA drivers.")
    if torch.kind == "xpu":
        return Finding(check=CHECK, severity=Severity.info, title="PyTorch is an Intel GPU (XPU) build",
                       detail=f"{label} targets Intel GPUs. NVIDIA drivers are not involved; see the Intel findings.")
    if torch.kind == "other":
        return Finding(check=CHECK, severity=Severity.info, title="PyTorch build type not recognised",
                       detail=f"{label} has the build tag '+{torch.tag}'. It was not compared with the driver.")
    # CUDA builds from here on
    if gpu.state == "none" and torch.kind == "cuda-default":
        return Finding(
            check=CHECK, severity=Severity.info, title="No NVIDIA GPU; PyTorch from PyPI is a CUDA build",
            detail=f"{label} (Linux PyPI default) includes CUDA libraries it cannot use here, so it runs on the "
                   "CPU. That works, but a CPU build is much smaller.",
            fix="pip install torch --index-url https://download.pytorch.org/whl/cpu",
        )
    if gpu.state == "none":
        return Finding(
            check=CHECK, severity=Severity.warning, title="CUDA build of PyTorch but no NVIDIA driver found",
            detail=f"{label} expects an NVIDIA GPU. Code that calls .cuda() or device='cuda' will fail; "
                   "everything else runs on the CPU.",
            fix="Install the NVIDIA driver, or use a CPU build of PyTorch on this machine.",
        )
    if gpu.state == "failed" or gpu.cuda is None:
        return Finding(check=CHECK, severity=Severity.info, title="Could not compare PyTorch with the driver",
                       detail=f"{label} was found, but the driver's CUDA version could not be read.")
    if torch.kind == "cuda-default":
        return Finding(
            check=CHECK, severity=Severity.info, title="PyTorch CUDA version is not recorded in the install",
            detail=f"{label} came from PyPI on Linux, which is a CUDA build, but its CUDA version is not in the "
                   f"metadata. Your driver supports up to CUDA {_fmt(gpu.cuda)}.",
            fix='python -c "import torch; print(torch.version.cuda, torch.cuda.is_available())"',
        )
    if torch.cuda is None:  # cannot happen for a "cuda" build; stay safe if it ever does
        return Finding(check=CHECK, severity=Severity.info, title="PyTorch CUDA version unknown",
                       detail=f"{label} did not record its CUDA version.")
    if torch.cuda <= gpu.cuda:
        return Finding(check=CHECK, severity=Severity.ok, title="PyTorch CUDA build matches the driver",
                       detail=f"{label} is built for CUDA {_fmt(torch.cuda)}; the driver supports up to CUDA {_fmt(gpu.cuda)}.")
    if torch.cuda[0] == gpu.cuda[0]:
        return Finding(
            check=CHECK, severity=Severity.warning, title="Driver is slightly older than the PyTorch CUDA build",
            detail=f"{label} is built for CUDA {_fmt(torch.cuda)}, the driver supports up to {_fmt(gpu.cuda)}. "
                   "The same major version often still works, but if torch.cuda.is_available() is False, update the driver.",
            fix="Update the NVIDIA driver, or install a PyTorch build for an older CUDA version: "
                f"{PYTORCH_OLD_VERSIONS_PAGE}",
        )
    return Finding(
        check=CHECK, severity=Severity.critical, title="NVIDIA driver is too old for this PyTorch build",
        detail=f"{label} needs CUDA {_fmt(torch.cuda)} but the driver only supports up to CUDA {_fmt(gpu.cuda)}. "
               "PyTorch will not be able to use the GPU.",
        fix=f"Update the NVIDIA driver, or install a PyTorch build for CUDA {_fmt(gpu.cuda)}: {PYTORCH_OLD_VERSIONS_PAGE}",
    )


def _family_mismatch(builds: dict[str, TorchBuild]) -> Finding | None:
    tags = {name: b.tag for name, b in builds.items() if b.tag}
    if len(set(tags.values())) > 1:
        listing = ", ".join(f"{name} (+{tag})" for name, tag in sorted(tags.items()))
        return Finding(
            check=CHECK, severity=Severity.warning, title="torch, torchvision and torchaudio use different builds",
            detail=f"Installed: {listing}. Mixed CPU/CUDA builds cause import errors and missing operators.",
            fix=f"Reinstall all three from the same PyTorch index: {PYTORCH_INSTALL_PAGE}",
        )
    return None


# --------------------------------------------------------------------- run


def read_adapters() -> list[gpu_vendors.Adapter]:
    return gpu_vendors.detect_adapters(sys.platform)


def run(project: Path) -> list[Finding]:
    venvs = _find_venvs(project)
    installed = _installed(venvs[0]) if venvs else {}
    reqs = collect_requirements(project)
    declared = any(str(canonicalize_name(r.name)) in _TORCH_FAMILY for r in reqs.requirements)

    builds: dict[str, TorchBuild] = {}
    for name in _TORCH_FAMILY:
        version = installed.get(name)
        if version is not None:
            builds[name] = classify_torch(version)
    torch = builds.get("torch")

    gpu = read_gpu(project)
    findings = [_gpu_finding(gpu, torch_is_cuda=bool(torch and torch.kind in ("cuda", "cuda-default")))]
    memory_finding = _memory_finding(gpu)
    if memory_finding:
        findings.append(memory_finding)

    if torch is not None:
        findings.append(_compare(torch, gpu))
        mismatch = _family_mismatch(builds)
        if mismatch:
            findings.append(mismatch)
    elif declared:
        where = f"the {venvs[0].name} environment" if venvs else "any virtual environment"
        findings.append(Finding(
            check=CHECK, severity=Severity.info, title="PyTorch is declared but not installed",
            detail=f"The project lists PyTorch, but it was not found in {where}, so no build comparison was done.",
            fix="pip install -r requirements.txt",
        ))
    others = gpu_frameworks.check(installed, gpu, sys.platform, torch_is_cpu=bool(torch and torch.kind == "cpu"))
    if torch is None and not declared and not others:
        findings.append(Finding(
            check=CHECK, severity=Severity.info, title="Project does not use PyTorch",
            detail="No torch, torchvision or torchaudio in the requirements or the virtual environment. "
                   "Only the GPU itself was checked.",
        ))
    findings.extend(others)
    findings.extend(gpu_vendors.check(read_adapters(), installed, sys.platform, torch.kind if torch else None))
    return findings
