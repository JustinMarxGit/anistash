// AniStash frontend: hash-routed single page app, no build step.

const view = document.getElementById("view");
const nav = document.getElementById("nav");
let status = null;          // /api/status
let prefs = null;           // /api/settings, for defaults like sub/dub
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
  // The custom header marks requests as coming from this app; the server rejects
  // state-changing requests without it (cross-site forms and scripts can't add it)
  const headers = { "X-Requested-With": "anistash" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const resp = await fetch(path, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
    credentials: "same-origin",
  });
  let data = null;
  try { data = await resp.json(); } catch { /* empty body */ }
  if (resp.status === 401 && !path.startsWith("/api/login")) { location.hash = "#/login"; throw new ApiError("Please log in.", 401); }
  if (resp.status === 409) { location.hash = "#/setup"; throw new ApiError("Finish setup first.", 409); }
  if (resp.status === 403 && data?.detail === "password change required") { location.hash = "#/password"; throw new ApiError("Change your password first.", 403); }
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

// Library: which shows you already have (download folders + Jellyfin/Plex)
async function libraryMatch(items) {
  if (!items.length) return {};
  try { return (await api("/api/library/match", { method: "POST", body: { items } })).matches; }
  catch { return {}; }
}

function librarySummary(m) {
  const seasons = Object.entries(m.seasons || {}).map(([n, c]) => `${n === "0" ? "Specials" : `Season ${n}`}: ${c} episode${c === 1 ? "" : "s"}`);
  const where = [m.locations?.length ? `on ${m.locations.join(", ")}` : "", m.server ? `in ${m.server}` : ""].filter(Boolean).join(" · ");
  return { seasons: seasons.join(", "), where };
}

function libraryBadge(m) {
  if (!m) return "";
  const n = m.season !== undefined ? m.season_episodes : m.episodes;
  const label = m.season !== undefined && !m.season_episodes
    ? "Series in library"  // another season of it is, this one isn't
    : `In library${n ? ` · ${n} ep${n === 1 ? "" : "s"}` : ""}`;
  const { seasons: have, where } = librarySummary(m);
  return `<span class="inlib" data-tip="${esc(`Already in your library${where ? ` ${where}` : ""}${have ? `: ${have}` : ""}`)}">✓ ${label}</span>`;
}

