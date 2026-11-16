"use strict";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const api = (path, method = "GET", body) =>
  fetch(path, { method, headers: { "Content-Type": "application/json" }, body: body && JSON.stringify(body) })
    .then(async (r) => (r.ok ? r.json() : Promise.reject(new Error((await r.text()) || r.statusText))));
const tc = (t) => { t = Math.max(0, Math.floor(t)); const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
  return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(s).padStart(2, "0"); };
const store = { get: (k, d) => { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
                set: (k, v) => { try { localStorage.setItem(k, v); } catch {} } };
const platform = (u) => /twitch\.tv/.test(u) ? "twitch" : /youtu\.?be/.test(u) ? "youtube" : /kick\.com/.test(u) ? "kick" : "";
const isVodUrl = (u) => /\/videos?\/|watch\?v=|youtu\.be\//.test(u);
const DPR = () => window.devicePixelRatio || 1;
const cssv = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const alpha = (hex, a) => { const h = hex.replace("#", ""); const n = parseInt(h.length === 3 ? h.replace(/./g, "$&$&") : h, 16);
  return `rgba(${n >> 16 & 255},${n >> 8 & 255},${n & 255},${a})`; };
const clamp = (v, a, b) => Math.min(Math.max(v, a), b);

const S = { jobs: [], sel: store.get("sel", ""), filter: store.get("filter", "all"), sort: store.get("sort", "new"),
            layout: store.get("layout", "split"), ready: new Set(), booted: false, bg: [], built: {}, order: [],
            fonts: [], defStyle: null };

/* ---------------- fullscreen ---------------- */
const toggleFs = (el = document.documentElement) =>
  document.fullscreenElement ? document.exitFullscreen() : el.requestFullscreen?.().catch(() => {});
$("#appfs").onclick = () => toggleFs();

/* ---------------- new job form ---------------- */
const urlIn = $("#url"), detect = $("#detect");
function updateDetect() {
  const u = urlIn.value.trim(), p = platform(u);
  if (!u) { detect.textContent = ""; return; }
  const mode = $("#mode").value === "auto" ? (isVodUrl(u) ? "vod" : "live") : $("#mode").value;
  detect.innerHTML = p ? `<b>${p[0].toUpperCase() + p.slice(1)}</b> · ${mode === "vod" ? "VOD, clips the best moments" : "live channel, clips as it happens"}`
                       : "Use a Twitch, YouTube or Kick link";
}
urlIn.addEventListener("input", updateDetect);
$("#mode").addEventListener("change", updateDetect);
$$("#layout button").forEach((b) => {
  b.setAttribute("aria-pressed", String(b.dataset.v === S.layout));
  b.onclick = () => { S.layout = b.dataset.v; store.set("layout", S.layout);
    $$("#layout button").forEach((x) => x.setAttribute("aria-pressed", String(x === b))); };
});
$("#new").addEventListener("submit", async (e) => {
  e.preventDefault();
  const u = urlIn.value.trim();
  const mode = $("#mode").value === "auto" ? (isVodUrl(u) ? "vod" : "live") : $("#mode").value;
  if ("Notification" in window && Notification.permission === "default") Notification.requestPermission();
  try {
    const j = await api("/api/jobs", "POST", { url: u, mode, layout: S.layout });
    urlIn.value = ""; updateDetect(); select(j.id); refresh();
  } catch (err) { toast(`Couldn't start: ${esc(err.message)}`, true); }
});

/* ---------------- toasts + alerts ---------------- */
function toast(html, bad = false, actions = []) {
  const t = Object.assign(document.createElement("div"), { className: "toast" + (bad ? " bad" : "") });
  t.innerHTML = `<div style="flex:1">${html}</div>`;
  actions.forEach(([label, fn]) => { const b = Object.assign(document.createElement("button"), { className: "btn sm", textContent: label });
    b.onclick = () => { fn(); t.remove(); }; t.append(b); });
  $("#toasts").append(t);
  setTimeout(() => t.remove(), bad ? 9000 : 6000);
}
let actx;
function chime() {
  try {
    actx = actx || new AudioContext();
    [660, 990].forEach((f, i) => { const o = actx.createOscillator(), g = actx.createGain(), t = actx.currentTime + i * 0.11;
      o.frequency.value = f; o.type = "triangle"; g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(0.15, t + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, t + 0.18); o.connect(g).connect(actx.destination); o.start(t); o.stop(t + 0.2); });
  } catch {}
}
function announce(job, c) {
  if (Studio.st && Studio.st.cid === c.id) return; // the editor shows its own result
  chime();
  toast(`<b>Clip ready</b> · ${esc(job.name)} · heat ${c.score}`, false, [["Watch", () => Viewer.open(job.id, c.id)]]);
  if ("Notification" in window && Notification.permission === "granted" && document.hidden)
    new Notification(`Clip ready: ${job.name}`, { body: `Heat ${c.score} · ${Math.round(c.end - c.start)}s`, silent: true });
}

/* ---------------- canvases ---------------- */
function sizeCanvas(cv) {
  const w = Math.max(1, Math.round(cv.clientWidth * DPR())), h = Math.max(1, Math.round(cv.clientHeight * DPR()));
  if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
  return [cv.getContext("2d"), w, h];
}
function areaPath(g, vals, W, H, max, pad) {
  const n = vals.length, x = (i) => (n === 1 ? W : (i / (n - 1)) * W), y = (v) => H - pad - (v / max) * (H - pad * 2);
  g.beginPath(); g.moveTo(0, H);
  vals.forEach((v, i) => g.lineTo(x(i), y(v)));
  g.lineTo(W, H); g.closePath();
  return y;
}
function plot(cv, vals, { trigger = 0, bands = [], pad = 6, line = true } = {}) {
  const [g, W, H] = sizeCanvas(cv);
  g.clearRect(0, 0, W, H);
  if (!vals || !vals.length) return null;
  const hot1 = cssv("--hot1"), hot2 = cssv("--hot2"), bad = cssv("--bad"), warn = cssv("--warn");
  const max = Math.max(...vals, trigger * 1.35, 1);
  bands.forEach((b) => { g.fillStyle = b.color; g.fillRect(b.x0 * W, 0, Math.max((b.x1 - b.x0) * W, 2 * DPR()), H); });
  const grad = g.createLinearGradient(0, H, 0, 0);
  grad.addColorStop(0, alpha(hot1, 0.04)); grad.addColorStop(1, alpha(hot1, 0.5));
  const y = areaPath(g, vals, W, H, max, pad); g.fillStyle = grad; g.fill();
  if (trigger) { // anything over the trigger glows
    g.save(); g.beginPath(); g.rect(0, 0, W, y(trigger)); g.clip();
    areaPath(g, vals, W, H, max, pad); g.fillStyle = alpha(bad, 0.85); g.fill(); g.restore();
    g.setLineDash([6 * DPR(), 5 * DPR()]); g.strokeStyle = alpha(warn, 0.85); g.lineWidth = DPR();
    g.beginPath(); g.moveTo(0, y(trigger)); g.lineTo(W, y(trigger)); g.stroke(); g.setLineDash([]);
  }
  if (line) {
    const n = vals.length, lg = g.createLinearGradient(0, 0, W, 0);
    lg.addColorStop(0, hot1); lg.addColorStop(1, hot2);
    g.beginPath(); vals.forEach((v, i) => g[i ? "lineTo" : "moveTo"]((n === 1 ? W : (i / (n - 1)) * W), y(v)));
    g.strokeStyle = lg; g.lineWidth = 1.6 * DPR(); g.stroke();
  }
  return { W, H, y };
}

/* ---------------- selection ---------------- */
function select(id) { S.sel = id; store.set("sel", id); S.built = {}; render(); }
const jobById = (id) => S.jobs.find((x) => x.id === id);
const clipById = (jid, cid) => jobById(jid)?.clips.find((c) => c.id === cid);
function focusClip(jid, cid) {
  const el = document.getElementById(`c-${jid}-${cid}`);
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  el.classList.remove("flash"); void el.offsetWidth; el.classList.add("flash");
}

/* ---------------- sidebar ---------------- */
function renderSidebar() {
  const box = $("#jobs"), seen = new Set();
  S.jobs.forEach((j, i) => {
    const id = `j-${j.id}`; seen.add(id);
    let row = document.getElementById(id);
    if (!row) {
      row = Object.assign(document.createElement("button"), { id, className: "jobrow" });
      row.innerHTML = `<span class="n"></span><span class="tag"></span><canvas aria-hidden="true"></canvas><span class="s"></span>`;
      row.onclick = () => select(j.id);
    }
    const live = j.mode === "live" && ["running", "starting"].includes(j.status);
    const ready = j.clips.filter((c) => c.status === "ready"), posted = ready.filter((c) => c.posted).length;
    $(".n", row).textContent = j.name;
    const tag = $(".tag", row); tag.className = "tag" + (live ? " live" : ""); tag.textContent = live ? "● live" : j.mode;
    $(".s", row).textContent = j.status === "error" ? "Error, open for details" :
      `${ready.length} ready · ${posted} posted${["running", "starting"].includes(j.status) && !live ? " · working" : ""}`;
    row.setAttribute("aria-current", String(j.id === S.sel));
    if (box.children[i + 1] !== row) box.insertBefore(row, box.children[i + 1] || null);
    plot($("canvas", row), j.pulse || j.timeline?.values, { pad: 2, line: false });
  });
  $$(".jobrow", box).forEach((r) => { if (!seen.has(r.id)) r.remove(); });
  $("#pulse-dot").classList.toggle("on", S.jobs.some((j) => j.mode === "live" && j.status === "running"));
}

/* ---------------- main ---------------- */
function renderEmpty(main) {
  if (S.built.empty) return;
  S.built = { empty: true };
  const bg = !S.bg.length ? `<dt>Split</dt><dd><span class="warn">No background loops yet.</span> Put gameplay .mp4 files in <code>assets\\bg\\</code>. Until then, Split clips render as Full.</dd>` : "";
  main.innerHTML = `<section class="empty">
    <h1>Catch the moment chat loses it.</h1>
    <p>clipperr watches chat speed and cuts from just before it spikes. It adds captions and a 9:16 layout, so each clip is ready to post.</p>
    <dl>
      <dt>Live</dt><dd>Paste a channel link like <code>twitch.tv/caseoh_</code>. Clips appear about 30 seconds after each spike.</dd>
      <dt>VOD</dt><dd>Paste a past stream like <code>twitch.tv/videos/…</code> or a YouTube video. It reads the whole chat and renders the top 15 moments.</dd>
      <dt>Style</dt><dd>Open any clip's <b>Edit style</b> to choose fonts, colours, caption position and layout, then save it as the default.</dd>
      ${bg}
    </dl></section>`;
}

function buildJobShell(main, j) {
  main.innerHTML = `
    <div class="jhead">
      <h1></h1>
      <div class="meta"><span class="tag"></span><span class="status"></span><a class="mono src" target="_blank" rel="noopener"></a></div>
      <div class="acts"><button class="btn more" title="Render the next 5 best moments from this stream's saved chat">+ More clips</button><a class="btn zip" download title="Every ready clip, best first, plus captions.txt">Download all</a><button class="btn stop">Stop</button><button class="btn ghost danger del">Delete job</button></div>
    </div>
    <p class="log" aria-live="polite"></p>
    <div class="seis"><canvas></canvas><div class="lbl"></div><div class="tip"></div><div class="axis"></div></div>
    <div class="tools">
      <div class="seg filt" role="group" aria-label="Show clips">
        <button data-f="all">All<b></b></button><button data-f="ready">Ready<b></b></button>
        <button data-f="todo">To post<b></b></button><button data-f="posted">Posted<b></b></button>
      </div>
      <span class="sp"></span>
      <span class="bgwarn"></span>
      <div class="seg sort" role="group" aria-label="Sort clips">
        <button data-s="new">Newest</button><button data-s="hot">Hottest</button>
      </div>
    </div>
    <div class="grid"></div>`;
  $(".stop", main).onclick = () => api(`/api/jobs/${j.id}/stop`, "POST").then(refresh);
  $(".more", main).onclick = (e) => { e.currentTarget.disabled = true; api(`/api/jobs/${j.id}/more?n=5`, "POST").then(() => { toast("Looking for 5 more moments…"); refresh(); }).catch((err) => { toast(esc(err.message), true); refresh(); }); };
  $(".del", main).onclick = () => {
    if (!confirm(`Delete "${j.name}" and all its clips? This can't be undone.`)) return;
    api(`/api/jobs/${j.id}`, "DELETE").then(() => { select(""); refresh(); });
  };
  $$(".filt button", main).forEach((b) => (b.onclick = () => { S.filter = b.dataset.f; store.set("filter", S.filter); render(); }));
  $$(".sort button", main).forEach((b) => (b.onclick = () => { S.sort = b.dataset.s; store.set("sort", S.sort); render(); }));
  $(".seis canvas", main).onclick = (e) => {
    const job = jobById(S.sel); if (!job?.timeline) return;
    const tl = job.timeline, span = tl.values.length * tl.step, r = e.currentTarget.getBoundingClientRect();
    const t = tl.t0 + ((e.clientX - r.left) / r.width) * span;
    const hit = job.clips.find((c) => t >= c.start - span * 0.01 && t <= c.end + span * 0.01);
    if (hit) focusClip(job.id, hit.id);
  };
}

function renderSeis(main, j) {
  const cv = $(".seis canvas", main), lbl = $(".seis .lbl", main), tip = $(".seis .tip", main), axis = $(".seis .axis", main);
  if (j.mode === "live") {
    const v = j.pulse || [];
    plot(cv, v, { trigger: j.trigger || 0, pad: 18 });
    const now = v.length ? v[v.length - 1] : 0;
    lbl.innerHTML = `Chat heat<span>${now.toFixed(1)}/s</span>`;
    tip.textContent = v.length ? `over the dashed line (≈${j.trigger}/s) = spike` : "Waiting for chat…";
    axis.innerHTML = `<span>−${tc(v.length || 180)}</span><span>−${tc((v.length || 180) / 2)}</span><span>now</span>`;
  } else if (j.timeline) {
    const tl = j.timeline, span = tl.values.length * tl.step, ok = cssv("--ok");
    const ranked = [...j.clips].sort((a, b) => b.score - a.score);
    const minW = 8 / Math.max(cv.clientWidth, 1);
    const bands = ranked.map((c, i) => { const x0 = (c.start - tl.t0) / span, x1 = (c.end - tl.t0) / span, mid = (x0 + x1) / 2, w = Math.max(x1 - x0, minW);
      return { x0: mid - w / 2, x1: mid + w / 2, color: alpha(ok, i < 3 ? 0.45 : 0.22) }; });
    const r = plot(cv, tl.values, { bands, pad: 22 });
    if (r) {
      const g = cv.getContext("2d"); g.font = `600 ${10 * DPR()}px JetBrains Mono, monospace`; g.textAlign = "center";
      ranked.forEach((c, i) => { const x = ((c.start + c.end) / 2 - tl.t0) / span * r.W;
        g.fillStyle = alpha(ok, i < 3 ? 1 : 0.6); g.beginPath(); g.moveTo(x - 4 * DPR(), r.H - 2 * DPR()); g.lineTo(x + 4 * DPR(), r.H - 2 * DPR()); g.lineTo(x, r.H - 9 * DPR()); g.fill();
        if (i < 5) g.fillText(String(i + 1), x, r.H - 12 * DPR()); });
    }
    cv.classList.add("click");
    lbl.innerHTML = `Whole stream<span>${tc(span)}</span>`;
    tip.textContent = j.clips.length ? "Bands are clips. Click one to jump to it." : "";
    axis.innerHTML = `<span>0:00</span><span>${tc(span / 2)}</span><span>${tc(span)}</span>`;
  } else {
    plot(cv, []);
    lbl.innerHTML = "Reading chat";
    tip.textContent = "The timeline appears once the whole chat is downloaded.";
    axis.innerHTML = "";
  }
}

const STEP = { queued: [0, "Waiting for the previous clip"], fetching: [0, "Cutting footage"],
               transcribing: [1, "Writing captions"], rendering: [2, "Rendering 1080×1920"] };
const styleOf = (c) => ({ ...(S.defStyle || {}), ...(c.style || {}) });

// what chat spammed in the window + how much YouTube viewers rewatched it: the "why" behind the pick
const why = (c) => {
  const bits = (c.why || []).map(([t, n]) => `<span title="${n} messages in this window"><b>×${n}</b> ${esc(t)}</span>`);
  if (c.replay != null) bits.push(`<span class="rp" title="YouTube most-replayed score for this part of the video">replayed ${Math.round(c.replay * 100)}%</span>`);
  return bits.length ? `<div class="why" aria-label="Why this clip">${bits.join("")}</div>` : "";
};

function card(j, c, rank) {
  const el = Object.assign(document.createElement("article"), { id: `c-${j.id}-${c.id}`, className: "card" + (c.posted ? " posted" : "") });
  const dur = Math.round(c.end - c.start), ready = c.status === "ready", st = styleOf(c);
  let media;
  if (ready) media = `<video muted loop playsinline preload="metadata" src="/out/${j.id}/${c.id}.mp4?v=${c.v}" aria-label="Clip preview, click to watch"></video>
                      <button class="chip fs" aria-label="Watch fullscreen" title="Watch fullscreen">⛶</button>`;
  else if (c.status === "error") media = `<div class="work err"><p>${esc(c.error || "Something failed")}</p></div>`;
  else { const [k, label] = STEP[c.status] || [0, c.status];
    media = `<div class="work"><div class="steps">${[0, 1, 2].map((i) => `<i class="${i < k ? "done" : i === k ? "now" : ""}"></i>`).join("")}</div><p>${label}…</p></div>`; }
  el.innerHTML = `
    <div class="media">${media}
      <span class="chip badge">#${rank} · heat <em>${c.score}</em></span>
      ${c.posted ? `<span class="ribbon">Posted</span>` : ""}
      <span class="chip tc">${tc(c.start)} · ${dur}s</span>
    </div>
    <div class="cb">
      <canvas class="strip" aria-hidden="true"></canvas>
      ${why(c)}
      <div class="stylechip"><i class="dot" style="background:${esc(st.hl)}"></i><span>${st.layout === "split" ? "Split" : "Full"} · ${esc(st.font)}${c.hook ? " · hook" : ""}</span></div>
      <div class="cap"><textarea readonly aria-label="Caption, click to copy">${esc(c.caption)}</textarea></div>
      <div class="acts2">
        ${ready ? `<a class="btn ok" href="/out/${j.id}/${c.id}.mp4" download="${esc(j.name)}_${c.id}.mp4">Download</a>` : `<button class="btn" disabled>Download</button>`}
        <button class="btn mark">${c.posted ? "Posted ✓" : "Mark posted"}</button>
      </div>
      <div class="acts3">
        <button class="btn edit" ${c.status === "error" && !c.v ? "disabled" : ""}>Edit style</button>
        <button class="btn ghost danger rm" aria-label="Delete clip">✕</button>
      </div>
    </div>`;
  $(".mark", el).onclick = () => api(`/api/jobs/${j.id}/clips/${c.id}/posted`, "POST", { posted: !c.posted }).then(refresh);
  $(".rm", el).onclick = () => confirm("Delete this clip?") && api(`/api/jobs/${j.id}/clips/${c.id}`, "DELETE").then(refresh);
  $(".edit", el).onclick = () => Studio.open(j.id, c.id);
  $("textarea", el).onclick = (e) => { e.target.select(); navigator.clipboard?.writeText(c.caption).then(() => toast("Caption copied")); };
  const v = $("video", el);
  if (v) {
    v.onmouseenter = () => v.play().catch(() => {});
    v.onmouseleave = () => v.pause();
    v.onclick = () => Viewer.open(j.id, c.id);
    $(".fs", el).onclick = () => Viewer.open(j.id, c.id, true);
  }
  requestAnimationFrame(() => {
    const a = c.activity; if (!a?.values?.length) return;
    const n = a.values.length;
    plot($(".strip", el), a.values, { pad: 2, bands: [{ x0: (c.start - a.t0) / n, x1: (c.end - a.t0) / n, color: alpha(cssv("--ok"), 0.22) }] });
  });
  return el;
}

function visibleClips(j) {
  const list = j.clips.filter((c) => S.filter === "all" || (S.filter === "ready" && c.status === "ready") ||
    (S.filter === "todo" && c.status === "ready" && !c.posted) || (S.filter === "posted" && c.posted));
  const by = S.sort === "hot" ? (a, b) => b.score - a.score : (a, b) => b.created - a.created || b.score - a.score;
  return list.sort((a, b) => (b.status === "ready") - (a.status === "ready") || by(a, b));
}
const rankMap = (j) => new Map([...j.clips].sort((a, b) => b.score - a.score).map((c, i) => [c.id, i + 1]));

function renderJob(main, j) {
  if (S.built.job !== j.id) { buildJobShell(main, j); S.built = { job: j.id, cards: {} }; }
  const live = j.mode === "live" && ["running", "starting"].includes(j.status);
  $("h1", main).textContent = j.name;
  const tag = $(".jhead .tag", main); tag.className = "tag" + (live ? " live" : ""); tag.textContent = live ? "● live" : j.mode;
  const st = $(".status", main); st.className = "status " + j.status; st.textContent = j.status;
  const src = $(".src", main); src.href = j.url; src.textContent = j.url.replace(/^https?:\/\/(www\.)?/, "");
  const busy = ["running", "starting"].includes(j.status), nReady = j.clips.filter((c) => c.status === "ready").length;
  $(".stop", main).hidden = !busy;
  const more = $(".more", main); more.hidden = !j.can_more; more.disabled = busy;
  const zip = $(".zip", main); zip.hidden = !nReady; zip.href = `/api/jobs/${j.id}/zip`; zip.textContent = `Download all (${nReady})`;
  const log = $(".log", main); log.textContent = j.log || ""; log.classList.toggle("err", j.status === "error");
  renderSeis(main, j);

  const counts = { all: j.clips.length, ready: 0, todo: 0, posted: 0 };
  j.clips.forEach((c) => { if (c.status === "ready") { counts.ready++; c.posted ? counts.posted++ : counts.todo++; } });
  $$(".filt button", main).forEach((b) => { b.setAttribute("aria-pressed", String(b.dataset.f === S.filter)); $("b", b).textContent = counts[b.dataset.f]; });
  $$(".sort button", main).forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.s === S.sort)));
  $(".bgwarn", main).innerHTML = !S.bg.length && j.clips.some((c) => styleOf(c).layout === "split")
    ? `<span class="warn">No background loops in assets\\bg, so Split renders as Full</span>` : "";

  const ranks = rankMap(j), list = visibleClips(j);
  S.order = list.map((c) => c.id);
  const grid = $(".grid", main);
  if (!list.length) {
    const msg = !j.clips.length
      ? (j.status === "error" ? "This job stopped with an error. The message is above." :
         j.mode === "live" ? "No spikes yet. Clips appear here a few seconds after chat jumps." : "Clips appear here once the chat is read and the top moments are cut.")
      : "No clips match this filter.";
    if (grid.dataset.empty !== msg) { grid.innerHTML = `<p class="none">${msg}</p>`; grid.dataset.empty = msg; S.built.cards = {}; }
    return;
  }
  if (grid.dataset.empty) { grid.innerHTML = ""; delete grid.dataset.empty; }
  const keep = new Set();
  list.forEach((c, i) => {
    const sig = [c.status, c.v, c.posted, c.error, JSON.stringify(c.style), c.hook, c.start, c.end, ranks.get(c.id)].join("|");
    let el = document.getElementById(`c-${j.id}-${c.id}`);
    if (!el || S.built.cards[c.id] !== sig) {
      const fresh = card(j, c, ranks.get(c.id));
      if (el) el.replaceWith(fresh);
      el = fresh; S.built.cards[c.id] = sig;
    }
    keep.add(el.id);
    if (grid.children[i] !== el) grid.insertBefore(el, grid.children[i] || null);
  });
  [...grid.children].forEach((el) => { if (!keep.has(el.id)) el.remove(); });
}

