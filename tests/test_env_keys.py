from diagnostics.checks import env_keys
from tests.conftest import by_severity, titles


def test_nothing_found(project):
    assert env_keys.run(project)[0].severity.value == "info"


def test_missing_and_placeholder(project):
    (project / ".gitignore").write_text(".env\n")
    (project / ".env.example").write_text("A=your_key\nB=x\nC=y\n")
    (project / ".env").write_text("A=\nB=real\n")
    f = env_keys.run(project)
    details = " ".join(x.detail for x in f)
    assert "Missing: C" in details and "Needs a real value: A" in details


def test_values_never_leak(project):
    (project / ".gitignore").write_text(".env\n")
    (project / ".env.example").write_text("TOKEN=a\n")
    (project / ".env").write_text("TOKEN=super-secret-value-123\nEXTRA=other-secret\n")
    blob = " ".join(x.model_dump_json() for x in env_keys.run(project))
    assert "super-secret-value-123" not in blob and "other-secret" not in blob


def test_bom_env_file(project):
    (project / ".gitignore").write_text(".env\n")
    (project / ".env.example").write_text("OPENAI_API_KEY=your_key\n")
    (project / ".env").write_bytes(b"\xef\xbb\xbfOPENAI_API_KEY=sk-real\n")
    assert "All expected variables are set" in titles(env_keys.run(project))


def test_real_values_with_placeholder_words_pass(project):
    (project / ".gitignore").write_text(".env\n")
    (project / ".env.example").write_text("DB=a\nNAME=b\n")
    (project / ".env").write_text("DB=postgres://h/todo_db\nNAME=my_example_app\n")
    assert "All expected variables are set" in titles(env_keys.run(project))


def test_parse_formats(project):
    values, dups = env_keys.parse_env(
        "export A=1\nB = 'quoted value'\nC=val # comment\nD=\"x # not comment\"\n# note\nbad line\nA=2\n"
    )
    assert values == {"A": "2", "B": "quoted value", "C": "val", "D": "x # not comment"} and dups == ["A"]


def test_unset_detection():
    for v in ["", "your_key", "<token>", "changeme", "xxxx", "***", "paste_key_here", "None"]:
        assert env_keys.is_unset(v), v
    for v in ["sk-abc123", "postgres://h/todo", "localhost", "8080"]:
        assert not env_keys.is_unset(v), v


def test_not_gitignored_in_repo_is_critical(project):
    (project / ".git").mkdir()
    (project / ".env").write_text("A=1\n")
    assert by_severity(env_keys.run(project), "critical")


def test_not_gitignored_outside_repo_is_warning(project):
    (project / ".env").write_text("A=1\n")
    f = env_keys.run(project)
    # tmp dirs are not in a git repo, so this must be a warning, not critical
    assert any(x.severity.value == "warning" and "gitignore" in x.title for x in f)


def test_gitignore_variants(project):
    for pattern, expected in [(".env", True), (".env*", True), ("*.env", True), ("/.env", True),
                              ("**/.env", True), (".env/", False), ("# .env", False), (".venv/", False)]:
        (project / ".gitignore").write_text(pattern + "\n")
        assert env_keys._gitignore_covers_env(project) is expected, pattern


def test_gitignore_negation(project):
    (project / ".gitignore").write_text(".env\n!.env\n")
    assert env_keys._gitignore_covers_env(project) is False


def test_secret_in_template_flagged_without_value(project):
    (project / ".gitignore").write_text(".env\n")
    secret = "sk-" + "a1B2c3D4e5F6g7H8i9J0k1L2"
    (project / ".env.example").write_text(f"OPENAI_API_KEY={secret}\n")
    f = env_keys.run(project)
    assert by_severity(f, "critical")
    assert secret not in " ".join(x.model_dump_json() for x in f)


def test_template_without_env(project):
    (project / ".env.example").write_text("A=1\n")
    assert ".env is missing" in titles(env_keys.run(project))


def test_env_without_template_uses_own_keys(project):
    (project / ".gitignore").write_text(".env\n")
    (project / ".env").write_text("A=1\nB=\n")
    assert by_severity(env_keys.run(project), "critical")


def test_alt_template_names(project):
    (project / ".gitignore").write_text(".env\n")
    (project / ".env.sample").write_text("A=1\n")
    (project / ".env").write_text("B=1\n")
    assert any("Missing: A" in x.detail for x in env_keys.run(project))


def test_duplicates_reported(project):
    (project / ".gitignore").write_text(".env\n")
    (project / ".env.example").write_text("A=1\n")
    (project / ".env").write_text("A=1\nA=2\n")
    assert "Duplicate variables in .env" in titles(env_keys.run(project))


def test_many_missing_is_truncated(project):
    (project / ".gitignore").write_text(".env\n")
    (project / ".env.example").write_text("\n".join(f"K{i}=1" for i in range(100)))
    (project / ".env").write_text("X=1\n")
    assert "more)" in " ".join(x.detail for x in env_keys.run(project))
