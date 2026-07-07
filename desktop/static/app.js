const state = {
  source: "",
  format: "mp4",
  formats: [],
  activeJobId: null,
  ws: null,
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...opts.headers },
    ...opts,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || res.statusText);
  }
  return res.json();
}

function setSource(value) {
  state.source = value.trim();
  $("#source-input").value = state.source;
  $("#btn-convert").disabled = !state.source;
}

function selectedFormat() {
  return state.format;
}

function gatherOptions() {
  return {
    source: state.source,
    format: selectedFormat(),
    prores_profile: $("#prores-profile").value,
    forensic_mode: $("#opt-forensic").checked,
    preserve_source: $("#opt-preserve").checked,
    normalize_lufs: $("#opt-normalize").checked,
    embed_subtitles: $("#opt-subs").checked,
    strict_hdr: $("#opt-strict-hdr").checked,
    strict_dolby_vision: $("#opt-strict-dv").checked,
    strict_surround: $("#opt-strict-surround").checked,
    auto_classify: true,
    upload_after_verify: false,
  };
}

function renderFormats(formats) {
  const grid = $("#format-grid");
  grid.innerHTML = "";
  formats.forEach((fmt) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `fmt-btn${fmt === state.format ? " active" : ""}`;
    btn.textContent = fmt.toUpperCase();
    btn.addEventListener("click", () => {
      state.format = fmt;
      $$(".fmt-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      $("#prores-row").classList.toggle("hidden", fmt !== "prores");
    });
    grid.appendChild(btn);
  });
  $("#prores-row").classList.toggle("hidden", state.format !== "prores");
}

function renderProbe(data) {
  $("#probe-empty").classList.add("hidden");
  const el = $("#probe-data");
  el.classList.remove("hidden");

  const badges = [];
  if (data.video?.color_science === "hdr10" || data.video?.color_science === "hlg" || data.is_hdr) {
    badges.push('<span class="badge badge-hdr">HDR</span>');
  }
  if (data.video?.color_science === "dolby_vision") {
    badges.push('<span class="badge badge-dv">Dolby Vision</span>');
  }
  if ((data.audio?.channels || data.audio_channels || 0) >= 6) {
    badges.push('<span class="badge badge-surround">5.1+</span>');
  }

  const rows = [
    ["Title", data.title || "—"],
    ["Type", data.is_remote ? "Remote URL" : "Local file"],
    ["Container", data.format || "—"],
    ["Duration", data.duration_sec != null ? `${data.duration_sec.toFixed(1)}s` : (data.duration ? `${data.duration}s` : "—")],
    ["Video", data.video?.codec ? `${data.video.codec} ${data.video.resolution || ""}` : (data.has_video === false ? "None" : "—")],
    ["Color", (data.video?.color_science || (data.is_hdr ? "hdr" : "sdr")) + badges.join("")],
    ["Audio", data.audio?.codec ? `${data.audio.codec} ${data.audio.channels}ch` : `${data.audio_channels || "—"}ch`],
    ["Subtitles", data.subtitles != null ? data.subtitles : (data.has_subtitles ? "Yes" : "No")],
  ];

  el.innerHTML = rows.map(([k, v]) => `<div class="probe-row"><span>${k}</span><span>${v}</span></div>`).join("");
}

async function doProbe() {
  if (!state.source) return;
  try {
    const data = await api("/api/probe", { method: "POST", body: JSON.stringify({ source: state.source }) });
    renderProbe(data);
  } catch (e) {
    alert(`Inspect failed: ${e.message}`);
  }
}

async function doPlan() {
  if (!state.source) return;
  try {
    const data = await api("/api/plan", { method: "POST", body: JSON.stringify(gatherOptions()) });
    $("#plan-panel").classList.remove("hidden");
    if (data.remote) {
      $("#plan-output").textContent = `Remote: ${data.title}\nOutput format: ${data.output_format}\n\n${data.note}`;
    } else {
      $("#plan-output").textContent = JSON.stringify(data, null, 2);
    }
  } catch (e) {
    alert(`Plan preview failed: ${e.message}`);
  }
}

function setProgress(job) {
  $("#progress-panel").classList.remove("hidden");
  const pct = Math.round((job.progress || 0) * 100);
  $("#progress-pct").textContent = `${pct}%`;
  const ring = $("#progress-ring");
  const circumference = 326.7;
  ring.style.strokeDashoffset = circumference - (pct / 100) * circumference;
  $("#progress-msg").textContent = job.message || "Processing…";
  $("#tel-fps").textContent = job.fps || "—";
  $("#tel-speed").textContent = job.speed || "—";
  $("#tel-time").textContent = job.elapsed || "—";

  const box = $("#result-box");
  if (job.status === "completed" && job.result) {
    box.classList.remove("hidden", "fail");
    box.innerHTML = [
      `<strong>✓ Complete</strong>`,
      `Output: ${job.result.output_path}`,
      `SHA-256: ${job.result.sha256}`,
      job.result.evidence_bundle ? `Evidence: ${job.result.evidence_bundle}` : "",
    ].filter(Boolean).join("<br>");
  } else if (job.status === "failed") {
    box.classList.remove("hidden");
    box.classList.add("fail");
    box.innerHTML = `<strong>✗ Failed</strong><br>${job.error || "Unknown error"}`;
  }
}

