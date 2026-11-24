'use strict';
// No exception messages, source lines, paths, credentials or protocol data.
const bootstrap = `
import json, os, runpy, sys, traceback
try:
    runpy.run_module('iam_desktop', run_name='__main__')
except BaseException as exc:
    if isinstance(exc, SystemExit) and exc.code in (None, 0):
        raise
    allowed = {'ImportError','ModuleNotFoundError','OSError','PermissionError','FileNotFoundError','RuntimeError','ValueError','TypeError','AttributeError','UnicodeError','UnicodeDecodeError','SyntaxError','OperationalError'}
    kind = type(exc).__name__
    frames = traceback.extract_tb(exc.__traceback__)
    known = {'iam_desktop.py','iam_orchestrator.py','iam_service.py','interagentmail.py','iam_desktop_controls.py','iam_desktop_onboarding.py','iam_messenger_friend_delivery.py'}
    selected = [f for f in frames if os.path.basename(f.filename) in known]
    frame = selected[-1] if selected else None
    record = {'pythonError': kind if kind in allowed else 'OtherError', 'pythonFile': os.path.basename(frame.filename) if frame else 'unknown', 'lineNumber': frame.lineno if frame else 0}
    sys.stderr.write('IAM_STARTUP_DIAGNOSTIC ' + json.dumps(record) + '\\n')
    sys.stderr.flush()
    sys.exit(1)
`;
function watchStartup(stderr, diagnostics) {
  let pending='';
  stderr.on('data',chunk=>{
    pending+=chunk.toString('utf8');
    const lines=pending.split('\n');pending=lines.pop();
    if(pending.length>4096)pending='';
    for(const line of lines) {
      if(line.length>4096 || !line.startsWith('IAM_STARTUP_DIAGNOSTIC '))continue;
      try {diagnostics.event('backend-startup-failed',JSON.parse(line.slice(23)));}catch{}
    }
  });
}
module.exports={bootstrap,watchStartup};
