// anidl frontend: hash-routed single page app, no build step.

const view = document.getElementById("view");
const nav = document.getElementById("nav");
let status = null;          // /api/status
let pollTimer = null;       // downloads page refresh
let badgeTimer = null;      // queue badge refresh

// Helpers ------------------------------------------------------------------

const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const $ = (sel, root = view) => root.querySelector(sel);
const $$ = (sel, root = view) => [...root.querySelectorAll(sel)];

class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

async function api(path, { method = "GET", body } = {}) {
  const resp = await fetch(path, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
    credentials: "same-origin",
  });
  let data = null;
  try { data = await resp.json(); } catch { /* empty body */ }
  if (resp.status === 401 && !path.startsWith("/api/login")) { location.hash = "#/login"; throw new ApiError("Please log in.", 401); }
  if (resp.status === 409) { location.hash = "#/setup"; throw new ApiError("Finish setup first.", 409); }
  if (!resp.ok) {
    let detail = data?.detail;
    if (Array.isArray(detail)) detail = detail.map((d) => d.msg).join(", ");
    throw new ApiError(detail || `Request failed (${resp.status})`, resp.status);
  }
  return data;
}

function toast(message, bad = false) {
  const el = document.createElement("div");
  el.className = "toast" + (bad ? " bad" : "");
  el.textContent = message;
  document.getElementById("toasts").append(el);
  setTimeout(() => el.remove(), bad ? 6000 : 3500);
}

function loading(text = "Loading…") {
  return `<div class="loading"><div class="spinner"></div><span>${esc(text)}</span></div>`;
}

async function busy(button, fn) {
  const label = button.innerHTML;
  button.disabled = true;
  button.innerHTML = `<div class="spinner" style="width:18px;height:18px;border-width:2px"></div>`;
  try { return await fn(); }
  finally { button.disabled = false; button.innerHTML = label; }
}

function params() {
  const query = location.hash.split("?")[1] || "";
  return Object.fromEntries(new URLSearchParams(query));
}

function bytes(n) {
  if (n == null) return "";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1000 && i < units.length - 1) { n /= 1000; i++; }
  return `${n.toFixed(i > 2 ? 1 : 0)} ${units[i]}`;
}

function formData(form) {
  const data = {};
  for (const el of form.elements) {
    if (!el.name) continue;
    data[el.name] = el.type === "checkbox" ? el.checked : el.value;
  }
  return data;
}

function sheet(html) {
  const backdrop = document.createElement("div");
  backdrop.className = "sheet-backdrop";
  backdrop.innerHTML = `<div class="sheet" role="dialog" aria-modal="true">${html}</div>`;
  backdrop.addEventListener("click", (e) => { if (e.target === backdrop) backdrop.remove(); });
  document.body.append(backdrop);
  return backdrop;
}

// Small metadata cache so result lists don't refetch posters
const metaCache = new Map();
const metaQueue = [];
let metaActive = 0;
function fetchMeta(title) {
  if (metaCache.has(title)) return metaCache.get(title);
  const promise = new Promise((resolve) => metaQueue.push({ title, resolve }));
  metaCache.set(title, promise);
  pumpMeta();
  return promise;
}
function pumpMeta() {
  while (metaActive < 3 && metaQueue.length) {
    const { title, resolve } = metaQueue.shift();
    metaActive++;
    api(`/api/meta?title=${encodeURIComponent(title)}`)
      .then((d) => resolve(d.meta)).catch(() => resolve(null))
      .finally(() => { metaActive--; pumpMeta(); });
  }
}

// Router ----------------------------------------------------------------------

const routes = {
  "": browseView,
  "search": searchView,
  "show": showView,
  "downloads": downloadsView,
  "settings": settingsView,
  "setup": setupView,
  "login": loginView,
};

async function route() {
  clearInterval(pollTimer);
  document.querySelector(".actionbar")?.remove();
  document.querySelector(".sheet-backdrop")?.remove();
  const name = location.hash.replace(/^#\/?/, "").split("?")[0];
  try {
    status = await api("/api/status");
  } catch (e) {
    view.innerHTML = `<div class="error-box">Can't reach anidl: ${esc(e.message)}</div>`;
    return;
  }
  // Fresh install or unfinished setup always lands in the wizard
  if (!status.has_admin && name !== "setup") return void (location.hash = "#/setup");
  if (status.has_admin && !status.logged_in && name !== "login") return void (location.hash = "#/login");
  if (status.logged_in && !status.setup_complete && name !== "setup") return void (location.hash = "#/setup");

  const ready = status.logged_in && status.setup_complete;
  nav.hidden = !ready;
  document.body.style.paddingBottom = ready ? "" : "0";
  for (const a of $$("[data-nav]", nav)) {
    a.classList.toggle("active", a.dataset.nav === (name || "browse") || (name === "show" && a.dataset.nav === "search"));
  }
  if (ready && !badgeTimer) { refreshBadge(); badgeTimer = setInterval(refreshBadge, 10000); }
  window.scrollTo(0, 0);
  (routes[name] || browseView)();
}

async function refreshBadge() {
  try {
    const { batches } = await api("/api/queue");
    const active = batches.reduce((n, b) => n + b.counts.queued + b.counts.running, 0);
    const badge = document.getElementById("queue-badge");
    badge.hidden = active === 0;
    badge.textContent = active;
  } catch { /* shown elsewhere */ }
}

window.addEventListener("hashchange", route);
route();

// Login -----------------------------------------------------------------------------

function loginView() {
  view.innerHTML = `
    <form class="login card" id="login">
      <h1>Log in</h1>
      <p class="muted">Sign in with the admin account you created during setup.</p>
      <div id="err"></div>
      <label class="field"><span>Username</span><input type="text" name="username" autocomplete="username" autocapitalize="off" required></label>
      <label class="field"><span>Password</span><input type="password" name="password" autocomplete="current-password" required></label>
      <button class="primary block" type="submit">Log in</button>
      <p class="hint" style="margin-top:1rem">Locked out? Run <code>anidl-setup --reset-password</code> in the container's console.</p>
    </form>`;
  $("#login").addEventListener("submit", async (e) => {
    e.preventDefault();
    const button = $("button[type=submit]");
    try {
      await busy(button, () => api("/api/login", { method: "POST", body: formData(e.target) }));
      location.hash = "#/";
    } catch (err) {
      $("#err").innerHTML = `<div class="error-box">${esc(err.message)}</div>`;
    }
  });
}

// Browse ------------------------------------------------------------------------------

async function browseView() {
  view.innerHTML = `
    <div class="searchbar"><input type="search" id="q" placeholder="Search anime…" enterkeyhint="search" aria-label="Search anime"></div>
    <div class="section-head"><h2>Trending now</h2></div>
    <div id="trending">${loading()}</div>
    <div class="section-head"><h2>This season</h2></div>
    <div id="seasonal">${loading()}</div>`;
  $("#q").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.target.value.trim()) location.hash = `#/search?q=${encodeURIComponent(e.target.value.trim())}`;
  });
  for (const kind of ["trending", "seasonal"]) {
    api(`/api/browse/${kind}`).then(({ items, error }) => {
      const el = $(`#${kind}`);
      if (!el) return;
      if (!items.length) { el.innerHTML = `<p class="muted">${esc(error || "Nothing to show.")}</p>`; return; }
      el.innerHTML = `<div class="posters">${items.map((m) => `
        <a class="poster" href="#/search?q=${encodeURIComponent(m.title)}">
          <div class="art" style="background-image:url('${esc(m.cover)}')"></div>
          <div class="name">${esc(m.title)}</div>
          <div class="sub">${esc([m.format?.replace("_", " "), m.year, m.episodes && `${m.episodes} eps`].filter(Boolean).join(" · "))}</div>
        </a>`).join("")}</div>`;
    }).catch((e) => { const el = $(`#${kind}`); if (el) el.innerHTML = `<p class="muted">${esc(e.message)}</p>`; });
  }
}

// Search -------------------------------------------------------------------------------

