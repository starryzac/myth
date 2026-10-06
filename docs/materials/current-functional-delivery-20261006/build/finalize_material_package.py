"""Inventory this material delivery; structural checks are not product acceptance."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import stat
import subprocess
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from pypdf import PdfReader

BASE = Path(__file__).resolve().parents[1]
ROOT = BASE.parents[2]
NOW = datetime.now(UTC).isoformat()


def read(name):
    return json.loads((BASE / name).read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(name, data):
    with (BASE / name).open("x", encoding="utf-8", newline="") as target:
        json.dump(data, target, ensure_ascii=False, indent=2)
        target.write("\n")


def ref(path):
    return {"path": path.relative_to(BASE).as_posix(), "bytes": path.stat().st_size,
            "sha256": sha(path)}


sources = read("sources.json")
source_by_id = {item["id"]: item for item in sources["sources"]}
assert len(source_by_id) == len(sources["sources"]) == 45
comparisons = []
for item in sources["sources"]:
    captured = BASE / item["captured_copy"]
    assert sha(captured) == item["sha256"]
    assert captured.stat().st_size == item["size_bytes"]
    original = Path(item["absolute_original_path"])
    exists = original.is_file()
    current = sha(original) if exists else None
    comparisons.append({"id": item["id"], "repository_path": item["repository_path"],
                        "captured_sha256": item["sha256"], "current_sha256": current,
                        "current_exists": exists, "current_equal_captured": current == item["sha256"]})
write("build/source-current-comparison.json", {
    "observed_at": NOW, "scope": "ONLY_EXPLICIT_45_PUBLIC_DOCUMENTARY_ORIGINALS",
    "whole_source_freeze": False,
    "boundary": "Captured exact copies remain immutable. Later changes do not update this delivery.",
    "sources": comparisons,
    "changed_source_ids": [x["id"] for x in comparisons if not x["current_equal_captured"]]})

packet = read("pages.json")
assert packet["task_closed"] is False
assert packet["formal_closed_count"] == 21 and packet["requirement_denominator"] == 92
assert len(packet["proposal"]) == 12 and len(packet["whitepaper"]) == 30 and len(packet["slides"]) == 10
claims = read("claims_registry.yaml")
assert len(claims["claims"]) == 161 and claims["automatic_effect_claim_gate"] == "NOT_PASSED"
claim_by_id = {x["claim_id"]: x for x in claims["claims"]}
assert len(claim_by_id) == 161
paragraphs = 0
for kind in ("proposal", "whitepaper"):
    for page_number, page in enumerate(packet[kind], 1):
        assert all(r in source_by_id for r in page["refs"])
        for section_number, section in enumerate(page["sections"], 1):
            entry = claim_by_id[f"{kind.upper()}-{page_number:02d}-{section_number:02d}"]
            assert entry["text"] == section["text"] and entry["source_refs"] == page["refs"]
            assert entry["automatic_final_admission"] is False
            paragraphs += 1
assert paragraphs == 156
for claim in claims["claims"]:
    assert all(claim["source_sha256"][r] == source_by_id[r]["sha256"] for r in claim["source_refs"])
for slide in packet["slides"]:
    assert slide["body"] and all(r in source_by_id for r in slide["refs"])

metrics = read("metrics_registry.yaml")
assert len(metrics["mvp_required_14"]) == 14 and len(metrics["full_original_measures"]) == 28
all_metrics = metrics["mvp_required_14"] + metrics["full_original_measures"]
assert all(m["status"] == "NOT_RUN" and all(m[k] is None for k in ("value", "numerator", "denominator")) for m in all_metrics)
assert metrics["human_participants"] == metrics["human_records"] == 0
assert metrics["current_performance"] == "NOT_MEASURED"
observations = read("results/observations.json")
assert observations["status"] == "NOT_RUN" and observations["native_experiment_run_id"] is None
with (BASE / "results/observations.csv").open(encoding="utf-8-sig", newline="") as stream:
    rows = list(csv.DictReader(stream))
assert len(rows) == 42
assert all(row["status"] == "NOT_RUN" and all(row[k] == "" for k in ("value", "numerator", "denominator", "run_id")) for row in rows)

figures = read("figures_registry.yaml")
assert figures["required_mvp_figures"] == 8 and len(figures["figures"]) == 8
assert figures["native_screenshot_count_in_final_materials"] == 0
assert all(f["final_native_artifact"] is None for f in figures["figures"])
script = read("demo-shot-list.json")
assert script["status"] == "NOT_RECORDED" and script["actual_duration_seconds"] is None
previous = 0
for segment in script["segments"]:
    assert segment["start"] == previous and segment["end"] > previous
    previous = segment["end"]
assert previous == script["planned_seconds"] == 240

artifacts = []
for filename, pages, label in [
    ("钱途有界_当前功能企划_12页_交付版.pdf", 12, "proposal-delivery"),
    ("钱途有界_当前功能白皮书_30页_交付版.pdf", 30, "whitepaper-delivery"),
]:
    path = BASE / "outputs" / filename
    pdf = PdfReader(path)
    assert len(pdf.pages) == pages
    texts = [p.extract_text() or "" for p in pdf.pages]
    assert all(len(text) > 120 for text in texts)
    assert all("\ufffd" not in text for text in texts)
    previews = sorted((BASE / "build" / ("pdf-previews-" + label)).glob("page-*.png"))
    assert len(previews) == pages
    assert all(p.read_bytes().startswith(b"\x89PNG\r\n\x1a\n") for p in previews)
    artifacts.append({**ref(path), "format": "PDF", "actual_pages": pages,
                      "extracted_page_characters": [len(x) for x in texts], "rendered_pages": pages})

deck = BASE / "outputs" / "钱途有界_当前功能答辩_10页_交付版.pptx"
with zipfile.ZipFile(deck) as package:
    assert package.testzip() is None
    names = package.namelist()
    slides = [n for n in names if n.startswith("ppt/slides/slide") and n.endswith(".xml")]
    notes = [n for n in names if n.startswith("ppt/notesSlides/notesSlide") and n.endswith(".xml")]
    assert len(slides) == len(notes) == 10
    assert not any(n.startswith("ppt/media/") for n in names)
    assert all(b"Microsoft YaHei" in package.read(n) and b"<a:t>" in package.read(n) for n in slides)
final_deck_previews = sorted((BASE / "build/deck-final-file-previews").glob("slide-*.png"))
assert len(final_deck_previews) == 10
assert all(p.read_bytes().startswith(b"\x89PNG\r\n\x1a\n") for p in final_deck_previews)
artifacts.append({**ref(deck), "format": "PPTX", "actual_slides": 10,
                  "notes": 10, "editable_text": True, "actual_final_file_import_rendered_slides": 10,
                  "native_screenshot_count": 0, "native_powerpoint_open": "NOT_RUN"})

write("build/visual-review.json", {
    "method": "AI_RENDERED_LAYOUT_REVIEW_NOT_HUMAN_BUSINESS_ACCEPTANCE", "review_completed_at": NOW,
    "artifact_binding": artifacts,
    "pdf_renderer": "Installed Poppler actual final PDFs 100dpi",
    "pdf_contact_review": {"proposal": list(range(1, 13)), "whitepaper": list(range(1, 31))},
    "pdf_full_size_review": {"proposal": [3, 6, 11], "whitepaper": [5, 12, 27, 28, 30]},
    "pptx_renderer": "Actual final PPTX FileBlob import with Artifact Tool then PNG export",
    "pptx_full_size_review": list(range(1, 11)),
    "observations": ["No observed clipped text or overlap in the inspected renders.",
                     "Chinese body/headings and table rows readable at full-size renders.",
                     "White space reflects missing effect/native-figure inputs; no fabricated chart fills it."],
    "human_claim_review": "NOT_RUN", "native_powerpoint_open": "NOT_RUN",
    "native_browser_capture": "NOT_RUN", "product_acceptance": "INCOMPLETE", "task_closed": False})

write("build/first-pdf-render-failure-record.json", {
    "record_type": "RETROSPECTIVE_TOOL_TRANSCRIPT_ONLY_NOT_ORIGINAL_SUBPROCESS_LOG", "recorded_at": NOW,
    "tool_chunk_id": "2232ef", "child": "initial render_pdfs.py",
    "observed_exception": "ValueError on source caption/footer below the initial body minimum y=76; observed y=56",
    "wrapper_exit_code": 0,
    "wrapper_exit_boundary": "Following junction command masked the Python failure; zero wrapper exit did not prove PDF generation.",
    "initial_child_failure": True, "exact_original_child_exit_code_not_captured": True,
    "original_first_failed_source_bytes_not_separately_captured": True,
    "repair": "Source-caption lower margin explicitly changed to 40 for a later successful new artifact build.",
    "replacement_failure_log_generated": False})

log_records = []
for prefix, exit_code, purpose in [
    ("deck-first", 1, "PPTX package subprocess EPERM; no final success"),
    ("deck-second", 1, "First-party import lacked RUNTIME_NODE_MODULES"),
    ("deck-third", 0, "Earlier PPTX draft successful, retained"),
    ("deck-delivery", 0, "Final 10-slide file generation and skill finalizer"),
    ("deck-final-render", 0, "Actual final file import/render all 10 slides"),
    ("pdf-preview", 0, "First PDF draft 42-page actual render retained"),
    ("pdf-reading", 0, "Intermediate reading PDFs retained"),
    ("pdf-reading-preview", 0, "Intermediate 42-page actual render retained"),
    ("pdf-delivery", 0, "Final PDF generation and pypdf page checks"),
    ("pdf-delivery-preview", 0, "Final 42-page actual Poppler render"),
    ("supplementary", 0, "Authored report/registry/script/QA sources"),
    ("evidence-check", 2, "Existing checker rejected missing actual evidence request"),
]:
    logs = [BASE / "build" / (prefix + "." + suffix) for suffix in ("stdout", "stderr")]
    assert all(p.is_file() for p in logs)
    log_records.append({"label": prefix, "observed_terminal_exit_code": exit_code,
                        "purpose": purpose, "original_redirected_logs": [ref(p) for p in logs]})
write("build/commands.json", {
    "record_type": "RETROSPECTIVE_TERMINAL_SUMMARY_WITH_ORIGINAL_REDIRECTED_BYTE_LOGS",
    "native_execution_manifest": False, "recorded_at": NOW,
    "exact_poppler_argv_original": "build/pdf-delivery-render-commands.json",
    "full_shell_argv_for_earlier_runs_not_separately_archived": True,
    "runs": log_records,
    "no_database_or_browser_or_office_suite_called": True})
write("build/package-review.json", {
    "status": "MATERIAL_STRUCTURE_AND_CAPTURED_FILE_INTEGRITY_VERIFIED",
    "reviewed_at": NOW, "source_copy_count": 45, "paragraph_claims_exactly_matched": 156,
    "explicit_unmeasured_business_hypotheses": 5, "source_claim_registry_entries": 161,
    "claim_registry_scope": "Proposal/whitepaper section paragraphs and five explicit hypotheses. Slides/QA/script/report have source references; not an exhaustive semantic claim audit.",
    "mvp_metric_count": 14, "full_measure_count": 28, "all_experimental_values_null": True,
    "native_screenshot_count": 0, "original_figure_inventory": 8,
    "planned_script_seconds": 240, "actual_script_seconds": None,
    "human_participants": 0, "artifacts": artifacts,
    "semantic_source_verification": "MANUAL_NOT_RUN", "automatic_effect_claim_gate": "NOT_PASSED",
    "full_requirement_statuses": {f"FULL-{n}": "PENDING" for n in range(901, 907)},
    "product_acceptance": "INCOMPLETE", "task_closed": False})

files = []
excluded = []
for directory, dirs, names in os.walk(BASE, topdown=True, followlinks=False):
    keep = []
    for name in dirs:
        child = Path(directory) / name
        attributes = getattr(child.lstat(), "st_file_attributes", 0)
        if name in {"node_modules", "__pycache__"} or attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            excluded.append(child.relative_to(BASE).as_posix())
        else:
            keep.append(name)
    dirs[:] = keep
    for name in names:
        path = Path(directory) / name
        if path == BASE / "manifest.json":
            continue
        files.append(ref(path))
head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, check=True, text=True).stdout.strip()
write("manifest.json", {
    "protocol": "bounded-funds-current-functional-material-delivery-v1", "created_at": NOW,
    "status": "REVIEWABLE_MATERIAL_ARTIFACTS_DELIVERED_ACCEPTANCE_INCOMPLETE",
    "git_head_observed": head, "captured_source_head": sources["git_head"], "whole_repository_frozen": False,
    "output_directory": str(BASE), "artifacts": artifacts, "files": sorted(files, key=lambda f: f["path"]),
    "excluded_dependencies_and_generated_cache": excluded,
    "captured_sources": ref(BASE / "sources.json"), "captured_current_comparison": ref(BASE / "build/source-current-comparison.json"),
    "material_structure_review": ref(BASE / "build/package-review.json"),
    "layout_review": ref(BASE / "build/visual-review.json"), "terminal_run_summary": ref(BASE / "build/commands.json"),
    "formal_closed_count_unchanged": 21, "original_requirement_denominator": 92,
    "full_requirement_statuses": {f"FULL-{n}": "PENDING" for n in range(901, 907)},
    "source_claim_gate": "NOT_PASSED", "research": "NOT_STARTED_ACTUAL_PARTICIPANTS_0",
    "experiment": "NOT_RUN_NO_FORMAL_EFFECT_RESULTS", "demo_recording": "NOT_RECORDED",
    "actual_current_performance": "NOT_MEASURED", "native_powerpoint": "NOT_RUN",
    "script_timebox_revision": "SCRIPT_TIMEBOX_REVISION_20261006: original 280s sum -> planned 240s, original six content groups retained; not timed",
    "historical_failure_preservation": "Existing failures and original materials retained. This directory includes first/second failed deck byte logs and earlier successful drafts.",
    "touched_production_sources": False, "database_browser_docker_invoked": False,
    "installed_dependencies": False, "libreoffice_used": False, "product_acceptance": "INCOMPLETE", "task_closed": False})
print(json.dumps({"manifest": str(BASE / "manifest.json"), "manifest_sha256": sha(BASE / "manifest.json"),
                  "file_count": len(files), "artifacts": artifacts,
                  "changed_source_ids": [x["id"] for x in comparisons if not x["current_equal_captured"]],
                  "product_acceptance": "INCOMPLETE"}, ensure_ascii=False))
