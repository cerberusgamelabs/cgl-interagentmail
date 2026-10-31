'use strict';

// Own only the backend transport, never its process or any Codex session.
function createBackendRpc(backend, {timeoutMs = 150000} = {}) {
  const pending = new Map();
  let stopped = false, nextId = 1;
  const unavailable = () => new Error('Backend connection stopped. Delivery may be uncertain; check its original request ID before retrying.');
  function fail() {
    stopped = true;
    for (const item of pending.values()) {
      clearTimeout(item.timer);
      item.reject(unavailable());
    }
    pending.clear();
  }
  // A write callback does not consume the stream's separate error event.
  backend.stdin.on('error', fail);
  backend.stdin.on('close', fail);
  backend.on('error', fail);
  backend.on('exit', fail);
  backend.on('close', fail);
  function request(action, args = {}) {
    return new Promise((resolve, reject) => {
      const stream = backend.stdin;
      if (stopped || backend.exitCode !== null || !stream.writable ||
          stream.destroyed || stream.writableEnded || stream.writableFinished) {
        reject(unavailable());
        return;
      }
      const id = nextId++;
      let frame;
      try { frame = JSON.stringify({id, action, args}) + '\n'; }
      catch { reject(new Error('Request could not be encoded')); return; }
      const timer = setTimeout(() => {
        pending.delete(id);
        reject(new Error('Operation timed out. Do not resend uncertain messages.'));
      }, timeoutMs);
      pending.set(id, {resolve, reject, timer});
      try { stream.write(frame, error => { if (error) fail(); }); }
      catch { fail(); }
    });
  }
  function receive(message) {
    const item = pending.get(message?.id);
    if (!item) return;
    clearTimeout(item.timer);
    pending.delete(message.id);
    message.error ? item.reject(new Error(message.error)) : item.resolve(message.result);
  }
  function stop() {
    if (stopped) return;
    fail(); // Reject new calls and settle old calls before ending the stream.
    try {
      if (!backend.stdin.destroyed && !backend.stdin.writableEnded) backend.stdin.end();
    } catch { /* Error has already been reported to every pending caller. */ }
  }
  return {request, receive, stop};
}

module.exports = {createBackendRpc};
