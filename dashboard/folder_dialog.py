"""Opens the operating system's own "choose a folder" window on the user's computer.

The window is shown by a separate, fixed helper program (no user input is ever passed to it), so a
crash or hang in the GUI toolkit cannot take the dashboard down. The dashboard only receives the path
the user clicked; that path is validated again before it is used for anything.
"""

from __future__ import annotations

import os
import subprocess
import sys

TIMEOUT_SECONDS = 180
MAX_PATH_CHARS = 1000

# On Windows the person gets the normal, modern Windows "choose a folder" window (address bar, search box,
# Quick access list), shown by PowerShell. If that cannot be shown, PowerShell falls back to the older
# Windows folder window, and if PowerShell itself is unusable the helper tries Python's own window (tkinter).
# On macOS and Linux only the tkinter window is used. Every program here is fixed text: nothing the browser
# or the user types is ever put into them.
_MODERN_PICKER_SOURCE = """using System;
using System.Runtime.InteropServices;

public static class ModernFolderPicker
{
    [ComImport, Guid("DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7")]
    private class FileOpenDialogClass { }

    [ComImport, Guid("42F85136-DB7E-439C-85F1-E4075D135FC8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IFileDialog
    {
        [PreserveSig] int Show(IntPtr parent);
        void SetFileTypes(uint count, IntPtr specs);
        void SetFileTypeIndex(uint index);
        void GetFileTypeIndex(out uint index);
        void Advise(IntPtr events, out uint cookie);
        void Unadvise(uint cookie);
        void SetOptions(uint options);
        void GetOptions(out uint options);
        void SetDefaultFolder(IShellItem item);
        void SetFolder(IShellItem item);
        void GetFolder(out IShellItem item);
        void GetCurrentSelection(out IShellItem item);
        void SetFileName([MarshalAs(UnmanagedType.LPWStr)] string name);
        void GetFileName([MarshalAs(UnmanagedType.LPWStr)] out string name);
        void SetTitle([MarshalAs(UnmanagedType.LPWStr)] string title);
        void SetOkButtonLabel([MarshalAs(UnmanagedType.LPWStr)] string text);
        void SetFileNameLabel([MarshalAs(UnmanagedType.LPWStr)] string label);
        void GetResult(out IShellItem item);
        void AddPlace(IShellItem item, int placement);
        void SetDefaultExtension([MarshalAs(UnmanagedType.LPWStr)] string extension);
        void Close(int result);
        void SetClientGuid(ref Guid guid);
        void ClearClientData();
        void SetFilter(IntPtr filter);
    }

    [ComImport, Guid("43826D1E-E718-42EE-BC55-A1E261C37BFE"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IShellItem
    {
        void BindToHandler(IntPtr context, ref Guid handler, ref Guid iid, out IntPtr result);
        void GetParent(out IShellItem parent);
        void GetDisplayName(uint kind, [MarshalAs(UnmanagedType.LPWStr)] out string name);
        void GetAttributes(uint mask, out uint attributes);
        void Compare(IShellItem other, uint hint, out int order);
    }

    [DllImport("user32.dll")]
    private static extern IntPtr GetForegroundWindow();

    [DllImport("user32.dll")]
    private static extern bool SetProcessDpiAwarenessContext(IntPtr context);

    [DllImport("user32.dll")]
    private static extern bool SetProcessDPIAware();

    // PowerShell is not marked as high-resolution aware, so Windows would stretch the window and make the
    // text blurry on a scaled screen. Saying "I can draw sharp" before any window opens fixes that.
    private static void MakeTextSharp()
    {
        try { if (SetProcessDpiAwarenessContext(new IntPtr(-4))) { return; } } catch (Exception) { } // Windows 10 and newer
        try { SetProcessDPIAware(); } catch (Exception) { } // older Windows
    }

    // Returns the chosen folder, or null when the person pressed Cancel. Throws if the window cannot be shown.
    public static string Pick(string title)
    {
        MakeTextSharp();
        IFileDialog dialog = (IFileDialog)new FileOpenDialogClass();
        uint options;
        dialog.GetOptions(out options);
        dialog.SetOptions(options | 0x20 | 0x40 | 0x800); // pick folders | real folders only | path must exist
        dialog.SetTitle(title);
        int result = dialog.Show(GetForegroundWindow());
        if (result == unchecked((int)0x800704C7)) { return null; } // cancelled
        if (result != 0) { Marshal.ThrowExceptionForHR(result); }
        IShellItem item;
        dialog.GetResult(out item);
        string path;
        item.GetDisplayName(0x80058000, out path); // the normal file-system path
        return path;
    }
}
"""

