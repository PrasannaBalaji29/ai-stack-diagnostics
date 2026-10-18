from diagnostics.checks import compat
from tests.conftest import by_severity, titles


def test_no_venv(project):
    assert titles(compat.run(project)) == ["No virtual environment found"]


def test_python_version_matches(project, make_venv):
    make_venv(project, version="3.12.1")
    (project / "pyproject.toml").write_text('[project]\nname="x"\nrequires-python=">=3.10"\n')
    assert "Venv Python matches the project" in titles(compat.run(project))


def test_python_version_too_old_is_critical(project, make_venv):
    make_venv(project, version="3.9.7")
    (project / "pyproject.toml").write_text('[project]\nname="x"\nrequires-python=">=3.10"\n')
    crit = by_severity(compat.run(project), "critical")
    assert crit and "3.9.7" in crit[0].detail and ">=3.10" in crit[0].detail


def test_python_version_too_new_is_critical(project, make_venv):
    make_venv(project, version="3.13.0")
    (project / "pyproject.toml").write_text('[project]\nname="x"\nrequires-python=">=3.9,<3.13"\n')
    assert by_severity(compat.run(project), "critical")


def test_no_requires_python_is_info(project, make_venv):
    make_venv(project, version="3.13.0")
    assert any(f.severity.value == "info" and "Python 3.13.0" in f.title for f in compat.run(project))


def test_package_not_supporting_this_python(project, make_venv, make_dist):
    venv = make_venv(project, version="3.13.0")
    make_dist(venv, "oldlib", "1.0", requires_python=">=3.7,<3.12")
    make_dist(venv, "goodlib", "2.0", requires_python=">=3.8")
    crit = by_severity(compat.run(project), "critical")
    assert len(crit) == 1 and "oldlib" in crit[0].detail and "goodlib" not in crit[0].detail


def test_version_conflict_detected(project, make_venv, make_dist):
    venv = make_venv(project)
    make_dist(venv, "app-lib", "1.0", requires=["numpy<2"])
    make_dist(venv, "numpy", "2.1.0")
    err = by_severity(compat.run(project), "error")
    assert err and "app-lib needs numpy<2 but 2.1.0 is installed" in err[0].detail


def test_missing_dependency_detected(project, make_venv, make_dist):
    venv = make_venv(project)
    make_dist(venv, "app-lib", "1.0", requires=["requests>=2"])
    err = by_severity(compat.run(project), "error")
    assert err and "app-lib needs requests" in err[0].detail


def test_markers_and_extras_are_respected(project, make_venv, make_dist):
    venv = make_venv(project, version="3.13.0")
    make_dist(venv, "lib", "1.0", requires=[
        'tomli; python_version < "3.11"',          # not needed on 3.13
        'colorama; sys_platform == "nonexistent"',  # other OS
        'pytest; extra == "test"',                  # optional extra
    ])
    f = compat.run(project)
    assert "No package conflicts found" in titles(f)


def test_prerelease_versions_do_not_false_positive(project, make_venv, make_dist):
    venv = make_venv(project)
    make_dist(venv, "lib", "1.0", requires=["torch>=2.5"])
    make_dist(venv, "torch", "2.6.0.dev20241112+cu121")
    assert "No package conflicts found" in titles(compat.run(project))


def test_name_normalisation(project, make_venv, make_dist):
    venv = make_venv(project)
    make_dist(venv, "lib", "1.0", requires=["Typing_Extensions>=4"])
    make_dist(venv, "typing-extensions", "4.12.0")
    assert "No package conflicts found" in titles(compat.run(project))


def test_garbage_metadata_does_not_crash(project, make_venv, make_dist):
    venv = make_venv(project)
    make_dist(venv, "lib", "1.0", requires=["!!! not a requirement", "ok>=1; bad marker ((("], requires_python="~~garbage")
    assert compat.run(project)


def test_missing_metadata_file_falls_back(project, make_venv):
    venv = make_venv(project)
    site = next(venv.glob("lib/python*/site-packages"))
    (site / "lonely-1.0.dist-info").mkdir()
    assert "No package conflicts found" in titles(compat.run(project))


def test_many_conflicts_are_capped(project, make_venv, make_dist):
    venv = make_venv(project)
    for i in range(60):
        make_dist(venv, f"lib{i}", "1.0", requires=["ghostpkg>=1"])
    err = by_severity(compat.run(project), "error")
    assert err and "more)" in err[0].detail