async function searchView() {
  const q = params().q || "";
  const dub = params().dub === "1";
  view.innerHTML = `
    <form class="searchbar" id="search">
      <input type="search" name="q" value="${esc(q)}" placeholder="Search anime…" enterkeyhint="search" aria-label="Search anime" ${q ? "" : "autofocus"}>
      <button class="primary" type="submit">Search</button>
    </form>
    <div class="row" style="margin-bottom:1rem">
      <div class="segmented" role="group" aria-label="Audio">
        <button type="button" data-dub="0" class="${dub ? "" : "on"}">Sub</button>
        <button type="button" data-dub="1" class="${dub ? "on" : ""}">Dub</button>
      </div>
      <span class="muted small grow">Tick several results to queue them together, e.g. every season.</span>
    </div>
    <div id="results">${q ? loading("Searching…") : ""}</div>`;
  $("#search").addEventListener("submit", (e) => {
    e.preventDefault();
    const value = e.target.q.value.trim();
    if (value) location.hash = `#/search?q=${encodeURIComponent(value)}${dub ? "&dub=1" : ""}`;
  });
  for (const b of $$("[data-dub]")) {
    b.addEventListener("click", () => { location.hash = `#/search?q=${encodeURIComponent(q)}${b.dataset.dub === "1" ? "&dub=1" : ""}`; });
  }
  if (!q) return;

  let results;
  try {
    ({ results } = await api(`/api/search?q=${encodeURIComponent(q)}&dub=${dub}`));
  } catch (e) {
    $("#results").innerHTML = `<div class="error-box">${esc(e.message)}</div>`;
    return;
  }
  if (!results.length) { $("#results").innerHTML = `<p class="muted">No results for “${esc(q)}”.</p>`; return; }

  const selected = new Set();
  $("#results").innerHTML = `<div class="results">${results.map((r) => `
    <div class="result" data-index="${r.index}">
      <div class="thumb" data-title="${esc(r.title)}"></div>
      <a class="body" href="#/show?q=${encodeURIComponent(q)}&i=${r.index}&t=${encodeURIComponent(r.title)}${dub ? "&dub=1" : ""}">
        <span class="title">${esc(r.title)}</span>
        <span class="meta" data-meta>${r.title_known ? "" : "Only result"}</span>
      </a>
      <label class="pick" aria-label="Select ${esc(r.title)}"><input type="checkbox" value="${r.index}"></label>
    </div>`).join("")}</div>`;

  for (const thumb of $$(".thumb[data-title]")) {
    fetchMeta(thumb.dataset.title).then((m) => {
      if (!m) return;
      thumb.style.backgroundImage = `url('${m.cover}')`;
      const metaEl = thumb.parentElement.querySelector("[data-meta]");
      metaEl.textContent = [m.format?.replace("_", " "), m.year, m.episodes && `${m.episodes} eps`, m.score && `${m.score}%`].filter(Boolean).join(" · ");
    });
  }

  const bar = document.createElement("div");
  bar.className = "actionbar";
  bar.hidden = true;
  bar.innerHTML = `<div class="summary"><b id="sel-count"></b><span class="muted">All episodes of each</span></div>
    <button class="ghost" id="sel-clear">Clear</button><button class="primary" id="sel-queue">Queue…</button>`;
  document.body.append(bar);
  const update = () => {
    bar.hidden = selected.size === 0;
    bar.querySelector("#sel-count").textContent = `${selected.size} selected`;
    for (const el of $$(".result")) el.classList.toggle("selected", selected.has(Number(el.dataset.index)));
  };
  for (const box of $$(".pick input")) {
    box.addEventListener("change", () => { box.checked ? selected.add(Number(box.value)) : selected.delete(Number(box.value)); update(); });
  }
  bar.querySelector("#sel-clear").addEventListener("click", () => { selected.clear(); $$(".pick input").forEach((b) => (b.checked = false)); update(); });
  bar.querySelector("#sel-queue").addEventListener("click", () => {
    const picked = results.filter((r) => selected.has(r.index)).map((r) => ({ query: q, index: r.index, title: r.title }));
    queueSheet(picked, dub);
  });
}

async function queueSheet(picked, dub) {
  const s = sheet(loading("Reading episode lists…"));
  const box = s.querySelector(".sheet");
  let planned, locs, settings;
  try {
    [planned, locs, settings] = await Promise.all([
      api("/api/plan", { method: "POST", body: { entries: picked, dub } }),
      api("/api/locations"),
      api("/api/settings"),
    ]);
  } catch (e) {
    box.innerHTML = `<div class="error-box">${esc(e.message)}</div><button class="block" data-close>Close</button>`;
    box.querySelector("[data-close]").onclick = () => s.remove();
    return;
  }
  box.innerHTML = `
    <h2>Queue ${planned.entries.length} ${planned.entries.length === 1 ? "entry" : "entries"}</h2>
    <form id="qform">
      <label class="field"><span>Series folder name</span><input type="text" name="series_name" value="${esc(planned.series_name)}" required>
        <div class="hint">All seasons go in this folder so Jellyfin/Plex show them as one series. Keep the (year): it helps them match the show.</div></label>
      <div class="grid2">
        <label class="field"><span>Download to</span>${locationSelect(locs.locations)}</label>
        <label class="field"><span>Quality</span>${qualitySelect(settings.quality)}</label>
      </div>
      ${planned.entries.map((e, i) => `
        <div class="card" style="padding:12px">
          <b>${esc(e.title)}</b>
          <div class="muted small">${e.error ? esc(e.error) : `${e.episodes.length} episodes`}${e.special ? " · special" : ""}</div>
          <div class="row" style="margin-top:.5rem">
            <label class="field grow" style="margin:0"><span>Season</span><input type="number" min="0" max="99" name="season-${i}" value="${e.season}"></label>
            <label class="field grow" style="margin:0"><span>Episode offset</span><input type="number" min="0" name="offset-${i}" value="${e.offset}"></label>
          </div>
        </div>`).join("")}
      <p class="hint">Offset continues numbering for later parts of a season (offset 12 makes episode 1 → E13).</p>
      <div class="row end"><button type="button" class="ghost" data-close>Cancel</button><button class="primary" type="submit">Add to queue</button></div>
    </form>`;
  box.querySelector("[data-close]").onclick = () => s.remove();
  box.querySelector("#qform").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const f = formData(ev.target);
    const entries = planned.entries.map((e, i) => ({
      query: e.query, index: e.index, title: e.title, episodes: e.episodes,
      season: Number(f[`season-${i}`]), offset: Number(f[`offset-${i}`] || 0),
    })).filter((e) => e.episodes.length);
    try {
      const r = await busy(ev.submitter, () => api("/api/queue", { method: "POST", body: {
        series_name: f.series_name, location_id: Number(f.location_id), mode: dub ? "dub" : "sub", quality: f.quality, entries,
      } }));
      s.remove();
      queuedToast(r);
    } catch (e) { toast(e.message, true); }
  });
}

function queuedToast(r) {
  const parts = [`Queued ${r.added} episode${r.added === 1 ? "" : "s"}`];
  if (r.skipped_existing) parts.push(`${r.skipped_existing} already downloaded`);
  if (r.skipped_queued) parts.push(`${r.skipped_queued} already queued`);
  toast(parts.join(" · "));
  refreshBadge();
}

function locationSelect(locs, selectedId) {
  if (!locs.length) return `<div class="warn-box">No download locations. Add one in Settings.</div>`;
  const chosen = selectedId ?? locs.find((l) => l.is_default)?.id;
  return `<select name="location_id">${locs.map((l) => `<option value="${l.id}" ${l.id === chosen ? "selected" : ""}>${esc(l.name)}${l.status && !(l.status.mounted && l.status.writable) ? " (offline)" : ""}</option>`).join("")}</select>`;
}

