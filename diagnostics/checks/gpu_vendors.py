"""AMD, Intel and Apple GPUs: find out what graphics the machine has, and say what ML libraries can do with it.

Read-only and it runs no program at all:
- Windows: reads the display-adapter names from the registry (HKEY_LOCAL_MACHINE, read access only).
- Linux: reads the PCI vendor id from /sys/class/drm/card*/device/vendor.
- macOS: Apple silicon is recognised from the processor type.

NVIDIA is compared exactly in gpu.py (driver and CUDA version). For the other makers there is no single
driver number to compare, so these findings say which build of each library can use the GPU.
Sources: PyTorch "Get started" pages for ROCm and Intel GPU (XPU), AMD's PyTorch-on-Windows notes
(a public preview for Radeon RX 7000/9000 and a few Ryzen AI chips), ONNX Runtime install guide (DirectML).
"""

from __future__ import annotations

import importlib
import platform as platform_module
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from packaging.version import Version

from diagnostics.models import Finding, Severity

CHECK = "gpu"
MAX_ADAPTERS = 8
MAX_NAME_CHARS = 80
_DISPLAY_CLASS_KEY = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
_PCI_VENDORS = {"0x10de": "nvidia", "0x1002": "amd", "0x8086": "intel"}
_IGNORED_NAMES = ("microsoft basic", "microsoft remote", "remote display", "virtual", "citrix", "parsec", "displaylink")
# Laptop/desktop processors with built-in graphics ("Radeon Graphics", "Radeon 680M", "Vega 8") share system memory.
_AMD_INTEGRATED_RE = re.compile(r"radeon(\(tm\))?\s+graphics$|radeon(\(tm\))?\s+\d{3}m\b|\bvega\b", re.IGNORECASE)
_XPU_INDEX = "https://download.pytorch.org/whl/xpu"
_PYTORCH_PAGE = "https://pytorch.org/get-started/locally/"

Installed = dict[str, Version | None]


@dataclass
class Adapter:
    vendor: str  # "nvidia", "amd", "intel", "apple", "other"
    name: str | None = None

    @property
    def integrated(self) -> bool | None:
        if self.vendor == "amd" and self.name:
            return bool(_AMD_INTEGRATED_RE.search(self.name))
        return None


# ---------------------------------------------------------------- detection


def _clean(name: str) -> str:
    return "".join(ch for ch in name if ch.isprintable()).strip()[:MAX_NAME_CHARS]


def _vendor_of(name: str) -> str:
    lowered = name.lower()
    if "nvidia" in lowered or "geforce" in lowered or "quadro" in lowered:
        return "nvidia"
    if "amd" in lowered or "radeon" in lowered or "ati " in lowered:
        return "amd"
    if "intel" in lowered:
        return "intel"
    return "other"


def adapters_from_names(names: list[str]) -> list[Adapter]:
    found: list[Adapter] = []
    seen: set[str] = set()
    for raw in names:
        name = _clean(raw)
        if not name or name.lower() in seen or any(skip in name.lower() for skip in _IGNORED_NAMES):
            continue
        seen.add(name.lower())
        found.append(Adapter(_vendor_of(name), name))
        if len(found) >= MAX_ADAPTERS:
            break
    return found


def _windows_names(winreg: Any) -> list[str]:
    names: list[str] = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _DISPLAY_CLASS_KEY) as root:
            for index in range(64):
                try:
                    sub = winreg.EnumKey(root, index)
                except OSError:
                    break
                if not (len(sub) == 4 and sub.isdigit()):
                    continue  # skip the "Configuration" and "Properties" entries
                try:
                    with winreg.OpenKey(root, sub) as key:
                        value, kind = winreg.QueryValueEx(key, "DriverDesc")
                except OSError:
                    continue
                if kind == winreg.REG_SZ and isinstance(value, str):
                    names.append(value)
    except OSError:
        return []
    return names


def _windows_adapters() -> list[Adapter]:
    try:
        winreg = importlib.import_module("winreg")
    except ImportError:
        return []
    return adapters_from_names(_windows_names(winreg))


def linux_adapters(root: Path = Path("/sys/class/drm")) -> list[Adapter]:
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return []
    found: list[Adapter] = []
    for entry in entries:
        if not re.fullmatch(r"card\d+", entry.name):
            continue
        try:
            with open(entry / "device" / "vendor", encoding="ascii", errors="ignore") as handle:
                vendor = _PCI_VENDORS.get(handle.read(16).strip().lower())
        except OSError:
            continue
        if vendor:
            found.append(Adapter(vendor))
        if len(found) >= MAX_ADAPTERS:
            break
    return found


def detect_adapters(platform: str) -> list[Adapter]:
    """The graphics adapters of this machine. An empty list means 'could not tell', never an error."""
    try:
        if platform == "win32":
            return _windows_adapters()
        if platform.startswith("linux"):
            return linux_adapters()
        if platform == "darwin" and platform_module.machine() == "arm64":
            return [Adapter("apple", "Apple silicon GPU")]
    except Exception:  # noqa: BLE001 - detection is a bonus, never a reason to fail
        return []
    return []


# ----------------------------------------------------------------- findings


def _names(adapters: list[Adapter]) -> str:
    return ", ".join(a.name or a.vendor.upper() for a in adapters)


def _only(adapters: list[Adapter], vendor: str) -> list[Adapter]:
    return [a for a in adapters if a.vendor == vendor]


