const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
test('desktop bridge explicitly enables UTF-8 and round-trips newer emojis', () => {
  const root = path.join(__dirname, '../..');
  const main = fs.readFileSync(path.join(root, 'desktop/main.cjs'), 'utf8');
  const backend = fs.readFileSync(path.join(root, 'iam_desktop.py'), 'utf8');
  assert.match(main, /\['-X','utf8','-u','-m','iam_desktop'\]/);
  assert.match(backend, /sys.stdin.reconfigure\(encoding='utf-8', errors='strict'\)/);
  const python = process.env.IAM_DESKTOP_PYTHON || (fs.existsSync(path.join(root, '.venv/Scripts/python.exe')) ? path.join(root, '.venv/Scripts/python.exe') : 'python3');
  const sample = '🥹🫠🫶 — café';
  const result = spawnSync(python, ['-X', 'utf8', '-c', "import sys,json; sys.stdin.reconfigure(encoding='utf-8', errors='strict'); print(json.dumps(json.loads(sys.stdin.readline())))"], {
    input: JSON.stringify({body: sample}) + '\n', encoding: 'utf8', windowsHide: true
  });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(JSON.parse(result.stdout).body, sample);
});