function qualitySelect(current) {
  const options = [["best", "Highest available"], ["1080", "Up to 1080p"], ["720", "Up to 720p"], ["480", "Up to 480p"], ["360", "Up to 360p"], ["worst", "Smallest"]];
  return `<select name="quality">${options.map(([v, l]) => `<option value="${v}" ${v === String(current) ? "selected" : ""}>${l}</option>`).join("")}</select>`;
}

// Show page ------------------------------------------------------------------------------

async function showView() {
  const p = params();
  const dub = p.dub === "1";
  view.innerHTML = loading("Loading seasons and episodes…");
  let data, locs, settings;
  try {
    [data, locs, settings] = await Promise.all([
      api(`/api/show?q=${encodeURIComponent(p.q)}&i=${p.i}&t=${encodeURIComponent(p.t)}&dub=${dub}`),
      api("/api/locations"),
      api("/api/settings"),
    ]);
  } catch (e) {
    view.innerHTML = `<div class="error-box">${esc(e.message)}</div><a class="btn" href="#/search?q=${encodeURIComponent(p.q || "")}">Back to search</a>`;
    return;
  }

  const meta = data.meta?.meta;
  const seasons = data.seasons;
  const picks = new Map(seasons.map((s) => [s.title, new Set()]));
  const existing = new Map();
  let active = seasons.findIndex((s) => s.title === p.t);
  if (active < 0) active = 0;
  let locationId = locs.locations.find((l) => l.is_default)?.id;

  view.innerHTML = `
    <a href="#/search?q=${encodeURIComponent(p.q)}${dub ? "&dub=1" : ""}" class="small">← Results</a>
    <div class="hero" style="margin-top:.75rem">
      <div class="art" style="background-image:url('${esc(meta?.cover || "")}')"></div>
      <div>
        <h1>${esc(data.series_name)}</h1>
        <div class="muted small">${esc([meta?.format?.replace("_", " "), meta?.year, meta?.status?.toLowerCase().replace("_", " "), meta?.score && `${meta.score}%`].filter(Boolean).join(" · "))}</div>
        <div class="genres">${(meta?.genres || []).slice(0, 5).map((g) => `<span class="pill">${esc(g)}</span>`).join("")}<span id="jf-badge"></span></div>
        ${meta?.description ? `<p class="synopsis clamp" id="syn">${esc(meta.description.replace(/<[^>]+>/g, ""))}</p>` : ""}
      </div>
    </div>

    <div class="card">
      <div class="grid2">
        <label class="field"><span>Series folder name</span><input type="text" id="series" value="${esc(data.series_name)}"></label>
        <label class="field"><span>Download to</span>${locationSelect(locs.locations, locationId)}</label>
      </div>
      <div class="row">
        <div class="segmented" role="group" aria-label="Audio">
          <button type="button" data-dub="0" class="${dub ? "" : "on"}">Sub</button>
          <button type="button" data-dub="1" class="${dub ? "on" : ""}">Dub</button>
        </div>
        <div class="grow" style="min-width:140px">${qualitySelect(settings.quality)}</div>
      </div>
    </div>

    <div class="section-head"><h2>Seasons</h2><button class="small" id="all-seasons">Select all seasons</button></div>
    <div class="tabs" id="tabs" role="tablist"></div>
    <div id="season"></div>
    <div class="pad-actionbar"></div>`;

  $("#syn")?.addEventListener("click", (e) => e.target.classList.toggle("clamp"));
  for (const b of $$("[data-dub]")) {
    b.addEventListener("click", () => { location.hash = `#/show?q=${encodeURIComponent(p.q)}&i=${p.i}&t=${encodeURIComponent(p.t)}${b.dataset.dub === "1" ? "&dub=1" : ""}`; });
  }
  api(`/api/mediaserver/series?name=${encodeURIComponent(data.series_name)}`).then((r) => {
    if (r.exists) $("#jf-badge").innerHTML = `<span class="pill ok">In ${serverName(settings.media_server)}</span>`;
  }).catch(() => {});

  const bar = document.createElement("div");
  bar.className = "actionbar";
  bar.innerHTML = `<div class="summary"><b id="pick-count">Nothing selected</b><span class="muted" id="pick-detail">Pick episodes below</span></div>
    <button class="primary" id="download" disabled>Download</button>`;
  document.body.append(bar);

  const refreshExisting = async () => {
    const select = $("select[name=location_id]");
    if (!select) return;
    locationId = Number(select.value);
    try {
      const r = await api("/api/existing", { method: "POST", body: {
        location_id: locationId, series_name: $("#series").value,
        entries: seasons.map((s) => ({ title: s.title, season: s.season, offset: s.offset, episodes: s.episodes })),
      } });
      existing.clear();
      for (const [title, eps] of Object.entries(r.existing)) existing.set(title, new Set(eps));
      renderSeason();
      renderTabs();
    } catch { /* ✓ marks are a nicety */ }
  };

  const updateBar = () => {
    const chosen = seasons.filter((s) => picks.get(s.title).size);
    const total = chosen.reduce((n, s) => n + picks.get(s.title).size, 0);
    bar.querySelector("#pick-count").textContent = total ? `${total} episode${total === 1 ? "" : "s"}` : "Nothing selected";
    bar.querySelector("#pick-detail").textContent = total
      ? chosen.map((s) => `S${String(s.season).padStart(2, "0")}${s.part > 1 ? ` pt ${s.part}` : ""}`).join(", ")
      : "Pick episodes below";
    bar.querySelector("#download").disabled = !total || !locationId;
  };

  const renderTabs = () => {
    $("#tabs").innerHTML = seasons.map((s, i) => {
      const n = picks.get(s.title).size;
      return `<button class="tab ${i === active ? "on" : ""}" role="tab" data-tab="${i}" aria-selected="${i === active}">
        <span>${s.special ? "Special" : `Season ${s.season}`}${s.part > 1 ? ` · Part ${s.part}` : ""}</span>
        <small>${s.episodes.length} eps${n ? ` · <span class="count">${n} picked</span>` : ""}</small></button>`;
    }).join("");
    for (const t of $$("[data-tab]")) t.addEventListener("click", () => { active = Number(t.dataset.tab); renderTabs(); renderSeason(); });
    updateBar();
  };

  const renderSeason = () => {
    const s = seasons[active];
    const chosen = picks.get(s.title);
    const have = existing.get(s.title) || new Set();
    $("#season").innerHTML = `
      <div class="card">
        <div class="muted small" style="margin-bottom:.5rem">${esc(s.title)}</div>
        ${s.error ? `<div class="error-box">${esc(s.error)}</div>` : ""}
        <div class="grid2 always">
          <label class="field"><span>Save as season</span><input type="number" min="0" max="99" id="season-no" value="${s.season}"></label>
          <label class="field"><span>Episode offset</span><input type="number" min="0" id="offset" value="${s.offset}"></label>
        </div>
        ${s.part > 1 || s.offset ? `<p class="hint" style="margin-top:-.5rem">Part ${s.part} continues the season: offset ${s.offset} saves episode 1 as E${String(s.offset + 1).padStart(2, "0")}.</p>` : ""}
        <div class="row" style="margin-bottom:.75rem">
          <button class="small" data-sel="all">All</button>
          <button class="small" data-sel="none">None</button>
          <button class="small" data-sel="missing">Not downloaded</button>
        </div>
        <form class="row" id="range" style="margin-bottom:1rem">
          <input type="text" class="grow" name="range" placeholder="e.g. 1-5, 8, 12-" inputmode="text" aria-label="Episode range">
          <button class="small" type="submit">Select range</button>
        </form>
        ${s.episodes.length ? `<div class="episodes">${s.episodes.map((e) => `
          <label class="ep ${chosen.has(e) ? "on" : ""} ${have.has(e) ? "have" : ""}" title="${have.has(e) ? "Already downloaded" : ""}">
            <input type="checkbox" value="${esc(e)}" ${chosen.has(e) ? "checked" : ""}>${esc(e)}</label>`).join("")}</div>`
          : `<p class="muted">No episodes available yet.</p>`}
      </div>`;
    $("#season-no").addEventListener("change", (e) => { s.season = Number(e.target.value); renderTabs(); refreshExisting(); });
    $("#offset").addEventListener("change", (e) => { s.offset = Number(e.target.value || 0); refreshExisting(); });
    for (const box of $$(".ep input")) {
      box.addEventListener("change", () => {
        box.checked ? chosen.add(box.value) : chosen.delete(box.value);
        box.parentElement.classList.toggle("on", box.checked);
        renderTabs();
      });
    }
    for (const b of $$("[data-sel]")) {
      b.addEventListener("click", () => {
        chosen.clear();
        if (b.dataset.sel === "all") s.episodes.forEach((e) => chosen.add(e));
        if (b.dataset.sel === "missing") s.episodes.filter((e) => !have.has(e)).forEach((e) => chosen.add(e));
        renderSeason(); renderTabs();
      });
    }
    $("#range").addEventListener("submit", (ev) => {
      ev.preventDefault();
      try {
        for (const e of parseRange(ev.target.range.value, s.episodes)) chosen.add(e);
        renderSeason(); renderTabs();
      } catch (err) { toast(err.message, true); }
    });
  };

  $("#all-seasons").addEventListener("click", () => {
    for (const s of seasons) {
      if (s.special) continue;
      const have = existing.get(s.title) || new Set();
      s.episodes.filter((e) => !(settings.skip_existing && have.has(e))).forEach((e) => picks.get(s.title).add(e));
    }
    renderSeason(); renderTabs();
    toast("Selected every episode of every season" + (settings.skip_existing ? " not already downloaded" : ""));
  });
  $("select[name=location_id]")?.addEventListener("change", refreshExisting);
  $("#series").addEventListener("change", refreshExisting);

  bar.querySelector("#download").addEventListener("click", async (ev) => {
    const entries = seasons.filter((s) => picks.get(s.title).size).map((s) => ({
      query: s.query, index: s.index, title: s.title, season: s.season, offset: s.offset,
      episodes: s.episodes.filter((e) => picks.get(s.title).has(e)),
    }));
    try {
      const r = await busy(ev.currentTarget, () => api("/api/queue", { method: "POST", body: {
        series_name: $("#series").value, location_id: locationId, mode: dub ? "dub" : "sub",
        quality: $("select[name=quality]").value, entries,
      } }));
      queuedToast(r);
      for (const set of picks.values()) set.clear();
      renderSeason(); renderTabs();
    } catch (e) { toast(e.message, true); }
  });

  renderTabs();
  renderSeason();
  refreshExisting();
}