function relTime(ts) {
  if (!ts) return "never";
  const diff = ts * 1000 - Date.now();
  const abs = Math.abs(diff), past = diff < 0;
  const units = [[86400000, "day"], [3600000, "hour"], [60000, "minute"]];
  for (const [ms, name] of units) {
    if (abs >= ms) {
      const n = Math.round(abs / ms);
      return past ? `${n} ${name}${n === 1 ? "" : "s"} ago` : `in ${n} ${name}${n === 1 ? "" : "s"}`;
    }
  }
  return past ? "just now" : "in a moment";
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

// Sub, dub or both for this page: the toggle in the URL wins, else the saved preference
function wantMode() {
  const p = params();
  if (["sub", "dub", "both"].includes(p.mode)) return p.mode;
  if ("dub" in p) return p.dub === "1" ? "dub" : "sub";  // links from older versions
  return prefs?.mode || "sub";
}
const modeParam = (mode) => `&mode=${mode}`;
const MODE_LABEL = { sub: "sub", dub: "dub", both: "sub + dub" };

function modeToggle(mode) {
  return `<div class="segmented" role="group" aria-label="Audio">
    <button type="button" data-mode="sub" class="${mode === "sub" ? "on" : ""}">Sub</button>
    <button type="button" data-mode="dub" class="${mode === "dub" ? "on" : ""}">Dub</button>
    <button type="button" data-mode="both" class="${mode === "both" ? "on" : ""}">Sub + Dub</button>
  </div>`;
}

// Tooltips ---------------------------------------------------------------------------
// Every button gets a plain-language explanation of what it does: shown on hover with a
// mouse, on keyboard focus, and on a long press on touch screens (where hover doesn't exist).

const TIPS = [
  ['[data-nav="browse"]', "Trending anime and what's airing this season"],
  ['[data-nav="search"]', "Find a show by its name"],
  ['[data-nav="downloads"]', "What's downloading, what's finished, and the shows you follow"],
  ['[data-nav="settings"]', "Your account, download folders, Jellyfin/Plex and other options"],
  ['[data-mode="sub"]', "Japanese audio with English subtitles"],
  ['[data-mode="dub"]', "English audio"],
  ['[data-mode="both"]', "Download both and combine them into one file, so you can switch between Japanese and English in Jellyfin or Plex"],
  ['#follow', "Download new episodes of this show by themselves, as soon as they come out"],
  ['#all-seasons', "Tick every episode of every season, except ones you already have"],
  ['[data-tab]', "Show this season's episodes. The count tells you how many you've ticked"],
  ['[data-sel="all"]', "Tick every episode in this season"],
  ['[data-sel="none"]', "Untick everything in this season"],
  ['[data-sel="missing"]', "Tick only the episodes you don't have yet"],
  ['#range button', "Tick the episodes you typed, for example 1-5, 8, 12-"],
  ['#download', "Add the ticked episodes to the download queue"],
  ['#sel-queue', "Download every episode of the shows you ticked"],
  ['#sel-clear', "Untick everything"],
  ['.pick', "Tick to download this together with other results, for example every season"],
  ['[data-tabto="queue"]', "What's downloading now and what has finished"],
  ['[data-tabto="following"]', "Shows that download their new episodes by themselves"],
  ['#clear', "Remove finished downloads from this list. The files stay"],
  ['[data-b="up"]', "Download this season sooner (move it up the queue)"],
  ['[data-b="down"]', "Download this season later (move it down the queue)"],
  ['[data-b="retry"]', "Try the episodes that failed again"],
  ['[data-b="cancel"]', "Stop downloading this season. Finished episodes stay"],
  ['[data-b="remove"]', "Remove this from the list. The files stay"],
  ['[data-b="rescan"]', "Tell Jellyfin/Plex to look for these episodes now"],
  ['[data-i="up"]', "Download this episode sooner"],
  ['[data-i="down"]', "Download this episode later"],
  ['[data-i="retry"]', "Try this episode again"],
  ['[data-i="cancel"]', "Don't download this episode"],
  ['[data-f="check"]', "Look for new episodes of this show right now"],
  ['[data-f="pause"]', "Stop checking this show for new episodes for now"],
  ['[data-f="resume"]', "Start checking this show for new episodes again"],
  ['[data-f="seasons"]', "Choose whether new seasons of this show are followed by themselves"],
  ['[data-f="unfollow"]', "Stop downloading new episodes of this show. Episodes you have stay"],
  ['[data-type="smb"]', "A shared folder on your NAS or a Windows PC (what most NAS boxes use)"],
  ['[data-type="nfs"]', "An NFS export, common on Linux file servers and TrueNAS"],
  ['[data-type="local"]', "A folder inside the AniStash container, such as a Proxmox bind mount"],
  ['[data-connect]', "Log in to your NAS and find the anime folder for you"],
  ['[data-change]', "Choose a different folder on the NAS"],
  ['[data-use]', "Save downloads in this folder"],
  ['[data-usenew]', "Create this new folder here and save downloads in it"],
  ['[data-browse]', "List the folders this NFS server shares"],
  ['[data-act="test"]', "Check this folder can be reached and written to (reconnects it if needed)"],
  ['[data-act="edit"]', "Change this download folder's settings"],
  ['[data-act="default"]', "Pick this folder automatically for new downloads"],
  ['.loc[data-loc] [data-act="remove"]', "Stop using this folder. Files already there stay"],
  ['#add-loc', "Add another place to save downloads"],
  ['[data-kind="none"]', "Don't tell a media server about new downloads"],
  ['[data-kind="jellyfin"]', "Tell your Jellyfin server about new downloads"],
  ['[data-kind="plex"]', "Tell your Plex server about new downloads"],
  ['#ms-test', "Check the address and key work, and load your libraries"],
  ['#update-tools', "Get the newest ani-cli and yt-dlp. This fixes most search and download problems"],
  ['#update-app', "Install the newest AniStash. Your settings, users and downloads stay"],
  ['#logout', "Sign out on this device"],
  ['#logout-all', "Sign out on every device, for example if you lost a phone"],
  ['[data-generate]', "Make a strong random password"],
  ['[data-user] [data-role]', "Admin: everything. Member: browse and download. Viewer: browse only"],
  ['[data-user] [data-act="password"]', "Give them a new temporary password. They pick their own at next login"],
  ['[data-user] [data-act="disable"]', "Stop this person from logging in (you can undo this)"],
  ['[data-user] [data-act="enable"]', "Let this person log in again"],
  ['[data-user] [data-act="delete"]', "Delete this account. Their downloads stay"],
  ['#recheck', "Run the system check again"],
  ['#skip', "Skip this step for now. You can set it up later in Settings"],
  ['#pw-logout', "Sign out without changing the password"],
];

const tipEl = document.createElement("div");
tipEl.className = "tip";
tipEl.setAttribute("role", "tooltip");
tipEl.hidden = true;
document.body.append(tipEl);

function applyTips(root = document) {
  for (const [selector, text] of TIPS) {
    for (const el of root.querySelectorAll(selector)) {
      if (!el.dataset.tip) {
        el.dataset.tip = text;
        if (!el.getAttribute("aria-label") && !el.textContent.trim()) el.setAttribute("aria-label", text);
      }
    }
  }
}

function showTip(target) {
  const text = target?.dataset.tip;
  if (!text) return;
  tipEl.textContent = text;
  tipEl.hidden = false;
  const r = target.getBoundingClientRect();
  const t = tipEl.getBoundingClientRect();
  const left = Math.max(8, Math.min(r.left + r.width / 2 - t.width / 2, window.innerWidth - t.width - 8));
  let top = r.top - t.height - 8;
  if (top < 8) top = r.bottom + 8;
  tipEl.style.left = `${left}px`;
  tipEl.style.top = `${top}px`;
}
const hideTip = () => { tipEl.hidden = true; };

let tipTimer = null;
let longPressed = false;
document.addEventListener("pointerover", (e) => {
  if (e.pointerType !== "mouse") return;
  const target = e.target.closest("[data-tip]");
  clearTimeout(tipTimer);
  if (!target) { hideTip(); return; }
  tipTimer = setTimeout(() => showTip(target), 300);
});
document.addEventListener("pointerout", (e) => {
  const target = e.target.closest("[data-tip]");
  if (e.pointerType === "mouse" && target && !target.contains(e.relatedTarget)) { clearTimeout(tipTimer); hideTip(); }
});
document.addEventListener("pointerdown", (e) => {
  hideTip();
  if (e.pointerType === "mouse") return;
  const target = e.target.closest("[data-tip]");
  if (!target) return;
  longPressed = false;
  tipTimer = setTimeout(() => { longPressed = true; showTip(target); setTimeout(hideTip, 3000); }, 500);
});
for (const type of ["pointerup", "pointercancel"]) document.addEventListener(type, () => clearTimeout(tipTimer));
// A long press shows the explanation; it shouldn't also press the button
document.addEventListener("click", (e) => {
  if (longPressed) { e.preventDefault(); e.stopPropagation(); longPressed = false; }
}, true);
document.addEventListener("contextmenu", (e) => { if (e.target.closest("[data-tip]") && !e.pointerType?.includes("mouse")) e.preventDefault(); });
document.addEventListener("focusin", (e) => { if (e.target.matches?.("[data-tip]:focus-visible")) showTip(e.target); });
document.addEventListener("focusout", hideTip);
window.addEventListener("scroll", hideTip, { passive: true });
new MutationObserver(() => applyTips()).observe(document.body, { childList: true, subtree: true });
applyTips();

// Router ----------------------------------------------------------------------

const routes = {
  "": browseView,
  "search": searchView,
  "show": showView,
  "downloads": downloadsView,
  "settings": settingsView,
  "setup": setupView,
  "login": loginView,
  "password": passwordView,
};

// What the signed-in user may do (the server enforces the same rules)
const can = {
  download: () => ["admin", "member"].includes(status?.user?.role),
  admin: () => status?.user?.role === "admin",
};

async function route() {
  clearInterval(pollTimer);
  document.querySelector(".actionbar")?.remove();
  document.querySelector(".sheet-backdrop")?.remove();
  const name = location.hash.replace(/^#\/?/, "").split("?")[0];
  try {
    status = await api("/api/status");
  } catch (e) {
    view.innerHTML = `<div class="error-box">Can't reach AniStash: ${esc(e.message)}</div>`;
    return;
  }
  // Fresh install or unfinished setup always lands in the wizard
  if (!status.has_admin && name !== "setup") return void (location.hash = "#/setup");
  if (status.has_admin && !status.logged_in && name !== "login") return void (location.hash = "#/login");
  if (status.logged_in && status.user?.must_change_password && name !== "password") return void (location.hash = "#/password");
  if (status.logged_in && !status.setup_complete && name !== "setup") return void (location.hash = "#/setup");

  const ready = status.logged_in && status.setup_complete && !status.user?.must_change_password;
  nav.hidden = !ready;
  document.body.style.paddingBottom = ready ? "" : "0";
  for (const a of $$("[data-nav]", nav)) {
    a.classList.toggle("active", a.dataset.nav === (name || "browse") || (name === "show" && a.dataset.nav === "search"));
  }
  if (ready && !badgeTimer) { refreshBadge(); badgeTimer = setInterval(refreshBadge, 10000); }
  if (ready && !prefs) prefs = await api("/api/settings").catch(() => null);
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
      <p class="muted">Sign in with your AniStash account.</p>
      <div id="err"></div>
      <label class="field"><span>Username</span><input type="text" name="username" autocomplete="username" autocapitalize="off" required></label>
      <label class="field"><span>Password</span><input type="password" name="password" autocomplete="current-password" required></label>
      <button class="primary block" type="submit">Log in</button>
      <p class="hint" style="margin-top:1rem">Locked out? Run <code>anistash-setup --reset-password</code> in the container's console.</p>
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

// Forced password change ---------------------------------------------------------------

function passwordView() {
  view.innerHTML = `
    <form class="login card" id="pwform">
      <h1>Choose a new password</h1>
      <p class="muted">${status?.user?.must_change_password ? "An admin set a temporary password for your account. Pick your own to continue." : "Change your password."}</p>
      <div id="err"></div>
      <label class="field"><span>Current (temporary) password</span><input type="password" name="current" autocomplete="current-password" required></label>
      <label class="field"><span>New password</span><input type="password" name="new" autocomplete="new-password" minlength="10" required>
        <div class="hint">At least 10 characters. A few random words is easy to remember and hard to guess.</div></label>
      <label class="field"><span>Repeat new password</span><input type="password" name="new2" autocomplete="new-password" minlength="10" required></label>
      <button class="primary block" type="submit">Save password</button>
      <button class="ghost block" type="button" id="pw-logout" style="margin-top:.5rem">Log out</button>
    </form>`;
  $("#pw-logout").addEventListener("click", async () => { await api("/api/logout", { method: "POST" }); location.hash = "#/login"; });
  $("#pwform").addEventListener("submit", async (e) => {
    e.preventDefault();
    const d = formData(e.target);
    if (d.new !== d.new2) { $("#err").innerHTML = `<div class="error-box">The new passwords don't match.</div>`; return; }
    try {
      await busy(e.submitter, () => api("/api/account/password", { method: "POST", body: { current: d.current, new: d.new } }));
      toast("Password changed");
      location.hash = "#/";
    } catch (err) { $("#err").innerHTML = `<div class="error-box">${esc(err.message)}</div>`; }
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
        <a class="poster" href="#/search?q=${encodeURIComponent(m.title)}" data-card="${m.id}">
          <div class="art" style="background-image:url('${esc(m.cover)}')"><span data-lib></span></div>
          <div class="name">${esc(m.title)}</div>
          <div class="sub">${esc([m.format?.replace("_", " "), m.year, m.episodes && `${m.episodes} eps`].filter(Boolean).join(" · "))}</div>
        </a>`).join("")}</div>`;
      libraryMatch(items.map((m) => ({ id: m.id, titles: [m.title, m.romaji] }))).then((matches) => {
        for (const [id, m] of Object.entries(matches)) {
          const slot = el.querySelector(`[data-card="${id}"] [data-lib]`);
          if (slot) slot.outerHTML = libraryBadge(m);
        }
      });
    }).catch((e) => { const el = $(`#${kind}`); if (el) el.innerHTML = `<p class="muted">${esc(e.message)}</p>`; });
  }
}

// Search -------------------------------------------------------------------------------

async function searchView() {
  const q = params().q || "";
  const mode = wantMode();
  const dub = mode === "dub";  // the episode lists for "both" come from the sub
  view.innerHTML = `
    <form class="searchbar" id="search">
      <input type="search" name="q" value="${esc(q)}" placeholder="Search anime…" enterkeyhint="search" aria-label="Search anime" ${q ? "" : "autofocus"}>
      <button class="primary" type="submit">Search</button>
    </form>
    <div class="row" style="margin-bottom:1rem">
      ${modeToggle(mode)}
      <span class="muted small grow">${can.download() ? "Tick several results to queue them together, e.g. every season." : ""}</span>
    </div>
    <div id="results">${q ? loading("Searching…") : ""}</div>`;
  $("#search").addEventListener("submit", (e) => {
    e.preventDefault();
    const value = e.target.q.value.trim();
    if (value) location.hash = `#/search?q=${encodeURIComponent(value)}${modeParam(mode)}`;
  });
  for (const b of $$("[data-mode]")) {
    b.addEventListener("click", () => { location.hash = `#/search?q=${encodeURIComponent(q)}${modeParam(b.dataset.mode)}`; });
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
      <a class="body" href="#/show?q=${encodeURIComponent(q)}&i=${r.index}&t=${encodeURIComponent(r.title)}${modeParam(mode)}">
        <span class="title">${esc(r.title)}</span>
        <span class="meta" data-meta>${r.title_known ? "" : "Only result"}</span>
        <span data-lib></span>
      </a>
      ${can.download() ? `<label class="pick" aria-label="Select ${esc(r.title)}"><input type="checkbox" value="${r.index}"></label>` : ""}
    </div>`).join("")}</div>`;

  libraryMatch(results.map((r) => ({ id: r.index, titles: [r.title] }))).then((matches) => {
    for (const [index, m] of Object.entries(matches)) {
      const slot = $(`.result[data-index="${index}"] [data-lib]`);
      if (slot) slot.outerHTML = libraryBadge(m);
    }
  });

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
    queueSheet(picked, mode);
  });
}

async function queueSheet(picked, mode) {
  const dub = mode === "dub";
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
        series_name: f.series_name, location_id: Number(f.location_id), mode, quality: f.quality, entries,
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
  const mode = wantMode();
  const dub = mode === "dub";
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
    <a href="#/search?q=${encodeURIComponent(p.q)}${modeParam(mode)}" class="small">← Results</a>
    <div class="hero" style="margin-top:.75rem">
      <div class="art" style="background-image:url('${esc(meta?.cover || "")}')"></div>
      <div>
        <h1>${esc(data.series_name)}</h1>
        <div class="muted small">${esc([meta?.format?.replace("_", " "), meta?.year, meta?.status?.toLowerCase().replace("_", " "), meta?.score && `${meta.score}%`].filter(Boolean).join(" · "))}</div>
        <div class="genres">${(meta?.genres || []).slice(0, 5).map((g) => `<span class="pill">${esc(g)}</span>`).join("")}</div>
        <div id="inlib"></div>
        ${meta?.description ? `<p class="synopsis clamp" id="syn">${esc(meta.description.replace(/<[^>]+>/g, ""))}</p>` : ""}
      </div>
    </div>

    <div class="card">
      <div class="grid2">
        <label class="field"><span>Series folder name</span><input type="text" id="series" value="${esc(data.series_name)}"></label>
        <label class="field"><span>Download to</span>${locationSelect(locs.locations, locationId)}</label>
      </div>
      <div class="row">
        ${modeToggle(mode)}
        <div class="grow" style="min-width:140px">${qualitySelect(settings.quality)}</div>
      </div>
    </div>

    <div class="section-head"><h2>Seasons</h2>
      <div class="row">${can.download() ? `<button class="small" id="follow">☆ Follow</button>` : ""}<button class="small" id="all-seasons">Select all seasons</button></div></div>
    <div class="tabs" id="tabs" role="tablist"></div>
    <div id="season"></div>
    <div class="pad-actionbar"></div>`;

  $("#syn")?.addEventListener("click", (e) => e.target.classList.toggle("clamp"));
  for (const b of $$("[data-mode]")) {
    b.addEventListener("click", () => { location.hash = `#/show?q=${encodeURIComponent(p.q)}&i=${p.i}&t=${encodeURIComponent(p.t)}${modeParam(b.dataset.mode)}`; });
  }
  libraryMatch([{ id: "series", titles: [data.series_name, p.t, ...(meta?.names || []), ...seasons.map((x) => x.title)] }]).then((matches) => {
    const m = matches.series;
    if (!m || !$("#inlib")) return;
    const { seasons: have, where } = librarySummary(m);
    $("#inlib").innerHTML = `<div class="libbox">✓ <b>In your library</b>${where ? ` <span class="muted">${esc(where)}</span>` : ""}
      ${have ? `<div class="small">${esc(have)}</div>` : ""}</div>`;
  });

  const bar = document.createElement("div");
  bar.className = "actionbar";
  bar.innerHTML = can.download()
    ? `<div class="summary"><b id="pick-count">Nothing selected</b><span class="muted" id="pick-detail">Pick episodes below</span></div>
    <button class="primary" id="download" disabled>Download</button>`
    : `<div class="summary"><b>View only</b><span class="muted">Your account can browse but not download. Ask an admin for access.</span></div>
    <span id="pick-count" hidden></span><span id="pick-detail" hidden></span><button id="download" hidden disabled></button>`;
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
        series_name: $("#series").value, location_id: locationId, mode,
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

  // Follow: keep this show up to date automatically
  const followBtn = $("#follow");
  const markFollowed = async () => {
    if (!followBtn) return;
    const { follows } = await api("/api/follows").catch(() => ({ follows: [] }));
    const mine = follows.filter((f) => f.mode === mode && seasons.some((x) => x.title === f.title));
    if (mine.length) {
      followBtn.textContent = "★ Following";
      followBtn.classList.add("following");
      followBtn.dataset.tip = "You follow this show, so new episodes download by themselves. Tap to manage it under Downloads → Following";
      followBtn.onclick = () => { location.hash = "#/downloads?tab=following"; };
    }
  };
  followBtn?.addEventListener("click", () => {
    if (followBtn.classList.contains("following")) return;
    const regular = seasons.filter((x) => !x.special);
    const latest = regular[regular.length - 1] || seasons[seasons.length - 1];
    const sh = sheet(`
      <h2>Keep ${esc($("#series").value)} up to date</h2>
      <p class="muted">AniStash checks for new episodes around each broadcast and downloads them as soon as they're on the site.</p>
      <form id="followform">
        <label class="field"><span>Season to follow</span><select name="season">${seasons.map((x, k) =>
          `<option value="${k}" ${x === latest ? "selected" : ""}>${x.special ? "Special" : `Season ${x.season}`}${x.part > 1 ? ` · Part ${x.part}` : ""} — ${esc(x.title)}</option>`).join("")}</select></label>
        <label class="check"><input type="checkbox" name="new_seasons" checked> Also follow new seasons when they come out</label>
        <div class="field" style="margin-top:.75rem"><span class="muted small" style="font-weight:600">Episodes that are already out</span>
          <label class="check"><input type="radio" name="backfill" value="all" checked> Download what's missing from every season</label>
          <label class="check"><input type="radio" name="backfill" value="season"> Download what's missing from the followed season</label>
          <label class="check"><input type="radio" name="backfill" value="none"> Skip them, only new episodes from now on</label>
        </div>
        <p class="hint">Saves to ${esc($("select[name=location_id]")?.selectedOptions[0]?.textContent || "the default location")} as ${MODE_LABEL[mode]}, ${esc($("select[name=quality]").selectedOptions[0].textContent.toLowerCase())}.</p>
        <div class="row end"><button type="button" class="ghost" data-close>Cancel</button><button class="primary" type="submit">Follow</button></div>
      </form>`);
    sh.querySelector("[data-close]").onclick = () => sh.remove();
    sh.querySelector("#followform").addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const f = formData(ev.target);
      const backfill = ev.target.querySelector("input[name=backfill]:checked").value;
      const chosen = seasons[Number(f.season)];
      const common = { series_name: $("#series").value, location_id: locationId, mode, quality: $("select[name=quality]").value };
      try {
        await busy(ev.submitter, async () => {
          await api("/api/follows", { method: "POST", body: { ...common, new_seasons: f.new_seasons, only_new: backfill === "none",
            entry: { query: chosen.query, index: chosen.index, title: chosen.title, season: chosen.season, offset: chosen.offset } } });
          if (backfill === "all") {
            // The followed season catches up by itself; queue the other seasons now
            const others = seasons.filter((x) => x !== chosen && !x.special && x.episodes.length).map((x) => ({
              query: x.query, index: x.index, title: x.title, season: x.season, offset: x.offset, episodes: x.episodes }));
            if (others.length) queuedToast(await api("/api/queue", { method: "POST", body: { ...common, entries: others } }));
          }
        });
        sh.remove();
        toast(`Following ${chosen.title}. New episodes download automatically.`);
        markFollowed();
      } catch (err) { toast(err.message, true); }
    });
  });
  markFollowed();
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
  const tab = params().tab === "following" ? "following" : "queue";
  view.innerHTML = `
    <div class="section-head"><h1>Downloads</h1>${tab === "queue" && can.download() ? `<button class="small" id="clear">${can.admin() ? "Clear finished" : "Clear my finished"}</button>` : ""}</div>
    <div class="segmented" role="tablist" style="margin-bottom:1rem">
      <button type="button" class="${tab === "queue" ? "on" : ""}" data-tabto="queue">Queue</button>
      <button type="button" class="${tab === "following" ? "on" : ""}" data-tabto="following">Following</button>
    </div>
    <div id="queue">${loading()}</div>`;
  for (const b of $$("[data-tabto]")) b.onclick = () => { location.hash = `#/downloads${b.dataset.tabto === "following" ? "?tab=following" : ""}`; };
  if (tab === "following") {
    await renderFollowing();
    pollTimer = setInterval(renderFollowing, 15000);
    return;
  }
  $("#clear")?.addEventListener("click", async () => {
    const r = await api("/api/queue/clear-finished", { method: "POST" });
    toast(`Removed ${r.removed} finished ${r.removed === 1 ? "batch" : "batches"}`);
    renderQueue();
  });
  await renderQueue();
  pollTimer = setInterval(renderQueue, 2000);
}

