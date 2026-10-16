from diagnostics.checks import python_env
from tests.conftest import by_severity, titles


def test_no_venv(project):
    f = python_env.run(project)
    assert f[0].severity.value == "warning" and "No virtual environment" in f[0].title


def test_all_ok_linux_layout(project, make_venv):
    make_venv(project, packages={"requests": "2.31.0"})
    (project / "requirements.txt").write_text("requests==2.31.0\n")
    f = python_env.run(project)
    assert "All requirements are installed" in titles(f)


def test_all_ok_windows_layout(project, make_venv):
    make_venv(project, packages={"requests": "2.31.0"}, windows_layout=True)
    (project / "requirements.txt").write_text("requests\n")
    assert "All requirements are installed" in titles(python_env.run(project))


def test_missing_package(project, make_venv):
    make_venv(project, packages={"requests": "2.31.0"})
    (project / "requirements.txt").write_text("requests\nnot-a-real-package\n")
    crit = by_severity(python_env.run(project), "critical")
    assert len(crit) == 1 and "not-a-real-package" in crit[0].detail


def test_name_normalization(project, make_venv):
    make_venv(project, packages={"sentence-transformers": "3.0.0"})
    (project / "requirements.txt").write_text("Sentence_Transformers>=3\n")
    assert "All requirements are installed" in titles(python_env.run(project))


def test_version_mismatch(project, make_venv):
    make_venv(project, packages={"requests": "2.31.0"})
    (project / "requirements.txt").write_text("requests==9.9.9\n")
    warn = by_severity(python_env.run(project), "warning")
    assert warn and "requests" in warn[0].detail and "2.31.0" in warn[0].detail


def test_version_specifier_satisfied(project, make_venv):
    make_venv(project, packages={"requests": "2.31.0"})
    (project / "requirements.txt").write_text("requests>=2.0,<3\n")
    assert not by_severity(python_env.run(project), "warning")


def test_platform_marker_skipped(project, make_venv):
    make_venv(project, packages={})
    (project / "requirements.txt").write_text('pywin32; sys_platform == "nope-os"\n')
    assert "All requirements are installed" in titles(python_env.run(project))


def test_platform_marker_applies(project, make_venv):
    make_venv(project, packages={})
    (project / "requirements.txt").write_text('missingpkg; python_version >= "3.8"\n')
    assert by_severity(python_env.run(project), "critical")


def test_python_version_marker_uses_venv_version(project, make_venv):
    make_venv(project, packages={}, version="3.13.0")
    (project / "requirements.txt").write_text('legacy; python_version < "3.9"\n')
    assert "All requirements are installed" in titles(python_env.run(project))


def test_extras_and_inline_comments_and_hashes(project, make_venv):
    make_venv(project, packages={"uvicorn": "0.30.0", "numpy": "1.26.0"})
    (project / "requirements.txt").write_text(
        "uvicorn[standard]==0.30.0  # server\nnumpy==1.26.0 \\\n    --hash=sha256:abc\n"
    )
    assert "All requirements are installed" in titles(python_env.run(project))


def test_include_files_followed(project, make_venv):
    make_venv(project, packages={})
    (project / "requirements.txt").write_text("-r base.txt\n")
    (project / "base.txt").write_text("hiddenpkg\n")
    crit = by_severity(python_env.run(project), "critical")
    assert crit and "hiddenpkg" in crit[0].detail


def test_include_cycle_is_safe(project, make_venv):
    make_venv(project, packages={})
    (project / "a.txt").write_text("-r b.txt\n")
    (project / "b.txt").write_text("-r a.txt\n")
    (project / "requirements.txt").write_text("-r a.txt\n")
    python_env.run(project)  # must not hang or crash


def test_include_escape_blocked(project, make_venv, tmp_path_factory):
    make_venv(project, packages={})
    outside = tmp_path_factory.mktemp("outside") / "evil.txt"
    outside.write_text("evilpkg\n")
    (project / "requirements.txt").write_text(f"-r {outside}\n")
    f = python_env.run(project)
    assert not any("evilpkg" in x.detail for x in f)
    assert any("outside the project" in x.detail for x in f)


def test_utf16_requirements(project, make_venv):
    make_venv(project, packages={"requests": "2.31.0"})
    (project / "requirements.txt").write_bytes("requests==2.31.0\n".encode("utf-16"))
    assert "All requirements are installed" in titles(python_env.run(project))


def test_pyproject_dependencies(project, make_venv):
    make_venv(project, packages={})
    (project / "pyproject.toml").write_text('[project]\nname="x"\ndependencies=["needed>=1"]\n')
    crit = by_severity(python_env.run(project), "critical")
    assert crit and "needed" in crit[0].detail


def test_invalid_lines_skipped_with_note(project, make_venv):
    make_venv(project, packages={"requests": "2.31.0"})
    (project / "requirements.txt").write_text("requests\n@@@ not valid !!!\n-e .\n--index-url https://x\n")
    f = python_env.run(project)
    assert "All requirements are installed" in titles(f)
    assert any("could not be parsed" in x.detail for x in f)


def test_broken_base_python(project, make_venv):
    make_venv(project, packages={}, home=str(project / "gone"))
    (project / "requirements.txt").write_text("x\n")
    assert any("missing Python" in t for t in titles(python_env.run(project)))


def test_no_requirements(project, make_venv):
    make_venv(project)
    assert "No declared requirements" in titles(python_env.run(project))


def test_multiple_venvs_prefers_dot_venv(project, make_venv):
    make_venv(project, name="venv")
    make_venv(project, name=".venv")
    f = python_env.run(project)
    assert ".venv" in f[0].title and "venv" in f[0].detail


def test_unreadable_requirements_does_not_crash(project, make_venv):
    make_venv(project)
    (project / "requirements.txt").write_bytes(b"\xff\xfe\x00\x00garbage\x00\xff")
    python_env.run(project)
