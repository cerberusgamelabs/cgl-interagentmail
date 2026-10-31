"""Run unittest without PowerShell stderr/pipeline exit-status ambiguity."""
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
result = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-q'],
                        cwd=root, capture_output=True, text=True, errors='replace')
lines = result.stderr.splitlines()
if result.returncode:
    print('\n'.join(lines[-120:]))
else:
    print('\n'.join(line for line in lines if line.startswith('Ran ') or line == 'OK'))
print('Python regression exit code:', result.returncode)
raise SystemExit(result.returncode)
