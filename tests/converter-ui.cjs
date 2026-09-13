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
  if (url === '/api/session') data = {authenticated: true, hosting: 'proxmox'};
  else if (url === '/api/music/lookup') data = {title: 'Song', artist: 'Artist', provider: 'Spotify',
    candidates: [{title: 'Artist - Song', artist: 'Artist', duration: 123, url: 'https://www.youtube.com/watch?v=abcdefghijk'}]};
  else if (url === '/api/jobs' && options.method === 'POST') {
    data = {id: 'fixture', ...JSON.parse(options.body), status: 'ready', title: 'Test',
      filename: 'Test.mka', created_at: Date.now() / 1000};
    queue.push(data);
  } else if (url === '/api/jobs') data = queue;
  return {ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(data))};
};
const tick = () => new Promise(resolve => setTimeout(resolve, 10));
w.eval(readFileSync(path.join(base, 'app.js'), 'utf8'));
(async () => {
  await tick();
  const doc = w.document;
  assert.equal(doc.querySelector('#notice').hidden, true, 'session init/refresh should not throw');
  assert.equal(doc.querySelector('#convert-button').disabled, false);
  assert.equal(doc.querySelector('#quality').value, 'best');
  assert.match(doc.querySelector('#quality').textContent, /2160p/);
  const audio = doc.querySelector('input[value="mp3"]');
  audio.checked = true; audio.dispatchEvent(new w.Event('change'));
  assert.match(doc.querySelector('#quality').textContent, /320 kbps/);
  doc.querySelector('#quality').value = 'original';
  doc.querySelector('#media-url').value = 'https://media.example/test.opus';
  await doc.querySelector('#convert-form').onsubmit({preventDefault() {}});
  const submitted = JSON.parse(calls.find(c => c.options.method === 'POST' && c.url === '/api/jobs').options.body);
  assert.equal(submitted.format, 'mka');
  const download = doc.querySelector('.download-link');
  assert.ok(download.href.endsWith('/api/jobs/fixture/file'));
  assert.equal(download.download, 'Test.mka');
  doc.querySelector('#media-url').value = 'https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC';
  await doc.querySelector('#convert-form').onsubmit({preventDefault() {}});
  assert.match(doc.querySelector('#music-results').textContent, /not files from Spotify/);
  assert.equal(queue.length, 1, 'lookup must not silently download a match');
  doc.querySelector('#music-results button').click();
  assert.equal(doc.querySelector('#media-url').value, 'https://www.youtube.com/watch?v=abcdefghijk');
  w.dispatchEvent(new w.Event('focus'));
  await tick();
  assert.equal(doc.querySelector('#notice').hidden, true, 'focus refresh regression');
  console.log('UI passed: session refresh, quality selection, streaming link, explicit music match choice.');
  w.close();
})().catch(error => { console.error(error); w.close(); process.exitCode = 1; });
