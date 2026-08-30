"use strict";

const KNOWN_GRADIENTS = new Set([
  "happy", "sad", "romantic", "driving", "long_drive_slow",
  "party", "workout", "chill", "focus", "nostalgic", "devotional",
]);

const state = {
  moods: [],
  currentMood: null,
  queue: [],
  index: 0,
  bgToggle: false, // which bg layer is currently visible
  pollTimer: null,
};

const el = {
  moods: document.getElementById("moods"),
  audio: document.getElementById("audio"),
  art: document.getElementById("art"),
  artGlow: document.getElementById("art-glow"),
  title: document.getElementById("track-title"),
  artist: document.getElementById("track-artist"),
  seek: document.getElementById("seek-bar"),
  timeCur: document.getElementById("time-current"),
  timeTot: document.getElementById("time-total"),
  play: document.getElementById("play-btn"),
  iconPlay: document.getElementById("icon-play"),
  iconPause: document.getElementById("icon-pause"),
  prev: document.getElementById("prev-btn"),
  next: document.getElementById("next-btn"),
  shuffle: document.getElementById("shuffle-btn"),
  volume: document.getElementById("volume-bar"),
  queueList: document.getElementById("queue-list"),
  queueCount: document.getElementById("queue-count"),
  queueEmpty: document.getElementById("queue-empty"),
  bgA: document.getElementById("bg-a"),
  bgB: document.getElementById("bg-b"),
  indexBtn: document.getElementById("index-btn"),
  healthBtn: document.getElementById("health-btn"),
  statusNav: document.getElementById("status-navidrome"),
  statusLLM: document.getElementById("status-llm"),
  statusClap: document.getElementById("status-clap"),
  scoresBtn: document.getElementById("scores-btn"),
  scoresOverlay: document.getElementById("scores-overlay"),
  scoresClose: document.getElementById("scores-close"),
  scoresFilter: document.getElementById("scores-filter"),
  scoresHeadRow: document.getElementById("scores-head-row"),
  scoresBody: document.getElementById("scores-body"),
  scoresMeta: document.getElementById("scores-meta"),
  offlineBtn: document.getElementById("offline-btn"),
  overlay: document.getElementById("overlay"),
  overlayStart: document.getElementById("overlay-start"),
  overlayClose: document.getElementById("overlay-close"),
  overlayText: document.getElementById("overlay-text"),
  overlayTitle: document.getElementById("overlay-title"),
  progressFill: document.getElementById("progress-fill"),
  progressLabel: document.getElementById("progress-label"),
};

let shuffleOn = false;