// Mirrors naming.parse_episode_selection on the server
function parseRange(text, available) {
  const num = (v) => Number(v);
  const numeric = available.filter((e) => /^\d+(\.\d+)?$/.test(e));
  const chosen = new Set();
  for (const part of text.split(/[,\s]+/).filter(Boolean)) {
    const m = part.match(/^(\d+(?:\.\d+)?)?-(\d+(?:\.\d+)?)?$/);
    if (part === "all" || part === "*") available.forEach((e) => chosen.add(e));
    else if (m && (m[1] || m[2])) {
      const lo = m[1] ? num(m[1]) : -Infinity, hi = m[2] ? num(m[2]) : Infinity;
      if (lo > hi) throw new Error(`Range ${part} runs backwards`);
      numeric.filter((e) => num(e) >= lo && num(e) <= hi).forEach((e) => chosen.add(e));
    } else if (available.includes(part)) chosen.add(part);
    else if (/^\d+(\.\d+)?$/.test(part) && numeric.some((e) => num(e) === num(part))) numeric.filter((e) => num(e) === num(part)).forEach((e) => chosen.add(e));
    else throw new Error(`Episode ${part} doesn't exist`);
  }
  return available.filter((e) => chosen.has(e));
}

// Downloads ------------------------------------------------------------------------------------

const expanded = new Set();

async function downloadsView() {
  view.innerHTML = `
    <div class="section-head"><h1>Downloads</h1><button class="small" id="clear">Clear finished</button></div>
    <div id="queue">${loading()}</div>`;
  $("#clear").addEventListener("click", async () => {
    const r = await api("/api/queue/clear-finished", { method: "POST" });
    toast(`Removed ${r.removed} finished ${r.removed === 1 ? "batch" : "batches"}`);
    renderQueue();
  });
  await renderQueue();
  pollTimer = setInterval(renderQueue, 2000);
}

const STATUS_PILL = { queued: "", running: "accent", done: "ok", failed: "bad", cancelled: "warn", skipped: "" };

async function renderQueue() {
  let batches;
  try { ({ batches } = await api("/api/queue")); }
  catch (e) { if ($("#queue")) $("#queue").innerHTML = `<div class="error-box">${esc(e.message)}</div>`; return; }
  const el = $("#queue");
  if (!el) return;
  if (!batches.length) {
    el.innerHTML = `<div class="card muted">Nothing queued. Find something under <a href="#/">Browse</a> or <a href="#/search">Search</a>.</div>`;
    return;
  }
  el.innerHTML = batches.map((b) => {
    const total = b.items.length;
    const progress = b.items.reduce((n, i) => n + (i.status === "done" ? 100 : i.status === "running" ? i.progress : 0), 0) / Math.max(total, 1);
    const open = expanded.has(b.id) || b.counts.running > 0 && !expanded.has(-b.id);
    const state = b.counts.running ? `<span class="pill accent">Downloading</span>`
      : b.counts.queued ? `<span class="pill">Queued</span>`
      : b.counts.failed ? `<span class="pill bad">${b.counts.failed} failed</span>`
      : `<span class="pill ok">Done</span>`;
    return `<div class="batch" data-batch="${b.id}">
      <div class="batch-head" data-toggle="${b.id}" aria-expanded="${open}">
        <div class="grow">
          <b>${esc(b.series_name)} · S${String(b.season).padStart(2, "0")}</b>
          <div class="muted small">${esc(b.title)}</div>
          <div class="muted small">${b.counts.done}/${total} episodes · ${esc(b.mode)} · ${esc(b.location || "?")}</div>
          <div class="progress ${b.active ? "" : "done"}" style="margin-top:.4rem"><i style="width:${progress.toFixed(1)}%"></i></div>
        </div>
        ${state}
      </div>
      ${b.warning ? `<div class="warn-box" style="margin:0 14px 12px">${esc(b.warning)}</div>` : ""}
      ${open ? `
        <div class="batch-tools">
          <button class="small" data-b="up" aria-label="Move up">↑</button>
          <button class="small" data-b="down" aria-label="Move down">↓</button>
          ${b.counts.failed + b.counts.cancelled ? `<button class="small" data-b="retry">Retry failed</button>` : ""}
          ${b.active ? `<button class="small danger" data-b="cancel">Cancel</button>` : `<button class="small" data-b="remove">Remove</button>`}
        </div>
        <div class="items">${b.items.map((i) => `
          <div class="item" data-item="${i.id}">
            <span class="epno">E${esc(i.episode)}${b.offset && /^\d+$/.test(i.episode) ? `<div class="muted small">→ E${Number(i.episode) + b.offset}</div>` : ""}</span>
            <div style="min-width:0">
              <span class="pill ${STATUS_PILL[i.status]}">${esc(i.status)}</span>${i.quality ? ` <span class="pill">${esc(i.quality)}</span>` : ""}
              ${i.status === "running" ? `<div class="progress ${i.progress ? "" : "indeterminate"}" style="margin-top:.35rem"><i style="width:${i.progress}%"></i></div>` : ""}
              ${i.detail ? `<div class="detail">${esc(i.detail)}</div>` : ""}
              ${i.error ? `<div class="err">${esc(i.error)}</div>` : ""}
            </div>
            <div class="row">
              ${i.status === "queued" ? `<button class="small icon" data-i="up" aria-label="Move up">↑</button><button class="small icon" data-i="down" aria-label="Move down">↓</button>` : ""}
              ${["failed", "cancelled"].includes(i.status) ? `<button class="small" data-i="retry">Retry</button>` : ""}
              ${["queued", "running"].includes(i.status) ? `<button class="small icon danger" data-i="cancel" aria-label="Cancel">✕</button>` : ""}
            </div>
          </div>`).join("")}</div>` : ""}
    </div>`;
  }).join("");

  for (const head of $$("[data-toggle]")) {
    head.onclick = () => {
      const id = Number(head.dataset.toggle);
      const open = head.getAttribute("aria-expanded") === "true";
      expanded.delete(id); expanded.delete(-id);
      expanded.add(open ? -id : id);
      renderQueue();
    };
  }
  for (const b of $$("[data-b]")) {
    b.onclick = async () => {
      const id = b.closest("[data-batch]").dataset.batch;
      try {
        if (b.dataset.b === "remove") await api(`/api/batches/${id}`, { method: "DELETE" });
        else await api(`/api/batches/${id}/${b.dataset.b}`, { method: "POST" });
        renderQueue(); refreshBadge();
      } catch (e) { toast(e.message, true); }
    };
  }
  for (const b of $$("[data-i]")) {
    b.onclick = async () => {
      const id = b.closest("[data-item]").dataset.item;
      try { await api(`/api/items/${id}/${b.dataset.i}`, { method: "POST" }); renderQueue(); refreshBadge(); }
      catch (e) { toast(e.message, true); }
    };
  }
}

