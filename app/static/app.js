const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const state = { view: "all", downloads: [], system: null, query: "" };
const titles = {
  all: ["Overview", "All downloads", "Everything currently moving and previously downloaded."],
  active: ["Active", "Active downloads", "Downloads in progress, paused, or waiting."],
  queued: ["Queue", "Waiting in queue", "Higher priority items start first."],
  finished: ["History", "Download history", "Completed, failed, and cancelled downloads."],
};

function escapeHtml(value = "") {
  return String(value).replace(/[&<>'"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[c]);
}

function formatBytes(bytes, perSecond = false) {
  if (!bytes) return perSecond ? "0 B/s" : "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / Math.pow(1024, exponent);
  return `${value >= 10 || exponent === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[exponent]}${perSecond ? "/s" : ""}`;
}

function formatEta(seconds) {
  if (seconds == null) return "—";
  if (seconds < 60) return `${seconds}s left`;
  if (seconds < 3600) return `${Math.ceil(seconds / 60)}m left`;
  return `${Math.floor(seconds / 3600)}h ${Math.ceil((seconds % 3600) / 60)}m left`;
}

function relativeTime(value) {
  if (!value) return "—";
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 1000));
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return new Date(value).toLocaleDateString();
}

function displayName(item) {
  if (item.name) return item.name;
  try {
    const url = new URL(item.url);
    return decodeURIComponent(url.pathname.split("/").filter(Boolean).pop() || url.hostname);
  } catch { return item.url.startsWith("magnet:") ? "Torrent download" : item.url; }
}

function fileType(item) {
  const name = displayName(item);
  const extension = name.includes(".") ? name.split(".").pop().slice(0, 4) : item.category.slice(0, 3);
  return escapeHtml(extension.toUpperCase());
}

function actions(item) {
  const buttons = [];
  if (["downloading", "queued"].includes(item.status)) buttons.push(`<button class="action-button" data-action="pause" data-id="${item.id}" title="Pause">Ⅱ</button>`);
  if (["paused", "failed", "cancelled"].includes(item.status)) buttons.push(`<button class="action-button" data-action="resume" data-id="${item.id}" title="Resume or retry">▶</button>`);
  if (["downloading", "queued", "paused"].includes(item.status)) buttons.push(`<button class="action-button" data-action="cancel" data-id="${item.id}" title="Cancel">■</button>`);
  if (item.status === "completed" && item.relative_path) buttons.push(`<a class="action-button" href="/api/downloads/${item.id}/file" title="Download file">⇩</a>`);
  buttons.push(`<button class="action-button danger" data-action="delete" data-id="${item.id}" title="Remove from history">×</button>`);
  return buttons.join("");
}

function render() {
  const [page, title, subtitle] = titles[state.view];
  $("#pageTitle").textContent = page;
  $("#listTitle").textContent = title;
  $("#listSubtitle").textContent = subtitle;
  $$(".nav-item[data-view]").forEach(button => button.classList.toggle("active", button.dataset.view === state.view));

  const list = state.downloads;
  const downloading = list.filter(item => item.status === "downloading");
  const active = list.filter(item => ["downloading", "queued", "paused"].includes(item.status));
  const queued = list.filter(item => item.status === "queued");
  const completed = list.filter(item => item.status === "completed");
  $("#activeStat").textContent = downloading.length;
  $("#activeBadge").textContent = active.length;
  $("#queueStat").textContent = queued.length;
  $("#doneStat").textContent = completed.length;
  $("#speedStat").textContent = formatBytes(downloading.reduce((sum, item) => sum + item.speed_bytes, 0), true);

  let filtered = list;
  if (state.view === "active") filtered = list.filter(item => ["downloading", "queued", "paused"].includes(item.status));
  if (state.view === "queued") filtered = queued;
  if (state.view === "finished") filtered = list.filter(item => ["completed", "failed", "cancelled"].includes(item.status));
  if (state.query) {
    const q = state.query.toLowerCase();
    filtered = filtered.filter(item => `${displayName(item)} ${item.url} ${item.category}`.toLowerCase().includes(q));
  }

  $("#emptyState").classList.toggle("hidden", filtered.length > 0);
  $("#downloadList").innerHTML = filtered.map(item => {
    const progress = Math.max(0, Math.min(100, item.progress || 0));
    const progressDetail = item.status === "downloading"
      ? `${formatBytes(item.speed_bytes, true)} · ${formatEta(item.eta_seconds)}`
      : item.status === "completed" ? relativeTime(item.finished_at) : item.status === "queued" ? `Priority ${item.priority}` : relativeTime(item.updated_at);
    const progressClass = item.status === "failed" ? "failed" : item.status === "paused" ? "paused" : "";
    return `<article class="download-row">
      <div class="file-info"><div class="file-icon">${fileType(item)}</div><div class="file-text"><strong title="${escapeHtml(displayName(item))}">${escapeHtml(displayName(item))}</strong><small><span>${escapeHtml(item.category)}</span><span class="tool-chip">${escapeHtml(item.tool)}</span>${item.total_bytes ? `<span>${formatBytes(item.total_bytes)}</span>` : ""}</small></div></div>
      <div class="progress-wrap"><div class="progress-head"><span>${Math.round(progress)}%</span><span>${escapeHtml(progressDetail)}</span></div><div class="progress-track"><i class="${progressClass}" style="width:${progress}%"></i></div></div>
      <span class="status ${item.status}" title="${escapeHtml(item.error || "")}">${item.status}</span>
      <div class="row-actions">${actions(item)}</div>
    </article>`;
  }).join("");
}

