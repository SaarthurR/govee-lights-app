// Exercise the page's color editing code, with no browser, network or light writes.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(`${__dirname}/../index.html`, 'utf8');
const elements = new Map();
const $ = id => {
  if (!elements.has(id)) elements.set(id, {value: '', style: {}, textContent: '', open: false,
    offsetWidth: 248, offsetHeight: 314, classList: {add() {}, remove() {}},
    getBoundingClientRect: () => ({left: 30, bottom: 200}),
    removeAttribute() {}, setAttribute() {}, addEventListener() {},
    hidePopover() { this.open = false; }, showPopover() { this.open = true; },
    matches() { return this.open; }, querySelectorAll: () => []});
  return elements.get(id);
};
let look, writes = [], pendingWrites = [], slow = false, toasts = [];
const copy = x => JSON.parse(JSON.stringify(x));
const red = [255, 0, 0], green = [0, 255, 0], blue = [0, 0, 255];
const events = new Map(), frames = [];
let sensorOptions;
const context = {$, window: {addEventListener() {}}, picked: 0, shownColors: '', light: 'a', innerWidth: 390, innerHeight: 844,
  document: {activeElement: null}, requestAnimationFrame: callback => frames.push(callback),
  ColorControls: {
    DragDropManager: class {monitor = {addEventListener: (type, callback) => events.set(type, callback)}; dragOperation = {status: {idle: true}};},
    KeyboardSensor: class {}, isSortable: () => false,
    PointerSensor: {configure: options => (sensorOptions = options), defaults: {activationConstraints: () => 'touch defaults'}},
    PointerActivationConstraints: {Distance: class {constructor(options) {Object.assign(this, options);}}},
  },
  cur: () => look, advanced: () => true, same: (a, b) => JSON.stringify(a) === JSON.stringify(b),
  hex: h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16)), syncControls() {},
  toast: message => toasts.push(message),
  set: settings => {
    writes.push(copy(settings));
    const finish = () => { Object.assign(look, copy(settings)); return {ok: true}; };
    return slow ? new Promise(resolve => pendingWrites.push(() => resolve(finish()))) : Promise.resolve(finish());
  },
};
vm.createContext(context);
vm.runInContext(html.slice(html.indexOf('const animColors ='), html.indexOf('// sliders:')), context);
const run = text => vm.runInContext(text, context);
const show = () => { context.cs = copy(look.effect === 'custom' ? run('animColors(cur().animation)') : look.colors); run('shown = {cs, anim: liveAnim()}; picked = 0;'); };
const settle = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  assert.equal(sensorOptions.activationConstraints({pointerType: 'mouse'}, {})[0].value, 5);
  assert.equal(sensorOptions.activationConstraints({pointerType: 'touch'}, {}), 'touch defaults');
  $('custom').onclick();
  assert.equal($('colorPicker').open, true);
  assert.deepEqual($('colorPicker').style, {left: '30px', top: '210px'}); // positioned in the opening click, before paint
  $('colorPicker').hidePopover();
  run('colorDragging = true; colorSorter.dragOperation.status.idle = false');
  const dropped = events.get('dragend')({operation: {source: null}, canceled: true});
  assert.equal(run('colorDragging'), true);
  run('colorSorter.dragOperation.status.idle = true'); frames.shift()(); await dropped;
  assert.equal(run('colorDragging'), false);
  look = {colors: [red, green, blue]}; show(); run('picked = 1; reorderColors(0, 2)');
  assert.deepEqual(look.colors, [green, blue, red]); assert.equal(run('picked'), 0);
  show(); const count = writes.length; look.colors[0] = blue; run('reorderColors(0, 1)');
  assert.equal(writes.length, count); assert.match(toasts.at(-1), /another screen/);

  look = {effect: 'custom', colors: [red, green, blue], animation: {
    frames: [{pattern: [red, green, red, blue], duration: 2, fade: .5}, {pattern: [blue, red], duration: 3, fade: 0}], shift: 1, motion: 'center', block: 2}};
  show(); run('reorderColors(0, 2)');
  assert.deepEqual(look.animation.frames[0].pattern, [green, blue, green, red]);
  assert.deepEqual(look.animation.frames[1].pattern, [red, green]);
  assert.equal(look.animation.frames[0].duration, 2); assert.equal(look.animation.motion, 'center');
  show(); run('picked = 1; removeColor()');
  assert(!look.animation.frames.some(f => f.pattern.some(c => context.same(c, blue))));
  assert(look.animation.frames.every(f => f.pattern.length));

  look = {colors: [red, green, blue]}; show(); run('picked = 1; removeColor()');
  assert.deepEqual(look.colors, [red, blue]);
  look = {colors: [red]}; show(); const last = writes.length; run('removeColor()');
  assert.equal(writes.length, last);

  look = {colors: [red, blue]}; show(); slow = true; writes = [];
  run('queuePickerColor("#112233"); queuePickerColor("#223344"); queuePickerColor("#334455")');
  assert.equal(writes.length, 1); pendingWrites.shift()(); await settle();
  assert.equal(writes.length, 2); pendingWrites.shift()(); await settle();
  assert.deepEqual(look.colors[0], [51, 68, 85]); assert.equal(run('colorWriting'), false);

  // Switching lights cancels a queued edit; the in-flight request keeps its original target.
  show(); writes = [];
  run('queuePickerColor("#445566"); queuePickerColor("#556677"); colorSession++; colorPending = null; light = "b"');
  pendingWrites.shift()(); await settle(); assert.equal(writes.length, 1);

  slow = false; run('light = "a"'); look = {effect: 'custom', colors: [red, blue], animation: {frames: [{pattern: [red, blue, red], duration: 2}]}};
  show(); run('queuePickerColor("#123456")'); await settle();
  run('queuePickerColor("#234567")'); await settle();
  assert.deepEqual(look.animation.frames[0].pattern, [[35, 69, 103], blue, [35, 69, 103]]);
  console.log('Colors: reorder, stale-state rejection, animation preservation, per-color removal, serial live edits and light switching passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