function render() {
  renderSidebar();
  const main = $("#main");
  const j = jobById(S.sel) || S.jobs[0];
  if (!j) return renderEmpty(main);
  if (j.id !== S.sel) { S.sel = j.id; store.set("sel", j.id); }
  renderJob(main, j);
  const todo = S.jobs.reduce((n, x) => n + x.clips.filter((c) => c.status === "ready" && !c.posted).length, 0);
  document.title = todo ? `(${todo}) clipperr` : "clipperr";
}

async function refresh() {
  try {
    const jobs = await api("/api/jobs");
    jobs.forEach((j) => j.clips.forEach((c) => {
      const k = `${j.id}/${c.id}`;
      if (c.status === "ready" && !S.ready.has(k)) { S.ready.add(k); if (S.booted) announce(j, c); }
    }));
    S.jobs = jobs; S.booted = true;
    render(); Viewer.sync(); Studio.sync();
  } catch (e) { /* server restarting; next poll retries */ }
}

/* =====================================================================
   Viewer: fullscreen player for finished clips
   ===================================================================== */
const Viewer = {
  el: null, jid: null, cid: null,
  list() { const j = jobById(this.jid); return j ? S.order.filter((id) => clipById(j.id, id)?.status === "ready") : []; },
  open(jid, cid, native = false) {
    this.close(); this.jid = jid; this.cid = cid;
    this.el = Object.assign(document.createElement("div"), { className: "overlay viewer", role: "dialog" });
    this.el.setAttribute("aria-modal", "true"); this.el.setAttribute("aria-label", "Clip player");
    this.el.innerHTML = `
      <div class="ovbar"><span class="t"></span><span class="m"></span><span class="sp"></span>
        <button class="btn edit">Edit style</button><a class="btn ok dl">Download</a><button class="btn mark"></button>
        <button class="btn icon nfs" title="Fullscreen (F)" aria-label="Fullscreen">⛶</button>
        <button class="btn icon x" aria-label="Close (Esc)">✕</button></div>
      <div class="stage"><button class="btn navb prev" aria-label="Previous clip">‹</button>
        <video controls autoplay playsinline></video>
        <button class="btn navb next" aria-label="Next clip">›</button></div>
      <div class="foot keys">← → switch clip · Space play/pause · F fullscreen · M mute · Esc close</div>`;
    document.body.append(this.el);
    $(".x", this.el).onclick = () => this.close();
    $(".prev", this.el).onclick = () => this.step(-1);
    $(".next", this.el).onclick = () => this.step(1);
    $(".nfs", this.el).onclick = () => toggleFs($("video", this.el));
    $(".edit", this.el).onclick = () => { const { jid: a, cid: b } = this; this.close(); Studio.open(a, b); };
    $(".mark", this.el).onclick = () => { const c = clipById(this.jid, this.cid);
      api(`/api/jobs/${this.jid}/clips/${this.cid}/posted`, "POST", { posted: !c.posted }).then(refresh); };
    this.load();
    if (native) toggleFs($("video", this.el));
    $(".x", this.el).focus({ preventScroll: true });
  },
  load() {
    const j = jobById(this.jid), c = clipById(this.jid, this.cid); if (!j || !c) return this.close();
    const v = $("video", this.el), src = `/out/${j.id}/${c.id}.mp4?v=${c.v}`;
    if (v.getAttribute("src") !== src) { v.src = src; v.play().catch(() => {}); }
    $(".t", this.el).textContent = j.name;
    $(".m", this.el).textContent = `#${rankMap(j).get(c.id)} · heat ${c.score} · ${tc(c.start)} · ${Math.round(c.end - c.start)}s`;
    const dl = $(".dl", this.el); dl.href = src; dl.download = `${j.name}_${c.id}.mp4`;
    $(".mark", this.el).textContent = c.posted ? "Posted ✓" : "Mark posted";
    const l = this.list(), i = l.indexOf(c.id);
    $(".prev", this.el).disabled = i <= 0; $(".next", this.el).disabled = i < 0 || i >= l.length - 1;
  },
  step(d) { const l = this.list(), i = l.indexOf(this.cid); if (l[i + d]) { this.cid = l[i + d]; this.load(); } },
  sync() { if (this.el) this.load(); },
  close() { if (!this.el) return; if (document.fullscreenElement) document.exitFullscreen().catch(() => {}); this.el.remove(); this.el = null; },
  key(e) {
    const v = $("video", this.el);
    if (e.key === "Escape" && !document.fullscreenElement) this.close();
    else if (e.key === "ArrowLeft") this.step(-1);
    else if (e.key === "ArrowRight") this.step(1);
    else if (e.key === "f" || e.key === "F") toggleFs(v);
    else if (e.key === "m" || e.key === "M") v.muted = !v.muted;
    else if (e.key === " " && e.target.tagName !== "BUTTON") { e.preventDefault(); v.paused ? v.play() : v.pause(); }
    else return;
  },
};