WINDOWS_FOLDER_WINDOW_SCRIPT = "\n".join([
    "$ErrorActionPreference = 'Stop'",
    "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)",
    "$source = @'",
    _MODERN_PICKER_SOURCE,
    "'@",
    "$answer = $null",
    "try {",
    "    Add-Type -TypeDefinition $source",
    "    $picked = [ModernFolderPicker]::Pick('Choose a project folder')",
    "    if ($picked) { $answer = 'PATH:' + $picked } else { $answer = 'CANCELLED' }",
    "} catch {",
    "    $answer = $null",
    "}",
    "if ($null -eq $answer) {",
    "    Add-Type -AssemblyName System.Windows.Forms",
    "    $owner = New-Object System.Windows.Forms.Form",
    "    $owner.TopMost = $true",
    "    $dialog = New-Object System.Windows.Forms.FolderBrowserDialog",
    "    $dialog.Description = 'Choose a project folder'",
    "    $dialog.ShowNewFolderButton = $false",
    "    if ($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {",
    "        $answer = 'PATH:' + $dialog.SelectedPath",
    "    } else {",
    "        $answer = 'CANCELLED'",
    "    }",
    "}",
    "[Console]::Out.WriteLine($answer)",
])

_HELPER_TEMPLATE = r"""
import base64
import os
import subprocess
import sys

WINDOWS_SCRIPT = __WINDOWS_SCRIPT__


def tk_folder_window():
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
    except Exception:
        return None
    try:
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except Exception:
            pass
        chosen = filedialog.askdirectory(title="Choose a project folder", mustexist=True)
    except Exception:
        return None
    finally:
        try:
            root.destroy()
        except Exception:
            pass
    return "PATH:" + chosen if chosen else "CANCELLED"


def windows_folder_window():
    if sys.platform != "win32":
        return None
    exe = os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    if not os.path.isfile(exe):
        return None
    encoded = base64.b64encode(WINDOWS_SCRIPT.encode("utf-16-le")).decode("ascii")  # no quoting problems
    command = [exe, "-NoProfile", "-NonInteractive", "-STA", "-WindowStyle", "Hidden", "-EncodedCommand", encoded]
    try:
        done = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=170, stdin=subprocess.DEVNULL, check=False, creationflags=0x08000000,
        )
    except subprocess.TimeoutExpired:
        return "CANCELLED"
    except Exception:
        return None
    lines = [ln.strip().lstrip("\ufeff") for ln in done.stdout.splitlines() if ln.strip()]
    last = lines[-1] if lines else ""
    if last == "CANCELLED" or last.startswith("PATH:"):
        return last
    return None


print(windows_folder_window() or tk_folder_window() or "UNAVAILABLE")
"""

HELPER_CODE = _HELPER_TEMPLATE.replace("__WINDOWS_SCRIPT__", repr(WINDOWS_FOLDER_WINDOW_SCRIPT))


def choose_folder() -> dict:
    """Returns {"path": str}, {"cancelled": True} or {"error": str}."""
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # no black console box; the dialog itself still shows
    try:
        done = subprocess.run(  # noqa: S603 - our own interpreter, a fixed script, no user input
            [sys.executable, "-c", HELPER_CODE], capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=TIMEOUT_SECONDS, stdin=subprocess.DEVNULL, check=False, **kwargs,
        )
    except subprocess.TimeoutExpired:
        return {"cancelled": True}
    except OSError:
        return {"error": "The folder window could not be opened."}
    lines = [ln.strip() for ln in done.stdout.splitlines() if ln.strip()]
    last = lines[-1] if lines else ""
    if last == "UNAVAILABLE" or not last:
        return {"error": "The folder window is not available here. Type or paste the folder path instead."}
    if last == "CANCELLED":
        return {"cancelled": True}
    if last.startswith("PATH:"):
        path = os.path.normpath(last[5:])
        if 0 < len(path) <= MAX_PATH_CHARS and not any(ord(ch) < 32 for ch in path):
            return {"path": path}
    return {"error": "The folder window returned something unexpected."}
