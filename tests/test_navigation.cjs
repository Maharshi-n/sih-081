// Run with: node tests/test_navigation.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const ids = ['overview', 'contributions', 'verification', 'provenance'];
const tops = {overview: 0, contributions: 600, verification: 1200, provenance: 1800};
const sections = Object.fromEntries(ids.map(id => [id, {
  id, getBoundingClientRect: () => ({top: tops[id]}),
  scrollIntoView() {},
}]));
const links = ids.map(id => ({
  hash: '#' + id, active: false, current: null,
  classList: {toggle(name, active) { links.find(a => a.hash === '#' + id).active = active; }},
  setAttribute(name, value) { this.current = value; },
  removeAttribute() { this.current = null; },
  addEventListener(name, callback) { this.click = callback; },
}));
const events = {}, frames = [];
const window = {innerHeight: 800, scrollY: 0,
  addEventListener(name, callback) { events[name] = callback; }};
const location = {hash: ''};
const document = {
  documentElement: {scrollHeight: 2200},
  querySelectorAll: () => links,
  querySelector: hash => sections[hash.slice(1)],
  getElementById: id => sections[id],
};
const html = fs.readFileSync(path.join(__dirname, '../web/index.html'), 'utf8');
const navigation = html.slice(html.indexOf('const navLinks='), html.indexOf('function fillSelect'));
vm.runInNewContext(navigation, {window, document, location,
  history: {pushState(a, b, hash) { location.hash = hash; }},
  matchMedia: () => ({matches: true}),
  requestAnimationFrame: callback => frames.push(callback),
});
const flush = () => { while (frames.length) frames.shift()(); };
const active = () => links.filter(a => a.active).map(a => a.hash);
flush();
assert.deepEqual(active(), ['#overview']);
for (const link of links) {
  link.click({preventDefault() {}});
  assert.deepEqual(active(), [link.hash]);
  assert.equal(link.current, 'location');
  events.scroll(); flush();
  assert.deepEqual(active(), [link.hash], 'Highlight survives scrolling toward the destination');
}
events.wheel();
tops.overview = -620; tops.contributions = 26;
events.scroll(); flush();
assert.deepEqual(active(), ['#contributions'], 'Manual scrolling updates the highlight');
window.scrollY = 1400;
events.scroll(); flush();
assert.deepEqual(active(), ['#provenance'], 'Page bottom selects data provenance');
location.hash = '#overview';
events.popstate();
assert.deepEqual(active(), ['#overview'], 'History navigation restores the highlight');
console.log('Navigation checks passed');
