/* No third-party scripts. Media titles and errors are inserted as text only. */
const $ = selector => document.querySelector(selector);
const HISTORY_KEY = 'crate-converter-history-v1';
const activeStates = new Set(['queued', 'downloading', 'converting']);
let sessionReady = false;
let filter = 'all';
let jobs = [];
let timer;
let syncing = false;
let history = [];
try { history = JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]'); } catch { /* storage may be disabled */ }
if (!Array.isArray(history)) history = [];
history = history.filter(x => x && typeof x.id === 'string' && typeof x.url === 'string');

function saveHistory() {
  try { localStorage.setItem(HISTORY_KEY, JSON.stringify(history)); } catch { /* private browsing */ }
}

function showError(message) {
  $('#form-error').textContent = message || '';
  $('#form-error').hidden = !message;
}

async function api(path, options = {}, retry = true) {
  const response = await fetch(path, {...options, headers: {'X-Crate-Request': '1', ...(options.body ? {'Content-Type': 'application/json'} : {}), ...options.headers}});
  if (response.status === 204) return null;
  const data = await response.json().catch(() => ({detail: 'The server could not be reached. Please try again.'}));
  if (!response.ok) {
    if (response.status === 401 && path !== '/api/session' && retry) {
      await api('/api/session');
      return api(path, options, false);
    }
    const detail = Array.isArray(data.detail) ? data.detail[0]?.msg?.replace(/^Value error, /, '') : data.detail;
    throw new Error(detail || 'Something went wrong. Please try again.');
  }
  return data;
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function allJobs() {
  const current = new Set(jobs.map(x => x.id));
  return [...jobs, ...history.filter(x => !current.has(x.id)).map(x => ({...x, status: 'expired'}))]
    .sort((a, b) => b.created_at - a.created_at);
}

function renderJobs() {
  const all = allJobs();
  $('#job-count').textContent = all.length;
  $('#clear-history').hidden = !all.some(x => !activeStates.has(x.status));
  const shown = all.filter(x => filter === 'all' || (filter === 'mp3' ? ['mp3', 'mka'].includes(x.format) : ['mp4', 'mkv'].includes(x.format)));
  $('#empty-state').hidden = shown.length > 0;
  const fragment = document.createDocumentFragment();
  for (const job of shown) {
    const row = element('article', 'job');
    row.append(element('div', 'job-icon', ['mp3', 'mka'].includes(job.format) ? '♫' : '▷'));
    const content = element('div', 'job-content');
    content.append(element('h3', 'job-title', job.title || 'Media clip'));
    let source = '';
    try { source = new URL(job.url).hostname.replace(/^www\./, ''); } catch { /* invalid saved history */ }
    const size = job.size ? ` · ${(job.size / 1048576).toFixed(1)} MB` : '';
    const resolution = job.width && job.height ? ` · ${job.width} × ${job.height}` : '';
    content.append(element('p', 'job-meta', `${(job.format || '').toUpperCase()} · ${source}${resolution}${size}`));
    const labels = {queued: 'Waiting in the queue…', downloading: 'Downloading the source…', converting: 'Preparing your file…', ready: 'Ready to save', cancelled: 'Cancelled', failed: job.error || 'Conversion failed', expired: 'File expired · convert again to download'};
    content.append(element('p', `job-status${job.status === 'failed' ? ' job-error' : ''}`, labels[job.status] || job.status));
    if (activeStates.has(job.status)) {
      const progress = element('progress');
      progress.max = 100;
      progress.setAttribute('aria-label', 'Conversion progress');
      if (job.progress > 0) progress.value = job.progress;
      content.append(progress);
    }
    const actions = element('div', 'job-actions');
    if (job.status === 'ready') {
      const link = element('a', 'download-link', 'Download ↓');
      link.href = `/api/jobs/${encodeURIComponent(job.id)}/file`;
      link.download = job.filename || `download.${job.format}`;
      // A native download streams to disk instead of buffering the entire file.
      link.onclick = () => showError('');
      actions.append(link);
    }
    if (activeStates.has(job.status)) {
      const cancel = element('button', 'text-button', 'Cancel');
      cancel.onclick = async () => {
        cancel.disabled = true;
        try { await api(`/api/jobs/${encodeURIComponent(job.id)}/cancel`, {method: 'POST'}); await sync(); }
        catch (error) { showError(error.message); cancel.disabled = false; }
      };
      actions.append(cancel);
    } else {
      const again = element('button', 'text-button', 'Convert again');
      again.onclick = () => {
        $('#media-url').value = job.url;
        const radio = $(`input[name="format"][value="${['mp3', 'mka'].includes(job.format) ? 'mp3' : 'mp4'}"]`);
        radio.checked = true;
        updateQuality();
        $('#quality').value = ['mkv', 'mka'].includes(job.format) ? 'original' : (job.quality || 'best');
        $('#media-url').focus();
        $('#convert-form').scrollIntoView({behavior: 'smooth', block: 'center'});
      };
      actions.append(again);
    }
    row.append(content, actions);
    fragment.append(row);
  }
  // Preserve focus across polls to make cancel/download controls keyboard-usable.
  const focused = document.activeElement;
  if (!$('#job-list').contains(focused)) $('#job-list').replaceChildren(fragment);
}

async function sync() {
  if (!sessionReady || syncing) return;
  syncing = true;
  try {
    jobs = await api('/api/jobs');
    const combined = new Map(history.map(x => [x.id, x]));
    for (const job of jobs) combined.set(job.id, {id: job.id, url: job.url, title: job.title, format: job.format, quality: job.quality, size: job.size, created_at: job.created_at});
    history = [...combined.values()].sort((a, b) => b.created_at - a.created_at);
    saveHistory();
    renderJobs();
    $('#notice').hidden = true;
  } catch (error) {
    $('#notice').textContent = error.message;
    $('#notice').hidden = false;
  } finally {
    syncing = false;
    clearTimeout(timer);
    if (sessionReady && jobs.some(x => activeStates.has(x.status))) timer = setTimeout(sync, 2500);
  }
}

async function init() {
  try {
    await api('/api/session');
    sessionReady = true;
    $('#convert-button').disabled = false;
    $('#limits').textContent = 'No duration, file-size, or daily download caps';
    $('#notice').hidden = true;
    await sync();
    renderJobs();
  } catch (error) {
    $('#notice').textContent = error.message;
    setTimeout(init, 15000);
  }
}

$('#convert-form').onsubmit = async event => {
  event.preventDefault();
  const button = $('#convert-button');
  button.disabled = true;
  showError('');
  try {
    const url = $('#media-url').value.trim();
    const parsed = new URL(url);
    if (['open.spotify.com', 'spotify.link'].includes(parsed.hostname) || (parsed.hostname === 'music.apple.com' && !parsed.pathname.includes('/post/'))) {
      $('#music-results').hidden = false;
      $('#music-results').textContent = 'Finding public recordings…';
      try {
        const result = await api('/api/music/lookup', {method: 'POST', body: JSON.stringify({url})});
        renderMatches(result);
      } catch (error) {
        $('#music-results').hidden = true;
        throw error;
      }
      return;
    }
    const mode = $('input[name="format"]:checked').value;
    const quality = $('#quality').value;
    const format = quality === 'original' ? (mode === 'mp3' ? 'mka' : 'mkv') : mode;
    const job = await api('/api/jobs', {method: 'POST', body: JSON.stringify({url, format, quality: quality === 'original' ? 'best' : quality})});
    $('#media-url').value = '';
    jobs.push(job);
    renderJobs();
    await sync();
  } catch (error) { showError(error.message); }
  finally { button.disabled = false; }
};

$('#clear-history').onclick = async () => {
  const button = $('#clear-history');
  button.disabled = true;
  try {
    for (const job of jobs.filter(x => !activeStates.has(x.status))) await api(`/api/jobs/${encodeURIComponent(job.id)}`, {method: 'DELETE'});
    history = history.filter(x => jobs.some(j => j.id === x.id && activeStates.has(j.status)));
    jobs = jobs.filter(x => activeStates.has(x.status));
    saveHistory(); renderJobs();
  } catch (error) { showError(error.message); }
  finally { button.disabled = false; }
};

document.querySelectorAll('[data-filter]').forEach(button => button.onclick = () => {
  filter = button.dataset.filter;
  document.querySelectorAll('[data-filter]').forEach(x => x.setAttribute('aria-pressed', String(x === button)));
  renderJobs();
});
document.addEventListener('visibilitychange', () => { if (!document.hidden) sync(); });
window.addEventListener('focus', sync);
function updateQuality() {
  const audio = $('input[name="format"]:checked').value === 'mp3';
  const choices = audio
    ? [['best', 'Best MP3 · 320 kbps'], ['original', 'Original audio · no re-encoding (MKA)'], ['256', '256 kbps'], ['192', '192 kbps'], ['128', '128 kbps']]
    : [['best', 'Maximum available · MP4'], ['original', 'Original video + audio · MKV'], ['2160', 'Up to 4K · 2160p'], ['1440', 'Up to 1440p'], ['1080', 'Up to 1080p'], ['720', 'Up to 720p'], ['480', 'Up to 480p']];
  $('#quality').replaceChildren(...choices.map(([value, label]) => {
    const option = element('option', '', label); option.value = value; return option;
  }));
  $('#quality-note').textContent = audio
    ? 'Original keeps the source audio without another lossy conversion. Higher MP3 bitrates cannot restore detail missing from the source.'
    : 'Maximum uses the best source resolution, including 4K and 8K when available. Original preserves the source codecs; MKV may need VLC.';
}
function renderMatches(result) {
  const panel = $('#music-results');
  panel.replaceChildren(element('h3', '', result.artist + ' — ' + result.title),
    element('p', '', 'Choose the correct recording below. These are YouTube search results, not files from ' + result.provider + '. Check the artist, version and duration before downloading.'));
  for (const match of result.candidates) {
    const row = element('div', 'match-row');
    row.append(element('strong', '', match.title));
    row.append(element('small', '', [match.artist, match.duration ? Math.floor(match.duration / 60) + ':' + String(Math.floor(match.duration % 60)).padStart(2, '0') : 'Duration unavailable'].filter(Boolean).join(' · ')));
    const preview = element('a', 'text-button', 'Check recording ↗');
    preview.href = match.url; preview.target = '_blank'; preview.rel = 'noopener noreferrer';
    const select = element('button', 'text-button', 'Use this recording');
    select.type = 'button';
    select.onclick = () => {
      $('#media-url').value = match.url;
      $('input[name="format"][value="mp3"]').checked = true;
      updateQuality();
      panel.hidden = true;
      $('#convert-button').focus();
      showError('');
    };
    row.append(preview, select); panel.append(row);
  }
}
document.querySelectorAll('input[name="format"]').forEach(input => input.addEventListener('change', updateQuality));
updateQuality();
init();
