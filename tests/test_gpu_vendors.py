"""AMD, Intel and Apple graphics: detection without running programs, and the guidance findings."""

import sys

import pytest

from diagnostics.checks import gpu, gpu_vendors
from diagnostics.checks.gpu import GpuInfo
from diagnostics.checks.gpu_vendors import Adapter
from tests.conftest import by_severity, titles

AMD_IGPU = Adapter("amd", "AMD Radeon(TM) Graphics")
AMD_RX = Adapter("amd", "AMD Radeon RX 7800 XT")
INTEL_ARC = Adapter("intel", "Intel(R) Arc(TM) A770 Graphics")
INTEL_UHD = Adapter("intel", "Intel(R) UHD Graphics")
NVIDIA = Adapter("nvidia", "NVIDIA GeForce RTX 3050")
APPLE = Adapter("apple", "Apple silicon GPU")


@pytest.fixture
def run_with(monkeypatch, project, make_venv):
    def _run(adapters, packages, platform="win32"):
        monkeypatch.setattr(gpu, "read_gpu", lambda *a, **k: GpuInfo(state="none"))
        monkeypatch.setattr(gpu, "read_adapters", lambda: adapters)
        monkeypatch.setattr(sys, "platform", platform)
        pairs = [item.split("==") if "==" in item else [item, "1.0"] for item in packages]
        make_venv(project, packages={name: version for name, version in pairs})
        (project / "requirements.txt").write_text("\n".join(name for name, _ in pairs) + "\n")
        return gpu.run(project)
    return _run


def one(findings, title):
    hits = [f for f in findings if f.title == title]
    assert len(hits) == 1, f"{title!r} not found once in {titles(findings)}"
    return hits[0]


# ---------------------------------------------------------------- detection

def test_names_are_sorted_into_vendors():
    found = gpu_vendors.adapters_from_names(
        ["AMD Radeon(TM) Graphics", "NVIDIA GeForce RTX 3050", "Intel(R) UHD Graphics", "Matrox G200"])
    assert [a.vendor for a in found] == ["amd", "nvidia", "intel", "other"]


def test_virtual_and_duplicate_adapters_are_dropped():
    found = gpu_vendors.adapters_from_names(
        ["Microsoft Basic Display Adapter", "Microsoft Remote Display Adapter", "AMD Radeon(TM) Graphics",
         "amd radeon(tm) graphics", "Parsec Virtual Display Adapter", ""])
    assert [a.name for a in found] == ["AMD Radeon(TM) Graphics"]


def test_names_are_cleaned_and_capped():
    found = gpu_vendors.adapters_from_names(["AMD Radeon\x1b[31m\x00 Graphics " + "x" * 500])
    assert len(found[0].name) <= gpu_vendors.MAX_NAME_CHARS and "\x1b" not in found[0].name and "\x00" not in found[0].name


def test_the_number_of_adapters_is_capped():
    found = gpu_vendors.adapters_from_names([f"AMD Radeon RX {i}" for i in range(100)])
    assert len(found) == gpu_vendors.MAX_ADAPTERS


@pytest.mark.parametrize("name,integrated", [
    ("AMD Radeon(TM) Graphics", True), ("AMD Radeon(TM) Vega 8 Graphics", True), ("AMD Radeon 680M", True),
    ("AMD Radeon(TM) 780M Graphics", True), ("AMD Radeon RX 7800 XT", False), ("AMD Radeon RX 6600M", False),
])
def test_integrated_amd_graphics_are_recognised(name, integrated):
    assert Adapter("amd", name).integrated is integrated


def test_integrated_is_unknown_without_a_name_or_for_other_makers():
    assert Adapter("amd").integrated is None and Adapter("nvidia", "NVIDIA GeForce").integrated is None


class FakeKey:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeWinreg:
    HKEY_LOCAL_MACHINE = 1
    REG_SZ = 1

    def __init__(self, subkeys, broken=False):
        self.subkeys, self.broken = subkeys, broken

    def OpenKey(self, root, sub):  # noqa: N802 - mirrors the real module
        if self.broken:
            raise PermissionError("denied")
        if isinstance(root, FakeKey):
            if sub in root.data:
                return FakeKey(root.data[sub])
            raise FileNotFoundError(sub)
        return FakeKey(self.subkeys)

    def EnumKey(self, root, index):  # noqa: N802
        keys = list(root.data)
        if index >= len(keys):
            raise OSError("no more")
        return keys[index]

    def QueryValueEx(self, key, name):  # noqa: N802
        if name in key.data:
            return key.data[name], 1
        raise FileNotFoundError(name)


