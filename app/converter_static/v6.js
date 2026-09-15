/* Crate v6 public UX: advanced jobs, uploads, batches, sharing, history search, and PWA hooks. */
const v6AudioFormats = new Set(['mp3','mka','m4a','opus','flac','wav','aac']);
const v6VideoQualities = new Set(['best','2160','1440','1080','720','480']);
const v6AudioQualities = new Set(['best','320','256','192','128']);
let v6Batches = [];
let v6ShareJob = null;
let v6LocalFile = null;

function v6Number(selector) {
  const raw = $(selector)?.value?.trim();
  if (!raw) return null;
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
}

function v6Options() {
  const mode = $('input[name="format"]:checked')?.value || 'mp4';
  let quality = $('#quality')?.value || 'best';
  let format = $('#output-format')?.value || 'auto';
  if (format === 'auto') format = quality === 'original' ? (mode === 'mp3' ? 'mka' : 'mkv') : mode;
  if (quality === 'original') quality = 'best';
  if (v6AudioFormats.has(format) && !v6AudioQualities.has(quality)) quality = 'best';
  if (!v6AudioFormats.has(format) && !v6VideoQualities.has(quality)) quality = 'best';
  if (['mkv','mka','flac','wav'].includes(format)) quality = 'best';
  const subtitles = Boolean($('#include-subtitles')?.checked);
  return {
    format, quality,
    priority: $('#job-priority')?.value || 'normal',
    start: v6Number('#trim-start'), end: v6Number('#trim-end'),
    fps: v6Number('#video-fps'), crf: v6Number('#video-crf'),
    video_codec: $('#video-codec')?.value || 'auto',
    audio_codec: $('#audio-codec')?.value || 'auto',
    audio_bitrate: v6Number('#audio-bitrate'),
    hardware: $('#hardware-mode')?.value || 'auto',
    metadata: Boolean($('#embed-metadata')?.checked),
    thumbnail: Boolean($('#embed-thumbnail')?.checked),
    subtitles,
    subtitle_langs: ($('#subtitle-langs')?.value || 'en').split(',').map(x => x.trim()).filter(Boolean),
    subtitle_mode: subtitles ? ($('#subtitle-mode')?.value || 'external') : 'off',
    filename_template: $('#filename-template')?.value?.trim() || '{title}',
  };
}

async function v6Upload(file, options) {
  const form = new FormData();
  form.append('file', file, file.name);
  form.append('options', JSON.stringify(options));
  const response = await fetch('/api/uploads', {method:'POST', headers:{'X-Crate-Request':'1'}, body:form});
  const data = await response.json().catch(() => ({detail:'Upload failed.'}));
  if (!response.ok) throw new Error(data.detail || 'Upload failed.');
  return data;
}
async function v6Submit(event) {
  event.preventDefault();
  const button = $('#convert-button');
  button.disabled = true; showError('');
  try {
    const options = v6Options();
    if (v6LocalFile) {
      jobs.push(await v6Upload(v6LocalFile, options));
      v6LocalFile = null; $('#local-file').value = ''; $('#local-file-name').textContent = '';
    } else {
      const urls = $('#media-url').value.split(/\r?\n/).map(x => x.trim()).filter(Boolean);
      if (!urls.length) throw new Error('Paste a public media URL or choose a local file.');
      for (const url of urls) new URL(url);
      if (urls.length === 1) {
        const parsed = new URL(urls[0]);
        if (['open.spotify.com','spotify.link'].includes(parsed.hostname) || (parsed.hostname === 'music.apple.com' && !parsed.pathname.includes('/post/'))) {
          $('#music-results').hidden = false; $('#music-results').textContent = 'Finding public recordings…';
          renderMatches(await api('/api/music/lookup', {method:'POST', body:JSON.stringify({url:urls[0]})}));
          return;
        }
        jobs.push(await api('/api/jobs', {method:'POST', body:JSON.stringify({url:urls[0], ...options})}));
      } else {
        const batch = await api('/api/batches', {method:'POST', body:JSON.stringify({urls, ...options})});
        for (const job of batch.jobs) if (!jobs.some(existing => existing.id === job.id)) jobs.push(job);
        $('#notice').textContent = `Batch started · ${batch.jobs.length} jobs`; $('#notice').hidden = false;
      }
      $('#media-url').value = '';
    }
    renderJobs(); await sync(); await v6RefreshBatches();
  } catch (error) {
    showError(error.message === 'Invalid URL' ? 'Every batch line must be a valid URL.' : error.message);
  } finally { button.disabled = false; }
}
function v6CheckedIds(batchNode, batch) {
  const checked = [...batchNode.querySelectorAll('input[data-job-id]:checked')].map(x => x.dataset.jobId);
  return checked.length ? checked : batch.jobs.map(job => job.id);
}

async function v6BatchAction(batchNode, batch, action, button) {
  button.disabled = true;
  try {
    const ids = v6CheckedIds(batchNode, batch);
    await api(`/api/batches/${encodeURIComponent(batch.id)}/action`, {
      method:'POST', body:JSON.stringify({action, ids}),
    });
    await sync(); await v6RefreshBatches();
  } catch (error) { showError(error.message); }
  finally { button.disabled = false; }
}

