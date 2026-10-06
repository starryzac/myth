"""Build a reviewable HTML/source package; missing PDF/content acceptance remains nonzero."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ("docs/proposal/企划书_正文.md", "docs/proposal/技术附录.md")


def build_review(root: Path, output: Path, head: str) -> dict[str, object]:
    root = root.resolve()
    target = output.resolve()
    if not target.is_relative_to(root / "output") and not target.is_relative_to(root / ".runtime"):
        raise ValueError("Review output must be a new repository output/ or .runtime/ directory")
    if target.exists() or output.is_symlink():
        raise ValueError("Existing output must be retained; refusing overwrite")
    originals = []
    for name in SOURCES:
        path = root / name
        if not path.resolve().is_relative_to(root) or path.is_symlink():
            raise ValueError("Proposal source escaped the repository")
        raw = path.read_bytes()
        if not raw.strip():
            raise ValueError("Actual proposal source is empty")
        originals.append((name, raw, raw.decode("utf-8")))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir(exist_ok=False)
    sections = []
    artifacts = []
    for name, raw, text in originals:
        saved = target / "source" / name
        saved.parent.mkdir(parents=True, exist_ok=True)
        with saved.open("xb") as stream:
            stream.write(raw)
        if saved.read_bytes() != raw:
            raise ValueError("Copied original bytes differ")
        artifacts.append(
            {
                "path": saved.relative_to(target).as_posix(),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size_bytes": len(raw),
            }
        )
        sections.append(
            f"<section><h2>{html.escape(name)}</h2><pre>{html.escape(text)}</pre></section>"
        )
    rendered = (
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>钱途有界企划源稿审阅包</title><style>"
        "body{max-width:65rem;margin:2rem auto;padding:1rem;font:1rem/1.7 sans-serif;}"
        "pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;}h2{margin-top:3rem;}"
        "</style><body><h1>企划源稿审阅包</h1><p>WORKING_NOT_ACCEPTANCE：原文按原字节保留，"
        "可能包含过期状态；未做当前内容一致性审阅。没有生成PDF，页数未知，真人研究和效果主张未验证。</p>"
        + "".join(sections)
        + "</body></html>"
    ).encode("utf-8")
    with (target / "index.html").open("xb") as stream:
        stream.write(rendered)
    artifacts.append(
        {
            "path": "index.html",
            "sha256": hashlib.sha256(rendered).hexdigest(),
            "size_bytes": len(rendered),
        }
    )
    stable = all((root / name).read_bytes() == raw for name, raw, _ in originals)
    report: dict[str, object] = {
        "protocol": "bounded-funds-proposal-review-v1",
        "status": "REVIEW_SOURCE_BUILT" if stable else "SOURCE_CHANGED",
        "acceptance_status": "INCOMPLETE",
        "exit_code": 1,
        "git_head": head,
        "source_stable": stable,
        "source_scope": list(SOURCES),
        "artifacts": artifacts,
        "pdf": "NOT_RENDERED",
        "actual_pdf_pages": None,
        "compile_status": "NOT_RUN",
        "content_consistency_review": "NOT_RUN",
        "human_research": "NOT_VERIFIED",
        "experiment_claims": "NOT_VERIFIED",
        "required_figures": "NOT_VERIFIED",
        "task_closed": False,
    }
    with (target / "manifest.json").open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    location = args.output or os.environ.get("BOUNDEDFUNDS_PROPOSAL_OUTPUT")
    output = (
        Path(location)
        if location
        else ROOT
        / "output/proposal-review"
        / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8])
    )
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
        ).stdout.strip()
        report = build_review(ROOT, output, head)
        print(json.dumps({"directory": str(output), **report}, ensure_ascii=False, indent=2))
        return 1  # A source review package is not a paginated/content-reviewed final proposal.
    except (OSError, ValueError, UnicodeError, subprocess.CalledProcessError):
        print(
            "REJECTED: source/output/encoding boundary failed; "
            "retained outputs are not overwritten",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
