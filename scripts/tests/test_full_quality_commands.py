"""TOOL_ONLY command/error/path risks; these fixtures are not product evidence."""

import hashlib
import json
import runpy
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[2]
build_proposal = SimpleNamespace(**runpy.run_path(str(ROOT / "scripts/build_proposal.py")))
evidence_check = SimpleNamespace(**runpy.run_path(str(ROOT / "scripts/evidence_check.py")))
security_check = SimpleNamespace(**runpy.run_path(str(ROOT / "scripts/security_check.py")))


@pytest.fixture
def tool_workspace() -> Path:
    # Retain fresh TOOL_ONLY inputs; Windows pytest mode0700 temp is not readable here.
    path = ROOT / ".runtime/full002-tool-fixtures" / uuid4().hex
    path.mkdir(parents=True)
    return path


def test_security_propagates_actual_checker_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    run = Mock(return_value=subprocess.CompletedProcess([], 7))
    monkeypatch.setattr(security_check.subprocess, "run", run)
    assert security_check.main([]) == 7
    assert run.call_args.args[0] == security_check.security_command()
    assert run.call_args.kwargs["check"] is False


def test_missing_evidence_request_does_not_start_or_select_newest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BOUNDEDFUNDS_EVIDENCE_REQUEST", raising=False)
    run = Mock(side_effect=AssertionError("must not start"))
    monkeypatch.setattr(evidence_check.subprocess, "run", run)
    assert evidence_check.main([]) == 2
    run.assert_not_called()


def test_evidence_passes_exact_manifest_and_preserves_incomplete_exit(
    monkeypatch: pytest.MonkeyPatch, tool_workspace: Path
) -> None:
    tmp_path = tool_workspace
    source = tmp_path / "retained-failed.json"
    raw = b'{"status":"FAILED","purpose":"TOOL_ONLY"}'
    source.write_bytes(raw)
    output = tmp_path / "new-check"
    run = Mock(return_value=subprocess.CompletedProcess([], 1))
    monkeypatch.setattr(evidence_check.subprocess, "run", run)
    assert evidence_check.main(["--manifest", str(source), "--output", str(output)]) == 1
    assert run.call_args.args[0] == evidence_check.evidence_command(source, output)
    assert source.read_bytes() == raw
    assert not output.exists()


def proposal_sources(root: Path) -> None:
    for name in build_proposal.SOURCES:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "# TOOL_ONLY\n<script>not executable</script>\n真人研究 NOT_STARTED\n", encoding="utf-8"
        )


def test_review_copies_actual_bytes_and_escapes_content_without_acceptance(
    tool_workspace: Path,
) -> None:
    tmp_path = tool_workspace
    proposal_sources(tmp_path)
    originals = {name: (tmp_path / name).read_bytes() for name in build_proposal.SOURCES}
    output = tmp_path / "output/review"
    result = build_proposal.build_review(tmp_path, output, "TOOL_ONLY_NO_REAL_HEAD")
    assert result["exit_code"] == 1
    assert result["acceptance_status"] == "INCOMPLETE"
    assert result["actual_pdf_pages"] is None and result["task_closed"] is False
    rendered = (output / "index.html").read_text(encoding="utf-8")
    assert "&lt;script&gt;not executable&lt;/script&gt;" in rendered
    assert "<script>" not in rendered
    report = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    for name, raw in originals.items():
        assert (tmp_path / name).read_bytes() == raw
        assert (output / "source" / name).read_bytes() == raw
        assert any(row["sha256"] == hashlib.sha256(raw).hexdigest() for row in report["artifacts"])


def test_review_overwrite_retains_all_previous_bytes(tool_workspace: Path) -> None:
    tmp_path = tool_workspace
    proposal_sources(tmp_path)
    output = tmp_path / "output/review"
    build_proposal.build_review(tmp_path, output, "TOOL_ONLY")
    before = {path: path.read_bytes() for path in output.rglob("*") if path.is_file()}
    with pytest.raises(ValueError, match="overwrite"):
        build_proposal.build_review(tmp_path, output, "TOOL_ONLY")
    assert all(path.read_bytes() == raw for path, raw in before.items())


def test_review_rejects_boundary_and_missing_source_before_writing(tool_workspace: Path) -> None:
    tmp_path = tool_workspace
    proposal_sources(tmp_path)
    with pytest.raises(ValueError, match="output"):
        build_proposal.build_review(tmp_path, tmp_path / "apps/new-output", "TOOL_ONLY")
    assert not (tmp_path / "apps").exists()
    (tmp_path / build_proposal.SOURCES[0]).unlink()
    with pytest.raises(FileNotFoundError):
        build_proposal.build_review(tmp_path, tmp_path / "output/review", "TOOL_ONLY")
    assert not (tmp_path / "output").exists()
