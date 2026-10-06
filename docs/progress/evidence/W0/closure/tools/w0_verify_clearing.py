import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

root = Path(__file__).resolve().parents[1]
out = root / "docs/progress/evidence/W0" / ("verification-benchmark-clearing-"+sys.argv[1])
out.mkdir(exist_ok=False)
sources = {name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in (
    "scripts/benchmark_reads.py", "apps/api/app/tests/test_benchmark_reads.py"
)}
commands = [[sys.executable, "-m", "ruff", "check", *sources],
            [sys.executable, "-m", "ruff", "format", "--check", *sources],
            [sys.executable, "-m", "mypy", "apps/api/app/tests/test_benchmark_reads.py"],
            [sys.executable, "-m", "pytest", "apps/api/app/tests/test_benchmark_reads.py", "--no-cov", "-p", "no:cacheprovider", "-q", "-x"]]
results = []
for number, argv in enumerate(commands,1):
    start = time.perf_counter()
    run = subprocess.run(argv,cwd=root,capture_output=True,text=True,encoding="utf-8",errors="replace")
    path = out / f"{number:02}.log"
    path.write_text(run.stdout+run.stderr,encoding="utf-8")
    results.append(dict(argv=argv,exit_code=run.returncode,wall_seconds=time.perf_counter()-start,log_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    print(run.stdout[-1600:]+run.stderr[-500:],flush=True)
    if run.returncode:
        break
(out/"manifest.json").write_text(json.dumps(dict(source_before=sources,commands=results),ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
raise SystemExit(results[-1]["exit_code"])