// Locations (shared by settings and setup) ---------------------------------------------------

function locationForm(loc = null, networkShares = true) {
  const type = loc?.type || (networkShares ? "smb" : "local");
  const c = loc?.config || {};
  return `
    <form class="locform" data-id="${loc?.id ?? ""}">
      <label class="field"><span>Name</span><input type="text" name="name" value="${esc(loc?.name || "")}" placeholder="NAS anime" required></label>
      ${loc ? `<input type="hidden" name="type" value="${type}">` : `
      <div class="field"><span class="muted small" style="font-weight:600">Type</span><br>
        <div class="segmented" role="group" aria-label="Location type">
          <button type="button" data-type="smb" class="${type === "smb" ? "on" : ""}" ${networkShares ? "" : "disabled"}>SMB</button>
          <button type="button" data-type="nfs" class="${type === "nfs" ? "on" : ""}" ${networkShares ? "" : "disabled"}>NFS</button>
          <button type="button" data-type="local" class="${type === "local" ? "on" : ""}">Local folder</button>
        </div>
        <input type="hidden" name="type" value="${type}">
      </div>`}
      <div data-fields="smb" ${type === "smb" ? "" : "hidden"}>
        <div class="grid2">
          <label class="field"><span>Server</span><input type="text" name="server" value="${esc(c.server || "")}" placeholder="192.168.1.10 or nas.lan" autocapitalize="off"></label>
          <label class="field"><span>Share</span><input type="text" name="share" value="${esc(c.share || "")}" placeholder="media" autocapitalize="off"></label>
        </div>
        <label class="field"><span>Subfolder (optional)</span><input type="text" name="subpath" value="${esc(c.subpath || "")}" placeholder="anime" autocapitalize="off"></label>
        <div class="grid2">
          <label class="field"><span>Username</span><input type="text" name="username" value="${esc(c.username || "")}" autocapitalize="off" autocomplete="off"></label>
          <label class="field"><span>Password</span><input type="password" name="password" autocomplete="new-password" placeholder="${loc ? "Leave blank to keep" : ""}"></label>
        </div>
        <div class="grid2">
          <label class="field"><span>Domain (optional)</span><input type="text" name="domain" value="${esc(c.domain || "")}" autocapitalize="off"></label>
          <label class="field"><span>SMB version</span><select name="vers">${["default", "3.1.1", "3.0", "2.1", "2.0", "1.0"].map((v) => `<option ${v === (c.vers || "default") ? "selected" : ""}>${v}</option>`).join("")}</select></label>
        </div>
      </div>
      <div data-fields="nfs" ${type === "nfs" ? "" : "hidden"}>
        <div class="grid2">
          <label class="field"><span>Server</span><input type="text" name="nfs_server" value="${esc(type === "nfs" ? c.server || "" : "")}" placeholder="192.168.1.10" autocapitalize="off"></label>
          <label class="field"><span>Export path</span><input type="text" name="export" value="${esc(c.export || "")}" placeholder="/mnt/pool/media/anime" autocapitalize="off"></label>
        </div>
        <label class="field"><span>Mount options (optional)</span><input type="text" name="options" value="${esc(c.options || "")}" placeholder="vers=4.1" autocapitalize="off"></label>
      </div>
      <div data-fields="local" ${type === "local" ? "" : "hidden"}>
        <label class="field"><span>Folder</span><input type="text" name="path" value="${esc(c.path || "")}" placeholder="/mnt/media/anime" autocapitalize="off">
          <div class="hint">Use this for a Proxmox bind mount or the container's own disk. Allowed under /mnt, /media, /srv, /data, /downloads, /storage or /home.</div></label>
      </div>
      <label class="field"><span>Media server sees this folder as (optional)</span><input type="text" name="server_path" value="${esc(loc?.server_path || "")}" placeholder="/media/anime" autocapitalize="off">
        <div class="hint">The same folder as Jellyfin or Plex sees it. Lets anidl scan only the new season folders instead of the whole anime library.</div></label>
      <div class="err"></div>
      <div class="row end">
        <button type="button" class="ghost" data-cancel>Cancel</button>
        <button type="submit" class="primary">${loc ? "Save & test" : "Add & test"}</button>
      </div>
    </form>`;
}

function bindLocationForm(form, onSaved, onCancel) {
  for (const b of $$("[data-type]", form)) {
    b.addEventListener("click", () => {
      form.type.value = b.dataset.type;
      for (const x of $$("[data-type]", form)) x.classList.toggle("on", x === b);
      for (const f of $$("[data-fields]", form)) f.hidden = f.dataset.fields !== b.dataset.type;
    });
  }
  $("[data-cancel]", form).addEventListener("click", onCancel);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const d = formData(form);
    const body = { name: d.name, type: d.type, server_path: d.server_path };
    if (d.type === "smb") Object.assign(body, { server: d.server, share: d.share, subpath: d.subpath, username: d.username, password: d.password, domain: d.domain, vers: d.vers });
    if (d.type === "nfs") Object.assign(body, { server: d.nfs_server, export: d.export, options: d.options });
    if (d.type === "local") body.path = d.path;
    const id = form.dataset.id;
    $(".err", form).innerHTML = "";
    try {
      await busy(e.submitter, () => api(id ? `/api/locations/${id}` : "/api/locations", { method: id ? "PUT" : "POST", body }));
      toast("Location saved: mounted and writable.");
      onSaved();
    } catch (err) {
      $(".err", form).innerHTML = `<div class="error-box">${esc(err.message)}</div>`;
    }
  });
}