async function api(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json", ...(options.headers || {}) }, ...options });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${response.status})`);
  }
  return response.status === 204 ? null : response.json();
}

async function refresh(silent = true) {
  try {
    state.downloads = await api("/api/downloads");
    render();
  } catch (error) { if (!silent) toast(error.message, true); }
}

async function loadSystem() {
  try {
    state.system = await api("/api/system");
    const { storage } = state.system;
    const percent = storage.total ? Math.round(storage.used / storage.total * 100) : 0;
    $("#storagePercent").textContent = `${percent}%`;
    $("#storageFill").style.width = `${percent}%`;
    $("#storageText").textContent = `${formatBytes(storage.free)} free of ${formatBytes(storage.total)}`;
    $("#settingsBody").innerHTML = `
      <div class="settings-row"><b>Parallel downloads</b><span>${state.system.max_concurrent}</span></div>
      <div class="settings-row"><b>Protected with login</b><span>${state.system.auth_enabled ? "Yes" : "No"}</span></div>
      ${Object.entries(state.system.tools).map(([name, ready]) => `<div class="settings-row"><b>${name}</b><span class="tool-state ${ready ? "" : "missing"}">${ready ? "Installed" : "Missing"}</span></div>`).join("")}
      <div class="settings-row"><b>Downloads path</b><span>${escapeHtml(state.system.download_dir)}</span></div>`;
  } catch (error) { toast(error.message, true); }
}

function toast(message, error = false) {
  const element = document.createElement("div");
  element.className = `toast${error ? " error" : ""}`;
  element.textContent = message;
  $("#toastRegion").append(element);
  setTimeout(() => element.remove(), 4000);
}

async function submitDownload(event) {
  event.preventDefault();
  const urls = $("#urlInput").value.split("\n").map(value => value.trim()).filter(Boolean);
  if (!urls.length) return;
  const payload = {
    urls,
    tool: $("#toolInput").value,
    category: $("#categoryInput").value,
    priority: Number($("#priorityInput").value),
    options: { audio_only: $("#mediaMode").value === "audio", audio_format: "mp3", playlist: $("#playlistInput").checked },
  };
  const button = $("#submitDownload");
  button.disabled = true;
  button.textContent = "Adding…";
  try {
    await api("/api/downloads/bulk", { method: "POST", body: JSON.stringify(payload) });
    $("#addDialog").close();
    $("#addForm").reset();
    toast(`${urls.length} download${urls.length === 1 ? "" : "s"} added to the queue`);
    await refresh(false);
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; button.textContent = "Add to queue"; }
}

async function handleAction(button) {
  const { action, id } = button.dataset;
  try {
    if (action === "delete") {
      if (!confirm("Remove this item from download history? The downloaded file will be kept.")) return;
      await api(`/api/downloads/${id}`, { method: "DELETE" });
    } else {
      await api(`/api/downloads/${id}/${action}`, { method: "POST" });
    }
    await refresh(false);
  } catch (error) { toast(error.message, true); }
}

function openAdd() { $("#addDialog").showModal(); setTimeout(() => $("#urlInput").focus(), 50); }

$("#addButton").addEventListener("click", openAdd);
$$(".open-add").forEach(button => button.addEventListener("click", openAdd));
$$('[data-close]').forEach(button => button.addEventListener("click", () => $("#addDialog").close()));
$("#addForm").addEventListener("submit", submitDownload);
$("#openSettings").addEventListener("click", () => $("#settingsDialog").showModal());
$$('[data-close-settings]').forEach(button => button.addEventListener("click", () => $("#settingsDialog").close()));
$("#refreshButton").addEventListener("click", () => refresh(false));
$("#searchInput").addEventListener("input", event => { state.query = event.target.value; render(); });
$$(".nav-item[data-view]").forEach(button => button.addEventListener("click", () => { state.view = button.dataset.view; render(); }));
$("#downloadList").addEventListener("click", event => { const button = event.target.closest("[data-action]"); if (button) handleAction(button); });
$("#urlInput").addEventListener("input", async event => {
  const first = event.target.value.split("\n").find(value => value.trim());
  const detected = $("#detectedTool");
  if (!first || $("#toolInput").value !== "auto") return detected.classList.add("hidden");
  try { const result = await api(`/api/detect?url=${encodeURIComponent(first.trim())}`); detected.textContent = `Auto detection will use ${result.tool} for this link.`; detected.classList.remove("hidden"); } catch {}
});

loadSystem();
refresh(false);
setInterval(() => refresh(true), 1500);
setInterval(loadSystem, 30000);

