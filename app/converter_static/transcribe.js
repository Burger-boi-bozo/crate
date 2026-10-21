/* Crate Transcribe: same-origin UI with persistent, owner-scoped job history. */
(() => {
  const host = document.querySelector(".downloads-section");
  if (!host) return;
  const section = document.createElement("section");
  section.className = "transcript-section";
  section.id = "transcribe";
  section.setAttribute("aria-labelledby", "transcript-heading");
  section.innerHTML =
    '<div class="section-heading"><div><span class="step-number">03 / TRANSCRIBE</span><h2 id="transcript-heading">Speech to text</h2></div><span class="transcribe-pill">Local AI</span></div>' +
    '<p class="transcribe-description">Paste a supported media link or upload a file. Crate retrieves the audio, transcribes it locally, and creates timestamped subtitles automatically.</p>' +
    '<form id="transcribe-url-form" class="transcribe-link-form"><h3>Transcribe a media link</h3>' +
      '<label for="transcribe-url">Paste a YouTube, video, podcast, or direct audio link</label>' +
      '<input id="transcribe-url" type="url" inputmode="url" autocomplete="url" placeholder="https://www.youtube.com/watch?v=..." maxlength="2048" required>' +
      '<div class="transcribe-link-controls"><label for="transcribe-url-lang">Language</label>' +
      '<select id="transcribe-url-lang"><option value="auto">Auto-detect</option><option value="en">English</option><option value="es">Spanish</option><option value="fr">French</option><option value="de">German</option><option value="pt">Portuguese</option><option value="it">Italian</option><option value="ja">Japanese</option><option value="ko">Korean</option><option value="zh">Chinese</option><option value="ru">Russian</option><option value="ar">Arabic</option><option value="hi">Hindi</option></select>' +
      '<button class="primary" type="submit">Transcribe link</button></div>' +
      '<small>Crate downloads the audio in the background. Results stay in your transcription queue if you close this page.</small></form>' +
    '<div class="transcribe-panels">' +
      '<form id="transcribe-upload-form" class="transcribe-panel"><h3>Upload media</h3>' +
        '<label for="transcribe-file">Audio or video file</label><input type="file" id="transcribe-file" accept="audio/*,video/*,.mkv,.mka,.m4b,.opus,.flac,.aac,.webm,.avi,.mov,.gif,.webp" required>' +
        '<label for="transcribe-upload-lang">Spoken language</label><select id="transcribe-upload-lang" class="transcribe-lang"><option value="auto">Detect automatically</option><option value="en">English</option><option value="es">Spanish</option><option value="fr">French</option><option value="de">German</option><option value="pt">Portuguese</option><option value="ja">Japanese</option><option value="ko">Korean</option><option value="zh">Chinese</option></select>' +
        '<button class="primary" type="submit">Transcribe upload</button></form>' +
      '<form id="transcribe-existing-form" class="transcribe-panel"><h3>Use a Crate download</h3>' +
        '<label for="transcribe-existing">Completed audio or video</label><select id="transcribe-existing" required><option value="">Loading completed downloads...</option></select>' +
        '<label for="transcribe-existing-lang">Spoken language</label><select id="transcribe-existing-lang" class="transcribe-lang"><option value="auto">Detect automatically</option><option value="en">English</option><option value="es">Spanish</option><option value="fr">French</option><option value="de">German</option><option value="pt">Portuguese</option><option value="ja">Japanese</option><option value="ko">Korean</option><option value="zh">Chinese</option></select>' +
        '<button class="primary" type="submit">Transcribe download</button></form>' +
    '</div><p id="transcribe-notice" role="status" aria-live="polite"></p>' +
    '<div class="transcribe-heading"><h3>Transcription queue</h3><button type="button" id="transcribe-refresh" class="text-button">Refresh</button></div>' +
    '<div id="transcribe-history" aria-live="polite"><p class="transcribe-description">Loading transcription jobs...</p></div>';
  host.insertAdjacentElement("afterend", section);
  if (window.location.hash === "#transcribe") {
    window.addEventListener("load", () => section.scrollIntoView({block: "start"}), {once: true});
  }

  const $ = (sel) => section.querySelector(sel);
  const history = $("#transcribe-history");
  const message = $("#transcribe-notice");
  const formatDate = (n) => new Date(n * 1000).toLocaleString();
  async function api(path, opts = {}) {
    const response = await fetch(path, {credentials: "same-origin", ...opts,
      headers: {"X-Crate-Request": "1", ...(opts.headers || {})}});
    if (!response.ok) {
      let issue;
      try { issue = (await response.json()).detail; } catch (_) {}
      throw new Error(typeof issue === "string" ? issue : ("Request failed (" + response.status + ")"));
    }
    return response.status === 204 ? null : response.json();
  }
  function inform(text, error = false) {
    message.textContent = text;
    message.classList.toggle("transcribe-error", error);
  }
  function item(tag, klass, text) {
    const el = document.createElement(tag);
    if (klass) el.className = klass;
    if (text !== undefined) el.textContent = text;
    return el;
  }
  async function loadExisting() {
    const select = $("#transcribe-existing");
    try {
      const jobs = await api("/api/jobs?status=ready");
      const current = select.value;
      select.replaceChildren(new Option("Choose a completed download", ""));
      for (const job of jobs) {
        if (["gif", "webp"].includes(job.format)) continue;
        select.append(new Option((job.filename || job.title || job.id).slice(0, 110), job.id));
      }
      if (current && Array.from(select.options).some(o => o.value === current)) select.value = current;
    } catch (_) {
      select.replaceChildren(new Option("Refresh Crate session to load downloads", ""));
    }
  }
  function render(jobs) {
    history.replaceChildren();
    if (!jobs.length) {
      history.append(item("p", "transcribe-description", "No transcriptions yet. Paste a media URL, upload a file, or select a completed download."));
      return;
    }
    for (const job of jobs) {
      const wrap = item("article", "transcribe-job");
      const top = item("div", "transcribe-job-top");
      const heading = item("strong", "", job.filename);
      heading.title = job.filename;
      top.append(heading, item("span", "transcribe-status status-" + job.status, job.status === "waiting_download" ? "Downloading media" : job.status));
      wrap.append(top);
      wrap.append(item("p", "transcribe-job-info", formatDate(job.created_at) + " · " +
        (job.detected_language || job.language).toUpperCase() + " · " + job.segment_count + " segments"));
      if (["waiting_download", "queued", "extracting", "transcribing"].includes(job.status)) {
        const progress = item("progress", "transcribe-progress");
        progress.max = 100;
        progress.value = job.progress;
        progress.setAttribute("aria-label", "Transcription progress");
        wrap.append(progress);
        wrap.append(item("p", "transcribe-job-info", (job.message || "Working") + " · " + job.progress + "%"));
      } else if (job.status === "failed") {
        wrap.append(item("p", "transcribe-error", job.error || "Transcription failed"));
      } else {
        const actions = item("div", "transcribe-actions");
        for (const ext of ["txt", "srt", "vtt", "json"]) {
          const a = item("a", "transcribe-export", ext.toUpperCase());
          a.href = "/api/transcriptions/" + encodeURIComponent(job.id) + "/file?format=" + ext;
          actions.append(a);
        }
        wrap.append(actions);
      }
      if (["completed", "failed", "queued", "waiting_download"].includes(job.status)) {
        const remove = item("button", "text-button transcribe-remove", "Remove");
        remove.type = "button";
        remove.addEventListener("click", async () => {
          if (!confirm("Remove this transcript and its exported files?")) return;
          try { await api("/api/transcriptions/" + encodeURIComponent(job.id), {method: "DELETE"}); await load(); }
          catch (e) { inform(e.message, true); }
        });
        wrap.append(remove);
      }
      history.append(wrap);
    }
  }
  async function load() {
    try {
      const [cap, jobs] = await Promise.all([
        api("/api/transcriptions/capabilities"), api("/api/transcriptions")]);
      section.classList.toggle("transcribe-unavailable", !cap.available);
      if (!cap.available) inform("Transcription worker is offline or initializing.", true);
      else if (message.textContent === "Transcription worker is offline or initializing.") inform("");
      render(jobs);
    } catch (e) { inform(e.message, true); }
  }
  $("#transcribe-url-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.currentTarget.querySelector("button[type=submit]");
    const url = $("#transcribe-url").value.trim();
    if (!url) return;
    button.disabled = true;
    inform("Sending link to Crate's media downloader...");
    try {
      await api("/api/transcriptions/from-url", {method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({url, language: $("#transcribe-url-lang").value})});
      $("#transcribe-url").value = "";
      inform("Link queued. Crate will download and transcribe it automatically.");
      await load();
    } catch (e) { inform(e.message, true); }
    finally { button.disabled = false; }
  });
  $("#transcribe-upload-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.currentTarget.querySelector("button[type=submit]");
    const file = $("#transcribe-file").files[0];
    if (!file) return;
    button.disabled = true;
    inform("Uploading media to Crate...");
    try {
      const payload = new FormData();
      payload.append("file", file);
      payload.append("language", $("#transcribe-upload-lang").value);
      await api("/api/transcriptions/upload", {method: "POST", body: payload});
      $("#transcribe-file").value = "";
      inform("Upload queued for transcription.");
      await load();
    } catch (e) { inform(e.message, true); }
    finally { button.disabled = false; }
  });
  $("#transcribe-existing-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.currentTarget.querySelector("button[type=submit]");
    if (!$("#transcribe-existing").value) return;
    button.disabled = true;
    try {
      await api("/api/transcriptions/from-job", {method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({job_id: $("#transcribe-existing").value,
                              language: $("#transcribe-existing-lang").value})});
      inform("Download queued for transcription.");
      await load();
    } catch (e) { inform(e.message, true); }
    finally { button.disabled = false; }
  });
  $("#transcribe-refresh").addEventListener("click", () => { loadExisting(); load(); });
  loadExisting();
  load();
  setInterval(() => {
    if (document.visibilityState !== "visible") return;
    load();
    loadExisting();
  }, 6000);
})();
