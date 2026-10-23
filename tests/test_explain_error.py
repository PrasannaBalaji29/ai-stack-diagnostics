"""explain_error: known errors are recognised, answers are safe, and nothing pasted is ever echoed back."""

import json
import time

import pytest

from diagnostics.checks import explain_error as E
from tests.conftest import titles

SAMPLES = {
    "module_not_found": "ModuleNotFoundError: No module named 'cv2'",
    "circular_import": "ImportError: cannot import name 'OpenAI' from partially initialized module 'openai' (most likely due to a circular import)",
    "cannot_import_name": "ImportError: cannot import name 'Chroma' from 'langchain.vectorstores' (/x/__init__.py)",
    "dll_load": "ImportError: DLL load failed while importing _ctypes: The specified module could not be found.",
    "shared_object": "ImportError: libGL.so.1: cannot open shared object file: No such file or directory",
    "numpy2": "A module that was compiled using NumPy 1.x cannot be run in",
    "pydantic_settings": "pydantic.errors.PydanticImportError: `BaseSettings` has been moved to the `pydantic-settings` package.",
    "openai_v1": "You tried to access openai.ChatCompletion, but this is no longer supported in openai>=1.0.0",
    "unicode_decode": "UnicodeDecodeError: 'charmap' codec can't decode byte 0x9d in position 12: character maps to <undefined>",
    "unicode_encode": "UnicodeEncodeError: 'charmap' codec can't encode character '\\u2192' in position 0: character maps to <undefined>",
    "permission": "PermissionError: [WinError 5] Access is denied: 'C:\\\\x'",
    "file_not_found": "FileNotFoundError: [Errno 2] No such file or directory: 'data.csv'",
    "port_in_use": "ERROR: [Errno 10048] error while attempting to bind on address ('127.0.0.1', 8000): only one usage of each socket address",
    "ssl_cert": "ssl.SSLCertVerificationError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed",
    "ollama_unreachable": "httpx.ConnectError: [WinError 10061] No connection could be made ... http://localhost:11434",
    "ollama_model_missing": "ollama._types.ResponseError: model 'llama3.1' not found, try pulling it first",
    "hf_gated": "huggingface_hub.errors.GatedRepoError: 401 Client Error. Cannot access gated repo for url https://huggingface.co/x",
    "hf_offline": "OSError: We couldn't connect to 'https://huggingface.co' to load this file",
    "auth_401": "openai.AuthenticationError: Error code: 401 - Incorrect API key provided: ***",
    "quota": "Error code: 429 - You exceeded your current quota, please check your plan",
    "rate_limit": "anthropic.RateLimitError: Error code: 429 - rate_limit_error",
    "model_404": "openai.NotFoundError: Error code: 404 - The model `gpt-5-turbo` does not exist or you do not have access",
    "context_length": "This model's maximum context length is 8192 tokens. However, your messages resulted in 9000 tokens",
    "cuda_oom": "torch.OutOfMemoryError: CUDA out of memory. Tried to allocate 2.00 GiB",
    "torch_no_cuda": "AssertionError: Torch not compiled with CUDA enabled",
    "cuda_driver_old": "RuntimeError: The NVIDIA driver on your system is too old (found version 11040)",
    "cuda_no_kernel": "RuntimeError: CUDA error: no kernel image is available for execution on the device",
    "cudnn_missing": "Could not load library cudnn_cnn_infer64_8.dll. Error code 126",
    "chroma_dimension": "chromadb.errors.InvalidDimensionException: Embedding dimension 768 does not match collection dimensionality 384",
    "chroma_sqlite": "RuntimeError: Your system has an unsupported version of sqlite3. Chroma requires sqlite3 >= 3.35.0.",
    "python_multipart": 'RuntimeError: Form data requires "python-multipart" to be installed.',
    "event_loop": "RuntimeError: asyncio.run() cannot be called from a running event loop",
    "json_decode": "json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)",
    "env_missing": "KeyError: 'OPENAI_API_KEY'",
    "mcp_spawn": "Error: spawn python ENOENT",
    "mcp_stdout": "Unexpected token 'I', \"INFO: Star\"... is not valid JSON",
    "mcp_disconnected": "Server disconnected. For troubleshooting guidance, please visit our debugging documentation",
    "pip_managed": "error: externally-managed-environment",
    "pip_no_version": "ERROR: No matching distribution found for torch==99",
    "build_failed": "error: Microsoft Visual C++ 14.0 or greater is required. Get it with Build Tools",
    "ps_policy": "File C:\\x\\Activate.ps1 cannot be loaded because running scripts is disabled on this system.",
    "cmd_not_found": "pip : The term 'pip' is not recognized as the name of a cmdlet, function, script file",
    "streamlit_config": "StreamlitAPIException: set_page_config() can only be called once per app page",
    "recursion": "RecursionError: maximum recursion depth exceeded while calling a Python object",
    "memory_error": "MemoryError",
}


