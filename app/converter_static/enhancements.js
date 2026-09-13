/* Crate v3 controls and detailed progress, layered onto the existing UI. */
activeStates.add('paused');
const baseRenderJobs = renderJobs;

function humanBytes(value) {
  if (!Number.isFinite(value) || value <= 0) return '';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let size = value, index = 0;
  while (size >= 1024 && index < units.length - 1) { size /= 1024; index += 1; }
  return `${size.toFixed(index ? 1 : 0)} ${units[index]}`;
}
function humanEta(value) {
  if (!Number.isFinite(value) || value < 0) return '';
  const seconds = Math.round(value);
  if (seconds < 60) return `${seconds}s remaining`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s remaining`;
}
function detailedStatus(job) {
  if (job.status === 'queued') return job.queue_position ? `Waiting in queue · position ${job.queue_position}` : 'Waiting in queue…';
  if (job.status === 'paused') return `Paused${job.stage && job.stage !== 'paused' ? ` · ${job.stage}` : ''}`;
  if (job.status === 'downloading') {
    const amount = job.total_bytes ? `${humanBytes(job.downloaded_bytes)} / ${humanBytes(job.total_bytes)}` : humanBytes(job.downloaded_bytes);
    const speed = job.speed ? `${humanBytes(job.speed)}/s` : '';
    return [job.stage === 'resolving' ? 'Resolving source…' : 'Downloading', amount, speed, humanEta(job.eta)].filter(Boolean).join(' · ');
  }
  if (job.status === 'converting') return `Converting${Number.isFinite(job.conversion_progress) ? ` · ${job.conversion_progress}%` : '…'}`;
  if (job.status === 'failed') return job.error || 'Conversion failed';
  if (job.status === 'ready') return 'Ready to save';
  if (job.status === 'cancelled') return 'Cancelled';
  if (job.status === 'expired') return 'File expired · convert again to download';
  return job.status;
}

async function jobAction(job, action, button) {
  button.disabled = true;
  showError('');
  try {
    const created = await api(`/api/jobs/${encodeURIComponent(job.id)}/${action}`, {method: 'POST'});
    if (action === 'retry' && created) jobs.push(created);
    await sync();
  } catch (error) {
    showError(error.message);
    button.disabled = false;
  }
}

renderJobs = function enhancedRenderJobs() {
  baseRenderJobs();
  const all = allJobs();
  const shown = all.filter(job => filter === 'all' || (filter === 'mp3' ? ['mp3', 'mka'].includes(job.format) : ['mp4', 'mkv'].includes(job.format)));
  [...document.querySelectorAll('#job-list .job')].forEach((row, index) => {
    const job = shown[index];
    if (!job) return;
    const status = row.querySelector('.job-status');
    if (status) status.textContent = detailedStatus(job);
    const progress = row.querySelector('progress');
    if (progress && Number.isFinite(job.progress) && job.progress > 0) progress.value = job.progress;
    const content = row.querySelector('.job-content');
    if (content && job.status === 'failed' && !content.querySelector('.job-diagnostic')) {
      const details = element('details', 'job-diagnostic');
      const summary = element('summary', '', job.error_code ? `Details · ${job.error_code}` : 'Details');
      details.append(summary, element('p', '', job.diagnostic || 'No additional diagnostic was recorded.'));
      content.append(details);
    }
    const actions = row.querySelector('.job-actions');
    if (!actions || actions.querySelector('[data-v3-action]')) return;
    if (['queued', 'downloading', 'converting'].includes(job.status)) {
      const pause = element('button', 'text-button', 'Pause');
      pause.dataset.v3Action = 'pause'; pause.onclick = () => jobAction(job, 'pause', pause); actions.prepend(pause);
    } else if (job.status === 'paused') {
      const resume = element('button', 'text-button', 'Resume');
      resume.dataset.v3Action = 'resume'; resume.onclick = () => jobAction(job, 'resume', resume); actions.prepend(resume);
    } else if (['failed', 'cancelled', 'expired'].includes(job.status)) {
      const retry = element('button', 'text-button', 'Retry');
      retry.dataset.v3Action = 'retry'; retry.onclick = () => jobAction(job, 'retry', retry); actions.prepend(retry);
    }
  });
};