/* =====================================================================
   Studio: Layout → Captions → Hook → Trim → Render
   ===================================================================== */
const PALETTE = ["#FFFFFF", "#FFE600", "#FFB020", "#FF4D2E", "#FF3EA5", "#A35BFF", "#00E5FF", "#2EFF6A", "#000000"];
const PRESETS = [
  [["Classic", "pop"], { font: "Arial Black", color: "#FFFFFF", hl: "#FFE600", outline: "#000000", outline_w: 7, upper: true, pop: true, words: 3 }],
  [["Big", "clean"], { font: "Anton", color: "#FFFFFF", hl: "#FFFFFF", outline: "#000000", outline_w: 6, upper: true, pop: false, words: 2, size: 110 }],
  [["Green", "pop"], { font: "Poppins Black", color: "#FFFFFF", hl: "#2EFF6A", outline: "#000000", outline_w: 8, upper: true, pop: true, words: 3 }],
  [["Comic", "pop"], { font: "Luckiest Guy", color: "#FFFFFF", hl: "#FFB020", outline: "#000000", outline_w: 8, upper: true, pop: true, words: 2 }],
  [["Neon", "glow"], { font: "Bebas Neue", color: "#00E5FF", hl: "#FF3EA5", outline: "#000000", outline_w: 5, upper: true, pop: true, words: 3, size: 120 }],
];
const STEPS = ["Layout", "Captions", "Hook", "Trim", "Render"];
const ratioCache = {};
function fontRatio(name) { // libass sizes by ascent+descent; CSS by em. Convert so the preview matches the render.
  if (ratioCache[name]) return ratioCache[name];
  const g = document.createElement("canvas").getContext("2d"); g.font = `100px "${name}"`;
  const m = g.measureText("Hg"), h = (m.fontBoundingBoxAscent || 0) + (m.fontBoundingBoxDescent || 0);
  const r = h ? 100 / h : 0.82;
  if (document.fonts.check(`100px "${name}"`)) ratioCache[name] = r;
  return r;
}