async function locationsPanel(container, { onChange } = {}) {
  container.innerHTML = loading();
  let data;
  try { data = await api("/api/locations"); }
  catch (e) { container.innerHTML = `<div class="error-box">${esc(e.message)}</div>`; return; }
  const render = () => {
    container.innerHTML = `
      ${data.locations.length ? data.locations.map((l) => {
        const s = l.status;
        const health = !s.mounted ? `<span class="pill bad">Not mounted</span>` : !s.writable ? `<span class="pill bad">Read-only</span>` : `<span class="pill ok">OK</span>`;
        return `<div class="loc" data-loc="${l.id}">
          <div style="min-width:0">
            <b>${esc(l.name)}</b> ${l.is_default ? `<span class="pill accent">Default</span>` : ""} ${health}
            <div class="path">${esc(l.type.toUpperCase())} → ${esc(l.path)}${l.server_path ? ` · server sees: ${esc(l.server_path)}` : ""}</div>
            ${s.free_bytes != null ? `<div class="muted small">${bytes(s.free_bytes)} free of ${bytes(s.total_bytes)}</div>` : ""}
          </div>
          <div class="row" style="align-content:start">
            <button class="small" data-act="test">Test</button>
            <button class="small" data-act="edit">Edit</button>
            ${l.is_default ? "" : `<button class="small" data-act="default">Make default</button>`}
            <button class="small danger" data-act="remove">Remove</button>
          </div>
          <div data-editor style="grid-column:1/-1"></div>
        </div>`;
      }).join("") : `<p class="muted">No download locations yet.</p>`}
      ${data.network_shares ? "" : `<div class="warn-box">SMB/NFS shares can only be added when anidl runs in its LXC. Local folders work everywhere.</div>`}
      <div id="new-loc"></div>
      <button class="block" id="add-loc" style="margin-top:.75rem">+ Add location</button>`;

    const reload = async () => { data = await api("/api/locations"); render(); onChange?.(data.locations); };
    $("#add-loc", container).addEventListener("click", () => {
      $("#add-loc", container).hidden = true;
      const holder = $("#new-loc", container);
      holder.innerHTML = `<div class="card" style="margin-top:.75rem">${locationForm(null, data.network_shares)}</div>`;
      bindLocationForm($("form", holder), reload, () => { holder.innerHTML = ""; $("#add-loc", container).hidden = false; });
    });
    for (const row of $$("[data-loc]", container)) {
      const id = Number(row.dataset.loc);
      const loc = data.locations.find((l) => l.id === id);
      for (const b of $$("[data-act]", row)) {
        b.addEventListener("click", async () => {
          try {
            if (b.dataset.act === "test") {
              const s = await busy(b, () => api(`/api/locations/${id}/test`, { method: "POST" }));
              toast(`${loc.name}: mounted and writable · ${bytes(s.free_bytes)} free`);
              await reload();
            } else if (b.dataset.act === "default") {
              await api(`/api/locations/${id}/default`, { method: "POST" }); await reload();
            } else if (b.dataset.act === "remove") {
              if (!confirm(`Remove "${loc.name}"? Files already downloaded there are not deleted.`)) return;
              await api(`/api/locations/${id}`, { method: "DELETE" }); await reload();
            } else if (b.dataset.act === "edit") {
              const holder = $("[data-editor]", row);
              holder.innerHTML = `<div class="card" style="margin-top:.75rem">${locationForm(loc, data.network_shares)}</div>`;
              bindLocationForm($("form", holder), reload, () => { holder.innerHTML = ""; });
            }
          } catch (e) { toast(e.message, true); }
        });
      }
    }
  };
  render();
  onChange?.(data.locations);
}

// Settings ------------------------------------------------------------------------------------------

function defaultsForm(s) {
  return `
    <div class="grid2">
      <label class="field"><span>Audio</span><select name="mode"><option value="sub" ${s.mode === "sub" ? "selected" : ""}>Sub</option><option value="dub" ${s.mode === "dub" ? "selected" : ""}>Dub</option></select></label>
      <label class="field"><span>Quality</span>${qualitySelect(s.quality)}</label>
      <label class="field"><span>Downloads at once</span><select name="concurrency">${[1, 2, 3, 4, 5].map((n) => `<option ${n === s.concurrency ? "selected" : ""}>${n}</option>`).join("")}</select></label>
      <label class="field"><span>File naming</span><select name="naming">
        <option value="server" ${s.naming === "server" ? "selected" : ""}>Jellyfin / Plex (Show (2013)/Season 01/Show (2013) - S01E01)</option>
        <option value="original" ${s.naming === "original" ? "selected" : ""}>ani-cli original (Show/Title Episode 1)</option></select></label>
    </div>
    <label class="check"><input type="checkbox" name="skip_existing" ${s.skip_existing ? "checked" : ""}> Skip episodes that are already downloaded</label>`;
}

const SERVER = {
  jellyfin: { name: "Jellyfin", url: "http://192.168.1.20:8096", token: "API key", tokenHint: "In Jellyfin: Dashboard → API Keys → +" },
  plex: { name: "Plex", url: "http://192.168.1.20:32400", token: "Plex token", tokenHint: "Your X-Plex-Token: open any item in Plex Web → ⋯ → Get Info → View XML, and copy the X-Plex-Token value from the address bar." },
};
const serverName = (kind) => SERVER[kind]?.name || "media server";

function mediaServerForm(s) {
  const kind = s.media_server || "none";
  const info = SERVER[kind] || SERVER.jellyfin;
  return `
    <div class="field"><span class="muted small" style="font-weight:600">Media server</span><br>
      <div class="segmented" role="group" aria-label="Media server">
        ${["none", "jellyfin", "plex"].map((k) => `<button type="button" data-kind="${k}" class="${k === kind ? "on" : ""}">${k === "none" ? "None" : SERVER[k].name}</button>`).join("")}
      </div>
      <input type="hidden" name="kind" value="${kind}">
    </div>
    <div data-server ${kind === "none" ? "hidden" : ""}>
      <label class="field"><span>Server URL</span><input type="url" name="media_url" value="${esc(s.media_url)}" placeholder="${info.url}" autocapitalize="off"></label>
      <label class="field"><span data-token-label>${info.token}</span><input type="password" name="token" placeholder="${s.media_token_set ? "Saved. Leave blank to keep it." : ""}" autocomplete="off">
        <div class="hint" data-token-hint>${esc(info.tokenHint)}</div></label>
      <label class="field"><span>Anime library to scan</span>
        <select name="library_id">${s.media_library_id
          ? `<option value="${esc(s.media_library_id)}" selected>${esc(s.media_library_name || s.media_library_id)}</option>`
          : `<option value="">Test the connection to load your libraries</option>`}</select>
        <div class="hint">anidl only ever scans this one library, never everything on the server. With a "media server sees this as" path on the location, it scans just the new season folders, which is faster still.</div>
        <div class="hint lib-paths"></div></label>
      <label class="field"><span>When to scan</span><select name="media_refresh">
        <option value="season" ${s.media_refresh === "season" ? "selected" : ""}>When a season finishes</option>
        <option value="episode" ${s.media_refresh === "episode" ? "selected" : ""}>After every episode</option>
        <option value="off" ${s.media_refresh === "off" ? "selected" : ""}>Never</option></select></label>
      <div class="ms-result"></div>
    </div>`;
}

function bindMediaServerForm(form) {
  for (const b of $$("[data-kind]", form)) {
    b.addEventListener("click", () => {
      const kind = b.dataset.kind;
      if (form.kind.value !== kind) {
        // Switching servers: the old library list and token belong to the other server
        form.library_id.innerHTML = `<option value="">Test the connection to load your libraries</option>`;
        form.token.placeholder = "";
        $(".lib-paths", form).textContent = "";
        $(".ms-result", form).innerHTML = "";
      }
      form.kind.value = kind;
      for (const x of $$("[data-kind]", form)) x.classList.toggle("on", x === b);
      $("[data-server]", form).hidden = kind === "none";
      if (SERVER[kind]) {
        form.media_url.placeholder = SERVER[kind].url;
        $("[data-token-label]", form).textContent = SERVER[kind].token;
        $("[data-token-hint]", form).textContent = SERVER[kind].tokenHint;
      }
    });
  }
}

function serverBody(form) {
  const d = formData(form);
  return { kind: d.kind, url: d.media_url, token: d.token || null };
}

async function loadLibraries(form, saved) {
  const select = form.library_id;
  const { libraries } = await api("/api/mediaserver/libraries", { method: "POST", body: serverBody(form) });
  const current = select.value || saved || "";
  // Preselect the saved library, else one named like anime, else the first TV library
  const guess = libraries.find((l) => l.id === current) || libraries.find((l) => /anime/i.test(l.name))
    || libraries.find((l) => ["tvshows", "show"].includes(l.type)) || libraries[0];
  select.innerHTML = libraries.length
    ? libraries.map((l) => `<option value="${esc(l.id)}" ${l === guess ? "selected" : ""}>${esc(l.name)} (${esc(l.type)})</option>`).join("")
    : `<option value="">No libraries found</option>`;
  const showPaths = () => {
    const lib = libraries.find((l) => l.id === select.value);
    $(".lib-paths", form).textContent = lib?.paths.length ? `Folders in ${lib.name}: ${lib.paths.join(", ")}` : "";
    for (const input of $$("[data-serverpath]", form)) if (lib?.paths.length) input.placeholder = lib.paths[0];
  };
  select.onchange = showPaths;
  showPaths();
  return libraries;
}

