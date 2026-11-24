"""Paths, settings, logging and small helpers shared by every part of the factory."""
import os, sys, json, time, shutil, subprocess, threading, datetime, collections

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "assets")
FONT_BOLD = os.path.join(ASSETS, "fonts", "Poppins-Bold.ttf")
FONT_HEAVY = os.path.join(ASSETS, "fonts", "Anton-Regular.ttf")

# Everything the user owns lives in one folder they can open in Finder.
HOME_DIR = os.environ.get("GVCF_HOME") or os.path.join(os.path.expanduser("~"), "GTA VI Content Factory")
DIRS = {
    "inbox":      os.path.join(HOME_DIR, "Drop videos here"),      # raw long videos dropped here get clipped
    "ship_inbox": os.path.join(HOME_DIR, "Drop ready reels here"), # already-edited 9:16 reels dropped here go straight to queue
    "projects":   os.path.join(HOME_DIR, "Projects"),              # one folder per source video
    "queue":      os.path.join(HOME_DIR, "Ready to post"),         # rendered reels/long videos waiting to post
    "youtube":    os.path.join(HOME_DIR, "YouTube uploads"),       # Day folders to drag into YouTube Studio
    "covers":     os.path.join(HOME_DIR, "Cover backgrounds"),     # your own art for thumbnails
    "logs":       os.path.join(HOME_DIR, "logs"),
    "keys":       os.path.join(HOME_DIR, ".keys"),
    "trash":      os.path.join(HOME_DIR, "_to_delete"),
}
SETTINGS_FILE = os.path.join(HOME_DIR, "settings.json")

DEFAULTS = {
    "setup_done": False,
    "setup_step": 0,
    "launch_mode": "",          # "web" or "terminal"; asked at the end of setup
    "page_name": "",                 # e.g. "My GTA VI Page"
    "handle": "",                    # e.g. "mygta6page" (Instagram/Threads handle, no @)
    "game": "GTA VI",                # header on every reel: "In GTA VI"
    "header": "In GTA VI",
    # AI
    "ai_provider": "auto",           # auto | claude | openai | ollama | none
    "claude_model": "claude-sonnet-4-5",
    "openai_model": "gpt-4.1-mini",
    "ollama_model": "qwen2.5vl:7b",
    # clipping
    "reel_seconds": 25,              # Instagram API rejects long reels on some accounts; 25s is safe
    "reels_per_hour": 12,            # how many reels to aim for per hour of source video
    "long_minutes": 25,              # length of long-form parts
    "make_long": True,
    "credit_line": "",               # e.g. "gameplay: @someone" if you clip someone else's stream (with permission)
    # captions ({hook} {handle} {page} {keywords} {hashtags} are filled in)
    "caption_instagram": "follow @{handle} for more {game}\n\n{hook}\n\n{keywords}\n\n{hashtags}",
    "caption_facebook": "{hook}\n\nFollow {page} for more {game} videos!\n\n{hashtags}",
    "caption_threads": "{hook}",
    "hashtags": "#gta6 #gtavi #gta6gameplay #grandtheftauto6 #vicecity #gta6clips",
    # posting
    "post_every_min": {"instagram": 60, "facebook": 60, "threads": 60},
    "platform_stagger_min": {"instagram": 0, "facebook": 0, "threads": 0},
    "jitter_min": 0,                 # +- minutes of human timing jitter around post_every_min
    "sync_meta": True,               # post the exact same video across IG, FB & Threads in lockstep
    "post_hours": [0, 24],           # local hours when posting is allowed, [0, 24] = any time
    "alternate_long": False,         # keep IG/FB/Threads on the same reel when sync_meta is True
    "ig_trial_reels": True,          # test IG reels on non-followers first (auto-falls back if account ineligible)
    "caption_rotation": True,        # rotate 5 native CTA styles per platform so captions don't look robotic
    "auto_thumb_offset": True,       # pass sharpest mid-action frame ms as reel cover
    "external_ship_folder": "",      # optional external folder of ready 9:16 reels to sync into queue
    "platforms_on": {"instagram": True, "facebook": True, "threads": True},
    "posting_on": False,             # remembered so posting resumes after a restart
    # covers
    "cover_style": "bold-left",
    "cover_background": "",          # file in "Cover backgrounds"; empty = a frame from the video
    # youtube
    "youtube_per_day": 15,
}

