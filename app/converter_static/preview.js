/* Crate v4: inspect a public link before submitting the conversion. */
let previewTimer;
let previewRequest = 0;

function previewDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds <= 0) return '';
  const rounded = Math.round(seconds);
  return `${Math.floor(rounded / 60)}:${String(rounded % 60).padStart(2, '0')}`;
}

function hidePreview() {
  const panel = document.querySelector('#link-preview');
  if (panel) panel.hidden = true;
}

async function updatePreview() {
  const input = document.querySelector('#media-url');
  const panel = document.querySelector('#link-preview');
  if (!input || !panel) return;
  const url = input.value.trim();
  if (!url) { hidePreview(); return; }
  try { new URL(url); } catch { hidePreview(); return; }
  const requestId = ++previewRequest;
  panel.hidden = false;
  panel.querySelector('.preview-title').textContent = 'Checking link…';
  panel.querySelector('.preview-meta').textContent = '';
  try {
    const data = await api('/api/preview', {method: 'POST', body: JSON.stringify({url})});
    if (requestId !== previewRequest || input.value.trim() !== url) return;
    panel.querySelector('.preview-title').textContent = data.title || 'Public media link';
    const meta = [data.creator, data.source, previewDuration(data.duration)].filter(Boolean).join(' · ');
    panel.querySelector('.preview-meta').textContent = meta || 'Ready to submit';
  } catch {
    if (requestId === previewRequest) hidePreview();
  }
}

const previewInput = document.querySelector('#media-url');
previewInput?.addEventListener('input', () => {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(updatePreview, 650);
});
previewInput?.addEventListener('paste', () => {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(updatePreview, 150);
});