async function saveMediaServer(form) {
  const d = formData(form);
  const option = form.library_id.selectedOptions[0];
  await api("/api/mediaserver", { method: "PUT", body: {
    ...serverBody(form),
    library_id: d.kind === "none" ? "" : d.library_id,
    library_name: d.library_id ? option.textContent.replace(/ \([^)]*\)$/, "") : "",
  } });
  await api("/api/settings", { method: "PUT", body: { media_refresh: d.media_refresh } });
}

async function testMediaServer(form) {
  const out = $(".ms-result", form);
  try {
    const r = await api("/api/mediaserver/test", { method: "POST", body: serverBody(form) });
    out.innerHTML = `<div class="ok-box">Connected to ${esc(r.name)} (${esc(r.kind)} ${esc(r.version)})</div>`;
    await loadLibraries(form);
    return true;
  } catch (e) {
    out.innerHTML = `<div class="error-box">${esc(e.message)}</div>`;
    return false;
  }
}

async function settingsView() {
  view.innerHTML = loading();
  const [s, sys] = await Promise.all([api("/api/settings"), api("/api/system")]);
  view.innerHTML = `
    <h1>Settings</h1>
    <div class="card"><h2>Download locations</h2><div id="locs"></div></div>

    <form class="card" id="defaults"><h2>Downloads</h2>${defaultsForm(s)}
      <div class="row end"><button class="primary" type="submit">Save</button></div></form>

    <form class="card" id="ms"><h2>Media server</h2>${mediaServerForm(s)}
      <div class="row end"><button type="button" id="ms-test">Test</button><button class="primary" type="submit">Save</button></div></form>

    <div class="card"><h2>System</h2>
      <div class="checks">
        <div><span>ani-cli</span><span>${esc(sys.ani_cli || "not found")}</span></div>
        <div><span>yt-dlp</span><span>${esc(sys.yt_dlp || "not found")}</span></div>
        <div><span>ffmpeg</span><span>${esc(sys.ffmpeg || "not found")}</span></div>
        <div><span>anidl</span><span>v${esc(status.version)}${sys.latest ? ` · newest release ${esc(sys.latest)}` : ""}</span></div>
      </div>
      ${sys.update_available ? `<div class="ok-box">anidl ${esc(sys.latest)} is available.
        <div class="row" style="margin-top:.5rem"><button class="primary small" id="update-app" ${sys.helper ? "" : "disabled"}>Update anidl</button>
        <a class="small" href="https://github.com/JustinMarxGit/anidl/releases" target="_blank" rel="noopener">What's new</a></div></div>` : ""}
      <p class="muted small">If searches or downloads start failing, the anime site probably changed. Updating ani-cli usually fixes it. Tools also update weekly on their own.</p>
      <button id="update-tools" ${sys.helper ? "" : "disabled"}>Update ani-cli and yt-dlp</button>
      <pre id="update-out" class="small muted" style="white-space:pre-wrap" hidden></pre>
    </div>

    <form class="card" id="pw"><h2>Account</h2>
      <p class="muted">Signed in as <b>${esc(status.username)}</b>.</p>
      <div class="grid2">
        <label class="field"><span>Current password</span><input type="password" name="current" autocomplete="current-password" required></label>
        <label class="field"><span>New password</span><input type="password" name="new" autocomplete="new-password" minlength="8" required></label>
      </div>
      <div class="row end"><button type="button" id="logout" class="ghost">Log out</button><button class="primary" type="submit">Change password</button></div>
    </form>

    <div class="card"><h2>Setup</h2>
      <p class="muted">Walk through the setup wizard again. Your locations, settings and queue are kept.</p>
      <a class="btn" href="#/setup?rerun=1">Run setup again</a>
    </div>`;

  locationsPanel($("#locs"));
  $("#defaults").addEventListener("submit", async (e) => {
    e.preventDefault();
    try { await busy(e.submitter, () => api("/api/settings", { method: "PUT", body: formData(e.target) })); toast("Saved"); }
    catch (err) { toast(err.message, true); }
  });
  bindMediaServerForm($("#ms"));
  $("#ms").addEventListener("submit", async (e) => {
    e.preventDefault();
    try { await busy(e.submitter, () => saveMediaServer(e.target)); toast("Media server settings saved"); e.target.token.value = ""; }
    catch (err) { toast(err.message, true); }
  });
  $("#ms-test").addEventListener("click", (e) => busy(e.currentTarget, () => testMediaServer($("#ms"))));
  if (s.media_server !== "none" && s.media_token_set) loadLibraries($("#ms"), s.media_library_id).catch(() => {});
  $("#update-tools").addEventListener("click", async (e) => {
    try {
      const r = await busy(e.currentTarget, () => api("/api/system/update-tools", { method: "POST" }));
      $("#update-out").hidden = false;
      $("#update-out").textContent = r.output;
      toast("Update finished");
    } catch (err) { toast(err.message, true); }
  });
  $("#update-app")?.addEventListener("click", async (e) => {
    if (!confirm(`Update anidl to ${sys.latest}? The page reconnects when it's done; running downloads restart.`)) return;
    try {
      await busy(e.currentTarget, () => api("/api/system/update-app", { method: "POST" }));
      toast("Updating… this page reloads when anidl is back.");
      // The service restarts during the update: wait for the new version to answer
      const started = Date.now();
      const poll = setInterval(async () => {
        try {
          const s2 = await api("/api/status");
          if (s2.version !== status.version) { clearInterval(poll); location.reload(); }
        } catch { /* restarting */ }
        if (Date.now() - started > 300000) { clearInterval(poll); toast("The update is taking long. Check `anidl-update` in the console.", true); }
      }, 3000);
    } catch (err) { toast(err.message, true); }
  });
  $("#pw").addEventListener("submit", async (e) => {
    e.preventDefault();
    try { await busy(e.submitter, () => api("/api/account/password", { method: "POST", body: formData(e.target) })); toast("Password changed"); e.target.reset(); }
    catch (err) { toast(err.message, true); }
  });
  $("#logout").addEventListener("click", async () => { await api("/api/logout", { method: "POST" }); location.hash = "#/login"; });
}

// Setup wizard --------------------------------------------------------------------------------------