// --- Helpers ---------------------------------------------------------------
function fmtTime(sec) {
  if (!isFinite(sec) || sec < 0) return "0:00";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

// --- Background --------------------------------------------------------------
function setMoodBackground(slug) {
  const cls = KNOWN_GRADIENTS.has(slug) ? `grad-${slug}` : "grad-default";
  const show = state.bgToggle ? el.bgA : el.bgB;
  const hide = state.bgToggle ? el.bgB : el.bgA;
  show.className = `bg-layer ${cls}`;
  // force reflow so the animation restarts cleanly, then fade in
  void show.offsetWidth;
  show.classList.add("visible");
  hide.classList.remove("visible");
  state.bgToggle = !state.bgToggle;
  document.body.dataset.mood = slug;
}

// --- Moods -------------------------------------------------------------------
async function loadMoods() {
  const data = await api("/api/moods");
  state.moods = data.moods;
  el.moods.innerHTML = "";
  for (const mood of state.moods) {
    const btn = document.createElement("button");
    btn.className = "mood-pill";
    btn.textContent = mood.label;
    btn.dataset.slug = mood.slug;
    btn.addEventListener("click", () => selectMood(mood.slug));
    el.moods.appendChild(btn);
  }
}

async function selectMood(slug) {
  state.currentMood = slug;
  for (const btn of el.moods.children) {
    btn.classList.toggle("active", btn.dataset.slug === slug);
  }
  setMoodBackground(slug);

  try {
    const data = await api(`/api/radio?mood=${encodeURIComponent(slug)}&limit=60`);
    if (!data.tracks.length) {
      el.queueEmpty.textContent =
        "No scored songs for this mood yet. Run “Rebuild library” first.";
      el.queueEmpty.classList.remove("hidden");
      openOverlay();
      return;
    }
    state.queue = data.tracks;
    state.index = 0;
    saveQueueLocal(slug);
    renderQueue();
    playCurrent();
  } catch (err) {
    el.queueEmpty.textContent = "Couldn’t load songs. Is the library indexed?";
    el.queueEmpty.classList.remove("hidden");
  }
}

// --- Queue / playback --------------------------------------------------------
function renderQueue() {
  el.queueList.innerHTML = "";
  el.queueEmpty.classList.toggle("hidden", state.queue.length > 0);
  el.queueCount.textContent = state.queue.length ? `${state.queue.length} songs` : "";

  state.queue.forEach((track, i) => {
    const li = document.createElement("li");
    li.className = "queue-item" + (i === state.index ? " playing" : "");
    li.innerHTML = `
      <img class="queue-thumb" src="${track.cover_url}" alt="" loading="lazy" />
      <div class="queue-info">
        <div class="queue-song"></div>
        <div class="queue-artist"></div>
      </div>`;
    li.querySelector(".queue-song").textContent = track.title;
    li.querySelector(".queue-artist").textContent = track.artist;
    li.addEventListener("click", () => { state.index = i; playCurrent(); });
    el.queueList.appendChild(li);
  });
}

function playCurrent() {
  const track = state.queue[state.index];
  if (!track) return;
  el.audio.src = track.stream_url;
  el.audio.play().catch(() => {});
  el.title.textContent = track.title;
  el.artist.textContent = track.artist;
  el.art.src = track.cover_url;
  el.art.onerror = () => { el.art.removeAttribute("src"); };
  renderQueue();
}

function next() {
  if (!state.queue.length) return;
  if (shuffleOn) {
    state.index = Math.floor(Math.random() * state.queue.length);
  } else {
    state.index = (state.index + 1) % state.queue.length;
  }
  playCurrent();
}

function prev() {
  if (!state.queue.length) return;
  if (el.audio.currentTime > 3) { el.audio.currentTime = 0; return; }
  state.index = (state.index - 1 + state.queue.length) % state.queue.length;
  playCurrent();
}

// --- Player controls ---------------------------------------------------------
el.play.addEventListener("click", () => {
  if (el.audio.paused) el.audio.play().catch(() => {});
  else el.audio.pause();
});
el.next.addEventListener("click", next);
el.prev.addEventListener("click", prev);
el.shuffle.addEventListener("click", () => {
  shuffleOn = !shuffleOn;
  el.shuffle.classList.toggle("active", shuffleOn);
});
el.volume.addEventListener("input", () => { el.audio.volume = el.volume.value / 100; });
el.audio.volume = el.volume.value / 100;

el.audio.addEventListener("play", () => {
  el.iconPlay.classList.add("hidden");
  el.iconPause.classList.remove("hidden");
  document.body.classList.add("playing");
});
el.audio.addEventListener("pause", () => {
  el.iconPlay.classList.remove("hidden");
  el.iconPause.classList.add("hidden");
  document.body.classList.remove("playing");
});
el.audio.addEventListener("ended", next);
el.audio.addEventListener("timeupdate", () => {
  const d = el.audio.duration || 0;
  el.timeCur.textContent = fmtTime(el.audio.currentTime);
  el.timeTot.textContent = fmtTime(d);
  if (d > 0 && !seeking) el.seek.value = String((el.audio.currentTime / d) * 1000);
});

let seeking = false;
el.seek.addEventListener("input", () => { seeking = true; });
el.seek.addEventListener("change", () => {
  const d = el.audio.duration || 0;
  if (d > 0) el.audio.currentTime = (el.seek.value / 1000) * d;
  seeking = false;
});

// --- Connection status -------------------------------------------------------
function setHealthPill(pill, stateName, errText) {
  pill.classList.remove("ok", "fail", "checking", "off");
  pill.classList.add(stateName);
  const label = pill.dataset.label;
  const words = { ok: "connected", fail: "unreachable", checking: "checking…", off: "disabled" };
  pill.title = errText ? `${label}: ${errText}` : `${label}: ${words[stateName]}`;
}

function setClapPill(genre) {
  // genre = {enabled, available, loaded} from /api/health, or undefined.
  if (!genre || !genre.enabled) {
    setHealthPill(el.statusClap, "off", "genre model disabled (set GENRE_MODEL_ENABLED=1)");
    return;
  }
  if (genre.available === false) {
    setHealthPill(el.statusClap, "fail",
      "enabled but torch/transformers not in the image — rebuild with INSTALL_GENRE=true");
    return;
  }
  setHealthPill(el.statusClap, "ok", genre.loaded ? "enabled (model loaded)" : "enabled");
}

async function refreshHealth() {
  setHealthPill(el.statusNav, "checking");
  setHealthPill(el.statusLLM, "checking");
  setHealthPill(el.statusClap, "checking");
  el.healthBtn.disabled = true;
  try {
    // Read the body even on 503 (it carries navidrome/llm booleans + errors).
    const res = await fetch("/api/health");
    const data = await res.json().catch(() => ({}));
    setHealthPill(el.statusNav, data.navidrome ? "ok" : "fail", data.navidrome_error);
    setHealthPill(el.statusLLM, data.llm ? "ok" : "fail", data.llm_error);
    setClapPill(data.genre);
  } catch (err) {
    setHealthPill(el.statusNav, "fail", String(err));
    setHealthPill(el.statusLLM, "fail", String(err));
    setHealthPill(el.statusClap, "fail", String(err));
  } finally {
    el.healthBtn.disabled = false;
  }
}

el.healthBtn.addEventListener("click", refreshHealth);

// --- Scores table ------------------------------------------------------------
let scoresData = { moods: [], tracks: [], total: 0 };
let scoresSort = { key: null, dir: -1 };  // dir: 1 asc, -1 desc

// Top CLAP label + probability for a track (or null if not classified).
function clapTop(t) {
  const gs = t.genre_scores;
  if (!gs) return null;
  let label = null, prob = -1;
  for (const [l, p] of Object.entries(gs)) {
    if (p > prob) { prob = p; label = l; }
  }
  return label === null ? null : { label, prob };
}

// Multi-line tooltip of the top CLAP labels and their probabilities.
function clapTip(t) {
  const gs = t.genre_scores;
  if (!gs) return "";
  return Object.entries(gs)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 6)
    .map(([l, p]) => `${l}: ${p.toFixed(2)}`)
    .join("\n");
}

