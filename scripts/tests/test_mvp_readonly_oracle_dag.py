"""TOOL_ONLY source/capability risks; no financial case, database, or browser execution."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from scripts.mvp_readonly_oracle_dag import (
    FINANCIAL_CAPABILITIES,
    METRICS,
    RegistrationError,
    build_registration,
    canonical,
    inspect_capability,
    module_path,
    parse_module,
    sha,
    strict_json,
    verify_readonly_registration,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def tmp_path() -> Path:
    # Preserve TOOL_ONLY risk originals; do not run pytest's ACL-sensitive cleanup.
    path = ROOT / ".runtime/W1-readonly-DAG-installed-TOOL_ONLY-fixtures" / uuid4().hex
    path.mkdir(parents=True, exist_ok=False)
    return path


def original(root: Path, module: str, text: str) -> None:
    path = root / module_path(module)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def registered(root: Path) -> tuple[bytes, dict[str, bytes]]:
    original(root, "scripts.mvp_observations", "import json\ndef observe():\n    return {}\n")
    return build_registration(root, {"E2", "E5"})


def verify(root: Path, raw: bytes, sources: dict[str, bytes]) -> dict[str, Any]:
    return verify_readonly_registration(root, raw, sources, expected_registration_sha256=sha(raw))


def test_stdlib_positive_not_measurement(tmp_path: Path) -> None:
    raw, sources = registered(tmp_path)
    result = verify(tmp_path, raw, sources)
    assert result["status"] == "REGISTERED_NOT_EXECUTED"
    assert result["financial_effect_evidence"] is False
    assert result["metric_ids"] == ["E2", "E5"]
    assert result["initial_24_status"] == "NOT_FROZEN_NOT_RUN"


def test_registered_dependency_and_alias_resolve_exactly(tmp_path: Path) -> None:
    original(
        tmp_path,
        "scripts.mvp_trace_metrics",
        "from scripts.mvp_observations import actor_metric as original_actor\n"
        "def observe():\n    return original_actor()\n",
    )
    original(tmp_path, "scripts.mvp_observations", "def actor_metric():\n    return {}\n")
    raw, sources = build_registration(tmp_path, {"A1"})
    result = verify(tmp_path, raw, sources)
    assert result["capabilities"]["scripts.mvp_trace_metrics:observe"] == [
        "scripts.mvp_observations:actor_metric"
    ]


@pytest.mark.parametrize(
    "module",
    [
        "requests",
        "urllib.request",
        "socket",
        "httpx",
        "sqlalchemy",
        "psycopg",
        "subprocess",
        "os",
        "importlib",
        "runpy",
        "ctypes",
        "pickle",
        "marshal",
        "app.services.execution",
        "scripts.mvp_arm_executor",
        "scripts.mvp_baselines",
        "scripts.tasks",
        "scripts.fake_oracle",
    ],
)
def test_unknown_network_database_executor_dependency_rejected(tmp_path: Path, module: str) -> None:
    original(
        tmp_path,
        "scripts.mvp_observations",
        "def observe():\n    import " + module + "\n    return {}\n",
    )
    with pytest.raises(RegistrationError, match="Unregistered"):
        build_registration(tmp_path, {"E2"})


@pytest.mark.parametrize(
    "body",
    [
        "exec('pass')",
        "eval('1')",
        "__import__('json')",
        "alias = eval\n    alias('1')",
        "globals()",
        "locals()",
        "getattr(__builtins__, 'eval')('1')",
        "open('x', 'w')",
        "Path('x').write_bytes(b'x')",
        "Path('x').replace('y')",
        "path.replace('y')",
        "sys.modules['x'] = 1",
        "setattr(sys, 'x', 1)",
        "main()",
        "(lambda: 1)()",
        "vars(sys.modules)['x']()",
        "FunctionType(compile('pass', 'x', 'exec'), {})()",
        "compile('pass', 'x', 'exec')",
        "alias = compile\n    alias('pass', 'x', 'exec')",
        "calc = sys.modules['app.domain.boundary'].compute_boundary\n    calc()",
        "vars(sys.modules)",
    ],
)
def test_dynamic_or_mutating_operations_rejected(tmp_path: Path, body: str) -> None:
    original(
        tmp_path,
        "scripts.mvp_observations",
        "import sys\nfrom pathlib import Path\nfrom types import FunctionType\n"
        "def observe(path=None):\n    " + body + "\n    return {}\n",
    )
    with pytest.raises(RegistrationError):
        build_registration(tmp_path, {"E2"})


@pytest.mark.parametrize(
    "statement", ["import json as renamed", "from json import *", "from . import json"]
)
def test_ambiguous_imports_rejected(tmp_path: Path, statement: str) -> None:
    original(tmp_path, "scripts.mvp_observations", statement + "\ndef observe():\n    return {}\n")
    with pytest.raises(RegistrationError):
        build_registration(tmp_path, {"E2"})


@pytest.mark.parametrize(
    "name",
    [
        "compute_boundary",
        "select_asset",
        "revalidate_execution",
        "plan_goal_allocation",
        "plan_recovery",
        "classify_autonomy",
    ],
)
def test_financial_function_alias_is_forbidden(tmp_path: Path, name: str) -> None:
    original(
        tmp_path,
        "scripts.mvp_trace_metrics",
        "from app.domain.audit_chain import verify_epoch\n"
        "def observe():\n    return verify_epoch()\n",
    )
    original(
        tmp_path,
        "app.domain.audit_chain",
        "from app.domain.execution import "
        + name
        + " as checker\ndef verify_epoch():\n    return checker()\n",
    )
    original(tmp_path, "app.domain.execution", "def " + name + "():\n    return {}\n")
    with pytest.raises(RegistrationError, match="Financial evaluator capability"):
        build_registration(tmp_path, {"A3"})


def test_version_constant_is_fingerprint_not_evaluator(tmp_path: Path) -> None:
    original(
        tmp_path,
        "scripts.mvp_trace_metrics",
        "from app.domain.audit_chain import verify_epoch\n"
        "def observe():\n    return verify_epoch()\n",
    )
    original(
        tmp_path,
        "app.domain.audit_chain",
        "from app.domain.boundary import ALGORITHM_VERSION\n"
        "def verify_epoch():\n    return ALGORITHM_VERSION\n",
    )
    original(
        tmp_path,
        "app.domain.boundary",
        "ALGORITHM_VERSION = 'original-v1'\ndef compute_boundary():\n    return {'fake': 1}\n",
    )
    raw, sources = build_registration(tmp_path, {"A3"})
    result = verify(tmp_path, raw, sources)
    assert "app.domain.boundary:ALGORITHM_VERSION" in result["capabilities"]
    assert "app.domain.boundary:compute_boundary" not in result["capabilities"]
    assert not any("fake" in node for node in result["capabilities"])


def test_script_cannot_call_production_boundary_even_version_constant(tmp_path: Path) -> None:
    original(
        tmp_path,
        "scripts.mvp_financial_metrics",
        "from app.domain.boundary import ALGORITHM_VERSION\n"
        "def observe():\n    return ALGORITHM_VERSION\n",
    )
    with pytest.raises(RegistrationError, match="typed audit verifier"):
        build_registration(tmp_path, {"S1"})


@pytest.mark.parametrize("changed", ["path.read_text()", "b'pass'", "other.read_bytes()"])
def test_compile_requires_actual_source_bytes_pattern(changed: str) -> None:
    text = (
        "class TraceBundle:\n def source_guard(self, path):\n  compiled = compile("
        + changed
        + ", str(path), 'exec', dont_inherit=True)\n  return compiled.co_consts\n"
    )
    module = parse_module("scripts.mvp_trace_metrics", text.encode())
    with pytest.raises(RegistrationError, match="compilation"):
        inspect_capability(module, "TraceBundle")


@pytest.mark.parametrize(
    "changed",
    [
        "str(other), 'exec', dont_inherit=True",
        "str(path), 'eval', dont_inherit=True",
        "str(path), 'exec', dont_inherit=False",
        "str(path), 'exec'",
        "str(path), 'exec', dont_inherit=1",
    ],
)
def test_compile_requires_exact_nonexecuting_mode(changed: str) -> None:
    text = (
        "class TraceBundle:\n def source_guard(self, path):\n"
        "  compiled = compile(path.read_bytes(), " + changed + ")\n  return compiled.co_consts\n"
    )
    with pytest.raises(RegistrationError, match="compilation"):
        inspect_capability(parse_module("scripts.mvp_trace_metrics", text.encode()), "TraceBundle")


def test_compile_codetype_comparison_allowed_but_code_execution_refused() -> None:
    text = (
        "class TraceBundle:\n def source_guard(self, path):\n"
        "  compiled = compile(path.read_bytes(), str(path), 'exec', dont_inherit=True)\n"
        "  return compiled.co_consts\n"
    )
    _, exceptions = inspect_capability(
        parse_module("scripts.mvp_trace_metrics", text.encode()), "TraceBundle"
    )
    assert exceptions[0]["kind"] == "COMPILE_CODETYPE_COMPARE_ONLY"
    changed = text.replace("return compiled.co_consts", "return compiled()")
    with pytest.raises(RegistrationError, match="Code object execution"):
        inspect_capability(
            parse_module("scripts.mvp_trace_metrics", changed.encode()), "TraceBundle"
        )


def test_compiled_object_alias_is_not_executable() -> None:
    text = (
        "class TraceBundle:\n def source_guard(self, path):\n"
        "  compiled = compile(path.read_bytes(), str(path), 'exec', dont_inherit=True)\n"
        "  alias = compiled\n  return alias()\n"
    )
    with pytest.raises(RegistrationError, match="Code object execution"):
        inspect_capability(parse_module("scripts.mvp_trace_metrics", text.encode()), "TraceBundle")


def test_source_comparison_cannot_mutate_runtime_modules() -> None:
    text = "import sys\nclass TraceBundle:\n def source_guard(self):\n  sys.modules.clear()\n"
    with pytest.raises(RegistrationError, match="source-comparison only"):
        inspect_capability(parse_module("scripts.mvp_trace_metrics", text.encode()), "TraceBundle")


def test_function_type_alias_is_not_a_source_comparison() -> None:
    text = (
        "from types import FunctionType\nclass TraceBundle:\n def source_guard(self, code):\n"
        "  runner = FunctionType\n  return runner(code, {})()\n"
    )
    with pytest.raises(RegistrationError):
        inspect_capability(parse_module("scripts.mvp_trace_metrics", text.encode()), "TraceBundle")


def test_source_and_archived_bytes_drift_refused(tmp_path: Path) -> None:
    raw, sources = registered(tmp_path)
    key = next(iter(sources))
    altered = dict(sources, **{key: sources[key] + b"#changed\n"})
    with pytest.raises(RegistrationError, match="source bytes differ"):
        verify(tmp_path, raw, altered)
    original(tmp_path, "scripts.mvp_observations", "def observe():\n return {'different': 1}\n")
    with pytest.raises(RegistrationError, match="no longer matches"):
        verify(tmp_path, raw, sources)


@pytest.mark.parametrize(
    "field",
    [
        "metric_bindings",
        "components",
        "capabilities",
        "source_files",
        "inspection_exceptions",
        "stdlib_roots",
        "financial_effect_evidence",
    ],
)
def test_registered_graph_cannot_be_weakened_or_result_injected(tmp_path: Path, field: str) -> None:
    raw, sources = registered(tmp_path)
    value = strict_json(raw)
    value[field] = {} if isinstance(value[field], dict) else []
    altered = canonical(value)
    with pytest.raises(RegistrationError):
        verify(tmp_path, altered, sources)


def test_hash_and_inventory_must_be_external_and_exact(tmp_path: Path) -> None:
    raw, sources = registered(tmp_path)
    with pytest.raises(RegistrationError, match="manifest hash"):
        verify_readonly_registration(tmp_path, raw, sources, expected_registration_sha256="0" * 64)
    with pytest.raises(RegistrationError, match="inventory"):
        verify(tmp_path, raw, {})
    with pytest.raises(RegistrationError, match="inventory"):
        verify(tmp_path, raw, dict(sources, **{"../outside.py": b"x"}))


def test_missing_dependency_source_and_illegal_initializer_refused(tmp_path: Path) -> None:
    original(
        tmp_path,
        "scripts.mvp_trace_metrics",
        "from scripts.mvp_observations import actor_metric\n"
        "def observe():\n return actor_metric()\n",
    )
    with pytest.raises(FileNotFoundError):
        build_registration(tmp_path, {"A1"})
    registered(tmp_path)
    (tmp_path / "scripts/__init__.py").write_text("print('SIDE_EFFECT')\n", encoding="utf-8")
    with pytest.raises(RegistrationError, match="initializer"):
        build_registration(tmp_path, {"E2"})


def test_top_level_financial_execution_is_refused(tmp_path: Path) -> None:
    original(
        tmp_path,
        "scripts.mvp_trace_metrics",
        "from app.domain.audit_chain import verify_epoch\ndef observe():\n return verify_epoch()\n",
    )
    original(
        tmp_path,
        "app.domain.audit_chain",
        "from app.domain.boundary import ALGORITHM_VERSION\n"
        "def verify_epoch():\n return ALGORITHM_VERSION\n",
    )
    original(
        tmp_path,
        "app.domain.boundary",
        "from app.domain.execution import revalidate_execution\n"
        "ALGORITHM_VERSION='v1'\nBAD = revalidate_execution()\n",
    )
    with pytest.raises(RegistrationError, match="Top-level financial execution"):
        build_registration(tmp_path, {"A3"})


def test_all_four_actual_calculators_and_audit_caps_register_without_running() -> None:
    raw, originals = build_registration(ROOT, METRICS, framework=True)
    result = verify(ROOT, raw, originals)
    assert result["metric_ids"] == sorted(METRICS)
    financial = {
        key for key in result["capabilities"] if key.split(":")[0] in FINANCIAL_CAPABILITIES
    }
    assert financial
    assert all(key.split(":")[1] in FINANCIAL_CAPABILITIES[key.split(":")[0]] for key in financial)
    assert not any(
        key.endswith(
            (
                ":compute_boundary",
                ":select_asset",
                ":revalidate_execution",
                ":plan_goal_allocation",
                ":plan_recovery",
            )
        )
        for key in result["capabilities"]
    )
    assert set(result["metric_ids"]) == METRICS
    assert result["financial_effect_evidence"] is False


def test_old_stdlib_path_and_actual_four_rejections_are_retained() -> None:
    from scripts.mvp_corpus import CorpusError, independent_python

    assert "oracle" in independent_python(b"import json\ndef oracle():\n return {}\n")
    for relative in (
        "scripts/mvp_observations.py",
        "scripts/mvp_trace_metrics.py",
        "scripts/mvp_financial_metrics.py",
        "scripts/mvp_financial_oracles.py",
    ):
        with pytest.raises(CorpusError, match="unregistered evaluator"):
            independent_python((ROOT / relative).read_bytes())


def test_typing_runtime_is_original_byte_bound_and_not_just_version() -> None:
    raw, originals = build_registration(ROOT, METRICS, framework=True)
    runtime_key = next(key for key in originals if key.startswith("typed-runtime/"))
    altered = dict(originals)
    altered[runtime_key] += b"DIFFERENT_INSTALLED_RUNTIME"
    with pytest.raises(RegistrationError, match="source bytes differ"):
        verify(ROOT, raw, altered)
    value = strict_json(raw)
    value["framework"]["distributions"]["pydantic"]["version"] = "different"
    with pytest.raises(RegistrationError, match="no longer matches"):
        verify(ROOT, canonical(value), originals)


@pytest.mark.parametrize(
    "data", [b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}', b"[]", b"\xff"]
)
def test_original_json_is_strict(data: bytes) -> None:
    with pytest.raises(RegistrationError):
        strict_json(data)


def test_unknown_metric_or_source_path_refused(tmp_path: Path) -> None:
    with pytest.raises(RegistrationError):
        build_registration(tmp_path, {"S99"})
    with pytest.raises(RegistrationError):
        module_path("app.services.execution")


def test_semantic_dict_is_not_claimed_as_calculator_execution(tmp_path: Path) -> None:
    raw, sources = registered(tmp_path)
    assert "MEASURED" not in json.dumps(verify(tmp_path, raw, sources))
