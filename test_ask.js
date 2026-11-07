// JS selection/refresh checks using the page's actual functions; no browser needed.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(`${__dirname}/index.html`, 'utf8');
const extract = (start, end) => html.slice(html.indexOf(start), html.indexOf(end, html.indexOf(start)));
const context = {
  light: '', state: {lights: {}}, localStorage: {light: 'forgotten'},
  ids() { return Object.keys(context.state.lights); },
  pickTab(k) { context.light = k; },
};
vm.createContext(context);
vm.runInContext(extract('function fixLight()', 'async function toggleAccount()'), context);
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
  const element = () => ({hidden: false, style: {}, classList: {toggle() {}, add() {}, remove() {}},
    replaceChildren() {}, setAttribute() {}, querySelectorAll: () => [], parentElement: {}});
  Object.assign(context, {
    $: id => elements[id] ||= element(),
    document: {querySelectorAll: () => [], createElement: element},
    setupOpen: true, wsteps: ['hello'], wat: 0, welcomeDone: false,
    scrollTo() {}, drawTabs() {}, syncControls() {}, draw() {}, feedbackURL: () => '',
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
  context.fetch = async () => ({json: async () => ({lights: {}, claude: true, ask: true})});
  vm.runInContext(extract('async function refresh()', 'async function poll()'), context);
  await context.refresh(); await context.refresh();
  assert.equal(welcomes, 0);
  assert.equal(elements.askCard.hidden, false);

  // Sending immediately after setup waits for refresh before choosing a light.
  context.state = {lights: {}}; context.light = '';
  let selected;
  context.refresh = async () => { context.state.lights.a = {name: 'First'}; };
  Object.assign(context, {bubble: element, expireIn() {}, loadThemes() {}, loadSchedule() {},
    post: async () => { selected = context.light; return {say: 'Done'}; }});
  vm.runInContext(extract('async function ask(text)', "fetch('/api/chat')"), context);
  await context.ask('question');
  assert.equal(selected, 'a');
  console.log('Ask selection: empty/all/stale/renamed/forgotten/welcome and awaited setup refresh passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