const AIRING = {
  RELEASING: ["Airing", "accent"], NOT_YET_RELEASED: ["Not aired yet", ""], FINISHED: ["Finished", "ok"],
  HIATUS: ["On hiatus", "warn"], CANCELLED: ["Cancelled", "bad"],
};

async function renderFollowing() {
  let data;
  try { data = await api("/api/follows"); }
  catch (e) { if ($("#queue")) $("#queue").innerHTML = `<div class="error-box">${esc(e.message)}</div>`; return; }
  const el = $("#queue");
  if (!el) return;
  if (!data.follows.length) {
    el.innerHTML = `<div class="card muted">You're not following anything yet. Open a show and tap <b>☆ Follow</b> to keep it up to date automatically.</div>`;
    return;
  }
  el.innerHTML = data.follows.map((f) => {
    const mine = data.admin || f.created_by === data.me;
    const [label, cls] = f.status === "paused" ? ["Paused", "warn"] : AIRING[f.airing_status] || ["Checking…", ""];
    const next = f.status === "paused" ? "Paused, not checking"
      : f.airing_status === "RELEASING" && f.next_episode && f.next_airing_at
        ? `Episode ${f.next_episode} airs ${relTime(f.next_airing_at)}`
        : f.airing_status === "FINISHED" ? "All episodes have aired; still watching for late uploads and new seasons" : "";
    return `<div class="batch" data-follow="${f.id}">
      <div class="batch-head" style="cursor:default">
        <div class="grow">
          <b>${esc(f.series_name)} · S${String(f.season).padStart(2, "0")}</b>
          <div class="muted small">${esc(f.title)}</div>
          ${next ? `<div class="small" style="margin-top:.25rem">${esc(next)}</div>` : ""}
          <div class="muted small">${f.last_checked ? `Checked ${relTime(f.last_checked)}: ${esc(f.last_result)}` : "First check is starting…"}${f.status === "active" && f.next_check ? ` · next check ${relTime(f.next_check)}` : ""}</div>
          <div class="muted small">${esc(MODE_LABEL[f.mode] || f.mode)} · ${esc(f.quality)} · ${esc(f.location)}${f.new_seasons ? " · new seasons too" : ""}${f.owner ? ` · by ${esc(f.owner)}` : ""}</div>
        </div>
        <span class="pill ${cls}">${label}</span>
      </div>
      ${mine ? `<div class="batch-tools">
        <button class="small" data-f="check">Check now</button>
        <button class="small" data-f="${f.status === "paused" ? "resume" : "pause"}">${f.status === "paused" ? "Resume" : "Pause"}</button>
        <button class="small" data-f="seasons">${f.new_seasons ? "Stop new seasons" : "Also new seasons"}</button>
        <button class="small danger" data-f="unfollow">Unfollow</button>
      </div>` : ""}
    </div>`;
  }).join("");
  for (const b of $$("[data-f]")) {
    b.onclick = async () => {
      const id = Number(b.closest("[data-follow]").dataset.follow);
      const f = data.follows.find((x) => x.id === id);
      try {
        if (b.dataset.f === "check") { await api(`/api/follows/${id}/check`, { method: "POST" }); toast("Checking now…"); setTimeout(renderFollowing, 4000); }
        else if (b.dataset.f === "pause" || b.dataset.f === "resume") await api(`/api/follows/${id}`, { method: "PUT", body: { status: b.dataset.f === "pause" ? "paused" : "active" } });
        else if (b.dataset.f === "seasons") await api(`/api/follows/${id}`, { method: "PUT", body: { new_seasons: !f.new_seasons } });
        else if (b.dataset.f === "unfollow") {
          if (!confirm(`Stop following ${f.title}? Downloaded episodes stay.`)) return;
          await api(`/api/follows/${id}`, { method: "DELETE" });
        }
        renderFollowing();
      } catch (err) { toast(err.message, true); }
    };
  }
}

