import hashlib
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

label, *args = sys.argv[1:]
allowed = {'goto', 'reload', 'snapshot', 'find', 'click', 'resize', 'screenshot',
           'requests', 'request', 'response-headers', 'response-body', 'console',
           'eval', 'close'}
if not label.startswith('MVP-401-') or not args or args[0] not in allowed:
    raise SystemExit('Homepage verification commands only')
dest = Path('.runtime/MVP-401-browser-cli')
dest.mkdir(exist_ok=True)
log = dest / (label + '.txt')
meta = log.with_suffix('.json')
if log.exists() or meta.exists():
    raise SystemExit('Choose a fresh evidence label')
cli = 'C:/Users/starryzac/AppData/Local/npm-cache/_npx/31e32ef8478fbf80/node_modules/@playwright/cli/playwright-cli.js'
command = [shutil.which('node'), cli, '-s=bounded401', *args]
start = time.monotonic()
record = {'started_at': datetime.now(timezone.utc).isoformat(), 'command': command,
          'browser': 'Microsoft Edge', 'observed_executable_version': '154.0.4258.37'}
result = subprocess.run(command, capture_output=True)
log.write_bytes(result.stdout + result.stderr)
record.update(exit_code=result.returncode, elapsed_seconds=time.monotonic()-start,
              finished_at=datetime.now(timezone.utc).isoformat(),
              log_sha256=hashlib.sha256(log.read_bytes()).hexdigest())
record['reported_error'] = '### Error' in result.stdout.decode('utf-8', errors='replace')
meta.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
print((result.stdout + result.stderr).decode('utf-8', errors='replace'), end='')
raise SystemExit(result.returncode or int(record['reported_error']))
