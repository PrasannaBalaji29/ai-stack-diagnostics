import json

import pytest

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


def test_torch_project_suggests_gpu_check(project):
    (project / "requirements.txt").write_text("torch\n")
    assert "check_gpu" in S.scan(project)["suggested_checks"]
    (project / "app.py").write_text("import torchvision\n")
    assert "check_gpu" in S.scan(project)["suggested_checks"]


def test_non_torch_project_does_not_suggest_gpu_check(project):
    (project / "requirements.txt").write_text("requests\n")
    assert "check_gpu" not in S.scan(project)["suggested_checks"]


def test_other_libraries_lists_every_unknown_import(project):
    (project / "app.py").write_text("import cv2\nimport matplotlib.pyplot as plt\nfrom keras import layers\nimport os\nimport openai\n")
    r = S.scan(project)
    assert {"cv2", "matplotlib", "keras"} <= set(r["other_libraries"])
    assert "os" not in r["other_libraries"] and "openai" not in r["other_libraries"]  # stdlib and known stack excluded


def test_other_libraries_excludes_local_modules_and_removed_stdlib(project):
    (project / "helpers.py").write_text("")
    (project / "app.py").write_text("import helpers\nimport distutils\nimport somethirdparty\n")
    assert S.scan(project)["other_libraries"] == ["somethirdparty"]


def test_scan_suggests_imports_and_compat(project, make_venv):
    (project / "app.py").write_text("import os\n")
    r = S.scan(project)
    assert "check_imports" in r["suggested_checks"] and "check_compat" not in r["suggested_checks"] and r["has_venv"] is False
    make_venv(project)
    r = S.scan(project)
    assert "check_compat" in r["suggested_checks"] and r["has_venv"] is True


def test_empty_folder_suggests_only_python_env(project):
    assert S.scan(project)["suggested_checks"] == ["check_python_env"]


@pytest.mark.parametrize("requirement,label", [
    ("tensorflow", "TensorFlow"), ("tensorflow-cpu", "TensorFlow"), ("jax", "JAX"), ("jaxlib", "JAX"),
    ("jax-cuda12-plugin", "JAX"), ("onnxruntime", "ONNX Runtime"), ("onnxruntime-gpu", "ONNX Runtime"),
    ("cupy-cuda12x", "CuPy"),
])
def test_other_gpu_libraries_suggest_the_gpu_check(project, requirement, label):
    (project / "requirements.txt").write_text(f"{requirement}\n")
    r = S.scan(project)
    assert label in r["detected_stack"] and "check_gpu" in r["suggested_checks"]


@pytest.mark.parametrize("requirement,label", [
    ("transformers", "Hugging Face Transformers"), ("sentence-transformers", "Sentence-Transformers"),
    ("vllm", "vLLM"), ("llama-cpp-python", "llama.cpp"), ("bitsandbytes", "bitsandbytes"), ("flash-attn", "FlashAttention"),
])
def test_libraries_that_use_the_gpu_through_pytorch_suggest_the_gpu_check(project, requirement, label):
    (project / "requirements.txt").write_text(f"{requirement}\n")
    r = S.scan(project)
    assert label in r["detected_stack"] and "check_gpu" in r["suggested_checks"]


def test_flash_attn_import_is_detected(project):
    (project / "app.py").write_text("import flash_attn\n")
    assert "FlashAttention" in S.scan(project)["detected_stack"]


def test_a_plain_rag_project_still_skips_the_gpu_check(project):
    (project / "requirements.txt").write_text("langchain\nchromadb\nopenai\nstreamlit\n")
    assert "check_gpu" not in S.scan(project)["suggested_checks"]
