/* Crate v6 private admin controls. Every endpoint used here requires an admin session. */
let v6AdminSettings = {};
let v6AdminSecurity = null;
let v6AdminDeployment = null;

function v6TextRow(title, detail, actions = []) {
  const row = document.createElement('div'); row.className = 'admin-list-row';
  const copy = document.createElement('div'); const strong = document.createElement('strong'); strong.textContent = title;
  const small = document.createElement('small'); small.textContent = detail || ''; copy.append(strong, small); row.append(copy);
  const box = document.createElement('div'); box.className = 'admin-job-actions'; for (const action of actions) box.append(action); row.append(box); return row;
}

function v6Button(label, callback) {
  const button = document.createElement('button'); button.className = 'text-button'; button.type = 'button'; button.textContent = label;
  button.onclick = async () => { button.disabled = true; try { await callback(); } catch (error) { alert(error.message); } finally { button.disabled = false; } };
  return button;
}

function v6RenderMetrics(data) {
  const root = $('#metrics-chart'); root.replaceChildren(); const buckets = data?.buckets || [];
  const max = Math.max(1, ...buckets.map(item => Math.max(item.submitted, item.ready, item.failed)));
  for (const item of buckets) {
    const col = document.createElement('div'); col.className = 'metric-column'; col.title = `${new Date(item.start*1000).toLocaleTimeString()} · ${item.submitted} submitted · ${item.ready} ready · ${item.failed} failed`;
    const submitted = document.createElement('i'); submitted.className = 'metric-bar submitted'; submitted.style.height = `${Math.max(2, item.submitted/max*100)}%`;
    const ready = document.createElement('i'); ready.className = 'metric-bar ready'; ready.style.height = `${Math.max(2, item.ready/max*100)}%`;
    const failed = document.createElement('i'); failed.className = 'metric-bar failed'; failed.style.height = `${Math.max(2, item.failed/max*100)}%`;
    col.append(submitted, ready, failed); root.append(col);
  }
}
function v6RenderSettings(values) {
  v6AdminSettings = values || {}; const root = $('#settings-grid'); root.replaceChildren();
  for (const [key, value] of Object.entries(v6AdminSettings)) {
    const label = document.createElement('label'); label.textContent = key.replaceAll('_',' ');
    let input;
    if (typeof value === 'boolean') { input = document.createElement('input'); input.type='checkbox'; input.checked=value; }
    else { input = document.createElement('input'); input.type='number'; input.value=String(value); input.step=Number.isInteger(value)?'1':'0.1'; }
    input.dataset.setting = key; label.append(input); root.append(label);
  }
}

function v6RenderStorage(data) {
  $('#storage-summary').textContent = `${bytes(data.stored_bytes)} stored · ${bytes(data.disk?.free)} free`;
  const root = $('#storage-files'); root.replaceChildren();
  for (const item of data.files || []) {
    const hours = item.expires_at ? Math.max(0, Math.round((item.expires_at-Date.now()/1000)/3600)) : null;
    const extend = v6Button('Keep 7 days', async () => { await adminApi(`/api/admin/jobs/${item.id}/retention`, {method:'POST', body:JSON.stringify({hours:168})}); await v6LoadAdmin(); });
    root.append(v6TextRow(item.filename || item.title || item.id, `${bytes(item.size)} · ${item.format?.toUpperCase() || ''} · ${hours==null?'no expiry':`${hours}h left`}`, [extend]));
  }
  if (!root.children.length) root.textContent = 'No completed files are currently stored.';
}


function v6RenderCapabilities(data) {
  const root=$('#capabilities-list'); root.replaceChildren();
  const hardware=data.hardware?.available ? 'hardware encoder available' : 'software encoding';
  const encoders=Object.entries(data.hardware?.encoders || {}).flatMap(([codec, values]) => values.map(value => `${codec}:${value}`));
  root.append(v6TextRow('FFmpeg', data.ffmpeg || 'unavailable'));
  root.append(v6TextRow('yt-dlp', data.yt_dlp || 'unavailable'));
  root.append(v6TextRow('Encoding', `${hardware}${encoders.length ? ' · '+encoders.join(', ') : ''}`));
  root.append(v6TextRow('Outputs', (data.formats || []).join(', ')));
}

function v6RenderTokens(items) {
  const root=$('#token-list'); root.replaceChildren();
  for (const item of items || []) {
    const revoke=v6Button('Revoke', async()=>{ await adminApi(`/api/admin/tokens/${item.id}`, {method:'DELETE'}); await v6LoadAdmin(); });
    root.append(v6TextRow(item.name, `${item.scopes.join(', ')} · ${item.revoked_at?'revoked':'active'} · last used ${item.last_used?new Date(item.last_used*1000).toLocaleString():'never'}`, item.revoked_at?[]:[revoke]));
  }
}
function v6RenderWebhooks(items) {
  const root=$('#webhook-list'); root.replaceChildren();
  for (const item of items || []) {
    const toggle=v6Button(item.enabled?'Disable':'Enable', async()=>{ await adminApi(`/api/admin/webhooks/${item.id}`, {method:'PATCH', body:JSON.stringify({enabled:!item.enabled})}); await v6LoadAdmin(); });
    const remove=v6Button('Delete', async()=>{ await adminApi(`/api/admin/webhooks/${item.id}`, {method:'DELETE'}); await v6LoadAdmin(); });
    const delivery=item.last_delivery?`last ${item.last_status || 'error'} · ${new Date(item.last_delivery*1000).toLocaleString()}`:'never delivered';
    root.append(v6TextRow(item.name, `${item.events.join(', ')} · ${delivery}${item.last_error?` · ${item.last_error}`:''}`, [toggle,remove]));
  }
}

