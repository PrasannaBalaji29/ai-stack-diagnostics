"""The Browse button must still work when Python's own folder window (tkinter) is broken, as on a Windows
install with missing Tcl/Tk files: it then uses the folder window that ships with Windows."""

from __future__ import annotations

import base64
import io
import os
import subprocess
import sys
from contextlib import redirect_stdout

import pytest

from dashboard import folder_dialog


def _run_helper(monkeypatch, *, platform="win32", tk_works=False, ps_out=None, ps_exc=None, ps_exists=True):
    """Runs the real helper program text in-process with a fake tkinter and a fake PowerShell."""
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if ps_exc is not None:
            raise ps_exc
        return subprocess.CompletedProcess(cmd, 0, stdout=ps_out or "", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(os.path, "isfile", lambda _p: ps_exists)
    monkeypatch.setattr(sys, "platform", platform)
    if tk_works:
        import types

        fake_tk = types.ModuleType("tkinter")
        fake_tk.Tk = lambda: types.SimpleNamespace(
            withdraw=lambda: None, attributes=lambda *a: None, destroy=lambda: None)
        fake_fd = types.ModuleType("tkinter.filedialog")
        fake_fd.askdirectory = lambda **kw: "C:/picked/by/tk"
        fake_tk.filedialog = fake_fd
        monkeypatch.setitem(sys.modules, "tkinter", fake_tk)
        monkeypatch.setitem(sys.modules, "tkinter.filedialog", fake_fd)
    else:
        monkeypatch.setitem(sys.modules, "tkinter", None)  # makes "import tkinter" fail, like a broken install
        monkeypatch.setitem(sys.modules, "tkinter.filedialog", None)
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        exec(compile(folder_dialog.HELPER_CODE, "<helper>", "exec"), {"__name__": "__main__"})  # noqa: S102
    return buffer.getvalue().strip(), calls


def test_broken_tkinter_on_windows_uses_the_windows_folder_window(monkeypatch):
    out, calls = _run_helper(monkeypatch, ps_out="PATH:C:\\Users\\prasa\\ai-stack-demos\\demo-broken\r\n")
    assert out == "PATH:C:\\Users\\prasa\\ai-stack-demos\\demo-broken"
    assert len(calls) == 1


def test_windows_cancel_and_byte_order_mark(monkeypatch):
    assert _run_helper(monkeypatch, ps_out="CANCELLED\n")[0] == "CANCELLED"
    assert _run_helper(monkeypatch, ps_out="\ufeffPATH:C:\\x\n")[0] == "PATH:C:\\x"


@pytest.mark.parametrize("kwargs", [
    {"ps_out": ""}, {"ps_out": "garbage"}, {"ps_exists": False}, {"ps_exc": OSError("no")}, {"platform": "linux"},
])
def test_everything_else_stays_unavailable(monkeypatch, kwargs):
    assert _run_helper(monkeypatch, **kwargs)[0] == "UNAVAILABLE"


def test_a_hung_windows_window_counts_as_cancelled(monkeypatch):
    out, _ = _run_helper(monkeypatch, ps_exc=subprocess.TimeoutExpired("x", 1))
    assert out == "CANCELLED"


def test_working_tkinter_is_used_first_and_powershell_is_not_started(monkeypatch):
    out, calls = _run_helper(monkeypatch, tk_works=True, platform="linux")
    assert out == "PATH:C:/picked/by/tk"
    assert calls == []


def test_powershell_command_is_fixed_text_only(monkeypatch):
    _, calls = _run_helper(monkeypatch, ps_out="CANCELLED")
    command = calls[0]
    assert command[0].endswith("powershell.exe")
    assert command[1:7] == ["-NoProfile", "-NonInteractive", "-STA", "-WindowStyle", "Hidden", "-EncodedCommand"]
    decoded = base64.b64decode(command[7]).decode("utf-16-le")
    assert decoded == folder_dialog.WINDOWS_FOLDER_WINDOW_SCRIPT
    assert "FolderBrowserDialog" in decoded
    assert "$args" not in decoded and "Read-Host" not in decoded and "Invoke-Expression" not in decoded
    assert len(command) == 8  # nothing else is ever added


def test_on_windows_the_modern_windows_window_comes_first_even_if_tkinter_works(monkeypatch):
    out, calls = _run_helper(monkeypatch, tk_works=True, ps_out="PATH:C:\\from\\windows\\window\n")
    assert out == "PATH:C:\\from\\windows\\window"
    assert len(calls) == 1


def test_on_windows_tkinter_is_the_backup_when_powershell_gives_nothing(monkeypatch):
    out, calls = _run_helper(monkeypatch, tk_works=True, ps_out="")
    assert out == "PATH:C:/picked/by/tk"
    assert len(calls) == 1


def test_the_windows_script_has_the_modern_window_and_an_older_backup():
    script = folder_dialog.WINDOWS_FOLDER_WINDOW_SCRIPT
    assert "ModernFolderPicker" in script and "FileOpenDialogClass" in script  # modern Windows window
    assert "FolderBrowserDialog" in script  # older Windows window, used only if the modern one cannot be shown
    assert script.count("@'") == 1 and "\n'@\n" in script  # the embedded C# block is closed properly


def test_the_windows_window_asks_for_sharp_text_before_it_opens():
    script = folder_dialog.WINDOWS_FOLDER_WINDOW_SCRIPT
    assert "SetProcessDpiAwarenessContext" in script and "SetProcessDPIAware" in script
    assert script.index("MakeTextSharp();") < script.index("new FileOpenDialogClass()")  # before any window exists
