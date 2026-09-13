/* Lightweight server status panel. */
function formatBytes(value) {
  if (!Number.isFinite(value)) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let size = value, index = 0;
  while (size >= 1024 && index < units.length - 1) { size /= 1024; index += 1; }
  return `${size.toFixed(index ? 1 : 0)} ${units[index]}`;
}

async function refreshServerStatus() {
  const panel = document.querySelector('#server-status');
  if (!panel) return;
  try {
    const response = await fetch('/api/status', {headers: {'X-Crate-Request': '1'}});
    if (!response.ok) return;
    const data = await response.json();
    document.querySelector('#status-state').textContent = data.status === 'ok' ? 'Online' : data.status;
    document.querySelector('#status-workers').textContent = String(data.workers ?? '—');
    document.querySelector('#status-active').textContent = String(data.active ?? 0);
    document.querySelector('#status-storage').textContent = `${formatBytes(data.disk_free)} free`;
    document.querySelector('#status-build').textContent = data.label || '—';
    panel.hidden = false;
  } catch { /* keep the main app usable if status is unavailable */ }
}

window.addEventListener('load', refreshServerStatus);
window.addEventListener('focus', refreshServerStatus);
