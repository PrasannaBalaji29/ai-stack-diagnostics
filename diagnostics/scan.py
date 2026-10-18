"""Detects a project's AI stack by reading requirements and imports. Read-only."""

from __future__ import annotations

import ast
import json
import os
import sys
from pathlib import Path

from packaging.utils import canonicalize_name

from diagnostics.checks.python_env import _find_venvs
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


STDLIB_NAMES = frozenset(sys.stdlib_module_names)
# Standard-library modules that were REMOVED, with the first Python version that no longer has them.
REMOVED_STDLIB: dict[str, tuple[int, int]] = {
    **{m: (3, 12) for m in ("asynchat", "asyncore", "distutils", "imp", "smtpd")},
    **{m: (3, 13) for m in (
        "aifc", "audioop", "cgi", "cgitb", "chunk", "crypt", "imghdr", "lib2to3", "mailcap", "msilib", "nis",
        "nntplib", "ossaudiodev", "pipes", "sndhdr", "spwd", "sunau", "telnetlib", "uu", "xdrlib",
    )},
}
_OPTIONAL_CATCHES = {"ImportError", "ModuleNotFoundError", "Exception", "BaseException"}


def _catches_import_error(handler: ast.ExceptHandler) -> bool:
    kind = handler.type
    if kind is None:
        return True
    nodes = kind.elts if isinstance(kind, ast.Tuple) else [kind]
    return any(isinstance(n, ast.Name) and n.id in _OPTIONAL_CATCHES for n in nodes)


class _ImportCollector(ast.NodeVisitor):
    """Collects top-level imported names. A name is 'optional' only if every use is inside a
    try/except ImportError block (the common pattern for optional dependencies)."""

    def __init__(self) -> None:
        self.found: dict[str, bool] = {}
        self._guarded = 0

    def _add(self, name: str) -> None:
        top = name.split(".")[0]
        self.found[top] = self.found.get(top, True) and self._guarded > 0

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._add(alias.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level == 0 and node.module:
            self._add(node.module)

    def visit_Try(self, node: ast.Try) -> None:
        guarded = any(_catches_import_error(h) for h in node.handlers)
        self._guarded += guarded
        for stmt in node.body:
            self.visit(stmt)
        self._guarded -= guarded
        for other in [*node.handlers, *node.orelse, *node.finalbody]:
            self.visit(other)

    visit_TryStar = visit_Try  # type: ignore[assignment]


def raw_imports(source: str) -> dict[str, bool]:
    """Top-level imported module names -> True if only imported optionally. Never raises."""
    try:
        collector = _ImportCollector()
        collector.visit(ast.parse(source))
        return collector.found
    except Exception:  # noqa: BLE001 - syntax errors, null bytes, absurd nesting
        return {}


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
            lines.extend(ln for ln in source.splitlines() if not ln.lstrip().startswith(("%", "!")))
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
    raw: dict[str, bool] = {}
    for path in py_files:
        text = read_text(path) or ""
        imported |= _imports(text)
        for name, optional in raw_imports(text).items():
            raw[name] = raw.get(name, True) and optional
    for path in notebooks:
        text = _notebook_source(path)
        imported |= _imports(text)
        for name, optional in raw_imports(text).items():
            raw[name] = raw.get(name, True) and optional
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

    # Any other third-party library the code imports, so nothing is missed just because it is not on the list above.
    local_names = {n.lower() for n in local}
    other_imports = sorted(
        name for name in raw
        if name not in STDLIB_NAMES and name not in REMOVED_STDLIB and name.lower() not in local_names
        and _match(IMPORT_ALIASES.get(name, name)) is None
    )

    has_env = safe_file(project, ".env") is not None
    has_template = any(safe_file(project, n) for n in (".env.example", ".env.sample", ".env.template", ".env.dist"))
    has_venv = bool(_find_venvs(project))
    checks = ["check_python_env"]
    if has_env or has_template:
        checks.append("check_env_and_keys")
    if ports:
        checks.append("check_ports")
    if "Ollama" in stack:
        checks.append("check_ollama")
    if "PyTorch" in stack:
        checks.append("check_gpu")
    if py_files or notebooks:
        checks.append("check_imports")
    if has_venv:
        checks.append("check_compat")

    return {
        "project": project.name,
        "python_files_scanned": len(py_files),
        "notebooks_scanned": len(notebooks),
        "truncated": truncated,
        "has_requirements_txt": safe_file(project, "requirements.txt") is not None,
        "has_pyproject": safe_file(project, "pyproject.toml") is not None,
        "has_env_file": has_env,
        "has_env_template": has_template,
        "has_venv": has_venv,
        "detected_stack": stack,
        "other_libraries": other_imports[:80],
        "categories": categories,
        "suggested_ports": sorted(ports),
        "suggested_checks": checks,
        "notes": reqs.notes,
    }