const STATUS_PILL = { queued: "", running: "accent", done: "ok", failed: "bad", cancelled: "warn", skipped: "" };

async function renderQueue() {
  let batches, me, isAdmin;
  try { ({ batches, me, admin: isAdmin } = await api("/api/queue")); }
  catch (e) { if ($("#queue")) $("#queue").innerHTML = `<div class="error-box">${esc(e.message)}</div>`; return; }
  const el = $("#queue");
  if (!el) return;
  if (!batches.length) {
    el.innerHTML = `<div class="card muted">Nothing queued. Find something under <a href="#/">Browse</a> or <a href="#/search">Search</a>.</div>`;
    return;
  }
  el.innerHTML = batches.map((b) => {
    const total = b.items.length;
    const mine = isAdmin || (can.download() && b.created_by === me);
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
          <div class="muted small">${b.counts.done}/${total} episodes · ${esc(MODE_LABEL[b.mode] || b.mode)} · ${esc(b.location || "?")}${b.owner ? ` · by ${esc(b.owner)}` : ""}${b.follow_id ? ` · <span class="pill accent">auto</span>` : ""}</div>
          <div class="progress ${b.active ? "" : "done"}" style="margin-top:.4rem"><i style="width:${progress.toFixed(1)}%"></i></div>
        </div>
        ${state}
      </div>
      ${b.warning ? `<div class="warn-box" style="margin:0 14px 12px">${esc(b.warning)}
        ${mine && /scan failed/.test(b.warning) ? `<div style="margin-top:.5rem"><button class="small" data-b="rescan">Scan now</button></div>` : ""}</div>` : ""}
      ${open ? `
        ${mine ? `<div class="batch-tools">
          <button class="small" data-b="up" aria-label="Move up">↑</button>
          <button class="small" data-b="down" aria-label="Move down">↓</button>
          ${b.counts.failed + b.counts.cancelled ? `<button class="small" data-b="retry">Retry failed</button>` : ""}
          ${b.active ? `<button class="small danger" data-b="cancel">Cancel</button>` : `<button class="small" data-b="remove">Remove</button>`}
        </div>` : ""}
        <div class="items">${b.items.map((i) => `
          <div class="item" data-item="${i.id}">
            <span class="epno">E${esc(i.episode)}${b.offset && /^\d+$/.test(i.episode) ? `<div class="muted small">→ E${Number(i.episode) + b.offset}</div>` : ""}</span>
            <div style="min-width:0">
              <span class="pill ${STATUS_PILL[i.status]}">${esc(i.status)}</span>${i.quality ? ` <span class="pill">${esc(i.quality)}</span>` : ""}
              ${i.status === "running" ? `<div class="progress ${i.progress ? "" : "indeterminate"}" style="margin-top:.35rem"><i style="width:${i.progress}%"></i></div>` : ""}
              ${i.detail ? `<div class="detail">${esc(i.detail)}</div>` : ""}
              ${i.error ? `<div class="err">${esc(i.error)}</div>` : ""}
            </div>
            <div class="row" ${mine ? "" : "hidden"}>
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
        else if (b.dataset.b === "rescan") {
          await busy(b, () => api(`/api/batches/${id}/rescan`, { method: "POST" }));
          toast("Asked your media server to scan the new episodes");
        } else await api(`/api/batches/${id}/${b.dataset.b}`, { method: "POST" });
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

function locationAddress(type, c) {
  if (type === "smb" && c.server) return `//${c.server}/${c.share || ""}${c.subpath ? `/${c.subpath}` : ""}`;
  if (type === "nfs" && c.server) return `${c.server}:${c.export || "/"}`;
  return "";
}

// Mirrors locations.parse_address on the server, for the live preview
function parseAddress(text) {
  const t = text.trim();
  let m = t.match(/^(?:smb|cifs):\/\/([^/\\]+)[/\\]?(.*)$/i) || t.match(/^(?:\\\\|\/\/)([^/\\]+)[/\\]?(.*)$/);
  const decode = (part) => { try { return decodeURIComponent(part).trim(); } catch { return part.trim(); } };
  if (m) {
    const parts = m[2].split(/[/\\]+/).map(decode).filter(Boolean);
    return { type: "smb", server: m[1], share: parts[0] || "", subpath: parts.slice(1).join("/") };
  }
  m = t.match(/^nfs:\/\/([^/]+)(\/.*)?$/i) || t.match(/^([A-Za-z0-9.-]+):(\/.*)$/);
  if (m) return { type: "nfs", server: m[1], export: (m[2] || "/").replace(/\/+$/, "") || "/" };
  return null;
}

function locationForm(loc = null, networkShares = true, { wizard = false } = {}) {
  const type = loc?.type || (networkShares ? "smb" : "local");
  const c = loc?.config || {};
  const serverPath = `<label class="field"><span>Jellyfin/Plex sees this folder as</span><input type="text" name="server_path" value="${esc(loc?.server_path || "")}" placeholder="/media/anime" autocapitalize="off">
        <div class="hint">Only needed for faster Jellyfin/Plex scans: the same folder, as your media server's own path.</div></label>`;
  return `
    <form class="locform" data-id="${loc?.id ?? ""}">
      ${loc ? `<input type="hidden" name="type" value="${type}">` : `
      <div class="field">
        <div class="segmented" role="group" aria-label="Where are downloads saved">
          <button type="button" data-type="smb" class="${type === "smb" ? "on" : ""}" ${networkShares ? "" : "disabled"}>NAS (SMB)</button>
          <button type="button" data-type="nfs" class="${type === "nfs" ? "on" : ""}" ${networkShares ? "" : "disabled"}>NFS</button>
          <button type="button" data-type="local" class="${type === "local" ? "on" : ""}">Local</button>
        </div>
        <input type="hidden" name="type" value="${type}">
      </div>`}
      <div data-fields="smb" ${type === "smb" ? "" : "hidden"}>
        <label class="field"><span>NAS address</span><input type="text" name="server" value="${esc(type === "smb" ? c.server || "" : "")}" placeholder="10.10.1.3" autocapitalize="off" spellcheck="false" inputmode="url"></label>
        <div class="grid2">
          <label class="field"><span>Username</span><input type="text" name="username" value="${esc(c.username || "")}" autocapitalize="off" autocomplete="off" placeholder="blank for guest"></label>
          <label class="field"><span>Password</span><input type="password" name="password" autocomplete="new-password" placeholder="${loc ? "Leave blank to keep" : ""}"></label>
        </div>
        <button type="button" class="block" data-connect>${loc ? "Connect & browse" : "Connect & find anime folder"}</button>
        <div class="hint" data-preview></div>
        <div class="browser" data-browser></div>
        <details class="more"><summary>Type the share address yourself, or more options</summary>
          <label class="field" style="margin-top:.75rem"><span>Share address</span>
            <input type="text" name="address" value="${esc(locationAddress("smb", type === "smb" ? c : {}))}" placeholder="//10.10.1.3/media/anime" autocapitalize="off" spellcheck="false">
            <div class="hint">Server, share, then any folder inside it. Missing folders are created.</div></label>
          <div class="grid2">
            <label class="field"><span>Domain</span><input type="text" name="domain" value="${esc(c.domain || "")}" autocapitalize="off"></label>
            <label class="field"><span>SMB version</span><select name="vers">${["default", "3.1.1", "3.0", "2.1", "2.0", "1.0"].map((v) => `<option ${v === (c.vers || "default") ? "selected" : ""}>${v}</option>`).join("")}</select></label>
          </div>
        </details>
      </div>
      <div data-fields="nfs" ${type === "nfs" ? "" : "hidden"}>
        <label class="field"><span>Export address</span>
          <input type="text" name="nfs_address" value="${esc(locationAddress("nfs", type === "nfs" ? c : {}))}" placeholder="10.10.1.3:/mnt/pool/media/anime" autocapitalize="off" spellcheck="false">
          <div class="hint">Server, a colon, then the exported folder (or a folder inside it).</div>
          <div class="hint" data-preview></div></label>
        <button type="button" class="small" data-browse>Find exports on this server</button>
        <div class="shares" data-shares></div>
        <details class="more"><summary>More options</summary>
          <label class="field" style="margin-top:.75rem"><span>Mount options</span><input type="text" name="options" value="${esc(c.options || "")}" placeholder="vers=4.1" autocapitalize="off"></label>
        </details>
      </div>
      <div data-fields="local" ${type === "local" ? "" : "hidden"}>
        <label class="field"><span>Folder</span><input type="text" name="path" value="${esc(c.path || "")}" placeholder="/mnt/media/anime" autocapitalize="off">
          <div class="hint">For a Proxmox bind mount or the container's own disk. Allowed under /mnt, /media, /srv, /data, /downloads, /storage or /home.</div></label>
      </div>
      <label class="field" style="margin-top:1rem"><span>Name <span class="muted">(optional)</span></span><input type="text" name="name" value="${esc(loc?.name || "")}" placeholder="Filled in from the folder"></label>
      ${wizard ? "" : loc ? serverPath : `<details class="more"><summary>Jellyfin/Plex path (optional)</summary>${serverPath}</details>`}
      <div class="err"></div>
      <div class="row end">
        <button type="button" class="ghost" data-cancel>Cancel</button>
        <button type="submit" class="primary">${loc ? "Save & test" : "Add & test"}</button>
      </div>
    </form>`;
}

function bindLocationForm(form, onSaved, onCancel) {
  const setType = (type) => {
    form.type.value = type;
    for (const x of $$("[data-type]", form)) x.classList.toggle("on", x.dataset.type === type);
    for (const f of $$("[data-fields]", form)) f.hidden = f.dataset.fields !== type;
  };
  for (const b of $$("[data-type]", form)) b.addEventListener("click", () => setType(b.dataset.type));

  const autoName = (p) => p?.type === "smb" ? (p.subpath ? `${p.subpath.split("/").pop()} on ${p.share}` : p.share)
    : p?.type === "nfs" ? p.export.split("/").filter(Boolean).pop() || p.server : "";

  const preview = (input) => {
    const out = $("[data-preview]", input.closest("[data-fields]"));
    const p = parseAddress(input.value);
    if (!input.value.trim()) out.textContent = "";
    else if (!p) out.innerHTML = `<span style="color:var(--warn)">Start with //server/share (SMB) or server:/path (NFS).</span>`;
    else if (p.type === "smb") out.innerHTML = p.share
      ? `<div class="chosen">✓ Saving to <b>${esc(p.share)}</b>${p.subpath ? ` › <b>${esc(p.subpath.split("/").join(" › "))}</b>` : ""} <span class="muted">on ${esc(p.server)}</span></div>`
      : `Server <b>${esc(p.server)}</b>. Add the share name.`;
    else out.innerHTML = `✓ NFS server <b>${esc(p.server)}</b> · path <b>${esc(p.export)}</b>`;
    if (p?.share || p?.export) form.name.placeholder = autoName(p) || "Filled in from the folder";
    // An NFS address typed into the SMB box (or the reverse) moves to the right type
    if (p && !form.dataset.id && p.type !== form.type.value) {
      const target = p.type === "smb" ? form.address : form.nfs_address;
      target.value = input.value;
      input.value = "";
      setType(p.type);
      preview(target);
    }
  };
  for (const input of [form.address, form.nfs_address]) {
    input.addEventListener("input", () => preview(input));
    preview(input);
  }

  // SMB: connect, auto-pick the anime folder, or browse shares and folders
  const browser = $("[data-browser]", form);
  const smb = { server: "", shares: [], suggestions: [] };
  const login = () => { const d = formData(form); return { username: d.username, password: d.password, domain: d.domain }; };
  const choose = (share, path) => {
    form.address.value = `//${smb.server}/${share}${path.length ? `/${path.join("/")}` : ""}`;
    form.server.value = smb.server;
    preview(form.address);
    renderPicked(share, path);
  };
  const renderPicked = (share, path) => {
    const others = smb.suggestions.filter((s) => s.share !== share || s.path.join("/") !== path.join("/"));
    browser.innerHTML = `
      ${others.length ? `<div class="hint" style="margin-top:.5rem">Other matches:</div>${others.map((s, k) =>
        `<button type="button" class="pickrow" data-sugg="${k}">📁 ${esc([s.share, ...s.path].join(" › "))}</button>`).join("")}` : ""}
      <button type="button" class="small ghost" data-change>Pick a different folder</button>`;
    for (const b of $$("[data-sugg]", browser)) b.onclick = () => { const s = others[Number(b.dataset.sugg)]; choose(s.share, s.path); };
    $("[data-change]", browser).onclick = () => openFolder(share, path.slice(0, -1));
  };
  const renderShares = () => {
    browser.innerHTML = `
      ${smb.suggestions.length ? `<div class="hint" style="margin-top:.5rem">Anime folders found:</div>${smb.suggestions.map((s, k) =>
        `<button type="button" class="pickrow" data-sugg="${k}">📁 ${esc([s.share, ...s.path].join(" › "))}</button>`).join("")}` : ""}
      <div class="hint" style="margin-top:.5rem">${smb.suggestions.length ? "Or browse a share:" : "No folder called anime was found. Pick a share to browse:"}</div>
      ${smb.shares.map((sh, k) => `<button type="button" class="pickrow" data-share="${k}">🗄️ ${esc(sh.name)}${sh.comment ? ` <span class="muted">· ${esc(sh.comment)}</span>` : ""}</button>`).join("")}`;
    for (const b of $$("[data-sugg]", browser)) b.onclick = () => { const s = smb.suggestions[Number(b.dataset.sugg)]; choose(s.share, s.path); };
    for (const b of $$("[data-share]", browser)) b.onclick = () => openFolder(smb.shares[Number(b.dataset.share)].name, []);
  };
  const openFolder = async (share, path) => {
    browser.innerHTML = loading("Opening…");
    let folders;
    try {
      ({ folders } = await api("/api/locations/folders", { method: "POST", body: { server: smb.server, share, path, ...login() } }));
    } catch (err) {
      browser.innerHTML = `<div class="error-box" style="margin-top:.5rem">${esc(err.message)}</div><button type="button" class="small" data-back>Back to shares</button>`;
      $("[data-back]", browser).onclick = renderShares;
      return;
    }
    const crumbs = [["All shares", -2], [share, -1], ...path.map((p, k) => [p, k])];
    browser.innerHTML = `
      <div class="crumbs">${crumbs.map(([label, k]) => `<button type="button" class="ghost small" data-crumb="${k}">${esc(label)}</button>`).join("<span>›</span>")}</div>
      ${folders.map((f) => `<button type="button" class="pickrow" data-open="${esc(f)}">📁 ${esc(f)}</button>`).join("") || `<div class="hint">No folders here.</div>`}
      <div class="row" style="margin-top:.5rem">
        <button type="button" class="primary small" data-use>Use ${esc(path.length ? path[path.length - 1] : share)}</button>
        <input type="text" class="grow" data-newname placeholder="or a new folder, e.g. anime" style="min-height:36px">
        <button type="button" class="small" data-usenew>Use new</button>
      </div>`;
    for (const b of $$("[data-crumb]", browser)) {
      b.onclick = () => { const k = Number(b.dataset.crumb); k === -2 ? renderShares() : openFolder(share, k === -1 ? [] : path.slice(0, k + 1)); };
    }
    for (const b of $$("[data-open]", browser)) b.onclick = () => openFolder(share, [...path, b.dataset.open]);
    $("[data-use]", browser).onclick = () => choose(share, path);
    $("[data-usenew]", browser).onclick = () => {
      const name = $("[data-newname]", browser).value.trim();
      if (name) choose(share, [...path, ...name.split(/[/\\]+/).filter(Boolean)]);
    };
  };
  $("[data-connect]", form)?.addEventListener("click", async (e) => {
    const server = form.server.value.trim().replace(/^(smb:|cifs:)?[/\\]+/i, "").split(/[/\\]/)[0];
    if (!server) { form.server.focus(); return; }
    try {
      const r = await busy(e.currentTarget, () => api("/api/locations/browse", { method: "POST", body: { type: "smb", server, suggest: !form.dataset.id, ...login() } }));
      Object.assign(smb, { server: r.server, shares: r.shares, suggestions: r.suggestions || [] });
      // The best anime folder is picked straight away; the others stay one tap away
      if (smb.suggestions.length) choose(smb.suggestions[0].share, smb.suggestions[0].path);
      else renderShares();
    } catch (err) {
      browser.innerHTML = `<div class="error-box" style="margin-top:.5rem">${esc(err.message)}</div>`;
    }
  });

  for (const b of $$("[data-browse]", form)) {
    b.addEventListener("click", async () => {
      const list = $("[data-shares]", b.closest("[data-fields]"));
      try {
        const r = await busy(b, () => api("/api/locations/browse", { method: "POST", body: { type: "nfs", address: form.nfs_address.value } }));
        list.innerHTML = `<div class="hint">Tap one to use it:</div>` + r.shares.map((sh) =>
          `<button type="button" class="small share" data-pick="${esc(sh.name)}">${esc(sh.name)}${sh.comment ? ` <span class="muted">· ${esc(sh.comment)}</span>` : ""}</button>`).join("");
        for (const pick of $$("[data-pick]", list)) {
          pick.addEventListener("click", () => { form.nfs_address.value = `${r.server}:${pick.dataset.pick}`; preview(form.nfs_address); list.innerHTML = ""; });
        }
      } catch (err) {
        list.innerHTML = `<div class="error-box" style="margin-top:.5rem">${esc(err.message)}</div>`;
      }
    });
  }

  $("[data-cancel]", form).addEventListener("click", onCancel);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const d = formData(form);
    const body = { name: d.name, type: d.type, server_path: d.server_path || "" };
    if (d.type === "smb") {
      if (!parseAddress(d.address)?.share) {
        $(".err", form).innerHTML = `<div class="error-box">Tap “Connect” to pick the folder first, or type the share address under “Type the share address yourself”.</div>`;
        return;
      }
      Object.assign(body, { address: d.address, username: d.username, password: d.password, domain: d.domain, vers: d.vers });
    }
    if (d.type === "nfs") Object.assign(body, { address: d.nfs_address, options: d.options });
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

async function locationsPanel(container, { onChange, wizard = false } = {}) {
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
      ${data.network_shares ? "" : `<div class="warn-box">SMB/NFS shares can only be added when AniStash runs in its LXC. Local folders work everywhere.</div>`}
      <div id="new-loc"></div>
      <button class="block" id="add-loc" style="margin-top:.75rem">+ Add location</button>`;

    const reload = async () => { data = await api("/api/locations"); render(); onChange?.(data.locations); };
    $("#add-loc", container).addEventListener("click", () => {
      $("#add-loc", container).hidden = true;
      const holder = $("#new-loc", container);
      holder.innerHTML = `<div class="card" style="margin-top:.75rem">${locationForm(null, data.network_shares, { wizard })}</div>`;
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
      <label class="field"><span>Sub or dub</span><select name="mode">
        <option value="sub" ${s.mode === "sub" ? "selected" : ""}>Sub (Japanese audio, subtitles)</option>
        <option value="dub" ${s.mode === "dub" ? "selected" : ""}>Dub (English audio)</option>
        <option value="both" ${s.mode === "both" ? "selected" : ""}>Sub + Dub (both audio tracks in one file)</option></select>
        <div class="hint">What search and show pages start with. You can still switch per download. Sub + Dub lets you switch language in Jellyfin/Plex's audio menu.</div></label>
      <label class="field"><span>With Sub + Dub, play first</span><select name="both_default">
        <option value="sub" ${s.both_default !== "dub" ? "selected" : ""}>Japanese audio (with English subtitles)</option>
        <option value="dub" ${s.both_default === "dub" ? "selected" : ""}>English audio</option></select></label>
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
        <select name="library_id" data-saved="${esc(s.media_library_id || "")}">${s.media_library_id
          ? `<option value="${esc(s.media_library_id)}" selected>${esc(s.media_library_name || s.media_library_id)}</option>`
          : `<option value="">Test the connection to load your libraries</option>`}</select>
        <div class="hint">AniStash only ever scans this one library, never everything on the server. With a "media server sees this as" path on the location, it scans just the new season folders, which is faster still.</div>
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
  saved = saved ?? select.dataset.saved;
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
    const unsaved = select.value && select.value !== (select.dataset.saved || "");
    $(".lib-paths", form).innerHTML = (unsaved ? `<span style="color:var(--warn);font-weight:600">Suggested, not saved yet: press Save to use ${esc(lib?.name || "it")}.</span> ` : "")
      + (lib?.paths.length ? `Folders in ${esc(lib.name)}: ${esc(lib.paths.join(", "))}` : "");
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
  // What's shown is now what's saved
  form.library_id.dataset.saved = d.kind === "none" ? "" : d.library_id;
  form.library_id.dispatchEvent(new Event("change"));
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

function accountCard() {
  const role = status.user?.role || "";
  return `
    <form class="card" id="pw"><h2>Your account</h2>
      <p class="muted">Signed in as <b>${esc(status.username)}</b> · ${esc(role)}</p>
      <div class="grid2">
        <label class="field"><span>Current password</span><input type="password" name="current" autocomplete="current-password" required></label>
        <label class="field"><span>New password</span><input type="password" name="new" autocomplete="new-password" minlength="10" required>
          <div class="hint">At least 10 characters.</div></label>
      </div>
      <div class="row end"><button class="primary" type="submit">Change password</button></div>
      <h3 style="margin-top:1rem">Signed-in devices</h3>
      <div id="sessions" class="muted small">${loading()}</div>
      <div class="row" style="margin-top:.75rem"><button type="button" id="logout" class="ghost">Log out</button>
        <button type="button" id="logout-all" class="danger small">Log out on all devices</button></div>
    </form>`;
}

function bindAccountCard() {
  $("#pw").addEventListener("submit", async (e) => {
    e.preventDefault();
    try { await busy(e.submitter, () => api("/api/account/password", { method: "POST", body: formData(e.target) })); toast("Password changed. Other devices were signed out."); e.target.reset(); }
    catch (err) { toast(err.message, true); }
  });
  $("#logout").addEventListener("click", async () => { await api("/api/logout", { method: "POST" }); location.hash = "#/login"; });
  $("#logout-all").addEventListener("click", async () => {
    if (!confirm("Log out everywhere, including this device?")) return;
    await api("/api/account/logout-everywhere", { method: "POST" });
    location.hash = "#/login";
  });
  api("/api/account/sessions").then(({ sessions }) => {
    $("#sessions").innerHTML = sessions.map((x) => `<div>${esc(x.user_agent.replace(/^Mozilla\/5\.0 \(([^)]*)\).*$/, "$1").slice(0, 60) || "Unknown device")} · ${esc(x.ip)} · last active ${new Date(x.last_seen_at * 1000).toLocaleString()}</div>`).join("") || "None";
  }).catch(() => {});
}

const ROLE_HELP = {
  admin: "Everything, including users, locations and settings",
  member: "Browse and download; manage their own downloads",
  viewer: "Browse only",
};

function randomPassword() {
  // 16 characters from an unambiguous alphabet, from the browser's cryptographic RNG
  const chars = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789";
  const bytes = crypto.getRandomValues(new Uint32Array(16));
  return Array.from(bytes, (b) => chars[b % chars.length]).join("");
}

async function usersPanel(container) {
  let data;
  try { data = await api("/api/users"); }
  catch (e) { container.innerHTML = `<div class="error-box">${esc(e.message)}</div>`; return; }
  const when = (t) => t ? new Date(t * 1000).toLocaleDateString() : "never";
  container.innerHTML = `
    ${data.users.map((u) => `
      <div class="loc" data-user="${u.id}">
        <div style="min-width:0">
          <b>${esc(u.username)}</b> <span class="pill ${u.role === "admin" ? "accent" : ""}">${esc(u.role)}</span>
          ${u.disabled ? `<span class="pill bad">disabled</span>` : ""}${u.must_change_password ? ` <span class="pill warn">must change password</span>` : ""}
          ${u.id === status.user.id ? `<span class="pill">you</span>` : ""}
          <div class="muted small">Last login ${esc(when(u.last_login_at))}</div>
        </div>
        <div class="row" style="align-content:start">
          ${u.id === status.user.id ? "" : `
            <select class="small" data-role aria-label="Role for ${esc(u.username)}" style="min-height:36px;width:auto">
              ${data.roles.map((r) => `<option value="${r}" ${r === u.role ? "selected" : ""}>${r}</option>`).join("")}</select>
            <button class="small" data-act="password">Reset password</button>
            <button class="small" data-act="${u.disabled ? "enable" : "disable"}">${u.disabled ? "Enable" : "Disable"}</button>
            <button class="small danger" data-act="delete">Delete</button>`}
        </div>
      </div>`).join("")}
    <details class="more" id="add-user-box" style="margin-top:1rem"><summary>+ Add a user</summary>
      <form id="add-user" style="margin-top:.75rem">
        <div class="grid2">
          <label class="field"><span>Username</span><input type="text" name="username" autocapitalize="off" autocomplete="off" required pattern="[A-Za-z0-9._\-]{3,32}"></label>
          <label class="field"><span>Role</span><select name="role">${data.roles.map((r) => `<option value="${r}" ${r === "member" ? "selected" : ""}>${r}</option>`).join("")}</select>
            <div class="hint" data-role-help>${ROLE_HELP.member}</div></label>
        </div>
        <label class="field"><span>Temporary password</span>
          <div class="row"><input type="text" name="password" class="grow" autocomplete="off" minlength="10" required spellcheck="false"><button type="button" class="small" data-generate>Generate</button></div>
          <div class="hint">Give it to them privately. It's shown only now and stored only as a hash.</div></label>
        <label class="check"><input type="checkbox" name="must_change" checked> Make them choose their own password at first login</label>
        <div class="row end"><button class="primary" type="submit">Add user</button></div>
      </form>
    </details>`;

  const form = $("#add-user", container);
  form.role.addEventListener("change", () => { $("[data-role-help]", form).textContent = ROLE_HELP[form.role.value]; });
  $("[data-generate]", form).addEventListener("click", () => { form.password.value = randomPassword(); });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const d = formData(form);
    try {
      await busy(e.submitter, () => api("/api/users", { method: "POST", body: { username: d.username, password: d.password, role: d.role, must_change: d.must_change } }));
      toast(`Added ${d.username}`);
      usersPanel(container);
    } catch (err) { toast(err.message, true); }
  });
  for (const row of $$("[data-user]", container)) {
    const id = Number(row.dataset.user);
    const u = data.users.find((x) => x.id === id);
    $("[data-role]", row)?.addEventListener("change", async (e) => {
      try { await api(`/api/users/${id}`, { method: "PUT", body: { role: e.target.value } }); toast(`${u.username} is now ${e.target.value}. Their sessions were signed out.`); usersPanel(container); }
      catch (err) { toast(err.message, true); e.target.value = u.role; }
    });
    for (const b of $$("[data-act]", row)) {
      b.addEventListener("click", async () => {
        try {
          if (b.dataset.act === "password") {
            const pw = randomPassword();
            if (!confirm(`Set a new temporary password for ${u.username}?\n\nThey'll have to change it at next login, and all their sessions end.`)) return;
            await api(`/api/users/${id}/password`, { method: "POST", body: { password: pw, must_change: true } });
            sheet(`<h2>New password for ${esc(u.username)}</h2>
              <p>Give them this temporary password privately. It won't be shown again.</p>
              <p><code style="font-size:1.2rem;user-select:all">${esc(pw)}</code></p>
              <button class="primary block" data-close>Done</button>`).querySelector("[data-close]").onclick = (ev) => ev.target.closest(".sheet-backdrop").remove();
          } else if (b.dataset.act === "delete") {
            if (!confirm(`Delete ${u.username}? Their downloads stay.`)) return;
            await api(`/api/users/${id}`, { method: "DELETE" });
          } else {
            await api(`/api/users/${id}`, { method: "PUT", body: { disabled: b.dataset.act === "disable" } });
          }
          usersPanel(container);
        } catch (err) { toast(err.message, true); }
      });
    }
  }
}

