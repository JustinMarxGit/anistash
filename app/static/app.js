// AniStash frontend: hash-routed single page app, no build step.

const view = document.getElementById("view");
const nav = document.getElementById("nav");
let status = null;          // /api/status
let prefs = null;           // /api/settings, for defaults like sub/dub
let pagePoll = null;        // the current page's refresh (downloads)
let badgePoll = null;       // queue badge refresh
let routeSeq = 0;           // bumped on every navigation, so late async work can tell it's stale

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
  const serverVersion = resp.headers.get("X-AniStash-Version");
  if (serverVersion && PAGE_VERSION && serverVersion !== PAGE_VERSION) serverUpdated(serverVersion);
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

// The version this page was loaded with. When the server answers with a newer one (after an
// update, from this page, another device or an automatic update) the page reloads itself, so
// everyone runs the new version and sees what changed.
const PAGE_VERSION = document.querySelector('meta[name="anistash-version"]')?.content || "";
let reloading = false;
function serverUpdated(version) {
  if (reloading) return;
  reloading = true;
  if (updateSheet) return updateSheet.finished(version);  // the update window says it itself
  const reload = () => {
    // Don't throw away a half-filled form: wait until its window is closed
    if (document.querySelector(".sheet-backdrop")) return void setTimeout(reload, 1500);
    toast(`AniStash was updated to v${version}. Reloading…`);
    setTimeout(() => location.reload(), 1500);
  };
  reload();
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
  return `<span class="inlib" data-tip="${esc(`Already in your library${where ? ` ${where}` : ""}${have ? `: ${have}` : ""}`)}">${icon("check")}${label}</span>`;
}

const icon = (name, cls = "") => `<svg class="i ${cls}" aria-hidden="true"><use href="#i-${name}"/></svg>`;

// Light / dark and accent colour --------------------------------------------------------------
// Each person's own: saved with their account (so it follows them to other devices) and in this
// browser too, so the page doesn't flash the wrong colours while it loads.

// The presets come from Sanzo Wada's "A Dictionary of Color Combinations" (Haishoku Sōkan, 1933):
// each is one of his colours, shown with the two it's paired with in one of his numbered combinations.
// [hex ('' = AniStash's own vermilion, Wada's Red Orange), name, combination No., [partner colours]]
const ACCENTS = [
  ["", "Red Orange", 164, [["#fcb315", "Orange Yellow"], ["#4f4086", "Violet"]]],
  ["#f37f94", "Eosine Pink", 134, [["#9a72aa", "Light Mauve"], ["#59256a", "Red Violet"]]],
  ["#d46d7a", "Old Rose", 162, [["#8fa071", "Rainette Green"], ["#b984af", "Lilac"]]],
  ["#cc1236", "Carmine", 122, [["#fdbf68", "Cream Yellow"], ["#00978d", "Benzol Green"]]],
  ["#f99d1b", "Yellow Orange", 151, [["#f5ecc2", "Sulpher Yellow"], ["#064f6e", "Vandar Poel's Blue"]]],
  ["#437742", "Cossack Green", 135, [["#f5ecc2", "Sulpher Yellow"], ["#97acc8", "Salvia Blue"]]],
  ["#00b49b", "Sea Green", 133, [["#82241f", "Vandyke Red"], ["#b09f36", "Citrine"]]],
  ["#0093a5", "Cerulian Blue", 125, [["#d1b0a7", "Fawn"], ["#40456a", "Violet Blue"]]],
  ["#5a82b3", "Olympic Blue", 157, [["#7d133a", "Pansy Purple"], ["#d6b43e", "Olive Ocher"]]],
  ["#1c4286", "Deep Lyons Blue", 126, [["#ebd3a2", "Ivory Buff"], ["#e2b540", "Yellow Ocher"]]],
  ["#a36aa5", "Aconite Violet", 187, [["#005b8d", "Helvetia Blue"], ["#c0a9b3", "Grayish Lavender"]]],
  ["#34454c", "Slate Color", 140, [["#f3a257", "Golden Yellow"], ["#007190", "Antwarp Blue"]]],
];
const darkQuery = window.matchMedia("(prefers-color-scheme: dark)");
let theme = { mode: "system", accent: "" };
try { Object.assign(theme, JSON.parse(localStorage.getItem("anistash-theme") || "{}")); } catch { /* storage blocked */ }

