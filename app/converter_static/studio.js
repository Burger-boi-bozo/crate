/* Crate Studio — presentation/navigation only. Existing queue and media APIs stay untouched. */
(() => {
  "use strict";
  const main = document.querySelector("main");
  const intro = main?.querySelector(".intro");
  const converter = main?.querySelector(".converter-card");
  const supported = main?.querySelector(".supported");
  const downloads = main?.querySelector(".downloads-section");
  const transcript = main?.querySelector("#transcribe");
  const help = main?.querySelector(".help");
  const changelog = main?.querySelector(".changelog");
  if (!main || !intro || !converter || !supported || !downloads || !transcript) return;

  const topRight = document.querySelector(".top-right");
  if (topRight) {
    topRight.replaceChildren();
    const status = document.createElement("span");
    status.className = "studio-top-status";
    status.innerHTML = '<span class="studio-status-dot" aria-hidden="true"></span> SELF-HOSTED MEDIA TOOLS';
    const admin = document.createElement("a");
    admin.className = "studio-admin-link";
    admin.href = "/admin";
    admin.textContent = "Admin";
    topRight.append(status, admin);
  }

  intro.innerHTML =
    '<span class="studio-kicker"><span class="studio-kicker-line"></span> CRATE MEDIA STUDIO <span class="studio-kicker-version">/ 06</span></span>' +
    '<h1>Make media<br><span>work for you.</span></h1>' +
    '<p>Grab the highest-quality video or audio you need, or turn anything spoken into a clean, timestamped transcript.</p>';

  const tabs = document.createElement("nav");
  tabs.className = "studio-tabs";
  tabs.setAttribute("role", "tablist");
  tabs.setAttribute("aria-label", "Crate tools");
  tabs.innerHTML =
    '<a href="#download" class="studio-tab" id="studio-tab-download" role="tab" aria-controls="studio-panel-download" aria-selected="true" tabindex="0">' +
      '<span class="studio-tab-icon" aria-hidden="true"><svg viewBox="0 0 24 24" width="23" height="23" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12m-5-5 5 5 5-5M4 17v3h16v-3"/></svg></span>' +
      '<span class="studio-tab-copy"><strong>Download media</strong><small>Video · Audio · Convert</small></span><span class="studio-tab-number" aria-hidden="true">01</span></a>' +
    '<a href="#transcribe" class="studio-tab" id="studio-tab-transcribe" role="tab" aria-controls="studio-panel-transcribe" aria-selected="false" tabindex="-1">' +
      '<span class="studio-tab-icon" aria-hidden="true"><svg viewBox="0 0 24 24" width="23" height="23" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="3"/><path d="M8 10h8M8 14h5"/></svg></span>' +
      '<span class="studio-tab-copy"><strong>Transcribe</strong><small>Speech · Captions · Text</small></span><span class="studio-tab-number" aria-hidden="true">02</span></a>';
  intro.insertAdjacentElement("afterend", tabs);

  const downloadPanel = document.createElement("div");
  downloadPanel.id = "studio-panel-download";
  downloadPanel.className = "studio-panel";
  downloadPanel.setAttribute("role", "tabpanel");
  downloadPanel.setAttribute("aria-labelledby", "studio-tab-download");

  const transcribePanel = document.createElement("div");
  transcribePanel.id = "studio-panel-transcribe";
  transcribePanel.className = "studio-panel";
  transcribePanel.setAttribute("role", "tabpanel");
  transcribePanel.setAttribute("aria-labelledby", "studio-tab-transcribe");
  transcribePanel.hidden = true;

  // Keep original DOM nodes and their bound event handlers in place.
  tabs.after(downloadPanel, transcribePanel);
  [converter, supported, downloads, help, changelog].filter(Boolean).forEach(node => downloadPanel.append(node));
  transcribePanel.append(transcript);

  const cardHeader = converter.querySelector(".card-header");
  if (cardHeader) {
    const overline = cardHeader.querySelector(".step-number");
    if (overline) overline.textContent = "DOWNLOAD / CREATE";
    const note = cardHeader.querySelector(".card-note");
    if (note) note.textContent = "One link or a whole batch";
  }
  const conversionForm = converter.querySelector("#convert-form");
  if (conversionForm) {
    const label = conversionForm.querySelector('label[for="media-url"]');
    if (label) label.textContent = "What would you like to download?";
    const button = conversionForm.querySelector("#convert-button");
    if (button) {
      button.setAttribute("aria-label", "Start media download");
    }
  }
  const downloadsHeading = downloads.querySelector("#downloads-heading");
  if (downloadsHeading) {
    // Preserve the count node and update logic.
    const count = downloadsHeading.querySelector("#job-count");
    downloadsHeading.replaceChildren("Downloads ", count || document.createTextNode(""));
  }
  const downloadSectionTitle = downloads.querySelector(".section-heading .step-number");
  if (downloadSectionTitle) downloadSectionTitle.textContent = "YOUR LIBRARY";
  const transcriptHeading = transcript.querySelector("#transcript-heading");
  if (transcriptHeading) transcriptHeading.textContent = "Turn speech into text";
  const transcriptEyebrow = transcript.querySelector(".section-heading .step-number");
  if (transcriptEyebrow) transcriptEyebrow.textContent = "TRANSCRIPTION STUDIO";

  const tabDownload = tabs.querySelector("#studio-tab-download");
  const tabTranscribe = tabs.querySelector("#studio-tab-transcribe");
  const mapping = { download: [tabDownload, downloadPanel], transcribe: [tabTranscribe, transcribePanel] };
  let active = "";
  const fromHash = () => location.hash.toLowerCase() === "#transcribe" ? "transcribe" : "download";

  function select(which, {changeUrl = false, scroll = false} = {}) {
    const next = which === "transcribe" ? "transcribe" : "download";
    if (changeUrl && location.hash !== "#" + next) {
      history.pushState(null, "", "#" + next);
    }
    for (const [key, [tab, panel]] of Object.entries(mapping)) {
      const selected = key === next;
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
      tab.classList.toggle("is-selected", selected);
      panel.hidden = !selected;
    }
    document.title = next === "transcribe"
      ? "Transcribe audio & video — Crate"
      : "Download video & audio — Crate";
    const changed = active !== next;
    active = next;
    if (scroll && changed) {
      const behavior = matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
      tabs.scrollIntoView({behavior, block: "start"});
    }
  }

  for (const [name, [tab]] of Object.entries(mapping)) {
    tab.addEventListener("click", event => {
      event.preventDefault();
      select(name, {changeUrl: true, scroll: true});
    });
    tab.addEventListener("keydown", event => {
      let target;
      if (event.key === "ArrowRight" || event.key === "ArrowLeft") target = name === "download" ? "transcribe" : "download";
      if (event.key === "Home") target = "download";
      if (event.key === "End") target = "transcribe";
      if (!target) return;
      event.preventDefault();
      select(target, {changeUrl: true});
      mapping[target][0].focus();
    });
  }

  document.querySelectorAll('a[href="#transcribe"], a[href="#download"]').forEach(anchor => {
    if (anchor.getAttribute("role") === "tab") return;
    anchor.addEventListener("click", event => {
      event.preventDefault();
      select(anchor.getAttribute("href").slice(1), {changeUrl: true, scroll: true});
    });
  });
  window.addEventListener("hashchange", () => select(fromHash()));
  window.addEventListener("popstate", () => select(fromHash()));
  const initial = fromHash();
  select(initial);
  if (initial === "transcribe") {
    window.addEventListener("load", () => {
      requestAnimationFrame(() => tabs.scrollIntoView({block: "start"}));
    }, {once: true});
  }
  document.documentElement.classList.add("studio-ready");
})();