def ids(text, ctx=None):
    results, _ = E.explain(text, ctx)
    return [m.rule.id for m, _, _ in results]


def test_every_rule_has_a_sample_and_the_other_way_round():
    assert set(SAMPLES) == {r.id for r in E.RULES}


def test_rule_ids_are_unique_and_fields_are_filled():
    assert len({r.id for r in E.RULES}) == len(E.RULES)
    for r in E.RULES:
        assert r.title and r.cause and r.fix and r.compiled, r.id


def test_templates_format_even_when_no_name_was_found():
    for r in E.RULES:
        r.cause.format_map(E._Safe())
        r.fix.format_map(E._Safe())


@pytest.mark.parametrize("rule_id", sorted(SAMPLES))
def test_each_known_error_is_recognised(rule_id):
    assert rule_id in ids(SAMPLES[rule_id])


@pytest.mark.parametrize("rule_id", sorted(SAMPLES))
def test_each_known_error_gives_a_cause_and_a_fix(rule_id):
    findings = E.run(SAMPLES[rule_id])
    hit = next(f for f in findings if f.severity.value in ("warning", "critical"))
    assert hit.detail and hit.fix and "{" not in hit.detail and "{" not in hit.fix


# ------------------------------------------------------------ extracted values

def test_missing_module_maps_to_the_pip_name():
    hit = E.run("ModuleNotFoundError: No module named 'cv2'")[0]
    assert "opencv-python" in hit.fix
    assert "scikit-learn" in E.run("ModuleNotFoundError: No module named 'sklearn.ensemble'")[0].fix
    assert "pyyaml" in E.run("ModuleNotFoundError: No module named 'yaml'")[0].fix


def test_ollama_model_name_is_used_in_the_fix():
    assert "ollama pull llama3.1" in E.run(SAMPLES["ollama_model_missing"])[0].fix


def test_port_number_is_found():
    assert "8000" in E.run(SAMPLES["port_in_use"])[0].fix
    assert "8501" in E.run("OSError: [Errno 98] Address already in use\nStreamlit is running on port 8501")[0].fix
    assert "PORT" in E.run("OSError: [Errno 98] Address already in use")[0].fix


def test_env_var_name_is_used():
    assert "OPENAI_API_KEY" in E.run(SAMPLES["env_missing"])[0].fix


def test_chroma_sizes_are_explained():
    detail = E.run(SAMPLES["chroma_dimension"])[0].detail
    assert "384" in detail and "768" in detail


def test_removed_standard_library_module():
    hit = E.run("ModuleNotFoundError: No module named 'distutils'")[0]
    assert "removed" in hit.detail and "3.12" in hit.detail


def test_pkg_resources_points_to_setuptools():
    assert "setuptools" in E.run("ModuleNotFoundError: No module named 'pkg_resources'")[0].fix


# --------------------------------------------------------------------- ranking