def check(adapters: list[Adapter], installed: Installed, platform: str, torch_kind: str | None) -> list[Finding]:
    """Findings for AMD, Intel and Apple graphics. `torch_kind` is 'cpu', 'cuda', 'rocm', 'xpu'... or None."""
    others = [a for a in adapters if a.vendor in ("amd", "intel", "other", "apple")]
    if not others:
        return []
    out: list[Finding] = []
    amd, intel, apple = _only(adapters, "amd"), _only(adapters, "intel"), _only(adapters, "apple")

    if not apple:
        out.append(Finding(
            check=CHECK, severity=Severity.info, title="Other graphics found (not NVIDIA)",
            detail=f"{_names(others)}. NVIDIA drivers are compared exactly; for other graphics this check says "
                   "which library builds can use them.",
        ))

    if apple:
        if torch_kind is not None:
            out.append(Finding(
                check=CHECK, severity=Severity.ok, title="PyTorch can use the Apple GPU",
                detail="The standard macOS PyTorch build supports the Apple GPU through 'mps' (torch.backends.mps).",
            ))
        if "tensorflow" in installed and "tensorflow-metal" not in installed:
            out.append(Finding(
                check=CHECK, severity=Severity.info, title="TensorFlow is installed without tensorflow-metal",
                detail="TensorFlow needs the tensorflow-metal plugin to use the Apple GPU.",
                fix="pip install tensorflow-metal",
            ))

    if amd:
        out.extend(_amd(amd, installed, platform, torch_kind))
    if intel:
        out.extend(_intel(intel, torch_kind))
    if platform == "win32" and (amd or intel or _only(adapters, "other")):
        out.extend(_directml(installed))
    if torch_kind == "rocm" and not amd and not apple:
        out.append(Finding(
            check=CHECK, severity=Severity.warning, title="PyTorch is a ROCm (AMD) build but no AMD GPU was found",
            detail=f"Graphics found: {_names(adapters)}. A ROCm build only works with a supported AMD GPU.",
            fix=f"Install the PyTorch build for your hardware: {_PYTORCH_PAGE}",
        ))
    if torch_kind == "xpu" and not intel:
        out.append(Finding(
            check=CHECK, severity=Severity.warning, title="PyTorch is an Intel GPU (XPU) build but no Intel GPU was found",
            detail=f"Graphics found: {_names(adapters)}. An XPU build only works with a supported Intel GPU.",
            fix=f"Install the PyTorch build for your hardware: {_PYTORCH_PAGE}",
        ))
    return out


def _amd(amd: list[Adapter], installed: Installed, platform: str, torch_kind: str | None) -> list[Finding]:
    out: list[Finding] = []
    names = _names(amd)
    integrated = all(a.integrated for a in amd)
    if torch_kind == "rocm":
        out.append(Finding(check=CHECK, severity=Severity.ok, title="PyTorch ROCm build found for the AMD GPU",
                           detail=f"{names}. Whether this exact GPU is supported depends on AMD's compatibility list."))
        return out
    if torch_kind is None:
        return out
    if platform == "win32":
        extra = (" It looks like integrated graphics (shared with the CPU), which usually give little or no speed-up "
                 "for models anyway." if integrated else "")
        out.append(Finding(
            check=CHECK, severity=Severity.info, title="PyTorch runs on the CPU with this AMD graphics",
            detail=f"{names}. AMD's native PyTorch for Windows is a preview for newer Radeon RX 7000/9000 cards and a few "
                   f"Ryzen AI chips; other AMD graphics use the CPU.{extra}",
            fix="For AMD GPU on Windows with ONNX models: pip install onnxruntime-directml",
        ))
    elif platform.startswith("linux"):
        known = [a for a in amd if a.name]
        discrete = bool(known) and not integrated
        out.append(Finding(
            check=CHECK, severity=Severity.warning if discrete else Severity.info,
            title="AMD GPU present but PyTorch is not a ROCm build",
            detail=f"{names or 'An AMD GPU'}. PyTorch only uses AMD GPUs with its ROCm build, and ROCm supports "
                   "selected Radeon and Instinct cards only (built-in Ryzen graphics are usually not supported).",
            fix=f"For a supported card, pick the ROCm command at {_PYTORCH_PAGE}",
        ))
    return out


def _intel(intel: list[Adapter], torch_kind: str | None) -> list[Finding]:
    if torch_kind == "xpu":
        return [Finding(check=CHECK, severity=Severity.ok, title="PyTorch Intel GPU (XPU) build found",
                        detail=f"{_names(intel)}. Whether this exact GPU is supported depends on Intel's list.")]
    if torch_kind is None:
        return []
    return [Finding(
        check=CHECK, severity=Severity.info, title="PyTorch is not the Intel GPU (XPU) build",
        detail=f"{_names(intel)}. PyTorch uses Intel graphics only with its XPU build, and only for Arc graphics and "
               "recent Core Ultra chips. Older built-in Intel graphics (UHD, Iris) are not supported.",
        fix=f"If your Intel GPU is supported: pip install torch torchvision torchaudio --index-url {_XPU_INDEX}",
    )]


def _directml(installed: Installed) -> list[Finding]:
    if "onnxruntime-directml" in installed:
        return [Finding(check=CHECK, severity=Severity.ok, title="ONNX Runtime DirectML can use this Windows GPU",
                        detail="onnxruntime-directml runs ONNX models on any DirectX 12 graphics card.")]
    if "onnxruntime" in installed and "onnxruntime-gpu" not in installed:
        return [Finding(
            check=CHECK, severity=Severity.info, title="ONNX Runtime uses the CPU; DirectML could use this GPU",
            detail="The plain onnxruntime package runs on the CPU. On Windows, onnxruntime-directml uses any DirectX 12 GPU.",
            fix="pip uninstall -y onnxruntime  then: pip install onnxruntime-directml",
        )]
    return []
