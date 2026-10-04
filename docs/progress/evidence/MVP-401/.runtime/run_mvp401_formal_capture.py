import hashlib
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

command = ['.venv/Scripts/python.exe', '.runtime/migrate_mvp401_formal.py',
           '--apply-preserving-old-data']
raw = Path('.runtime/MVP-401-formal-preserving-upgrade.txt')
meta = raw.with_suffix('.json')
assert not raw.exists() and not meta.exists(), 'Preserve prior attempt'
source = Path(command[1])
record = {'command': command, 'started_at': datetime.now(UTC).isoformat(),
          'code_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
          'helper_sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
started = time.monotonic()
result = subprocess.run(command, capture_output=True)
raw.write_bytes(result.stdout + result.stderr)
record.update(exit_code=result.returncode, elapsed_seconds=time.monotonic()-started,
              finished_at=datetime.now(UTC).isoformat(),
              helper_unchanged=record['helper_sha256'] == hashlib.sha256(source.read_bytes()).hexdigest(),
              log_sha256=hashlib.sha256(raw.read_bytes()).hexdigest())
meta.write_text(json.dumps(record, indent=2), encoding='utf-8')
print(raw.read_text(encoding='utf-8', errors='replace'))
print(json.dumps(record))
raise SystemExit(result.returncode)
