// JS selection/refresh checks using the page's actual functions; no browser needed.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(`${__dirname}/../index.html`, 'utf8');
const extract = (start, end) => html.slice(html.indexOf(start), html.indexOf(end, html.indexOf(start)));
const context = {
  light: '', state: {lights: {}}, localStorage: {light: 'forgotten'},
  ids() { return Object.keys(context.state.lights); },
  pickTab(k) { context.light = k; },
};
vm.createContext(context);
vm.runInContext(extract('function fixLight()', 'async function loadGovee()'), context);
context.fixLight(); assert.equal(context.light, '');
context.state.lights.a = {name: 'First'};
context.fixLight(); assert.equal(context.light, 'a'); // first run after welcome
context.state.lights.a.name = 'Renamed';
context.fixLight(); assert.equal(context.light, 'a');
context.state.lights.b = {name: 'Second'};
context.light = 'all'; context.fixLight(); assert.equal(context.light, 'all');
context.light = 'forgotten'; context.fixLight(); assert.equal(context.light, 'a');
delete context.state.lights.a; context.fixLight(); assert.equal(context.light, 'b');
delete context.state.lights.b; context.fixLight(); assert.equal(context.light, '');

(async () => {
  // A setup POST must not resolve until the fresh selection is available.
  let release;
  Object.assign(context, {
    fetch: async () => ({json: async () => ({ok: true})}),
    refresh: () => new Promise(resolve => { release = resolve; }),
  });
  vm.runInContext(extract('async function post(', 'const set ='), context);
  let done = false;
  const posting = context.post('/api/lights', {}, null).then(() => { done = true; });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(done, false);
  release(); await posting; assert.equal(done, true);

  const elements = {};
  const element = () => ({hidden: false, style: {}, classList: {toggle() {}, add() {}, remove() {}}, close() {},
    replaceChildren() {}, setAttribute() {}, querySelectorAll: () => [], parentElement: {}});
  Object.assign(context, {
    $: id => elements[id] ||= element(),
    document: {querySelectorAll: () => [], createElement: element},
    setupOpen: true, wsteps: ['hello'], wat: 0, welcomeDone: false,
    scrollTo() {}, drawTabs() {}, syncControls() {}, draw() {}, feedbackURL: () => '', needLan() {},
  });
  vm.runInContext(extract('function showSetup()', 'async function useAsk('), context);
  vm.runInContext(extract('function closeSetup()', 'function step('), context);
  context.showSetup();
  assert.equal(elements.setupDone.hidden, false);
  assert.equal(elements.setupDone.textContent, 'Use without lights');
  context.closeSetup();
  assert.equal(elements.app.hidden, false);
  let welcomes = 0;
  context.openWelcome = () => { welcomes++; };
  context.fetch = async () => ({json: async () => ({lights: {}, claude: true, ask: true, version: "test"})});
  vm.runInContext(extract('let disconnected =', 'async function poll()'), context);
  await context.refresh(); await context.refresh();
  assert.equal(welcomes, 0);
  assert.equal(elements.askCard.hidden, false);
  assert.equal(elements.noLights.hidden, false); // skipped setup still offers Find lights
  let reloaded = 0, cleared = 0;
  context.location = {reload() { reloaded++; }};
  context.alertUI = message => { if (!message) cleared++; };
  context.fetch = async () => { throw Error('restarting'); };
  await context.refresh();
  context.fetch = async () => ({json: async () => ({lights: {}, update: {version: 'old', enabled: false}})});
  await context.refresh();
  assert.equal(cleared, 1);
  assert.equal(elements.autoUpdate.checked, false);
  context.fetch = async () => ({json: async () => ({lights: {}, update: {version: 'new', enabled: true}})});
  await context.refresh();
  assert.equal(reloaded, 1); // Fetch the new page after the updated server starts.


  // Sending immediately after setup waits for refresh before choosing a light.
  context.state = {lights: {}}; context.light = '';
  let selected;
  context.refresh = async () => { context.state.lights.a = {name: 'First'}; };
  Object.assign(context, {asking: false, bubble: element, expireIn() {}, loadThemes() {}, loadSchedule() {},
    post: async () => { selected = context.light; return {say: 'Done'}; }});
  vm.runInContext(extract('async function ask(text)', "fetch('/api/chat')"), context);
  await context.ask('question');
  assert.equal(selected, 'a');

  // macOS Local Network step: stays while not allowed (each poll of /api/state), goes on by itself once it is.
  let now = 0, steps = 0, closed = 0, posted = [];
  const lan = l => { context.state = {lights: {}, lan: l}; context.needLan(); };
  for (const k of Object.keys(elements)) delete elements[k];
  Object.assign(context, {Date: {now: () => now}, hideQR() {}, showSetup() {}, wsteps: ['hello', 'network', 'find', 'done'], wat: 1, setupOpen: true,
    step(d) { steps++; context.wat += d; }, closeSetup() { closed++; context.wsteps = null; context.setupOpen = false; },
    post: async path => { posted.push(path); }});
  vm.runInContext(extract('let netAt', 'function closeSetup()'), context);
  lan({asked: false, allowed: 'unknown', fallback: false});
  assert.equal(elements.netGo.hidden, false); assert.equal(elements.netTitle.textContent, 'Local network access');
  await context.allowLan(); assert.deepEqual(posted, ['/api/network']);
  lan({asked: true, allowed: false, fallback: false});  // the macOS alert is up
  assert.equal(elements.netGo.hidden, true); assert.equal(elements.netWait.hidden, false); assert.equal(elements.netOff.hidden, true);
  now = 16000; lan({asked: true, allowed: false, fallback: false});  // clicked Don't Allow
  assert.equal(elements.netTitle.textContent, 'Access is off'); assert.equal(elements.netOff.hidden, false); assert.equal(context.wat, 1);
  lan({asked: true, allowed: false, fallback: true});
  assert.equal(elements.netFix.hidden, false); assert.equal(elements.netOff.hidden, true); assert.equal(steps, 0);
  lan({asked: true, allowed: true, fallback: false});
  assert.equal(steps, 1); assert.equal(context.wat, 2);  // on to Find lights
  context.wsteps = null; context.wat = 0; context.setupOpen = false;
  lan({asked: true, allowed: false, fallback: false});  // turned off later: blocker over every page
  assert.deepEqual([...context.wsteps], ['network']); assert.equal(context.setupOpen, true);
  lan({asked: true, allowed: false, fallback: false}); assert.equal(closed, 0);
  lan({asked: true, allowed: true, fallback: false}); assert.equal(closed, 1);
  lan({asked: true, allowed: true, fallback: false}); assert.equal(context.wsteps, null);  // not macOS, or allowed: never shown
  console.log('Ask selection: empty/all/stale/renamed/forgotten/welcome and awaited setup refresh passed.');
  console.log('Local Network step: waits, shows the off and install help, advances and unblocks once allowed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
