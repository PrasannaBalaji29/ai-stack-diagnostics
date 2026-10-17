import json

from diagnostics import scan as S


def test_empty_project(project):
    r = S.scan(project)
    assert r["detected_stack"] == [] and r["suggested_checks"] == ["check_python_env"]


def test_detects_from_imports_and_requirements(project):
    (project / "requirements.txt").write_text("fastapi\nchromadb==0.5\n")
    (project / "app.py").write_text("import streamlit as st\nfrom langchain_openai import ChatOpenAI\nimport torch\n")
    r = S.scan(project)
    for name in ("FastAPI", "ChromaDB", "Streamlit", "LangChain", "PyTorch"):
        assert name in r["detected_stack"], name
    assert {8000, 8001, 8501} <= set(r["suggested_ports"])
    assert "check_ports" in r["suggested_checks"]
    assert "ChromaDB" in r["categories"]["vector-db"]


def test_alias_imports(project):
    (project / "a.py").write_text("from sentence_transformers import SentenceTransformer\nimport sklearn\nimport llama_cpp\n")
    r = S.scan(project)
    assert {"Sentence-Transformers", "scikit-learn", "llama.cpp"} <= set(r["detected_stack"])


def test_venv_and_hidden_dirs_skipped(project, make_venv):
    venv = make_venv(project, name="myenv")
    (venv / "x.py").write_text("import fastapi\n")
    (project / ".hidden").mkdir()
    (project / ".hidden" / "y.py").write_text("import flask\n")
    (project / "node_modules").mkdir()
    (project / "node_modules" / "z.py").write_text("import gradio\n")
    assert S.scan(project)["detected_stack"] == []


def test_syntax_error_and_null_bytes_survive(project):
    (project / "bad.py").write_text("def broken(:\n")
    (project / "nul.py").write_bytes(b"import torch\x00\n")
    (project / "good.py").write_text("import ollama\n")
    assert "Ollama" in S.scan(project)["detected_stack"]


def test_notebook_with_magics(project):
    nb = {"cells": [
        {"cell_type": "code", "source": ["%pip install x\n", "import transformers\n"]},
        {"cell_type": "markdown", "source": ["import flask"]},
    ]}
    (project / "n.ipynb").write_text(json.dumps(nb))
    r = S.scan(project)
    assert r["detected_stack"] == ["Hugging Face Transformers"] and r["notebooks_scanned"] == 1


def test_broken_notebook_json(project):
    (project / "n.ipynb").write_text("{not json")
    S.scan(project)


def test_local_module_shadowing_ignored(project):
    (project / "torch").mkdir()
    (project / "torch" / "__init__.py").write_text("")
    (project / "app.py").write_text("import torch\n")
    assert "PyTorch" not in S.scan(project)["detected_stack"]


def test_truncation_flag(project, monkeypatch):
    monkeypatch.setattr(S, "MAX_FILES", 5)
    for i in range(20):
        (project / f"f{i}.py").write_text("x=1")
    r = S.scan(project)
    assert r["truncated"] is True and r["python_files_scanned"] <= 5


def test_oversize_file_skipped(project, monkeypatch):
    monkeypatch.setattr(S, "MAX_BYTES", 10)
    (project / "big.py").write_text("import torch\n" + "#" * 100)
    assert S.scan(project)["detected_stack"] == []


def test_env_template_triggers_env_check(project):
    (project / ".env.example").write_text("A=1\n")
    assert "check_env_and_keys" in S.scan(project)["suggested_checks"]


def test_pyproject_deps_detected(project):
    (project / "pyproject.toml").write_text('[project]\nname="x"\ndependencies=["openai>=1","qdrant-client"]\n')
    r = S.scan(project)
    assert {"OpenAI API", "Qdrant"} <= set(r["detected_stack"])


def test_ollama_project_suggests_ollama_check(project):
    (project / "app.py").write_text("import ollama\n")
    r = S.scan(project)
    assert "check_ollama" in r["suggested_checks"] and 11434 in r["suggested_ports"]