const AUDIT_LABELS = {
  login: "Logged in", login_failed: "Failed login", login_locked: "Login blocked (too many tries)", logout: "Logged out",
  logout_everywhere: "Logged out everywhere", password_changed: "Changed password", password_change_failed: "Wrong current password",
  password_reset: "Reset password", user_created: "Added user", user_changed: "Changed user", user_deleted: "Deleted user",
  settings_changed: "Changed settings", media_server_changed: "Changed media server", location_added: "Added location",
  location_changed: "Changed location", location_removed: "Removed location", queued: "Queued download",
  update_started: "Started update", admin_set: "Set admin (console)",
  follow_added: "Followed", follow_removed: "Unfollowed",
};

async function auditPanel(container) {
  try {
    const { entries } = await api("/api/audit");
    container.innerHTML = entries.length ? `<div class="audit">${entries.slice(0, 100).map((e) => `
      <div class="${["login_failed", "login_locked", "password_change_failed"].includes(e.action) ? "bad" : ""}">
        <span class="muted">${esc(new Date(e.at * 1000).toLocaleString())}</span>
        <b>${esc(e.actor)}</b> ${esc(AUDIT_LABELS[e.action] || e.action)}${e.detail ? `: ${esc(e.detail)}` : ""}
        ${e.ip ? `<span class="muted">· ${esc(e.ip)}</span>` : ""}</div>`).join("")}</div>` : `<p class="muted">Nothing yet.</p>`;
  } catch (e) { container.innerHTML = `<div class="error-box">${esc(e.message)}</div>`; }
}

