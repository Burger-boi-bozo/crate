// Run with npm run test:ui.
const {JSDOM} = require('jsdom');
const {readFileSync} = require('node:fs');
const assert = require('node:assert/strict');
const path = require('node:path');
const base = path.join(__dirname, '../app/converter_static');
const dom = new JSDOM(readFileSync(path.join(base, 'index.html'), 'utf8'),
  {url: 'https://testserver/', runScripts: 'outside-only'});
const w = dom.window;
const calls = [];
const queue = [];
w.fetch = async (url, options = {}) => {
  calls.push({url, options});
  let data = {};
  if (url === '/api/session') data = {authenticated: true, hosting: 'proxmox', workers: 2};
  else if (url === '/api/version') data = {version: '3.0.0', build: 'abcdef123456', label: 'v3.0.0 · abcdef123456'};
  else if (url === '/api/status') data = {status: 'ok', workers: 2, active: 1, disk_free: 10737418240, disk_total: 21474836480, label: 'v3.0.0 · abcdef123456'};
  else if (url === '/api/music/lookup') data = {title: 'Song', artist: 'Artist', provider: 'Spotify',
    candidates: [{title: 'Artist - Song', artist: 'Artist', duration: 123, url: 'https://www.youtube.com/watch?v=abcdefghijk'}]};
  else if (/\/pause$/.test(url)) { queue[0].status = 'paused'; queue[0].stage = 'paused'; data = queue[0]; }
  else if (/\/resume$/.test(url)) { queue[0].status = 'downloading'; queue[0].stage = 'downloading'; data = queue[0]; }
  else if (/\/retry$/.test(url)) { data = {...queue[0], id: 'retry', status: 'queued', queue_position: 1}; queue.push(data); }
  else if (url === '/api/jobs' && options.method === 'POST') {
    data = {id: 'fixture', ...JSON.parse(options.body), status: 'downloading', stage: 'downloading',
      title: 'Test', created_at: Date.now() / 1000, progress: 42, downloaded_bytes: 5242880,
      total_bytes: 10485760, speed: 1048576, eta: 5, queue_position: null};
    queue.push(data);
  } else if (url === '/api/jobs') data = queue;
  return {ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(data))};
};
const tick = () => new Promise(resolve => setTimeout(resolve, 15));
w.eval(readFileSync(path.join(base, 'app.js'), 'utf8'));
w.eval(readFileSync(path.join(base, 'enhancements.js'), 'utf8'));
w.eval(readFileSync(path.join(base, 'version.js'), 'utf8'));
w.eval(readFileSync(path.join(base, 'status.js'), 'utf8'));
(async () => {
  await tick(); await tick();
  const doc = w.document;
  assert.equal(doc.querySelector('#notice').hidden, true);
  assert.equal(doc.querySelector('#convert-button').disabled, false);
  assert.equal(doc.querySelector('#quality').value, 'best');
  assert.equal(doc.querySelector('#status-state').textContent, 'Online');
  assert.match(doc.querySelector('#status-build').textContent, /abcdef123456/);
  const audio = doc.querySelector('input[value="mp3"]');
  audio.checked = true; audio.dispatchEvent(new w.Event('change'));
  doc.querySelector('#quality').value = 'original';
  doc.querySelector('#media-url').value = 'https://media.example/test.opus';
  await doc.querySelector('#convert-form').onsubmit({preventDefault() {}});
  assert.match(doc.querySelector('.job-status').textContent, /5.0 MB \/ 10.0 MB/);
  assert.match(doc.querySelector('.job-status').textContent, /1.0 MB\/s/);
  const pause = [...doc.querySelectorAll('.job-actions button')].find(x => x.textContent === 'Pause');
  assert.ok(pause); pause.click(); await tick();
  assert.equal(queue[0].status, 'paused');
  doc.querySelector('#media-url').value = 'https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC';
  await doc.querySelector('#convert-form').onsubmit({preventDefault() {}});
  assert.match(doc.querySelector('#music-results').textContent, /not files from Spotify/);
  assert.equal(doc.querySelector('#build-version').textContent.trim(), 'v3.0.0 · abcdef123456');
  console.log('UI passed: progress, controls, status, version, quality, and explicit music lookup.');
  w.close();
})().catch(error => { console.error(error); w.close(); process.exitCode = 1; });
