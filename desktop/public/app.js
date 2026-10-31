const $ = id => document.getElementById(id);
const mailbox = new URLSearchParams(location.search).get('mailbox') || 'buddies';
const headerPortrait = document.createElement('span'); headerPortrait.id = 'headerPortrait';
if (mailbox !== 'buddies') document.querySelector('header .logo').replaceWith(headerPortrait);
const call = (action, args) => window.iam.call(action, args);
if(mailbox==='buddies') {
  const connect=document.createElement('button');connect.textContent='Connect phone';
  connect.onclick=()=>call('control-window',{mailbox,kind:'pairing'}).catch(e=>toastNotice(e.message));
  $('roster').prepend(connect);
  const friends=document.createElement('button');friends.textContent='My friend requests';
  friends.onclick=()=>call('control-window',{mailbox,kind:'friends'}).catch(e=>toastNotice(e.message));
  $('roster').prepend(friends);
}
let sessionReconnect;
// Notices are non-modal overlays: they never resize the conversation or steal focus.
const toast = document.createElement('aside'); toast.id = 'toast'; toast.hidden = true;
const toastText = document.createElement('span'); toastText.setAttribute('role', 'status'); toastText.setAttribute('aria-live', 'polite');
const toastClose = document.createElement('button'); toastClose.type = 'button'; toastClose.textContent = '×'; toastClose.setAttribute('aria-label', 'Dismiss notification');
toast.append(toastText, toastClose); document.body.append(toast);
let toastTimer, lastToast = '', lastToastAt = 0;
function hideToast() { clearTimeout(toastTimer); toast.hidden = true; }
function toastNotice(text) {
  if (!text.trim() || (text === lastToast && Date.now() - lastToastAt < 30000)) return;
  lastToast = text; lastToastAt = Date.now(); toastText.textContent = text; toast.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(hideToast, 10000);
}
toastClose.onclick = hideToast;
toast.onmouseenter = () => clearTimeout(toastTimer);
toast.onmouseleave = () => { clearTimeout(toastTimer); toastTimer = setTimeout(hideToast, 5000); };
toast.onfocusin = () => clearTimeout(toastTimer);
toast.onfocusout = toast.onmouseleave;
function toastFrom(element) {
  element.removeAttribute('role'); element.removeAttribute('aria-live');
  new MutationObserver(() => toastNotice(element.textContent)).observe(element, {childList: true, characterData: true, subtree: true});
}
toastFrom($('notice'));
const themeButton = document.createElement('button');
themeButton.id = 'themeToggle';
document.querySelector('header').append(themeButton);
function applyTheme(value) {
  document.documentElement.dataset.theme = value === 'light' ? 'light' : 'dark';
  themeButton.textContent = value === 'light' ? 'Dark mode' : 'Light mode';
  themeButton.setAttribute('aria-label', `Switch to ${themeButton.textContent}`);
}
applyTheme('dark');
call('theme').then(applyTheme).catch(() => {});
themeButton.onclick = async () => {
  const value = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
  applyTheme(value);
  try { await call('theme', {value}); } catch {}
};
// Keep open buddy/chat windows consistent without refreshing a draft.
setInterval(() => call('theme').then(applyTheme).catch(() => {}), 2000);
let roster = [], approvals = [], initialized = false, currentApproval = null;
const rosterHealth = document.createElement('p');
rosterHealth.setAttribute('role', 'status');
rosterHealth.textContent = 'Loading buddy list…';
if (mailbox === 'buddies') $('buddies').before(rosterHealth);
const profileDialog = document.createElement('dialog'); profileDialog.className = 'profile-dialog';
const profileTitle = document.createElement('h2'); profileTitle.id = 'profileTitle';
profileDialog.setAttribute('aria-labelledby', 'profileTitle');
const profileDetails = document.createElement('div');
const profileClose = document.createElement('button'); profileClose.type = 'button'; profileClose.textContent = 'Close';
profileClose.onclick = () => profileDialog.close();
profileDialog.append(profileTitle, profileDetails, profileClose); document.body.append(profileDialog);
function showProfile() {
  const buddy = roster.find(b => b.mailbox === mailbox); if (!buddy) return;
  profileTitle.textContent = buddy.name; profileDetails.replaceChildren(avatar(buddy));
  for (const [key, label] of [['pronouns','Pronouns'],['status','Status'],['greeting','Greeting'],['bio','About'],['mood','Mood']]) {
    const value = buddy.profile[key]; if (!value) continue;
    const heading = document.createElement('h3'), body = document.createElement('p');
    heading.textContent = label; body.textContent = value; profileDetails.append(heading, body);
  }
  if (!profileDialog.open) profileDialog.showModal();
}
if (mailbox !== 'buddies') {
  for (const trigger of [headerPortrait, $('title')]) {
    trigger.classList.add('profile-trigger'); trigger.tabIndex = 0;
    trigger.setAttribute('role', 'button'); trigger.setAttribute('aria-label', 'View profile');
    trigger.onclick = showProfile;
    trigger.onkeydown = event => { if (event.key === 'Enter' || event.key === ' ') {event.preventDefault();showProfile();} };
  }
}
const awayDialog = document.createElement('dialog'); awayDialog.className = 'away-dialog';
const awayTitle = document.createElement('h2'), awayPresence = document.createElement('p');
const awayBody = document.createElement('p'), awayRuntime = document.createElement('small');
awayBody.className = 'away-message';
const awayClose = document.createElement('button'); awayClose.type = 'button'; awayClose.textContent = 'Close';
awayClose.onclick = () => awayDialog.close();
awayTitle.id = 'awayTitle'; awayDialog.setAttribute('aria-labelledby', 'awayTitle');
awayDialog.append(awayTitle, awayPresence, awayBody, awayRuntime, awayClose); document.body.append(awayDialog);
function chosenStatus(buddy) {
  const labels = {available: 'Available', away: 'Away', busy: 'Busy', do_not_disturb: 'Do not disturb'};
  return [labels[buddy.profile.presence], buddy.profile.status].filter(Boolean).join(' · ') || 'View status';
}
function showAway(mailboxId) {
  const buddy = roster.find(b => b.mailbox === mailboxId); if (!buddy) return;
  awayTitle.textContent = `${buddy.name}'s status`;
  awayPresence.textContent = chosenStatus(buddy);
  awayBody.textContent = buddy.profile.away_message || 'No away message set.';
  awayRuntime.textContent = `Session: ${buddy.availability}. Chosen status does not control delivery or wake permissions.`;
  if (!awayDialog.open) awayDialog.showModal();
}
if (mailbox !== 'buddies') {
  const statusButton = document.createElement('button'); statusButton.type = 'button'; statusButton.className = 'chosen-status';
  statusButton.id = 'chosenStatus'; statusButton.textContent = 'View status';
  statusButton.onclick = () => showAway(mailbox); $('subtitle').after(statusButton);
}
let approvalFields = [], previousNotices = new Set();
const formFields = document.createElement('div');
$('details').after(formFields);
const noticeButton = document.createElement('button');
noticeButton.type = 'button'; noticeButton.textContent = 'Important notices';
noticeButton.hidden = mailbox !== 'buddies';
document.querySelector('footer').prepend(noticeButton);
const noticeDialog = document.createElement('dialog');
const noticeTitle = document.createElement('h2'); noticeTitle.textContent = 'Important notices';
const noticeItems = document.createElement('div');
const noticeClose = document.createElement('button'); noticeClose.textContent = 'Close';
noticeClose.onclick = () => noticeDialog.close();
noticeDialog.append(noticeTitle, noticeItems, noticeClose); document.body.append(noticeDialog);
noticeButton.onclick = () => noticeDialog.showModal();
function renderNotices(notices) {
  noticeButton.textContent = `Important notices (${notices.length})`;
  noticeItems.replaceChildren();
  for (const item of notices) {
    const card = document.createElement('section'), title = document.createElement('h3'), body = document.createElement('p');
    title.textContent = `${item.kind === 'infrastructure' ? 'Infrastructure alert' : 'Agent notice'} — ${roster.find(b => b.mailbox === item.mailbox)?.name || item.mailbox}`;
    body.textContent = item.body; body.style.whiteSpace = 'pre-wrap';
    const time = document.createElement('time'); time.dataset.epoch = item.created; time.textContent = IAMMessageTime.format(item.created);
    const dismiss = document.createElement('button'); dismiss.textContent = 'Dismiss';
    dismiss.onclick = () => call('dismiss-notice', {id: item.id}).then(refresh).catch(e => toastNotice(e.message));
    const chat = document.createElement('button'); chat.textContent = 'Open chat';
    chat.onclick = () => call('window', {mailbox: item.mailbox}).catch(e => toastNotice(e.message));
    card.append(title, time, body, chat, dismiss); noticeItems.append(card);
  }
  if (!notices.length) noticeItems.textContent = 'No outstanding notices.';
  if (notices.some(n => !previousNotices.has(n.id))) {
    tone('approval'); toastNotice('Important notices need your attention. Open Important notices below.');
  }
  previousNotices = new Set(notices.map(n => n.id));
}
let previousApprovals = new Set(), previousReplies = new Set(), previousStates = new Map();
let previousUnread = new Map(), historySignature = '', refreshing = false, audio;
let updateHistory;
const usageFooter = document.createElement('div');
const respondingIndicator = document.createElement('div');
let respondingExpiry;
function clearResponding() { respondingIndicator.replaceChildren(); respondingIndicator.dataset.label = ''; }
respondingIndicator.className = 'responding-indicator';
respondingIndicator.setAttribute('role', 'status');
respondingIndicator.setAttribute('aria-live', 'polite');
$('compose').before(respondingIndicator);
const contextHeader = document.createElement('small');
contextHeader.id = 'contextUsage';
contextHeader.hidden = mailbox === 'buddies';
document.querySelector('header').append(contextHeader);
usageFooter.id = 'weeklyUsage';
document.querySelector('footer').prepend(usageFooter);
function contextLabel(context) {
  if (context?.stale) return `Context ~${context.remainingPercent}% left (last known)`;
  if (context?.state === 'compacting') return 'Compacting…';
  if (!context) return 'Context: awaiting usage report';
  return `Context ~${context.remainingPercent}% left · ${new Date(context.updatedAt * 1000).toLocaleTimeString()}`;
}

