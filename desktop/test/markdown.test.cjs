const test = require('node:test');
const assert = require('node:assert/strict');
const {render} = require('../public/markdown.js');
class Node {
  constructor(tag, text = '') {this.tag = tag; this.children = []; this.text = text;}
  append(...nodes) {this.children.push(...nodes);}
  replaceChildren() {this.children = []; this.text = '';}
  set textContent(value) {this.text = value; this.children = [];}
  get textContent() {return this.text + this.children.map(n => n.textContent).join('');}
}
const doc = {createElement: tag => new Node(tag), createTextNode: text => new Node('#text', text)};
function parse(text) {const root = new Node('div'); render(root, text, doc); return root;}
function tags(root) {return [root.tag, ...root.children.flatMap(tags)];}

test('underscores inside filenames and identifiers stay literal', () => {
  for (const text of ['MESSENGER_ANDROID_PLAN.md', 'path/to/my_file_name.txt', 'foo__bar__baz', '日本_語_名']) {
    const root = parse(text); assert.equal(root.textContent, text);
    assert.ok(!tags(root).includes('em')); assert.ok(!tags(root).includes('strong'));
  }
  const root = parse('**MESSENGER_ANDROID_PLAN.md** and _italic_ and __bold__');
  assert.equal(root.textContent, 'MESSENGER_ANDROID_PLAN.md and italic and bold');
  assert.equal(tags(root).filter(t => t === 'em').length, 1);
});
test('bold, italics, inline code and paragraphs use DOM elements', () => {
  const root = parse('Hello **Lily** and *Rowan* with `code`.\nNext line.\n\nAnother paragraph.');
  assert.equal(root.children.length, 2);
  assert.ok(tags(root).includes('strong')); assert.ok(tags(root).includes('em')); assert.ok(tags(root).includes('code'));
  assert.match(root.textContent, /Next line/);
});
test('bullet and numbered lists preserve starting number', () => {
  const root = parse('- First\n- **Second**\n\n3. Third\n4. Fourth');
  assert.equal(root.children[0].tag, 'ul'); assert.equal(root.children[0].children.length, 2);
  assert.equal(root.children[1].tag, 'ol'); assert.equal(root.children[1].start, 3);
});
test('fenced code stays literal including HTML and formatting', () => {
  const root = parse('```html\n<img src=x onerror=alert(1)>\n**literal**\n```');
  assert.deepEqual(tags(root), ['div','pre','code']);
  assert.match(root.textContent, /\*\*literal\*\*/);
});
test('HTML, links, image embeds and javascript URLs never become active elements', () => {
  const text = '<script>alert(1)</script> ![tracker](https://evil.test/x) [click](javascript:alert(1))';
  const root = parse(text);
  assert.equal(root.textContent, text);
  assert.deepEqual(tags(root), ['div','p','#text']);
});
test('unclosed fence renders as inert code and rerender replaces content', () => {
  const root = parse('~~~\n<script>');
  assert.equal(root.children[0].tag, 'pre');
  render(root, 'new', doc); assert.equal(root.textContent, 'new'); assert.equal(root.children.length, 1);
});

test('same-line fenced review stays visible, with surrounding paragraphs intact', () => {
  const review = '“A tense opening—and already too many theories.” — Sophie Vale';
  const root = parse('Approved review:\n```' + review + '```\nAfterward.');
  assert.equal(root.children[1].tag, 'pre');
  assert.equal(root.children[1].textContent, review);
  assert.equal(root.children[2].textContent, 'Afterward.');
});

test('prose opening line is not discarded as a language hint', () => {
  const root = parse('```A review with **literal** formatting\nsecond line\n```');
  assert.equal(root.textContent, 'A review with **literal** formatting\nsecond line');
  assert.deepEqual(tags(root), ['div','pre','code']);
  assert.equal(parse('```hello').textContent, 'hello');
});

test('all three review layouts preserve the review text', () => {
  const review = '“Some thoughts.” — Sophie Vale';
  assert.equal(parse('```'+review+'```').textContent, review);
  // A closing fence attached to prose is literal, not silently dropped.
  assert.equal(parse('```\n'+review+'```').textContent, review+'```');
  assert.equal(parse('```\n'+review+'\n```').textContent, review);
});

test('language hints, long fences and inert content remain safe', () => {
  for (const language of ['js','c++','c#','objective-c','text'])
    assert.equal(parse('```'+language+'\nexample\n```').textContent, 'example');
  assert.equal(parse('````a ``` b````').textContent, 'a ``` b');
  assert.equal(parse('~~~literal~~~').textContent, 'literal');
  const root=parse('```<img src=x onerror=alert(1)> **literal**```');
  assert.deepEqual(tags(root), ['div','pre','code']);
  assert.equal(root.textContent, '<img src=x onerror=alert(1)> **literal**');
});
