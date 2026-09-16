const {JSDOM} = require('jsdom');
const {readFileSync} = require('node:fs');
const assert = require('node:assert/strict');
const path = require('node:path');

const base = path.join(__dirname, '../app/converter_static');
const dom = new JSDOM(readFileSync(path.join(base, 'admin.html'), 'utf8'),
  {url: 'https://testserver/admin', runScripts: 'outside-only'});
const w = dom.window;
w.alert = () => {};
w.confirm = () => true;
w.setInterval = () => 0;
let processorPriority = 'gpu';
const calls = [];
function payload(url) {
  if (url === '/api/admin/status') return {label:'v6.1.0 · test',uptime_seconds:10,workers:3,active:0,pending:0,
    disk:{total:100,used:10,free:90},memory:{total:4096,available:3072},load:[0,0,0],
    metrics:{completed:0,failed:0,output_bytes:0},scheduler:{heavy_claimed:0,heavy_limit:1,maintenance:false,drain:false},providers:[]};
  if (url === '/api/admin/jobs') return [];
  if (url === '/api/admin/settings') return {values:{workers:3,heavy_workers:1,processor_priority:processorPriority}};
  if (url === '/api/admin/metrics/timeseries') return {buckets:[]};
  if (url === '/api/admin/storage') return {stored_bytes:0,disk:{free:90},files:[]};
  if (url === '/api/admin/capabilities') return {hardware:{available:false,encoders:{}},
    gpu_worker:{configured:true,available:true,compatible:true,worker:'rx6700xt',version:'6.1.0',video_codecs:['h264','hevc']},
    processor_priority:processorPriority,formats:['mp4','mkv']};
  if (url === '/api/admin/tokens' || url === '/api/admin/webhooks' || url.startsWith('/api/admin/audit') || url === '/api/admin/passkeys') return [];
  if (url === '/api/admin/security') return {passkeys:0,session_ttl:43200,trusted_ips_configured:false,rate_limit:{blocked_sources:0}};
  if (url === '/api/admin/deployment') return {current:{label:'v6.1.0 · test'},rollback_target:null,releases:[]};
  return {};
}

w.fetch = async (url, options = {}) => {
  calls.push({url, options});
  if (url === '/api/admin/settings' && options.method === 'PATCH') {
    const body = JSON.parse(options.body); processorPriority = body.values.processor_priority;
    return {ok:true,status:200,json:async()=>({values:{workers:3,heavy_workers:1,processor_priority:processorPriority},changed:body.values})};
  }
  return {ok:true,status:200,json:async()=>payload(url)};
};
const tick = (ms=30) => new Promise(resolve => setTimeout(resolve, ms));
const sources = ['admin.js','admin-v6.js'].map(name => readFileSync(path.join(base,name),'utf8'));
w.eval(sources.join('\n'));

(async () => {
  await tick(400);
  const doc = w.document;
  const select = doc.querySelector('[data-setting="processor_priority"]');
  assert.ok(select, 'processor priority selector should render');
  assert.equal(select.tagName, 'SELECT');
  assert.equal(select.value, 'gpu');
  assert.match(select.parentElement.textContent, /GPU preferred/);
  assert.match(select.parentElement.textContent, /CPU preferred/);
  select.value = 'cpu';
  doc.querySelector('#save-settings').click();
  await tick(100);
  const patch = calls.find(call => call.url === '/api/admin/settings' && call.options.method === 'PATCH');
  assert.ok(patch, 'saving processor priority should PATCH admin settings');
  assert.equal(JSON.parse(patch.options.body).values.processor_priority, 'cpu');
  assert.equal(processorPriority, 'cpu');
  assert.match(doc.querySelector('#capabilities-list').textContent, /CPU preferred/);
  console.log('Admin UI passed: processor priority renders, saves, and updates operator status.');
  w.close();
})().catch(error => { console.error(error); w.close(); process.exitCode = 1; });
