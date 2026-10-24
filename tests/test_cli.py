"""The terminal command: python -m diagnostics."""

import io
import json
import subprocess
import sys

import pytest

from diagnostics import __main__ as cli


def run_cli(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_report_for_a_project_without_a_venv(project, capsys):
    (project / "requirements.txt").write_text("requests\n")
    code, out, _ = run_cli(capsys, str(project))
    assert code == 0  # a warning does not fail by default
    assert "Health:" in out and "/100" in out and "What to fix first" in out and "No virtual environment found" in out
    assert "Fix: python -m venv .venv" in out and "Checks run:" in out


def test_fail_on_warning_gives_exit_code_1(project, capsys):
    (project / "requirements.txt").write_text("requests\n")
    assert run_cli(capsys, str(project), "--fail-on", "warning")[0] == 1
    assert run_cli(capsys, str(project), "--fail-on", "never")[0] == 0
    assert run_cli(capsys, str(project), "--fail-on", "critical")[0] == 0


def test_a_clean_project_exits_0_even_when_strict(project, make_venv, capsys):
    make_venv(project, packages={"requests": "2.31.0"})
    (project / "requirements.txt").write_text("requests\n")
    (project / "app.py").write_text("import requests\n")
    code, out, _ = run_cli(capsys, str(project), "--fail-on", "warning")
    assert code == 0 and "Nothing to fix." in out


def test_json_output_is_valid_json_with_the_report_fields(project, capsys):
    (project / "requirements.txt").write_text("requests\n")
    code, out, _ = run_cli(capsys, str(project), "--json")
    data = json.loads(out)
    assert code == 0 and {"score", "status", "summary", "fix_first", "by_check"} <= set(data)


def test_bad_paths_exit_with_2_and_a_message_on_stderr(capsys, tmp_path):
    code, out, err = run_cli(capsys, str(tmp_path / "nope"))
    assert code == 2 and out == "" and "does not exist" in err
    assert run_cli(capsys, "")[0] == 2


def test_the_current_folder_is_the_default(project, capsys, monkeypatch):
    (project / "requirements.txt").write_text("requests\n")
    monkeypatch.chdir(project)
    code, out, _ = run_cli(capsys)
    assert code == 0 and str(project.resolve()) in out


def test_explain_reads_stdin(capsys, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("ModuleNotFoundError: No module named 'cv2'\n"))
    code, out, _ = run_cli(capsys, "--explain")
    assert code == 0 and "A Python library is missing" in out and "opencv-python" in out


def test_explain_with_a_project_uses_the_project(project, make_venv, capsys, monkeypatch):
    make_venv(project, packages={"requests": "2.31.0"})
    monkeypatch.setattr(sys, "stdin", io.StringIO("ModuleNotFoundError: No module named 'requests'\n"))
    code, out, _ = run_cli(capsys, "--explain", str(project))
    assert code == 0 and "IS installed" in out


def test_explain_json_and_secret_warning(capsys, monkeypatch):
    secret = "sk-" + "a1B2c3D4" * 5
    monkeypatch.setattr(sys, "stdin", io.StringIO(f"Error 401 Incorrect API key provided: {secret}\n"))
    code, out, _ = run_cli(capsys, "--explain", "--json")
    data = json.loads(out)
    assert code == 0 and data[0]["severity"] == "critical" and secret not in out


def test_explain_with_a_bad_project_path(capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "stdin", io.StringIO("x"))
    assert run_cli(capsys, "--explain", str(tmp_path / "nope"))[0] == 2


def test_explain_with_empty_input_says_so(capsys, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    code, out, _ = run_cli(capsys, "--explain")
    assert code == 0 and "Nothing to explain" in out


def test_explain_input_is_capped(capsys, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("x" * 5_000_000))
    assert run_cli(capsys, "--explain")[0] == 0


def test_output_never_crashes_on_a_console_that_cannot_show_a_character(project, monkeypatch):
    (project / "requirements.txt").write_text("requests\n")
    (project / "caf\u00e9\u2192").mkdir()
    raw = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="cp1252", errors="strict"))
    assert cli.main([str(project)]) == 0
    sys.stdout.flush()
    assert b"Health:" in raw.getvalue()


def test_colors_only_on_a_real_terminal_and_never_with_no_color(monkeypatch):
    class Tty(io.StringIO):
        def isatty(self):
            return True
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert cli._use_color(Tty()) is True and cli._use_color(io.StringIO()) is False
    monkeypatch.setenv("NO_COLOR", "1")
    assert cli._use_color(Tty()) is False
    monkeypatch.delenv("NO_COLOR")
    monkeypatch.setattr(sys, "platform", "win32")
    for name in ("WT_SESSION", "TERM_PROGRAM", "ANSICON"):
        monkeypatch.delenv(name, raising=False)
    assert cli._use_color(Tty()) is False  # an old Windows console
    monkeypatch.setenv("TERM_PROGRAM", "vscode")
    assert cli._use_color(Tty()) is True


def test_plain_report_has_no_escape_codes_and_is_ascii(project, capsys):
    (project / "requirements.txt").write_text("requests\n")
    _, out, _ = run_cli(capsys, str(project))
    assert "\x1b" not in out and out.isascii()


def test_the_command_works_as_a_real_subprocess(project):
    (project / "requirements.txt").write_text("requests\n")
    done = subprocess.run([sys.executable, "-m", "diagnostics", str(project), "--json"], capture_output=True, text=True, timeout=120, check=False)
    assert done.returncode == 0 and json.loads(done.stdout)["project"]
    bad = subprocess.run([sys.executable, "-m", "diagnostics", "--fail-on", "nonsense"], capture_output=True, text=True, timeout=60, check=False)
    assert bad.returncode == 2


@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_help_is_available(flag, capsys):
    with pytest.raises(SystemExit) as stop:
        cli.main([flag])
    assert stop.value.code == 0 and "--explain" in capsys.readouterr().out


def test_one_version_everywhere():
    import tomllib
    from pathlib import Path

    import dashboard
    root = Path(__file__).resolve().parent.parent
    assert tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"] == dashboard.__version__