function tone(kind) {
  try {
    audio ||= new AudioContext(); audio.resume();
    const now = audio.currentTime;
    const notes = kind === 'approval' ? [880, 660, 880] : kind === 'arrival' ? [440, 660] : kind === 'departure' ? [660, 330] : [620, 830];
    notes.forEach((frequency, i) => {
      const oscillator = audio.createOscillator(), gain = audio.createGain();
      oscillator.frequency.value = frequency;
      gain.gain.setValueAtTime(0, now + i * .12);
      gain.gain.linearRampToValueAtTime(.08, now + i * .12 + .01);
      gain.gain.exponentialRampToValueAtTime(.001, now + i * .12 + .14);
      oscillator.connect(gain); gain.connect(audio.destination);
      oscillator.start(now + i * .12); oscillator.stop(now + i * .12 + .15);
    });
  } catch {}
}

function avatar(buddy) {
  const el = document.createElement(buddy.profile.avatar ? 'img' : 'span'); el.className = 'avatar';
  if (buddy.profile.avatar) { el.src = buddy.profile.avatar; el.alt = ''; }
  else el.textContent = buddy.name.slice(0, 1);
  if (buddy.profile.color) el.style.border = `3px solid ${buddy.profile.color}`;
  return el;
}

async function inspectApproval(id) {
  try {
    const item = await call('approval', {id}); currentApproval = id;
    const persistence = $('persistChoice'); persistence.replaceChildren();
    for (const [value, label] of [['', 'This call only'], ['session', 'For this session'], ['always', 'Always allow this tool (saved by Codex)']]) {
      if (value && !(item.persistence || []).includes(value)) continue;
      const option = document.createElement('option'); option.value=value; option.textContent=label; persistence.append(option);
    }
    persistence.value=''; $('persistLabel').hidden=!(item.persistence || []).length;
    $('accept').textContent = item.form ? 'Submit response' : 'Approve once';
    persistence.onchange = () => { $('accept').textContent = persistence.value === 'always' ? 'Always allow this tool' : persistence.value === 'session' ? 'Allow for session' : item.form?.length ? 'Submit response' : 'Approve once'; };
    persistence.onchange();
    $('approvalWho').textContent = roster.find(b => b.mailbox === item.mailbox)?.name || item.mailbox;
    $('details').textContent = JSON.stringify({kind: item.method, ...item.details}, null, 2);
    formFields.replaceChildren(); approvalFields = [];
    for (const field of item.form || []) {
      const label = document.createElement('label'), select = document.createElement('select');
      label.textContent = `${field.label}${field.required ? ' (required)' : ''}: `;
      const blank = document.createElement('option'); blank.value = ''; blank.textContent = 'Choose…'; select.append(blank);
      const options = field.type === 'boolean' ? [false, true] : field.options;
      options.forEach((value, index) => { const option = document.createElement('option'); option.value = String(index); option.textContent = String(value); select.append(option); });
      label.append(select); formFields.append(label, document.createElement('br'));
      approvalFields.push({key: field.key, select, options});
    }
    $('approval').showModal();
  } catch (error) { $('notice').textContent = error.message; }
}