const hexToRgb = (h) => { const n = parseInt(h.slice(1), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; };
const rgbToHex = (c) => "#" + c.map((v) => Math.round(Math.max(0, Math.min(255, v))).toString(16).padStart(2, "0")).join("");
const mixRgb = (a, b, t) => a.map((v, i) => v + (b[i] - v) * t);
function luminance(c) {
  const [r, g, b] = c.map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
function contrast(a, b) {
  const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
}
// Any colour stays readable: nudged darker on light pages and lighter on dark ones until text in
// it stands out from the background (WCAG 3:1, the level for large text and controls)
function readableAccent(hex, dark) {
  const bg = dark ? [22, 24, 30] : [255, 253, 249];
  let c = hexToRgb(hex);
  for (let i = 0; i < 14 && contrast(c, bg) < 3; i++) c = mixRgb(c, dark ? [255, 255, 255] : [0, 0, 0], 0.12);
  return c;
}
const isDark = () => theme.mode === "dark" || (theme.mode !== "light" && darkQuery.matches);

function applyTheme(next = {}, { fade = false } = {}) {
  theme = { ...theme, ...next };
  const root = document.documentElement;
  if (fade && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    root.classList.add("theme-fade");
    clearTimeout(applyTheme.timer);
    applyTheme.timer = setTimeout(() => root.classList.remove("theme-fade"), 450);
  }
  if (theme.mode === "light" || theme.mode === "dark") root.dataset.theme = theme.mode;
  else delete root.dataset.theme;
  const dark = isDark();
  if (theme.accent) {
    const c = readableAccent(theme.accent, dark);
    const white = [255, 255, 255];
    root.style.setProperty("--accent", rgbToHex(c));
    root.style.setProperty("--accent-hover", rgbToHex(mixRgb(c, dark ? white : [0, 0, 0], 0.14)));
    root.style.setProperty("--accent-soft", `rgb(${c.map(Math.round).join(" ")} / ${dark ? 14 : 11}%)`);
    // White text like AniStash's own buttons, unless the colour is too light for it
    root.style.setProperty("--accent-text", contrast(c, white) >= 3 ? "#fff" : "#16130f");
  } else {
    for (const v of ["--accent", "--accent-hover", "--accent-soft", "--accent-text"]) root.style.removeProperty(v);
  }
  for (const m of document.querySelectorAll('meta[name="theme-color"]')) m.content = dark ? "#0d0e12" : "#f5f2ec";
  // The browser tab's icon wears the same colour as the logo
  const css = getComputedStyle(root);
  const fill = css.getPropertyValue("--accent").trim(), ink = css.getPropertyValue("--accent-text").trim();
  const favicon = document.getElementById("favicon");
  if (favicon && fill) {
    favicon.href = "data:image/svg+xml," + encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 104 104">
      <rect width="104" height="104" rx="26" fill="${fill}"/>
      <path d="M29 33.5H75.5Q72 45.5 57.5 55M50.5 46Q51.5 66.5 33 79" fill="none" stroke="${ink}" stroke-width="10" stroke-linecap="round" stroke-linejoin="round"/></svg>`);
  }
  const toggle = document.getElementById("theme-toggle");
  if (toggle) {
    toggle.innerHTML = icon(dark ? "sun" : "moon");
    toggle.setAttribute("aria-label", dark ? "Switch to light mode" : "Switch to dark mode");
    toggle.dataset.tip = dark ? "Switch to light mode" : "Switch to dark mode";
  }
  try { localStorage.setItem("anistash-theme", JSON.stringify(theme)); } catch { /* storage blocked */ }
}

// Saves a change for this person (and shows it straight away)
async function saveTheme(next) {
  applyTheme(next, { fade: true });
  if (!status?.user) return;
  try {
    await api("/api/account/preferences", { method: "PUT", body: { theme_mode: theme.mode, accent: theme.accent } });
    Object.assign(status.user, { theme_mode: theme.mode, accent: theme.accent });
  } catch (err) { toast(err.message, true); }
}

applyTheme();
darkQuery.addEventListener("change", () => applyTheme({}, { fade: true }));
document.getElementById("theme-toggle").addEventListener("click", () => saveTheme({ mode: isDark() ? "light" : "dark" }));

function skeletonPosters(n) {
  return Array.from({ length: n }, (_, k) => `<div class="poster" style="--i:${k}"><div class="art skel"></div><div class="skel skel-line"></div><div class="skel skel-line" style="width:60%"></div></div>`).join("");
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

// A repeating refresh that pauses while the tab is hidden (catching up as soon as it's
// visible again) and never starts a run while the previous one is still going
function poller(fn, ms) {
  let busy = false;
  const tick = async () => {
    if (busy || document.hidden) return;
    busy = true;
    try { await fn(); } catch { /* the next tick tries again */ } finally { busy = false; }
  };
  const timer = setInterval(tick, ms);
  const onVisible = () => { if (!document.hidden) tick(); };
  document.addEventListener("visibilitychange", onVisible);
  return { tick, stop() { clearInterval(timer); document.removeEventListener("visibilitychange", onVisible); } };
}

// Updates `from` in place to look like `to` (both elements), keeping the nodes that stay the
// same, so refreshes don't restart animations, lose hover/focus or reload anything.
// Children with data-key are matched by key, so reordered rows move instead of being rebuilt.
function morph(from, to) {
  if (from.nodeType !== to.nodeType || from.nodeName !== to.nodeName
      || (from.nodeType === 1 && from.dataset.key !== to.dataset.key)) {
    from.replaceWith(to);
    return;
  }
  if (from.nodeType !== 1) {
    if (from.nodeValue !== to.nodeValue) from.nodeValue = to.nodeValue;
    return;
  }
  for (const { name } of [...from.attributes]) if (!to.hasAttribute(name)) from.removeAttribute(name);
  for (const { name, value } of to.attributes) if (from.getAttribute(name) !== value) from.setAttribute(name, value);
  morphChildren(from, to);
}

function morphChildren(from, to) {
  const keyed = new Map();
  for (const child of from.children) if (child.dataset.key) keyed.set(child.dataset.key, child);
  let cur = from.firstChild;
  for (const next of [...to.childNodes]) {
    const key = next.nodeType === 1 ? next.dataset.key : undefined;
    if (key && keyed.has(key)) {
      const old = keyed.get(key);
      keyed.delete(key);
      if (old === cur) cur = cur.nextSibling;
      else from.insertBefore(old, cur);
      morph(old, next);
    } else if (!key && cur && !(cur.nodeType === 1 && cur.dataset.key)) {
      const old = cur;
      cur = cur.nextSibling;
      morph(old, next);
    } else {
      from.insertBefore(next, cur);
    }
  }
  while (cur) { const after = cur.nextSibling; cur.remove(); cur = after; }
}

// Artwork loads as it nears the screen instead of all at once. Elements carry the image
// URL in data-bg; `root` is a sideways-scrolling shelf, which needs its own observer.
function lazyImages(container, root = null) {
  const els = container.querySelectorAll("[data-bg]");
  const show = (el) => { el.style.backgroundImage = `url(${JSON.stringify(el.dataset.bg)})`; el.removeAttribute("data-bg"); };
  if (!("IntersectionObserver" in window)) return els.forEach(show);
  const io = new IntersectionObserver((entries) => {
    for (const e of entries) if (e.isIntersecting) { show(e.target); io.unobserve(e.target); }
  }, { root, rootMargin: root ? "0px 600px" : "500px 0px" });
  els.forEach((el) => io.observe(el));
}

function sheet(html, { dismissable = true } = {}) {
  const backdrop = document.createElement("div");
  backdrop.className = "sheet-backdrop";
  backdrop.innerHTML = `<div class="sheet" role="dialog" aria-modal="true">${html}</div>`;
  if (dismissable) backdrop.addEventListener("click", (e) => { if (e.target === backdrop) backdrop.remove(); });
  document.body.append(backdrop);
  document.documentElement.classList.add("sheet-open");
  return backdrop;
}
// The page unlocks once the last window is gone, however it was closed
new MutationObserver(() => {
  if (!document.querySelector(".sheet-backdrop")) document.documentElement.classList.remove("sheet-open");
}).observe(document.body, { childList: true });

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
  ['[data-shelf="-1"]', "Scroll this row back"],
  ['[data-shelf="1"]', "Scroll this row to see more"],
  ['#media-user', "Which Jellyfin/Plex account is yours. Picked for you suggests shows like the ones it watches"],
  ['[data-media]', "Which Jellyfin/Plex account is this person's, for their Picked for you"],
  ['#surprise', "Open a random anime: a good way to find something new. On Discover it keeps to the genres you picked"],
  ['#again', "Pick another random anime"],
  ['[data-go="-1"]', "Previous show"],
  ['[data-go="1"]', "Next show"],
  ['.dot', "Jump to this show"],
  ['.chip', "Show anime of this genre"],
  ['[data-sort="rated"]', "Best scored by fans first"],
  ['[data-sort="newest"]', "Most recently started first"],
  ['#find', "Look this show up on the anime site to pick seasons and episodes to download"],
  ['#theme-toggle', "Switch between light and dark"],
  ['[data-theme-mode="system"]', "Follow your device: light by day, dark at night if your device does that"],
  ['.swatch', "Use this colour for buttons and highlights (only you see it)"],
  ['#accent-picker', "Pick any colour you like"],
  ['.sn-back', "Back to the list of settings"],
  ['#mal-clear', "Forget the saved MyAnimeList Client ID (Jikan is used instead)"],
  ['#check-updates', "Ask GitHub now whether a newer AniStash is out (normally checked every few hours)"],
  ['#auto-update', "Install new AniStash versions by themselves, while nothing is downloading"],
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
  ['#replay-tour', "Walk through the short guided tour of AniStash again"],
  ['[data-lang="en"]', "Show anime titles in English"],
  ['[data-lang="ja"]', "Show anime titles in Japanese (romaji), e.g. Sousou no Frieren"],
  ['[data-setlang="en"]', "Show anime titles in English"],
  ['[data-setlang="ja"]', "Show anime titles in Japanese (romaji), e.g. Sousou no Frieren"],
  ['#show-changes', "See what changed in each AniStash update"],
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
// New elements get their tips once per frame, however many changes that frame made
let tipsQueued = false;
new MutationObserver(() => {
  if (tipsQueued) return;
  tipsQueued = true;
  requestAnimationFrame(() => { tipsQueued = false; applyTips(); });
}).observe(document.body, { childList: true, subtree: true });
applyTips();

// What's new and the guided tour ------------------------------------------------------

async function showWhatsNew({ all = false } = {}) {
  let data;
  try { data = await api(`/api/account/whats-new${all ? "?all=1" : ""}`); } catch { return; }
  if (!data.entries.length) return;
  const sh = sheet(`
    <h2>What's new in AniStash</h2>
    ${data.entries.map((e) => `
      <div class="whatsnew">
        <div class="muted small">Version ${esc(e.version)}</div>
        <h3>${esc(e.title)}</h3>
        <ul>${e.items.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>
      </div>`).join("")}
    <button class="primary block" data-close>Got it</button>`);
  const close = () => { sh.remove(); if (!all) api("/api/account/whats-new/seen", { method: "POST" }).catch(() => {}); };
  sh.querySelector("[data-close]").onclick = close;
  sh.addEventListener("click", (e) => { if (e.target === sh) close(); });
}

function tourSteps() {
  const admin = can.admin(), downloads = can.download();
  return [
    { route: "#/", title: "Welcome to AniStash",
      text: "This quick tour shows you around. It takes about a minute, and you can skip it any time." },
    { route: "#/", el: '[data-nav="browse"]', title: "Browse",
      text: "What's trending and what's airing this season. Shows you already have get a green ✓ In library badge." },
    { route: "#/", el: "#langswitch", title: "English or Japanese titles",
      text: "Switch every title between English and Japanese. Search finds a show by either name, whichever you pick." },
    { route: "#/", el: "#best", title: "Best this season",
      text: "The best shows airing now, ranked by fan ratings from MyAnimeList, AniList and Kitsu, and by what people are watching. The number on each poster is the combined rating; hover over it to see where it comes from." },
    { route: "#/", el: "#foryou, #foryou-hint", title: "Picked for you",
      text: "Shows airing now that are close to what you watch in Jellyfin or Plex, and what you have and follow, with the reason underneath. The more you watch, the better it gets." },
    { route: "#/", el: ".poster", title: "Found something?",
      text: "Tap a poster to see what it's about, where it fits in its story, and more shows like it. Find episodes takes you to downloading." },
    { route: "#/discover", el: "#genres", title: "Discover",
      text: "Browse every anime by genre: pick one or more, then sort by popular, top rated, trending or newest, and narrow it by type, status or year." },
    { route: "#/discover", el: "#surprise", title: "Surprise me",
      text: "Can't decide? Surprise me opens a random, well-liked anime, keeping to the genres you picked." },
    { route: "#/search", el: "#search", title: "Search",
      text: "Type a show's name and press Search. Tap a result to open it." },
    { route: "#/search", el: ".segmented[aria-label=Audio]", title: "Sub, Dub or both",
      text: "Japanese with subtitles, English audio, or Sub + Dub: one file with both languages, so you can switch in Jellyfin or Plex." },
    downloads
      ? { title: "Downloading a show",
          text: "On a show's page, tick the episodes you want (or tap All) and press Download. Tap Follow and new episodes will download by themselves as they come out, new seasons too." }
      : { title: "Watching", text: "Your account can browse and search. Ask an admin if you'd like to download too." },
    { route: "#/downloads", el: '[data-tabto="queue"]', title: "Downloads",
      text: "See what's downloading and what's finished. Finished episodes go straight to your NAS, and Jellyfin/Plex is told about them." },
    { route: "#/downloads", el: '[data-tabto="following"]', title: "Following",
      text: "Shows you follow, when their next episode airs, and when AniStash last checked." },
    { route: "#/settings", el: "#settings-nav", title: "Settings",
      text: admin ? "Settings is split into sections: updates, appearance (light or dark, your own colours), downloads, storage, Jellyfin/Plex, users and more."
                  : "Pick light or dark and your own colours under Appearance, and change your password under Your account." },
    { route: "#/", title: "One last tip",
      text: "Not sure what a button does? Hover over it, or press and hold it on a phone, and it explains itself. Enjoy!" },
  ];
}

let tour = null;

async function waitFor(selector, ms = 6000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    const el = document.querySelector(selector);
    if (el && el.offsetParent !== null) return el;
    await new Promise((r) => setTimeout(r, 120));
  }
  return null;
}

async function startTour() {
  if (tour) return;
  tour = { steps: tourSteps(), at: 0 };
  tour.root = document.createElement("div");
  tour.root.className = "tour";
  tour.root.innerHTML = `<div class="tour-spot"></div><div class="tour-pop" role="dialog" aria-live="polite"></div>`;
  document.body.append(tour.root);
  window.addEventListener("resize", placeTour);
  window.addEventListener("scroll", placeTour, { passive: true });
  await showTourStep(0);
}

function endTour(done = true) {
  if (!tour) return;
  tour.root.remove();
  window.removeEventListener("resize", placeTour);
  window.removeEventListener("scroll", placeTour);
  tour = null;
  if (done) api("/api/account/tour", { method: "POST", body: { done: true } }).catch(() => {});
}

async function showTourStep(i) {
  if (!tour) return;
  tour.at = Math.max(0, Math.min(i, tour.steps.length - 1));
  const step = tour.steps[tour.at];
  const pop = tour.root.querySelector(".tour-pop");
  if (step.route && !location.hash.startsWith(step.route) && !(step.route === "#/" && ["", "#", "#/"].includes(location.hash))) {
    location.hash = step.route;
  }
  tour.target = step.el ? await waitFor(step.el) : null;
  if (!tour) return;
  tour.target?.scrollIntoView({ block: "center", behavior: "instant" });
  const last = tour.at === tour.steps.length - 1;
  pop.innerHTML = `
    <div class="content">
    <div class="tour-dots" aria-label="Step ${tour.at + 1} of ${tour.steps.length}">${tour.steps.map((_, k) => `<i class="${k === tour.at ? "on" : ""}"></i>`).join("")}</div>
    <h3>${esc(step.title)}</h3>
    <p>${esc(step.text)}</p>
    <div class="row">
      <button class="ghost small" data-tour="skip">${last ? "" : "Skip tour"}</button>
      <span class="grow"></span>
      ${tour.at ? `<button class="small" data-tour="back">Back</button>` : ""}
      <button class="primary small" data-tour="next">${last ? "Finish" : "Next"}</button>
    </div>
    </div>`;
  pop.querySelector('[data-tour="skip"]').hidden = last;
  pop.querySelector('[data-tour="next"]').onclick = () => (last ? endTour() : showTourStep(tour.at + 1));
  pop.querySelector('[data-tour="back"]')?.addEventListener("click", () => showTourStep(tour.at - 1));
  pop.querySelector('[data-tour="skip"]').onclick = () => endTour();
  placeTour();
  pop.querySelector('[data-tour="next"]').focus({ preventScroll: true });
}

function placeTour() {
  if (!tour) return;
  const spot = tour.root.querySelector(".tour-spot");
  const pop = tour.root.querySelector(".tour-pop");
  const target = tour.target;
  if (!target || !document.contains(target)) {
    // No element for this step: a dimmed screen with the card in the middle
    Object.assign(spot.style, { left: "50%", top: "50%", width: "0px", height: "0px" });
    pop.style.left = `${Math.max(12, (window.innerWidth - pop.offsetWidth) / 2)}px`;
    pop.style.top = `${Math.max(12, (window.innerHeight - pop.offsetHeight) / 2)}px`;
    return;
  }
  const r = target.getBoundingClientRect();
  const pad = 6;
  Object.assign(spot.style, { left: `${r.left - pad}px`, top: `${r.top - pad}px`, width: `${r.width + pad * 2}px`, height: `${r.height + pad * 2}px` });
  const below = r.bottom + pad + 12;
  const top = below + pop.offsetHeight < window.innerHeight - 8 ? below : Math.max(8, r.top - pad - 12 - pop.offsetHeight);
  pop.style.top = `${top}px`;
  pop.style.left = `${Math.max(12, Math.min(r.left + r.width / 2 - pop.offsetWidth / 2, window.innerWidth - pop.offsetWidth - 12))}px`;
}

document.addEventListener("keydown", (e) => {
  if (!tour) return;
  if (e.key === "Escape") endTour();
  if (e.key === "ArrowRight") showTourStep(tour.at + 1);
  if (e.key === "ArrowLeft") showTourStep(tour.at - 1);
});

// Router ----------------------------------------------------------------------

const routes = {
  "": browseView,
  "search": searchView,
  "discover": discoverView,
  "anime": animeView,
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
  const seq = ++routeSeq;
  pagePoll?.stop();
  pagePoll = null;
  document.querySelector(".actionbar")?.remove();
  document.querySelector(".sheet-backdrop")?.remove();
  const name = location.hash.replace(/^#\/?/, "").split("?")[0];
  try {
    status = await api("/api/status");
  } catch (e) {
    view.innerHTML = `<div class="error-box">Can't reach AniStash: ${esc(e.message)}</div>`;
    return;
  }
  if (seq !== routeSeq) return;  // a newer navigation started while this one waited
  if (status.user && (status.user.theme_mode !== theme.mode || status.user.accent !== theme.accent)) {
    applyTheme({ mode: status.user.theme_mode, accent: status.user.accent }, { fade: true });
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
    a.classList.toggle("active", a.dataset.nav === (name || "browse") || (name === "show" && a.dataset.nav === "search")
      || (name === "anime" && a.dataset.nav === "discover"));
  }
  requestAnimationFrame(moveNavIndicator);
  const lang = document.getElementById("langswitch");
  lang.hidden = !ready;
  for (const b of lang.querySelectorAll("[data-lang]")) b.classList.toggle("on", b.dataset.lang === titleLang());
  // Every page slides in
  view.classList.remove("enter");
  void view.offsetWidth;
  view.classList.add("enter");
  if (ready && !badgePoll) { badgePoll = poller(refreshBadge, 10000); badgePoll.tick(); }
  if (ready && !prefs) prefs = await api("/api/settings").catch(() => null);
  if (seq !== routeSeq) return;
  window.scrollTo(0, 0);
  await (routes[name] || browseView)();
  if (ready && !route.bannerChecked) { route.bannerChecked = true; refreshUpdateBanner(); }
  // Once per person: what changed since their last visit, or the tour for newcomers
  if (ready && !tour && !document.querySelector(".sheet-backdrop")) {
    if (status.user?.whats_new && !route.whatsNewShown) { route.whatsNewShown = true; showWhatsNew(); }
    else if (status.user && !status.user.tour_done && !route.tourShown && name === "") { route.tourShown = true; startTour(); }
  }
}

async function refreshBadge() {
  if (pagePoll && $("#queue[data-view=queue]")) return;  // the queue page's own refresh keeps it current
  try { setBadge(await api("/api/queue/summary")); } catch { /* shown elsewhere */ }
}

function setBadge(counts) {
  const active = counts.queued + counts.running;
  const badge = document.getElementById("queue-badge");
  badge.hidden = active === 0;
  if (badge.textContent !== String(active)) badge.textContent = active;
}

// English / Japanese titles: saved per person, applied by re-rendering the page
async function setTitleLang(lang) {
  if (!status?.user || lang === titleLang()) return;
  try {
    await api("/api/account/preferences", { method: "PUT", body: { title_lang: lang } });
    status.user.title_lang = lang;
    toast(lang === "ja" ? "Showing Japanese titles" : "Showing English titles");
    route();
  } catch (err) { toast(err.message, true); }
}
document.getElementById("langswitch").addEventListener("click", (e) => {
  const b = e.target.closest("[data-lang]");
  if (b) setTitleLang(b.dataset.lang);
});

// The highlight behind the active tab glides to it
function moveNavIndicator() {
  const ind = $(".nav-ind", nav), active = $("a.active", nav);
  if (!ind) return;
  if (!active || nav.hidden) { ind.style.opacity = "0"; return; }
  ind.style.opacity = "1";
  ind.style.transform = `translateX(${active.offsetLeft}px)`;
  ind.style.width = `${active.offsetWidth}px`;
}
window.addEventListener("resize", moveNavIndicator);
window.addEventListener("scroll", () => document.body.classList.toggle("scrolled", window.scrollY > 4), { passive: true });

window.addEventListener("hashchange", route);
route();

// Login -----------------------------------------------------------------------------

function loginView() {
  view.innerHTML = `
    <form class="login card" id="login">
      <svg class="seal" aria-hidden="true"><use href="#logo"/></svg>
      <h1>Welcome back</h1>
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

// Titles: English or Japanese (romaji), per person (Settings or the EN/JA switch at the top)
const titleLang = () => status?.user?.title_lang || "en";
function showTitle(m) {
  if (!m) return "";
  return titleLang() === "ja" ? m.romaji || m.title : m.english || m.title || m.romaji;
}

function posterCard(m, k, { rank = 0 } = {}) {
  const sub = [m.format?.replace("_", " "), m.year, m.episodes && `${m.episodes} eps`].filter(Boolean).join(" · ");
  const score = m.rating ? `<span class="score" data-tip="${esc(ratingTip(m))}">${icon("star")}${(m.rating.combined / 10).toFixed(1)}</span>` : "";
  const dub = m.available?.dub ? `<span class="dubtag" data-tip="${esc(`An English dub is out: ${m.available.dub} episode${m.available.dub === 1 ? "" : "s"} so far`)}">Dub</span>` : "";
  return `<a class="poster rise" style="--i:${k}" href="#/anime?id=${m.id}" data-card="${m.id}">
      ${rank ? `<span class="rank ${rank >= 10 ? "two" : ""}" aria-hidden="true">${rank}</span>` : ""}
      <div class="art" data-bg="${esc(m.cover || "")}">${score}${dub}<span data-lib></span></div>
      <div class="name">${esc(showTitle(m))}</div>
      <div class="sub">${esc(sub)}</div>
      ${m.reason ? `<div class="reason">${esc(m.reason)}</div>` : ""}
    </a>`;
}

// "8.7": what it's made of, in plain words
function ratingTip(m) {
  const r = m.rating, parts = [];
  if (r.mal) parts.push(`MyAnimeList ${(r.mal / 10).toFixed(2)}`);
  if (r.anilist) parts.push(`AniList ${Math.round(r.anilist)}%`);
  if (r.kitsu) parts.push(`Kitsu ${Math.round(r.kitsu)}%`);
  const ranks = m.available?.ranks || {};
  const hot = ranks.trending ? `#${ranks.trending} trending on hianime` : ranks.top10 ? `#${ranks.top10} most watched on hianime today`
    : ranks.airing ? `#${ranks.airing} top airing on hianime` : "";
  return `Rated ${(r.combined / 10).toFixed(1)} out of 10 by fans: ${parts.join(" · ")}${hot ? `. Right now: ${hot}` : ""}`;
}

function markLibrary(el, items) {
  libraryMatch(items.map((m) => ({ id: m.id, titles: [m.english, m.romaji, m.title] }))).then((matches) => {
    for (const [id, m] of Object.entries(matches)) {
      for (const slot of el.querySelectorAll(`[data-card="${id}"] [data-lib]`)) slot.outerHTML = libraryBadge(m);
    }
  });
}

// The top of Browse: the most trending shows with wide artwork, one after another on a timer.
// Each dot fills up while its slide shows; the timer pauses while you hover, while the banner
// is off screen or the tab is hidden, and doesn't run at all with reduced motion. Each show
// stays 7 seconds (the dot-fill animation in app.css).
function featureCarousel(feature, items) {
  const slides = items.map((m, i) => ({ m, rank: i + 1 })).filter(({ m }) => m.banner).slice(0, 6);
  if (!slides.length) slides.push({ m: items[0], rank: 1 });
  feature.innerHTML = `
    <div class="slides">${slides.map(({ m, rank }, i) => `
      <div class="slide ${i ? "" : "on"}" data-i="${i}" aria-hidden="${i ? "true" : "false"}">
        <div class="bg" data-bg="${esc(m.banner || m.cover)}"></div>
        <div class="slide-text">
          <span class="eyebrow">${icon("spark")} #${rank} trending</span>
          <h1>${esc(showTitle(m))}</h1>
          <div class="meta">${esc([m.format?.replace("_", " "), m.year, m.episodes && `${m.episodes} episodes`].filter(Boolean).join(" · "))}${(m.genres || []).length
            ? `<span class="meta-genres"> · ${esc(m.genres.join(" · "))}</span>` : ""}</div>
          <div class="row"><a class="btn primary" href="#/anime?id=${m.id}" ${i ? 'tabindex="-1"' : ""}>${icon("arrow")}More info</a>
            <a class="btn glass" href="#/search?q=${encodeURIComponent(m.english || m.romaji || m.title)}" ${i ? 'tabindex="-1"' : ""}>${icon("download")}Find episodes</a>
            <span data-card="${m.id}"><span data-lib></span></span></div>
        </div>
      </div>`).join("")}</div>
    ${slides.length > 1 ? `<div class="carousel-ui">
      <button type="button" class="fbtn" data-go="-1" aria-label="Previous show">${icon("back")}</button>
      <div class="dots" role="tablist">${slides.map((_, i) => `<button type="button" class="dot ${i ? "" : "on"}" data-i="${i}" aria-label="Show ${i + 1} of ${slides.length}"><i></i></button>`).join("")}</div>
      <button type="button" class="fbtn" data-go="1" aria-label="Next show">${icon("arrow")}</button>
    </div>` : ""}`;

  const els = [...feature.querySelectorAll(".slide")];
  const dots = [...feature.querySelectorAll(".dot")];
  const showBg = (i) => {
    const bg = els[i]?.querySelector(".bg[data-bg]");
    if (bg) { bg.style.backgroundImage = `url(${JSON.stringify(bg.dataset.bg)})`; bg.removeAttribute("data-bg"); }
  };
  let current = 0;
  const go = (i) => {
    current = (i + els.length) % els.length;
    els.forEach((el, k) => {
      el.classList.toggle("on", k === current);
      el.setAttribute("aria-hidden", k === current ? "false" : "true");
      for (const a of el.querySelectorAll("a")) a.tabIndex = k === current ? 0 : -1;
    });
    dots.forEach((d) => d.classList.remove("on"));
    void feature.offsetWidth;  // restart the dot's timer animation
    dots[current]?.classList.add("on");
    showBg(current);
    showBg((current + 1) % els.length);  // the next banner loads ahead of time
  };
  showBg(0);
  showBg(1);
  pauseOffscreen(feature);
  if (els.length < 2) return;

  feature.addEventListener("click", (e) => {
    const step = e.target.closest("[data-go]"), dot = e.target.closest(".dot");
    if (step) go(current + Number(step.dataset.go));
    else if (dot) go(Number(dot.dataset.i));
  });
  // The active dot's fill animation is the timer: when it ends, the next show slides in
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (!reduced) feature.addEventListener("animationend", (e) => { if (e.animationName === "dot-fill") go(current + 1); });
  else feature.classList.add("still");
  const pause = (on) => feature.classList.toggle("paused", on);
  // Reading a show pauses the timer; the arrows and dots don't, so after clicking one the new
  // show's bar visibly starts filling. Only keyboard focus pauses (a clicked button keeps focus).
  feature.addEventListener("pointerover", (e) => { if (e.pointerType === "mouse") pause(!e.target.closest(".carousel-ui")); });
  feature.addEventListener("pointerleave", (e) => { if (e.pointerType === "mouse") pause(false); });
  feature.addEventListener("focusin", (e) => { if (e.target.matches(":focus-visible") && !e.target.closest(".carousel-ui")) pause(true); });
  feature.addEventListener("focusout", () => pause(false));
  const onVisible = () => { if (!feature.isConnected) return document.removeEventListener("visibilitychange", onVisible); feature.classList.toggle("hidden-tab", document.hidden); };
  document.addEventListener("visibilitychange", onVisible);
  // Swipe on phones: the banner follows your thumb, lifts and tilts like a picked-up card, and
  // is thrown off the side when you let go fast or far enough (otherwise it springs back).
  // The next show rises from underneath as you drag.
  let drag = null, frame = 0;
  const neighbour = (dir) => els[(current + dir + els.length) % els.length];
  const lay = (dx) => {
    const el = els[current], w = feature.clientWidth, p = Math.min(1, Math.abs(dx) / w);
    el.style.transform = `translateX(${dx}px) rotate(${(dx / w) * 7}deg) scale(${1 - p * 0.05})`;
    const under = dx === 0 ? null : neighbour(dx < 0 ? 1 : -1);
    for (const n of els) {
      if (n === el) continue;
      n.classList.toggle("peek", n === under);
      if (n === under) { n.style.transform = `scale(${0.9 + 0.1 * p})`; n.style.opacity = String(0.35 + 0.65 * p); }
      else { n.style.transform = ""; n.style.opacity = ""; }
    }
  };
  const settle = (els_, ms, then) => setTimeout(() => {
    feature.classList.add("instant");
    then?.();
    for (const n of els_) { n.style.transform = ""; n.style.transition = ""; n.style.opacity = ""; n.classList.remove("peek"); }
    requestAnimationFrame(() => requestAnimationFrame(() => feature.classList.remove("instant")));
    feature.classList.remove("dragging");
    pause(false);
  }, ms);

  feature.addEventListener("pointerdown", (e) => {
    if (e.pointerType === "mouse" || e.target.closest(".carousel-ui") || drag?.busy) return;
    drag = { x: e.clientX, y: e.clientY, dx: 0, active: false, samples: [[e.timeStamp, e.clientX]], id: e.pointerId };
  });
  feature.addEventListener("pointermove", (e) => {
    if (!drag || drag.busy || e.pointerId !== drag.id) return;
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    if (!drag.active) {
      if (Math.abs(dy) > 10 && Math.abs(dy) > Math.abs(dx)) { drag = null; return; }  // scrolling the page
      if (Math.abs(dx) < 8) return;
      drag.active = true;
      feature.classList.add("dragging");
      pause(true);
      try { feature.setPointerCapture(e.pointerId); } catch { /* synthetic or already released */ }
    }
    drag.dx = dx;
    drag.samples.push([e.timeStamp, e.clientX]);
    while (drag.samples.length > 2 && e.timeStamp - drag.samples[0][0] > 100) drag.samples.shift();
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(() => drag && lay(drag.dx));
  });
  const release = (e) => {
    if (!drag || drag.busy || (e && e.pointerId !== drag.id)) return;
    if (!drag.active) { drag = null; return; }
    cancelAnimationFrame(frame);
    const { dx, samples } = drag;
    const [t0, x0] = samples[0], [t1, x1] = samples[samples.length - 1];
    const v = t1 > t0 ? (x1 - x0) / (t1 - t0) : 0;  // px per ms, over the last tenth of a second
    const w = feature.clientWidth, el = els[current];
    const thrown = Math.abs(dx) > w * 0.28 || (Math.abs(v) > 0.45 && Math.sign(v) === Math.sign(dx));
    feature.dataset.dragged = String(Date.now());  // the tap that ended the drag isn't a click
    drag.busy = true;
    if (thrown) {
      const dir = dx < 0 ? 1 : -1, under = neighbour(dir);
      const target = -dir * w * 1.3;
      // as fast as it was thrown, within reason
      const ms = Math.round(Math.max(170, Math.min(420, Math.abs(target - dx) / Math.max(Math.abs(v), 1.3))));
      el.style.transition = `transform ${ms}ms cubic-bezier(.15,.75,.35,1), opacity ${ms}ms ease-in`;
      el.style.transform = `translateX(${target}px) rotate(${-dir * 16}deg) scale(.92)`;
      el.style.opacity = "0.4";
      under.classList.add("peek");
      under.style.transition = `transform ${ms}ms cubic-bezier(.2,.8,.3,1), opacity ${ms}ms`;
      under.style.transform = "scale(1)";
      under.style.opacity = "1";
      settle([el, under], ms, () => go(current + dir));
    } else {
      el.style.transition = "transform .5s cubic-bezier(.34,1.56,.64,1)";
      el.style.transform = "";
      for (const n of els) if (n.classList.contains("peek")) { n.style.transition = "transform .4s, opacity .4s"; n.style.transform = "scale(.9)"; n.style.opacity = "0"; }
      settle(els, 500);
    }
    setTimeout(() => { drag = null; }, 520);
  };
  feature.addEventListener("pointerup", release);
  feature.addEventListener("pointercancel", release);
  feature.addEventListener("click", (e) => {
    if (Date.now() - Number(feature.dataset.dragged || 0) < 400) { e.preventDefault(); e.stopPropagation(); }
  }, true);
}

// The banner's slow zoom only runs while it's on screen
function pauseOffscreen(el) {
  if (!el || !("IntersectionObserver" in window)) return;
  new IntersectionObserver(([e]) => el.classList.toggle("offscreen", !e.isIntersecting)).observe(el);
}

async function browseView() {
  view.innerHTML = `
    <section class="feature" id="feature"><div class="bg skel"></div></section>
    <form class="searchbar" id="quick">${icon("search", "lead")}
      <input type="search" id="q" placeholder="Search anime by English or Japanese name" enterkeyhint="search" aria-label="Search anime"></form>
    <div class="section-head"><h2>Browse by genre</h2>
      <div class="row"><button class="small" id="surprise">${icon("dice")}<span>Surprise me</span></button>
        ${shelfNav("genre-row").replace('class="shelf-nav"', 'class="shelf-nav always"')}</div></div>
    <div class="chips-row" id="genre-row">${'<span class="chip skel" style="width:84px">&nbsp;</span>'.repeat(8)}</div>
    <div class="section-head"><h2>Best this season</h2>${shelfNav("best")}</div>
    <p class="hint shelf-hint" id="best-sources"></p>
    <div class="shelf ranked" id="best">${skeletonPosters(6)}</div>
    <div class="section-head"><h2>Picked for you</h2>${shelfNav("foryou")}</div>
    <p class="hint shelf-hint" id="foryou-hint"></p>
    <div class="shelf" id="foryou">${skeletonPosters(6)}</div>
    <div class="section-head"><h2>Trending now</h2>${shelfNav("trending")}</div>
    <div class="shelf ranked" id="trending">${skeletonPosters(8)}</div>
    <div class="section-head"><h2>This season</h2></div>
    <div class="posters" id="seasonal">${skeletonPosters(12)}</div>`;
  $("#quick").addEventListener("submit", (e) => {
    e.preventDefault();
    const value = $("#q").value.trim();
    if (value) location.hash = `#/search?q=${encodeURIComponent(value)}`;
  });
  for (const b of $$("[data-shelf]")) {
    b.addEventListener("click", () => { const shelf = $(`#${b.dataset.for}`); shelf.scrollBy({ left: Number(b.dataset.shelf) * shelf.clientWidth * .8, behavior: "smooth" }); });
  }
  $("#surprise").addEventListener("click", (e) => surprise(e.currentTarget));
  genreList().then((genres) => {
    const row = $("#genre-row");
    if (!row) return;
    row.innerHTML = genres.map((g) => genreChip(g)).join("");
    scrollHints(row);
  });

  const seq = routeSeq;
  const trending = browseList("/api/browse/trending"), seasonal = browseList("/api/browse/seasonal");
  const best = browseList("/api/recommend/season"), forYou = browseList("/api/recommend/for-you");

  best.then(({ items, sources }) => {
    const shelf = $("#best");
    if (!shelf) return;
    if (!items.length) { shelf.outerHTML = `<p class="muted">Couldn't reach the rating sites right now.</p>`; return; }
    shelf.innerHTML = items.map((m, k) => posterCard(m, k, { rank: k + 1 })).join("");
    lazyImages(shelf, shelf);
    const used = Object.entries(sources).filter(([, ok]) => ok).map(([k]) => SOURCE_LABEL[k]);
    const down = Object.entries(sources).filter(([, ok]) => !ok).map(([k]) => SOURCE_LABEL[k]);
    $("#best-sources").textContent = `Ranked by fan ratings and what people are watching: ${used.join(", ")}`
      + (down.length ? ` (${down.join(", ")} unavailable right now)` : "");
  }).catch((e) => { const el = $("#best"); if (el) el.outerHTML = `<p class="muted">${esc(e.message)}</p>`; });

  forYou.then(({ items, based_on: b }) => {
    const shelf = $("#foryou");
    if (!shelf) return;
    if (!items.length) {
      $("#foryou-hint").textContent = b.shows
        ? "Nothing airing right now is close to your shows. Check back next season."
        : `${b.server ? `Watch a few shows in ${b.server}, or download` : "Download"} or follow a few, and AniStash will suggest similar ones that are airing now.`;
      shelf.remove();
      return;
    }
    $("#foryou-hint").textContent = b.watched
      ? `Airing now and close to what you watch in ${b.server}${b.account ? ` (as ${b.account})` : ""} and the shows you follow.`
      : `Airing now and close to the ${b.shows} show${b.shows === 1 ? "" : "s"} you have and follow.`;
    shelf.innerHTML = items.map((m, k) => posterCard(m, k)).join("");
    lazyImages(shelf, shelf);
  }).catch(() => { $("#foryou")?.remove(); });

  trending.then(({ items, error }) => {
    const shelf = $("#trending"), feature = $("#feature");
    if (!shelf) return;
    if (!items.length) { shelf.outerHTML = `<p class="muted">${esc(error || "Nothing to show.")}</p>`; feature.remove(); return; }
    featureCarousel(feature, items);
    shelf.innerHTML = items.slice(0, 10).map((m, k) => posterCard(m, k, { rank: k + 1 })).join("")
      + items.slice(10).map((m, k) => posterCard(m, k + 10)).join("");
    lazyImages(shelf, shelf);
    pauseOffscreen(feature);
  }).catch((e) => { const el = $("#trending"); if (el) el.outerHTML = `<p class="muted">${esc(e.message)}</p>`; });

  seasonal.then(({ items, error }) => {
    const el = $("#seasonal");
    if (!el) return;
    if (!items.length) { el.outerHTML = `<p class="muted">${esc(error || "Nothing to show.")}</p>`; return; }
    el.innerHTML = items.map((m, k) => posterCard(m, k)).join("");
    lazyImages(el);
  }).catch((e) => { const el = $("#seasonal"); if (el) el.outerHTML = `<p class="muted">${esc(e.message)}</p>`; });

  // One library check for both lists
  const lists = await Promise.all([best, trending, seasonal].map((p) => p.then((d) => d.items).catch(() => [])));
  const unique = [...new Map(lists.flat().map((m) => [m.id, m])).values()];
  if (seq === routeSeq) markLibrary(view, unique);
}

// Browse's lists are kept for 5 minutes, so going back to Browse is instant
const browseCache = new Map();
function browseList(path) {
  const hit = browseCache.get(path);
  if (hit && Date.now() - hit.at < 300000) return hit.promise;
  const promise = api(path);
  browseCache.set(path, { at: Date.now(), promise });
  promise.then((d) => { if (!d.items.length) browseCache.delete(path); }, () => browseCache.delete(path));
  return promise;
}

// A sideways row shows a fade (and enables its arrows) only on the side that has more to see
function scrollHints(row) {
  const buttons = $$(`[data-for="${row.id}"]`);
  const update = () => {
    const start = row.scrollLeft > 4;
    const end = row.scrollLeft + row.clientWidth < row.scrollWidth - 4;
    row.classList.toggle("more-before", start);
    row.classList.toggle("more-after", end);
    for (const b of buttons) b.disabled = b.dataset.shelf === "-1" ? !start : !end;
  };
  row.addEventListener("scroll", update, { passive: true });
  window.addEventListener("resize", update);
  update();
}

const SOURCE_LABEL = { anilist: "AniList", mal: "MyAnimeList", kitsu: "Kitsu", hianime: "hianime" };

const shelfNav = (id) => `<div class="shelf-nav"><button class="small icon" data-shelf="-1" data-for="${id}" aria-label="Scroll left">${icon("back")}</button><button class="small icon" data-shelf="1" data-for="${id}" aria-label="Scroll right">${icon("arrow")}</button></div>`;

// Genres, Surprise me, Discover and anime pages ----------------------------------------------

// "More like this" under a show's download page
async function moreLikeThis(id, name) {
  const seq = routeSeq;
  let a;
  try { a = await api(`/api/anime/${id}`); } catch { return; }
  const box = $("#similar-box");
  if (seq !== routeSeq || !box || !a.similar.length) return;
  box.innerHTML = `<div class="section-head" style="margin-top:1.5rem"><h2>More like this</h2>${shelfNav("similar")}</div>
    <p class="hint shelf-hint">Recommended by people who liked ${esc(name)}.</p>
    <div class="shelf" id="similar">${a.similar.map((m, k) => posterCard(m, k)).join("")}</div>`;
  lazyImages($("#similar"), $("#similar"));
  for (const b of $$("[data-shelf]", box)) {
    b.addEventListener("click", () => { const shelf = $("#similar"); shelf.scrollBy({ left: Number(b.dataset.shelf) * shelf.clientWidth * .8, behavior: "smooth" }); });
  }
  markLibrary(box, a.similar);
}

let genresPromise = null;
function genreList() {
  genresPromise ||= api("/api/genres").then((d) => d.genres).catch(() => { genresPromise = null; return []; });
  return genresPromise;
}

const genreChip = (g, on = false, tag = "a") => tag === "a"
  ? `<a class="chip" href="#/discover?genre=${encodeURIComponent(g)}">${esc(g)}</a>`
  : `<button type="button" class="chip ${on ? "on" : ""}" data-genre="${esc(g)}" aria-pressed="${on}">${esc(g)}</button>`;

// A random anime (of these genres, if any) opens on its own page
async function surprise(button, filters = {}) {
  const query = new URLSearchParams(Object.entries(filters).filter(([, v]) => v)).toString();
  try {
    const pick = await busy(button, () => api(`/api/random${query ? `?${query}` : ""}`));
    location.hash = `#/anime?id=${pick.id}&random=1${query ? `&${query}` : ""}`;
  } catch (err) { toast(err.message, true); }
}

const SORT_LABEL = { popular: "Popular", rated: "Top rated", trending: "Trending", newest: "Newest" };
const FORMAT_LABEL = { "": "Any type", tv: "TV series", movie: "Movies", ona: "ONA, OVA & specials" };
const STATUS_LABEL = { "": "Any status", airing: "Airing now", finished: "Finished", upcoming: "Coming soon" };

async function discoverView() {
  const p = params();
  const state = {
    genre: (p.genre || "").split(",").filter(Boolean), sort: SORT_LABEL[p.sort] ? p.sort : "popular",
    format: FORMAT_LABEL[p.format] ? p.format : "", status: STATUS_LABEL[p.status] ? p.status : "", year: p.year || "",
  };
  const thisYear = new Date().getFullYear();
  view.innerHTML = `
    <div class="section-head"><h1>Discover</h1>
      <button class="primary small" id="surprise">${icon("dice")}<span>Surprise me</span></button></div>
    <div class="section-head" style="margin:-.5rem 0 .6rem">
      <p class="hint" style="margin:0">Pick genres to narrow it down. Shows must have every genre you pick.</p>
      ${shelfNav("genres").replace('class="shelf-nav"', 'class="shelf-nav always"')}</div>
    <div class="chips-row" id="genres">${'<span class="chip skel" style="width:84px">&nbsp;</span>'.repeat(12)}</div>
    <div class="discover-filters">
      <div class="segmented" role="group" aria-label="Sort">${Object.entries(SORT_LABEL).map(([k, v]) =>
        `<button type="button" data-sort="${k}" class="${state.sort === k ? "on" : ""}">${v}</button>`).join("")}</div>
      <select id="f-format" aria-label="Type">${Object.entries(FORMAT_LABEL).map(([k, v]) => `<option value="${k}" ${state.format === k ? "selected" : ""}>${v}</option>`).join("")}</select>
      <select id="f-status" aria-label="Status">${Object.entries(STATUS_LABEL).map(([k, v]) => `<option value="${k}" ${state.status === k ? "selected" : ""}>${v}</option>`).join("")}</select>
      <select id="f-year" aria-label="Year"><option value="">Any year</option>${Array.from({ length: thisYear + 2 - 1970 }, (_, i) => thisYear + 1 - i)
        .map((y) => `<option value="${y}" ${String(y) === state.year ? "selected" : ""}>${y}</option>`).join("")}</select>
    </div>
    <div class="posters" id="results">${skeletonPosters(12)}</div>
    <div id="more"></div>`;

  const seq = routeSeq;
  let page = 0, loading = false, done = false, observer = null;
  const filters = () => ({ genre: state.genre.join(","), format: state.format, status: state.status });
  const load = async (fresh) => {
    if (loading || (done && !fresh)) return;
    loading = true;
    if (fresh) { page = 0; done = false; $("#results").innerHTML = skeletonPosters(12); }
    const query = new URLSearchParams({ ...filters(), sort: state.sort, year: state.year, page: page + 1 });
    for (const [k, v] of [...query]) if (!v) query.delete(k);
    try {
      const data = await api(`/api/discover?${query}`);
      if (seq !== routeSeq) return;
      const grid = $("#results");
      if (fresh || page === 0) grid.innerHTML = "";
      if (!data.items.length && page === 0) {
        grid.innerHTML = `<p class="muted">${esc(data.error || "Nothing matches. Try fewer genres or another year.")}</p>`;
      }
      const chunk = document.createElement("div");
      chunk.innerHTML = data.items.map((m, k) => posterCard(m, k)).join("");
      const cards = [...chunk.children];
      grid.append(...cards);
      lazyImages(grid);
      markLibrary(grid, data.items);
      page += 1;
      done = !data.has_next;
      $("#more").innerHTML = done ? "" : `<button class="block" id="load-more">Show more</button>`;
      $("#load-more")?.addEventListener("click", () => load(false));
      if (!done && "IntersectionObserver" in window) {
        observer?.disconnect();
        observer = new IntersectionObserver(([e]) => { if (e.isIntersecting) load(false); }, { rootMargin: "800px 0px" });
        observer.observe($("#more"));
      }
    } catch (e) {
      if ($("#results")) $("#results").innerHTML = `<div class="error-box">${esc(e.message)}</div>`;
    } finally { loading = false; }
  };
  // Filters change the results in place and the address (so Back and sharing work), without a page reload
  const apply = () => {
    const query = new URLSearchParams({ genre: state.genre.join(","), sort: state.sort === "popular" ? "" : state.sort,
      format: state.format, status: state.status, year: state.year });
    for (const [k, v] of [...query]) if (!v) query.delete(k);
    history.replaceState(null, "", `#/discover${query.toString() ? `?${query}` : ""}`);
    load(true);
  };

  genreList().then((genres) => {
    const row = $("#genres");
    if (!row) return;
    row.innerHTML = genres.map((g) => genreChip(g, state.genre.includes(g), "button")).join("");
    scrollHints(row);
    row.querySelector(".chip.on")?.scrollIntoView({ block: "nearest", inline: "center" });
    row.addEventListener("click", (e) => {
      const chip = e.target.closest("[data-genre]");
      if (!chip) return;
      const g = chip.dataset.genre;
      state.genre = state.genre.includes(g) ? state.genre.filter((x) => x !== g) : [...state.genre, g];
      chip.classList.toggle("on");
      chip.setAttribute("aria-pressed", chip.classList.contains("on"));
      apply();
    });
  });
  for (const b of $$("[data-sort]")) {
    b.addEventListener("click", () => {
      state.sort = b.dataset.sort;
      for (const x of $$("[data-sort]")) x.classList.toggle("on", x === b);
      apply();
    });
  }
  for (const [id, key] of [["#f-format", "format"], ["#f-status", "status"], ["#f-year", "year"]]) {
    $(id).addEventListener("change", (e) => { state[key] = e.target.value; apply(); });
  }
  $("#surprise").addEventListener("click", (e) => surprise(e.currentTarget, filters()));
  for (const b of $$("[data-shelf]")) {
    b.addEventListener("click", () => { const row = $(`#${b.dataset.for}`); row.scrollBy({ left: Number(b.dataset.shelf) * row.clientWidth * .8, behavior: "smooth" }); });
  }
  load(true);
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const STATUS_TEXT = { RELEASING: "Airing now", FINISHED: "Finished", NOT_YET_RELEASED: "Coming soon", HIATUS: "On hiatus", CANCELLED: "Cancelled" };

// One anime's page: what it is, where it fits, and more like it. "Find episodes" leads to downloading.
async function animeView() {
  const p = params();
  view.innerHTML = `
    <div class="show-hero"><div class="backdrop skel"></div>
      <div class="hero"><div class="art skel"></div>
        <div><div class="skel skel-line" style="height:2rem;width:70%"></div><div class="skel skel-line" style="width:40%"></div></div></div></div>`;
  let a;
  try { a = await api(`/api/anime/${encodeURIComponent(p.id)}`); }
  catch (e) {
    view.innerHTML = `<div class="error-box">${esc(e.message)}</div><a class="btn" href="#/discover">Back to Discover</a>`;
    return;
  }
  const searchName = a.english || a.romaji || a.title;
  const started = a.start?.[0] ? [a.start[1] && MONTHS[a.start[1] - 1], a.start[0]].filter(Boolean).join(" ") : "";
  const facts = [
    ["Type", a.format?.replace("_", " ")],
    ["Episodes", a.episodes ? `${a.episodes}${a.duration ? ` × ${a.duration} min` : ""}` : a.duration ? `${a.duration} min` : ""],
    ["Status", STATUS_TEXT[a.status] || ""],
    ["Started", started],
    ["Studio", a.studio],
    ["Next episode", a.next_episode ? `Episode ${a.next_episode.episode} ${relTime(a.next_episode.airingAt)}` : ""],
  ].filter(([, v]) => v);
  const random = p.random === "1";
  const again = { genre: p.genre || "", format: p.format || "", status: p.status || "" };
  view.innerHTML = `
    <div class="show-hero">
      <div class="backdrop ${a.banner ? "" : "blur"}" style="background-image:url(${esc(JSON.stringify(a.banner || a.cover || ""))})"></div>
      <button type="button" class="btn small back" id="back">${icon("back")}Back</button>
      <div class="hero">
        <div class="art" style="background-image:url(${esc(JSON.stringify(a.cover || ""))})"></div>
        <div class="hero-text">
          ${random ? `<span class="eyebrow">${icon("dice")} Surprise pick</span>` : ""}
          <h1>${esc(showTitle(a))}</h1>
          <div class="muted small">${esc([titleLang() === "ja" ? a.english : a.romaji, a.native].filter((x) => x && x !== showTitle(a)).join(" · "))}</div>
          <div class="row" style="margin:.5rem 0 .2rem;gap:.5rem">
            ${a.score ? `<span class="score-big" data-tip="Average fan score on AniList">${icon("star")}${(a.score / 10).toFixed(1)}</span>` : ""}
            ${a.popularity ? `<span class="muted small">${a.popularity.toLocaleString()} people follow it on AniList</span>` : ""}
          </div>
          <div class="genres">${(a.genres || []).map((g) => genreChip(g)).join("")}</div>
          <div id="inlib"></div>
        </div>
      </div>
    </div>
    <div class="row" style="margin-bottom:1rem">
      <a class="btn primary" id="find" href="#/search?q=${encodeURIComponent(searchName)}">${icon("download")}Find episodes</a>
      ${random ? `<button class="small" id="again">${icon("dice")}<span>Another surprise</span></button>` : ""}
    </div>
    ${a.description ? `<p class="synopsis clamp" id="syn">${esc(a.description.replace(/<[^>]+>/g, ""))}</p>` : ""}
    ${facts.length ? `<div class="facts">${facts.map(([k, v]) => `<div><span class="muted small">${k}</span><b>${esc(v)}</b></div>`).join("")}</div>` : ""}
    ${a.tags?.length ? `<div class="genres tags">${a.tags.map((t) => `<span class="pill">${esc(t)}</span>`).join("")}</div>` : ""}
    ${a.relations.length ? `<div class="section-head"><h2>Same story</h2>${shelfNav("related")}</div>
      <div class="shelf" id="related">${a.relations.map((m, k) => posterCard({ ...m, reason: m.relation }, k)).join("")}</div>` : ""}
    <div class="section-head"><h2>More like this</h2>${a.similar.length ? shelfNav("similar") : ""}</div>
    ${a.similar.length
      ? `<p class="hint shelf-hint">Recommended by people who liked ${esc(showTitle(a))}.</p>
         <div class="shelf" id="similar">${a.similar.map((m, k) => posterCard(m, k)).join("")}</div>`
      : `<p class="muted">No recommendations yet. <a href="#/discover?genre=${encodeURIComponent((a.genres || [])[0] || "")}">Browse ${esc((a.genres || [])[0] || "similar")} anime</a> instead.</p>`}`;

  $("#back").addEventListener("click", () => (history.length > 1 ? history.back() : (location.hash = "#/")));
  $("#syn")?.addEventListener("click", (e) => e.target.classList.toggle("clamp"));
  $("#again")?.addEventListener("click", (e) => surprise(e.currentTarget, again));
  for (const id of ["related", "similar"]) if ($(`#${id}`)) lazyImages($(`#${id}`), $(`#${id}`));
  for (const b of $$("[data-shelf]")) {
    b.addEventListener("click", () => { const shelf = $(`#${b.dataset.for}`); shelf.scrollBy({ left: Number(b.dataset.shelf) * shelf.clientWidth * .8, behavior: "smooth" }); });
  }
  markLibrary(view, [...a.relations, ...a.similar]);
  libraryMatch([{ id: "me", titles: [a.english, a.romaji, ...(a.names || [])] }]).then((matches) => {
    const m = matches.me;
    if (!m || !$("#inlib")) return;
    const { seasons: have, where } = librarySummary(m);
    $("#inlib").innerHTML = `<div class="libbox">${icon("check")}<div><b>In your library</b>${where ? ` <span class="muted">${esc(where)}</span>` : ""}
      ${have ? `<div class="small">${esc(have)}</div>` : ""}</div></div>`;
  });
}

// Search -------------------------------------------------------------------------------

async function searchView() {
  const q = params().q || "";
  const mode = wantMode();
  const dub = mode === "dub";  // the episode lists for "both" come from the sub
  view.innerHTML = `
    <form class="searchbar" id="search">${icon("search", "lead")}
      <input type="search" name="q" value="${esc(q)}" placeholder="English or Japanese name" enterkeyhint="search" aria-label="Search anime" ${q ? "" : "autofocus"}>
      <button class="primary" type="submit">Search</button>
    </form>
    <div class="row" style="margin-bottom:1rem">
      ${modeToggle(mode)}
      <span class="muted small" style="flex-basis:100%">${can.download() ? "Tick several results to queue them together, e.g. every season." : ""}</span>
    </div>
    <div id="results">${q ? `<div class="results">${Array.from({ length: 6 }, (_, k) => `<div class="result rise" style="--i:${k}"><div class="thumb skel" style="height:110px"></div><div class="body"><div class="skel skel-line" style="width:70%"></div><div class="skel skel-line" style="width:40%"></div></div></div>`).join("")}</div>` : ""}</div>`;
  $("#search").addEventListener("submit", (e) => {
    e.preventDefault();
    const value = e.target.q.value.trim();
    if (value) location.hash = `#/search?q=${encodeURIComponent(value)}${modeParam(mode)}`;
  });
  for (const b of $$("[data-mode]")) {
    b.addEventListener("click", () => { location.hash = `#/search?q=${encodeURIComponent(q)}${modeParam(b.dataset.mode)}`; });
  }
  if (!q) return;

  let results, alsoSearched = [];
  try {
    ({ results, also_searched: alsoSearched = [] } = await api(`/api/search?q=${encodeURIComponent(q)}&dub=${dub}`));
  } catch (e) {
    $("#results").innerHTML = `<div class="error-box">${esc(e.message)}</div>`;
    return;
  }
  if (!results.length) { $("#results").innerHTML = `<p class="muted">No results for “${esc(q)}”.</p>`; return; }

  const selected = new Set();
  $("#results").innerHTML = (alsoSearched.length ? `<p class="muted small">Also searched for ${alsoSearched.map((x) => `“${esc(x)}”`).join(" and ")}, its other name${alsoSearched.length > 1 ? "s" : ""}.</p>` : "")
    + `<div class="results">${results.map((r, k) => `
    <div class="result rise" style="--i:${k}" data-key="${r.key}">
      <div class="thumb" data-title="${esc(r.title)}"></div>
      <a class="body" href="#/show?q=${encodeURIComponent(r.query || q)}&i=${r.index}&t=${encodeURIComponent(r.title)}${modeParam(mode)}">
        <span class="title" data-name>${esc(r.title)}</span>
        <span class="meta" data-alt></span>
        <span class="meta" data-meta>${r.title_known ? "" : "Only result"}</span>
        <span data-lib></span>
      </a>
      ${can.download() ? `<label class="pick" aria-label="Select ${esc(r.title)}"><input type="checkbox" value="${r.key}"></label>` : ""}
    </div>`).join("")}</div>`;

  libraryMatch(results.map((r) => ({ id: r.key, titles: [r.title] }))).then((matches) => {
    for (const [key, m] of Object.entries(matches)) {
      const slot = $(`.result[data-key="${key}"] [data-lib]`);
      if (slot) slot.outerHTML = libraryBadge(m);
    }
  });

  for (const thumb of $$(".thumb[data-title]")) {
    fetchMeta(thumb.dataset.title).then((m) => {
      if (!m) return;
      thumb.style.backgroundImage = `url(${JSON.stringify(m.thumb || m.cover)})`;
      const row = thumb.parentElement;
      // Show the title in the chosen language, with the other one underneath
      const site = thumb.dataset.title;
      const preferred = showTitle(m);
      const other = titleLang() === "ja" ? (m.english || site) : m.romaji;
      if (preferred && preferred !== site && titleLang() === "ja") row.querySelector("[data-name]").textContent = preferred;
      const shown = row.querySelector("[data-name]").textContent;
      if (other && other.toLowerCase() !== shown.toLowerCase()) row.querySelector("[data-alt]").textContent = other;
      row.querySelector("[data-meta]").textContent = [m.format?.replace("_", " "), m.year, m.episodes && `${m.episodes} eps`, m.score && `${m.score}%`].filter(Boolean).join(" · ");
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
    for (const el of $$(".result")) el.classList.toggle("selected", selected.has(el.dataset.key));
  };
  for (const box of $$(".pick input")) {
    box.addEventListener("change", () => { box.checked ? selected.add(box.value) : selected.delete(box.value); update(); });
  }
  bar.querySelector("#sel-clear").addEventListener("click", () => { selected.clear(); $$(".pick input").forEach((b) => (b.checked = false)); update(); });
  bar.querySelector("#sel-queue").addEventListener("click", () => {
    const picked = results.filter((r) => selected.has(r.key)).map((r) => ({ query: r.query || q, index: r.index, title: r.title }));
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
      prefs || api("/api/settings"),
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
  view.innerHTML = `
    <div class="show-hero"><div class="backdrop skel"></div>
      <div class="hero"><div class="art skel"></div>
        <div><div class="skel skel-line" style="height:2rem;width:70%"></div><div class="skel skel-line" style="width:40%"></div>
          <p class="muted small" style="margin-top:1rem">Finding seasons and episodes… this can take a few seconds.</p></div></div></div>
    <div class="tabs">${'<div class="tab skel" style="width:120px"></div>'.repeat(3)}</div>`;
  let data, locs, settings;
  try {
    [data, locs, settings] = await Promise.all([
      api(`/api/show?q=${encodeURIComponent(p.q)}&i=${p.i}&t=${encodeURIComponent(p.t)}&dub=${dub}`),
      api("/api/locations"),
      prefs || api("/api/settings"),
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

  const heading = meta ? showTitle(meta) || data.series_name : data.series_name;
  view.innerHTML = `
    <div class="show-hero">
      <div class="backdrop ${meta?.banner ? "" : "blur"}" style="background-image:url('${esc(meta?.banner || meta?.cover || "")}')"></div>
      <a href="#/search?q=${encodeURIComponent(p.q)}${modeParam(mode)}" class="btn small back">${icon("back")}Results</a>
    <div class="hero">
      <div class="art" style="background-image:url('${esc(meta?.cover || "")}')"></div>
      <div class="hero-text">
        <h1>${esc(heading)}</h1>
        <div class="muted small">${esc([meta?.format?.replace("_", " "), meta?.year, meta?.status?.toLowerCase().replace("_", " "), meta?.score && `${meta.score}%`].filter(Boolean).join(" · "))}</div>
        <div class="genres">${(meta?.genres || []).slice(0, 5).map((g) => `<span class="pill">${esc(g)}</span>`).join("")}</div>
        <div id="inlib"></div>
        ${meta?.description ? `<p class="synopsis clamp" id="syn">${esc(meta.description.replace(/<[^>]+>/g, ""))}</p>` : ""}
      </div>
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
      <div class="row">${can.download() ? `<button class="small" id="follow">${icon("star")}<span>Follow</span></button>` : ""}<button class="small" id="all-seasons">Select all seasons</button></div></div>
    <div class="tabs" id="tabs" role="tablist"></div>
    <div id="season"></div>
    <div id="similar-box"></div>
    <div class="pad-actionbar"></div>`;
  if (meta?.id) moreLikeThis(meta.id, heading);

  $("#syn")?.addEventListener("click", (e) => e.target.classList.toggle("clamp"));
  for (const b of $$("[data-mode]")) {
    b.addEventListener("click", () => { location.hash = `#/show?q=${encodeURIComponent(p.q)}&i=${p.i}&t=${encodeURIComponent(p.t)}${modeParam(b.dataset.mode)}`; });
  }
  libraryMatch([{ id: "series", titles: [data.series_name, p.t, ...(meta?.names || []), ...seasons.map((x) => x.title)] }]).then((matches) => {
    const m = matches.series;
    if (!m || !$("#inlib")) return;
    const { seasons: have, where } = librarySummary(m);
    $("#inlib").innerHTML = `<div class="libbox">${icon("check")}<div><b>In your library</b>${where ? ` <span class="muted">${esc(where)}</span>` : ""}
      ${have ? `<div class="small">${esc(have)}</div>` : ""}</div></div>`;
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
      followBtn.innerHTML = `${icon("star", "fill")}<span>Following</span>`;
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
    <div id="queue" data-view="${tab}">${loading()}</div>`;
  for (const b of $$("[data-tabto]")) b.onclick = () => { location.hash = `#/downloads${b.dataset.tabto === "following" ? "?tab=following" : ""}`; };
  const seq = routeSeq;
  if (tab === "following") {
    await renderFollowing();
    if (seq === routeSeq) pagePoll = poller(renderFollowing, 15000);
    return;
  }
  $("#clear")?.addEventListener("click", async () => {
    const r = await api("/api/queue/clear-finished", { method: "POST" });
    toast(`Removed ${r.removed} finished ${r.removed === 1 ? "batch" : "batches"}`);
    renderQueue();
  });
  queueState = null;
  bindQueueActions($("#queue"));
  await renderQueue();
  if (seq === routeSeq) pagePoll = poller(renderQueue, 2000);  // unless we've already left the page
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
    el.innerHTML = `<div class="card muted">You're not following anything yet. Open a show and tap <b>Follow</b> to keep it up to date automatically.</div>`;
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

let queueState = null;  // the last queue answer, with its revision

// Fetches the queue (an unchanged queue answers with just its revision) and patches the page
async function renderQueue() {
  let data;
  const since = queueState ? `?since=${encodeURIComponent(queueState.rev)}` : "";
  try { data = await api(`/api/queue${since}`); }
  catch (e) { if ($("#queue")) $("#queue").innerHTML = `<div class="error-box">${esc(e.message)}</div>`; return; }
  if (!$("#queue")) return;
  if (!data.unchanged) {
    queueState = data;
    setBadge(data.counts);
  }
  drawQueue();
}

function drawQueue() {
  const el = $("#queue");
  if (!el || !queueState) return;
  const { batches, me, admin: isAdmin } = queueState;
  const next = document.createElement("div");
  next.innerHTML = !batches.length
    ? `<div class="card muted">Nothing queued. Find something under <a href="#/">Browse</a> or <a href="#/search">Search</a>.</div>`
    : batches.map((b) => batchHTML(b, isAdmin || (can.download() && b.created_by === me))).join("");
  applyTips(next);  // so patching keeps the tips already on the page
  morphChildren(el, next);
}

function batchHTML(b, mine) {
  const total = b.items.length;
  const progress = b.items.reduce((n, i) => n + (i.status === "done" ? 100 : i.status === "running" ? i.progress : 0), 0) / Math.max(total, 1);
  const open = expanded.has(b.id) || b.counts.running > 0 && !expanded.has(-b.id);
  const state = b.counts.running ? `<span class="pill accent">Downloading</span>`
    : b.counts.queued ? `<span class="pill">Queued</span>`
    : b.counts.failed ? `<span class="pill bad">${b.counts.failed} failed</span>`
    : `<span class="pill ok">Done</span>`;
  return `<div class="batch" data-batch="${b.id}" data-key="b${b.id}">
    <div class="batch-head" data-toggle="${b.id}" aria-expanded="${open}">
      <div class="grow">
        <b>${esc(b.series_name)} · S${String(b.season).padStart(2, "0")}</b>
        <div class="muted small">${esc(b.title)}</div>
        <div class="muted small">${b.counts.done}/${total} episodes · ${esc(MODE_LABEL[b.mode] || b.mode)} · ${esc(b.location || "?")}${b.owner ? ` · by ${esc(b.owner)}` : ""}${b.follow_id ? ` · <span class="pill accent">auto</span>` : ""}</div>
        <div class="progress ${b.active ? "" : "done"}" style="margin-top:.4rem"><i style="width:${progress.toFixed(1)}%"></i></div>
      </div>
      ${state}
    </div>
    ${b.scanned_at && !b.warning ? `<div class="muted small" style="margin:-6px 14px 12px" data-tip="Your media server was asked to look for these episodes. It can take a minute or two for them to appear">✓ Sent to ${esc(b.scanned_with)} · ${relTime(b.scanned_at)}</div>` : ""}
    ${b.warning ? `<div class="warn-box" style="margin:0 14px 12px">${esc(b.warning)}
      ${mine && /scan failed/.test(b.warning) ? `<div style="margin-top:.5rem"><button class="small" data-b="rescan">Scan now</button></div>` : ""}</div>` : ""}
    ${open ? `
      ${mine ? `<div class="batch-tools">
        <button class="small icon" data-b="up" aria-label="Move up">${icon("up")}</button>
        <button class="small icon" data-b="down" aria-label="Move down">${icon("down")}</button>
        ${b.counts.failed + b.counts.cancelled ? `<button class="small" data-b="retry">Retry failed</button>` : ""}
        ${b.active ? `<button class="small danger" data-b="cancel">Cancel</button>` : `<button class="small" data-b="remove">Remove</button>`}
      </div>` : ""}
      <div class="items">${b.items.map((i) => `
        <div class="item" data-item="${i.id}" data-key="i${i.id}">
          <span class="epno">E${esc(i.episode)}${b.offset && /^\d+$/.test(i.episode) ? `<div class="muted small">→ E${Number(i.episode) + b.offset}</div>` : ""}</span>
          <div style="min-width:0">
            <span class="pill ${STATUS_PILL[i.status]}">${esc(i.status)}</span>${i.quality ? ` <span class="pill">${esc(i.quality)}</span>` : ""}
            ${i.status === "running" ? `<div class="progress ${i.progress ? "live" : "indeterminate"}" style="margin-top:.35rem"><i style="width:${i.progress}%"></i></div>` : ""}
            ${i.detail ? `<div class="detail">${esc(i.detail)}</div>` : ""}
            ${i.error ? `<div class="err">${esc(i.error)}</div>` : ""}
          </div>
          <div class="row" ${mine ? "" : "hidden"}>
            ${i.status === "queued" ? `<button class="small icon" data-i="up" aria-label="Move up">${icon("up")}</button><button class="small icon" data-i="down" aria-label="Move down">${icon("down")}</button>` : ""}
            ${["failed", "cancelled"].includes(i.status) ? `<button class="small" data-i="retry">Retry</button>` : ""}
            ${["queued", "running"].includes(i.status) ? `<button class="small icon danger" data-i="cancel" aria-label="Cancel">${icon("x")}</button>` : ""}
          </div>
        </div>`).join("")}</div>` : ""}
  </div>`;
}

// One set of click handlers for the whole list, so patching rows never needs to rebind them
function bindQueueActions(el) {
  el.addEventListener("click", async (e) => {
    const head = e.target.closest("[data-toggle]");
    const b = e.target.closest("[data-b]"), i = e.target.closest("[data-i]");
    if (head && !b && !i) {
      const id = Number(head.dataset.toggle);
      const open = head.getAttribute("aria-expanded") === "true";
      expanded.delete(id); expanded.delete(-id);
      expanded.add(open ? -id : id);
      return drawQueue();
    }
    try {
      if (b) {
        const id = b.closest("[data-batch]").dataset.batch;
        if (b.dataset.b === "remove") await api(`/api/batches/${id}`, { method: "DELETE" });
        else if (b.dataset.b === "rescan") {
          await busy(b, () => api(`/api/batches/${id}/rescan`, { method: "POST" }));
          toast("Asked your media server to scan the new episodes");
        } else await api(`/api/batches/${id}/${b.dataset.b}`, { method: "POST" });
      } else if (i) {
        await api(`/api/items/${i.closest("[data-item]").dataset.item}/${i.dataset.i}`, { method: "POST" });
      } else return;
      renderQueue();
    } catch (err) { toast(err.message, true); }
  });
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
        <label class="field"><span>NAS address</span><input type="text" name="server" value="${esc(type === "smb" ? c.server || "" : "")}" placeholder="192.168.1.10" autocapitalize="off" spellcheck="false" inputmode="url"></label>
        <div class="grid2">
          <label class="field"><span>Username</span><input type="text" name="username" value="${esc(c.username || "")}" autocapitalize="off" autocomplete="off" placeholder="blank for guest"></label>
          <label class="field"><span>Password</span><input type="password" name="password" autocomplete="new-password" placeholder="${loc ? "Leave blank to keep" : ""}"></label>
        </div>
        <button type="button" class="block" data-connect>${loc ? "Connect & browse" : "Connect & find anime folder"}</button>
        <div class="hint" data-preview></div>
        <div class="browser" data-browser></div>
        <details class="more"><summary>Type the share address yourself, or more options</summary>
          <label class="field" style="margin-top:.75rem"><span>Share address</span>
            <input type="text" name="address" value="${esc(locationAddress("smb", type === "smb" ? c : {}))}" placeholder="//192.168.1.10/media/anime" autocapitalize="off" spellcheck="false">
            <div class="hint">Server, share, then any folder inside it. Missing folders are created.</div></label>
          <div class="grid2">
            <label class="field"><span>Domain</span><input type="text" name="domain" value="${esc(c.domain || "")}" autocapitalize="off"></label>
            <label class="field"><span>SMB version</span><select name="vers">${["default", "3.1.1", "3.0", "2.1", "2.0", "1.0"].map((v) => `<option ${v === (c.vers || "default") ? "selected" : ""}>${v}</option>`).join("")}</select></label>
          </div>
        </details>
      </div>
      <div data-fields="nfs" ${type === "nfs" ? "" : "hidden"}>
        <label class="field"><span>Export address</span>
          <input type="text" name="nfs_address" value="${esc(locationAddress("nfs", type === "nfs" ? c : {}))}" placeholder="192.168.1.10:/mnt/pool/media/anime" autocapitalize="off" spellcheck="false">
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
      ? `<div class="chosen">${icon("check")}<span>Saving to <b>${esc(p.share)}</b>${p.subpath ? ` › <b>${esc(p.subpath.split("/").join(" › "))}</b>` : ""} <span class="muted">on ${esc(p.server)}</span></span></div>`
      : `Server <b>${esc(p.server)}</b>. Add the share name.`;
    else out.innerHTML = `<div class="chosen">${icon("check")}<span>NFS server <b>${esc(p.server)}</b> · path <b>${esc(p.export)}</b></span></div>`;
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
        `<button type="button" class="pickrow" data-sugg="${k}">${icon("folder")}${esc([s.share, ...s.path].join(" › "))}</button>`).join("")}` : ""}
      <button type="button" class="small ghost" data-change>Pick a different folder</button>`;
    for (const b of $$("[data-sugg]", browser)) b.onclick = () => { const s = others[Number(b.dataset.sugg)]; choose(s.share, s.path); };
    $("[data-change]", browser).onclick = () => openFolder(share, path.slice(0, -1));
  };
  const renderShares = () => {
    browser.innerHTML = `
      ${smb.suggestions.length ? `<div class="hint" style="margin-top:.5rem">Anime folders found:</div>${smb.suggestions.map((s, k) =>
        `<button type="button" class="pickrow" data-sugg="${k}">${icon("folder")}${esc([s.share, ...s.path].join(" › "))}</button>`).join("")}` : ""}
      <div class="hint" style="margin-top:.5rem">${smb.suggestions.length ? "Or browse a share:" : "No folder called anime was found. Pick a share to browse:"}</div>
      ${smb.shares.map((sh, k) => `<button type="button" class="pickrow" data-share="${k}">${icon("drive")}${esc(sh.name)}${sh.comment ? ` <span class="muted">· ${esc(sh.comment)}</span>` : ""}</button>`).join("")}`;
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
      ${folders.map((f) => `<button type="button" class="pickrow" data-open="${esc(f)}">${icon("folder")}${esc(f)}</button>`).join("") || `<div class="hint">No folders here.</div>`}
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
  prefs = await api("/api/settings", { method: "PUT", body: { media_refresh: d.media_refresh } });
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

function accountSection() {
  const role = status.user?.role || "";
  return `
    <form class="card" id="pw"><h2>Password</h2>
      <p class="muted">Signed in as <b>${esc(status.username)}</b> · ${esc(role)}</p>
      <div class="grid2">
        <label class="field"><span>Current password</span><input type="password" name="current" autocomplete="current-password" required></label>
        <label class="field"><span>New password</span><input type="password" name="new" autocomplete="new-password" minlength="10" required>
          <div class="hint">At least 10 characters.</div></label>
      </div>
      <div class="row end"><button class="primary" type="submit">Change password</button></div>
    </form>
    <div id="media-link"></div>
    <div class="card"><h2>Signed-in devices</h2>
      <div id="sessions" class="muted small">${loading()}</div>
      <div class="row" style="margin-top:.75rem"><button type="button" id="logout" class="ghost">Log out</button>
        <button type="button" id="logout-all" class="danger small">Log out on all devices</button></div>
    </div>`;
}

function appearanceSection() {
  const custom = theme.accent && !ACCENTS.some(([hex]) => hex === theme.accent);
  return `
    <div class="card" id="appearance">
      <p class="muted small" style="margin-top:0">Only you see these. They follow you to every device you sign in on.</p>
      <h3 style="margin-top:.6rem">Light or dark</h3>
      <div class="segmented" role="group" aria-label="Light or dark">
        ${[["system", "Match device"], ["light", "Light"], ["dark", "Dark"]].map(([k, v]) =>
          `<button type="button" data-theme-mode="${k}" class="${theme.mode === k ? "on" : ""}">${v}</button>`).join("")}
      </div>
      <p class="hint">The ${icon("moon")} button at the top switches too.</p>
      <h3>Colour</h3>
      <div class="swatches" role="group" aria-label="Accent colour">
        ${ACCENTS.map(([hex, name, combo, partners]) => `<button type="button" class="swatch ${theme.accent === hex ? "on" : ""}" data-accent="${hex}"
            style="--sw:${hex || "#dd4027"}" aria-pressed="${theme.accent === hex}"
            data-tip="${esc(`${name}${hex ? "" : " (AniStash's own colour)"}. In combination No. ${combo} it's paired with ${partners.map((x) => x[1]).join(" and ")}`)}">
            <span>${partners.map(([c]) => `<i style="background:${c}"></i>`).join("")}</span><b>${name}</b></button>`).join("")}
        <label class="swatch custom ${custom ? "on" : ""}" style="--sw:${custom ? theme.accent : "#888"}">
          <span>${icon("plus")}</span><b>Your own</b>
          <input type="color" id="accent-picker" value="${custom ? theme.accent : "#e2401f"}" aria-label="Pick any colour"></label>
      </div>
      <p class="hint">The colours come from Sanzo Wada's <i>A Dictionary of Color Combinations</i> (1933); the small dots show what he paired each
        one with. Very light or very dark colours are adjusted a little so text stays readable.</p>
      <div class="theme-preview" aria-hidden="true">
        <span class="btn primary small">${icon("download")}Download</span>
        <span class="chip on">Fantasy</span><span class="chip">Drama</span>
        <span class="pill accent">Downloading</span>
        <div class="progress" style="flex:1 1 120px"><i style="width:62%"></i></div>
      </div>
      <h3>Show titles in</h3>
      <div class="segmented" role="group" aria-label="Title language">
        <button type="button" data-setlang="en" class="${titleLang() === "en" ? "on" : ""}">English</button>
        <button type="button" data-setlang="ja" class="${titleLang() === "ja" ? "on" : ""}">Japanese (romaji)</button>
      </div>
      <p class="hint">Search finds shows by either name, whichever you pick.</p>
    </div>`;
}

function bindAppearance() {
  const pane = $("#appearance");
  const mark = () => {
    for (const b of $$("[data-theme-mode]", pane)) b.classList.toggle("on", b.dataset.themeMode === theme.mode);
    const custom = theme.accent && !ACCENTS.some(([hex]) => hex === theme.accent);
    for (const b of $$("[data-accent]", pane)) {
      b.classList.toggle("on", b.dataset.accent === theme.accent);
      b.setAttribute("aria-pressed", b.dataset.accent === theme.accent);
    }
    const own = $(".swatch.custom", pane);
    own.classList.toggle("on", Boolean(custom));
    if (custom) own.style.setProperty("--sw", theme.accent);
  };
  pane.addEventListener("click", (e) => {
    const mode = e.target.closest("[data-theme-mode]"), sw = e.target.closest("[data-accent]");
    if (mode) saveTheme({ mode: mode.dataset.themeMode }).then(mark);
    else if (sw) saveTheme({ accent: sw.dataset.accent }).then(mark);
  });
  const picker = $("#accent-picker", pane);
  picker.addEventListener("input", () => { applyTheme({ accent: picker.value }); mark(); });  // live while dragging
  picker.addEventListener("change", () => saveTheme({ accent: picker.value.toLowerCase() }).then(mark));
  for (const b of $$("[data-setlang]", pane)) b.addEventListener("click", () => setTitleLang(b.dataset.setlang));
}

function helpSection() {
  return `
    <div class="card"><h2>Getting around</h2>
      <p class="muted small">Not sure what a button does? Hover over it, or press and hold it on a phone.</p>
      <div class="row"><button type="button" class="small" id="replay-tour">Show the tour again</button>
        <button type="button" class="small" id="show-changes">What's new</button></div>
    </div>
    <div class="card"><h2>About AniStash</h2>
      <p>Version <b>${esc(status.version)}</b> · free and open source under the GPL-3.0 license.</p>
      <p class="muted small">AniStash was made with Claude Code, Anthropic's AI coding assistant, directed and tested by its maintainer.
        Source, documentation and issues: <a href="https://github.com/JustinMarxGit/anistash" target="_blank" rel="noopener">github.com/JustinMarxGit/anistash</a>.</p>
    </div>`;
}

const SERVER_LABEL = { jellyfin: "Jellyfin", plex: "Plex" };

// Options for linking a person to their Jellyfin/Plex account (only the server that's set up)
function mediaAccountOptions(accounts, selected) {
  return `<option value="" ${selected === "" ? "selected" : ""}>Match by username automatically</option>
    ${accounts.map((a) => `<option value="${esc(a.id)}" ${a.id === selected ? "selected" : ""}>${esc(a.name)}</option>`).join("")}
    <option value="-" ${selected === "-" ? "selected" : ""}>Don't use watch history</option>`;
}

async function mediaLinkCard(el) {
  let data;
  try { data = await api("/api/mediaserver/accounts"); } catch { return; }
  if (data.kind === "none") return;
  const label = SERVER_LABEL[data.kind];
  const selected = data.off ? "-" : data.linked?.id || "";
  el.innerHTML = `<div class="card"><h2>Your ${label} account</h2>
    <label class="field"><span>Picked for you learns from what this account watches</span>
      <select id="media-user">${mediaAccountOptions(data.accounts, selected)}</select></label>
    ${data.error ? `<p class="hint">Couldn't reach ${label}: ${esc(data.error)}</p>` : ""}</div>`;
  $("#media-user", el).addEventListener("change", async (e) => {
    try {
      await api("/api/account/media-user", { method: "PUT", body: { account_id: e.target.value } });
      browseCache.delete("/api/recommend/for-you");
      toast(e.target.value === "-" ? "Picked for you won't use your watch history"
        : `Picked for you now learns from what you watch in ${label}`);
    } catch (err) { toast(err.message, true); }
  });
}

function bindAccount() {
  mediaLinkCard($("#media-link"));
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

function bindHelp() {
  $("#replay-tour").addEventListener("click", () => { location.hash = "#/"; setTimeout(startTour, 300); });
  $("#show-changes").addEventListener("click", () => showWhatsNew({ all: true }));
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
  let data, media;
  try { [data, media] = await Promise.all([api("/api/users"), api("/api/mediaserver/accounts").catch(() => ({ kind: "none" }))]); }
  catch (e) { container.innerHTML = `<div class="error-box">${esc(e.message)}</div>`; return; }
  const linking = media.kind !== "none" && media.accounts.length;
  const when = (t) => t ? new Date(t * 1000).toLocaleDateString() : "never";
  container.innerHTML = `
    ${data.users.map((u) => `
      <div class="loc" data-user="${u.id}">
        <div style="min-width:0">
          <b>${esc(u.username)}</b> <span class="pill ${u.role === "admin" ? "accent" : ""}">${esc(u.role)}</span>
          ${u.disabled ? `<span class="pill bad">disabled</span>` : ""}${u.must_change_password ? ` <span class="pill warn">must change password</span>` : ""}
          ${u.id === status.user.id ? `<span class="pill">you</span>` : ""}
          <div class="muted small">Last login ${esc(when(u.last_login_at))}</div>
          ${linking ? `<label class="small muted" style="display:flex;gap:.4rem;align-items:center;margin-top:.35rem">${SERVER_LABEL[media.kind]}
            <select class="small" data-media aria-label="${SERVER_LABEL[media.kind]} account for ${esc(u.username)}" style="min-height:32px;width:auto">
              ${mediaAccountOptions(media.accounts, u.media_user_id)}</select></label>` : ""}
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
  for (const sel of $$("[data-media]", container)) {
    sel.addEventListener("change", async () => {
      const id = sel.closest("[data-user]").dataset.user;
      try {
        await api(`/api/users/${id}/media-user`, { method: "PUT", body: { account_id: sel.value } });
        toast("Saved");
      } catch (err) { toast(err.message, true); usersPanel(container); }
    });
  }
}

const AUDIT_LABELS = {
  login: "Logged in", login_failed: "Failed login", login_locked: "Login blocked (too many tries)", logout: "Logged out",
  logout_everywhere: "Logged out everywhere", password_changed: "Changed password", password_change_failed: "Wrong current password",
  password_reset: "Reset password", user_created: "Added user", user_changed: "Changed user", user_deleted: "Deleted user",
  settings_changed: "Changed settings", media_server_changed: "Changed media server", location_added: "Added location",
  location_changed: "Changed location", location_removed: "Removed location", queued: "Queued download",
  update_started: "Started update", admin_set: "Set admin (console)",
  follow_added: "Followed", follow_removed: "Unfollowed", media_account_linked: "Linked media server account",
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

// Settings: a list of sections instead of one long page. On a phone the list comes first and a
// section opens on its own (with a way back); on a wide screen the list stays beside it.
const SETTINGS_SECTIONS = [
  { id: "updates", icon: "spark", title: "Updates", desc: "Check for and install new versions", admin: true },
  { id: "appearance", icon: "palette", title: "Appearance", desc: "Light or dark, colours, titles" },
  { id: "downloads", icon: "download", title: "Downloads", desc: "Sub or dub, quality, naming", admin: true },
  { id: "account", icon: "user", title: "Your account", desc: "Password, devices, watch history" },
  { id: "storage", icon: "drive", title: "Storage", desc: "Where downloads are saved", admin: true },
  { id: "media", icon: "tv", title: "Media server", desc: "Jellyfin or Plex", admin: true },
  { id: "users", icon: "users", title: "Users", desc: "Who can use AniStash", admin: true },
  { id: "ratings", icon: "star", title: "Ratings", desc: "MyAnimeList Client ID", admin: true },
  { id: "tools", icon: "settings", title: "Tools & logs", desc: "ani-cli, security log, setup", admin: true },
  { id: "help", icon: "help", title: "Help & about", desc: "Tour, what's new, version" },
];

async function settingsView() {
  const sections = SETTINGS_SECTIONS.filter((x) => !x.admin || can.admin());
  const wanted = params().s;
  const current = sections.find((x) => x.id === wanted) || null;
  const shown = current || sections[0];  // wide screens always show one
  view.innerHTML = `
    <div class="settings ${current ? "has-section" : ""}">
      <nav class="settings-nav" id="settings-nav" aria-label="Settings sections">
        <h1>Settings</h1>
        ${sections.map((x) => `<a class="sn-item ${x.id === shown.id ? "on" : ""}" href="#/settings?s=${x.id}" data-s="${x.id}">
          <span class="sn-icon">${icon(x.icon)}</span>
          <span class="grow"><b>${x.title}</b><small data-desc="${x.id}">${x.desc}</small></span>
          ${icon("arrow", "sn-chevron")}</a>`).join("")}
      </nav>
      <section class="settings-pane" id="settings-pane" aria-live="polite">
        <a class="btn small ghost sn-back" href="#/settings">${icon("back")}Settings</a>
        <h1 class="sn-title">${shown.title}</h1>
        <div id="section">${loading()}</div>
      </section>
    </div>`;
  if (can.admin()) {
    api("/api/system/update-status").then((u) => {
      if (!u.update_available) return;
      const item = $('[data-s="updates"]');
      item?.querySelector("b").insertAdjacentHTML("beforeend", ` <span class="badge-dot">${esc(u.latest)}</span>`);
      if (item) item.querySelector("[data-desc]").textContent = `Version ${u.latest} is ready to install`;
    }).catch(() => {});
  }
  const seq = routeSeq;
  try {
    await SETTINGS_RENDER[shown.id]($("#section"));
  } catch (err) {
    if (seq === routeSeq) $("#section").innerHTML = `<div class="error-box">${esc(err.message)}</div>`;
  }
}

const SETTINGS_RENDER = {
  async updates(el) {
    const [s, sys] = await Promise.all([api("/api/settings"), api("/api/system")]);
    const updateState = sys.update_available
      ? `Version ${esc(sys.latest)} is ready to install.`
      : sys.latest ? "You're on the newest version." : "Couldn't reach GitHub to check for a new version.";
    el.innerHTML = `
      <div class="card update-card ${sys.update_available ? "has-update" : ""}" id="updates">
        <div class="update-head">
          <div class="grow">
            <h2>AniStash v${esc(status.version)}</h2>
            <p class="muted" style="margin:0">${updateState}</p>
          </div>
          <div class="row">
            ${sys.update_available ? `<button class="primary" id="update-app" ${sys.helper ? "" : "disabled"}>${icon("download")}Update to ${esc(sys.latest)}</button>` : ""}
            <button class="${sys.update_available ? "" : "primary"}" id="check-updates">${icon("retry")}Check for updates</button>
          </div>
        </div>
        ${sys.update_available ? `<a class="small" href="https://github.com/JustinMarxGit/anistash/releases" target="_blank" rel="noopener">What's new in ${esc(sys.latest)}</a>` : ""}
        <label class="check" style="margin-top:.75rem"><input type="checkbox" id="auto-update" ${s.auto_update ? "checked" : ""} ${sys.helper ? "" : "disabled"}>
          Install updates automatically</label>
        <p class="hint" style="margin-top:0">Checks every 30 minutes and installs a new version while nothing is downloading. If an update fails, the previous version is put back. When this is off, admins see a banner instead.</p>
      </div>`;
    $("#update-app")?.addEventListener("click", () => runUpdate(sys.latest));
    $("#check-updates").addEventListener("click", async (e) => {
      try {
        const info = await busy(e.currentTarget, () => api("/api/system/update-status?fresh=1"));
        if (!info.update_available) return toast(`You're up to date (v${info.current})`);
        toast(`AniStash ${info.latest} is available`);
        route.bannerChecked = false;
        route();  // shows the Update button
      } catch (err) { toast(err.message, true); }
    });
    $("#auto-update").addEventListener("change", async (e) => {
      try {
        prefs = await api("/api/settings", { method: "PUT", body: { auto_update: e.target.checked } });
        toast(e.target.checked ? "Updates will install automatically" : "Automatic updates are off. You'll see a banner when one is available");
        refreshUpdateBanner();
      } catch (err) { toast(err.message, true); e.target.checked = !e.target.checked; }
    });
  },

  appearance(el) {
    el.innerHTML = appearanceSection();
    bindAppearance();
  },

  async downloads(el) {
    const s = await api("/api/settings");
    el.innerHTML = `<form class="card" id="defaults"><h2>Download defaults</h2>
      <p class="muted small">What show pages start with. You can still change them for each download.</p>
      ${defaultsForm(s)}<div class="row end"><button class="primary" type="submit">Save</button></div></form>`;
    $("#defaults").addEventListener("submit", async (e) => {
      e.preventDefault();
      try { prefs = await busy(e.submitter, () => api("/api/settings", { method: "PUT", body: formData(e.target) })); toast("Saved"); }
      catch (err) { toast(err.message, true); }
    });
  },

  account(el) {
    el.innerHTML = accountSection();
    bindAccount();
  },

  storage(el) {
    el.innerHTML = `<div class="card"><h2>Download locations</h2>
      <p class="muted small">NAS shares (SMB or NFS) or folders where finished episodes are saved.</p><div id="locs"></div></div>`;
    return locationsPanel($("#locs"));
  },

  async media(el) {
    const s = await api("/api/settings");
    el.innerHTML = `<form class="card" id="ms"><h2>Jellyfin or Plex</h2>${mediaServerForm(s)}
      <div class="row end"><button type="button" id="ms-test">Test</button><button class="primary" type="submit">Save</button></div></form>`;
    bindMediaServerForm($("#ms"));
    $("#ms").addEventListener("submit", async (e) => {
      e.preventDefault();
      try { await busy(e.submitter, () => saveMediaServer(e.target)); toast("Media server settings saved"); e.target.token.value = ""; }
      catch (err) { toast(err.message, true); }
    });
    $("#ms-test").addEventListener("click", (e) => busy(e.currentTarget, () => testMediaServer($("#ms"))));
    if (s.media_server !== "none" && s.media_token_set) loadLibraries($("#ms"), s.media_library_id).catch(() => {});
  },

  users(el) {
    el.innerHTML = `<div class="card"><h2>Who can use AniStash</h2>
      <p class="muted small">Admins manage everything. Members can browse and download. Viewers can only browse.</p>
      <div id="users"></div></div>`;
    return usersPanel($("#users"));
  },

  async ratings(el) {
    const s = await api("/api/settings");
    el.innerHTML = `<form class="card" id="mal"><h2>MyAnimeList</h2>
      <p class="muted small">Best this season combines fan ratings from MyAnimeList, AniList and Kitsu with what's popular on hianime.</p>
      <label class="field"><span>MyAnimeList Client ID (optional)</span>
        <input type="text" name="mal_client_id" autocomplete="off" spellcheck="false"
          placeholder="${s.mal_client_id_set ? "Saved. Paste a new one to replace it" : "32 letters and numbers"}"></label>
      <p class="hint">Makes MyAnimeList scores reliable. It's free: sign in at myanimelist.net, open <i>Account settings → API → Create ID</i>
        (App type: <i>other</i>), then paste the Client ID here. Without one, AniStash uses Jikan, a public MyAnimeList mirror, when it's up.</p>
      <div class="row end">${s.mal_client_id_set ? `<button type="button" class="small" id="mal-clear">Remove</button>` : ""}
        <button class="primary" type="submit">Save</button></div></form>`;
    const saveMal = async (value, button) => {
      try {
        prefs = await busy(button, () => api("/api/settings", { method: "PUT", body: { mal_client_id: value } }));
        browseCache.delete("/api/recommend/season");
        browseCache.delete("/api/recommend/for-you");
        toast(value ? "MyAnimeList Client ID saved" : "MyAnimeList Client ID removed");
        route();
      } catch (err) { toast(err.message, true); }
    };
    $("#mal").addEventListener("submit", (e) => {
      e.preventDefault();
      const value = e.target.mal_client_id.value.trim();
      if (value) saveMal(value, e.submitter);
    });
    $("#mal-clear")?.addEventListener("click", (e) => saveMal("", e.currentTarget));
  },

  async tools(el) {
    const sys = await api("/api/system");
    el.innerHTML = `
      <div class="card"><h2>ani-cli and friends</h2>
        <div class="checks">
          <div><span>ani-cli</span><span>${esc(sys.ani_cli || "not found")}</span></div>
          <div><span>yt-dlp</span><span>${esc(sys.yt_dlp || "not found")}</span></div>
          <div><span>ffmpeg</span><span>${esc(sys.ffmpeg || "not found")}</span></div>
        </div>
        <p class="muted small">If searches or downloads start failing, the anime site probably changed. Updating ani-cli usually fixes it. Tools also update weekly on their own.</p>
        <button id="update-tools" ${sys.helper ? "" : "disabled"}>Update ani-cli and yt-dlp</button>
        <pre id="update-out" class="small muted" style="white-space:pre-wrap" hidden></pre>
      </div>
      <div class="card"><h2>Security log</h2>
        <p class="muted small">Logins, failed attempts and changes to users and settings.</p>
        <div id="audit"></div></div>
      <div class="card"><h2>Setup</h2>
        <p class="muted">Walk through the setup wizard again. Your locations, settings and queue are kept.</p>
        <a class="btn" href="#/setup?rerun=1">Run setup again</a>
      </div>`;
    $("#update-tools").addEventListener("click", async (e) => {
      try {
        const r = await busy(e.currentTarget, () => api("/api/system/update-tools", { method: "POST" }));
        $("#update-out").hidden = false;
        $("#update-out").textContent = r.output;
        toast("Update finished");
      } catch (err) { toast(err.message, true); }
    });
    return auditPanel($("#audit"));
  },

  help(el) {
    el.innerHTML = helpSection();
    bindHelp();
  },
};

// Updates (admins only) ---------------------------------------------------------------------------

// The update window: shows each step of anistash-update as it happens, then reloads the page
// into the new version (or says why it didn't work; a failed update puts the old version back)
let updateSheet = null;
const UPDATE_STEPS = [
  ["start", "Starting the update"],
  ["download", "Downloading the new version"],
  ["install", "Installing"],
  ["restart", "Restarting AniStash"],
];

async function runUpdate(latest) {
  if (!confirm(`Update AniStash to ${latest}? It takes a minute or two. Downloads in progress restart afterwards.`)) return;
  const backdrop = sheet(`<h2>Updating to ${esc(latest)}</h2>
    <div class="checks" id="upd-steps">${UPDATE_STEPS.map(([key, label]) =>
      `<div data-step="${key}"><span>${label}</span><span class="pill" data-state>Waiting</span></div>`).join("")}</div>
    <p class="muted small" id="upd-note" style="margin-top:1rem">You can keep this open; the page reloads by itself when the new version is running.</p>
    <div class="row end" id="upd-actions" hidden><button class="primary" data-close>Close</button></div>`, { dismissable: false });
  const box = backdrop.querySelector(".sheet");
  const started = Date.now();
  let timer = null;
  const stop = () => { clearInterval(timer); updateSheet = null; };
  let current = "start";
  const show = (stepKey, failed = false) => {
    if (stepKey !== "done") current = stepKey;
    const at = UPDATE_STEPS.findIndex(([key]) => key === stepKey);
    UPDATE_STEPS.forEach(([key], i) => {
      const pill = box.querySelector(`[data-step="${key}"] [data-state]`);
      const [text, cls] = i < at || stepKey === "done" ? ["Done", "ok"] : i === at ? (failed ? ["Failed", "bad"] : ["Working…", "accent"]) : ["Waiting", ""];
      pill.textContent = text;
      pill.className = `pill ${cls}`;
    });
  };
  const end = (message, bad) => {
    stop();
    box.querySelector("#upd-note").innerHTML = message;
    box.querySelector("#upd-note").className = bad ? "warn-box" : "ok-box";
    box.querySelector("#upd-actions").hidden = false;
    box.querySelector("[data-close]").onclick = () => { backdrop.remove(); reloading = false; };
  };
  updateSheet = {
    finished(version) {
      stop();
      show("done");
      box.querySelector("#upd-note").className = "ok-box";
      box.querySelector("#upd-note").textContent = `AniStash v${version} is running. Reloading…`;
      setTimeout(() => location.reload(), 1500);
    },
  };
  show("start");
  try {
    await api("/api/system/update-app", { method: "POST" });
  } catch (err) {
    show("start", true);
    return end(`The update couldn't start: ${esc(err.message)}`, true);
  }
  timer = setInterval(async () => {
    let p;
    try { p = await api("/api/system/update-progress"); }
    catch { show("restart"); return; }  // the service is restarting
    if (!updateSheet) return;  // the new version answered (serverUpdated took over)
    if (p.state === "running") show(p.step === "rollback" ? "restart" : p.step || "start", p.step === "rollback");
    else if (p.state === "failed" && p.at * 1000 >= started - 5000) {
      show(current, true);
      end(`The update didn't work: ${esc(p.message)} ${p.message.includes("back on") ? "" : "AniStash wasn't changed."}`, true);
    } else if (p.state === "done" && p.at * 1000 >= started - 5000 && p.version === PAGE_VERSION) {
      end(esc(p.message || "AniStash is already up to date."), false);
    }
    if (updateSheet && Date.now() - started > 600000) {
      end("The update is taking very long. Run <code>anistash-update</code> in the container's console to see what's happening.", true);
    }
  }, 2000);
}

async function refreshUpdateBanner() {
  const banner = document.getElementById("banner");
  if (!can.admin() || !status?.setup_complete) { banner.hidden = true; return; }
  let info;
  try { info = await api("/api/system/update-status"); } catch { return; }
  let dismissed = "";
  try { dismissed = sessionStorage.getItem("anistash-banner-dismissed") || ""; } catch { /* storage blocked */ }
  if (!info.update_available || info.auto_update || dismissed === info.latest) { banner.hidden = true; return; }
  banner.hidden = false;
  banner.innerHTML = `<div class="banner-inner">
    <span class="grow">${icon("spark", "lead")} <b>AniStash ${esc(info.latest)} is available</b> <span class="muted">(you have v${esc(info.current)})</span></span>
    <a class="small" href="https://github.com/JustinMarxGit/anistash/releases/tag/${encodeURIComponent(info.latest)}" target="_blank" rel="noopener"
       data-tip="Read what this update changes (opens GitHub)">What's new</a>
    ${info.can_update ? `<button class="primary small" id="banner-update" data-tip="Install the update now. Your settings, users and downloads stay">Update now</button>` : ""}
    <button class="ghost small icon" id="banner-close" aria-label="Hide until next visit" data-tip="Hide this until you next open AniStash">${icon("x")}</button>
  </div>`;
  $("#banner-update", banner)?.addEventListener("click", (e) => runUpdate(info.latest));
  $("#banner-close", banner).addEventListener("click", () => {
    banner.hidden = true;
    try { sessionStorage.setItem("anistash-banner-dismissed", info.latest); } catch { /* storage blocked */ }
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
          ${back && step > 0 ? `<button type="button" id="back" class="ghost">${icon("back")}Back</button>` : "<span></span>"}
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
      const line = (name, value, ok) => `<div><span>${ok ? icon("check", "okc") : icon("alert", "warnc")}${name}</span><span>${esc(value)}</span></div>`;
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
      view.innerHTML = frame("Download defaults", `<form id="defaults">${defaultsForm(settings)}
          <h3 style="margin-top:1rem">Updates</h3>
          <label class="check"><input type="checkbox" name="auto_update" ${settings.auto_update || !status.setup_complete ? "checked" : ""}> Install AniStash updates automatically (recommended)</label>
          <p class="hint" style="margin-top:0">New versions install themselves while nothing is downloading, and roll back if something goes wrong. Turn it off to update by hand from a banner instead.</p></form>
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
          <dt>Updates</dt><dd>${settings.auto_update ? "install automatically" : "manual (banner when available)"}</dd>
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
