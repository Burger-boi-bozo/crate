/* Resolve public metadata before the user starts a download. */
(() => {
  const input = document.querySelector('#media-url');
  const panel = document.querySelector('#link-preview');
  const form = document.querySelector('#convert-form');
  if (!input || !panel) return;
  let timer = null;
  let controller = null;
  let sequence = 0;

  const clear = () => { panel.hidden = true; panel.replaceChildren(); };
  const duration = seconds => {
    if (!Number.isFinite(seconds) || seconds <= 0) return '';
    const value = Math.round(seconds);
    return value >= 3600
      ? `${Math.floor(value / 3600)}:${String(Math.floor(value % 3600 / 60)).padStart(2, '0')}:${String(value % 60).padStart(2, '0')}`
      : `${Math.floor(value / 60)}:${String(value % 60).padStart(2, '0')}`;
  };
  function render(data) {
    panel.replaceChildren();
    if (data.thumbnail) {
      const image = document.createElement('img');
      image.src = data.thumbnail; image.alt = ''; image.loading = 'eager';
      panel.append(image);
    } else {
      const placeholder = document.createElement('div');
      placeholder.className = 'preview-placeholder'; placeholder.textContent = '▷';
      panel.append(placeholder);
    }
    const copy = document.createElement('div'); copy.className = 'preview-copy';
    const source = document.createElement('p'); source.className = 'preview-source'; source.textContent = data.source || 'Media';
    const title = document.createElement('p'); title.className = 'preview-title'; title.textContent = data.title || 'Media clip';
    const meta = document.createElement('p'); meta.className = 'preview-meta';
    meta.textContent = [data.creator, duration(data.duration)].filter(Boolean).join(' · ');
    copy.append(source, title, meta); panel.append(copy); panel.hidden = false;
  }
  function loading() {
    panel.replaceChildren();
    const text = document.createElement('p'); text.className = 'preview-loading'; text.textContent = 'Checking this link…';
    panel.append(text); panel.hidden = false;
  }
  function failure(message) {
    panel.replaceChildren();
    const text = document.createElement('p'); text.className = 'preview-error';
    text.textContent = `${message || 'Preview unavailable.'} You can still try the download.`;
    panel.append(text); panel.hidden = false;
  }
  async function lookup() {
    const url = input.value.trim();
    if (!url) { clear(); return; }
    try { new URL(url); } catch { clear(); return; }
    const current = ++sequence;
    controller?.abort(); controller = new AbortController();
    loading();
    try {
      const data = await api('/api/preview', {method: 'POST', body: JSON.stringify({url}), signal: controller.signal});
      if (current === sequence) render(data);
    } catch (error) {
      if (error.name !== 'AbortError' && current === sequence) failure(error.message);
    }
  }
  input.addEventListener('input', () => {
    clearTimeout(timer); controller?.abort();
    if (!input.value.trim()) { clear(); return; }
    timer = setTimeout(lookup, 650);
  });
  input.addEventListener('paste', () => { clearTimeout(timer); timer = setTimeout(lookup, 80); });
  form?.addEventListener('submit', () => setTimeout(() => { if (!input.value.trim()) clear(); }, 300));
})();