def test_windows_registry_names_are_read():
    fake = FakeWinreg({
        "0000": {"DriverDesc": "AMD Radeon(TM) Graphics"}, "0001": {"DriverDesc": "NVIDIA GeForce RTX 3050"},
        "0002": {}, "Configuration": {"DriverDesc": "ignored"}, "Properties": {},
    })
    assert gpu_vendors._windows_names(fake) == ["AMD Radeon(TM) Graphics", "NVIDIA GeForce RTX 3050"]


def test_an_unreadable_registry_gives_no_names():
    assert gpu_vendors._windows_names(FakeWinreg({}, broken=True)) == []


def test_linux_vendor_ids_are_read(tmp_path):
    for card, vendor in {"card0": "0x1002", "card1": "0x10de", "card2": "0x1234", "card3": "0x8086"}.items():
        (tmp_path / card / "device").mkdir(parents=True)
        (tmp_path / card / "device" / "vendor").write_text(vendor + "\n")
    (tmp_path / "card0-HDMI-A-1" / "device").mkdir(parents=True)
    (tmp_path / "card0-HDMI-A-1" / "device" / "vendor").write_text("0x10de\n")
    (tmp_path / "card4").mkdir()  # no vendor file
    assert [a.vendor for a in gpu_vendors.linux_adapters(tmp_path)] == ["amd", "nvidia", "intel"]


def test_linux_without_drm_gives_nothing(tmp_path):
    assert gpu_vendors.linux_adapters(tmp_path / "missing") == []


def test_apple_silicon_is_recognised(monkeypatch):
    monkeypatch.setattr(gpu_vendors.platform_module, "machine", lambda: "arm64")
    assert [a.vendor for a in gpu_vendors.detect_adapters("darwin")] == ["apple"]
    monkeypatch.setattr(gpu_vendors.platform_module, "machine", lambda: "x86_64")
    assert gpu_vendors.detect_adapters("darwin") == []


def test_detection_never_raises(monkeypatch):
    def boom():
        raise RuntimeError("registry exploded")
    monkeypatch.setattr(gpu_vendors, "_windows_adapters", boom)
    assert gpu_vendors.detect_adapters("win32") == []
    assert gpu_vendors.detect_adapters("freebsd14") == []


# ---------------------------------------------------------------------- AMD

def test_amd_integrated_graphics_on_windows_means_cpu(run_with):
    f = run_with([AMD_IGPU], ["torch==2.5.1+cpu"])
    hit = one(f, "PyTorch runs on the CPU with this AMD graphics")
    assert hit.severity.value == "info" and "integrated" in hit.detail and "onnxruntime-directml" in hit.fix
    assert "Other graphics found (not NVIDIA)" in titles(f) and not by_severity(f, "warning")


def test_amd_discrete_card_on_windows_is_not_called_integrated(run_with):
    hit = one(run_with([AMD_RX], ["torch==2.5.1+cpu"]), "PyTorch runs on the CPU with this AMD graphics")
    assert "integrated" not in hit.detail and "preview" in hit.detail


def test_amd_discrete_card_on_linux_without_rocm_warns(run_with):
    f = run_with([AMD_RX], ["torch==2.5.1"], platform="linux")
    assert one(f, "AMD GPU present but PyTorch is not a ROCm build").severity.value == "warning"


def test_amd_on_linux_without_a_name_only_informs(run_with):
    f = run_with([Adapter("amd")], ["torch==2.5.1"], platform="linux")
    assert one(f, "AMD GPU present but PyTorch is not a ROCm build").severity.value == "info"


def test_amd_integrated_on_linux_only_informs(run_with):
    f = run_with([AMD_IGPU], ["torch==2.5.1"], platform="linux")
    assert one(f, "AMD GPU present but PyTorch is not a ROCm build").severity.value == "info"


def test_rocm_build_with_an_amd_gpu_is_ok(run_with):
    f = run_with([AMD_RX], ["torch==2.5.1+rocm6.2"], platform="linux")
    assert "PyTorch ROCm build found for the AMD GPU" in titles(f) and not by_severity(f, "warning")