def test_the_last_error_in_a_traceback_comes_first():
    text = ("ModuleNotFoundError: No module named 'foo'\n"
            "During handling of the above exception, another exception occurred:\n"
            "torch.OutOfMemoryError: CUDA out of memory")
    assert ids(text)[0] == "cuda_oom" and "module_not_found" in ids(text)


def test_circular_import_beats_cannot_import_name():
    found = ids(SAMPLES["circular_import"])
    assert "circular_import" in found and "cannot_import_name" not in found


def test_gated_model_beats_the_generic_401():
    found = ids(SAMPLES["hf_gated"])
    assert "hf_gated" in found and "auth_401" not in found


def test_at_most_four_explanations():
    text = "\n".join(SAMPLES[k] for k in ("cuda_oom", "ssl_cert", "json_decode", "recursion", "memory_error", "event_loop"))
    assert len(ids(text)) == E.MAX_MATCHES


def test_unknown_error_is_said_honestly():
    f = E.run("Traceback (most recent call last):\n  File 'x.py', line 1\nValueError: the moon is wrong")
    assert titles(f) == ["I didn't find a known pattern in this error"]
    assert "ValueError" in f[0].detail and "moon" not in json.dumps([x.model_dump() for x in f])


def test_empty_and_wrong_input():
    for bad in ("", "   \n  ", None, 42, b"bytes"):
        assert titles(E.run(bad)) == ["Nothing to explain"]


# ------------------------------------------------------------------ project use

def test_wrong_python_when_the_library_is_installed_in_the_venv(project, make_venv):
    make_venv(project, packages={"requests": "2.31.0"})
    ctx = E._context(project)
    hit = E.run("ModuleNotFoundError: No module named 'requests'", project)[0]
    assert "IS installed" in hit.detail and "Select Interpreter" in hit.fix
    assert "requests" in ctx.installed


def test_declared_but_not_installed(project, make_venv):
    make_venv(project, packages={})
    (project / "requirements.txt").write_text("requests\n")
    hit = E.run("ModuleNotFoundError: No module named 'requests'", project)[0]
    assert "listed in the project's requirements" in hit.detail and "-r requirements.txt" in hit.fix


def test_neither_installed_nor_declared(project, make_venv):
    make_venv(project, packages={})
    hit = E.run("ModuleNotFoundError: No module named 'requests'", project)[0]
    assert "neither installed" in hit.detail and "requirements.txt" in hit.fix


def test_a_local_module_is_a_path_problem(project):
    (project / "utils.py").write_text("x = 1\n")
    hit = E.run("ModuleNotFoundError: No module named 'utils'", project)[0]
    assert "your own project" in hit.detail and "python -m" in hit.fix


def test_a_local_file_shadowing_a_library(project):
    (project / "openai.py").write_text("x = 1\n")
    hit = E.run(SAMPLES["circular_import"], project)[0]
    assert "named 'openai'" in hit.detail and "Rename" in hit.fix


def test_without_a_project_the_answer_is_still_useful():
    hit = E.run("ModuleNotFoundError: No module named 'requests'")[0]
    assert "python -m pip install requests" in hit.fix


# -------------------------------------------------------------------- secrets

SECRETS = [
    "sk-" + "a1B2c3D4" * 5,
    "sk-ant-api03-" + "Zx9" * 14,
    "ghp_" + "A1b2C3d4E5" * 4,
    "hf_" + "AbCdEfGhIj" * 4,
    "AKIA" + "ABCDEFGHIJKLMNOP",
    "AIza" + "SyA-1234567890abcdefghijklmnopqrstu",
    "xoxb-123456789012-abcdefghijkl",
    "Authorization: Bearer " + "eyJhbGciOi" * 3,
    "OPENAI_API_KEY=" + "q1w2e3r4t5y6u7i8o9p0",
    "-----BEGIN RSA PRIVATE KEY-----",
]


