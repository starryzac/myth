import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

label, *tests = sys.argv[1:]
if not label.startswith('MVP-401-') or not tests or any(not t.startswith('apps/api/app/tests/') for t in tests):
    raise SystemExit('Exact MVP-401 targeted test nodes required')
raw = Path('.runtime', label + '.txt')
meta = raw.with_suffix('.json')
if raw.exists() or meta.exists():
    raise SystemExit('Preserve prior records: choose a fresh label')
paths = sorted(set(Path('apps/api/app').rglob('*.py')) | set(Path('apps/api/alembic').rglob('*.py')))
before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
command = [sys.executable, '-m', 'pytest', *tests, '--no-cov', '-vv', '-x', '-p', 'no:cacheprovider']
start = time.monotonic()
record = {'started_at': datetime.now(timezone.utc).isoformat(), 'command': command, 'source_before': before,
          'pytest_addopts': os.environ.get('PYTEST_ADDOPTS', ''),
          'pythonpath': os.environ.get('PYTHONPATH', ''),
          'phase_probe_sha256': hashlib.sha256(Path('.runtime/mvp401_phase_probe.py').read_bytes()).hexdigest() if 'mvp401_phase_probe' in os.environ.get('PYTEST_ADDOPTS', '') else None,
          'code_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()}
with raw.open('xb') as log:
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               env=dict(os.environ, PYTHONUTF8='1', PYTHONUNBUFFERED='1'))
    assert process.stdout is not None
    for line in process.stdout:
        log.write(line)
        log.flush()
        print(line.decode('utf-8', errors='replace'), end='', flush=True)
    code = process.wait()
record.update(exit_code=code, finished_at=datetime.now(timezone.utc).isoformat(),
              elapsed_seconds=time.monotonic()-start,
              log_sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),
              changed_during_run=[p for p,h in before.items() if not Path(p).exists() or hashlib.sha256(Path(p).read_bytes()).hexdigest()!=h])
meta.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({k:v for k,v in record.items() if k!='source_before'}, ensure_ascii=False), flush=True)
raise SystemExit(code)
