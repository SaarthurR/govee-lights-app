// Sliders auto-save to the selected theme; other look edits can still be saved explicitly.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(`${__dirname}/../index.html`, 'utf8');
const elements = new Map();
const $ = id => { if (!elements.has(id)) elements.set(id, {}); return elements.get(id); };
const copy = x => JSON.parse(JSON.stringify(x));
const theme = {effect:'wave', colors:[[1,2,3]], brightness:80, speed:1, amount:.3};
const state = {lights:{a:copy(theme), b:copy(theme)}};
let posts = [];
const context = {$, state, light:'all', ids:()=>Object.keys(state.lights), lead:()=>context.light === 'all' ? 'a' : context.light,
  cur:()=>state.lights[context.light === 'all' ? 'a' : context.light], where:()=>context.light,
  same:(a,b)=>JSON.stringify(a) === JSON.stringify(b),
  set:async settings => { for (const key of context.light === 'all' ? Object.keys(state.lights) : [context.light]) Object.assign(state.lights[key], copy(settings)); return {ok:true}; },
  post:async (path,body,message) => {
    posts.push({path,body,message});
    if (path === '/api/set') {
      await context.set(body.settings);
      context.updatedTheme = {...copy(run('savedThemes')[body.theme] || {}), ...body.settings};
      return {ok:true, ...(body.theme ? {theme:body.theme,saved:context.updatedTheme} : {})};
    }
    context.updatedTheme = copy(context.cur()); delete context.updatedTheme.on; return {ok:true};
  },
  loadThemes:async () => vm.runInContext('savedThemes.Ocean = updatedTheme', context),
};
vm.createContext(context);
vm.runInContext(html.slice(html.indexOf('let themesKey ='),html.indexOf('async function loadThemes()')),context);
const run = code => vm.runInContext(code, context);
context.theme = copy(theme); run('savedThemes.Ocean = theme');
(async () => {
  run('themeSaveUI()'); assert.equal(run('selectedThemes.a'),'Ocean'); // recover a uniquely matching theme after reload
  await run('playLook("Ocean", theme, true)');
  assert.equal($('themeSave').hidden,false); assert.equal($('themeSaveBtn').hidden,true);
  for(const s of Object.values(state.lights)) Object.assign(s,{speed:.4,amount:.7,brightness:42});
  run('themeSaveUI()'); assert.equal($('themeSaveBtn').hidden,false); assert.equal(posts.length,0);
  await run('saveThemeChanges()');
  assert.deepEqual(copy(posts),[{path:'/api/theme',body:{name:'Ocean',replace:true},message:'Saved changes to “Ocean”'}]);
  assert.equal(run('savedThemes.Ocean.speed'),.4); assert.equal(run('savedThemes.Ocean.brightness'),42);
  assert.equal($('themeSaveBtn').hidden,true); assert.equal($('themeSaveHint').textContent,'Saved to “Ocean”');
  posts = [];
  await run('adjustSetting({speed:.6}, "speed")');
  assert.deepEqual(copy(posts[0]),{path:'/api/set',body:{settings:{speed:.6},theme:'Ocean'},message:'Saved speed to “Ocean”'});
  assert.equal(run('savedThemes.Ocean.speed'),.6);
  assert.equal($('themeSaveBtn').hidden,true);
  await run('playLook("Ocean", theme, true)'); // an old tile callback must use the newly saved speed
  assert.equal(state.lights.a.speed,.6);
  state.lights.b.speed = 2; run('themeSaveUI()');
  assert.equal($('themeSaveBtn').hidden,true); assert.match($('themeSaveHint').textContent,/Pick one light/);
  await run('playLook("Preset", {effect:"rainbow"})'); assert.equal($('themeSave').hidden,true);
  posts = []; await run('adjustSetting({speed:2}, "speed")');
  assert.equal(posts[0].body.theme,undefined); assert.equal(posts[0].message,'Speed updated');
  const background = [{inert:false}, {inert:false}], styles = new Map();
  context.window = {visualViewport:{height:400,offsetTop:10}};
  context.innerHeight = 800; context.compactControls = {matches:true};
  context.document = {querySelectorAll:()=>background};
  $('askCard').style = {setProperty:(name,value)=>styles.set(name,value)};
  $('askCard').classList = {toggle:(_,value)=>$('askCard').open=value};
  $('askText').blur = () => $('askText').blurred=true;
  vm.runInContext(html.slice(html.indexOf('function chatViewport()'),html.indexOf("$('askText').addEventListener('focus'")),context);
  run('askOpen(true)');
  assert.equal(styles.get('--chat-height'),'400px'); assert.equal(styles.get('--chat-top'),'10px');
  assert(background.every(el=>el.inert));
  context.window.visualViewport.height=700; run('chatViewport()'); assert.equal(styles.get('--chat-height'),'700px');
  run('askOpen(false)'); assert(background.every(el=>!el.inert)); assert.equal($('askText').blurred,true);
  console.log('Themes: explicit update, saved confirmation, brightness/speed persistence data, mixed-light guard and preset isolation passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
