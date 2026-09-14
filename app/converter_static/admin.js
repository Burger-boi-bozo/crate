const $ = selector => document.querySelector(selector);
let adminTimer = null;
let adminJobs = [];

async function adminApi(path, options = {}) {
  const response = await fetch(path, {...options, headers: {'X-Crate-Request': '1', ...(options.body ? {'Content-Type': 'application/json'} : {}), ...options.headers}});
  if (response.status === 204) return null;
  const data = await response.json().catch(() => ({detail: 'The server did not return a readable response.'}));
  if (!response.ok) throw Object.assign(new Error(data.detail || 'Admin request failed.'), {status: response.status});
  return data;
}
function bytes(value) {
  if (!Number.isFinite(value)) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB']; let size = value, index = 0;
  while (size >= 1024 && index < units.length - 1) { size /= 1024; index += 1; }
  return `${size.toFixed(index ? 1 : 0)} ${units[index]}`;
}
function elapsed(value) {
  const seconds = Math.max(0, Math.floor(value || 0));
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ${Math.floor(seconds % 3600 / 60)}m`;
  return `${Math.floor(seconds / 86400)}d ${Math.floor(seconds % 86400 / 3600)}h`;
}
function card(label, value) {
  const node = document.createElement('div'); node.className = 'admin-card';
  const small = document.createElement('small'); small.textContent = label;
  const strong = document.createElement('strong'); strong.textContent = value;
  node.append(small, strong); return node;
}
function renderStatus(status) {
  const diskPct = status.disk?.total ? Math.round(status.disk.used / status.disk.total * 100) : 0;
  const memUsed = status.memory?.total && status.memory?.available ? status.memory.total - status.memory.available : null;
  $('#admin-cards').replaceChildren(
    card('Version', status.label || '—'), card('Uptime', elapsed(status.uptime_seconds)),
    card('Workers', `${status.workers} · ${status.active} active`), card('Queue', `${status.pending} pending`),
    card('Disk', `${bytes(status.disk?.free)} free · ${diskPct}% used`),
    card('Memory', memUsed ? `${bytes(memUsed)} / ${bytes(status.memory.total)}` : '—'),
    card('Load', status.load?.length ? status.load.join(' · ') : '—'), card('Failures · 24h', String(status.recent_failures ?? 0))
  );
  const providers = $('#provider-list'); providers.replaceChildren();
  for (const item of status.providers || []) {
    const row = document.createElement('div'); row.className = 'provider-row';
    for (const value of [item.host, `${item.total} jobs`, `${item.ready} ready`, `${item.failed} failed`, `${item.blocked} blocked`]) {
      const cell = document.createElement(value === item.host ? 'strong' : 'span'); cell.textContent = value; row.append(cell);
    }
    providers.append(row);
  }
  if (!(status.providers || []).length) providers.textContent = 'No provider history yet.';
  $('#admin-version').textContent = status.label || 'v4';
  $('#admin-updated').textContent = `Updated ${new Date().toLocaleTimeString()}`;
}
function jobMatches(job) {
  const filter = $('#job-filter').value;
  if (filter === 'all') return true;
  if (filter === 'active') return ['queued', 'downloading', 'converting', 'paused'].includes(job.status);
  return job.status === filter;
}
function actionButton(label, action, job) {
  const button = document.createElement('button'); button.className = 'text-button'; button.textContent = label;
  button.onclick = async () => {
    button.disabled = true;
    try {
      await adminApi(`/api/admin/jobs/${encodeURIComponent(job.id)}/${action}`, {method: action === 'delete' ? 'DELETE' : 'POST'});
      await refreshAdmin();
    } catch (error) { alert(error.message); button.disabled = false; }
  };
  return button;
}
function renderJobs() {
  const container = $('#admin-jobs'); container.replaceChildren();
  for (const job of adminJobs.filter(jobMatches)) {
    const row = document.createElement('article'); row.className = 'admin-job';
    const title = document.createElement('div');
    const strong = document.createElement('strong'); strong.textContent = job.title || 'Media clip'; strong.title = job.url || '';
    const meta = document.createElement('div'); meta.className = 'admin-job-meta'; meta.textContent = `${job.source_host || 'unknown'} · ${(job.format || '').toUpperCase()} · ${job.quality || 'best'}`;
    title.append(strong, meta);
    if (job.error_code || job.diagnostic) { const error = document.createElement('div'); error.className = 'admin-error'; error.textContent = [job.error_code, job.diagnostic].filter(Boolean).join(' · '); title.append(error); }
    const state = document.createElement('span'); state.className = `pill ${job.status}`; state.textContent = job.status;
    const progress = document.createElement('span'); progress.textContent = Number.isFinite(job.progress) ? `${job.progress}%` : '—';
    const actions = document.createElement('div'); actions.className = 'admin-job-actions';
    if (['queued', 'downloading', 'converting', 'paused'].includes(job.status)) actions.append(actionButton('Cancel', 'cancel', job));
    if (['failed', 'cancelled', 'expired'].includes(job.status)) actions.append(actionButton('Retry', 'retry', job));
    if (!['queued', 'downloading', 'converting', 'paused'].includes(job.status)) actions.append(actionButton('Delete', 'delete', job));
    row.append(title, state, progress, actions); container.append(row);
  }
  if (!container.children.length) container.textContent = 'No jobs match this filter.';
}
async function refreshAdmin() {
  clearTimeout(adminTimer);
  try {
    const [status, jobs] = await Promise.all([adminApi('/api/admin/status'), adminApi('/api/admin/jobs')]);
    $('#admin-login').hidden = true; $('#admin-dashboard').hidden = false;
    adminJobs = jobs; renderStatus(status); renderJobs();
    adminTimer = setTimeout(refreshAdmin, 5000);
  } catch (error) {
    if (error.status === 401) { $('#admin-dashboard').hidden = true; $('#admin-login').hidden = false; return; }
    $('#admin-updated').textContent = error.message;
    adminTimer = setTimeout(refreshAdmin, 10000);
  }
}
$('#admin-login-form').onsubmit = async event => {
  event.preventDefault(); const error = $('#admin-login-error'); error.hidden = true;
  try { await adminApi('/api/admin/login', {method: 'POST', body: JSON.stringify({password: $('#admin-password').value})}); $('#admin-password').value = ''; await refreshAdmin(); }
  catch (reason) { error.textContent = reason.message; error.hidden = false; }
};
$('#admin-logout').onclick = async () => { await adminApi('/api/admin/session', {method: 'DELETE'}); location.reload(); };
$('#job-filter').onchange = renderJobs;
window.addEventListener('focus', refreshAdmin);
refreshAdmin();