function renderRoster() {
  const filter = $('search').value.toLowerCase();
  // Commit only a complete render; an exception must not erase the last good list.
  const fragment = document.createDocumentFragment();
  for (const buddy of [...roster].sort((a, b) => a.name.localeCompare(b.name))) {
    if (!`${buddy.name} ${buddy.mailbox}`.toLowerCase().includes(filter)) continue;
    const row = document.createElement('div'); row.className = 'buddy';
    const open = document.createElement('button'); open.className = 'buddy-open'; open.append(avatar(buddy));
    const info = document.createElement('span'); info.className = 'info';
    const name = document.createElement('strong'); name.textContent = buddy.name;
    if (roster.filter(b => b.name.toLowerCase() === buddy.name.toLowerCase()).length > 1) {
      const address = document.createElement('small'); address.className = 'mailbox-label'; address.textContent = buddy.mailbox;
      name.append(address);
    }
    const status = document.createElement('small');
    status.textContent = `Session: ${buddy.availability}`;
    info.append(name, status); open.append(info);
    const context = document.createElement('small'); context.className = 'context-usage';
    context.textContent = contextLabel(buddy.context);
    context.title = 'Estimate from the latest reported token usage, not lifetime tokens. Updates while attached; may differ from the CLI after compaction.';
    info.append(context);
    open.title = `${buddy.name} <${buddy.mailbox}> — ${buddy.availability}`;
    open.onclick = () => call('window', {mailbox: buddy.mailbox}).catch(e => { $('notice').textContent = e.message; });
    row.append(open);
    const chosen = document.createElement('button'); chosen.type = 'button'; chosen.className = 'chosen-status';
    chosen.textContent = chosenStatus(buddy); chosen.title = `Read ${buddy.name}'s chosen status and away message`;
    chosen.onclick = () => showAway(buddy.mailbox); row.append(chosen);
    if (buddy.unread) { const badge = document.createElement('span'); badge.className = 'badge'; badge.textContent = buddy.unread; badge.title = 'Unread addressed replies'; row.append(badge); }
    const pending = approvals.find(a => a.mailbox === buddy.mailbox);
    if (pending) {
      const badge = document.createElement('button'); badge.className = 'badge'; badge.textContent = '◆';
      badge.title = 'Needs your approval'; badge.setAttribute('aria-label', `Review approval for ${buddy.name}`);
      badge.onclick = () => inspectApproval(pending.id); row.append(badge);
    }
    fragment.append(row);
  }
  const visible = fragment.childNodes.length;
  $('buddies').replaceChildren(fragment);
  rosterHealth.textContent = !roster.length ? 'No registered buddies.' : !visible ? 'No buddies match your search.' : '';
}