def test_rocm_build_without_an_amd_gpu_warns(run_with):
    f = run_with([NVIDIA, INTEL_UHD], ["torch==2.5.1+rocm6.2"], platform="linux")
    assert "PyTorch is a ROCm (AMD) build but no AMD GPU was found" in titles(f)


def test_rocm_build_is_not_judged_when_adapters_are_unknown(run_with):
    f = run_with([], ["torch==2.5.1+rocm6.2"], platform="linux")  # e.g. WSL2 hides the GPU
    assert "PyTorch is a ROCm (AMD) build but no AMD GPU was found" not in titles(f)


def test_amd_without_pytorch_only_lists_the_graphics(run_with):
    f = run_with([AMD_IGPU], ["requests"])
    assert "Other graphics found (not NVIDIA)" in titles(f)
    assert "PyTorch runs on the CPU with this AMD graphics" not in titles(f)


# -------------------------------------------------------------------- Intel

def test_intel_graphics_with_cpu_torch_explains_xpu(run_with):
    hit = one(run_with([INTEL_ARC], ["torch==2.5.1+cpu"]), "PyTorch is not the Intel GPU (XPU) build")
    assert hit.severity.value == "info" and "whl/xpu" in hit.fix and "UHD" in hit.detail


def test_xpu_build_with_intel_graphics_is_ok(run_with):
    f = run_with([INTEL_ARC], ["torch==2.5.1+xpu"])
    assert "PyTorch Intel GPU (XPU) build found" in titles(f) and not by_severity(f, "warning")


def test_xpu_build_without_intel_graphics_warns(run_with):
    f = run_with([AMD_IGPU], ["torch==2.5.1+xpu"])
    assert "PyTorch is an Intel GPU (XPU) build but no Intel GPU was found" in titles(f)


def test_xpu_torch_is_recognised():
    from packaging.version import Version
    assert gpu.classify_torch(Version("2.5.1+xpu"), "win32").kind == "xpu"


# -------------------------------------------------------------------- Apple

def test_apple_silicon_with_torch_is_ok(run_with):
    f = run_with([APPLE], ["torch==2.5.1"], platform="darwin")
    assert "PyTorch can use the Apple GPU" in titles(f) and "Other graphics found (not NVIDIA)" not in titles(f)


def test_apple_tensorflow_needs_the_metal_plugin(run_with):
    f = run_with([APPLE], ["tensorflow==2.16.2"], platform="darwin")
    assert one(f, "TensorFlow is installed without tensorflow-metal").fix == "pip install tensorflow-metal"


def test_apple_tensorflow_with_the_metal_plugin_is_fine(run_with):
    f = run_with([APPLE], ["tensorflow==2.16.2", "tensorflow-metal==1.1.0"], platform="darwin")
    assert "TensorFlow is installed without tensorflow-metal" not in titles(f)


# ---------------------------------------------------------------- DirectML

def test_cpu_onnxruntime_on_windows_with_amd_suggests_directml(run_with):
    f = run_with([AMD_IGPU], ["onnxruntime==1.20.0"])
    assert "onnxruntime-directml" in one(f, "ONNX Runtime uses the CPU; DirectML could use this GPU").fix


def test_directml_package_is_ok(run_with):
    f = run_with([AMD_IGPU], ["onnxruntime-directml==1.20.0"])
    assert "ONNX Runtime DirectML can use this Windows GPU" in titles(f)


def test_directml_is_not_suggested_on_linux(run_with):
    f = run_with([AMD_RX], ["onnxruntime==1.20.0"], platform="linux")
    assert "ONNX Runtime uses the CPU; DirectML could use this GPU" not in titles(f)


# -------------------------------------------------------------- nothing to say

def test_no_adapters_means_no_vendor_findings(run_with):
    f = run_with([], ["torch==2.5.1+cpu"])
    assert "Other graphics found (not NVIDIA)" not in titles(f)


def test_nvidia_only_machine_gets_no_vendor_findings(run_with):
    f = run_with([NVIDIA], ["torch==2.5.1+cpu"])
    assert "Other graphics found (not NVIDIA)" not in titles(f)


def test_a_laptop_with_amd_and_nvidia_lists_only_the_other_graphics(run_with):
    f = run_with([AMD_IGPU, NVIDIA], ["torch==2.5.1+cpu"])
    assert "AMD Radeon(TM) Graphics" in one(f, "Other graphics found (not NVIDIA)").detail
    assert "NVIDIA GeForce" not in one(f, "Other graphics found (not NVIDIA)").detail
