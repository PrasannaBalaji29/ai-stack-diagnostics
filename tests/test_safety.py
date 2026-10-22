import codecs
from pathlib import Path

import pytest

from diagnostics.safety import ProjectPathError, read_text, resolve_project_path, safe_file


def test_valid_dir(tmp_path):
    assert resolve_project_path(str(tmp_path)) == tmp_path.resolve()


def test_quoted_path_is_cleaned(tmp_path):
    assert resolve_project_path(f'"{tmp_path}"') == tmp_path.resolve()


@pytest.mark.parametrize("bad", ["", "   ", None, 123])
def test_empty_or_wrong_type(bad):
    with pytest.raises(ProjectPathError):
        resolve_project_path(bad)


def test_missing_path(tmp_path):
    with pytest.raises(ProjectPathError, match="does not exist"):
        resolve_project_path(str(tmp_path / "nope"))


def test_file_not_dir(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x")
    with pytest.raises(ProjectPathError, match="Not a directory"):
        resolve_project_path(str(f))


def test_drive_root_refused():
    with pytest.raises(ProjectPathError, match="drive root"):
        resolve_project_path(str(Path.cwd().anchor))


def test_home_refused():
    with pytest.raises(ProjectPathError, match="home"):
        resolve_project_path(str(Path.home()))


def test_safe_file_blocks_parent_escape(tmp_path):
    project = tmp_path / "p"
    project.mkdir()
    (tmp_path / "secret.txt").write_text("x")
    assert safe_file(project, "../secret.txt") is None


def test_safe_file_blocks_symlink_escape(tmp_path):
    project = tmp_path / "p"
    project.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    try:
        (project / "link.txt").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks not permitted here")
    assert safe_file(project, "link.txt") is None


def test_safe_file_allows_inside(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    assert safe_file(tmp_path, "a.txt") is not None
    assert safe_file(tmp_path, "missing.txt") is None


@pytest.mark.parametrize("encoded", [
    "KEY=value\n".encode("utf-8"),
    codecs.BOM_UTF8 + "KEY=value\n".encode("utf-8"),
    "KEY=value\n".encode("utf-16"),
    "KEY=value\n".encode("utf-16-le"),
])
def test_read_text_encodings(tmp_path, encoded):
    f = tmp_path / "x.txt"
    f.write_bytes(encoded)
    assert read_text(f).strip() == "KEY=value"


def test_read_text_cp1252_fallback(tmp_path):
    f = tmp_path / "x.txt"
    f.write_bytes("caf\xe9=1".encode("cp1252"))
    assert "caf" in read_text(f)


def test_read_text_missing_returns_none(tmp_path):
    assert read_text(tmp_path / "nope.txt") is None


def test_control_characters_in_path_give_a_clean_error():
    from diagnostics.safety import ProjectPathError, resolve_project_path
    for bad in ("\x00", "C:\\proj\x00evil", "a\nb", "a\tb", "\x1b[31m"):
        with pytest.raises(ProjectPathError, match="invalid"):
            resolve_project_path(bad)