const Studio = {
  el: null, st: null,
  open(jid, cid, step = 0) {
    const c = clipById(jid, cid); if (!c) return;
    Viewer.close(); this.close(true);
    this.st = { jid, cid, step, draft: structuredClone(styleOf(c)), hook: c.hook || "", dS: 0, dE: 0, dirty: false,
                view: "mock", wait: null, words: [], frame: null, ar: 9 / 16 };
    this.el = Object.assign(document.createElement("div"), { className: "overlay studio", role: "dialog" });
    this.el.setAttribute("aria-modal", "true"); this.el.setAttribute("aria-label", "Clip style editor");
    this.el.innerHTML = `
      <div class="ovbar">
        <button class="btn ghost back">← Clips</button><span class="t"></span><span class="m"></span><span class="sp"></span>
        <button class="btn icon prevc" aria-label="Previous clip" title="Previous clip">‹</button>
        <button class="btn icon nextc" aria-label="Next clip" title="Next clip">›</button>
        <button class="btn icon sfs" aria-label="Fullscreen" title="Fullscreen">⛶</button>
        <button class="btn icon x" aria-label="Close (Esc)">✕</button>
      </div>
      <div class="body">
        <div class="left">
          <div class="seg vt" role="group" aria-label="Preview mode">
            <button data-v="mock">Live preview</button><button data-v="render">Rendered clip</button>
          </div>
          <div class="phone" aria-label="9:16 preview"></div>
          <p class="keys pnote"></p>
        </div>
        <div class="right">
          <nav class="pipe" aria-label="Steps">${STEPS.map((s, i) => `<button data-i="${i}"><span>${i + 1}</span>${s}</button>`).join("")}</nav>
          <div class="panel"></div>
          <div class="pfoot"><span class="dirty" aria-live="polite"></span><button class="btn pb">Back</button><button class="btn go pn">Next</button></div>
        </div>
      </div>`;
    document.body.append(this.el);
    $(".x", this.el).onclick = () => this.close();
    $(".back", this.el).onclick = () => this.close();
    $(".sfs", this.el).onclick = () => toggleFs(this.el);
    $(".prevc", this.el).onclick = () => this.switchClip(-1);
    $(".nextc", this.el).onclick = () => this.switchClip(1);
    $$(".vt button", this.el).forEach((b) => (b.onclick = () => { this.st.view = b.dataset.v; this.drawPhone(); }));
    $$(".pipe button", this.el).forEach((b) => (b.onclick = () => this.go(+b.dataset.i)));
    $(".pb", this.el).onclick = () => this.go(this.st.step - 1);
    $(".pn", this.el).onclick = () => this.go(this.st.step + 1);
    new ResizeObserver(() => this.drawPhone()).observe($(".phone", this.el));
    this.loadAssets(); this.header(); this.go(step);
    $(".x", this.el).focus({ preventScroll: true });
  },
  async loadAssets() {
    const { jid, cid } = this.st, j = jobById(jid), c = clipById(jid, cid);
    const img = new Image();
    img.onload = () => { if (this.st?.cid === cid) { this.st.frame = img.src; this.st.ar = img.naturalHeight / img.naturalWidth; this.drawPhone(); } };
    img.src = `/api/jobs/${jid}/clips/${cid}/frame?v=${c.start}`;
    try {
      const w = await fetch(`/out/${j.id}/${cid}_words.json`).then((r) => (r.ok ? r.json() : []));
      if (this.st?.cid === cid) { this.st.words = w; this.drawPhone(); }
    } catch {}
  },
  header() {
    const j = jobById(this.st.jid), c = clipById(this.st.jid, this.st.cid); if (!c) return;
    $(".t", this.el).textContent = j.name;
    $(".m", this.el).textContent = `#${rankMap(j).get(c.id)} · heat ${c.score} · ${tc(c.start)} · ${Math.round(c.end - c.start)}s`;
    const i = S.order.indexOf(c.id);
    $(".prevc", this.el).disabled = i <= 0; $(".nextc", this.el).disabled = i < 0 || i >= S.order.length - 1;
  },
  switchClip(d) {
    const i = S.order.indexOf(this.st.cid), next = S.order[i + d];
    if (!next || (this.st.dirty && !confirm("Discard your unrendered changes to this clip?"))) return;
    this.open(this.st.jid, next, this.st.step);
  },
  close(force = false) {
    if (!this.el) return;
    if (!force && this.st.dirty && !confirm("Close without rendering your changes?")) return;
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    this.el.remove(); this.el = null; this.st = null;
  },
  sync() {
    if (!this.el) return;
    const c = clipById(this.st.jid, this.st.cid); if (!c) return this.close(true);
    this.header();
    if (this.st.wait !== null && c.status === "ready" && c.v > this.st.wait) {
      this.st.wait = null; this.st.view = "render"; chime(); toast("Clip rendered"); this.drawPhone();
      if (this.st.step === 4) this.panel();
    } else if (this.st.wait !== null && c.status === "error") {
      this.st.wait = null; toast(`Render failed: ${esc(c.error)}`, true); if (this.st.step === 4) this.panel();
    }
    this.note();
  },
  set(k, v, repanel = false) {
    this.st.draft[k] = v; this.st.dirty = true;
    this.drawPhone(); this.note();
    if (repanel) this.panel();
  },
  note() {
    const c = clipById(this.st.jid, this.st.cid);
    $(".dirty", this.el).textContent = this.st.wait !== null ? "Rendering…" : this.st.dirty ? "Unrendered changes" : "";
    $(".pnote", this.el).textContent = this.st.view === "mock"
      ? "Preview of your settings on a frame from this clip. Drag the caption to move it."
      : c?.status === "ready" ? "The last rendered version of this clip." : "Not rendered yet.";
  },
  go(i) {
    this.st.step = clamp(i, 0, STEPS.length - 1);
    $$(".pipe button", this.el).forEach((b, k) => { b.classList.toggle("done", k < this.st.step);
      if (k === this.st.step) b.setAttribute("aria-current", "step"); else b.removeAttribute("aria-current"); });
    $(".pb", this.el).disabled = this.st.step === 0;
    $(".pn", this.el).hidden = this.st.step === STEPS.length - 1;
    $(".pn", this.el).textContent = `Next: ${STEPS[this.st.step + 1] || ""}`;
    this.panel(); $(".panel", this.el).scrollTop = 0;
  },

  /* ---------- the 9:16 preview ---------- */
  drawPhone() {
    if (!this.el) return;
    const ph = $(".phone", this.el), d = this.st.draft, c = clipById(this.st.jid, this.st.cid); if (!c) return;
    $$(".vt button", this.el).forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.v === this.st.view)));
    $(".vt button[data-v=render]", this.el).disabled = !c.v;
    this.note();
    if (this.st.view === "render" && c.v) {
      const src = `/out/${this.st.jid}/${c.id}.mp4?v=${c.v}`, v = $("video", ph);
      if (!v || v.getAttribute("src") !== src) ph.innerHTML = `<video controls autoplay loop playsinline src="${src}"></video>`;
      return;
    }
    const H = ph.clientHeight, k = H / 1920; if (!H) return;
    const fr = this.st.frame, noBg = !S.bg.length, layout = d.layout === "split" && !noBg ? "split" : "full";
    let html = "";
    if (layout === "full") {
      const z = clamp(d.zoom, 0.35, 1), fgH = (1080 * this.st.ar) / z, top = (1920 - fgH) / 2 - 80;
      html += fr ? `<img class="blur" src="${fr}" alt=""><div class="fg" style="top:${top * k}px;height:${fgH * k}px">
        <img src="${fr}" alt="" style="width:${100 / z}%;left:${-((1 / z - 1) / 2) * 100}%"></div>` : "";
    } else {
      const top = Math.floor((1920 * clamp(d.split, 0.4, 0.75)) / 2) * 2;
      const pick = S.bg.find((b) => b.name === d.bg) || S.bg[0];
      html += `<div class="top" style="top:0;height:${top * k}px">${fr ? `<img src="${fr}" alt="">` : ""}</div>
        <div class="bot" style="top:${top * k}px;height:${(1920 - top) * k}px">
        ${pick ? `<img src="/api/bg/${encodeURIComponent(pick.name)}/thumb" alt="">` : `<div class="ph">Background loop</div>`}</div>`;
    }
    if (d.captions) {
      const y = d.y != null ? d.y * 1920 : layout === "split" ? Math.floor((1920 * clamp(d.split, 0.4, 0.75)) / 2) * 2 : 1480;
      const r = fontRatio(d.font), words = this.sample(), hi = Math.min(1, words.length - 1);
      const text = words.map((w, i) => { const t = esc(d.upper ? w.toUpperCase() : w);
        return i === hi ? `<span style="color:${d.hl};${d.pop ? "font-size:1.18em;vertical-align:-.04em" : ""}">${t}</span>` : t; }).join(" ");
      html += `<div class="capt" style="top:${y * k}px;font-family:'${esc(d.font)}';font-size:${d.size * k * r}px;color:${d.color};
        -webkit-text-stroke:${d.outline_w * 2 * k}px ${d.outline};text-shadow:${d.shadow * k}px ${d.shadow * k}px 0 rgba(0,0,0,.5)">${text}</div>`;
    }
    if (this.st.hook.trim()) {
      const r = fontRatio(d.font), pad = 18 * k;
      html += `<div class="hookp" style="top:${180 * k}px"><span style="background:${d.hook_bg};color:${d.hook_color};font-family:'${esc(d.font)}';
        font-size:${d.hook_size * k * r}px;padding:${pad * 0.5}px ${pad}px">${esc(d.upper ? this.st.hook.toUpperCase() : this.st.hook)}</span></div>`;
    }
    ph.innerHTML = html || `<div class="work"><p>Loading frame…</p></div>`;
    const cap = $(".capt", ph); if (cap) this.dragify(cap, ph);
  },
  sample() {
    const c = clipById(this.st.jid, this.st.cid), off = c.start - c.src_start, n = this.st.draft.words;
    const ws = this.st.words.filter((w) => w.s >= off && w.s < off + (c.end - c.start)).map((w) => w.w.replace(/[{}\\]/g, ""));
    const mid = Math.max(0, Math.floor(ws.length / 2) - 1);
    return ws.length ? ws.slice(mid, mid + n) : ["Your", "caption", "here"].slice(0, n);
  },
  dragify(cap, ph) {
    cap.onpointerdown = (e) => {
      cap.setPointerCapture(e.pointerId); cap.classList.add("drag");
      const move = (ev) => { const r = ph.getBoundingClientRect(), y = clamp((ev.clientY - r.top) / r.height, 0.05, 0.95);
        this.st.draft.y = Math.round(y * 1000) / 1000; this.st.dirty = true; cap.style.top = `${y * r.height}px`;
        const o = $(".panel output[data-for=y]", this.el), rg = $(".panel input[data-k=y]", this.el);
        if (o) o.textContent = `${Math.round(y * 100)}%`; if (rg) rg.value = Math.round(y * 100); };
      cap.onpointermove = move;
      cap.onpointerup = () => { cap.onpointermove = null; cap.classList.remove("drag"); this.note(); };
    };
  },

  /* ---------- step panels ---------- */
  panel() {
    const p = $(".panel", this.el), d = this.st.draft;
    p.innerHTML = [this.pLayout, this.pCaptions, this.pHook, this.pTrim, this.pRender][this.st.step].call(this, d);
    // shared wiring: ranges, toggles, swatches, option buttons
    $$("input[type=range][data-k]", p).forEach((r) => (r.oninput = () => {
      const k = r.dataset.k, scale = +r.dataset.scale || 1, v = +r.value / scale;
      const o = $(`output[data-for=${k}]`, p); if (o) o.textContent = r.dataset.unit === "%" ? `${r.value}%` : r.value + (r.dataset.unit || "");
      k === "hook_secs" || k === "hook_size" || k in d ? this.set(k, v) : null;
    }));
    $$("input[type=checkbox][data-k]", p).forEach((t) => (t.onchange = () => this.set(t.dataset.k, t.checked, t.dataset.k === "captions")));
    $$("[data-sw]", p).forEach((b) => (b.onclick = () => this.set(b.dataset.sw, b.dataset.c, true)));
    $$("input[type=color][data-k]", p).forEach((t) => (t.oninput = () => this.set(t.dataset.k, t.value.toUpperCase())));
    $$("[data-set]", p).forEach((b) => (b.onclick = () => { const [k, v] = b.dataset.set.split("="); this.set(k, isNaN(+v) || v === "" ? v : +v, true); }));
    const bind = this["b" + STEPS[this.st.step]]; if (bind) bind.call(this, p, d);
  },
  range(label, k, min, max, val, unit = "", scale = 1) {
    return `<div class="grp"><label for="r-${k}">${label} <output data-for="${k}">${val}${unit === "%" ? "%" : unit}</output></label>
      <input id="r-${k}" type="range" data-k="${k}" data-unit="${unit}" data-scale="${scale}" min="${min}" max="${max}" value="${val}"></div>`;
  },
  swatches(label, k, cur) {
    return `<div class="grp"><div class="lab">${label}</div><div class="swatches">
      ${PALETTE.map((c) => `<button class="sw" data-sw="${k}" data-c="${c}" style="background:${c}" aria-label="${label} ${c}" aria-pressed="${cur.toUpperCase() === c}"></button>`).join("")}
      <input type="color" data-k="${k}" value="${cur}" aria-label="Custom ${label.toLowerCase()}"></div></div>`;
  },

  pLayout(d) {
    const opt = (l, title, sub) => `<button class="opt" data-set="layout=${l}" aria-pressed="${d.layout === l}">
      <div class="mini ${l}">${l === "full" ? "<i></i>" : "<i></i><i></i>"}</div><b>${title}</b><small>${sub}</small></button>`;
    let more;
    if (d.layout === "split") {
      const loops = S.bg.length
        ? `<div class="loops"><button class="loop rand" data-set="bg=" aria-pressed="${!d.bg}">Random</button>
            ${S.bg.map((b) => `<button class="loop" data-set="bg=${esc(b.name)}" aria-pressed="${d.bg === b.name}" style="background-image:url('/api/bg/${encodeURIComponent(b.name)}/thumb')"><span>${esc(b.name)}</span></button>`).join("")}</div>`
        : `<p class="warn">No background loops yet. Split clips render as Full until you add some.</p>`;
      more = this.range("Game height", "split", 40, 75, Math.round(d.split * 100), "%", 100) +
        `<div class="grp"><div class="lab">Background loop <button class="btn sm openbg">Open loops folder</button></div>${loops}
         <small class="keys">Drop .mp4 gameplay files into the folder, then press Refresh loops.</small>
         <button class="btn sm refreshbg" style="align-self:flex-start">Refresh loops</button></div>`;
    } else {
      more = this.range("Width of the stream kept", "zoom", 35, 100, Math.round(d.zoom * 100), "%", 100) +
        `<p class="lead" style="margin:0">Lower zooms in on the action; 100% keeps the whole 16:9 frame.</p>`;
    }
    return `<h3>Layout</h3><p class="lead">How the 16:9 stream fits a 9:16 phone screen.</p>
      <div class="opts">${opt("full", "Full", "Stream centred over a blurred copy")}${opt("split", "Split", "Stream on top, gameplay loop below")}</div>${more}`;
  },
  bLayout(p) {
    const ob = $(".openbg", p); if (ob) ob.onclick = () => api("/api/bg/open", "POST").catch((e) => toast(esc(e.message), true));
    const rb = $(".refreshbg", p); if (rb) rb.onclick = () => loadBg().then(() => { this.panel(); this.drawPhone(); });
  },

  pCaptions(d) {
    const tg = (k, label) => `<label class="tg"><input type="checkbox" data-k="${k}" ${d[k] ? "checked" : ""}>${label}</label>`;
    if (!d.captions) return `<h3>Captions</h3><p class="lead">Word-by-word captions from the stream audio.</p>
      <div class="toggles">${tg("captions", "Show captions")}</div>`;
    return `<h3>Captions</h3><p class="lead">Word-by-word captions from the stream audio. The highlighted word follows the speech.</p>
      <div class="grp"><div class="lab">Quick looks</div><div class="presets">
        ${PRESETS.map(([[a, b], p], i) => `<button class="preset" data-preset="${i}" aria-label="${a} ${b} preset" style="font-family:'${p.font}';color:${p.color}">${a.toUpperCase()} <span style="color:${p.hl}">${b.toUpperCase()}</span></button>`).join("")}</div></div>
      <div class="grp"><div class="lab">Font</div><div class="fonts">
        ${S.fonts.map((f) => `<button class="font" data-set="font=${esc(f.name)}" aria-pressed="${d.font === f.name}"><b style="font-family:'${esc(f.name)}'">GTA 6 CLIP</b><small>${esc(f.name)}</small></button>`).join("")}</div></div>
      ${this.range("Size", "size", 50, 160, d.size)}
      ${this.swatches("Text colour", "color", d.color)}
      ${this.swatches("Highlight colour", "hl", d.hl)}
      ${this.swatches("Outline colour", "outline", d.outline)}
      ${this.range("Outline width", "outline_w", 0, 14, d.outline_w)}
      ${this.range("Shadow", "shadow", 0, 8, d.shadow)}
      <div class="grp"><div class="lab">Words on screen at once</div><div class="seg" role="group" aria-label="Words per line">
        ${[1, 2, 3, 4].map((n) => `<button data-set="words=${n}" aria-pressed="${d.words === n}">${n}</button>`).join("")}</div></div>
      <div class="grp"><label for="r-y">Position <output data-for="y">${d.y == null ? "Auto" : Math.round(d.y * 100) + "%"}</output></label>
        <input id="r-y" type="range" data-k="y" min="5" max="95" value="${Math.round((d.y ?? (d.layout === "split" ? d.split : 0.77)) * 100)}">
        <div style="display:flex;gap:6px;align-items:center"><button class="btn sm autoy">Auto</button>
        <small class="keys">Auto puts it on the split line, or the lower third in Full. You can also drag it on the preview.</small></div></div>
      <div class="toggles">${tg("captions", "Show captions")}${tg("upper", "ALL CAPS")}${tg("pop", "Pop the spoken word")}</div>`;
  },
  bCaptions(p, d) {
    $$("[data-preset]", p).forEach((b) => (b.onclick = () => { Object.assign(d, PRESETS[+b.dataset.preset][1]); this.st.dirty = true; this.drawPhone(); this.panel(); }));
    const ry = $("input[data-k=y]", p);
    if (ry) ry.oninput = () => { this.set("y", +ry.value / 100); $("output[data-for=y]", p).textContent = `${ry.value}%`; };
    const ay = $(".autoy", p); if (ay) ay.onclick = () => this.set("y", null, true);
  },

  pHook(d) {
    return `<h3>Hook</h3><p class="lead">A banner over the first seconds that gives viewers a reason to keep watching. Leave it empty for none.</p>
      <div class="grp"><label for="hooktxt">Hook text</label>
        <input class="field" id="hooktxt" maxlength="80" placeholder="e.g. HE DID NOT SEE THAT COMING" value="${esc(this.st.hook)}"></div>
      ${this.range("Size", "hook_size", 40, 100, d.hook_size)}
      ${this.range("Show for", "hook_secs", 1, 6, d.hook_secs, "s")}
      ${this.swatches("Banner colour", "hook_bg", d.hook_bg)}
      ${this.swatches("Text colour", "hook_color", d.hook_color)}`;
  },
  bHook(p) {
    const t = $("#hooktxt", p);
    t.oninput = () => { this.st.hook = t.value; this.st.dirty = true; this.drawPhone(); this.note(); };
  },

  pTrim() {
    const c = clipById(this.st.jid, this.st.cid);
    const s = c.start + this.st.dS, e = c.end + this.st.dE, lo = c.src_start, hi = c.src_start + c.src_dur;
    const row = (label, k, v) => `<div class="trimrow"><span class="lab">${label}</span><span class="tcv">${tc(v)}</span>
      <div class="seg" role="group" aria-label="${label}">${[-2, -1, 1, 2].map((n) => `<button data-t="${k}:${n}">${n > 0 ? "+" : "−"}${Math.abs(n)}s</button>`).join("")}</div></div>`;
    return `<h3>Trim</h3><p class="lead">Chat reacts a few seconds after the moment. If the setup is cut off, start earlier.</p>
      ${row("Start", "dS", s)}${row("End", "dE", e)}
      <dl class="summary"><dt>Length</dt><dd>${Math.round(e - s)}s</dd>
      <dt>Room to extend</dt><dd>${Math.round(s - lo)}s before · ${Math.round(hi - e)}s after</dd></dl>`;
  },
  bTrim(p) {
    const c = clipById(this.st.jid, this.st.cid);
    $$("[data-t]", p).forEach((b) => (b.onclick = () => {
      const [k, n] = b.dataset.t.split(":"), lo = c.src_start, hi = c.src_start + c.src_dur;
      const s = c.start + this.st.dS + (k === "dS" ? +n : 0), e = c.end + this.st.dE + (k === "dE" ? +n : 0);
      if (s < lo || e > hi || e - s < 3) return toast("That goes past the footage kept for this clip.", true);
      this.st[k] += +n; this.st.dirty = true; this.panel(); this.note();
    }));
  },

  pRender(d) {
    const c = clipById(this.st.jid, this.st.cid), j = jobById(this.st.jid), busy = this.st.wait !== null;
    const n = j.clips.filter((x) => x.status === "ready" || x.status === "error").length;
    return `<h3>Render</h3><p class="lead">Check the summary, then render. Your choices only apply when you render.</p>
      <dl class="summary">
        <dt>Layout</dt><dd>${d.layout === "split" ? `Split, ${Math.round(d.split * 100)}% stream${S.bg.length ? "" : " (no loops yet, renders as Full)"}` : `Full, ${Math.round(d.zoom * 100)}% width`}</dd>
        <dt>Captions</dt><dd>${d.captions ? `${esc(d.font)} ${d.size}, ${d.words} word${d.words > 1 ? "s" : ""}, ${d.upper ? "caps" : "as spoken"}` : "Off"}</dd>
        <dt>Colours</dt><dd><span style="color:${d.color}">■</span> text <span style="color:${d.hl}">■</span> highlight <span style="color:${d.outline}">■</span> outline</dd>
        <dt>Position</dt><dd>${d.y == null ? "Auto" : Math.round(d.y * 100) + "% down"}</dd>
        <dt>Hook</dt><dd>${this.st.hook.trim() ? `“${esc(this.st.hook)}” for ${d.hook_secs}s` : "None"}</dd>
        <dt>Length</dt><dd>${Math.round(c.end + this.st.dE - c.start - this.st.dS)}s</dd>
      </dl>
      <div class="big"><button class="btn go rthis" ${busy ? "disabled" : ""}>${busy ? "Rendering…" : "Render this clip"}</button>
        <small>About 20 seconds. The preview switches to the result when it's done.</small></div>
      <div class="big"><button class="btn rall" ${busy ? "disabled" : ""}>Apply style to all ${n} finished clips</button>
        <small>Re-renders every finished clip in this job with this layout and caption style. Hooks and trims stay per clip.</small></div>
      <div class="big"><button class="btn rdef">Save as default style</button>
        <small>Clips from now on, in every job, start with this style.</small></div>`;
  },
  bRender(p, d) {
    const { jid, cid } = this.st;
    const style = () => ({ ...d });
    const renderThis = async () => {
      const c = clipById(jid, cid);
      await api(`/api/jobs/${jid}/clips/${cid}`, "POST", { d_start: this.st.dS, d_end: this.st.dE, hook: this.st.hook, style: style() });
      this.st.wait = c.v; this.st.dS = 0; this.st.dE = 0; this.st.dirty = false; this.panel(); this.note(); refresh();
    };
    $(".rthis", p).onclick = () => renderThis().catch((e) => toast(esc(e.message), true));
    $(".rall", p).onclick = async () => {
      try {
        if (this.st.dS || this.st.dE || this.st.hook !== (clipById(jid, cid).hook || "")) await renderThis();
        await api(`/api/jobs/${jid}/style`, "POST", { style: style() });
        this.st.wait = clipById(jid, cid).v; this.st.dirty = false;
        toast("Re-rendering every finished clip with this style"); this.panel(); refresh();
      } catch (e) { toast(esc(e.message), true); }
    };
    $(".rdef", p).onclick = () => api("/api/style", "PUT", { style: style() })
      .then((r) => { S.defStyle = r.style; toast("Saved as the default style for new clips"); })
      .catch((e) => toast(esc(e.message), true));
  },
};