async function settingsView() {
  if (!can.admin()) {
    // Members and viewers only manage their own account
    view.innerHTML = `<h1>Settings</h1>${accountCard()}`;
    bindAccountCard();
    return;
  }
  view.innerHTML = loading();
  const [s, sys] = await Promise.all([api("/api/settings"), api("/api/system")]);
  view.innerHTML = `
    <h1>Settings</h1>
    <div class="card"><h2>Download locations</h2><div id="locs"></div></div>

    <form class="card" id="defaults"><h2>Downloads</h2>${defaultsForm(s)}
      <div class="row end"><button class="primary" type="submit">Save</button></div></form>

    <div class="card"><h2>Users</h2>
      <p class="muted small">Admins manage everything. Members can browse and download. Viewers can only browse.</p>
      <div id="users"></div></div>

    <form class="card" id="ms"><h2>Media server</h2>${mediaServerForm(s)}
      <div class="row end"><button type="button" id="ms-test">Test</button><button class="primary" type="submit">Save</button></div></form>

    ${accountCard()}

    <div class="card"><h2>Security log</h2>
      <p class="muted small">Logins, failed attempts and changes to users and settings.</p>
      <div id="audit"></div></div>

    <div class="card"><h2>System</h2>
      <div class="checks">
        <div><span>ani-cli</span><span>${esc(sys.ani_cli || "not found")}</span></div>
        <div><span>yt-dlp</span><span>${esc(sys.yt_dlp || "not found")}</span></div>
        <div><span>ffmpeg</span><span>${esc(sys.ffmpeg || "not found")}</span></div>
        <div><span>AniStash</span><span>v${esc(status.version)}${sys.latest ? ` · newest release ${esc(sys.latest)}` : ""}</span></div>
      </div>
      ${sys.update_available ? `<div class="ok-box">AniStash ${esc(sys.latest)} is available.
        <div class="row" style="margin-top:.5rem"><button class="primary small" id="update-app" ${sys.helper ? "" : "disabled"}>Update AniStash</button>
        <a class="small" href="https://github.com/JustinMarxGit/anistash/releases" target="_blank" rel="noopener">What's new</a></div></div>` : ""}
      <p class="muted small">If searches or downloads start failing, the anime site probably changed. Updating ani-cli usually fixes it. Tools also update weekly on their own.</p>
      <button id="update-tools" ${sys.helper ? "" : "disabled"}>Update ani-cli and yt-dlp</button>
      <pre id="update-out" class="small muted" style="white-space:pre-wrap" hidden></pre>
    </div>

    <div class="card"><h2>Setup</h2>
      <p class="muted">Walk through the setup wizard again. Your locations, settings and queue are kept.</p>
      <a class="btn" href="#/setup?rerun=1">Run setup again</a>
    </div>`;

  locationsPanel($("#locs"));
  usersPanel($("#users"));
  auditPanel($("#audit"));
  bindAccountCard();
  $("#defaults").addEventListener("submit", async (e) => {
    e.preventDefault();
    try { prefs = await busy(e.submitter, () => api("/api/settings", { method: "PUT", body: formData(e.target) })); toast("Saved"); }
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
    if (!confirm(`Update AniStash to ${sys.latest}? The page reconnects when it's done; running downloads restart.`)) return;
    try {
      await busy(e.currentTarget, () => api("/api/system/update-app", { method: "POST" }));
      toast("Updating… this page reloads when AniStash is back.");
      // The service restarts during the update: wait for the new version to answer
      const started = Date.now();
      const poll = setInterval(async () => {
        try {
          const s2 = await api("/api/status");
          if (s2.version !== status.version) { clearInterval(poll); location.reload(); }
        } catch { /* restarting */ }
        if (Date.now() - started > 300000) { clearInterval(poll); toast("The update is taking long. Check `anistash-update` in the console.", true); }
      }, 3000);
    } catch (err) { toast(err.message, true); }
  });
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
      view.innerHTML = frame("Welcome to AniStash", `
        <p>AniStash lets you browse anime and download episodes to your NAS from any device, including your phone. This wizard sets up an admin account, where downloads go, and the optional Jellyfin or Plex connection.</p>
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
          <label class="field"><span>Password</span><input type="password" name="password" autocomplete="new-password" minlength="10" required>
            <div class="hint">At least 10 characters. A few random words is easy to remember and hard to guess.</div></label>
          <label class="field"><span>Repeat password</span><input type="password" name="password2" autocomplete="new-password" minlength="10" required></label>
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
      locationsPanel($("#locs"), { wizard: true, onChange: (l) => { locs = l; next.disabled = l.length === 0; } });
      next.addEventListener("click", () => go(3));
      return;
    }

    if (step === 3) {
      if (!locs.length) locs = (await api("/api/locations")).locations;
      view.innerHTML = frame("Media server (optional)", `
        <p class="muted">If you use Jellyfin or Plex, AniStash can tell it to scan new downloads right away, with no need to wait for the scheduled scan. It only ever scans your anime library.</p>
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
          settings = prefs = await busy(e.currentTarget, () => api("/api/settings", { method: "PUT", body: formData($("#defaults")) }));
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
        <p class="muted">Tip: on your phone, use "Add to Home Screen" to open AniStash like an app.</p>`, { next: "Finish" });
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
