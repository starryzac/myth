"""Explicit source-bound readonly calculator capabilities; never execute calculators.

Registered audit verification may inspect saved typed originals. It must not call
P's financial choice, boundary, autonomy, recovery, or execution evaluators.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.metadata
import json
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROTOCOL = "bounded-funds-readonly-oracle-dag-v2"
METHOD = "REGISTERED_READONLY_SYMBOL_CAPABILITY_DAG_V1"
SCRIPT_MODULES = {
    "scripts.mvp_observations",
    "scripts.mvp_observation_v2",
    "scripts.mvp_trace_metrics",
    "scripts.mvp_financial_metrics",
    "scripts.mvp_financial_oracles",
}
AUDIT_MODULES = {
    "audit_chain",
    "audit_chain_types",
    "bank_posting_codec",
    "policy_configuration",
    "decision_trace",
    "decision_trace_types",
    "external_bank_fact",
    "external_bank_fact_types",
    "income_ledger",
    "execution_types",
    "boundary_types",
    "asset_allocation_types",
    "asset_exposure",
    "recovery_types",
    "autonomy_types",
    "boundary_details_types",
    "policy_change_types",
}
FINGERPRINT_MODULES = {
    "asset_allocation",
    "autonomy",
    "boundary",
    "execution",
    "goal_allocation",
    "recovery",
}
MODULES = SCRIPT_MODULES | {"app.domain." + name for name in AUDIT_MODULES | FINGERPRINT_MODULES}
STDLIB = {
    "__future__",
    "argparse",
    "ast",
    "calendar",
    "collections",
    "copy",
    "dataclasses",
    "datetime",
    "decimal",
    "enum",
    "fractions",
    "functools",
    "hashlib",
    "itertools",
    "json",
    "math",
    "pathlib",
    "re",
    "statistics",
    "sys",
    "tomllib",
    "types",
    "typing",
    "uuid",
    "zoneinfo",
}
FRAMEWORK_NAMES = {
    "pydantic",
    "pydantic-core",
    "annotated-types",
    "typing-extensions",
    "typing-inspection",
}
FRAMEWORK_SYMBOLS = {
    "AfterValidator",
    "AwareDatetime",
    "BaseModel",
    "BeforeValidator",
    "ConfigDict",
    "Field",
    "StrictBool",
    "StrictInt",
    "StringConstraints",
    "ValidationError",
    "field_validator",
    "model_validator",
}
FORBIDDEN_NAMES = {
    "exec",
    "eval",
    "__import__",
    "breakpoint",
    "input",
    "globals",
    "locals",
    "setattr",
    "delattr",
    "getattr",
    "getattribute",
    "import_module",
    "load_module",
    "exec_module",
    "run_path",
    "run_module",
    "FunctionType",
    "type_from_spec",
    "PyDLL",
    "CDLL",
    "open",
    "__builtins__",
    "builtins",
    "vars",
}
FORBIDDEN_ATTRIBUTES = {
    "__builtins__",
    "__globals__",
    "__subclasses__",
    "__loader__",
    "__spec__",
    "__dict__",
    "__getattribute__",
    "exec",
    "eval",
    "__import__",
    "import_module",
    "load_module",
    "exec_module",
    "system",
    "popen",
    "spawn",
    "connect",
    "request",
    "urlopen",
    "send",
    "recv",
    "socket",
    "write_bytes",
    "write_text",
    "unlink",
    "mkdir",
    "rmdir",
    "rename",
    "replace",
    "touch",
    "truncate",
    "remove",
    "execute",
    "executemany",
    "commit",
    "rollback",
    "flush",
}
FINANCIAL_CAPABILITIES = {
    "app.domain.asset_allocation": {"ALGORITHM_VERSION"},
    "app.domain.autonomy": {"ALGORITHM_VERSION"},
    "app.domain.boundary": {"ALGORITHM_VERSION"},
    "app.domain.execution": {"ALGORITHM_VERSION", "ACTION_PLAN_TYPES", "execution_effect_hash"},
    "app.domain.goal_allocation": {"ALGORITHM_VERSION", "IncomeLot"},
    "app.domain.recovery": {"ALGORITHM_VERSION"},
}
FINANCIAL_FUNCTIONS = {
    "compute_boundary",
    "select_asset",
    "revalidate_execution",
    "plan_goal_allocation",
    "plan_recovery",
    "classify_autonomy",
    "evaluate_autonomy",
    "execute_action",
    "prepare_action",
    "run_recovery",
    "process_redemption",
    "ingest_external_fact",
}
AUDIT_ENTRYPOINTS = {
    "app.domain.audit_chain": {
        "parse_checkpoint",
        "parse_event",
        "parse_subject",
        "subject_hash",
        "verify_epoch",
    },
    "app.domain.audit_chain_types": {"AuditHead", "AuditVerification", "ReferenceBundle"},
}
METRICS = {"S1", "S2", "S3", "S4", "S5", "E1", "E2", "E3", "E4", "E5", "A1", "A2", "A3", "A4"}
METRIC_MODULE = {
    **{
        key: "scripts.mvp_financial_metrics"
        for key in ("S1", "S2", "S3", "S4", "S5", "E1", "E3", "E4")
    },
    **{key: "scripts.mvp_trace_metrics" for key in ("A1", "A2", "A3", "A4")},
    "E2": "scripts.mvp_observations",
    "E5": "scripts.mvp_observations",
}


class RegistrationError(ValueError):
    """Unregistered capability, source drift, or unsafe static operation."""


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _object(value: object, label: str) -> dict[str, Any]:
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise RegistrationError(label + " must be an object")
    return value


def _pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in values:
        if key in result:
            raise RegistrationError("Duplicate JSON key")
        result[key] = value
    return result


def strict_json(data: bytes) -> dict[str, Any]:
    def reject(value: str) -> None:
        raise RegistrationError("Nonfinite JSON " + value)

    try:
        return _object(
            json.loads(data.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=reject),
            "registration",
        )
    except (UnicodeError, json.JSONDecodeError) as error:
        raise RegistrationError("Invalid original UTF8 JSON") from error


def module_path(module: str) -> str:
    if module not in MODULES:
        raise RegistrationError("Unregistered source module: " + module)
    prefix = "apps/api/" if module.startswith("app.") else ""
    return prefix + module.replace(".", "/") + ".py"


def confined(root: Path, relative: str) -> Path:
    if not relative or "\\" in relative or Path(relative).is_absolute() or ":" in relative:
        raise RegistrationError("Source path must be canonical relative")
    if any(part in {"", ".", ".."} for part in relative.split("/")):
        raise RegistrationError("Noncanonical source path")
    base = root.resolve()
    target = (base / relative).resolve()
    if not target.is_relative_to(base) or target == base:
        raise RegistrationError("Source path escaped root")
    return target


def capture_framework(root: Path) -> dict[str, Any]:
    """Read installed immutable typing-runtime bytes; no imports or API/database calls."""
    lock = (root / "uv.lock").read_bytes()
    packages = tomllib.loads(lock.decode("utf-8"))["package"]
    locked = {item["name"]: item["version"] for item in packages if item["name"] in FRAMEWORK_NAMES}
    if set(locked) != FRAMEWORK_NAMES:
        raise RegistrationError("Complete locked typing runtime is missing")
    distributions: dict[str, Any] = {}
    prefix = Path(sys.prefix).resolve()
    for name in sorted(FRAMEWORK_NAMES):
        distribution = importlib.metadata.distribution(name)
        if distribution.version != locked[name] or not distribution.files:
            raise RegistrationError("Typing runtime does not match lock: " + name)
        rows = []
        for filename in sorted(distribution.files, key=str):
            if str(filename).endswith(".pyc"):
                continue
            original = Path(str(distribution.locate_file(filename))).resolve()
            if not original.is_relative_to(prefix) or not original.is_file():
                raise RegistrationError("Typing runtime source escaped current environment")
            data = original.read_bytes()
            rows.append(
                {
                    "path": original.relative_to(prefix).as_posix(),
                    "sha256": sha(data),
                    "bytes": len(data),
                }
            )
        distributions[name] = {"version": distribution.version, "files": rows}
    return {
        "protocol": "LOCKED_IMMUTABLE_TYPED_MODEL_RUNTIME_V1",
        "lock_sha256": sha(lock),
        "python": sys.version,
        "distributions": distributions,
    }


@dataclass
class Module:
    name: str
    tree: ast.Module
    definitions: dict[str, ast.AST]
    imports: dict[str, tuple[str, str | None]]


def _imports(nodes: ast.AST) -> dict[str, tuple[str, str | None]]:
    result: dict[str, tuple[str, str | None]] = {}
    for node in ast.walk(nodes):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    raise RegistrationError("Module alias imports are not registered")
                result[alias.name.split(".")[0]] = (alias.name, None)
        elif isinstance(node, ast.ImportFrom):
            if node.level or node.module is None:
                raise RegistrationError("Relative/dynamic source import is forbidden")
            for alias in node.names:
                if alias.name == "*":
                    raise RegistrationError("Star imports are forbidden")
                result[alias.asname or alias.name] = (node.module, alias.name)
    return result


def parse_module(name: str, data: bytes) -> Module:
    try:
        tree = ast.parse(data.decode("utf-8"), filename=module_path(name))
    except (UnicodeError, SyntaxError) as error:
        raise RegistrationError("Source AST cannot be read") from error
    definitions: dict[str, ast.AST] = {}
    imports: dict[str, tuple[str, str | None]] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            definitions[node.name] = node
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    definitions[target.id] = node
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.update(_imports(node))
        elif isinstance(node, ast.If):
            # TYPE_CHECKING names are exact source/type references, not financial calls.
            if isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING":
                imports.update(_imports(node))
    for imported_module, _ in imports.values():
        if imported_module.split(".")[0] not in STDLIB and imported_module not in MODULES | {
            "pydantic"
        }:
            raise RegistrationError("Unregistered runtime top-level import: " + imported_module)
    return Module(name, tree, definitions, imports)


def _is_compile_comparison(node: ast.Call) -> bool:
    if len(node.args) != 3 or len(node.keywords) != 1:
        return False
    first, second, mode = node.args
    return (
        isinstance(first, ast.Call)
        and isinstance(first.func, ast.Attribute)
        and isinstance(first.func.value, ast.Name)
        and first.func.value.id == "path"
        and first.func.attr == "read_bytes"
        and not first.args
        and not first.keywords
        and isinstance(second, ast.Call)
        and isinstance(second.func, ast.Name)
        and second.func.id == "str"
        and len(second.args) == 1
        and isinstance(second.args[0], ast.Name)
        and second.args[0].id == "path"
        and isinstance(mode, ast.Constant)
        and mode.value == "exec"
        and node.keywords[0].arg == "dont_inherit"
        and isinstance(node.keywords[0].value, ast.Constant)
        and node.keywords[0].value.value is True
    )


def inspect_capability(
    module: Module, symbol: str
) -> tuple[set[tuple[str, str]], list[dict[str, Any]]]:
    if symbol == "main" or symbol not in module.definitions:
        raise RegistrationError("Unknown/CLI capability: " + module.name + ":" + symbol)
    if module.name in FINANCIAL_CAPABILITIES and symbol not in FINANCIAL_CAPABILITIES[module.name]:
        raise RegistrationError(
            "Financial evaluator capability is forbidden: " + module.name + ":" + symbol
        )
    node = module.definitions[symbol]
    imports = dict(module.imports)
    imports.update(_imports(node))
    for target, exported in _imports(node).values():
        if target.split(".")[0] not in STDLIB and target not in MODULES | {"pydantic"}:
            raise RegistrationError("Unregistered dependency import: " + target)
        if target == "pydantic" and exported not in FRAMEWORK_SYMBOLS:
            raise RegistrationError("Unregistered typing framework symbol")
    edges: set[tuple[str, str]] = set()
    exceptions: list[dict[str, Any]] = []
    parents = {
        id(child): parent for parent in ast.walk(node) for child in ast.iter_child_nodes(parent)
    }
    code_variables: set[str] = set()
    for assignment in ast.walk(node):
        if isinstance(assignment, ast.Assign) and isinstance(assignment.value, ast.Call):
            function = assignment.value.func
            if isinstance(function, ast.Name) and function.id == "compile":
                code_variables.update(
                    target.id for target in assignment.targets if isinstance(target, ast.Name)
                )
    changed = True
    while changed:
        changed = False
        for assignment in ast.walk(node):
            if (
                isinstance(assignment, ast.Assign)
                and isinstance(assignment.value, ast.Name)
                and assignment.value.id in code_variables
            ):
                for alias_target in assignment.targets:
                    if isinstance(alias_target, ast.Name) and alias_target.id not in code_variables:
                        code_variables.add(alias_target.id)
                        changed = True
    for item in ast.walk(node):
        if isinstance(item, ast.Attribute) and item.attr in FINANCIAL_FUNCTIONS:
            raise RegistrationError("Financial evaluator attribute is forbidden")
        if (
            isinstance(item, ast.Attribute)
            and isinstance(item.value, ast.Name)
            and item.value.id == "sys"
            and item.attr == "modules"
        ):
            parent_lookup = parents.get(id(item))
            if (
                (module.name, symbol)
                not in {
                    ("scripts.mvp_trace_metrics", "TraceBundle"),
                    ("scripts.mvp_financial_metrics", "FinanceBundle"),
                }
                or not isinstance(parent_lookup, ast.Attribute)
                or parent_lookup.attr != "get"
            ):
                raise RegistrationError("Runtime module lookup is source-comparison only")
        if isinstance(item, (ast.AsyncFunctionDef, ast.Await, ast.Yield, ast.YieldFrom)):
            raise RegistrationError("Async/generator execution is not registered")
        if isinstance(item, ast.Attribute) and item.attr in FORBIDDEN_ATTRIBUTES:
            # Immutable string replace is common JSON/time normalization, not filesystem replace.
            if item.attr == "__dict__" and (module.name, symbol) in {
                ("app.domain.audit_chain", "_declared"),
                ("app.domain.decision_trace", "verify_trace"),
                ("app.domain.boundary_types", "BoundaryModel"),
            }:
                exceptions.append({"kind": "TYPED_MODEL_DECLARED_FIELDS", "line": item.lineno})
            elif item.attr == "replace":
                if isinstance(item.value, ast.Name) and item.value.id in {
                    "path",
                    "file",
                    "destination",
                    "source_path",
                    "target_path",
                }:
                    raise RegistrationError("Filesystem replacement is forbidden")
                if (
                    isinstance(item.value, ast.Call)
                    and isinstance(item.value.func, ast.Name)
                    and item.value.func.id == "Path"
                ):
                    raise RegistrationError("Filesystem replacement is forbidden")
            elif item.attr.startswith("__") or (
                isinstance((parent_operation := parents.get(id(item))), ast.Call)
                and parent_operation.func is item
            ):
                raise RegistrationError(
                    "Forbidden operation: " + module.name + ":" + symbol + ":" + item.attr
                )
        if isinstance(item, ast.Name) and item.id == "main":
            raise RegistrationError("Calculator must not enter report-writing CLI")
        if isinstance(item, ast.Name) and item.id in FORBIDDEN_NAMES:
            if item.id == "FunctionType" and (module.name, symbol) in {
                ("scripts.mvp_trace_metrics", "TraceBundle"),
                ("scripts.mvp_financial_metrics", "FinanceBundle"),
            }:
                # Original source guard only performs isinstance and __code__ equality.
                parent_check = parents.get(id(item))
                if (
                    isinstance(parent_check, ast.Call)
                    and isinstance(parent_check.func, ast.Name)
                    and parent_check.func.id == "isinstance"
                    and item in parent_check.args
                ):
                    continue
            if item.id == "vars" and (module.name, symbol) in {
                ("scripts.mvp_trace_metrics", "TraceBundle"),
                ("scripts.mvp_financial_metrics", "FinanceBundle"),
            }:
                exceptions.append({"kind": "LOADED_CODE_READONLY_COMPARISON", "line": item.lineno})
                continue
            if item.id == "getattr" and (module.name, symbol) in {
                ("app.domain.audit_chain", "_financial_identity"),
                ("app.domain.decision_trace", "_validate_content"),
            }:
                exceptions.append({"kind": "TYPED_ORIGINAL_FIELD_ACCESS", "line": item.lineno})
                continue
            raise RegistrationError("Dynamic operation/alias is forbidden: " + item.id)
        if isinstance(item, ast.Call):
            if isinstance(item.func, (ast.Call, ast.Subscript, ast.Lambda)):
                raise RegistrationError("Indirect dynamic callable execution is forbidden")
            if isinstance(item.func, ast.Name) and item.func.id in code_variables | {
                "FunctionType",
                "CodeType",
            }:
                raise RegistrationError("Code object execution is forbidden")
            if isinstance(item.func, ast.Name) and item.func.id == "compile":
                permitted = (module.name, symbol) in {
                    ("scripts.mvp_trace_metrics", "TraceBundle"),
                    ("scripts.mvp_financial_metrics", "FinanceBundle"),
                } and _is_compile_comparison(item)
                if not permitted:
                    raise RegistrationError("Unregistered compilation capability")
                exceptions.append({"kind": "COMPILE_CODETYPE_COMPARE_ONLY", "line": item.lineno})
            if isinstance(item.func, ast.Attribute) and item.func.attr in {"open", "write", "dump"}:
                raise RegistrationError("Mutable file/stream operation in calculator")
        if isinstance(item, ast.Name) and item.id == "compile":
            parent_operation = parents.get(id(item))
            if not isinstance(parent_operation, ast.Call) or parent_operation.func is not item:
                raise RegistrationError("Compilation alias is forbidden")
        if isinstance(item, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Delete)):
            targets = item.targets if isinstance(item, (ast.Assign, ast.Delete)) else [item.target]
            for assignment_target in targets:
                if isinstance(assignment_target, (ast.Attribute, ast.Subscript)):
                    base: ast.expr = assignment_target
                    while isinstance(base, (ast.Attribute, ast.Subscript)):
                        base = base.value
                    if isinstance(base, ast.Name) and base.id in imports:
                        raise RegistrationError("Imported module/runtime mutation is forbidden")
        if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load):
            if item.id in module.definitions and item.id != symbol:
                edges.add((module.name, item.id))
            elif item.id in imports:
                target, exported = imports[item.id]
                root = target.split(".")[0]
                if root in STDLIB:
                    continue
                if target == "pydantic" and exported in FRAMEWORK_SYMBOLS:
                    continue
                if target not in MODULES or exported is None:
                    raise RegistrationError("Unregistered dependency: " + target)
                if module.name in SCRIPT_MODULES and target.startswith("app."):
                    if (
                        module.name != "scripts.mvp_trace_metrics"
                        or exported not in AUDIT_ENTRYPOINTS.get(target, set())
                    ):
                        raise RegistrationError(
                            "Script may only enter registered typed audit verifier"
                        )
                edges.add((target, exported))
    return edges, exceptions


def _components(graph: dict[str, list[str]]) -> list[list[str]]:
    """Tarjan condensation: exact typed/recursive components form the registered DAG."""
    serial = 0
    numbers: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    active: set[str] = set()
    result: list[list[str]] = []

    def visit(vertex: str) -> None:
        nonlocal serial
        numbers[vertex] = low[vertex] = serial
        serial += 1
        stack.append(vertex)
        active.add(vertex)
        for target in graph[vertex]:
            if target not in numbers:
                visit(target)
                low[vertex] = min(low[vertex], low[target])
            elif target in active:
                low[vertex] = min(low[vertex], numbers[target])
        if low[vertex] == numbers[vertex]:
            component = []
            while True:
                target = stack.pop()
                active.remove(target)
                component.append(target)
                if target == vertex:
                    break
            result.append(sorted(component))

    for vertex in sorted(graph):
        if vertex not in numbers:
            visit(vertex)
    return sorted(result)


def build_registration(
    root: Path, metrics: set[str], *, framework: bool = False
) -> tuple[bytes, dict[str, bytes]]:
    """Prepare explicit new registration bytes; caller must review/archive/register them."""
    if not metrics or not metrics <= METRICS:
        raise RegistrationError("Unknown/empty metric bindings")
    originals: dict[str, bytes] = {}
    modules: dict[str, Module] = {}
    graph: dict[str, list[str]] = {}
    exceptions: dict[str, list[dict[str, Any]]] = {}
    pending = {(METRIC_MODULE[metric], "observe") for metric in metrics}
    while pending:
        name, symbol = min(pending)
        pending.remove((name, symbol))
        identity = name + ":" + symbol
        if identity in graph:
            continue
        if name not in modules:
            relative = module_path(name)
            data = confined(root, relative).read_bytes()
            originals[relative] = data
            modules[name] = parse_module(name, data)
        edges, allowance = inspect_capability(modules[name], symbol)
        graph[identity] = sorted(target + ":" + member for target, member in edges)
        exceptions[identity] = allowance
        pending.update(edge for edge in edges if edge[0] + ":" + edge[1] not in graph)
    # Imported modules execute their top-level imports even when we use only a
    # version constant or DTO. Bind that full source closure separately; those
    # sources do not thereby acquire callable financial capabilities.
    source_pending = set(modules)
    source_visited: set[str] = set()
    while source_pending:
        name = min(source_pending)
        source_pending.remove(name)
        if name in source_visited:
            continue
        source_visited.add(name)
        if name not in modules:
            relative = module_path(name)
            data = confined(root, relative).read_bytes()
            originals[relative] = data
            modules[name] = parse_module(name, data)
        for target, _ in modules[name].imports.values():
            if target in MODULES and target not in source_visited:
                source_pending.add(target)
        for statement in modules[name].tree.body:
            if isinstance(statement, (ast.Assign, ast.AnnAssign, ast.Expr)):
                for call in ast.walk(statement):
                    if isinstance(call, ast.Call) and isinstance(call.func, ast.Name):
                        imported = modules[name].imports.get(call.func.id)
                        if imported and imported[0] in FINANCIAL_CAPABILITIES:
                            raise RegistrationError("Top-level financial execution is forbidden")
                        if call.func.id in FORBIDDEN_NAMES | {"compile"}:
                            raise RegistrationError("Top-level dynamic execution is forbidden")
    for relative in (
        "scripts/__init__.py",
        "apps/api/app/__init__.py",
        "apps/api/app/domain/__init__.py",
    ):
        path = confined(root, relative)
        if path.is_file():
            data = path.read_bytes()
            tree = ast.parse(data.decode("utf-8"))
            if any(
                isinstance(item, (ast.Call, ast.Import, ast.ImportFrom)) for item in ast.walk(tree)
            ):
                raise RegistrationError("Package initializer effects are not registered")
            originals[relative] = data
    needs_framework = any(
        any(target == "pydantic" for target, _ in _imports(module.tree).values())
        for module in modules.values()
    )
    if needs_framework and not framework:
        raise RegistrationError("Explicit immutable typing runtime registration is required")
    paths = sorted(originals)
    typing_runtime = capture_framework(root) if needs_framework else None
    if typing_runtime is not None:
        for distribution_name, distribution in typing_runtime["distributions"].items():
            for runtime_original in distribution["files"]:
                path = confined(Path(sys.prefix), runtime_original["path"])
                archive_path = "typed-runtime/" + distribution_name + "/" + runtime_original["path"]
                data = path.read_bytes()
                if (
                    sha(data) != runtime_original["sha256"]
                    or len(data) != runtime_original["bytes"]
                ):
                    raise RegistrationError("Typing runtime changed during original capture")
                originals[archive_path] = data
    registration = {
        "protocol": PROTOCOL,
        "method": METHOD,
        "source_files": [
            {"path": path, "sha256": sha(originals[path]), "bytes": len(originals[path])}
            for path in paths
        ],
        "capabilities": graph,
        "components": _components(graph),
        "inspection_exceptions": exceptions,
        "source_only_modules": sorted(
            set(modules) - {identity.split(":")[0] for identity in graph}
        ),
        "metric_bindings": {
            metric: {
                "module": METRIC_MODULE[metric],
                "function": "observe",
                "pointer": "/metrics/" + metric,
            }
            for metric in sorted(metrics)
        },
        "framework": typing_runtime,
        "stdlib_roots": sorted(STDLIB),
        "financial_effect_evidence": False,
        "status": "REGISTRATION_DRAFT_NOT_EXECUTED",
    }
    return canonical(registration), originals


def verify_readonly_registration(
    root: Path,
    registration_bytes: bytes,
    archived_sources: dict[str, bytes],
    *,
    expected_registration_sha256: str,
) -> dict[str, Any]:
    """Every call rereads current+archived sources/AST/runtime; no cached authority."""
    if (
        not re.fullmatch(r"[0-9a-f]{64}", expected_registration_sha256)
        or sha(registration_bytes) != expected_registration_sha256
    ):
        raise RegistrationError("Externally registered original manifest hash differs")
    registration = strict_json(registration_bytes)
    bindings = _object(registration.get("metric_bindings"), "metric bindings")
    rebuilt, current_sources = build_registration(
        root, set(bindings), framework=registration.get("framework") is not None
    )
    if canonical(registration) != rebuilt:
        raise RegistrationError("Registered graph/capability/source/runtime no longer matches")
    if set(archived_sources) != set(current_sources):
        raise RegistrationError("Archived source inventory is not complete/exact")
    for relative, original in archived_sources.items():
        if type(original) is not bytes or original != current_sources[relative]:
            raise RegistrationError("Archived/current source bytes differ: " + relative)
    return {
        "protocol": PROTOCOL,
        "method": METHOD,
        "status": "REGISTERED_NOT_EXECUTED",
        "registration_sha256": expected_registration_sha256,
        "metric_ids": sorted(bindings),
        "source_files": registration["source_files"],
        "capabilities": registration["capabilities"],
        "components": registration["components"],
        "financial_effect_evidence": False,
        "initial_24_status": "NOT_FROZEN_NOT_RUN",
    }
