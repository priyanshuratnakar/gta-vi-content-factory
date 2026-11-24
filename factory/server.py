"""The local web app (http://localhost:8765). Only listens on this Mac."""
import os, sys, time, shutil, threading, platform
from fastapi import FastAPI, Request, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from . import core, jobs, ingest, pipeline, posting, youtube, youtube_bot, covers, render, ai

app = FastAPI(title="GTA VI Content Factory", docs_url=None, redoc_url=None)
WEB = os.path.join(core.HERE, "web")

@app.middleware("http")
async def guard(request: Request, call_next):
    # Web pages from other sites can't add custom headers without CORS (which we never allow),
    # so this stops random websites from poking the factory through your browser.
    if request.method == "POST" and request.headers.get("x-gvcf") != "1":
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return await call_next(request)

def ok(**kw): return {"ok": True, **kw}

# ---------------- pages ----------------
@app.get("/", response_class=HTMLResponse)
def index():
    return open(os.path.join(WEB, "index.html"), encoding="utf-8").read()

@app.get("/web/{name}")
def web_file(name: str):
    p = os.path.join(WEB, os.path.basename(name))
    if not os.path.exists(p): raise HTTPException(404)
    return FileResponse(p)

@app.get("/fonts/{name}")
def font_file(name: str):
    p = os.path.join(os.path.dirname(__file__), "assets", "fonts", os.path.basename(name))
    if not os.path.exists(p): raise HTTPException(404)
    return FileResponse(p)

@app.get("/media")
def media(path: str):
    """Previews of your videos/covers. Only files inside the factory folder are served."""
    real = os.path.realpath(path)
    if not real.startswith(os.path.realpath(core.HOME_DIR) + os.sep) or not os.path.isfile(real):
        raise HTTPException(404)
    return FileResponse(real)

# ---------------- state ----------------
_tools_cache = (0.0, None)

def tools():
    global _tools_cache
    now = time.monotonic()
    if _tools_cache[1] is not None and now - _tools_cache[0] < 15.0:
        return _tools_cache[1]
    t = {n: bool(core.which(n)) for n in ("ffmpeg", "ffprobe", "yt-dlp", "cloudflared")}
    t["ollama"] = ai.ollama_ok() is not None
    t["apple_silicon"] = platform.machine() == "arm64" and sys.platform == "darwin"
    _tools_cache = (now, t)
    return t

CONNECT_KEYS = {"instagram": "instagram_token", "facebook": "facebook_token", "threads": "threads_token"}

def up_next(q, n=10):
    cands = [it for it in q if it.get("approved") and os.path.isfile(it.get("file", ""))]
    cands.sort(key=lambda it: (-posting.item_priority(it), it.get("created", 0)))
    out = []
    for it in cands:
        todo = [p for p in ("instagram", "facebook", "threads")
                if it["posted"].get(p, {}).get("status") != "posted" and (it["kind"] == "reel" or p == "facebook")]
        if todo:
            out.append({"id": it["id"], "title": it["title"], "kind": it["kind"], "file": it["file"],
                        "tier": it.get("tier", ""), "source_tag": it.get("source_tag", ""),
                        "priority": it.get("priority", 0),
                        "cover": it.get("cover", ""), "todo": todo,
                        "errors": [p for p in todo if it["posted"].get(p, {}).get("status") == "error"]})
        if len(out) >= n: break
    return out

def recent(q, n=8):
    done = []
    for it in q:
        for p, v in it["posted"].items():
            if v.get("status") == "posted":
                done.append({"title": it["title"], "platform": p, "at": v.get("time", 0),
                             "url": v.get("url", ""), "metrics": v.get("metrics") or {}})
    return sorted(done, key=lambda d: d["at"] or 0, reverse=True)[:n]

_yt_status_cache = (0.0, None)

