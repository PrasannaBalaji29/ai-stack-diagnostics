"""explain_error: paste an error or traceback, get the likely cause and the fix in plain words.

Read-only and offline. The pasted text is only matched against a fixed list of known errors; it is never
executed, never sent anywhere, and never copied into the answer (only a few validated names such as a module
name, a port number or a model name are reused). If a project folder is given, the answer is sharpened with
what that folder actually has (is the missing library installed in its venv? is there a local file with that name?).

Safety rules: the text is capped, only the last lines are read (the real error is at the bottom of a
traceback), every pattern is bounded and runs on one short line at a time, and anything that looks like an
API key is reported as a warning without ever being repeated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from packaging.utils import canonicalize_name

from diagnostics.checks.python_env import _find_venvs, _installed
from diagnostics.models import Finding, Severity
from diagnostics.requirements import collect_requirements
from diagnostics.scan import IMPORT_ALIASES, REMOVED_STDLIB, _local_modules

CHECK = "explain_error"
MAX_CHARS = 20_000
MAX_LINES = 400
MAX_LINE_CHARS = 600
MAX_MATCHES = 4
_SAFE_VALUE = re.compile(r"^[A-Za-z0-9_.:/\-]{1,80}$")

# import name -> pip package name, where they differ (more are in scan.IMPORT_ALIASES)
_EXTRA_ALIASES = {
    "cv2": "opencv-python", "PIL": "pillow", "yaml": "pyyaml", "bs4": "beautifulsoup4", "dotenv": "python-dotenv",
    "fitz": "pymupdf", "docx": "python-docx", "dateutil": "python-dateutil", "skimage": "scikit-image",
    "jwt": "pyjwt", "git": "gitpython", "Crypto": "pycryptodome", "OpenSSL": "pyopenssl",
    "psycopg2": "psycopg2-binary", "serial": "pyserial", "magic": "python-magic", "attr": "attrs",
}

_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"\bhf_[A-Za-z0-9]{30,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{20,}"),
    re.compile(r"(?i)(?:api[_-]?key|secret|token|password)\b\s*[=:]\s*['\"]?[A-Za-z0-9_\-+]{16,}"),
    re.compile(r"-----BEGIN [A-Z ]{0,30}PRIVATE KEY-----"),
)


@dataclass(frozen=True)
class Rule:
    id: str
    patterns: tuple[str, ...]
    title: str
    cause: str
    fix: str
    severity: Severity = Severity.warning
    suppresses: tuple[str, ...] = ()
    compiled: tuple[re.Pattern[str], ...] = field(init=False, repr=False, default=())

    def __post_init__(self) -> None:
        object.__setattr__(self, "compiled", tuple(re.compile(p, re.IGNORECASE) for p in self.patterns))


@dataclass
class Match:
    rule: Rule
    line_no: int
    groups: dict[str, str]


@dataclass
class ProjectContext:
    local: set[str]
    installed: set[str]
    declared: set[str]


class _Safe(dict):
    def __missing__(self, key: str) -> str:
        return "that name"


# ----------------------------------------------------------------- the rules
# Plain words, no jargon. {module}, {name}, {port}, {model}, {lib}, {pkg}, {cmd}, {var} come from the error text
# (only after they pass a strict character check).

RULES: tuple[Rule, ...] = (
    Rule("module_not_found", (r"No module named ['\"](?P<module>[A-Za-z_][\w.]{0,80})['\"]",),
         "A Python library is missing", "Python cannot find '{module}'.", "python -m pip install {pkg}"),
    Rule("circular_import", (r"partially initialized module ['\"](?P<module>[\w.]{1,60})['\"]",
                              r"most likely due to a circular import"),
         "A file is importing itself, or your file has the same name as a library",
         "Python found a half-loaded module. Usually a file in your project is named like a library "
         "(torch.py, openai.py, chromadb.py ...), or two of your files import each other.",
         "Rename the file that shadows the library, delete its __pycache__ folder, and run again",
         suppresses=("cannot_import_name", "module_not_found")),
    Rule("cannot_import_name", (r"cannot import name ['\"](?P<name>\w{1,60})['\"] from ['\"](?P<source>[\w.]{1,80})['\"]",),
         "A library no longer has (or does not yet have) that name",
         "'{name}' cannot be imported from '{source}'. The library was upgraded or downgraded and the name moved or "
         "was removed. LangChain, OpenAI and Pydantic changed many import paths.",
         "Check the library's version with: python -m pip show {source}. For langchain imports try the new packages "
         "(langchain-community, langchain-openai, langchain-text-splitters). Otherwise pin the version your code was written for"),
    Rule("dll_load", (r"DLL load failed while importing (?P<module>\w{1,60})",),
         "Windows could not load a library file (DLL)",
         "'{module}' needs a Windows runtime file that is missing, or it was built for a different Python or CPU.",
         "Install the latest Microsoft Visual C++ Redistributable (x64), then: python -m pip install --force-reinstall {module}"),
    Rule("shared_object", (r"(?P<lib>lib[\w.+\-]{1,60}\.so[\w.]{0,20}): cannot open shared object file",),
         "A system library is missing (Linux)", "The program needs {lib}, which is not installed on this machine.",
         "Install the system package that provides {lib} (on Ubuntu: apt search {lib}), or reinstall the Python package that needs it"),
    Rule("numpy2", (r"compiled using NumPy 1\.x cannot be run in", r"numpy\.dtype size changed",
                    r"numpy\.core\.multiarray failed to import"),
         "NumPy version clash",
         "A library was built for a different NumPy major version than the one installed (NumPy 2 broke many older libraries).",
         'python -m pip install "numpy<2"   (or upgrade the library that complains to a version that supports NumPy 2)'),
    Rule("pydantic_settings", (r"has been moved to the .{0,3}pydantic-settings", r"BaseSettings.{0,40}pydantic-settings"),
         "Pydantic 2 moved BaseSettings", "In Pydantic 2, BaseSettings lives in its own package.",
         "python -m pip install pydantic-settings   then: from pydantic_settings import BaseSettings"),
    Rule("openai_v1", (r"You tried to access openai\.\w+, but this is no longer supported in openai>=1\.0\.0",
                       r"module 'openai' has no attribute '(ChatCompletion|Completion|Embedding)'"),
         "Your code uses the old OpenAI library style",
         "openai 1.0 and newer removed openai.ChatCompletion and similar calls.",
         "Use the new style: from openai import OpenAI; client = OpenAI(); client.chat.completions.create(...). "
         "Or pin the old library: python -m pip install \"openai<1\""),
    Rule("unicode_decode", (r"UnicodeDecodeError: 'charmap' codec can't decode",),
         "A file was read with the wrong text encoding",
         "Windows opens text files with the 'cp1252' encoding by default, but the file is UTF-8.",
         "Open files with encoding: open(path, encoding='utf-8'). Or set PYTHONUTF8=1 ($env:PYTHONUTF8=1 in PowerShell)"),
    Rule("unicode_encode", (r"UnicodeEncodeError: 'charmap' codec can't encode",),
         "Windows console cannot print that character", "The text has characters the Windows console encoding cannot show.",
         "$env:PYTHONUTF8=1 in PowerShell before running, or write to a file with encoding='utf-8'"),
    Rule("permission", (r"PermissionError: \[(WinError 5|Errno 13)\]",),
         "Permission denied", "The file or folder is locked by another program, read-only, or protected.",
         "Close the program using it (Excel, an editor, another Python process), pick a folder you own, or avoid system folders"),
    Rule("file_not_found", (r"FileNotFoundError: \[(Errno 2|WinError 2|WinError 3)\]",),
         "A file or folder was not found",
         "The path in the code does not exist from where you run it. Relative paths depend on the folder you start from.",
         "Print os.getcwd() to see where you are, use full paths or Path(__file__).parent / 'name', and check spelling"),
    Rule("port_in_use", (r"Errno 98\b", r"Errno 10048|WinError 10048", r"Address already in use",
                         r"only one usage of each socket address"),
         "That port is already in use", "Another program (often your own earlier run) is still listening on port {port}.",
         "Find it: netstat -ano | findstr :{port}   then stop it: taskkill /PID <number> /F   (or start on another port)"),
    Rule("ssl_cert", (r"CERTIFICATE_VERIFY_FAILED", r"certificate verify failed"),
         "HTTPS certificate check failed",
         "Python does not trust the certificate. Common causes: a company or school proxy, antivirus HTTPS scanning, or an old certificate list.",
         "python -m pip install -U certifi. If you are behind a proxy, ask for its root certificate. Do not turn verification off"),
    Rule("ollama_unreachable", (r"11434[^\n]{0,200}(refused|ConnectError|Failed to connect|WinError 10061|not running)",
                                r"(refused|ConnectError|Failed to connect|WinError 10061)[^\n]{0,200}11434",
                                r"Failed to connect to Ollama"),
         "Ollama is not running", "Nothing is listening on Ollama's port 11434.",
         "Start Ollama (the app, or: ollama serve), then check with: ollama list"),
    Rule("ollama_model_missing", (r"model ['\"]?(?P<model>[\w.:/\-]{1,80})['\"]? not found, try pulling it first",),
         "The Ollama model is not downloaded", "Ollama does not have the model '{model}' yet.",
         "ollama pull {model}   (and check the exact name with: ollama list)"),
    Rule("hf_gated", (r"gated repo", r"Access to model [\w./\-]{1,80} is restricted", r"Repository Not Found for url: https://huggingface\.co"),
         "Hugging Face needs you to log in or accept the model's terms",
         "The model is gated (you must accept its license on the model page) or private, or the name is wrong.",
         "Accept the terms on the model's Hugging Face page, then: huggingface-cli login (or set HF_TOKEN). Check the model name",
         suppresses=("auth_401",)),
    Rule("hf_offline", (r"couldn't connect to ['\"]https://huggingface\.co", r"We couldn't connect to 'https://huggingface\.co"),
         "Cannot reach Hugging Face", "No internet, a blocked network, or Hugging Face is unreachable.",
         "Check the connection or proxy. If the model is already downloaded, set HF_HUB_OFFLINE=1 to use the local copy"),
    Rule("auth_401", (r"Incorrect API key provided", r"invalid[_ ]api[_ ]key", r"invalid x-api-key", r"authentication_error",
                      r"API key not valid", r"\b401\b[^\n]{0,60}Unauthorized"),
         "The API key was rejected", "The key is wrong, expired, belongs to another provider, or was not loaded (an empty or old value is used).",
         "Check the key in your provider's dashboard, make sure .env is loaded (load_dotenv()), and that the variable name is exact. "
         "Never paste the key into chats"),
    Rule("quota", (r"insufficient_quota", r"exceeded your current quota", r"credit balance is too low"),
         "The account has no credit or quota left", "The key works, but the account is out of paid credit or its free quota.",
         "Add credit or check the plan and usage page of the provider"),
    Rule("rate_limit", (r"rate_limit_exceeded", r"RateLimitError", r"\b429\b[^\n]{0,60}(Too Many Requests|rate)"),
         "Too many requests too fast", "The provider limits how many requests (or tokens) per minute you can send.",
         "Wait and retry with a growing delay (exponential backoff), send fewer or smaller requests, or ask for a higher limit"),
    Rule("model_404", (r"The model `(?P<model>[\w.:\-]{1,80})` does not exist", r"not_found_error[^\n]{0,80}model",
                       r"models/[\w.\-]{1,80} is not found for API version"),
         "The model name is wrong, retired, or not available to your account",
         "The provider says it has no such model (model: {model}).",
         "Copy the exact model name from the provider's current model list; old names get retired"),
    Rule("context_length", (r"maximum context length", r"context_length_exceeded", r"prompt is too long",
                            r"input is too long", r"exceeds the context window", r"Request too large"),
         "The text sent to the model is too long", "The prompt plus the retrieved text is bigger than the model can read at once.",
         "Use smaller chunks, retrieve fewer chunks (lower k), trim the chat history, or use a model with a larger context"),
    Rule("cuda_oom", (r"CUDA out of memory", r"OutOfMemoryError", r"CUDA error: out of memory", r"HIP out of memory"),
         "The GPU ran out of memory", "The model and data do not fit in the GPU memory, or another program is using it.",
         "Use a smaller batch size or model (or a quantized one), close other GPU programs, restart the notebook. "
         "Run the check_gpu tool to see how full the GPU is"),
    Rule("torch_no_cuda", (r"Torch not compiled with CUDA enabled", r"Found no NVIDIA driver on your system"),
         "PyTorch cannot use a GPU here",
         "Either the CPU-only PyTorch is installed, or there is no NVIDIA GPU or driver on this machine.",
         "Run the check_gpu tool. On a machine with an NVIDIA GPU install the CUDA build from https://pytorch.org/get-started/locally/; "
         "otherwise run on the CPU"),
    Rule("cuda_driver_old", (r"CUDA driver version is insufficient", r"NVIDIA driver on your system is too old"),
         "The NVIDIA driver is too old", "The library needs a newer CUDA than your driver supports.",
         "Update the NVIDIA driver, then run the check_gpu tool to compare"),
    Rule("cuda_no_kernel", (r"no kernel image is available for execution on the device",),
         "This PyTorch build does not support your GPU model",
         "The installed build has no code for your GPU's architecture (the GPU is too new or too old for it).",
         "Install a PyTorch build that supports your GPU: https://pytorch.org/get-started/locally/"),
    Rule("cudnn_missing", (r"Could not load (library )?(?P<lib>cudnn[\w_.]{0,40})", r"Unable to load cuDNN",
                           r"Could not locate cudnn"),
         "The cuDNN library could not be loaded", "cuDNN is missing, or a different CUDA version of it is found first.",
         "Reinstall the library with its CUDA extras (for example tensorflow[and-cuda]) in a clean venv, and run check_gpu"),
    Rule("chroma_dimension", (r"Embedding dimension (?P<got>\d+) does not match collection dimensionality (?P<want>\d+)",),
         "The vector database was built with a different embedding model",
         "The collection stores vectors of size {want}, but the current embedding model makes size {got}.",
         "Use the same embedding model as when you built the index, or delete the old collection/folder and re-index"),
    Rule("chroma_sqlite", (r"unsupported version of sqlite3", r"sqlite3[^\n]{0,30}>= ?3\.35"),
         "ChromaDB needs a newer SQLite", "The SQLite built into this Python is too old for ChromaDB.",
         "Use a newer Python build, or: python -m pip install pysqlite3-binary and swap it in at the top of your script"),
    Rule("python_multipart", (r"Form data requires .{0,3}python-multipart",),
         "FastAPI needs python-multipart for forms and uploads", "File upload and form routes need an extra package.",
         "python -m pip install python-multipart"),
    Rule("event_loop", (r"asyncio\.run\(\) cannot be called from a running event loop", r"This event loop is already running"),
         "An async loop is already running",
         "You called asyncio.run() inside something that already has a loop (Jupyter, Streamlit, FastAPI).",
         "In a notebook use: await your_function(). In other code call the async function directly, or use nest_asyncio as a last resort"),
    Rule("json_decode", (r"JSONDecodeError: (Expecting value|Extra data|Unterminated string|Expecting property name)",),
         "The text is not valid JSON",
         "The program tried to read JSON but got something else: an empty reply, an HTML error page, or a model answer with extra words.",
         "Print the raw text before json.loads to see what came back; ask the model for JSON only, or use the provider's JSON/structured-output mode"),
    Rule("env_missing", (r"api_key client option must be set either by passing api_key to the client or by setting the (?P<var>[A-Z][A-Z0-9_]{2,60}) environment variable",
                         r"KeyError: ['\"](?P<var>[A-Z][A-Z0-9_]{2,60})['\"]"),
         "An environment variable is not set",
         "The code reads {var}, but it is missing in the environment of this run.",
         "Put it in .env and call load_dotenv() before using it, or set it: $env:{var}=\"...\" in PowerShell. "
         "Run the check_env_and_keys tool to compare .env with .env.example"),
    Rule("mcp_spawn", (r"spawn (?P<cmd>[\w.\-]{1,40}) ENOENT",),
         "Claude Desktop cannot start the MCP server command",
         "The command '{cmd}' is not found by Claude Desktop (it does not use your terminal's PATH or venv).",
         "In claude_desktop_config.json use the full path to the venv's python (C:\\...\\.venv\\Scripts\\python.exe) and the full path to server.py"),
    Rule("mcp_stdout", (r"Unexpected token[^\n]{0,80}is not valid JSON",),
         "The MCP server printed text that is not protocol data",
         "MCP over stdio uses stdout only for protocol messages. A print() or a log line on stdout breaks it.",
         "Remove print() calls and send logs to stderr: logging.basicConfig(stream=sys.stderr)"),
    Rule("mcp_disconnected", (r"Could not attach to MCP server", r"Server (transport )?closed unexpectedly", r"Server disconnected"),
         "The MCP server stopped right after starting",
         "It crashed on startup, or the command, path or Python in the config is wrong.",
         "Open the Claude Desktop MCP log (%APPDATA%\\Claude\\logs) and read the last lines; run the same command in a terminal to see the real error"),
    Rule("pip_managed", (r"externally-managed-environment",),
         "pip refuses to change the system Python", "The operating system protects its own Python from pip.",
         "Create and use a virtual environment: python -m venv .venv   then activate it and run pip again"),
    Rule("pip_no_version", (r"(?:No matching distribution found for|Could not find a version that satisfies the requirement) (?P<pkg>[A-Za-z0-9_.\-]{1,60})",),
         "pip cannot find a matching version",
         "There is no release of '{pkg}' for your Python version or system, or the name is misspelled, or there is no internet.",
         "Check the spelling on pypi.org, check python --version against what the package supports (very new Python versions often lack wheels), "
         "and python -m pip install -U pip"),
    Rule("build_failed", (r"Microsoft Visual C\+\+ 14\.0 or greater is required", r"Failed building wheel for (?P<pkg>[A-Za-z0-9_.\-]{1,60})"),
         "A package had to be compiled and that failed",
         "No ready-made wheel exists for your Python version, so pip tried to build it and the build tools are missing.",
         "Use a Python version the package supports (often one or two versions older), or install 'Microsoft C++ Build Tools'"),
    Rule("ps_policy", (r"running scripts is disabled on this system", r"PSSecurityException"),
         "PowerShell blocks scripts like Activate.ps1", "Windows script execution policy is set to Restricted.",
         "Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned   (then open a new terminal)"),
    Rule("cmd_not_found", (r"['\"]?(?P<cmd>python|pip|py|node|npm|git)['\"]? is not recognized as (the name of )?an? (internal or external command|cmdlet)",),
         "Windows cannot find the command '{cmd}'", "It is not installed or not on PATH (or only the Microsoft Store shortcut exists).",
         "Reinstall it with 'Add to PATH' ticked, or use the launcher: py -m pip ...  Open a new terminal afterwards"),
    Rule("streamlit_config", (r"set_page_config\(\) can only be called once per app page",),
         "st.set_page_config is in the wrong place", "It must be the first Streamlit command in the script and used once.",
         "Move st.set_page_config(...) to the very top, before any other st.* call and before imports that call st"),
    Rule("recursion", (r"RecursionError: maximum recursion depth exceeded",),
         "A function keeps calling itself", "Recursion without an end condition (or a very deep structure).",
         "Check the stopping condition of the recursive function; as a last resort raise sys.setrecursionlimit"),
    Rule("memory_error", (r"\bMemoryError\b",),
         "The computer ran out of RAM", "The data or model is bigger than the free memory.",
         "Process the data in smaller pieces, use a smaller or quantized model, close other programs"),
)

_RULES_BY_ID = {r.id: r for r in RULES}


# ------------------------------------------------------------------ matching


def _prepare(text: str) -> tuple[list[str], bool]:
    clean = "".join(ch if ch.isprintable() or ch in "\n\t" else " " for ch in text.replace("\r\n", "\n").replace("\r", "\n"))
    cut = len(clean) > MAX_CHARS
    clean = clean[-MAX_CHARS:]
    lines = [line[:MAX_LINE_CHARS] for line in clean.split("\n")][-MAX_LINES:]
    return lines, cut


def _safe_groups(match: re.Match[str]) -> dict[str, str]:
    return {k: v for k, v in match.groupdict().items() if v and _SAFE_VALUE.match(v)}


def match_rules(lines: list[str]) -> list[Match]:
    """Every known error found in the lines, the one nearest the bottom first."""
    found: dict[str, Match] = {}
    for line_no in range(len(lines) - 1, -1, -1):  # bottom to top: the last error is the real one
        line = lines[line_no]
        if not line.strip():
            continue
        for rule in RULES:
            if rule.id in found:
                continue
            for pattern in rule.compiled:
                hit = pattern.search(line)
                if hit:
                    found[rule.id] = Match(rule, line_no, _safe_groups(hit))
                    break
    suppressed = {s for m in found.values() for s in m.rule.suppresses}
    ordered = sorted((m for m in found.values() if m.rule.id not in suppressed), key=lambda m: -m.line_no)
    return ordered[:MAX_MATCHES]


def count_secret_lines(lines: list[str]) -> int:
    return sum(1 for line in lines if any(p.search(line) for p in _SECRET_PATTERNS))


def _last_exception_name(lines: list[str]) -> str | None:
    for line in reversed(lines):
        hit = re.match(r"^\s*(?P<name>[A-Za-z_][\w.]{0,60}(?:Error|Exception|Interrupt|Exit))\b", line)
        if hit:
            return hit.group("name")
    return None


# ------------------------------------------------------------ project context


def _context(project: Path) -> ProjectContext:
    venvs = _find_venvs(project)
    installed = {str(canonicalize_name(n)) for n in (_installed(venvs[0]) if venvs else {})}
    declared = {str(canonicalize_name(r.name)) for r in collect_requirements(project).requirements}
    local = {str(canonicalize_name(n)) for n in _local_modules(project)}
    return ProjectContext(local=local, installed=installed, declared=declared)


def _package_for(module: str) -> str:
    top = module.split(".")[0]
    return IMPORT_ALIASES.get(module) or IMPORT_ALIASES.get(top) or _EXTRA_ALIASES.get(top) or top


def _missing_module(groups: dict[str, str], ctx: ProjectContext | None) -> tuple[str, str]:
    module = groups.get("module", "that module")
    top = module.split(".")[0]
    pkg = _package_for(module)
    canonical = str(canonicalize_name(pkg))
    if top in REMOVED_STDLIB:
        major, minor = REMOVED_STDLIB[top]
        return (f"'{top}' was removed from Python's standard library in Python {major}.{minor}.",
                "python -m pip install setuptools (for distutils), or update the library that imports it, "
                f"or use a Python older than {major}.{minor}")
    if top == "pkg_resources":
        return ("pkg_resources comes from setuptools, which is not installed in this environment.",
                'python -m pip install setuptools   (if it still fails: python -m pip install "setuptools<81")')
    if ctx is not None:
        if str(canonicalize_name(top)) in ctx.local:
            return (f"'{top}' is a file or folder in your own project, so this is a path problem, not a missing library.",
                    "Run from the project's main folder, use python -m package.module instead of running a file inside a subfolder, "
                    "and check that a package folder has __init__.py")
        if canonical in ctx.installed:
            return (f"'{pkg}' IS installed in this project's virtual environment, so the code is running with a different Python.",
                    "In VS Code pick the project's interpreter (Ctrl+Shift+P, Python: Select Interpreter, choose .venv) "
                    "or run with .venv\\Scripts\\python your_file.py")
        if canonical in ctx.declared:
            return (f"'{pkg}' is listed in the project's requirements but not installed in its virtual environment.",
                    "python -m pip install -r requirements.txt   (with the venv active)")
        return (f"'{pkg}' is neither installed in the project's virtual environment nor listed in its requirements.",
                f"python -m pip install {pkg}   then add it to requirements.txt")
    return (f"Python cannot find '{module}'. It is not installed in the Python that runs the code, "
            "or you installed it into a different Python.",
            f"python -m pip install {pkg}   (using 'python -m pip' makes sure it goes into the same Python)")


def _circular(groups: dict[str, str], ctx: ProjectContext | None) -> tuple[str, str] | None:
    module = groups.get("module")
    if ctx is not None and module and str(canonicalize_name(module.split(".")[0])) in ctx.local:
        top = module.split(".")[0]
        return (f"Your project has a file or folder named '{top}', and it hides the real library of the same name.",
                f"Rename your '{top}' file or folder to something else, delete its __pycache__ folder, and run again")
    return None


def _port(text: str) -> str:
    hit = re.search(r"(?:\bport\b|\((?:'|\")[\d.:a-fA-F]{1,45}(?:'|\"),)\s*(?P<port>\d{2,5})\b", text, re.IGNORECASE)
    return hit.group("port") if hit and 1 <= int(hit.group("port")) <= 65535 else "PORT"


# ----------------------------------------------------------------------- run


def explain(error_text: str, ctx: ProjectContext | None = None) -> tuple[list[tuple[Match, str, str]], bool]:
    lines, cut = _prepare(error_text)
    full = "\n".join(lines)
    results: list[tuple[Match, str, str]] = []
    for match in match_rules(lines):
        values = _Safe(match.groups)
        cause, fix = match.rule.cause, match.rule.fix
        if match.rule.id == "module_not_found":
            values["pkg"] = _package_for(match.groups.get("module", ""))
            cause, fix = _missing_module(match.groups, ctx)
        elif match.rule.id == "circular_import":
            override = _circular(match.groups, ctx)
            if override:
                cause, fix = override
        elif match.rule.id == "port_in_use":
            values["port"] = _port(full)
        results.append((match, cause.format_map(values), fix.format_map(values)))
    return results, cut


def run(error_text: str, project: Path | None = None) -> list[Finding]:
    if not isinstance(error_text, str) or not error_text.strip():
        return [Finding(check=CHECK, severity=Severity.info, title="Nothing to explain",
                        detail="No error text was given.",
                        fix="Paste the error, ideally the last 10 to 20 lines including the final 'SomethingError: message' line")]
    lines, _ = _prepare(error_text)
    out: list[Finding] = []
    if count_secret_lines(lines):
        out.append(Finding(
            check=CHECK, severity=Severity.critical, title="The pasted text contains what looks like a secret key or password",
            detail="It is not repeated here. Treat that key as leaked, because it now exists in this chat and possibly in logs.",
            fix="Revoke or rotate the key in the provider's dashboard, put the new one in .env, and make sure .env is in .gitignore",
        ))
    ctx = _context(project) if project is not None else None
    results, cut = explain(error_text, ctx)
    for match, cause, fix in results:
        out.append(Finding(check=CHECK, severity=match.rule.severity, title=match.rule.title, detail=cause, fix=fix))
    if not results:
        name = _last_exception_name(lines)
        detail = (f"The error type is {name}, but it is not one of the known patterns." if name
                  else "No known pattern was found in this text.")
        out.append(Finding(
            check=CHECK, severity=Severity.info, title="I didn't find a known pattern in this error",
            detail=detail + " Read the very last line of the traceback first: it names the real problem.",
            fix="Paste the last 10 to 20 lines including the final error line, and tell me what you ran, so the cause can be reasoned out",
        ))
    if cut:
        out.append(Finding(check=CHECK, severity=Severity.info, title="Only the last part of the text was read",
                           detail=f"The text was longer than {MAX_CHARS} characters, so the beginning was ignored (the real error is at the bottom)."))
    return out

