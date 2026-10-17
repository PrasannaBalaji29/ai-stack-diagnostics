"""Detects a project's AI stack by reading requirements and imports. Read-only."""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path

from packaging.utils import canonicalize_name

from diagnostics.requirements import collect_requirements
from diagnostics.safety import read_text, safe_file

MAX_FILES = 300
MAX_DIRS = 5000
MAX_BYTES = 500_000
SKIP_DIRS = {
    "node_modules", "__pycache__", "site-packages", "dist", "build", "__pypackages__",
    "venv", "env", ".venv",
}

# canonical package name -> (label, category, default port)
SIGNATURES: dict[str, tuple[str, str, int | None]] = {
    "fastapi": ("FastAPI", "web", 8000),
    "uvicorn": ("Uvicorn", "web", 8000),
    "flask": ("Flask", "web", 5000),
    "streamlit": ("Streamlit", "web", 8501),
    "gradio": ("Gradio", "web", 7860),
    "langchain": ("LangChain", "llm-framework", None),
    "langgraph": ("LangGraph", "llm-framework", None),
    "llama-index": ("LlamaIndex", "llm-framework", None),
    "crewai": ("CrewAI", "llm-framework", None),
    "dspy": ("DSPy", "llm-framework", None),
    "autogen": ("AutoGen", "llm-framework", None),
    "openai": ("OpenAI API", "llm-api", None),
    "anthropic": ("Anthropic API", "llm-api", None),
    "google-generativeai": ("Gemini API", "llm-api", None),
    "ollama": ("Ollama", "local-llm", 11434),
    "vllm": ("vLLM", "local-llm", 8000),
    "llama-cpp-python": ("llama.cpp", "local-llm", None),
    "torch": ("PyTorch", "ml", None),
    "tensorflow": ("TensorFlow", "ml", None),
    "transformers": ("Hugging Face Transformers", "ml", None),
    "sentence-transformers": ("Sentence-Transformers", "ml", None),
    "scikit-learn": ("scikit-learn", "ml", None),
    "xgboost": ("XGBoost", "ml", None),
    "lightgbm": ("LightGBM", "ml", None),
    "chromadb": ("ChromaDB", "vector-db", 8001),
    "qdrant-client": ("Qdrant", "vector-db", 6333),
    "faiss-cpu": ("FAISS", "vector-db", None),
    "faiss": ("FAISS", "vector-db", None),
    "pinecone": ("Pinecone", "vector-db", None),
    "pymilvus": ("Milvus", "vector-db", 19530),
    "weaviate-client": ("Weaviate", "vector-db", None),
    "mlflow": ("MLflow", "mlops", 5000),
    "wandb": ("Weights & Biases", "mlops", None),
    "mcp": ("MCP", "protocol", None),
    "fastmcp": ("FastMCP", "protocol", None),
}
# any package whose name starts with the prefix maps to the signature key
PREFIXES = {"langchain-": "langchain", "llama-index-": "llama-index", "torchvision": "torch", "torchaudio": "torch"}
# import name -> package name, where they differ
IMPORT_ALIASES = {
    "sentence_transformers": "sentence-transformers",
    "llama_index": "llama-index",
    "qdrant_client": "qdrant-client",
    "llama_cpp": "llama-cpp-python",
    "sklearn": "scikit-learn",
    "faiss": "faiss-cpu",
    "weaviate": "weaviate-client",
    "google.generativeai": "google-generativeai",
}


def _walk_sources(project: Path) -> tuple[list[Path], list[Path], bool]:
    py_files: list[Path] = []
    notebooks: list[Path] = []
    dirs_seen = 0
    for root, dirnames, filenames in os.walk(project, followlinks=False):
        dirnames[:] = [
            d for d in dirnames
            if d not in SKIP_DIRS and not d.startswith(".") and not (Path(root) / d / "pyvenv.cfg").exists()
        ]
        dirs_seen += 1
        if dirs_seen > MAX_DIRS:
            return py_files, notebooks, True
        for filename in filenames:
            suffix = Path(filename).suffix
            if suffix not in (".py", ".ipynb"):
                continue
            path = Path(root) / filename
            try:
                if path.is_symlink() or path.stat().st_size > MAX_BYTES:
                    continue
            except OSError:
                continue
            (py_files if suffix == ".py" else notebooks).append(path)
            if len(py_files) + len(notebooks) >= MAX_FILES:
                return py_files, notebooks, True
    return py_files, notebooks, False


def _notebook_source(path: Path) -> str:
    try:
        data = json.loads(read_text(path) or "")
    except (ValueError, TypeError):
        return ""
    lines: list[str] = []
    for cell in data.get("cells", []) if isinstance(data, dict) else []:
        if isinstance(cell, dict) and cell.get("cell_type") == "code":
            source = cell.get("source", "")
            source = "".join(source) if isinstance(source, list) else str(source)
            lines.extend(l for l in source.splitlines() if not l.lstrip().startswith(("%", "!")))
    return "\n".join(lines)


def _imports(source: str) -> set[str]:
    try:
        tree = ast.parse(source)
    except Exception:  # noqa: BLE001 - syntax errors, null bytes, deep nesting
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module]
        else:
            continue
        for name in names:
            top = name.split(".")[0]
            found.add(IMPORT_ALIASES.get(name, IMPORT_ALIASES.get(top, top)))
    return found


def _local_modules(project: Path) -> set[str]:
    try:
        return {p.stem if p.is_file() else p.name for p in project.iterdir() if p.is_dir() or p.suffix == ".py"}
    except OSError:
        return set()


def _match(package: str) -> str | None:
    name = str(canonicalize_name(package))
    if name in SIGNATURES:
        return name
    for prefix, key in PREFIXES.items():
        if name.startswith(prefix):
            return key
    return None


def scan(project: Path) -> dict:
    py_files, notebooks, truncated = _walk_sources(project)
    local = {str(canonicalize_name(n)) for n in _local_modules(project)}

    imported: set[str] = set()
    for path in py_files:
        imported |= _imports(read_text(path) or "")
    for path in notebooks:
        imported |= _imports(_notebook_source(path))
    imported = {i for i in imported if str(canonicalize_name(i)) not in local}

    reqs = collect_requirements(project)
    packages = {r.name for r in reqs.requirements} | imported

    keys = {k for k in (_match(p) for p in packages) if k}
    stack: list[str] = []
    categories: dict[str, list[str]] = {}
    ports: set[int] = set()
    for key in SIGNATURES:  # stable, readable order
        if key not in keys:
            continue
        label, category, port = SIGNATURES[key]
        if label in stack:
            continue
        stack.append(label)
        categories.setdefault(category, []).append(label)
        if port:
            ports.add(port)

    has_env = safe_file(project, ".env") is not None
    has_template = any(safe_file(project, n) for n in (".env.example", ".env.sample", ".env.template", ".env.dist"))
    checks = ["check_python_env"]
    if has_env or has_template:
        checks.append("check_env_and_keys")
    if ports:
        checks.append("check_ports")
    if "Ollama" in stack:
        checks.append("check_ollama")

    return {
        "project": project.name,
        "python_files_scanned": len(py_files),
        "notebooks_scanned": len(notebooks),
        "truncated": truncated,
        "has_requirements_txt": safe_file(project, "requirements.txt") is not None,
        "has_pyproject": safe_file(project, "pyproject.toml") is not None,
        "has_env_file": has_env,
        "has_env_template": has_template,
        "detected_stack": stack,
        "categories": categories,
        "suggested_ports": sorted(ports),
        "suggested_checks": checks,
        "notes": reqs.notes,
    }
