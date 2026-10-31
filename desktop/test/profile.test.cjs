const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
test('profile details use an inert accessible dialog, not the conversation strip', () => {
  const js = fs.readFileSync(path.join(__dirname, '../public/app.js'), 'utf8');
  const css = fs.readFileSync(path.join(__dirname, '../public/style.css'), 'utf8');
  assert.ok(js.includes('[buddy.profile.pronouns, buddy.profile.status]'));
  assert.ok(js.includes('body.textContent = value'));
  assert.ok(js.includes("profileDialog.setAttribute('aria-labelledby', 'profileTitle')"));
  assert.ok(js.includes('trigger.onkeydown'));
  assert.ok(css.includes('.profile-dialog{max-height:80vh;overflow:auto}'));
  assert.ok(css.includes('#profile{display:block;flex-shrink:0;'));
});
