import {mkdir,copyFile} from 'node:fs/promises';
await mkdir(new URL('./dist/',import.meta.url),{recursive:true});
for(const name of ['index.html','app.css','app.mjs','native-adapter.mjs','transport.mjs','owner-proof.mjs','conversation.mjs','buddies.mjs','images.mjs','composer.mjs','pairing.mjs','live.mjs','full-text.mjs','documents.mjs','uploads.mjs','protected-state.mjs','profiles.mjs','edge-back.mjs','friendships.mjs','friend-visible.mjs'])
  await copyFile(new URL(name,import.meta.url),new URL('dist/'+name,import.meta.url));
await copyFile(new URL('image-grid.mjs',import.meta.url),new URL('dist/image-grid.mjs',import.meta.url));
await copyFile(new URL('node_modules/@capacitor/core/dist/index.js',import.meta.url),new URL('dist/capacitor.mjs',import.meta.url));
await copyFile(new URL('../desktop/assets/iam-messenger-256.png',import.meta.url),new URL('dist/mark.png',import.meta.url));
await copyFile(new URL('../desktop/public/markdown.js',import.meta.url),new URL('dist/markdown.js',import.meta.url));
for(const name of ['message-time.js','read-visible.js'])
  await copyFile(new URL('../desktop/public/'+name,import.meta.url),new URL('dist/'+name,import.meta.url));
await mkdir(new URL('android/app/src/main/res/drawable/',import.meta.url),{recursive:true});
await copyFile(new URL('../desktop/assets/iam-messenger-256.png',import.meta.url),new URL('android/app/src/main/res/drawable/iam_mark.png',import.meta.url));
console.log('Built local-only app assets; no credentials or runtime data included.');
