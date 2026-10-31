(function (root) {
  function format(seconds, now = new Date()) {
    if (typeof seconds !== 'number' || !Number.isFinite(seconds)) return 'Time unavailable';
    const date = new Date(seconds * 1000);
    if (!Number.isFinite(date.getTime())) return 'Time unavailable';
    const sameDay = (a, b) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
    const yesterday = new Date(now); yesterday.setDate(yesterday.getDate() - 1);
    const day = sameDay(date, now) ? 'Today' : sameDay(date, yesterday) ? 'Yesterday' :
      `${String(date.getMonth()+1).padStart(2,'0')}/${String(date.getDate()).padStart(2,'0')}/${date.getFullYear()}`;
    return `${day} - ${date.toLocaleTimeString('en-US', {hour:'numeric', minute:'2-digit', hour12:true})}`;
  }
  function refresh(container) {
    for (const stamp of container.querySelectorAll('time[data-epoch]')) stamp.textContent = format(Number(stamp.dataset.epoch));
  }
  const api = {format, refresh};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.IAMMessageTime = api;
})(typeof window !== 'undefined' ? window : globalThis);