async function v6ZipSelected(batchNode, batch, button) {
  button.disabled = true;
  try {
    const ids = v6CheckedIds(batchNode, batch);
    const response = await fetch(`/api/batches/${encodeURIComponent(batch.id)}/zip`, {
      method:'POST', headers:{'X-Crate-Request':'1','Content-Type':'application/json'}, body:JSON.stringify({ids}),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.detail || 'Selected files are not ready yet.');
    }
    const blob = await response.blob(); const url = URL.createObjectURL(blob);
    const link = document.createElement('a'); link.href = url; link.download = `crate-batch-${batch.id}.zip`; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) { showError(error.message); }
  finally { button.disabled = false; }
}
function v6RenderBatches() {
  const container = $('#batch-list'); if (!container) return;
  container.replaceChildren();
  for (const batch of v6Batches) {
    const card = element('details', 'batch-card');
    const summary = element('summary', 'batch-summary');
    summary.append(element('strong', '', batch.name || `Batch ${batch.id.slice(0,6)}`),
      element('span', '', `${batch.ready}/${batch.total} ready · ${batch.failed} failed · ${batch.active} active`));
    card.append(summary);
    const jobsBox = element('div', 'batch-jobs');
    for (const job of batch.jobs) {
      const label = element('label', 'batch-job');
      const check = document.createElement('input'); check.type = 'checkbox'; check.dataset.jobId = job.id; check.checked = job.status === 'ready';
      label.append(check, element('span', '', `${job.title || 'Media clip'} · ${job.status}`)); jobsBox.append(label);
    }
    card.append(jobsBox);
    const actions = element('div', 'batch-actions');
    for (const [label, action] of [['Pause selected','pause'],['Resume selected','resume'],['Cancel selected','cancel'],['Retry failed','retry_failed']]) {
      const button = element('button', 'text-button', label); button.type = 'button';
      button.onclick = () => v6BatchAction(card, batch, action, button); actions.append(button);
    }
    const zip = element('button', 'text-button', 'ZIP selected ready files'); zip.type = 'button';
    zip.onclick = () => v6ZipSelected(card, batch, zip); actions.append(zip); card.append(actions); container.append(card);
  }
}

async function v6RefreshBatches() {
  try { v6Batches = await api('/api/batches'); v6RenderBatches(); }
  catch { /* batch history is supplemental */ }
}
function v6OpenShare(job) {
  v6ShareJob = job;
  $('#share-result').hidden = true; $('#share-url').value = ''; $('#share-password').value = '';
  $('#share-dialog').showModal();
}

function v6DecorateJobs() {
  for (const job of allJobs()) {
    const row = document.querySelector(`.job[data-job-id="${job.id}"]`); if (!row) continue;
    const content = row.querySelector('.job-content'); const actions = row.querySelector('.job-actions');
    if (content && !content.querySelector('.v6-meta')) {
      const bits = [];
      if (job.cache_hit) bits.push('reused cached output');
      if (job.checksum) bits.push(`SHA-256 ${job.checksum.slice(0,12)}…`);
      if (job.video_encoder) bits.push(job.video_encoder);
      if (job.learned_eta) bits.push(`learned ETA ~${job.learned_eta}s`);
      if (bits.length) content.append(element('p', 'job-meta v6-meta', bits.join(' · ')));
    }
    if (!actions || actions.querySelector('[data-v6-action]')) continue;
    if (job.status === 'ready') {
      const share = element('button', 'text-button', 'Share'); share.type = 'button'; share.dataset.v6Action = 'share'; share.onclick = () => v6OpenShare(job); actions.append(share);
      if (job.subtitle_filename) {
        const sub = element('a', 'text-button', 'Subtitle ↓'); sub.dataset.v6Action = 'subtitle'; sub.href = `/api/jobs/${encodeURIComponent(job.id)}/subtitle`; actions.append(sub);
      }
      if (job.checksum) {
        const copy = element('button', 'text-button', 'Copy checksum'); copy.type = 'button'; copy.dataset.v6Action = 'checksum';
        copy.onclick = async () => { await navigator.clipboard?.writeText(job.checksum); copy.textContent = 'Copied'; setTimeout(() => copy.textContent = 'Copy checksum', 1200); };
        actions.append(copy);
      }
    }
    if (['queued','retry_wait'].includes(job.status)) {
      const priority = document.createElement('select'); priority.dataset.v6Action = 'priority'; priority.className = 'mini-select';
      for (const value of ['high','normal','low']) { const option=document.createElement('option'); option.value=value; option.textContent=`${value} priority`; option.selected=job.priority===value; priority.append(option); }
      priority.onchange = async () => { await api(`/api/jobs/${encodeURIComponent(job.id)}/priority`, {method:'POST', body:JSON.stringify({priority:priority.value})}); await sync(); };
      actions.append(priority);
    }
  }
}
function v6ApplySearch() {
  const query = ($('#history-search')?.value || '').trim().toLowerCase();
  for (const row of document.querySelectorAll('.job[data-job-id]')) {
    if (!query) { row.hidden = false; continue; }
    const job = allJobs().find(item => item.id === row.dataset.jobId);
    const text = [job?.title, job?.url, job?.filename, job?.format].filter(Boolean).join(' ').toLowerCase();
    row.hidden = !text.includes(query);
  }
}

