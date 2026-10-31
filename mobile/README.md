# IAM Messenger for Android

## Preview status

Android pilot source: 0.9.9b-pilot (version code 24).
Source availability does not grant hosted admission, activate a relay, or
constitute a signed public Android release. Production signing keys and Firebase
sender credentials are not included.

Developed with **Rowan — AI development collaborator** at Cerberus Game Labs,
under human project direction and review.

## Included functionality

- Owner-paired conversations, buddy-list home, isolated drafts, paged history,
  full-message viewing, foreground updates, and opt-in notifications.
- Same-relay friendships and invitations with separate participant history.
- Original image/document upload and Save As, with one-to-four-image preview
  grids. Cropped previews never replace original attachments.
- Device-local timestamps and delivery markers. Send receipts are not proof of
  reading; agent completion receipts require explicit reply linkage.
- Encrypted retained contacts and drafts across expired login. Reauthentication
  does not silently promote old device grants.

Friends in the main mobile buddy list remain planned. Independent-server
federation and remote sensitive-tool approvals are not implemented.

## Trust and privacy

Login alone is not owner authority. Owner access requires local desktop review
of a public-key-bound pairing grant and fingerprint. Android Keystore protects
P-256 keys; saved login state uses AES-GCM in the no-backup directory.
Hardware backing is reported, not assumed. A compromised unlocked app can still
use its keys.

External agent contact defaults off. The owner permits contact or permits the
agent to choose, and each friendship requires recipient acceptance. External
messages are never owner authorization. Friend history is separate from owner
transcripts. This is not model/tool isolation: external content can enter an
agent's primary context, so owners must review the steering risk.

Attachments retain original metadata. The relay can read them; this is not
end-to-end encryption. Limits are 4 MB/file, four files and 8 MB per category.
Published relay copies expire after 30 days; unsent staging after 24 hours.
Downloaded copies cannot be revoked. Content validation is not antivirus.
Account admission, quotas, attention budgets and deployment flags depend on
the compatible relay and connector configuration.

## Build and test

Use Node, an Android SDK and Android Studio JDK compatible with the pinned
Capacitor dependencies. From this folder on Windows:

```powershell
npm.cmd ci
npm.cmd test
.\build-android.cmd
```

The debug application ID is `xyz.cerberusgamelabs.iam.messenger.dev`.
Do not enroll debug builds as production owner devices.

`build-release.cmd` synchronizes web assets and runs release unit tests,
unsigned APK assembly and lint. JAVA_HOME and ANDROID_HOME overrides are
supported; defaults target a standard local Android Studio installation.

Release signing is an operator responsibility outside this repository. Use
your own protected key and Android SDK signing tools. Never commit passwords,
keys, credentials or signed artifacts. Upgrades must retain the existing signing
identity; do not routinely uninstall or clear app data.

## Savefix and acceptance

The savefix removes large base64 payloads from Capacitor call options before
opening Android's save picker and reports success only after the output stream
closes. If Android kills the process while the picker is open, the in-memory
export expires; retry is required and a destination placeholder may remain.
Private export bytes are not persisted for recovery.

Physical-device confirmation remains pending. Before distributing a signed
build, verify image saving produces a nonempty, readable original file; test
cancellation, large attachments, background/return behavior and upgrades without
data loss. Tests and build success alone do not establish device acceptance.

These build scripts perform no server reset or service restart. Hosted
deployment, account grants and connector activation are separate operator actions.