function creditLabel(credits) {
  if (credits?.unlimited === true) return 'Credits: unlimited';
  const raw = credits?.balance;
  const balance = (typeof raw === 'number' || typeof raw === 'string' && /^\d+(\.\d+)?$/.test(raw)) ? Number(raw) : NaN;
  return Number.isFinite(balance) && balance >= 0 ? `Credits: ${balance.toFixed(2)}` : 'Credits: unavailable';
}

async function refresh() {
  if (refreshing) return; refreshing = true;
  const previousRoster = roster, previousApprovalList = approvals;
  try {
    const snapshot = await call('snapshot'); roster = snapshot.buddies; approvals = snapshot.approvals;
    const weekly = snapshot.weekly;
    usageFooter.textContent = (weekly ? `Account weekly: ${weekly.remainingPercent}% left` : 'Account weekly: unavailable') + ' ● ' + creditLabel(snapshot.credits);
    usageFooter.title = weekly ? `Checked ${new Date(weekly.updatedAt * 1000).toLocaleTimeString()}${weekly.resetsAt ? ' · Resets ' + new Date(weekly.resetsAt * 1000).toLocaleString() : ''}` : 'Waiting for the account quota report; no allowance is assumed.';
    if (mailbox === 'buddies') {
      renderNotices(snapshot.notices || []);
      if (initialized) {
        if (approvals.some(a => !previousApprovals.has(a.id))) tone('approval');
        if (roster.some(b => b.unread > (previousUnread.get(b.mailbox) || 0))) tone('message');
        for (const b of roster) {
          const old = previousStates.get(b.mailbox);
          if (old && old !== b.availability) {
            if (b.availability === 'available') tone('arrival');
            else if (b.availability === 'unavailable') tone('departure');
          }
        }
      }
      previousApprovals = new Set(approvals.map(a => a.id));
      previousStates = new Map(roster.map(b => [b.mailbox, b.availability]));
      previousUnread = new Map(roster.map(b => [b.mailbox, b.unread]));
      renderRoster();
    } else {
      const buddy = roster.find(b => b.mailbox === mailbox);
      sessionReconnect?.observe(buddy);
      const responseLabel = buddy?.responding ? `${buddy.name} is responding` : '';
      clearTimeout(respondingExpiry);
      respondingExpiry = setTimeout(clearResponding, 7000);
      if (respondingIndicator.dataset.label !== responseLabel) {
        respondingIndicator.dataset.label = responseLabel;
        respondingIndicator.replaceChildren();
        if (responseLabel) {
          const label = document.createElement('span'); label.textContent = responseLabel;
          const dots = document.createElement('span'); dots.className = 'responding-dots'; dots.textContent = '•••'; dots.setAttribute('aria-hidden', 'true');
          respondingIndicator.append(label, dots);
        }
      }
      $('title').textContent = buddy?.name || mailbox; $('subtitle').textContent = buddy?.availability || 'unavailable';
      contextHeader.textContent = contextLabel(buddy?.context);
      contextHeader.title = 'Estimate from the latest reported usage; timestamp shows when it was received.';
      $('profile').replaceChildren();
      if (buddy) {
        $('chosenStatus').textContent = chosenStatus(buddy);
        headerPortrait.replaceChildren(avatar(buddy));
        const text = document.createElement('span');
        text.textContent = [buddy.profile.pronouns, buddy.profile.status].filter(Boolean).join(' · ');
        text.title = text.textContent;
        $('profile').append(text);
      }
      $('profile').hidden = !$('profile').textContent.trim();
      const history = await call('history', {mailbox});
      if (initialized && history.some(m => m.reply && !previousReplies.has(m.id))) tone('message');
      previousReplies = new Set(history.filter(m => m.reply).map(m => m.id));
      const signature = JSON.stringify(history);
      if (signature !== historySignature) {
        if (!updateHistory) updateHistory = IAMHistoryView.create($('messages'), message => {
          const group = document.createElement('section'); group.className = 'history-message';
          if (message.images?.length) {
            const attached = document.createElement('div'); attached.className = 'state';
            attached.textContent = 'Attached: ' + message.images.map(i => i.name).join(', ');
            group.append(attached);
          }
          for (const [text, who] of [[message.body, 'you'], [message.reply, 'them']]) {
            if (!text && !(who === 'you' && (message.images?.length || message.files?.length))) continue;
            const wrapper = document.createElement('div'); wrapper.className = `message-wrap ${who}`;
            if (who === 'them' && (!message.notice_kind || message.notice_kind === 'agent')) wrapper.dataset.readEvent = message.id + (message.notice_kind ? ':notice' : ':reply');
            const bubble = document.createElement('div'); bubble.className = `bubble ${who}`; IAMMarkdown.render(bubble, text);
            IAMEmoji.decorate(bubble);
            if (who === 'them' && message.notice_kind) {
              const label = document.createElement('small');
              label.textContent = message.notice_kind === 'infrastructure' ? 'IAM system alert' : 'Message sent to you';
              bubble.prepend(label);
            }
            if (who === 'them') for (const ref of message.reply_images || []) {
              const button = document.createElement('button'); button.type = 'button'; button.className = 'shared-picture';
              button.textContent = `View image: ${ref.name}`;
              button.onclick = () => call('image-window', ref).catch(error => toastNotice(error.message));
              bubble.append(button);
              const download = document.createElement('button'); download.type = 'button'; download.textContent = 'Download photo…';
              download.onclick = async () => {
                download.disabled = true;
                try { if (await call('image-save', ref)) toastNotice('Photo saved.'); }
                catch (error) { toastNotice(error.message); }
                finally { download.disabled = false; }
              };
              bubble.append(download);
              call('published-image', ref).then(item => {
                const preview = document.createElement('img'); preview.alt = item.name; preview.src = item.data;
                button.replaceChildren(preview); button.title = `Open ${item.name} in image viewer`;
              }).catch(error => { button.title = error.message; });
            }
            if (!text) bubble.textContent = 'Attachment';
            for (const ref of (who === 'you' ? message.files : message.reply_files) || []) {
              const card = document.createElement('div'); card.className = 'file-card';
              const label = document.createElement('p');
              const ext = ref.name.split('.').pop().toLowerCase();
              label.textContent = `${ref.name} · ${ext.toUpperCase()} · ${(ref.size / 1024).toFixed(1)} KB`;
              card.append(label);
              const previewable = ['txt','md','csv','json','log'].includes(ext);
              for (const [action, title] of [[previewable ? 'file-preview' : 'file-open', previewable ? 'Preview' : 'Open…'], ['file-save', 'Save As…']]) {
                const button = document.createElement('button'); button.type = 'button'; button.textContent = title;
                button.onclick = () => call(action, ref).catch(error => toastNotice(error.message)); card.append(button);
              }
              bubble.append(card);
            }
            const meta = document.createElement('div'); meta.className = 'bubble-meta';
            const stamp = document.createElement('time');
            const timestamp = who === 'you' ? message.created : message.replied_at;
            if (typeof timestamp === 'number') {
              const date = new Date(timestamp * 1000);
              stamp.dateTime = date.toISOString(); stamp.dataset.epoch = timestamp; stamp.textContent = IAMMessageTime.format(timestamp); stamp.title = date.toLocaleString();
            } else { stamp.textContent = 'Time unavailable'; stamp.title = 'This older reply predates recorded reply timestamps.'; }
            meta.append(stamp);
            if (who === 'you' && (message.reply != null || ['queued', 'submitting', 'accepted'].includes(message.state))) {
              const delivery = message.reply != null ? 'replied' : message.error === 'Turn finished; no reply published.' ? 'no-reply' : message.state;
              const labels = {queued: 'Queued', submitting: 'Submitting', accepted: 'Accepted by session (not a read receipt)', replied: 'Replied', 'no-reply': 'Turn finished; no reply published.'};
              const symbols = {queued: '', submitting: '\u00b7', accepted: '\u2022', replied: '\u2713', 'no-reply': '!'};
              const receipt = document.createElement('span'); receipt.className = `reply-receipt delivery-${delivery}`; receipt.textContent = symbols[delivery];
              receipt.title = labels[delivery]; receipt.setAttribute('role', 'img'); receipt.setAttribute('aria-label', labels[delivery]); meta.append(receipt);
            }
            wrapper.append(bubble, meta); group.append(wrapper);
          }
          if (message.error || !['queued', 'submitting', 'accepted', 'replied'].includes(message.state)) {
            const state = document.createElement('div'); state.className = 'state'; state.textContent = message.error || message.state;
            group.append(state);
          }
          return group;
        });
        updateHistory(history);
        historySignature = signature;
      }
      if (approvals.some(a => a.mailbox === mailbox)) $('notice').textContent = '◆ Approval needed — review it from the buddy list.';
    }
    initialized = true;
  } catch (error) {
    roster = previousRoster; approvals = previousApprovalList;
    clearResponding(); $('notice').textContent = error.message;
    rosterHealth.textContent = initialized
      ? 'Buddy list refresh failed. Showing the last successful list; retrying automatically.'
      : 'Buddy list unavailable. Retrying automatically.';
  }
  finally { refreshing = false; }
}

