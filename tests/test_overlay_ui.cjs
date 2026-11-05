/* No browser or npm required: node tests/test_overlay_ui.cjs
 * Checks renderer scheduling/draw-call bounds with a minimal DOM/Canvas stub.
 * Pixel appearance remains a real-WebKit/manual integration check.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function page() {
  let now = 0, nextId = 0;
  const raf = new Map();
  const stats = { strokes: 0, curves: 0, clears: 0, classChanges: 0 };
  const element = () => ({
    style: { setProperty() {} }, dataset: {}, children: [],
    appendChild(child) { this.children.push(child); },
    addEventListener() {},
    classList: { add() { stats.classChanges++; }, remove() { stats.classChanges++; } },
    set innerHTML(value) { this.children = []; },
  });
  const ctx = {
    setTransform() {}, beginPath() {}, moveTo() {}, fill() {}, arc() {},
    clearRect() { stats.clears++; },
    quadraticCurveTo() { stats.curves++; },
    stroke() { stats.strokes++; },
    createRadialGradient() { return { addColorStop() {} }; },
  };
  const nodes = Object.fromEntries(['stage', 'keys', 'comet', 'toolbar'].map(id => [id, element()]));
  nodes.comet.getContext = () => ctx;
  const listeners = {};
  const window = { innerWidth: 1920, innerHeight: 1080, devicePixelRatio: 1,
    addEventListener(type, callback) { listeners[type] = callback; } };
  const document = { body: { dataset: {} }, getElementById: id => nodes[id],
    createElement: element, querySelectorAll: () => [], addEventListener() {} };
  const context = vm.createContext({ window, document, console,
    performance: { now: () => now },
    requestAnimationFrame(callback) { const id = ++nextId; raf.set(id, callback); return id; },
    cancelAnimationFrame(id) { raf.delete(id); },
    setTimeout() {}, URLSearchParams, location: { search: '' },
  });
  const ui = path.join(__dirname, '../overlay-app/ui');
  vm.runInContext(fs.readFileSync(path.join(ui, 'demo/keys.js'), 'utf8'), context);
  vm.runInContext(fs.readFileSync(path.join(ui, 'overlay.js'), 'utf8'), context);
  // Resize can happen before host init; must not dereference a missing stage.
  listeners.resize();
  const api = window.swipetype;
  api.init({ keys: window.SWIPETYPE_DEMO_KEYS,
             stage: { xmin: -.75, xmax: 10, ymin: -.75, ymax: 2.75 }, layout: null });
  api.setMode('swipe');
  return { api, stats, raf, nodes,
    point(x, y, t) { now = t; api.events([{ event: 'point', x, y }]); },
    frame(t) {
      now = t;
      const work = [...raf.values()]; raf.clear();
      for (const callback of work) callback(t);
    },
  };
}

{
  const p = page();
  assert.equal(p.nodes.keys.children.length, 27); // 26 keys + cached glow
  for (let i = 0; i < 50; i++) p.point(2 + i / 10, .5, i * 8);
  assert.equal(p.raf.size, 1, 'point bursts schedule only one animation callback');
  p.frame(400);
  assert(p.stats.strokes > 0);
  assert(p.stats.strokes <= 3 * 8, 'three passes, at most eight age buckets each');
  assert(p.stats.curves > p.stats.strokes, 'curves are batched, not dropped');
  assert.equal(p.stats.classChanges, 0, 'nearest-key glow must not repaint static keys');
  const glow = p.nodes.keys.children.at(-1);
  assert.equal(glow.className, 'gk-active-glow');
  assert.equal(glow.style.opacity, '1');
  assert(glow.style.transform.includes('translate3d'));
  p.frame(900);
  assert.equal(p.raf.size, 0, 'expired trail stops requesting frames');
  assert.equal(glow.style.opacity, '0');
}

for (const mode of ['idle', 'layout']) {
  const p = page();
  p.point(3, 1, 0); p.point(4, 1, 8);
  assert.equal(p.raf.size, 1);
  p.api.setMode(mode);
  assert.equal(p.raf.size, 0, `${mode} cancels pending animation`);
  const before = p.stats.strokes;
  p.frame(16);
  assert.equal(p.stats.strokes, before);
}

{
  const p = page();
  p.point(3, 1, 0); p.point(4, 1, 8);
  p.api.events([{ event: 'clear' }]);
  assert.equal(p.raf.size, 0, 'clear cancels animation immediately');
  p.point(5, 1, 16); p.point(6, 1, 24);
  p.frame(32);
  assert(p.stats.strokes > 0, 'new gesture still renders after clear');
}
console.log('PASS: renderer batching, cached glow, expiry, clear, idle/layout, pre-init resize');