function v6RenderPasskeys(items) {
  const root=$('#passkey-list'); root.replaceChildren();
  for (const item of items || []) {
    const remove=v6Button('Delete', async()=>{ await adminApi(`/api/admin/passkeys/${item.id}`, {method:'DELETE'}); await v6LoadAdmin(); });
    root.append(v6TextRow(item.name, `added ${new Date(item.created_at*1000).toLocaleString()} · last used ${item.last_used?new Date(item.last_used*1000).toLocaleString():'never'}`, [remove]));
  }
  if (!root.children.length) root.textContent='No passkeys enrolled yet.';
}

function v6RenderAudit(items) {
  const root=$('#audit-list'); root.replaceChildren();
  for (const item of items || []) root.append(v6TextRow(item.action, `${new Date(item.created_at*1000).toLocaleString()} · ${item.actor}${item.target?` · ${item.target}`:''}`));
}

function v6RenderDeployment(data) {
  v6AdminDeployment=data; const root=$('#deployment-state'); root.replaceChildren();
  root.append(v6TextRow(data.current?.label || 'Current build', data.rollback_target?`Rollback target: ${String(data.rollback_target).slice(0,12)}`:'No rollback target recorded'));
  const releases=$('#release-history'); releases.replaceChildren();
  for (const item of data.releases || []) releases.append(v6TextRow(`v${item.version}${item.current?' · current':''}`, `${item.date} · ${item.summary} · rollback ${item.rollback || 'n/a'}`));
}
function v6B64ToBytes(value) {
  const normalized=value.replace(/-/g,'+').replace(/_/g,'/'); const padded=normalized+'='.repeat((4-normalized.length%4)%4);
  const raw=atob(padded); return Uint8Array.from(raw, c=>c.charCodeAt(0));
}
function v6BytesToB64(value) {
  const bytes=new Uint8Array(value); let raw=''; for (const byte of bytes) raw+=String.fromCharCode(byte);
  return btoa(raw).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');
}
function v6CreationOptions(options) {
  const copy=structuredClone(options); copy.challenge=v6B64ToBytes(copy.challenge); copy.user.id=v6B64ToBytes(copy.user.id);
  for (const item of copy.excludeCredentials || []) item.id=v6B64ToBytes(item.id); return copy;
}
function v6RequestOptions(options) {
  const copy=structuredClone(options); copy.challenge=v6B64ToBytes(copy.challenge);
  for (const item of copy.allowCredentials || []) item.id=v6B64ToBytes(item.id); return copy;
}
function v6CredentialJSON(credential) {
  const response={clientDataJSON:v6BytesToB64(credential.response.clientDataJSON)};
  if (credential.response.attestationObject) response.attestationObject=v6BytesToB64(credential.response.attestationObject);
  if (credential.response.authenticatorData) response.authenticatorData=v6BytesToB64(credential.response.authenticatorData);
  if (credential.response.signature) response.signature=v6BytesToB64(credential.response.signature);
  if (credential.response.userHandle) response.userHandle=v6BytesToB64(credential.response.userHandle);
  if (credential.response.getTransports) response.transports=credential.response.getTransports();
  return {id:credential.id,rawId:v6BytesToB64(credential.rawId),type:credential.type,response,clientExtensionResults:credential.getClientExtensionResults()};
}
async function v6EnrollPasskey() {
  if (!window.PublicKeyCredential) throw new Error('This browser does not support passkeys.');
  const name=prompt('Passkey name','My passkey') || 'Passkey';
  const start=await adminApi('/api/admin/passkeys/register/options',{method:'POST',body:JSON.stringify({name})});
  const credential=await navigator.credentials.create({publicKey:v6CreationOptions(start.options)});
  if (!credential) throw new Error('Passkey enrollment was cancelled.');
  await adminApi('/api/admin/passkeys/register/verify',{method:'POST',body:JSON.stringify({challenge_id:start.challenge_id,credential:v6CredentialJSON(credential)})});
  await v6LoadAdmin();
}

async function v6PasskeyLogin() {
  if (!window.PublicKeyCredential) throw new Error('This browser does not support passkeys.');
  const start=await adminApi('/api/admin/passkeys/auth/options',{method:'POST'});
  const credential=await navigator.credentials.get({publicKey:v6RequestOptions(start.options)});
  if (!credential) throw new Error('Passkey sign-in was cancelled.');
  await adminApi('/api/admin/passkeys/auth/verify',{method:'POST',body:JSON.stringify({challenge_id:start.challenge_id,credential:v6CredentialJSON(credential)})});
  await refreshAdmin(); await v6LoadAdmin();
}