$('search').oninput = renderRoster;
$('later').onclick = () => $('approval').close();
for (const decision of ['accept', 'decline']) $(decision).onclick = async () => {
  try {
    const content = Object.fromEntries(approvalFields.filter(f => f.select.value !== '').map(f => [f.key, f.options[Number(f.select.value)]]));
    const persist = decision === 'accept' ? $('persistChoice').value : '';
    await call('decide', {id: currentApproval, decision, content, ...(persist ? {persist} : {})}); $('approval').close(); await refresh();
  }
  catch (error) { $('notice').textContent = error.message; }
};
let sending = false;
let stagedImages = [], loadingImages = false, lastAttempt = null;
const attachments = document.createElement('div'); attachments.id = 'attachments';
$('compose').before(attachments);
const attachmentStatus = document.createElement('p'); attachmentStatus.id = 'attachmentStatus';
attachmentStatus.setAttribute('role', 'status'); attachmentStatus.setAttribute('aria-live', 'polite');
attachments.before(attachmentStatus);
toastFrom(attachmentStatus);
const composeTools = document.createElement('div'); composeTools.id = 'composeTools'; composeTools.setAttribute('role', 'group'); composeTools.setAttribute('aria-label', 'Message tools');
const attachButton = document.createElement('button'); attachButton.type = 'button'; attachButton.id = 'attachButton'; attachButton.setAttribute('aria-label', 'Attach images or documents');
attachButton.title = 'Attach images or documents (or paste / drop them here)';
const filePicker = document.createElement('input'); filePicker.type = 'file'; filePicker.accept = 'image/png,image/jpeg,image/webp,.txt,.md,.csv,.json,.log,.pdf,.docx,.xlsx,.pptx'; filePicker.multiple = true; filePicker.hidden = true;
composeTools.append(attachButton, filePicker); $('compose').prepend(composeTools);
attachButton.onclick = () => filePicker.click();
function showAttachments() {
  attachmentStatus.textContent = loadingImages ? 'Loading attachments…' : sending && stagedImages.length ? 'Sending attachments…' : stagedImages.length ? 'Attachments ready. Press Send when ready.' : '';
  attachmentStatus.hidden = !attachmentStatus.textContent;
  attachments.replaceChildren();
  stagedImages.forEach((item, index) => {
    const tile = document.createElement('div'), img = document.createElement('img'), remove = document.createElement('button');
    if (!item.document) { img.src = item.data; img.alt = item.name; img.title = item.name; }
    const label = document.createElement('span'); label.textContent = item.name;
    remove.type = 'button'; remove.textContent = 'Remove'; remove.disabled = sending;
    remove.onclick = () => { stagedImages.splice(index, 1); showAttachments(); };
    tile.append(item.document ? label : img, remove); attachments.append(tile);
  });
}
async function stageImages(files) {
  if (sending || loadingImages) return;
  loadingImages = true;
  showAttachments();
  try {
    const next = [...stagedImages];
    for (const file of files) {
      const documentFile = !['image/png','image/jpeg','image/webp'].includes(file.type);
      if (documentFile && !/\.(txt|md|csv|json|log|pdf|docx|xlsx|pptx)$/i.test(file.name)) throw new Error('Choose an image or supported document (TXT, MD, CSV, JSON, LOG, PDF, DOCX, XLSX, PPTX).');
      if (next.length >= 4 || file.size > 4000000 || next.reduce((n, i) => n + i.size, 0) + file.size > 8000000) throw new Error('Up to four attachments: 4 MB each, 8 MB total.');
      const data = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = () => reject(new Error('Image could not be read')); reader.readAsDataURL(file); });
      next.push({name: file.name || 'Pasted image', data: documentFile ? data.split(',')[1] : data, size: file.size, document: documentFile});
    }
    stagedImages = next;
  } catch (error) { $('notice').textContent = error.message; }
  finally { loadingImages = false; filePicker.value = ''; showAttachments(); }
}
filePicker.onchange = () => stageImages([...filePicker.files]);
$('body').addEventListener('paste', event => {
  const files = [...(event.clipboardData?.items || [])].filter(i => i.kind === 'file').map(i => i.getAsFile()).filter(Boolean);
  if (files.length) { event.preventDefault(); stageImages(files); }
});
document.addEventListener('dragover', event => event.preventDefault());
document.addEventListener('drop', event => {
  event.preventDefault();
  if (mailbox !== 'buddies') stageImages([...(event.dataTransfer?.files || [])]);
});
$('body').addEventListener('keydown', event => {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing || event.keyCode === 229) return;
  event.preventDefault();
  if (!event.repeat && !sending && !loadingImages) $('compose').requestSubmit($('compose').querySelector('button[type="submit"]'));
});
$('body').title = 'Enter to send; Shift+Enter for a new line';
if (mailbox !== 'buddies') IAMEmoji.mount($('compose'), $('body'), toastNotice, composeTools);
if (mailbox !== 'buddies') {
  for (const [kind, symbol, label] of [['status', 'i', 'Session status'], ['compact', '↧', 'Compact context'], ['stop', '■', 'Stop current turn'], ['more', '⋯', 'More session controls']]) {
    const button = document.createElement('button'); button.type = 'button'; button.textContent = symbol;
    button.title = label; button.setAttribute('aria-label', label);
    button.onclick = () => call('control-window', {mailbox, kind}).catch(error => toastNotice(error.message));
    composeTools.append(button);
  }
}
$('compose').onsubmit = async event => {
  if (sending || loadingImages) { event.preventDefault(); return; }
  event.preventDefault(); const body = $('body').value; if (!body.trim() && !stagedImages.length) return;
  const images = stagedImages.filter(i => !i.document).map(({name, data}) => ({name, data}));
  const files = stagedImages.filter(i => i.document).map(({name, data}) => ({name, data}));
  const signature = JSON.stringify({body, images, files});
  if (!lastAttempt || lastAttempt.signature !== signature) lastAttempt = {signature, id: crypto.randomUUID()};
  const button = $('compose').querySelector('button[type="submit"]'); sending = true; button.disabled = true;
  attachButton.disabled = true; showAttachments();
  try {
    const session = await call('ensure-session', {mailbox});
    if (session.state === 'cancelled') return;
    if (session.state !== 'bound') throw new Error('Session is not ready; draft kept.');
    await call('send', {mailbox, body, images, files, request_id: lastAttempt.id}); lastAttempt = null; stagedImages = []; showAttachments(); if ($('body').value === body) $('body').value = ''; await refresh();
  }
  catch (error) { $('notice').textContent = error.message; }
  finally { sending = false; button.disabled = false; attachButton.disabled = false; showAttachments(); }
};
if (mailbox !== 'buddies') {
  $('roster').hidden = true; $('conversation').hidden = false;
  // Restored views may attach only to an existing binding. Never start a thread or send input.
  const attachmentBar = document.createElement('div'); attachmentBar.className = 'session-reconnect';
  const attachmentText = document.createElement('span'); attachmentText.setAttribute('role', 'status');
  const retryAttach = document.createElement('button'); retryAttach.type = 'button'; retryAttach.textContent = 'Retry connection';
  attachmentBar.append(attachmentText, retryAttach); $('messages').before(attachmentBar);
  const reconnect = IAMReconnect.controller(call, mailbox, (text, retry) => {
    attachmentText.textContent = text; retryAttach.hidden = !retry; attachmentBar.hidden = !text;
  });
  retryAttach.onclick = reconnect;
  sessionReconnect = reconnect;
  reconnect();
  const checkRead = IAMReadVisible.tracker($('messages'), document, async event_ids => {
    const result = await call('mark-read', {mailbox,event_ids}); return result.marked !== false;
  });
  setInterval(checkRead, 750);
}
setInterval(() => IAMMessageTime.refresh(document), 30000);
refresh(); setInterval(refresh, 2000);