def _yt_status():
    global _yt_status_cache
    now = time.monotonic()
    if _yt_status_cache[1] is not None and now - _yt_status_cache[0] < 3.0:
        return _yt_status_cache[1]
    res = youtube_bot.studio_status()
    _yt_status_cache = (now, res)
    return res

@app.get("/api/state")
def state():
    s = core.load_settings()
    q = pipeline.queue()
    counts = {}
    for it in q:
        for p in ("instagram", "facebook", "threads"):
            st = it["posted"].get(p, {}).get("status")
            c = counts.setdefault(p, {"posted": 0, "error": 0, "waiting": 0})
            if st == "posted": c["posted"] += 1
            elif st == "error": c["error"] += 1
            elif it["approved"] and (it["kind"] == "reel" or p == "facebook"): c["waiting"] += 1
    accts = {p: bool(core.get_key(k)) for p, k in CONNECT_KEYS.items()} | {"meta_app": bool(core.get_key("meta_app_secret"))}
    for p, tag in (("instagram", "IG"), ("facebook", "FB"), ("threads", "TH")):
        if accts[p] and posting.STATUS.get(tag, {}).get("state") == "NOT CONNECTED":
            posting.STATUS.pop(tag, None)
    return {
        "settings": s, "home": core.HOME_DIR, "dirs": core.DIRS, "tools": tools(),
        "ai": {"provider": ai.pick_provider(s), "claude": bool(core.get_key("claude_api_key")),
               "openai": bool(core.get_key("openai_api_key"))},
        "accounts": accts,
        "youtube_bot": _yt_status(),
        "posting": {"status": posting.STATUS, "running": posting.running()},
        "counts": counts, "queue_size": len(q), "up_next": up_next(q), "recent": recent(q),
        "projects": [{"id": p["id"], "title": p["title"], "status": p["status"], "duration": p["duration"],
                      "reels": sum(1 for m in p["moments"] if m.get("reel")),
                      "approved": sum(1 for m in p["moments"] if m["status"] == "approved"),
                      "longs": len(p.get("long_parts", []))} for p in ingest.all_projects()],
        "jobs": jobs.listing(), "feed": list(core.FEED)[-80:],
        "cover_styles": covers.STYLES,
        "cover_backgrounds": sorted(f for f in os.listdir(core.DIRS["covers"]) if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))),
    }

@app.post("/api/settings")
async def settings(req: Request):
    body = await req.json()
    allowed = set(core.DEFAULTS)
    return ok(settings=core.save_settings({k: v for k, v in body.items() if k in allowed}))

# ---------------- AI ----------------
@app.post("/api/ai")
async def ai_setup(req: Request):
    b = await req.json()
    prov = b.get("provider")
    if b.get("key"):
        core.set_key({"claude": "claude_api_key", "openai": "openai_api_key"}[prov], b["key"])
    s = core.save_settings({"ai_provider": prov})
    good, msg = ai.test(prov, s) if prov != "none" else (True, "No AI: you'll write the hooks yourself in Review.")
    return {"ok": good, "message": msg}

# ---------------- videos ----------------
def _ingest_and_process(job, kind, value):
    with jobs.dl_sem:
        pid = ingest.from_link(job, value) if kind == "link" else ingest.from_file(job, value, move=(kind == "inbox"))
    job.title = ingest.load(pid)["title"][:60]
    return pipeline.process(job, pid)

@app.post("/api/videos/link")
async def add_link(req: Request):
    url = (await req.json()).get("url", "").strip()
    j = jobs.start("video", "Download + clip: " + url[:50], _ingest_and_process, "link", url, parallel=True)
    return ok(job=j.id)

@app.post("/api/videos/upload")
async def upload(file: UploadFile = File(...)):
    dst = os.path.join(core.DIRS["inbox"], os.path.basename(file.filename or "video.mp4"))
    with open(dst + ".part", "wb") as f:
        shutil.copyfileobj(file.file, f, 1024 * 1024)
    os.replace(dst + ".part", dst)
    return ok(path=dst)