async function openScores() {
  el.scoresOverlay.classList.remove("hidden");
  el.scoresBody.innerHTML = `<tr><td>Loading…</td></tr>`;
  try {
    scoresData = await api("/api/scores?limit=5000");
    buildScoresHead();
    renderScores();
  } catch (err) {
    el.scoresBody.innerHTML = `<tr><td>Couldn’t load scores (${err}). Is the library indexed?</td></tr>`;
  }
}
function closeScores() { el.scoresOverlay.classList.add("hidden"); }

function buildScoresHead() {
  const moodSlugs = scoresData.moods.map((m) => m.slug);
  const cols = [
    ["title", "Song"], ["artist", "Artist"], ["genre", "Genre"], ["_clap", "CLAP genre"],
    ...scoresData.moods.map((m) => [m.slug, m.label]),
  ];
  el.scoresHeadRow.innerHTML = "";
  for (const [key, label] of cols) {
    const th = document.createElement("th");
    th.textContent = label;
    const isMood = moodSlugs.includes(key);
    if (isMood) th.classList.add("mood-col");
    if (key === "_clap") th.classList.add("clap-col");
    th.title = key === "_clap" ? "CLAP audio classification — click to sort by confidence" : "Click to sort";
    th.addEventListener("click", () => {
      if (scoresSort.key === key) scoresSort.dir *= -1;
      else scoresSort = { key, dir: (isMood || key === "_clap") ? -1 : 1 };
      renderScores();
    });
    el.scoresHeadRow.appendChild(th);
  }
}

