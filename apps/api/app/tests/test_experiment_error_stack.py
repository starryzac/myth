"""TOOL_TEST_ONLY actual Python traceback/source-byte gates; no SQL or financial result."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from app.db.settings import REPOSITORY_ROOT
from app.services import experiment_arms as provider

ROOT = Path(REPOSITORY_ROOT).resolve()


class Fixture:
    def __init__(
        self, raw: bytes = b'def original():\n    raise RuntimeError("TOOL_TEST_ONLY")\n'
    ) -> None:
        self.directory = ROOT / ".runtime/W1-error-stack-tool-fixtures" / uuid4().hex
        self.directory.mkdir(parents=True)
        self.current = self.directory / "original.py"
        self.retained = self.directory / "retained.py"
        for path in (self.current, self.retained):
            with path.open("xb") as stream:
                stream.write(raw)
        self.module = ModuleType("TOOL_TEST_ONLY")
        exec(compile(raw, str(self.current), "exec", dont_inherit=True), self.module.__dict__)
        self.sha = hashlib.sha256(raw).hexdigest()
        self.relative = self.current.relative_to(ROOT).as_posix()

    def source(self, v2: bool) -> dict[str, Any]:
        if v2:
            return {"files": [{"path": self.relative, "sha256": self.sha}]}
        return {
            "implementation_files": [
                {"original_path": self.relative, "path": str(self.retained), "sha256": self.sha}
            ]
        }

    def exception(self) -> Exception:
        try:
            self.module.__dict__["original"]()
        except Exception as actual:
            return actual
        raise AssertionError("Synthetic original must really raise")

    def capture(
        self, patch: pytest.MonkeyPatch, *, v2: bool = False, source: Any = None
    ) -> dict[str, Any]:
        patch.setattr(
            provider, "_artifact", lambda *args: self.source(v2) if source is None else source
        )
        registry = {}
        if v2:
            registry["_invocation_bridge"] = SimpleNamespace(
                draft=SimpleNamespace(backing={self.current: self.retained})
            )
        return provider._execution_error_stack(self.exception(), registry)


@pytest.mark.parametrize("v2", [False, True])
def test_original_loaded_leaf_is_bound_to_real_source_and_retained_bytes(
    v2: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = Fixture()
    result = fixture.capture(monkeypatch, v2=v2)
    leaf = result["frames"][-1]
    assert leaf["is_registered_source"] is True
    assert leaf["original_path"] == leaf["path"] == fixture.relative
    assert leaf["absolute_path"] == str(fixture.current)
    assert leaf["sha256"] == fixture.sha and leaf["line"] == 2
    assert leaf["line_text"] == '    raise RuntimeError("TOOL_TEST_ONLY")'
    assert leaf["symbol"] == "original"
    assert result["stack_leaf_source_ref"] == {
        key: leaf[key] for key in ("original_path", "path", "sha256", "line", "line_text", "symbol")
    }
    assert result["exception_type"] == "RuntimeError"
    assert "TOOL_TEST_ONLY" in result["formatted_traceback"]
    assert result["stack_source_status"] == "CAPTURED_SOURCE_BOUND_ACTUAL_LEAF"
    assert "cause_code" not in result and "logical_cause_key" not in result


@pytest.mark.parametrize(
    "case", ["UNREGISTERED", "SHA", "RETAINED", "CODE", "MISSING_FILE", "REGISTRATION"]
)
def test_missing_or_wrong_original_never_localizes_by_success_label(
    case: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = Fixture()
    source = fixture.source(False)
    if case == "UNREGISTERED":
        source["implementation_files"] = []
    elif case == "SHA":
        source["implementation_files"][0]["sha256"] = "0" * 64
    elif case == "RETAINED":
        wrong = fixture.directory / "different-original.py"
        with wrong.open("xb") as stream:
            stream.write(b'def original():\n    return "SUCCESS"\n')
        source["implementation_files"][0]["path"] = str(wrong)
    elif case == "CODE":
        exec(
            compile(
                b'def original():\n    raise RuntimeError("counterfeit same path")\n',
                str(fixture.current),
                "exec",
                dont_inherit=True,
            ),
            fixture.module.__dict__,
        )
    elif case == "MISSING_FILE":
        exec(
            compile(
                b'def original():\n    raise RuntimeError("missing file")\n',
                str(fixture.directory / "absent.py"),
                "exec",
                dont_inherit=True,
            ),
            fixture.module.__dict__,
        )
    else:
        source = {"success": True}
    result = fixture.capture(monkeypatch, source=source)
    assert result["stack_source_status"] == "MISSING" and result["stack_leaf_source_ref"] is None
    assert result["frames"][-1]["is_registered_source"] is False
    assert result["exception_type"] == "RuntimeError" and result["formatted_traceback"]


def test_true_registered_upper_frame_cannot_replace_unregistered_actual_leaf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upper = Fixture(b"def original():\n    lower()\n")
    lower = Fixture()
    upper.module.__dict__["lower"] = lower.module.__dict__["original"]
    result = upper.capture(monkeypatch)
    assert any(frame["is_registered_source"] is True for frame in result["frames"][:-1])
    assert result["frames"][-1]["original_path"] == lower.relative
    assert result["frames"][-1]["is_registered_source"] is False
    assert result["stack_source_status"] == "MISSING" and result["stack_leaf_source_ref"] is None


def test_no_traceback_is_missing_and_does_not_invent_a_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(provider, "_artifact", lambda *args: {"implementation_files": []})
    result = provider._execution_error_stack(RuntimeError("actual missing traceback"), {})
    assert result["frames"] == [] and result["stack_leaf_source_ref"] is None
    assert result["stack_source_status"] == "MISSING"
    assert result["formatted_traceback"] == "RuntimeError: actual missing traceback\n"


def test_source_capture_exception_does_not_replace_original_cause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = Fixture()

    def missing(*args: Any) -> Any:
        raise ValueError("original source file missing")

    monkeypatch.setattr(provider, "_artifact", missing)
    result = provider._execution_error_stack(fixture.exception(), {})
    assert (
        result["exception_type"] == "RuntimeError"
        and "TOOL_TEST_ONLY" in result["formatted_traceback"]
    )
    assert result["source_registration_error"] == {
        "type": "ValueError",
        "message": "original source file missing",
    }
    assert result["stack_source_status"] == "MISSING"
