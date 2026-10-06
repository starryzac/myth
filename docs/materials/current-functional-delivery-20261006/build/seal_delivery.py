"""Seal terminal material bytes after output redirection has closed."""
import hashlib
import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path

base = Path(__file__).resolve().parents[1]
original_path = base / "manifest.json"
original = json.loads(original_path.read_text(encoding="utf-8"))


def file_ref(path):
    data = path.read_bytes()
    return {"path": path.relative_to(base).as_posix(), "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}


prior_mismatches = []
for item in original["files"]:
    current = file_ref(base / item["path"])
    if current != item:
        prior_mismatches.append({"path": item["path"], "prior": item, "current": current})
assert {x["path"] for x in prior_mismatches} <= {"build/finalize.stdout", "README.md", "HANDOFF.md"}
assert "build/finalize.stdout" in {x["path"] for x in prior_mismatches}
for item in original["artifacts"]:
    actual = file_ref(base / item["path"])
    assert actual["bytes"] == item["bytes"] and actual["sha256"] == item["sha256"]

files, excluded = [], []
for directory, dirs, names in os.walk(base, topdown=True, followlinks=False):
    keep = []
    for name in dirs:
        child = Path(directory) / name
        attributes = getattr(child.lstat(), "st_file_attributes", 0)
        if name in {"node_modules", "__pycache__"} or attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            excluded.append(child.relative_to(base).as_posix())
        else:
            keep.append(name)
    dirs[:] = keep
    for name in names:
        path = Path(directory) / name
        assert path.name != "delivery-manifest.json", "Refuse to reseal/overwrite existing delivery"
        files.append(file_ref(path))

delivery = {**original,
    "protocol": "bounded-funds-current-functional-material-delivery-seal-v2",
    "sealed_at": datetime.now(UTC).isoformat(),
    "files": sorted(files, key=lambda item: item["path"]),
    "excluded_dependencies_and_generated_cache": excluded,
    "prior_manifest_original": file_ref(original_path),
    "prior_manifest_is_diagnostic_not_final_seal": True,
    "prior_inventory_mismatches": prior_mismatches,
    "seal_revision_reason": "Initial inventory captured redirected stdout before process completion; that initial manifest and actual stdout remain intact. README/HANDOFF point to this new terminal-byte seal; pre-revision bytes remain in build/revision-3.",
    "seal_stdout_redirected_into_package": False,
    "producer_scope": "This material directory only; no product/effect/financial acceptance.",
}
with (base / "delivery-manifest.json").open("x", encoding="utf-8") as target:
    json.dump(delivery, target, ensure_ascii=False, indent=2)
    target.write("\n")
print(json.dumps({"delivery_manifest_sha256": file_ref(base / "delivery-manifest.json")["sha256"],
                  "file_count": len(files), "prior_mismatch_paths": [x["path"] for x in prior_mismatches],
                  "product_acceptance": "INCOMPLETE"}))
