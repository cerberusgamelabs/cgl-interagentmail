(function (root) {
  function controller(call, mailbox, show) {
    let running = false;
    let connected = false;
    function report(text, retry) {
      show(connected ? '' : text, connected ? false : retry);
    }
    async function reconnect() {
      if (running) return;
      running = true;
      report('Connecting to the existing session…', false);
      try {
        const ready = await call('session-readiness', {mailbox});
        if (ready.state === 'unbound') {
          report('No conversation bound yet. First Send will offer setup.', false);
          return;
        }
        if (ready.state !== 'bound') throw new Error('Session readiness is unavailable.');
        const result = await call('open', {mailbox});
        if (result.state === 'attached') show('', false);
        else if (result.state === 'busy') report('Session busy—reconnect when ready.', true);
        else throw new Error('Connection was not confirmed.');
      } catch (error) {
        report(`Could not connect: ${error.message || 'Service unavailable.'}`, true);
      } finally { running = false; }
    };
    reconnect.observe = function (buddy) {
      connected = buddy?.sessionConnected === true;
      if (connected) show('', false);
    };
    return reconnect;
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {controller};
  else root.IAMReconnect = {controller};
})(typeof window !== 'undefined' ? window : globalThis);