@app.post("/api/projects/{pid}/process")
def reprocess(pid: str):
    if not ingest.load(pid): raise HTTPException(404)
    j = jobs.start("video", "Re-clip: " + ingest.load(pid)["title"][:50], pipeline.process, pid, parallel=True)
    return ok(job=j.id)

@app.get("/api/projects/{pid}")
def project(pid: str):
    p = ingest.load(pid)
    if not p: raise HTTPException(404)
    return p

@app.post("/api/moments")
async def moment(req: Request):
    b = await req.json()
    m = pipeline.set_moment(b["pid"], b["mid"], approved=b.get("approved"), hook=b.get("hook"), emoji=b.get("emoji"))
    return ok(moment=m)

@app.post("/api/projects/{pid}/approve_all")
def approve_all(pid: str):
    pipeline.approve_all(pid); return ok()

@app.post("/api/longs")
async def longs(req: Request):
    b = await req.json()
    p = ingest.load(b["pid"])
    for part in p.get("long_parts", []):
        if part["n"] == b["n"]: part["approved"] = bool(b["approved"])
    ingest.save(p); pipeline.sync_queue(p)
    return ok()

# ---------------- covers ----------------
@app.post("/api/covers/upload")
async def cover_upload(file: UploadFile = File(...)):
    name = os.path.basename(file.filename or "background.jpg")
    with open(os.path.join(core.DIRS["covers"], name), "wb") as f:
        shutil.copyfileobj(file.file, f)
    core.save_settings({"cover_background": name})
    return ok(name=name)

@app.post("/api/covers/preview")
async def cover_preview(req: Request):
    b = await req.json()
    s = core.load_settings()
    projects = ingest.all_projects()
    bg = os.path.join(core.DIRS["covers"], b.get("background") or s.get("cover_background") or "")
    if not os.path.isfile(bg):
        if not projects: return {"ok": False, "message": "Add a video or upload a background image first."}
        bg = covers.background_for(projects[0], dict(s, cover_background=""), render.still)
    out = os.path.join(core.DIRS["covers"], "_preview.jpg")
    covers.make(bg, 1, out, s, style=b.get("style"))
    return ok(path=out, t=time.time())

@app.post("/api/projects/{pid}/covers")
def redo_covers(pid: str):
    def work(job):
        s = core.load_settings(); p = ingest.load(pid)
        bg = covers.background_for(p, s, render.still)
        for part in p.get("long_parts", []):
            part["cover"] = covers.make(bg, part["n"], part["file"][:-4] + " cover.jpg", s)
        ingest.save(p); pipeline.sync_queue(p)
    return ok(job=jobs.start("covers", "Remake covers", work).id)

# ---------------- accounts ----------------
@app.post("/api/accounts/meta_app")
async def meta_app(req: Request):
    b = await req.json()
    if b.get("app_id"): core.set_key("meta_app_id", b["app_id"])
    if b.get("app_secret"): core.set_key("meta_app_secret", b["app_secret"])
    return ok()

@app.post("/api/accounts/{platform}")
async def connect(platform: str, req: Request):
    if platform not in CONNECT_KEYS: raise HTTPException(404)
    b = await req.json()
    token = (b.get("token") or "").strip()
    if token:
        try:
            if platform in ("instagram", "facebook") and token.startswith("EAA"):
                token = posting.fb_long_lived(token)
            if platform == "threads":
                token = posting.threads_long_lived(token)
        except Exception as e:
            core.say("accounts", f"couldn't extend the key automatically: {e}", "warn")
        core.set_key(CONNECT_KEYS[platform], token)
        if platform == "facebook" and token.startswith("EAA"):
            core.set_key("instagram_token", token)      # one Meta key covers Instagram + the Page
            posting.cache_permanent_page_token(token, (b.get("page_id") or "").strip())
    if b.get("page_id"):
        core.set_key("facebook_page_id", b["page_id"].strip())
        if core.get_key("facebook_token"):
            posting.cache_permanent_page_token(core.get_key("facebook_token"), b["page_id"].strip())
    good, msg = posting.check(platform)
    return {"ok": good, "message": msg}

