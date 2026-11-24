// GTA VI Content Factory — local web app. Plain JS, no build step.
const $ = (s, el = document) => el.querySelector(s);
const esc = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const media = (p) => "/media?path=" + encodeURIComponent(p);
let S = null;                                   // last /api/state
let view = location.hash.slice(1) || "home";
let openProject = null, P = null, reviewTab = "review", openIntg = null, aiChoice = null, coverPreview = null;

async function api(path, body) {
  const opt = body instanceof FormData
    ? { method: "POST", body, headers: { "x-gvcf": "1" } }
    : body !== undefined ? { method: "POST", body: JSON.stringify(body), headers: { "Content-Type": "application/json", "x-gvcf": "1" } } : {};
  const r = await fetch(path, opt);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || j.error || r.statusText);
  return j;
}
function toast(msg, ms = 4000) {
  const t = $("#toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), ms);
}
window.addEventListener("unhandledrejection", (e) => toast(e.reason?.message || "Something went wrong"));
const fmtMin = (s) => (s >= 3600 ? `${Math.floor(s / 3600)}h ${Math.round((s % 3600) / 60)}m` : `${Math.max(1, Math.round(s / 60))} min`);
const countdown = (u) => { if (!u) return ""; const s = Math.max(0, Math.round(u - Date.now() / 1000)); return s >= 3600 ? `${Math.floor(s / 3600)}h ${String(Math.floor(s % 3600 / 60)).padStart(2, "0")}m` : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
const ago = (t) => { if (!t) return ""; const s = Date.now() / 1000 - t; return s < 60 ? "just now" : s < 3600 ? `${Math.round(s / 60)}m ago` : s < 86400 ? `${Math.round(s / 3600)}h ago` : `${Math.round(s / 86400)}d ago`; };

// ------------------------------------------------------------ icons (stroke, 24px grid)
const ICONS = {
  home: '<path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z"/>',
  film: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M7 3v18M17 3v18M3 8h4M17 8h4M3 16h4M17 16h4"/>',
  check: '<path d="M5 12.5 10 17 19 7"/>',
  review: '<rect x="3" y="3" width="18" height="18" rx="3"/><path d="m8 12 3 3 5-6"/>',
  link: '<path d="M10 14a4.5 4.5 0 0 0 6.4 0l3-3a4.5 4.5 0 0 0-6.4-6.4l-1 1"/><path d="M14 10a4.5 4.5 0 0 0-6.4 0l-3 3a4.5 4.5 0 0 0 6.4 6.4l1-1"/>',
  image: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="m21 16-5-5-9 9"/>',
  yt: '<rect x="2.5" y="5" width="19" height="14" rx="4"/><path d="m10 9 5 3-5 3z"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  term: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="m7 9 3 3-3 3M13 15h4"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  upload: '<path d="M12 16V4M7 9l5-5 5 5M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"/>',
  play: '<path d="M7 4.5v15l12-7.5z"/>',
  pause: '<path d="M8 5v14M16 5v14"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
  arrow: '<path d="M7 17 17 7M8 7h9v9"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  spark: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6"/>',
  key: '<circle cx="8" cy="15" r="4"/><path d="m10.8 12.2 8.7-8.7M17 6l2 2M15 8l2 2"/>',
  cpu: '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/>',
  refresh: '<path d="M20 11a8 8 0 0 0-14.9-3M4 4v4h4M4 13a8 8 0 0 0 14.9 3M20 20v-4h-4"/>',
  star: '<path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1 6.2L12 17.3 6.5 20.2l1-6.2L3 9.6l6.2-.9z"/>',
  xlogo: '<path d="M4 4l16 16M20 4 4 20" />',
  gh: '<path d="M9 19c-4 1.5-4-2-6-2.5M15 21v-3.5a3 3 0 0 0-.9-2.3c3-.3 6-1.5 6-6.5a5 5 0 0 0-1.4-3.5 4.7 4.7 0 0 0-.1-3.5s-1.1-.3-3.6 1.4a12.4 12.4 0 0 0-6.5 0C6 1.4 4.9 1.7 4.9 1.7a4.7 4.7 0 0 0-.1 3.5A5 5 0 0 0 3.4 8.7c0 5 3 6.2 6 6.5a3 3 0 0 0-.9 2.3V21"/>',
  ig: '<rect x="3" y="3" width="18" height="18" rx="5"/><circle cx="12" cy="12" r="4"/><circle cx="17.5" cy="6.5" r=".6" fill="currentColor"/>',
  fb: '<path d="M14.5 21v-7.5h2.6l.4-3h-3V8.6c0-.9.3-1.5 1.6-1.5h1.6V4.4A21 21 0 0 0 15.4 4c-2.3 0-3.9 1.4-3.9 4v2.5H9v3h2.5V21"/>',
  th: '<circle cx="12" cy="12" r="3.5"/><path d="M15.5 8.5v4.8a2.6 2.6 0 0 0 5.2 0V12a8.7 8.7 0 1 0-3.4 6.9"/>',
  meta: '<path d="M3 15c0-4 2-8 4.5-8C10 7 12 12 12 12s2 5 4.5 5S21 13 21 11s-1-4-3-4c-2.5 0-4.5 4-6 7-1.5 3-3 5-5 5s-4-1.5-4-4z"/>',
};
const ic = (n, cls = "i") => `<svg class="${cls}" viewBox="0 0 24 24">${ICONS[n] || ""}</svg>`;
const LINKS = { repo: "https://github.com/priyanshuratnakar/gta-vi-content-factory", gh: "https://github.com/priyanshuratnakar", x: "https://x.com/0xratnakar" };
const socials = (cls = "") => `<div class="socials ${cls}"><a class="btn sm" href="${LINKS.repo}" target="_blank">${ic("star")} Star on GitHub</a><a class="btn sm" href="${LINKS.x}" target="_blank">${ic("xlogo")} Follow @0xratnakar</a></div>`;
const PLAT = [["instagram", "IG", "Instagram", "ig"], ["facebook", "FB", "Facebook", "fb"], ["threads", "TH", "Threads", "th"]];

// ------------------------------------------------------------ shell
const NAV = [
  ["Create", [["home", "home", "Dashboard"], ["videos", "film", "Videos"], ["review", "review", "Review"]]],
  ["Publish", [["youtube", "yt", "YouTube"], ["covers", "image", "Thumbnails"]]],
  ["Setup", [["accounts", "link", "Accounts"], ["settings", "gear", "Settings"]]],
];
const toReview = () => S.projects.reduce((a, p) => a + Math.max(0, p.reels - p.approved), 0);
function shell(title, actions, content) {
  const n = toReview();
  return `<div class="app"><aside class="side">
    <div class="brand"><div class="mark"><b>VI</b></div><div class="t">Content Factory<small>${esc(S.settings.page_name || "GTA VI edition")}</small></div></div>
    ${NAV.map(([g, items]) => `<div class="group">${g}</div>${items.map(([id, icon, label]) =>
      `<button class="nav ${view === id ? "on" : ""}" onclick="go('${id}')">${ic(icon)}${label}${id === "review" && n ? `<span class="count">${n}</span>` : ""}</button>`).join("")}`).join("")}
    <div class="foot"><div class="group">Channels</div>
      ${PLAT.map(([p, tag, label, icon]) => { const k = chanKind(p, tag); return `<div class="chan">${ic(icon)}${label}<span class="dot ${k}" title="${esc(chanText(p, tag))}"></span></div>`; }).join("")}
      <div class="tip"><b>${ic("term")} Prefer the terminal?</b>Start with <code>./start.sh --terminal</code></div>
      <div class="made"><span>Free & open source · by <a href="${LINKS.x}" target="_blank">@0xratnakar</a></span>
        <div class="row" style="gap:6px;margin-top:8px"><a class="btn sm" href="${LINKS.repo}" target="_blank">${ic("star")} Star</a><a class="btn sm" href="${LINKS.x}" target="_blank">Follow on X</a></div></div>
    </div></aside>
    <main><div class="top"><h1>${title}</h1><span class="spacer"></span>${actions || ""}</div><div class="page">${content}</div></main></div>`;
}
const chanKind = (p, tag) => { const st = S.posting.status[tag] || {}; return st.kind === "dead" ? "no" : st.kind === "busy" || (S.posting.running[p] && S.accounts[p]) ? "busy" : S.accounts[p] ? "ok" : ""; };
const chanText = (p, tag) => (S.posting.status[tag] || {}).state || (S.accounts[p] ? "connected" : "not connected");
function go(v) { view = v; location.hash = v; render(); window.scrollTo(0, 0); }
window.addEventListener("hashchange", () => { const v = location.hash.slice(1) || "home"; if (v !== view) { view = v; render(); } });
async function openFolder(f) { const r = await api("/api/open", { folder: f }); toast("Opened " + r.path); }

// ------------------------------------------------------------ onboarding
let obStep = null;
const OB = [
  ["Name your page", "What people see on your reels"],
  ["Check your Mac", "Video tools the factory needs"],
  ["Pick the brain", "The AI that writes your hooks"],
  ["Connect your accounts", "Instagram, Facebook, Threads"],
  ["Thumbnail style", "For long gameplay parts"],
  ["Add your first video", "A stream VOD or a long session"],
  ["How do you want to run it?", "Browser or terminal"],
];
function obDone(i) {
  const s = S.settings, t = S.tools;
  return [s.page_name && s.handle, t.ffmpeg && t["yt-dlp"], s.ai_provider !== "auto" || S.ai.claude || S.ai.openai,
    S.accounts.instagram || S.accounts.facebook || S.accounts.threads, (s.setup_step || 0) > 4, S.projects.length > 0, !!s.launch_mode][i];
}
function onboarding() {
  if (obStep === null) obStep = Math.min(S.settings.setup_step || 0, OB.length - 1);
  const done = OB.filter((_, i) => obDone(i)).length;
  return `<div class="ob"><div class="bar-top"><div class="mark" style="width:28px;height:28px"><b style="font-size:14px">VI</b></div><b>Set up your factory</b><span class="done">${done} of ${OB.length} done</span><span class="spacer"></span>
    ${socials()}<button class="btn ghost sm" onclick="finishSetup()">Finish later</button></div>
    <div class="wrap"><div class="intro"><div class="kicker">${ic("spark")} for GTA VI</div><div class="hello">Welcome to the<br>Content Factory</div>
      <p class="sub">Put content out, grow the page, print $$$. Drop in long gameplay and the factory clips it, writes the hooks and posts reels on schedule while you play. About ten minutes to set up, and everything stays on this Mac.</p>
      <div class="row" style="justify-content:center;margin-top:16px">${aiBtn("all", "Copy a prompt so AI sets it up for you")}</div></div>
    ${OB.map(([t, d], i) => { const isOpen = i === obStep, ok = obDone(i);
      return `<div class="step ${isOpen ? "open" : ""} ${ok && !isOpen ? "done" : ""}">
        <div class="sh" onclick="obStep=${i};render()"><span class="num">${ok && !isOpen ? ic("check") : i + 1}</span><h3>${t}</h3><span class="d">${isOpen ? "" : d}</span></div>
        ${isOpen ? `<div class="sb">${obBody(i)}</div>` : ""}</div>`; }).join("")}
    ${obStep === OB.length - 1 ? "" : `<div class="finish"><button class="btn hot lg" onclick="finishSetup()">${done === OB.length ? "Open my dashboard" : "Go to dashboard"} ${ic("arrow")}</button>
      <p class="help">You can change anything later in Settings and Accounts.</p></div>`}
    <div class="finish small muted">Free and open source. If it helps, star the repo and follow <a href="${LINKS.x}" target="_blank">@0xratnakar</a> for updates.<div class="row" style="justify-content:center;margin-top:10px">${socials()}</div></div></div></div>`;
}
function obNav(skip = true, label = "Continue") {
  return `<div class="nav-row">${skip ? `<button class="btn ghost" onclick="obNext(true)">Skip for now</button>` : ""}<button class="btn primary" onclick="obNext()">${label}</button></div>`;
}
function obBody(i) {
  const s = S.settings, t = S.tools;
  if (i === 0) return `<div class="grid g2"><div><label>Page name</label><input id="w_page" value="${esc(s.page_name)}" placeholder="My GTA VI Page"></div>
      <div><label>Instagram / Threads handle</label><input id="w_handle" value="${esc(s.handle)}" placeholder="mygta6page"></div></div>
    <label>Text on top of every reel</label><input id="w_header" value="${esc(s.header)}" oninput="$('#w_prev').textContent=this.value||'In GTA VI'">
    <p class="help">Reels read like: <b id="w_prev" style="color:var(--text)">${esc(s.header)}</b>, if you wreck Brian's seaplane he's waiting at the dock to ask what happened 😭</p>
    <div class="card" style="margin-top:14px;border-color:rgba(255,181,71,.25)"><b>Only use footage you're allowed to use.</b><p class="muted small" style="margin:4px 0 0">Your own gameplay is safest. Clipping other creators needs their permission (many run clipping programs) and a credit.</p></div>
    ${obNav(false)}`;
  if (i === 1) {
    const row = (k, name, what, fix) => `<div class="li"><span class="dot ${t[k] ? "ok" : "no"}"></span><div class="meta"><b>${name}</b><span>${what}</span></div>${t[k] ? `<span class="pill ok">ready</span>` : `<code>${fix}</code>`}</div>`;
    return `<div class="list">${row("ffmpeg", "ffmpeg", "Cuts and renders video", "brew install ffmpeg")}${row("yt-dlp", "yt-dlp", "Downloads your streams from a link", "brew install yt-dlp")}
      ${row("cloudflared", "cloudflared", "Gives Threads a temporary link to each video", "brew install cloudflared")}${row("ollama", "Ollama · optional", "Free AI that runs on your Mac", "ollama.com/download")}</div>
      <p class="help">${t.apple_silicon ? "Apple Silicon detected, so local AI runs fast. " : ""}Anything missing? Run <code>./install.sh</code> again, then refresh this page.</p>${obNav(false)}`;
  }
  if (i === 2) return aiPicker() + obNav();
  if (i === 3) return `<p class="muted small" style="margin-top:0">Posting uses the official Meta APIs. Open a row to see exactly what to click.</p>${accountsList()}${obNav()}`;
  if (i === 4) return coverStyles() + obNav(false);
  if (i === 5) return addVideo() + obNav(true, S.projects.length ? "Continue" : "I'll add one later");
  if (i === 6) return runMode(true);
}
async function obNext(skip) {
  if (obStep === 0 && !skip) await api("/api/settings", { page_name: $("#w_page").value.trim(), handle: $("#w_handle").value.trim().replace(/^@/, ""), header: $("#w_header").value.trim() || "In GTA VI" });
  obStep = Math.min(obStep + 1, OB.length - 1);
  await api("/api/settings", { setup_step: Math.max(obStep, S.settings.setup_step || 0) });
  await refresh(); render();
}
async function finishSetup() { await api("/api/settings", { setup_done: true, setup_step: OB.length }); await refresh(); go("home"); }

function runMode(inOb) {
  const m = S.settings.launch_mode || "";
  return `<div class="grid g2">
    <div class="card choice ${m === "web" ? "on" : ""}" onclick="setMode('web',${inOb})"><div class="mock web"><div class="s"><i></i><i></i><i></i><i></i></div><div class="m"><div class="h"></div><div class="r"><i></i><i></i><i></i></div></div></div>
      <h3>In my browser <span class="tag">Easiest</span></h3><p>This dashboard. Review reels with video previews, edit hooks, connect accounts.</p></div>
    <div class="card choice ${m === "terminal" ? "on" : ""}" onclick="setMode('terminal',${inOb})"><div class="mock term"><span class="p">▄▀▄▀</span> <span class="w">CONTENT FACTORY</span>        <span class="g">● POSTING</span>
Instagram  <span class="g">●</span> next post   <span class="w">  48</span> posted  <span class="p">12:04</span>
Facebook   <span class="g">●</span> uploading  <span class="w">  31</span> posted
Threads    <span class="g">●</span> waiting    <span class="w">  67</span> posted
<span class="p">⠹</span> stream_0412  <span class="p">██████████░░░░</span>  71%</div>
      <h3>In the terminal</h3><p>A live dashboard in Terminal. Lighter, keyboard driven. Reviewing still opens the browser.</p></div></div>
  ${m === "terminal" ? `<div class="card" style="margin-top:12px"><b>Next time, start it with</b><p class="muted small" style="margin:6px 0 0"><code>./start.sh</code> or double-click <code>start.command</code>. It opens in Terminal. Keys: <span class="kbd">s</span> start/stop posting, <span class="kbd">a</span> approve all, <span class="kbd">o</span> open this web app, <span class="kbd">q</span> quit. Switch back any time with <code>./start.sh --web</code>.</p></div>` : ""}
  ${inOb ? `<div class="nav-row">${socials()}<span class="spacer"></span><button class="btn hot" onclick="finishSetup()" ${m ? "" : "disabled"}>Finish setup ${ic("arrow")}</button></div>` : ""}`;
}
async function setMode(m, inOb) { await api("/api/settings", { launch_mode: m }); await refresh(); render(); if (!inOb) toast(m === "terminal" ? "Next start opens in Terminal" : "Next start opens in your browser"); }

// ------------------------------------------------------------ AI
const AIS = [
  ["claude", "Claude", "Best hooks. Paste an Anthropic API key; a few cents per long video.", true, "Recommended"],
  ["openai", "OpenAI", "Paste an OpenAI API key. Similar cost.", true, ""],
  ["ollama", "Free, on your Mac", "Ollama with a vision model. Slower, no cost, works offline.", false, ""],
  ["none", "No AI", "Still finds the loud, busy moments. You write the hooks.", false, ""],
];
function aiPicker() {
  const cur = aiChoice || (S.settings.ai_provider === "auto" ? S.ai.provider : S.settings.ai_provider);
  const pick = AIS.find((a) => a[0] === cur) || AIS[0];
  return `<div class="grid g2">${AIS.map(([id, t, d, , tag]) => `<div class="card choice ${id === cur ? "on" : ""}" onclick="aiChoice='${id}';render()"><h3>${t}${tag ? `<span class="tag">${tag}</span>` : ""}</h3><p>${d}</p></div>`).join("")}</div>
    <div class="card" style="margin-top:12px">${pick[3] ? `<label>${pick[1]} API key ${S.ai[cur] ? '<span class="pill ok">saved</span>' : ""}</label>
      <div class="inline"><input id="ai_key" type="password" placeholder="${S.ai[cur] ? "Saved. Paste a new one to replace it" : "Paste your key"}"><button class="btn primary" onclick="saveAI('${cur}')">Save & test</button></div>
      <p class="help">${cur === "claude" ? 'Get one at <a href="https://console.anthropic.com/settings/keys" target="_blank">console.anthropic.com</a> → API keys.' : 'Get one at <a href="https://platform.openai.com/api-keys" target="_blank">platform.openai.com/api-keys</a>.'} It's stored in a hidden file on this Mac.</p>`
      : cur === "ollama" ? `<ol class="steps"><li>Install Ollama from <a href="https://ollama.com/download" target="_blank">ollama.com/download</a> and open it.</li><li>In Terminal run <code>ollama pull ${esc(S.settings.ollama_model)}</code> once (about 6 GB).</li><li>Press <b>Test</b>.</li></ol><button class="btn primary" onclick="saveAI('ollama')">Test</button>`
      : `<div class="row"><span class="muted">Nothing to set up.</span><span class="spacer"></span><button class="btn" onclick="saveAI('none')">Use no AI</button></div>`}
      <div id="ai_msg" class="result"></div></div>`;
}
async function saveAI(p) {
  $("#ai_msg").textContent = "Testing…";
  const r = await api("/api/ai", { provider: p, key: $("#ai_key")?.value || "" });
  $("#ai_msg").innerHTML = `<div><span class="pill ${r.ok ? "ok" : "no"}">${r.ok ? "Works" : "Problem"}</span> ${esc(r.message)}</div>`;
  await refresh();
}

// ------------------------------------------------------------ "let AI do it" prompts
const RULES = `Rules: never type my passwords or 2FA codes (I'll do those), don't accept terms, pay for anything or change account settings without asking me first, and pause whenever Meta or Google asks me to verify something. When a key or secret appears, don't paste it anywhere yourself: tell me where it is and I'll copy it into the Content Factory.`;
const INTRO = () => `I'm setting up GTA VI Content Factory (${LINKS.repo}), a free open-source tool running on my Mac at ${location.origin}. It clips my gameplay into reels and posts them through the official Instagram, Facebook and Threads APIs. My page is "${S.settings.page_name || "my page"}"${S.settings.handle ? ` (@${S.settings.handle})` : ""}. Please use my browser to help me with this step by step, and tell me what you're doing as you go.`;
const PROMPTS = {
  meta: () => `${INTRO()}

Goal: create my Meta developer app (one time).
1. Check my Instagram is a Professional account and is linked to my Facebook Page. If not, show me where to switch it.
2. Go to https://developers.facebook.com/apps and create an app named "${S.settings.page_name || "Content Factory"} poster".
3. Add the use cases "Manage everything on your Page", "Instagram API" and "Access the Threads API".
4. Open App settings → Basic and point me to the App ID and App secret so I can paste them into http://localhost:8765/#accounts → Meta app.

${RULES}`,
  fb: () => `${INTRO()}

Goal: get the Meta key that lets the factory post to my Instagram and Facebook Page.
1. Open https://developers.facebook.com/tools/explorer/ and select my app (top right).
2. Add these permissions: pages_show_list, pages_read_engagement, pages_manage_posts, instagram_basic, instagram_content_publish, business_management.
3. Click Generate Access Token and help me tick my Page and my Instagram account in the popup (I'll approve it).
4. Tell me when the token (starts with EAA) is ready so I can copy it into http://localhost:8765/#accounts → Instagram + Facebook Page.

${RULES}`,
  th: () => `${INTRO()}

Goal: get my Threads key.
1. In https://developers.facebook.com/apps open my app → Use cases → Access the Threads API → Customize, and add threads_basic and threads_content_publish.
2. Under App roles → Roles → Threads testers, add my Threads username${S.settings.handle ? ` (${S.settings.handle})` : ""}. Then open https://www.threads.net/settings/account → Website permissions → Invites so I can accept it.
3. Back in the Threads use case settings, use the User Token Generator to generate an access token.
4. Tell me when it's ready so I can copy it into http://localhost:8765/#accounts → Threads.

${RULES}`,
  yt: () => `${INTRO()}

Goal: schedule my YouTube uploads. I've dragged the videos from one "YouTube uploads/Day NN" folder into YouTube Studio and they're sitting as drafts. The folder also has "_titles and descriptions.txt" and thumbnails; I'll attach or paste them here.
For each draft in https://studio.youtube.com: set the title and description from that file, set "No, it's not made for kids", upload the matching thumbnail for long videos, then Schedule it. Space Shorts 30 minutes apart, alternating "In GTA VI" reels and gameplay parts, and put long videos 12 hours apart at good viewing times. Show me the schedule before you start.

${RULES}`,
  all: () => `${INTRO()}

Goal: finish the whole setup with me. Open http://localhost:8765 and work through the checklist: name the page, pick the AI, create my Meta app, connect Instagram + Facebook and Threads, pick a thumbnail style and add my first video. For the account steps, follow the instructions shown in each row of the Accounts page (open the row to see the exact clicks).

${RULES}`,
};
async function copyPrompt(k) {
  const t = PROMPTS[k]();
  try { await navigator.clipboard.writeText(t); }
  catch { const a = document.createElement("textarea"); a.value = t; document.body.appendChild(a); a.select(); document.execCommand("copy"); a.remove(); }
  toast("Prompt copied. Paste it into Claude (with Claude in Chrome) or any AI that can use your browser.", 6000);
}
const aiBtn = (k, label = "Let AI do it") => `<button class="btn sm ai" onclick="copyPrompt('${k}')" title="Copies a prompt for Claude in Chrome or another browser AI">${ic("spark")} ${label}</button>`;
const aiNote = (k) => `<div class="ai-note">${ic("spark")}<div><b>Rather not click through this?</b> Copy a ready-made prompt and paste it into Claude with <a href="https://claude.ai/chrome" target="_blank">Claude in Chrome</a> (or any AI that can use your browser). It does the clicking and stops for anything you should approve.</div>${aiBtn(k, "Copy prompt")}</div>`;

// ------------------------------------------------------------ accounts (integration list)
function accountsList() {
  const a = S.accounts;
  const item = (id, icon, name, desc, connected, panel, statusText) => `<div class="item ${openIntg === id ? "open" : ""}">
    <div class="li"><div class="logo-tile">${ic(icon)}</div><div class="meta"><b>${name}</b><span>${desc}</span></div>
      ${connected ? `<span class="pill ok">${statusText || "Connected"}</span>` : ""}
      <button class="btn sm" onclick="openIntg=openIntg==='${id}'?null:'${id}';render()">${openIntg === id ? "Close" : connected ? "Manage" : `Connect ${ic("arrow")}`}</button></div>
    ${openIntg === id ? `<div class="panel">${panel}</div>` : ""}</div>`;
  const meta = `${aiNote("meta")}<p class="muted small" style="margin:0">Meta only lets tools post through an app you own. It's free, and saving it lets the factory turn short keys into 60-day keys and refresh Threads by itself.</p>
    <ol class="steps"><li>Make your Instagram a <b>Professional</b> account and link it to your Facebook Page.</li>
      <li>Open <a href="https://developers.facebook.com/apps" target="_blank">developers.facebook.com/apps</a> → <b>Create app</b>. Name it anything.</li>
      <li>Add the use cases <b>Manage everything on your Page</b>, <b>Instagram API</b> and <b>Access the Threads API</b>.</li>
      <li><b>App settings → Basic</b>: copy the App ID and App secret below.</li></ol>
    <div class="grid g2"><div><label>App ID</label><input id="m_id" placeholder="1234567890"></div><div><label>App secret</label><input id="m_secret" type="password" placeholder="${a.meta_app ? "Saved" : ""}"></div></div>
    <div class="row" style="margin-top:12px"><button class="btn primary" onclick="saveMetaApp()">Save app</button><span class="help" style="margin:0">Have Claude in Chrome? Ask it to “set up my Meta app with the meta-setup skill”.</span></div>`;
  const fb = `${aiNote("fb")}<ol class="steps"><li>Open the <a href="https://developers.facebook.com/tools/explorer/" target="_blank">Graph API Explorer</a> and pick your app.</li>
      <li>Add <code>pages_show_list</code> <code>pages_read_engagement</code> <code>pages_manage_posts</code> <code>instagram_basic</code> <code>instagram_content_publish</code> <code>business_management</code>.</li>
      <li><b>Generate Access Token</b>, tick your Page and Instagram, approve, and copy the key (starts with <code>EAA</code>).</li></ol>
    <div class="grid g2"><div><label>Meta key</label><input id="k_fb" type="password" placeholder="EAA…"></div><div><label>Page ID <span class="faint">· only if you manage several</span></label><input id="k_page" placeholder="optional"></div></div>
    <div class="row" style="margin-top:12px"><button class="btn primary" onclick="connect('facebook')">Connect Instagram + Facebook</button></div><div id="msg_facebook" class="result"></div>`;
  const th = `${aiNote("th")}<ol class="steps"><li>In your app: <b>Use cases → Access the Threads API → Customize</b>. Add <code>threads_basic</code> and <code>threads_content_publish</code>.</li>
      <li><b>Roles → Threads testers</b>: add your username, then accept in Threads (Settings → Account → Website permissions → Invites).</li>
      <li><b>User Token Generator</b> → Generate Access Token → copy it.</li></ol>
    <label>Threads key</label><div class="inline"><input id="k_th" type="password" placeholder="THAA…"><button class="btn primary" onclick="connect('threads')">Connect</button></div><div id="msg_threads" class="result"></div>`;
  const yt = `${aiNote("yt")}<p class="muted small" style="margin:0">Google keeps uploads from unaudited apps private, so the factory builds ready-to-drag Day folders with titles, descriptions and thumbnails. Drag them into YouTube Studio, then let Claude schedule them with the <code>youtube-studio-scheduler</code> skill.</p>
    <div class="row" style="margin-top:12px"><button class="btn" onclick="go('youtube')">Open YouTube page</button></div>`;
  return `<div class="list intg">
    ${item("meta", "meta", "Meta app", "One-time, about 5 minutes. Keeps your keys alive for 60 days.", a.meta_app, meta, "Saved")}
    ${item("fb", "ig", "Instagram + Facebook Page", "One key posts reels to both.", a.instagram || a.facebook, fb, a.instagram && a.facebook ? "Both connected" : a.instagram ? "Instagram only" : "Facebook only")}
    ${item("th", "th", "Threads", "Reels, photos and text posts.", a.threads, th)}
    ${item("yt", "yt", "YouTube", "Drag-and-drop uploads, scheduled for you.", false, yt)}</div>`;
}
async function saveMetaApp() { await api("/api/accounts/meta_app", { app_id: $("#m_id").value.trim(), app_secret: $("#m_secret").value.trim() }); toast("Meta app saved"); await refresh(); render(); }
async function connect(p) {
  const el = $("#msg_" + p); el.textContent = "Checking with Meta…";
  const body = p === "threads" ? { token: $("#k_th").value } : { token: $("#k_fb").value, page_id: $("#k_page").value };
  const r = await api("/api/accounts/" + p, body);
  let msg = `<div><span class="pill ${r.ok ? "ok" : "no"}">${p === "threads" ? "Threads" : "Facebook"}</span> ${esc(r.message)}</div>`;
  if (p === "facebook") { const ig = await api("/api/accounts/instagram", {}); msg += `<div><span class="pill ${ig.ok ? "ok" : "no"}">Instagram</span> ${esc(ig.message)}</div>`; }
  await refresh(); el.innerHTML = msg;
}

// ------------------------------------------------------------ add video
function addVideo() {
  return `<div class="grid g2">
    <div class="card"><h3>Paste a link</h3><p class="muted small" style="margin:4px 0 12px">Your YouTube or Twitch stream VOD, or any video you have rights to.</p>
      <div class="inline"><input id="v_link" placeholder="https://youtube.com/watch?v=…" onkeydown="if(event.key==='Enter')addLink()"><button class="btn primary" onclick="addLink()">Clip it</button></div>
      <p class="help">On PS5 or Xbox, stream to YouTube while you play, then paste the stream link here.</p></div>
    <label class="drop" id="drop" ondragover="event.preventDefault();this.classList.add('hover')" ondragleave="this.classList.remove('hover')" ondrop="dropFile(event)">
      ${ic("upload")}<b>Drop a video file</b><span class="small">or click to choose. Files in the <a href="#" onclick="openFolder('inbox');return false">Drop videos here</a> folder are picked up too.</span>
      <input type="file" accept="video/*" hidden onchange="uploadFile(this.files[0])"></label></div>`;
}
async function addLink() { const url = $("#v_link").value.trim(); if (!url) return; await api("/api/videos/link", { url }); toast("Downloading. Follow it on the dashboard."); $("#v_link").value = ""; await refresh(); render(); }
function dropFile(e) { e.preventDefault(); $("#drop").classList.remove("hover"); const f = e.dataTransfer.files[0]; if (f) uploadFile(f); }
async function uploadFile(f) {
  if (!f) return; toast(`Copying ${f.name} into the factory…`, 60000);
  const fd = new FormData(); fd.append("file", f); await api("/api/videos/upload", fd);
  toast("Added. Clipping starts in a few seconds."); await refresh(); render();
}

// ------------------------------------------------------------ dashboard
function home() {
  const running = Object.values(S.posting.running).some(Boolean);
  const tot = Object.values(S.counts).reduce((a, c) => ({ posted: a.posted + c.posted, waiting: a.waiting + c.waiting, error: a.error + c.error }), { posted: 0, waiting: 0, error: 0 });
  const n = toReview(), jobs = S.jobs.filter((j) => j.status !== "done").slice(0, 4);
  const anyAcct = S.accounts.instagram || S.accounts.facebook || S.accounts.threads;
  const hint = !anyAcct ? `Connect an account to start posting. <a href="#accounts">Connect</a>` : n ? `${n} reels are waiting for your OK. <a href="#review">Review them</a>` : !S.up_next.length ? `Nothing approved yet. <a href="#videos">Add a video</a>` : "";
  const content = `
  <div class="hero"><div class="state"><span class="dot ${running ? "busy" : ""}"></span>${running ? "Posting on schedule" : "Posting is paused"}</div>
    <div class="big">${esc(S.settings.page_name || "Your page")}</div>
    <div class="muted">${running ? "Keep this Mac awake and plugged in. Videos go out on your schedule." : hint || "Everything is ready. Press start when you are."}</div>
    <div class="kpis"><div class="kpi"><b>${tot.posted}</b><span>posts out</span></div><div class="kpi"><b>${S.up_next.length >= 8 ? "8+" : S.up_next.length}</b><span>in the queue</span></div>
      <div class="kpi"><b>${n}</b><span>to review</span></div><div class="kpi"><b>${S.projects.length}</b><span>videos clipped</span></div></div></div>
  <div class="grid g3" style="margin-top:16px">${PLAT.map(([p, tag, label, icon]) => {
    const st = S.posting.status[tag] || {}, c = S.counts[p] || { posted: 0, waiting: 0, error: 0 }, k = chanKind(p, tag);
    return `<div class="card ch"><div class="head"><div class="logo-tile">${ic(icon)}</div><h3>${label}</h3><span class="spacer"></span>
        <span class="pill ${k === "ok" ? "ok" : k === "busy" ? "busy" : k === "no" ? "no" : ""}">${esc(S.accounts[p] ? st.state || "ready" : "not connected")}</span></div>
      <div class="nums"><div><b>${c.posted}</b><span>posted</span></div><div><b>${c.waiting}</b><span>waiting</span></div>${c.error ? `<div class="bad"><b>${c.error}</b><span>failed</span></div>` : ""}</div>
      <div class="next" id="cd_${tag}">${st.until ? `Next post in <b>${countdown(st.until)}</b>` : S.accounts[p] ? esc(st.account || "Ready when you start") : `<a href="#accounts">Connect ${label}</a>`}</div></div>`; }).join("")}</div>
  <div class="split"><div>
    <h2>Up next<span class="grow"></span><button class="btn sm" onclick="syncSTier()">${ic("star")} Sync Ready Reels</button><button class="btn ghost sm" onclick="go('review')">Review more</button></h2>
    ${S.up_next.length ? `<div class="list">${S.up_next.map((it) => `<div class="li"><div class="thumb ${it.kind === "reel" ? "" : "wide"}">${it.cover ? `<img src="${media(it.cover)}">` : `<video src="${media(it.file)}#t=1" preload="metadata" muted></video>`}</div>
        <div class="meta"><b>${it.tier === "S" ? `<span class="pill ok" style="margin-right:6px">S-TIER</span>` : ""}${esc(it.title)}</b><span class="kind">${esc(it.source_tag || (it.kind === "reel" ? "Reel" : "Long part"))}</span></div>
        <div class="chips">${it.todo.map((p) => `<span class="chip ${it.errors.includes(p) ? "err" : ""}" title="${it.errors.includes(p) ? "failed, will not retry by itself" : "waiting"}">${PLAT.find((x) => x[0] === p)[1]}</span>`).join("")}</div></div>`).join("")}</div>`
      : `<div class="list"><div class="empty-state">${ic("film")}<b>Queue is empty</b>Approve some reels or click Sync Global S-Tier.</div></div>`}
    <h2>Working on</h2>
    ${jobs.length ? `<div class="list">${jobs.map((j) => `<div class="li"><span class="dot ${j.status === "failed" ? "no" : "busy"}"></span><div class="meta"><b>${esc(j.title)}</b><span>${esc(j.error || j.step)}</span></div>
        <div class="mini-bar"><span class="small muted">${Math.round(j.progress * 100)}%</span><div class="bar"><i style="width:${Math.round(j.progress * 100)}%"></i></div></div></div>`).join("")}</div>`
      : `<div class="list"><div class="li"><div class="meta"><span>Nothing running. Drop a video in and it starts here.</span></div><button class="btn sm" onclick="go('videos')">${ic("plus")} Add video</button></div></div>`}
    ${S.recent.length ? `<h2>Recently posted<span class="grow"></span><button class="btn ghost sm" onclick="refreshStats()">${ic("refresh")} Refresh stats</button></h2><div class="list">${S.recent.map((r) => {
      const m = r.metrics || {}, stats = [m.views != null ? `${m.views} views` : "", m.likes != null ? `${m.likes} likes` : "", m.shares ? `${m.shares} shares` : ""].filter(Boolean).join(" · ");
      return `<div class="li"><div class="logo-tile">${ic(PLAT.find((x) => x[0] === r.platform)?.[3] || "spark")}</div><div class="meta"><b>${esc(r.title)}</b><span>${ago(r.at)}${stats ? ` · <b>${esc(stats)}</b>` : ""}</span></div>${r.url ? `<a class="btn sm ghost" href="${esc(r.url)}" target="_blank">Open ${ic("arrow")}</a>` : ""}</div>`;
    }).join("")}</div>` : ""}
  </div><div><h2>Live log</h2><div class="log" id="feed">${feedHTML()}</div></div></div>`;
  const actions = `<button class="btn ghost sm" onclick="openFolder('ship_inbox')">${ic("upload")} Drop-to-Ship folder</button><button class="btn ghost sm" onclick="openFolder('home')">${ic("folder")} Folder</button>` +
    (running ? `<button class="btn" onclick="postStop()">${ic("pause")} Pause posting</button>` : `<button class="btn hot" onclick="postStart()">${ic("play")} Start posting</button>`);
  return shell("Dashboard", actions, content);
}
const feedHTML = () => S.feed.map((l) => `<div class="${esc(l.level)}"><span class="t">${esc(l.t.slice(11, 19))}</span> <span class="g">${esc(l.tag)}</span> ${esc(l.msg)}</div>`).join("") + `<div><span class="cursor"></span></div>`;
async function postStart() { await api("/api/posting/start", {}); toast("Posting started"); await refresh(); render(); }
async function postStop() { await api("/api/posting/stop", {}); toast("Posting pauses after the current upload"); await refresh(); render(); }
async function syncSTier() { const r = await api("/api/queue/import_folder", {}); toast(`Synced ${r.total} Global S-Tier clips (+${r.added} new, ${r.updated} updated)`); await refresh(); render(); }
async function refreshStats() { toast("Fetching live views & likes…"); const r = await api("/api/posting/insights", {}); toast(`Updated stats on ${r.updated} post(s)`); await refresh(); render(); }

// ------------------------------------------------------------ videos
function videos() {
  const s = S.settings;
  const content = `<p class="sub">Each hour of footage becomes about ${s.reels_per_hour} reels${s.make_long ? ` plus ${s.long_minutes}-minute long parts` : ""}.</p>${addVideo()}
  <h2>Your videos</h2>${S.projects.length ? `<div class="list">${S.projects.map((p) => { const busy = S.jobs.find((j) => j.status === "running" && j.title.includes(p.title.slice(0, 20)));
    return `<div class="li"><div class="logo-tile">${ic("film")}</div><div class="meta"><b>${esc(p.title)}</b><span>${fmtMin(p.duration)} · ${p.reels} reels · ${p.longs} long parts</span></div>
      <div class="mini-bar"><span class="small muted">${p.approved}/${p.reels} approved</span><div class="bar"><i style="width:${p.reels ? Math.round(100 * p.approved / p.reels) : 0}%"></i></div></div>
      <span class="pill ${busy ? "busy" : p.status === "approved" ? "ok" : p.reels > p.approved ? "warn" : ""}">${busy ? "working" : esc(p.status)}</span>
      <button class="btn sm" onclick="openProject='${p.id}';reviewTab='review';go('review')">Review</button>
      <button class="btn sm ghost" title="Find moments again" onclick="reclip('${p.id}')">${ic("refresh")}</button></div>`; }).join("")}</div>`
    : `<div class="list"><div class="empty-state">${ic("film")}<b>No videos yet</b>Paste a link or drop a file above.</div></div>`}`;
  return shell("Videos", `<button class="btn ghost sm" onclick="openFolder('inbox')">${ic("folder")} Drop folder</button>`, content);
}
async function reclip(id) { if (!confirm("Find moments again and re-render? Reels that already posted stay posted.")) return; await api(`/api/projects/${id}/process`, {}); toast("Re-clipping started"); }

// ------------------------------------------------------------ review
async function loadProject() { if (!openProject && S.projects[0]) openProject = S.projects[0].id; P = openProject ? await api(`/api/projects/${openProject}`) : null; }
function review() {
  if (!P) return shell("Review", "", `<div class="list"><div class="empty-state">${ic("review")}<b>Nothing to review yet</b>Add a video and its reels show up here. <a href="#videos">Add a video</a></div></div>`);
  const counts = { review: 0, approved: 0, rejected: 0 }; P.moments.forEach((m) => (counts[m.status] = (counts[m.status] || 0) + 1));
  if (reviewTab === "review" && !counts.review && !review.picked) reviewTab = "all";
  const list = P.moments.filter((m) => reviewTab === "all" || m.status === reviewTab);
  const tabs = [["review", "To review"], ["approved", "Approved"], ["rejected", "Rejected"], ["all", "All"]];
  const actions = `<select style="width:auto;max-width:300px;height:34px;padding:0 10px" onchange="openProject=this.value;loadProject().then(render)">${S.projects.map((p) => `<option value="${p.id}" ${p.id === P.id ? "selected" : ""}>${esc(p.title.slice(0, 48))}</option>`).join("")}</select>
    <button class="btn hot" onclick="approveAll()" ${counts.review ? "" : "disabled"}>${ic("check")} Approve all${counts.review ? ` (${counts.review})` : ""}</button>`;
  const content = `<p class="sub">Only approved videos get posted. Edit a hook and the reel re-renders with it.</p>
  <div class="tabs">${tabs.map(([id, l]) => `<button class="tab ${reviewTab === id ? "on" : ""}" onclick="reviewTab='${id}';review.picked=1;render()">${l}<span class="n">${id === "all" ? P.moments.length : counts[id] || 0}</span></button>`).join("")}</div>
  ${list.length ? `<div class="reels">${list.map((m) => `<div class="reel ${m.status}" id="m_${m.id}">
    <div class="vid">${m.reel ? `<video src="${media(m.reel)}#t=1" controls preload="metadata"></video>` : `<div class="empty">Needs a hook</div>`}
      <div class="badge"><span class="pill ${m.status === "approved" ? "ok" : m.status === "rejected" ? "no" : "warn"}">${m.status === "review" ? "to review" : m.status}</span></div>
      <div class="score">${m.score && m.why !== "loud / busy moment" ? Math.round(m.score) + "/10 · " : ""}${fmtMin(m.start)}</div></div>
    <div class="body"><div class="hookrow"><textarea id="h_${m.id}" oninput="dirty('${m.id}')">${esc(m.hook)}</textarea><input class="emoji" id="e_${m.id}" value="${esc(m.emoji)}" title="emoji" oninput="dirty('${m.id}')"></div>
      ${m.why ? `<div class="why">${esc(m.why)}</div>` : ""}
      <div class="acts"><button class="btn sm save-hint" onclick="saveHook('${m.id}')">${ic("refresh")} Save & re-render</button></div>
      <div class="acts"><button class="btn sm yes ${m.status === "approved" ? "on" : ""}" onclick="setM('${m.id}',true)">${ic("check")} Approve</button><button class="btn sm nope ${m.status === "rejected" ? "on" : ""}" onclick="setM('${m.id}',false)">${ic("x")} Skip</button></div></div></div>`).join("")}</div>`
    : `<div class="list"><div class="empty-state">${ic("check")}<b>${reviewTab === "review" ? "All caught up" : "Nothing here"}</b>${reviewTab === "review" ? "Every reel in this video has been decided." : ""}</div></div>`}
  ${P.long_parts.length ? `<h2>Long parts</h2><div class="list">${P.long_parts.map((l) => `<div class="li"><div class="thumb wide">${l.cover ? `<img src="${media(l.cover)}">` : ""}</div><div class="meta"><b>${esc(l.title)}</b><span>${fmtMin(l.duration)}</span></div>
    <button class="btn sm yes ${l.approved ? "on" : ""}" onclick="setLong(${l.n},${!l.approved})">${l.approved ? `${ic("check")} Approved` : "Approve"}</button></div>`).join("")}</div>` : ""}`;
  return shell("Review", actions, content);
}
function dirty(id) { $("#m_" + id).classList.add("dirty"); }
async function saveHook(id) { await api("/api/moments", { pid: P.id, mid: id, hook: $("#h_" + id).value, emoji: $("#e_" + id).value }); $("#m_" + id).classList.remove("dirty"); toast("Re-rendering with the new hook…"); }
async function setM(id, ok) { await api("/api/moments", { pid: P.id, mid: id, approved: ok }); await loadProject(); await refresh(); render(); }
async function setLong(n, ok) { await api("/api/longs", { pid: P.id, n, approved: ok }); await loadProject(); render(); }
async function approveAll() { await api(`/api/projects/${P.id}/approve_all`, {}); await loadProject(); await refresh(); render(); toast("Approved. They'll post on your schedule."); }

// ------------------------------------------------------------ thumbnails
function coverStyles() {
  const s = S.settings;
  return `<div class="grid g3">${Object.entries(S.cover_styles).map(([id, d]) => `<div class="card choice ${s.cover_style === id ? "on" : ""}" onclick="setStyle('${id}')">
    <div class="cover-mock ${id}"><span class="a">${id === "minimal" ? "GTA VI · Part 1" : "GTA VI"}</span>${id === "bold-left" ? `<span class="b">Full gameplay · Part 1</span>` : ""}</div>
    <h3>${id.replace("-", " ").replace(/^./, (c) => c.toUpperCase())}</h3><p>${esc(d)}</p></div>`).join("")}</div>`;
}
function covers() {
  const s = S.settings, bgs = S.cover_backgrounds.filter((f) => f !== "_preview.jpg");
  const content = `<p class="sub">Thumbnails for your long gameplay parts on YouTube and Facebook.</p>
  <h2>Style</h2>${coverStyles()}
  <h2>Background</h2><div class="bgs">
    <button class="bg ${!s.cover_background ? "on" : ""}" onclick="setBg('')">${ic("film")}A frame from the video</button>
    ${bgs.map((f) => `<button class="bg ${f === s.cover_background ? "on" : ""}" onclick="setBg('${esc(f)}')"><img src="${media(S.dirs.covers + "/" + f)}" alt="${esc(f)}"></button>`).join("")}
    <label class="bg">${ic("upload")}Upload art<input type="file" accept="image/*" hidden onchange="uploadBg(this.files[0])"></label></div>
  <p class="help">1280×720 or bigger looks best. Use art you made or have rights to.</p>
  <h2>Preview<span class="grow"></span><button class="btn sm" onclick="previewCover()">${ic("refresh")} Render preview</button></h2>
  ${coverPreview ? `<img class="preview" src="${media(coverPreview)}&t=${Date.now()}">` : `<div class="list"><div class="empty-state">${ic("image")}<b>No preview yet</b>Press Render preview to see a real thumbnail.</div></div>`}`;
  return shell("Thumbnails", "", content);
}
async function setStyle(id) { await api("/api/settings", { cover_style: id }); await refresh(); render(); }
async function setBg(v) { await api("/api/settings", { cover_background: v }); await refresh(); render(); }
async function uploadBg(f) { if (!f) return; const fd = new FormData(); fd.append("file", f); await api("/api/covers/upload", fd); await refresh(); render(); toast("Background saved"); }
async function previewCover() { toast("Rendering…"); const r = await api("/api/covers/preview", { style: S.settings.cover_style, background: S.settings.cover_background || "" }); if (!r.ok) return toast(r.message); coverPreview = r.path; render(); }

// ------------------------------------------------------------ youtube
function youtube() {
  const yb = S.youtube_bot || { day_folders: [] };
  const days = yb.day_folders?.length ? yb.day_folders : ["S-Tier Day 01"];
  const statusPill = yb.signed_in
    ? `<span class="pill ok">Signed into YouTube Studio</span>`
    : yb.browser_open
      ? `<span class="pill busy">Chrome open — finish sign-in</span>`
      : `<span class="pill">Sign in once</span>`;
  const content = `<p class="sub">100% hands-free local Chrome + Playwright scheduler — bypasses Google's 6/day unaudited API Private lock.</p>
  <div class="list intg">
    <div class="item"><div class="li"><div class="logo-tile">${ic("yt")}</div>
      <div class="meta"><b>1 · Connect YouTube Studio (One-Time Sign-In)</b><span>Opens a dedicated Google Chrome window saved on this Mac. Sign into your <b>@${esc(S.settings.handle || "yourchannel")}</b> YouTube channel once — stays logged in forever.</span></div>
      ${statusPill}<button class="btn ${yb.signed_in ? "sm" : "primary sm"}" onclick="ytLogin()">${yb.signed_in ? "Open Chrome" : "Sign in on Chrome"}</button></div></div>
    <div class="item"><div class="li" style="flex-wrap:wrap;gap:10px"><div class="logo-tile">${ic("play")}</div>
      <div class="meta"><b>2 · Auto-Upload & Schedule Day Pack</b><span>Uploads every <code>.mp4</code>, attaches <code>- thumbnail.jpg</code>, fills title & description, marks Not Made For Kids, and schedules Public posts (${yb.scheduled_count || 0} scheduled so far).</span></div>
      <select id="yt_day" style="width:auto;min-width:165px">${days.map((d) => `<option value="${esc(d)}">${esc(d)}</option>`).join("")}</select>
      <input type="number" id="yt_gap" value="30" min="15" step="15" style="width:82px" title="Minutes between scheduled posts">
      <span class="small muted">min apart</span>
      <button class="btn hot sm" onclick="ytAutoPost()">${ic("upload")} Start Auto-Schedule</button></div></div>
    <div class="item"><div class="li"><div class="logo-tile">${ic("star")}</div>
      <div class="meta"><b>3 · Build more Day packs</b><span>Packs your priority queue into numbered Day folders with <code>9:16</code> + <code>16:9</code> thumbnails & descriptions.</span></div>
      <button class="btn sm" onclick="ytBuild('S-Tier Day', 3)">Build S-Tier packs</button>
      <button class="btn ghost sm" onclick="ytBuild('Day', 7)">Build more Days</button></div></div>
  </div>
  <div class="card" style="margin-top:16px"><b>Tip: Unlock 15+ daily uploads & custom thumbnails</b><p class="muted small" style="margin:4px 0 0">Inside YouTube Studio → <b>Settings → Channel → Feature eligibility → Intermediate & Advanced features</b>: verify your phone number once so YouTube lets the bot attach custom thumbnails and upload 15+ Shorts/day.</p></div>`;
  return shell("YouTube Auto-Poster", `<button class="btn ghost sm" onclick="openFolder('youtube')">${ic("folder")} Uploads folder</button>`, content);
}
async function ytBuild(prefix = "Day", days = 7) { const r = await api("/api/youtube/build", { days, prefix }); await refresh(); render(); toast(r.made.length ? "Made " + r.made.join(", ") : "Nothing new: approve or sync more videos first"); }
async function ytLogin() { await api("/api/youtube/login", {}); toast("Opened YouTube Studio Chrome window — sign into your YouTube channel once!", 6000); setTimeout(() => refresh().then(render), 2500); }
async function ytAutoPost() {
  const day = $("#yt_day")?.value || "S-Tier Day 01", spacing_min = Number($("#yt_gap")?.value || 45);
  await api("/api/youtube/autopost", { day, spacing_min, max_videos: 15 });
  toast(`Started YouTube Auto-Scheduler for ${day}! Follow progress on Dashboard.`, 5000);
  await refresh(); go("home");
}

// ------------------------------------------------------------ settings
function settings() {
  const s = S.settings, num = (k, label, help = "") => `<div><label>${label}</label><input type="number" id="s_${k}" value="${esc(s[k])}">${help ? `<p class="help">${help}</p>` : ""}</div>`;
  const sect = (t, d, body) => `<div class="sect"><div class="lead"><h3>${t}</h3><p>${d}</p></div><div>${body}</div></div>`;
  const content = `
  ${sect("Run mode", "Where the factory opens when you start it.", runMode(false))}
  ${sect("AI", "Which model watches each moment and writes the hook.", aiPicker())}
  ${sect("Clipping", "How long videos are cut up.", `<div class="grid g3">${num("reel_seconds", "Reel length (sec)", "25 is safe for Instagram's API.")}${num("reels_per_hour", "Reels per hour")}${num("long_minutes", "Long part (min)")}</div>
    <label>Credit line on reels</label><input id="s_credit_line" value="${esc(s.credit_line)}" placeholder="gameplay: @creator"><p class="help">Only when clipping someone else, with their permission.</p>`)}
  ${sect("Posting", "Priority S-Tier scheduler, cross-platform stagger, human jitter & IG Trial Reels.", `<div class="grid g3">${PLAT.map(([p, , l]) => `<div><label>${l} · every (min)</label><input type="number" id="pe_${p}" value="${s.post_every_min[p]}"></div>`).join("")}</div>
    <div class="grid g3" style="margin-top:8px">${PLAT.map(([p, , l]) => `<div><label>${l} · stagger offset (min)</label><input type="number" id="ps_${p}" value="${(s.platform_stagger_min || {})[p] ?? 0}"></div>`).join("")}</div>
    <div class="grid g3" style="margin-top:8px"><input type="number" id="ph_a" value="${s.post_hours[0]}" title="Start hour (0-24)"><input type="number" id="ph_b" value="${s.post_hours[1]}" title="End hour (0-24)"><input type="number" id="s_jitter_min" value="${s.jitter_min ?? 6}" title="+- human jitter minutes"></div>
    <p class="help">Left/middle: active hours (24h). Right: +- random jitter minutes around each slot.</p>
    <label>Platforms on</label><div class="checks">${PLAT.map(([p, , l]) => `<label><input type="checkbox" id="po_${p}" ${s.platforms_on[p] ? "checked" : ""}>${l}</label>`).join("")}</div>
    <label>Smart shipping upgrades</label><div class="checks">
      <label><input type="checkbox" id="opt_trial" ${s.ig_trial_reels !== false ? "checked" : ""}>Instagram Trial Reels (non-follower discovery)</label>
      <label><input type="checkbox" id="opt_rot" ${s.caption_rotation !== false ? "checked" : ""}>Rotate 5 native CTA caption styles</label>
      <label><input type="checkbox" id="opt_thumb" ${s.auto_thumb_offset !== false ? "checked" : ""}>Auto sharpest-frame reel cover</label></div>
    <p class="help">Daily API limits: Instagram ~50, Facebook ~30 reels, Threads 250.</p>`)}
  ${sect("Captions", "Placeholders: {hook} {handle} {page} {game} {keywords} {hashtags}", `${PLAT.map(([p, , l]) => `<label>${l}</label><textarea id="s_caption_${p}">${esc(s["caption_" + p])}</textarea>`).join("")}<label>Hashtags</label><input id="s_hashtags" value="${esc(s.hashtags)}">`)}
  ${sect("Setup", "Walk through the onboarding again.", `<button class="btn" onclick="rerunSetup()">Run setup again</button>`)}
  <div class="savebar"><button class="btn primary" onclick="saveSettings()">Save changes</button></div>`;
  return shell("Settings", "", content);
}
async function rerunSetup() { obStep = 0; await api("/api/settings", { setup_done: false }); await refresh(); render(); }
async function saveSettings() {
  const v = (k) => $("#s_" + k).value, n = (k) => Number(v(k));
  await api("/api/settings", {
    reel_seconds: n("reel_seconds"), reels_per_hour: n("reels_per_hour"), long_minutes: n("long_minutes"), credit_line: v("credit_line"),
    caption_instagram: v("caption_instagram"), caption_facebook: v("caption_facebook"), caption_threads: v("caption_threads"), hashtags: v("hashtags"),
    post_every_min: Object.fromEntries(PLAT.map(([p]) => [p, Number($("#pe_" + p).value)])),
    platform_stagger_min: Object.fromEntries(PLAT.map(([p]) => [p, Number($("#ps_" + p).value)])),
    jitter_min: Number($("#s_jitter_min").value),
    ig_trial_reels: $("#opt_trial").checked,
    caption_rotation: $("#opt_rot").checked,
    auto_thumb_offset: $("#opt_thumb").checked,
    post_hours: [Number($("#ph_a").value), Number($("#ph_b").value)],
    platforms_on: Object.fromEntries(PLAT.map(([p]) => [p, $("#po_" + p).checked])),
  });
  toast("Saved"); await refresh();
}

// ------------------------------------------------------------ render + polling
async function render() {
  if (!S) return;
  if (!S.settings.setup_done) { $("#root").innerHTML = onboarding(); return; }
  if (view === "review") await loadProject();
  const pages = { home, videos, review, accounts: () => shell("Accounts", aiBtn("all", "Copy setup prompt for AI"), `<p class="sub">Keys stay in a hidden folder on this Mac and never go anywhere else.</p>${accountsList()}`), covers, youtube, settings };
  $("#root").innerHTML = (pages[view] || home)();
  const f = $("#feed"); if (f) f.scrollTop = f.scrollHeight;
}
async function refresh() { S = await api("/api/state"); }
async function tick() {
  try {
    await refresh();
    const typing = document.activeElement?.matches("input,textarea,select");
    const playing = [...document.querySelectorAll("video")].some((v) => !v.paused);
    const sig = JSON.stringify([S.counts, S.up_next.map((i) => i.id + i.todo), S.jobs.map((j) => j.status + Math.round(j.progress * 100) + j.step),
      S.posting.running, Object.values(S.posting.status).map((x) => x.state + x.kind),
      S.projects.map((p) => p.id + p.status + p.reels + p.approved + p.longs), S.recent.length, S.accounts, S.youtube_bot?.signed_in, S.youtube_bot?.browser_open]);
    const changed = sig !== tick.sig; tick.sig = sig;
    if (S.settings.setup_done && ["home", "videos", "review", "youtube"].includes(view) && changed && !typing && !playing) render();
    else if ($("#feed")) { const f = $("#feed"), atEnd = f.scrollTop + f.clientHeight >= f.scrollHeight - 30; f.innerHTML = feedHTML(); if (atEnd) f.scrollTop = f.scrollHeight; }
  } catch (e) { toast("Lost connection to the factory. Is it still running?"); }
}
setInterval(() => PLAT.forEach(([p, tag]) => { const el = $("#cd_" + tag), st = S?.posting.status[tag]; if (el && st?.until) el.innerHTML = `Next post in <b>${countdown(st.until)}</b>`; }), 1000);
refresh().then(render).then(() => window.scrollTo(0, 0));
setInterval(tick, 3000);