function connectJobWs(jobId) {
  if (state.ws) {
    state.ws.close();
    state.ws = null;
  }
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/jobs/${jobId}`);
  state.ws = ws;
  ws.onmessage = (ev) => {
    const job = JSON.parse(ev.data);
    setProgress(job);
    if (job.status === "completed" || job.status === "failed") {
      loadJobs();
      ws.close();
    }
  };
}

async function doConvert() {
  if (!state.source) return;
  $("#btn-convert").disabled = true;
  $("#result-box").classList.add("hidden");
  try {
    const job = await api("/api/convert", { method: "POST", body: JSON.stringify(gatherOptions()) });
    state.activeJobId = job.id;
    setProgress(job);
    connectJobWs(job.id);
  } catch (e) {
    alert(`Convert failed: ${e.message}`);
  } finally {
    $("#btn-convert").disabled = !state.source;
  }
}

async function loadJobs() {
  const jobs = await api("/api/jobs");
  const list = $("#jobs-list");
  if (!jobs.length) {
    list.innerHTML = '<div class="empty-state">No jobs yet. Start a conversion from the Convert tab.</div>';
    return;
  }
  list.innerHTML = jobs.map((j) => `
    <div class="job-card">
      <div>
        <div><strong>${j.output_format.toUpperCase()}</strong> <span class="job-status ${j.status}">${j.status}</span></div>
        <div class="job-source">${j.source}</div>
      </div>
      <div class="job-status ${j.status}">${Math.round((j.progress || 0) * 100)}%</div>
    </div>
  `).join("");
}

function renderStandards(standards) {
  const items = [
    { title: "EBU R128 Loudness", desc: "ITU-R BS.1770 broadcast normalization at -23 LUFS integrated, -1.5 dBTP true peak when enabled." },
    { title: "ISOBMFF Fast Start", desc: "moov atom at front of MP4/MOV for streaming-compatible delivery (ISO/IEC 14496-12)." },
    { title: "Custody Digests", desc: "SHA-256 primary integrity hash plus MD5 legacy digest at each pipeline boundary (SWGDE practice)." },
    { title: "Metadata Preservation", desc: "Container tags and chapters forwarded on remux and transcode where FFmpeg supports." },
    { title: "Dolby Vision Strict", desc: "Enhancement layer preserved via bitstream copy only — transcode refused rather than silent degradation." },
    { title: "Hardware Encoders", desc: "Apple VideoToolbox H.264/HEVC/ProRes/AAC when available; software fallback otherwise." },
  ];
  $("#standards-grid").innerHTML = items.map((s) => `
    <div class="std-card glass">
      <h4>${s.title}</h4>
      <p>${s.desc}</p>
    </div>
  `).join("");
}

async function loadDoctor() {
  const data = await api("/api/doctor");
  const lines = [];
  lines.push(`Suite ${data.suite_version} · Desktop ${data.desktop_version}`);
  lines.push("");
  data.checks.forEach((c) => {
    lines.push(`${c.ok ? "✓" : "✗"} ${c.name}: ${c.path}`);
  });
  lines.push("");
  lines.push("Hardware encoders:");
  Object.entries(data.hardware).forEach(([k, v]) => {
    lines.push(`  ${v ? "✓" : "·"} ${k}`);
  });
  $("#doctor-output").innerHTML = lines.map((l) => {
    if (l.startsWith("✓")) return `<div class="doctor-ok">${l}</div>`;
    if (l.startsWith("✗")) return `<div class="doctor-bad">${l}</div>`;
    return `<div>${l}</div>`;
  }).join("");
}

async function initHealth() {
  try {
    const health = await api("/api/health");
    const doctor = await api("/api/doctor");
    const allOk = doctor.checks.every((c) => c.ok);
    const pill = $("#health-pill");
    pill.textContent = allOk ? "Ready" : "Missing tools";
    pill.className = `pill ${allOk ? "pill-ok" : "pill-bad"}`;
    $("#app-version").textContent = `v${health.version}`;
    const fmts = await api("/api/formats");
    state.formats = fmts.formats;
    renderFormats(fmts.formats);
    renderStandards(doctor.standards);
  } catch {
    $("#health-pill").textContent = "Offline";
    $("#health-pill").className = "pill pill-bad";
  }
}

function setupNav() {
  $$(".nav-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      $$(".nav-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      const view = btn.dataset.view;
      $$(".view").forEach((v) => v.classList.remove("active"));
      $(`#view-${view}`).classList.add("active");
      if (view === "jobs") loadJobs();
      if (view === "system") loadDoctor();
    });
  });
}

function setupDropzone() {
  const zone = $("#dropzone");
  ["dragenter", "dragover"].forEach((ev) => {
    zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add("dragover"); });
  });
  ["dragleave", "drop"].forEach((ev) => {
    zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.remove("dragover"); });
  });
  zone.addEventListener("drop", (e) => {
    const file = e.dataTransfer?.files?.[0];
    if (file) {
      setSource(file.path || file.name);
      doProbe();
    }
  });
  zone.addEventListener("click", () => {
    if (window.pywebview?.api?.pick_files) {
      window.pywebview.api.pick_files().then((paths) => {
        if (paths?.[0]) { setSource(paths[0]); doProbe(); }
      });
    }
  });
}

function setupEvents() {
  $("#source-input").addEventListener("input", (e) => {
    setSource(e.target.value);
  });
  $("#source-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") doProbe();
  });
  $("#btn-probe").addEventListener("click", doProbe);
  $("#btn-plan").addEventListener("click", doPlan);
  $("#btn-convert").addEventListener("click", doConvert);
  $("#btn-browse").addEventListener("click", () => {
    if (window.pywebview?.api?.pick_files) {
      window.pywebview.api.pick_files().then((paths) => {
        if (paths?.[0]) { setSource(paths[0]); doProbe(); }
      });
    } else {
      alert("Use the path field or drag-and-drop. Native file picker requires the desktop app.");
    }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  setupNav();
  setupDropzone();
  setupEvents();
  initHealth();
});