async function setupView() {
  const rerun = params().rerun === "1";
  let step = rerun ? 0 : Math.min(status.step, status.steps.length - 1);
  if (!status.has_admin) step = Math.min(step, 1);
  let settings = null;
  let locs = [];

  const go = async (n) => {
    step = n;
    if (status.has_admin) api("/api/setup/step", { method: "POST", body: { step: n } }).catch(() => {});
    window.scrollTo(0, 0);
    await render();
  };

  const frame = (title, body, { back = true, next = "Next", nextId = "next", skip = false } = {}) => `
    <div class="wizard">
      <p class="muted small" style="margin:0">Setup · step ${step + 1} of ${status.steps.length}</p>
      <div class="steps">${status.steps.map((_, i) => `<i class="${i <= step ? "on" : ""}"></i>`).join("")}</div>
      <div class="card">
        <h1>${title}</h1>
        ${body}
        <div class="wizard-nav">
          ${back && step > 0 ? `<button type="button" id="back" class="ghost">← Back</button>` : "<span></span>"}
          <div class="row">${skip ? `<button type="button" id="skip" class="ghost">Skip</button>` : ""}<button type="button" class="primary" id="${nextId}">${next}</button></div>
        </div>
      </div>
    </div>`;

  const bindBack = () => $("#back")?.addEventListener("click", () => go(step - 1));

  async function render() {
    if (step >= 2 && !settings) settings = await api("/api/settings");

    if (step === 0) {
      view.innerHTML = frame("Welcome to anidl", `
        <p>anidl lets you browse anime and download episodes to your NAS from any device, including your phone. This wizard sets up an admin account, where downloads go, and the optional Jellyfin or Plex connection.</p>
        <h2>System check</h2>
        <div id="checks">${loading("Checking tools and trying a test search…")}</div>`, { back: false });
      bindBack();
      $("#next").addEventListener("click", () => go(1));
      const sys = await api("/api/setup/system").catch((e) => ({ error: e.message }));
      const line = (name, value, ok) => `<div><span>${ok ? "✅" : "⚠️"} ${name}</span><span>${esc(value)}</span></div>`;
      if (!$("#checks")) return;
      $("#checks").innerHTML = sys.error ? `<div class="error-box">${esc(sys.error)}</div>` : `
        <div class="checks">
          ${line("ani-cli", sys.ani_cli || "not installed", !!sys.ani_cli)}
          ${line("yt-dlp", sys.yt_dlp || "not installed (ffmpeg fallback)", !!sys.yt_dlp)}
          ${line("ffmpeg", sys.ffmpeg || "not installed", !!sys.ffmpeg)}
          ${line("Network shares", sys.helper ? "supported" : "local folders only (not running in the LXC)", sys.helper)}
          ${line("Test search", sys.search_ok ? "working" : sys.search_error || "skipped", !!sys.search_ok)}
        </div>
        ${sys.search_ok === false ? `<div class="warn-box">Searching doesn't work right now. You can finish setup anyway and update ani-cli from Settings later.<br><button class="small" id="recheck" style="margin-top:.5rem">Check again</button></div>` : ""}`;
      $("#recheck")?.addEventListener("click", () => render());
      return;
    }

    if (step === 1) {
      if (status.has_admin) {
        view.innerHTML = frame("Admin account", `<div class="ok-box">The admin account <b>${esc(status.username || "")}</b> is set up.</div>
          <p class="muted">Change the password under Settings → Account.</p>`);
        bindBack();
        $("#next").addEventListener("click", () => go(2));
        return;
      }
      view.innerHTML = frame("Create the admin account", `
        <p class="muted">Anyone who can reach this page can start downloads and mount shares, so it needs a login.</p>
        <form id="admin">
          <div id="err"></div>
          <label class="field"><span>Username</span><input type="text" name="username" autocomplete="username" autocapitalize="off" required></label>
          <label class="field"><span>Password</span><input type="password" name="password" autocomplete="new-password" minlength="8" required></label>
          <label class="field"><span>Repeat password</span><input type="password" name="password2" autocomplete="new-password" minlength="8" required></label>
        </form>`, { next: "Create account" });
      bindBack();
      $("#next").addEventListener("click", async (e) => {
        const form = $("#admin");
        if (!form.reportValidity()) return;
        const d = formData(form);
        if (d.password !== d.password2) { $("#err").innerHTML = `<div class="error-box">The passwords don't match.</div>`; return; }
        try {
          await busy(e.currentTarget, () => api("/api/setup/admin", { method: "POST", body: { username: d.username, password: d.password } }));
          status = await api("/api/status");
          await go(2);
        } catch (err) { $("#err").innerHTML = `<div class="error-box">${esc(err.message)}</div>`; }
      });
      return;
    }

    if (step === 2) {
      view.innerHTML = frame("Download locations", `
        <p class="muted">Where should downloaded shows go? Add your NAS share (SMB or NFS) or a local folder. Each location is mounted and test-written when you save it. You can add more later and pick one per download.</p>
        <div id="locs"></div>`);
      bindBack();
      const next = $("#next");
      next.disabled = true;
      locationsPanel($("#locs"), { onChange: (l) => { locs = l; next.disabled = l.length === 0; } });
      next.addEventListener("click", () => go(3));
      return;
    }

    if (step === 3) {
      if (!locs.length) locs = (await api("/api/locations")).locations;
      view.innerHTML = frame("Media server (optional)", `
        <p class="muted">If you use Jellyfin or Plex, anidl can tell it to scan new downloads right away, with no need to wait for the scheduled scan. It only ever scans your anime library.</p>
        <form id="ms">${mediaServerForm(settings)}
          <div data-server ${settings.media_server === "none" ? "hidden" : ""}>
          ${locs.length ? `<h3>How the server sees your locations</h3><p class="muted small">The media server usually mounts the same share at a different path. Fill these in for the fastest, folder-only scans.</p>
          ${locs.map((l) => `<label class="field"><span>${esc(l.name)} <span class="muted">(${esc(l.path)} here)</span></span>
            <input type="text" data-serverpath="${l.id}" value="${esc(l.server_path)}" placeholder="/media/anime" autocapitalize="off"></label>`).join("")}` : ""}
          <button type="button" id="ms-test">Test connection</button>
          </div>
        </form>`, { skip: true, next: "Save & continue" });
      bindBack();
      const form = $("#ms");
      bindMediaServerForm(form);
      // The location paths and test button follow the same None/Jellyfin/Plex switch
      for (const b of $$("[data-kind]", form)) b.addEventListener("click", () => { for (const el of $$("[data-server]", form)) el.hidden = b.dataset.kind === "none"; });
      $("#ms-test").addEventListener("click", (e) => busy(e.currentTarget, () => testMediaServer(form)));
      if (settings.media_server !== "none" && settings.media_token_set) loadLibraries(form, settings.media_library_id).catch(() => {});
      $("#skip").addEventListener("click", () => go(4));
      $("#next").addEventListener("click", async (e) => {
        try {
          await busy(e.currentTarget, async () => {
            await saveMediaServer(form);
            for (const input of $$("[data-serverpath]", form)) {
              const loc = locs.find((l) => l.id === Number(input.dataset.serverpath));
              if (loc.server_path === input.value.trim()) continue;
              await api(`/api/locations/${loc.id}/server-path`, { method: "PUT", body: { server_path: input.value } });
            }
          });
          settings = await api("/api/settings");
          await go(4);
        } catch (err) { toast(err.message, true); }
      });
      return;
    }

    if (step === 4) {
      view.innerHTML = frame("Download defaults", `<form id="defaults">${defaultsForm(settings)}</form>
        <p class="muted small">You can change these for each download, and here later under Settings.</p>`, { next: "Save & continue" });
      bindBack();
      $("#next").addEventListener("click", async (e) => {
        try {
          settings = await busy(e.currentTarget, () => api("/api/settings", { method: "PUT", body: formData($("#defaults")) }));
          await go(5);
        } catch (err) { toast(err.message, true); }
      });
      return;
    }

    if (step === 5) {
      locs = (await api("/api/locations")).locations;
      settings = await api("/api/settings");
      view.innerHTML = frame("All set", `
        <dl class="summary">
          <dt>Admin</dt><dd>${esc(status.username || "")}</dd>
          <dt>Locations</dt><dd>${locs.map((l) => `${esc(l.name)}${l.is_default ? " (default)" : ""}`).join("<br>") || "none"}</dd>
          <dt>Media server</dt><dd>${settings.media_server !== "none" ? `${serverName(settings.media_server)} at ${esc(settings.media_url)} · ${settings.media_refresh === "off" ? "scanning off" : `scans ${esc(settings.media_library_name || "nothing (no library chosen)")} when ${settings.media_refresh === "episode" ? "each episode" : "each season"} finishes`}` : "none"}</dd>
          <dt>Defaults</dt><dd>${esc(settings.mode)} · ${esc(settings.quality)} · ${settings.concurrency} at once · ${esc(settings.naming)} naming</dd>
        </dl>
        <p class="muted">Tip: on your phone, use "Add to Home Screen" to open anidl like an app.</p>`, { next: "Finish" });
      bindBack();
      $("#next").addEventListener("click", async (e) => {
        try {
          await busy(e.currentTarget, () => api("/api/setup/complete", { method: "POST" }));
          location.hash = "#/";
        } catch (err) { toast(err.message, true); }
      });
    }
  }

  await render();
}