function renderScores() {
  const moods = scoresData.moods.map((m) => m.slug);
  const q = (el.scoresFilter.value || "").toLowerCase().trim();
  let rows = scoresData.tracks;
  if (q) {
    rows = rows.filter((t) =>
      (t.title || "").toLowerCase().includes(q) ||
      (t.artist || "").toLowerCase().includes(q) ||
      (t.genre || "").toLowerCase().includes(q) ||
      (clapTop(t)?.label || "").toLowerCase().includes(q));
  }
  if (scoresSort.key) {
    const k = scoresSort.key, dir = scoresSort.dir, isMood = moods.includes(k);
    rows = rows.slice().sort((a, b) => {
      let va, vb;
      if (k === "_clap") { va = clapTop(a)?.prob ?? -1; vb = clapTop(b)?.prob ?? -1; }
      else if (isMood) { va = a.scores?.[k] ?? 0; vb = b.scores?.[k] ?? 0; }
      else { va = (a[k] || "").toLowerCase(); vb = (b[k] || "").toLowerCase(); }
      return va < vb ? -dir : va > vb ? dir : 0;
    });
  }

  const frag = document.createDocumentFragment();
  for (const t of rows) {
    const tr = document.createElement("tr");
    let best = null, bestVal = 0;
    for (const m of moods) {
      const v = t.scores?.[m] ?? 0;
      if (v > bestVal) { bestVal = v; best = m; }
    }
    const tdSong = document.createElement("td");
    tdSong.className = "song"; tdSong.textContent = t.title || ""; tr.appendChild(tdSong);
    const tdArtist = document.createElement("td");
    tdArtist.className = "artist"; tdArtist.textContent = t.artist || ""; tr.appendChild(tdArtist);
    const tdGenre = document.createElement("td");
    tdGenre.textContent = t.genre || "—";
    if (t.genre_source) tdGenre.title = `source: ${t.genre_source}`;
    tr.appendChild(tdGenre);

    // CLAP audio classification: top label + confidence, tinted; hover for full list.
    const tdClap = document.createElement("td");
    tdClap.className = "clap-col";
    const top = clapTop(t);
    if (top) {
      tdClap.textContent = `${top.label} · ${top.prob.toFixed(2)}`;
      tdClap.title = clapTip(t);
      tdClap.style.background = `rgba(120,150,255,${(top.prob * 0.6).toFixed(2)})`;
    } else {
      tdClap.textContent = "—";
    }
    tr.appendChild(tdClap);

    for (const m of moods) {
      const v = t.scores?.[m] ?? 0;
      const td = document.createElement("td");
      td.className = "mood-col" + (m === best && v > 0 ? " best" : "");
      td.textContent = v.toFixed(2);
      td.style.background = v > 0 ? `rgba(53,208,127,${(v * 0.55).toFixed(2)})` : "transparent";
      tr.appendChild(td);
    }
    frag.appendChild(tr);
  }
  el.scoresBody.innerHTML = "";
  el.scoresBody.appendChild(frag);
  el.scoresMeta.textContent =
    `${rows.length} of ${scoresData.total} songs${q ? " (filtered)" : ""}` +
    (scoresSort.key ? ` · sorted by ${scoresSort.key} ${scoresSort.dir < 0 ? "↓" : "↑"}` : "");
}

el.scoresBtn.addEventListener("click", openScores);
el.scoresClose.addEventListener("click", closeScores);
el.scoresFilter.addEventListener("input", renderScores);

// --- Indexing overlay --------------------------------------------------------
function openOverlay() { el.overlay.classList.remove("hidden"); pollIndex(); }
function closeOverlay() { el.overlay.classList.add("hidden"); stopPolling(); }

el.indexBtn.addEventListener("click", openOverlay);
el.overlayClose.addEventListener("click", closeOverlay);
el.overlayStart.addEventListener("click", async () => {
  el.overlayStart.disabled = true;
  await api("/api/index/start", { method: "POST" }).catch(() => {});
  pollIndex();
});

function stopPolling() {
  if (state.pollTimer) { clearTimeout(state.pollTimer); state.pollTimer = null; }
}