const v6BaseRenderJobs = renderJobs;
renderJobs = function renderJobsV6() {
  v6BaseRenderJobs();
  v6DecorateJobs();
  v6ApplySearch();
};

$('#history-search')?.addEventListener('input', v6ApplySearch);

$('#share-form')?.addEventListener('submit', async event => {
  if (event.submitter?.value === 'cancel') return;
  event.preventDefault();
  if (!v6ShareJob) return;
  try {
    const body = {
      expires_in: Number($('#share-expiry').value),
      max_downloads: Number($('#share-limit').value || 0),
      password: $('#share-password').value || null,
    };
    const created = await api(`/api/jobs/${encodeURIComponent(v6ShareJob.id)}/share`, {method:'POST', body:JSON.stringify(body)});
    $('#share-url').value = created.url; $('#share-result').hidden = false;
  } catch (error) { showError(error.message); }
});

$('#copy-share')?.addEventListener('click', async () => {
  const value = $('#share-url').value; if (!value) return;
  await navigator.clipboard?.writeText(value); $('#copy-share').textContent = 'Copied';
  setTimeout(() => $('#copy-share').textContent = 'Copy link', 1200);
});
$('#paste-link')?.addEventListener('click', async () => {
  try {
    const value = await navigator.clipboard.readText();
    if (value.trim()) { $('#media-url').value = value.trim(); $('#media-url').dispatchEvent(new Event('input', {bubbles:true})); }
  } catch { showError('Clipboard access was blocked by the browser. Paste normally instead.'); }
});

$('#local-file')?.addEventListener('change', event => {
  v6LocalFile = event.target.files?.[0] || null;
  $('#local-file-name').textContent = v6LocalFile ? v6LocalFile.name : '';
  if (v6LocalFile) $('#media-url').value = '';
});

for (const target of [$('.batch-url-field'), $('#media-url')].filter(Boolean)) {
  target.addEventListener('dragover', event => { event.preventDefault(); target.classList.add('drop-active'); });
  target.addEventListener('dragleave', () => target.classList.remove('drop-active'));
  target.addEventListener('drop', event => {
    event.preventDefault(); target.classList.remove('drop-active');
    const file = [...(event.dataTransfer?.files || [])][0];
    if (file && /^(video|audio)\//.test(file.type)) {
      v6LocalFile = file; $('#local-file-name').textContent = file.name; $('#media-url').value = ''; return;
    }
    const text = event.dataTransfer?.getData('text/uri-list') || event.dataTransfer?.getData('text/plain') || '';
    if (text.trim()) { $('#media-url').value = text.trim(); $('#media-url').dispatchEvent(new Event('input', {bubbles:true})); }
  });
}

$('#convert-form').onsubmit = v6Submit;
function v6SharedUrl() {
  const params = new URLSearchParams(location.search);
  const raw = [params.get('url'), params.get('text'), params.get('title')].filter(Boolean).join(' ');
  const match = raw.match(/https?:\/\/[^\s]+/i);
  if (!match) return;
  $('#media-url').value = match[0];
  $('#media-url').dispatchEvent(new Event('input', {bubbles:true}));
  history.replaceState(null, '', location.pathname);
}

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => navigator.serviceWorker.register('/sw.js').catch(() => {}));
}

v6SharedUrl();
v6RefreshBatches();
setInterval(v6RefreshBatches, 5000);
setTimeout(() => { renderJobs(); v6DecorateJobs(); }, 0);
async function v6ReleaseHistory() {
  try {
    const items=await api('/api/releases'); const section=$('.changelog'); if (!section) return;
    for (const old of [...section.querySelectorAll('.changelog-entry')]) old.remove();
    const link=section.querySelector('.changelog-link');
    for (const item of items) {
      const details=document.createElement('details'); details.className='changelog-entry'; details.open=Boolean(item.current);
      const summary=document.createElement('summary'); summary.innerHTML='';
      summary.append(element('strong','',`v${item.version}${item.current?' · current':''}`),element('span','',item.date)); details.append(summary);
      details.append(element('p','',item.summary));
      if (item.features?.length) details.append(element('p','',`Features: ${item.features.join(' · ')}`));
      if (item.fixes?.length) details.append(element('p','',`Fixes: ${item.fixes.join(' · ')}`));
      if (item.rollback) details.append(element('p','',`Rollback target: v${item.rollback}`));
      section.insertBefore(details,link);
    }
  } catch { /* static changelog remains as fallback */ }
}

v6ReleaseHistory();