@pytest.mark.parametrize("secret", SECRETS)
def test_secrets_are_warned_about_and_never_repeated(secret):
    text = f"openai.AuthenticationError: Error code: 401\nrequest headers: {secret}\n"
    findings = E.run(text)
    assert findings[0].severity.value == "critical" and "secret" in findings[0].title
    dump = json.dumps([f.model_dump() for f in findings])
    needle = secret.split(" ")[-1].split("=")[-1]
    assert needle not in dump


def test_normal_errors_do_not_trigger_the_secret_warning():
    for sample in SAMPLES.values():
        assert not any(f.severity.value == "critical" for f in E.run(sample)), sample


# ---------------------------------------------------------------------- safety

def test_injected_names_never_reach_the_answer():
    for text in ("No module named 'os; rm -rf /'", "No module named '$(whoami)'", "No module named 'a b'",
                 "No module named '{pkg}'", "model 'x; calc' not found, try pulling it first"):
        findings = E.run(text)
        dump = json.dumps([f.model_dump() for f in findings])
        assert "rm -rf" not in dump and "whoami" not in dump and "calc" not in dump, text


def test_braces_in_the_pasted_text_do_not_break_formatting():
    findings = E.run("KeyError: '{0}'\nNo module named 'x'\n{{}} {} %s %(a)s")
    assert findings


def test_huge_input_is_capped_and_the_bottom_is_still_read():
    junk = "x" * 3_000_000
    start = time.perf_counter()
    findings = E.run(junk + "\ntorch.OutOfMemoryError: CUDA out of memory")
    assert time.perf_counter() - start < 3
    assert "The GPU ran out of memory" in titles(findings) and "Only the last part of the text was read" in titles(findings)


def test_adversarial_text_is_fast():
    nasty = [
        "11434 " + "refused " * 20000, "refused " * 20000 + "11434", "No module named '" + "a" * 100000,
        "401 " + "Unauthorized " * 20000, "api_key=" + "a" * 100000, "sk-" + "a" * 100000, "\n" * 200000,
        "(" + "'1.1.1.1', " * 10000, "model '" + "a" * 100000, "UnicodeDecodeError: " * 20000,
    ]
    for text in nasty:
        start = time.perf_counter()
        E.run(text)
        assert time.perf_counter() - start < 2, text[:30]


def test_control_characters_are_harmless():
    findings = E.run("ModuleNotFoundError: No module named 'cv2'\x00\x1b[31m\x07")
    assert "opencv-python" in findings[0].fix and "\x1b" not in json.dumps([f.model_dump() for f in findings])


def test_findings_are_labelled_with_this_check():
    assert {f.check for f in E.run(SAMPLES["cuda_oom"])} == {"explain_error"}


# ----------------------------------------------------------------- the MCP tool

def test_the_tool_works_and_is_json(project):
    import server
    out = server.explain_error("ModuleNotFoundError: No module named 'requests'", str(project))
    json.dumps(out)
    assert out and out[0]["check"] == "explain_error"
    assert server.explain_error("KeyError: 'OPENAI_API_KEY'")[0]["title"] == "An environment variable is not set"


def test_the_tool_survives_a_bad_path():
    import server
    out = server.explain_error("KeyError: 'X_Y_Z'", "/definitely/not/here")
    assert out[0]["severity"] == "error" and "path" in out[0]["title"].lower()


# ---------------------------------------------------------------------- fuzzing

from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.text(max_size=3000))
def test_any_text_gives_clean_findings(text):
    findings = E.run(text)
    assert findings and all(f.check == "explain_error" and f.title and f.detail for f in findings)
    assert all("{" not in (f.fix or "") and "{" not in f.detail for f in findings if f.severity.value == "warning")


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.sampled_from(sorted(SAMPLES.values())), st.text(max_size=400), st.text(max_size=400))
def test_known_errors_are_found_inside_any_surrounding_text(sample, before, after):
    clean_before = before.replace("\n", " ")
    results, _ = E.explain(f"{clean_before}\n{sample}\n{after.replace(chr(10), ' ')}")
    assert results