async function pollIndex() {
  stopPolling();
  let status;
  try {
    status = await api("/api/index/status");
  } catch {
    state.pollTimer = setTimeout(pollIndex, 3000);
    return;
  }

  const total = status.total || 0;
  const done = status.done || 0;
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
  el.progressFill.style.width = `${pct}%`;

  const running = status.running || status.status === "running";
  el.overlayStart.disabled = running;

  if (status.status === "error") {
    el.progressLabel.textContent = `Error: ${status.last_error || "unknown"}`;
    el.overlayStart.disabled = false;
    el.overlayStart.textContent = "Retry indexing";
  } else if (running) {
    const phase = status.phase === "scan" ? "Scanning library" : "Analyzing moods";
    el.progressLabel.textContent = `${phase}… ${done}/${total} (${pct}%)`;
  } else if (status.status === "done" || status.indexed > 0) {
    el.progressLabel.textContent = `Ready — ${status.indexed} tracks indexed.`;
    el.overlayStart.textContent = "Re-index";
    el.overlayStart.disabled = false;
  } else {
    el.progressLabel.textContent = "Not indexed yet.";
  }

  if (running) {
    state.pollTimer = setTimeout(pollIndex, 1500);
  }
}

// --- PWA / offline -----------------------------------------------------------
const QUEUE_KEY = "samradio.queue";

function saveQueueLocal(mood) {
  try {
    localStorage.setItem(QUEUE_KEY, JSON.stringify({ mood, tracks: state.queue }));
  } catch { /* storage full / disabled — non-fatal */ }
}

function loadQueueLocal() {
  try {
    const raw = localStorage.getItem(QUEUE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch { return null; }
}

// Play the last-saved queue from cache (used when the backend is unreachable).
function restoreOfflineQueue() {
  const saved = loadQueueLocal();
  if (!saved || !saved.tracks || !saved.tracks.length) return false;
  state.queue = saved.tracks;
  state.index = 0;
  if (saved.mood) {
    setMoodBackground(saved.mood);
    for (const btn of el.moods.children) {
      btn.classList.toggle("active", btn.dataset.slug === saved.mood);
    }
  }
  renderQueue();
  playCurrent();  // sets audio.src; the SW serves it from cache when offline
  return true;
}

let swReg = null;
function initServiceWorker() {
  if (!("serviceWorker" in navigator)) return;
  navigator.serviceWorker.register("/sw.js").then((reg) => { swReg = reg; }).catch(() => {});
  navigator.serviceWorker.addEventListener("message", (e) => {
    const m = e.data || {};
    if (m.type === "CACHE_PROGRESS") {
      el.offlineBtn.textContent = `Saving ${m.done}/${m.total}…`;
    } else if (m.type === "CACHE_DONE") {
      el.offlineBtn.textContent = "Saved offline ✓";
      el.offlineBtn.disabled = false;
      setTimeout(() => { el.offlineBtn.textContent = "Save offline"; }, 4000);
    } else if (m.type === "CACHE_CLEARED") {
      el.offlineBtn.textContent = "Save offline";
    }
  });
}

async function saveOffline() {
  if (!state.queue.length) {
    el.offlineBtn.textContent = "Pick a mood first";
    setTimeout(() => { el.offlineBtn.textContent = "Save offline"; }, 2500);
    return;
  }
  const ctrl = navigator.serviceWorker && navigator.serviceWorker.controller;
  if (!ctrl) {
    el.offlineBtn.textContent = "Reload, then retry";
    setTimeout(() => { el.offlineBtn.textContent = "Save offline"; }, 3000);
    return;
  }
  // Cache both the audio stream and the cover for every queued track.
  const paths = [];
  for (const t of state.queue) {
    if (t.stream_url) paths.push(t.stream_url);
    if (t.cover_url) paths.push(t.cover_url);
  }
  el.offlineBtn.disabled = true;
  el.offlineBtn.textContent = "Saving…";
  saveQueueLocal(state.currentMood);
  ctrl.postMessage({ type: "CACHE_TRACKS", paths });
}

el.offlineBtn.addEventListener("click", saveOffline);

// --- Init --------------------------------------------------------------------
async function init() {
  initServiceWorker();
  setMoodBackground("");  // show the default animated gradient immediately
  try {
    await loadMoods();
  } catch {
    // Backend unreachable — fall back to the last cached playlist so the user
    // can still play saved songs offline.
    if (restoreOfflineQueue()) {
      el.queueEmpty.textContent = "Offline — playing your saved playlist.";
    }
    return;
  }
  refreshHealth();  // fire-and-forget; updates the status pills
  try {
    const status = await api("/api/index/status");
    if ((status.indexed || 0) === 0 && !status.running) {
      openOverlay();
    }
  } catch { /* backend not ready; ignore */ }
}

init();
