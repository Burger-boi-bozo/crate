/* Crate v4: instant job updates with polling retained as a fallback. */
let crateEventSource;

function applyLiveJobs(nextJobs) {
  jobs = nextJobs;
  const combined = new Map(history.map(item => [item.id, item]));
  for (const job of jobs) combined.set(job.id, {
    id: job.id, url: job.url, title: job.title, format: job.format,
    quality: job.quality, size: job.size, created_at: job.created_at
  });
  history = [...combined.values()].sort((a, b) => b.created_at - a.created_at);
  saveHistory();
  renderJobs();
}

function applyLivePacket(packet) {
  if (packet.type === 'sync' && Array.isArray(packet.jobs)) {
    applyLiveJobs(packet.jobs);
    return;
  }
  if (!packet.job?.id) return;
  const index = jobs.findIndex(job => job.id === packet.job.id);
  if (index >= 0) jobs[index] = packet.job;
  else jobs.push(packet.job);
  applyLiveJobs(jobs);
}

function setLiveState(connected) {
  const badge = document.querySelector('#live-connection');
  if (!badge) return;
  badge.textContent = connected ? 'LIVE' : 'POLLING';
  badge.dataset.state = connected ? 'live' : 'polling';
}

function connectLiveUpdates() {
  if (!sessionReady) {
    setTimeout(connectLiveUpdates, 500);
    return;
  }
  crateEventSource?.close();
  crateEventSource = new EventSource('/api/events');
  crateEventSource.onopen = () => setLiveState(true);
  crateEventSource.onmessage = event => {
    try { applyLivePacket(JSON.parse(event.data)); } catch { /* ignore malformed packets */ }
  };
  crateEventSource.onerror = () => setLiveState(false);
}

window.addEventListener('load', connectLiveUpdates);
window.addEventListener('beforeunload', () => crateEventSource?.close());
