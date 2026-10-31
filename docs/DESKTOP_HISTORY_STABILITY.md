# Stable history refresh

Desktop history now reconciles message groups by request/notice identity and
content signature. Unchanged groups retain their DOM, loaded previews and event
handlers instead of being cleared whenever another message changes. Builds
complete before reconciliation so rendering errors preserve the old list.

Image preview buttons reserve a fixed 272px frame (260px image content) before
bytes arrive; previews use contain sizing. Native scroll anchoring is disabled
inside history to avoid fighting the explicit restoration logic.

The initial load goes to the bottom. Later updates follow new messages only
when the view was already within 32px of the bottom. Otherwise the first visible
message and its relative offset are preserved where that message still exists;
if history pruning removes it, the old scroll offset is retained and clamped.

Validation: 52 desktop unit tests, including seven reconciliation cases, and
`desktop/test/history-layout-smoke.cjs` in an isolated hidden Electron renderer.
The smoke test covers retained image identity, delayed image decoding, reading
position preservation and bottom-follow. It never connects to Messenger or
agent sessions. No production restart was performed for this change.