/* ---------------- keys ---------------- */
document.addEventListener("keydown", (e) => {
  if (e.target.matches?.("input:not([type=range]):not([type=checkbox]), textarea, select")) return;
  if (Viewer.el) return Viewer.key(e);
  if (Studio.el && e.key === "Escape" && !document.fullscreenElement) Studio.close();
});

/* ---------------- boot ---------------- */
async function loadBg() { try { S.bg = await api("/api/bg"); } catch { S.bg = []; } }
async function loadFonts() {
  try {
    const r = await api("/api/style"); S.defStyle = r.style; S.fonts = r.fonts;
    await Promise.all(r.fonts.map((f) => new FontFace(f.name, `url(/fonts/${encodeURIComponent(f.file)})`).load()
      .then((ff) => document.fonts.add(ff)).catch(() => {})));
    if (Studio.el) { Studio.panel(); Studio.drawPhone(); }
  } catch {}
}
Promise.all([loadBg(), loadFonts(), refresh()]).then(() => {
  render();
  const m = location.hash.match(/^#(edit|view)=([\w-]+)\/(clip_\d+)(?:&step=(\d))?/); // deep link, e.g. #edit=<job>/clip_003
  if (m) { select(m[2]); m[1] === "edit" ? Studio.open(m[2], m[3], +(m[4] || 0)) : Viewer.open(m[2], m[3]); }
});
setInterval(refresh, 1500);
window.addEventListener("resize", () => render());