_lock = threading.Lock()
_dirs_ready = False
_json_cache = {}
_which_cache = {}

def ensure_dirs():
    global _dirs_ready
    if _dirs_ready:
        return
    for d in DIRS.values():
        os.makedirs(d, exist_ok=True)
    _dirs_ready = True

def load_settings():
    ensure_dirs()
    s = dict(DEFAULTS)
    data = read_json(SETTINGS_FILE, None)
    if isinstance(data, dict):
        s.update(data)
    return s

def save_settings(changes):
    with _lock:
        s = load_settings()
        s.update(changes)
        write_json(SETTINGS_FILE, s)
        return s

# ---------- secrets (kept out of settings.json, chmod 600) ----------
def key_path(name): return os.path.join(DIRS["keys"], name)

def get_key(name):
    p = key_path(name)
    try:
        return open(p, encoding="utf-8").read().strip()
    except OSError:
        return ""

def set_key(name, value):
    ensure_dirs()
    p = key_path(name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(value.strip() + "\n")
    os.chmod(p, 0o600)

def del_key(name):
    p = key_path(name)
    if os.path.exists(p):
        os.replace(p, os.path.join(DIRS["trash"], f"{name}.{int(time.time())}"))

# ---------- log + live feed for the web app ----------
FEED = collections.deque(maxlen=400)
QUIET = False               # the terminal dashboard owns the screen, so no prints

def now(): return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def say(tag, msg, level="info"):
    line = {"t": now(), "tag": tag, "msg": str(msg), "level": level}
    FEED.append(line)
    try:
        with open(os.path.join(DIRS["logs"], "factory.log"), "a", encoding="utf-8") as f:
            f.write(f"[{line['t']}] [{tag}] {msg}\n")
    except OSError:
        pass
    if not QUIET:
        print(f"[{line['t']}] [{tag}] {msg}", flush=True)

# ---------- json files ----------
import copy

def read_json(path, default):
    try:
        st = os.stat(path)
        sig = (st.st_mtime_ns, st.st_size)
        hit = _json_cache.get(path)
        if hit and hit[0] == sig:
            return copy.deepcopy(hit[1])
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        _json_cache[path] = (sig, copy.deepcopy(data))
        return data
    except Exception:
        return default

def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{threading.get_ident()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
    try:
        st = os.stat(path)
        _json_cache[path] = ((st.st_mtime_ns, st.st_size), copy.deepcopy(data))
    except OSError:
        _json_cache.pop(path, None)

# ---------- external tools ----------
BREW_PATHS = ["/opt/homebrew/opt/ffmpeg-full/bin", "/opt/homebrew/bin", "/usr/local/bin"]

def which(name):
    if name in _which_cache:
        return _which_cache[name]
    found = None
    for d in BREW_PATHS:
        p = os.path.join(d, name)
        if os.access(p, os.X_OK):
            found = p
            break
    if not found:
        found = shutil.which(name)
    if found:
        _which_cache[name] = found
    return found

def fast_copy(src, dst):
    """Uses APFS copy-on-write clone on macOS (instant, zero extra disk space), falling back to shutil.copyfile."""
    if os.path.exists(dst):
        os.remove(dst)
    if sys.platform == "darwin":
        r = subprocess.run(["/bin/cp", "-c", src, dst], capture_output=True)
        if r.returncode == 0:
            return dst
    shutil.copyfile(src, dst)
    return dst

def run(cmd, timeout=None, check=True):
    env = dict(os.environ, PATH=":".join(BREW_PATHS + [os.environ.get("PATH", "")]))
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env, stdin=subprocess.DEVNULL)
    if check and r.returncode != 0:
        raise RuntimeError(f"{os.path.basename(cmd[0])} failed: {(r.stderr or r.stdout).strip()[-600:]}")
    return r

def duration(path):
    r = run([which("ffprobe") or "ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path])
    return float(r.stdout.strip() or 0)

def safe_name(text, limit=90):
    bad = '<>:"/\\|?*\n\r\t'
    t = "".join("-" if c in bad else c for c in text).strip(" .-")
    return t[:limit].rstrip(" .-") or "untitled"

def move_to_trash(path):
    """We never delete the user's files; things we replace go to _to_delete so they can bin them."""
    if os.path.exists(path):
        dst = os.path.join(DIRS["trash"], f"{int(time.time())}-{os.path.basename(path)}")
        shutil.move(path, dst)
