from diagnostics.checks import imports
from tests.conftest import by_severity, titles


def write(project, name, text):
    (project / name).write_text(text, encoding="utf-8")


def test_cv2_import_not_installed_suggests_opencv(project, make_venv):
    make_venv(project)
    write(project, "app.py", "import cv2\nimport os\n")
    f = imports.run(project)
    crit = by_severity(f, "critical")
    assert len(crit) == 1 and "cv2" in crit[0].detail and "opencv-python" in crit[0].fix


def test_matplotlib_keras_opencv_installed_and_declared_all_ok(project, make_venv, make_dist):
    venv = make_venv(project)
    make_dist(venv, "opencv-python", "4.10.0", modules=["cv2"])
    make_dist(venv, "matplotlib", "3.9.0", modules=["matplotlib", "pylab", "mpl_toolkits"])
    make_dist(venv, "keras", "3.5.0")
    write(project, "app.py", "import cv2\nimport matplotlib.pyplot as plt\nfrom keras.layers import Dense\n")
    write(project, "requirements.txt", "opencv-python\nmatplotlib\nkeras\n")
    f = imports.run(project)
    assert "Imports match the installed libraries" in titles(f)
    assert not by_severity(f, "critical") and not by_severity(f, "warning")


def test_installed_but_not_declared_warns(project, make_venv, make_dist):
    venv = make_venv(project)
    make_dist(venv, "opencv-python", "4.10.0", modules=["cv2"])
    make_dist(venv, "requests", "2.32.0")
    write(project, "app.py", "import cv2\nimport requests\n")
    write(project, "requirements.txt", "requests\n")
    warn = by_severity(imports.run(project), "warning")
    assert len(warn) == 1 and "cv2" in warn[0].detail and "opencv-python" in warn[0].fix


def test_declared_but_missing_is_left_to_python_env(project, make_venv):
    make_venv(project)
    write(project, "app.py", "import requests\n")
    write(project, "requirements.txt", "requests\n")
    assert not by_severity(imports.run(project), "critical")


def test_optional_import_is_info_not_critical(project, make_venv):
    make_venv(project)
    write(project, "app.py", "try:\n    import orjson\nexcept ImportError:\n    orjson = None\n")
    f = imports.run(project)
    assert not by_severity(f, "critical") and any("optional" in t for t in titles(f))


def test_import_used_both_optional_and_required_is_critical(project, make_venv):
    make_venv(project)
    write(project, "a.py", "try:\n    import orjson\nexcept ImportError:\n    pass\n")
    write(project, "b.py", "import orjson\n")
    assert by_severity(imports.run(project), "critical")


def test_local_modules_and_relative_imports_are_not_flagged(project, make_venv):
    make_venv(project)
    (project / "utils").mkdir()
    write(project, "utils/__init__.py", "")
    write(project, "helpers.py", "")
    write(project, "app.py", "import utils\nfrom helpers import x\nfrom . import y\nfrom .z import w\n")
    assert not by_severity(imports.run(project), "critical")


def test_stdlib_is_never_flagged(project, make_venv):
    make_venv(project)
    write(project, "app.py", "import os, sys, json, asyncio, pathlib, typing, collections.abc\nfrom concurrent.futures import X\n")
    assert not by_severity(imports.run(project), "critical")


def test_removed_stdlib_on_new_python(project, make_venv):
    make_venv(project, version="3.13.0")
    write(project, "app.py", "import distutils\nimport cgi\nimport imp\n")
    crit = by_severity(imports.run(project), "critical")
    assert crit and all(m in crit[0].detail for m in ("distutils", "cgi", "imp"))


def test_removed_stdlib_respects_python_version(project, make_venv):
    make_venv(project, version="3.12.4")
    write(project, "app.py", "import distutils\nimport cgi\n")  # cgi only removed in 3.13
    crit = by_severity(imports.run(project), "critical")
    assert crit and "distutils" in crit[0].detail and "cgi" not in crit[0].detail


def test_distutils_ok_when_setuptools_installed(project, make_venv, make_dist):
    venv = make_venv(project, version="3.12.4")
    make_dist(venv, "setuptools", "75.0.0", modules=["setuptools", "_distutils_hack"])
    write(project, "setup.py", "from distutils.core import setup\n")
    assert not by_severity(imports.run(project), "critical")


def test_old_python_keeps_removed_modules(project, make_venv):
    make_venv(project, version="3.10.0")
    write(project, "app.py", "import distutils\n")
    assert not by_severity(imports.run(project), "critical")


def test_notebook_imports_are_read(project, make_venv):
    make_venv(project)
    nb = '{"cells":[{"cell_type":"code","source":["import seaborn as sns\\n","%matplotlib inline\\n"]}]}'
    write(project, "n.ipynb", nb)
    assert "seaborn" in by_severity(imports.run(project), "critical")[0].detail


def test_no_venv_compares_with_requirements_only(project):
    write(project, "app.py", "import numpy\nimport flask\n")
    write(project, "requirements.txt", "numpy\n")
    f = imports.run(project)
    assert "No virtual environment found" in titles(f)
    assert any("flask" in x.detail for x in by_severity(f, "warning"))


def test_no_python_files(project):
    assert titles(imports.run(project)) == ["No Python files to check"]


def test_syntax_errors_and_null_bytes_do_not_crash(project, make_venv):
    make_venv(project)
    write(project, "bad.py", "def (:\n  import nothing\n")
    (project / "nul.py").write_bytes(b"import os\x00\nimport zzz\n")
    write(project, "good.py", "import qqq\n")
    assert imports.run(project)


def test_unsafe_import_names_never_reach_the_fix_command(project, make_venv):
    make_venv(project)
    write(project, "app.py", "import ñandú\nimport okname\n")
    for finding in imports.run(project):
        if finding.fix:
            assert "ñ" not in finding.fix and ";" not in finding.fix


def test_editable_install_counts_as_installed(project, make_venv, make_dist):
    venv = make_venv(project)
    make_dist(venv, "my-pkg", "0.1", with_top_level=False, editable=True)
    write(project, "app.py", "import my_pkg\n")
    assert not by_severity(imports.run(project), "critical")


def test_namespace_package_provided_by_other_dist(project, make_venv, make_dist):
    venv = make_venv(project)
    make_dist(venv, "google-generativeai", "0.8.0", modules=["google"])
    write(project, "app.py", "import google.generativeai as genai\n")
    write(project, "requirements.txt", "google-generativeai\n")
    assert not by_severity(imports.run(project), "critical")


def test_windows_layout_and_backslash_record(project, make_venv, make_dist):
    venv = make_venv(project, windows_layout=True)
    make_dist(venv, "opencv-python", "4.10.0", with_top_level=False, modules=["cv2"])
    info = next((venv / "Lib" / "site-packages").glob("opencv_python-*.dist-info"))
    (info / "RECORD").write_text("cv2\\__init__.py,sha256=x,1\ncv2\\cv2.pyd,sha256=y,2\n..\\..\\Scripts\\tool.exe,,\n")
    write(project, "app.py", "import cv2\n")
    write(project, "requirements.txt", "opencv-python\n")
    assert "Imports match the installed libraries" in titles(imports.run(project))