async function v6LoadAdmin() {
  if ($('#admin-dashboard')?.hidden) return;
  try {
    const [settings,metrics,storage,capabilities,tokens,hooks,audit,security,passkeys,deployment]=await Promise.all([
      adminApi('/api/admin/settings'),adminApi('/api/admin/metrics/timeseries'),adminApi('/api/admin/storage'),
      adminApi('/api/admin/capabilities'),adminApi('/api/admin/tokens'),adminApi('/api/admin/webhooks'),adminApi('/api/admin/audit?limit=100'),
      adminApi('/api/admin/security'),adminApi('/api/admin/passkeys'),adminApi('/api/admin/deployment')]);
    v6RenderSettings(settings.values); v6RenderMetrics(metrics); v6RenderStorage(storage); v6RenderCapabilities(capabilities); v6RenderTokens(tokens); v6RenderWebhooks(hooks);
    v6RenderAudit(audit); v6AdminSecurity=security; v6RenderPasskeys(passkeys); v6RenderDeployment(deployment);
    $('#security-summary').textContent=`${security.passkeys} passkeys · ${Math.round(security.session_ttl/3600)}h sessions${security.trusted_ips_configured?' · IP restricted':''}`;
    $('#rate-limit-state').textContent=`Admin login guard: ${security.rate_limit.blocked_sources} blocked sources in the last 5 minutes.`;
    $('#drain-toggle').textContent=adminStatus?.scheduler?.drain?'Disable drain':'Enable drain';
  } catch (error) { if (error.status !== 401) console.error(error); }
}
$('#save-settings')?.addEventListener('click', async event => {
  const button=event.currentTarget; button.disabled=true;
  try {
    const values={};
    for (const input of document.querySelectorAll('[data-setting]')) values[input.dataset.setting]=input.type==='checkbox'?input.checked:Number(input.value);
    await adminApi('/api/admin/settings',{method:'PATCH',body:JSON.stringify({values})}); await refreshAdmin(); await v6LoadAdmin();
  } catch (error) { alert(error.message); } finally { button.disabled=false; }
});

$('#drain-toggle')?.addEventListener('click', async event => {
  const button=event.currentTarget; button.disabled=true;
  try { await adminApi('/api/admin/drain',{method:'POST',body:JSON.stringify({enabled:!adminStatus?.scheduler?.drain})}); await refreshAdmin(); await v6LoadAdmin(); }
  catch (error) { alert(error.message); } finally { button.disabled=false; }
});

$('#token-form')?.addEventListener('submit', async event => {
  event.preventDefault(); const created=await adminApi('/api/admin/tokens',{method:'POST',body:JSON.stringify({name:$('#token-name').value,scopes:['jobs:read','jobs:write']})});
  $('#token-result').hidden=false; $('#token-result').textContent=`Copy now — this token is shown once: ${created.token}`; $('#token-name').value=''; await v6LoadAdmin();
});

$('#webhook-form')?.addEventListener('submit', async event => {
  event.preventDefault(); await adminApi('/api/admin/webhooks',{method:'POST',body:JSON.stringify({name:$('#webhook-name').value,url:$('#webhook-url').value,events:['job.ready','job.failed','batch.complete']})});
  $('#webhook-name').value=''; $('#webhook-url').value=''; await v6LoadAdmin();
});

$('#passkey-enroll')?.addEventListener('click', () => v6EnrollPasskey().catch(error=>alert(error.message)));
$('#passkey-login')?.addEventListener('click', () => v6PasskeyLogin().catch(error=>{ $('#admin-login-error').textContent=error.message; $('#admin-login-error').hidden=false; }));
$('#restore-backup')?.addEventListener('change', async event => {
  const file=event.target.files?.[0]; if (!file) return;
  if (!confirm('Restore this Crate backup? Maintenance mode must be enabled and active jobs must be finished.')) return;
  const form=new FormData(); form.append('file',file,file.name); $('#restore-state').textContent='Restoring…';
  try {
    const response=await fetch('/api/admin/restore',{method:'POST',headers:{'X-Crate-Request':'1'},body:form});
    const data=await response.json().catch(()=>({detail:'Restore failed.'})); if (!response.ok) throw new Error(data.detail || 'Restore failed.');
    $('#restore-state').textContent=`Restored ${data.jobs} jobs and ${data.batches} batches. Previous database: ${data.previous_database}`; await refreshAdmin(); await v6LoadAdmin();
  } catch (error) { $('#restore-state').textContent=error.message; }
  finally { event.target.value=''; }
});

if (!window.PublicKeyCredential) {
  $('#passkey-login').hidden=true; $('#passkey-enroll').hidden=true;
}

setInterval(v6LoadAdmin, 7000);
window.addEventListener('focus', v6LoadAdmin);
setTimeout(v6LoadAdmin, 300);