@app.post("/api/accounts/{platform}/disconnect")
def disconnect(platform: str):
    core.del_key(CONNECT_KEYS[platform]); return ok()

# ---------------- posting ----------------
@app.post("/api/posting/start")
def posting_start():
    core.save_settings({"posting_on": True}); posting.start_all(); return ok()

@app.post("/api/posting/stop")
def posting_stop():
    core.save_settings({"posting_on": False}); posting.stop_all(); return ok()

@app.post("/api/posting/retry")
async def posting_retry(req: Request):
    b = await req.json(); posting.retry(b["item"], b["platform"]); return ok()

@app.post("/api/posting/insights")
def posting_insights():
    return ok(**posting.refresh_insights())

@app.post("/api/queue/import_folder")
async def queue_import_folder(req: Request):
    b = await req.json()
    s = core.load_settings()
    default_dir = s.get("external_ship_folder") or core.DIRS["ship_inbox"]
    folder = os.path.expanduser((b.get("folder") or default_dir).strip())
    tier = b.get("tier") or "S"
    res = pipeline.import_external_folder(folder, tier=tier, priority_base=int(b.get("priority_base", 1000)))
    return ok(**res, folder=folder)

@app.get("/api/queue")
def get_queue():
    return pipeline.queue()

# ---------------- youtube ----------------
@app.post("/api/youtube/build")
async def yt_build(req: Request):
    b = await req.json()
    days = int(b.get("days", 7))
    prefix = b.get("prefix", "Day")
    return ok(made=youtube.build_days(days, prefix=prefix), folder=core.DIRS["youtube"])

@app.post("/api/youtube/login")
def yt_login():
    global _yt_status_cache
    _yt_status_cache = (0.0, None)
    return youtube_bot.launch_studio_browser()

@app.post("/api/youtube/autopost")
async def yt_autopost(req: Request):
    b = await req.json()
    day = (b.get("day") or "S-Tier Day 01").strip()
    spacing = int(b.get("spacing_min") or 45)
    max_v = int(b.get("max_videos") or 15)
    j = jobs.start("youtube", f"YouTube Auto-Schedule: {day}", youtube_bot.autopost_day_job,
                   day_name=day, spacing_min=spacing, max_videos=max_v, parallel=True)
    return ok(job=j.id, day=day)

@app.post("/api/open")
async def open_folder(req: Request):
    """Opens one of the factory folders in Finder."""
    which = (await req.json()).get("folder", "home")
    path = core.HOME_DIR if which == "home" else core.DIRS.get(which)
    if path and sys.platform == "darwin":
        os.system(f'open "{path}"')
    return ok(path=path)

# ---------------- background: watch the drop folder ----------------
def watch_inbox():
    seen = set()
    seen_ship = set()
    while True:
        try:
            for path in ingest.scan_inbox():
                if path in seen: continue
                seen.add(path)
                core.say("inbox", f"new video: {os.path.basename(path)}")
                jobs.start("video", os.path.basename(path)[:60], _ingest_and_process, "inbox", path, parallel=True)
            ship_dir = core.DIRS["ship_inbox"]
            if os.path.isdir(ship_dir):
                ready_files = [f for f in os.listdir(ship_dir) if f.lower().endswith((".mp4", ".mov")) and not f.startswith(".") and not f.endswith(".part")]
                new_ship = [f for f in ready_files if f not in seen_ship]
                if new_ship:
                    pipeline.import_external_folder(ship_dir, tier="S", priority_base=960)
                    seen_ship.update(new_ship)
        except Exception as e:
            core.say("inbox", f"watch error: {e}", "warn")
        time.sleep(20)

def boot():
    core.ensure_dirs()
    threading.Thread(target=watch_inbox, daemon=True).start()
    if core.load_settings().get("posting_on"):
        posting.start_all()
