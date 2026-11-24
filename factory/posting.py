"""Posts approved videos from the queue to Instagram, Facebook and Threads with the official APIs.

Upgrades over upstream:
1. Tier-priority scheduler (`priority` / `S-Tier` first, peak-hour S-Tier boost, cross-platform stagger + human jitter).
2. Local SHA-256 media-hash ledger (`logs/posted_hashes.json`) + remote `find_live` double-guard against duplicates.
3. Auto-scored sharpest frame `thumb_offset` (ms) + Instagram Trial Reels (`SS_PERFORMANCE` with automatic standard fallback).
4. Self-healing, single-file scoped `VideoLink` Cloudflare tunnel (never exposes HOME_DIR; auto-restarts if tunnel drops).
5. Permanent Facebook Page token caching + 60-day Instagram & Threads token auto-refresh.
6. Live post analytics poller (`refresh_insights()` -> views, likes, comments, shares stored on queue items).
"""
import os, re, sys, json, time, uuid, random, shutil, socket, tempfile, threading, subprocess, urllib.parse, urllib.request, urllib.error, datetime
from . import core, pipeline, covers, render

API = "v23.0"
G_FB, G_IG, G_TH = f"https://graph.facebook.com/{API}", f"https://graph.instagram.com/{API}", "https://graph.threads.net/v1.0"
STATUS = {}                 # platform -> {"state","until","account","kind"} for the web app
LEDGER_FILE = os.path.join(core.DIRS["logs"], "posted_hashes.json")
_qlock = threading.Lock()
_stop = threading.Event()
_next_jitter = {}

class AuthError(RuntimeError): pass

def _req(method, url, params=None, data=None, timeout=120):
    if params: url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    r = urllib.request.Request(url, data=data, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        if '"code":190' in body.replace(" ", ""): raise AuthError(body)
        raise RuntimeError(f"HTTP {e.code} from {url.split('?')[0]}: {body[:600]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"network problem: {e.reason}")

def req(method, url, params=None, data=None, timeout=120, tag="--"):
    for attempt in range(8):
        try: return _req(method, url, params, data, timeout)
        except AuthError: raise
        except RuntimeError as e:
            m = str(e).replace(" ", "")
            if "networkproblem" in m and attempt < 7:
                core.say(tag, "no internet right now, trying again in 2 min", "warn"); _nap(tag, 120, "NO INTERNET"); continue
            if any(x in m for x in ('"code":4,', '"code":17,', '"code":32,', '"code":613,', "requestlimit")) and attempt < 7:
                core.say(tag, "Meta says too many requests, waiting 15 min", "warn"); _nap(tag, 900, "RATE LIMITED"); continue
            raise

def form(d): return urllib.parse.urlencode(d).encode()

def _state(tag, text, secs=None, kind="wait", **extra):
    STATUS.setdefault(tag, {}).update(state=text, until=(time.time() + secs) if secs else None, kind=kind, **extra)

def _nap(tag, secs, text, total_until=None):
    _state(tag, text, total_until if total_until is not None else secs)
    _stop.wait(secs)

def push_file(url, token, path):
    size = os.path.getsize(path)
    r = subprocess.run(["curl", "-sS", "-X", "POST", url, "-H", f"Authorization: OAuth {token}", "-H", "offset: 0",
                        "-H", f"file_size: {size}", "--data-binary", f"@{path}", "--max-time", "900"],
                       capture_output=True, text=True)
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    if r.returncode != 0 or '"success":true' not in out.replace(" ", ""):
        raise RuntimeError(f"upload rejected: {out[:300]}")

def first_line(caption):
    return next((l.strip() for l in caption.splitlines() if l.strip() and not l.lower().startswith("follow ")), caption[:80])

# ---------------- SHA-256 duplicate ledger + sharpest cover offset ----------------
def ledger_check(platform, path):
    if not path or not os.path.isfile(path): return None
    sha = pipeline.file_sha256(path)
    led = core.read_json(LEDGER_FILE, {})
    hit = led.get(f"{platform}:{sha}")
    return (hit["id"], hit.get("url", "")) if hit and hit.get("id") else None

def ledger_record(platform, path, mid, url):
    if not path or not os.path.isfile(path): return
    with _qlock:
        sha = pipeline.file_sha256(path)
        led = core.read_json(LEDGER_FILE, {})
        led[f"{platform}:{sha}"] = {"id": mid, "url": url, "time": time.time(), "file": os.path.basename(path)}
        core.write_json(LEDGER_FILE, led)

def best_thumb_offset_ms(path):
    """Pick the sharpest, best-exposed mid-action timestamp (in ms) so IG/FB profile grids never get a black/blurry frame 0."""
    try:
        dur = core.duration(path)
        if dur <= 2.0: return 500
        tmp_dir = tempfile.mkdtemp(prefix="gvcf_thumb_")
        best = (-1.0, int(dur * 350))
        try:
            for i, frac in enumerate((0.18, 0.34, 0.50, 0.68, 0.82)):
                ts = max(0.3, min(dur - 0.5, dur * frac))
                jp = os.path.join(tmp_dir, f"f{i}.jpg")
                render.still(path, ts, jp)
                sc = covers._frame_quality(jp)
                if sc > best[0]:
                    best = (sc, int(round(ts * 1000)))
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        return best[1]
    except Exception:
        return 1500

# ---------------- token helpers (permanent FB Page token + IG/TH auto-refresh) ----------------
def fb_long_lived(short_token):
    """Swap a 1-hour Meta user token for a 60-day token and cache expiration."""
    app_id, secret = core.get_key("meta_app_id"), core.get_key("meta_app_secret")
    if not (app_id and secret): return short_token
    r = _req("GET", f"{G_FB}/oauth/access_token", {"grant_type": "fb_exchange_token", "client_id": app_id,
                                                  "client_secret": secret, "fb_exchange_token": short_token})
    if r.get("access_token"):
        core.set_key("meta_user_token_expires", str(int(time.time()) + int(r.get("expires_in", 5184000))))
    return r.get("access_token", short_token)

def cache_permanent_page_token(user_token, page_id=""):
    """A Page token derived from a long-lived User token has NO expiry date — cache it directly so FB never dies at 60 days."""
    try:
        if page_id:
            pg = _req("GET", f"{G_FB}/{page_id}", {"fields": "id,name,access_token", "access_token": user_token})
            if pg.get("access_token"):
                core.set_key("facebook_page_token", pg["access_token"])
                core.set_key("facebook_page_id", pg["id"])
                return pg["access_token"]
        pages = _req("GET", f"{G_FB}/me/accounts", {"fields": "id,name,access_token", "limit": 100, "access_token": user_token}).get("data", [])
        if len(pages) == 1 and pages[0].get("access_token"):
            core.set_key("facebook_page_token", pages[0]["access_token"])
            core.set_key("facebook_page_id", pages[0]["id"])
            return pages[0]["access_token"]
    except Exception:
        pass
    return ""

def instagram_refresh():
    """Auto-refresh IG Business Login tokens (`IG...`) or Meta User tokens (`EAA...`) when <20 days remain."""
    t = core.get_key("instagram_token")
    if not t: return
    exp = int(core.get_key("instagram_token_expires") or core.get_key("meta_user_token_expires") or 0)
    if exp and exp - time.time() > 20 * 86400: return
    try:
        if t.startswith("IG"):
            r = _req("GET", "https://graph.instagram.com/refresh_access_token", {"grant_type": "ig_refresh_token", "access_token": t})
            if r.get("access_token"):
                core.set_key("instagram_token", r["access_token"])
                core.set_key("instagram_token_expires", str(int(time.time()) + int(r.get("expires_in", 5184000))))
                core.say("IG", "Instagram key refreshed for another 60 days")
        elif t.startswith("EAA") and core.get_key("meta_app_id") and core.get_key("meta_app_secret"):
            nt = fb_long_lived(t)
            if nt and nt != t:
                core.set_key("instagram_token", nt)
                core.set_key("facebook_token", nt)
                core.say("IG", "Meta user key extended for another 60 days")
    except Exception as e:
        core.say("IG", f"couldn't auto-refresh Instagram key: {str(e)[:150]}", "warn")

def threads_long_lived(token):
    secret = core.get_key("threads_app_secret") or core.get_key("meta_app_secret")
    if not secret:
        return token
    try:
        r = _req("GET", "https://graph.threads.net/access_token", {"grant_type": "th_exchange_token", "client_secret": secret, "access_token": token})
        core.set_key("threads_token_expires", str(int(time.time()) + int(r.get("expires_in", 5184000))))
        return r.get("access_token", token)
    except RuntimeError:
        return token

def threads_refresh():
    t = core.get_key("threads_token")
    if not t: return
    exp = int(core.get_key("threads_token_expires") or 0)
    if exp and exp - time.time() > 20 * 86400: return
    try:
        r = _req("GET", "https://graph.threads.net/refresh_access_token", {"grant_type": "th_refresh_token", "access_token": t})
        core.set_key("threads_token", r["access_token"])
        core.set_key("threads_token_expires", str(int(time.time()) + int(r.get("expires_in", 5184000))))
        core.say("TH", "Threads key refreshed for another 60 days")
    except Exception as e:
        core.say("TH", f"couldn't refresh the Threads key: {str(e)[:160]}", "warn")

# ---------------- clients ----------------
class Instagram:
    name, tag, kinds = "instagram", "IG", ("reel",)
    def __init__(self):
        self.t = core.get_key("instagram_token")
        if not self.t: raise RuntimeError("not connected")
        if self.t.startswith("IG"):
            self.g = G_IG
            me = req("GET", f"{self.g}/me", {"fields": "user_id,username", "access_token": self.t})
            self.id, self.username = me["user_id"], me["username"]
        else:
            self.g = G_FB
            self.id = core.get_key("instagram_user_id")
            if not self.id:
                for pg in req("GET", f"{self.g}/me/accounts", {"fields": "instagram_business_account", "access_token": self.t}).get("data", []):
                    if pg.get("instagram_business_account"): self.id = pg["instagram_business_account"]["id"]; break
            if not self.id: raise RuntimeError("couldn't find the Instagram account linked to this key")
            core.set_key("instagram_user_id", self.id)
            self.username = req("GET", f"{self.g}/{self.id}", {"fields": "username", "access_token": self.t})["username"]
        self.account = f"@{self.username}"

    def room_today(self):
        try:
            r = req("GET", f"{self.g}/{self.id}/content_publishing_limit", {"fields": "quota_usage,config", "access_token": self.t}, tag=self.tag)
            d = (r.get("data") or [{}])[0]
            return d.get("quota_usage", 0) < (d.get("config", {}).get("quota_total") or 50)
        except AuthError: raise
        except RuntimeError: return True

    def find_live(self, line):
        r = req("GET", f"{self.g}/{self.id}/media", {"fields": "caption,permalink", "limit": 50, "access_token": self.t}, tag=self.tag)
        for m in r.get("data", []):
            if line in [l.strip() for l in (m.get("caption") or "").splitlines()]:
                return m["id"], m.get("permalink", "")
        return None

    def _create_container(self, item, offset_ms, trial, cover_url=""):
        f = {"media_type": "REELS", "upload_type": "resumable", "caption": item["captions"]["instagram"],
             "access_token": self.t}
        if cover_url:
            f["cover_url"] = cover_url
        else:
            f["thumb_offset"] = str(offset_ms)
        if trial:
            f["trial_params"] = json.dumps({"graduation_strategy": "SS_PERFORMANCE"})
        else:
            f["share_to_feed"] = "true"
        return req("POST", f"{self.g}/{self.id}/media", data=form(f), tag=self.tag)

    def post(self, item):
        s = core.load_settings()
        offset_ms = item.get("thumb_offset_ms") or (best_thumb_offset_ms(item["file"]) if s.get("auto_thumb_offset", True) else 1000)
        use_trial = bool(s.get("ig_trial_reels", True))
        cover_url, staged_cov = "", ""
        if item.get("cover") and os.path.isfile(item["cover"]) and core.which("cloudflared"):
            try:
                if not getattr(self, "link", None): self.link = VideoLink()
                cover_url, staged_cov = self.link.stage_url(item["cover"])
            except Exception:
                cover_url, staged_cov = "", ""
        try:
            try:
                c = self._create_container(item, offset_ms, trial=use_trial, cover_url=cover_url)
            except RuntimeError:
                if use_trial:
                    core.say(self.tag, "Trial Reels not active on this IG account yet — posting as standard Reel", "info")
                    c = self._create_container(item, offset_ms, trial=False, cover_url=cover_url)
                elif cover_url:
                    c = self._create_container(item, offset_ms, trial=False, cover_url="")
                else:
                    raise
            cid = c["id"]
            core.say(self.tag, f"uploading {os.path.getsize(item['file'])/1e6:.1f} MB ({'custom 9:16 cover' if cover_url else f'cover @{offset_ms/1000:.1f}s'})")
            push_file(c.get("uri") or f"https://rupload.facebook.com/ig-api-upload/{API}/{cid}", self.t, item["file"])
            for _ in range(20):
                time.sleep(30)
                st = req("GET", f"{self.g}/{cid}", {"fields": "status_code,status", "access_token": self.t}, tag=self.tag)
                if st.get("status_code") == "FINISHED": break
                if st.get("status_code") in ("ERROR", "EXPIRED"):
                    raise RuntimeError(f"processing failed: {st.get('status')}  (keep reels around 25-60s)")
            else:
                raise RuntimeError("still processing after 10 min")
        finally:
            if staged_cov:
                try: os.remove(staged_cov)
                except OSError: pass
        try:
            mid = req("POST", f"{self.g}/{self.id}/media_publish", data=form({"creation_id": cid, "access_token": self.t}), tag=self.tag)["id"]
        except AuthError: raise
        except RuntimeError as e:
            time.sleep(45)
            live = self.find_live(first_line(item["captions"]["instagram"]))
            if live: return live
            raise RuntimeError(f"publish failed: {e}")
        link = req("GET", f"{self.g}/{mid}", {"fields": "permalink", "access_token": self.t}, tag=self.tag).get("permalink", "")
        return mid, link

    def metrics(self, mid):
        out = {}
        try:
            m = _req("GET", f"{self.g}/{mid}", {"fields": "like_count,comments_count", "access_token": self.t}, timeout=20)
            out["likes"] = int(m.get("like_count") or 0)
            out["comments"] = int(m.get("comments_count") or 0)
        except Exception:
            pass
        for metric_list in ("views,saved,shares", "plays,saved,shares"):
            try:
                ins = _req("GET", f"{self.g}/{mid}/insights", {"metric": metric_list, "access_token": self.t}, timeout=20)
                for d in ins.get("data", []):
                    val = ((d.get("values") or [{}])[0]).get("value") or 0
                    k = "views" if d.get("name") in ("views", "plays") else d.get("name")
                    out[k] = int(val)
                if "views" in out: break
            except Exception:
                continue
        return out

class Facebook:
    name, tag, kinds = "facebook", "FB", ("reel", "long")
    def __init__(self):
        perm = core.get_key("facebook_page_token")
        pid = core.get_key("facebook_page_id")
        if perm and pid:
            try:
                pg = req("GET", f"{G_FB}/{pid}", {"fields": "id,name", "access_token": perm})
                self.t, self.id, self.page = perm, pg["id"], pg["name"]
                self.account = f"Page: {self.page}"
                return
            except AuthError:
                core.del_key("facebook_page_token")
        t = core.get_key("facebook_token")
        if not t: raise RuntimeError("not connected")
        if pid:
            pg = req("GET", f"{G_FB}/{pid}", {"fields": "id,name,access_token", "access_token": t})
            if not pg.get("access_token"): raise RuntimeError("this key can't manage the Page " + pg.get("name", pid))
            self.t, self.id, self.page = pg["access_token"], pg["id"], pg["name"]
            core.set_key("facebook_page_token", self.t)
        else:
            pages = req("GET", f"{G_FB}/me/accounts", {"fields": "id,name,access_token", "limit": 100, "access_token": t}).get("data", [])
            if len(pages) == 1:
                self.t, self.id, self.page = pages[0]["access_token"], pages[0]["id"], pages[0]["name"]
                core.set_key("facebook_page_id", self.id)
                core.set_key("facebook_page_token", self.t)
            elif pages:
                raise RuntimeError("this key manages several Pages; pick one in Accounts: " + ", ".join(p["name"] for p in pages))
            else:
                me = req("GET", f"{G_FB}/me", {"fields": "id,name", "access_token": t})
                self.t, self.id, self.page = t, me["id"], me["name"]
        self.account = f"Page: {self.page}"

    def room_today(self): return True

    def find_live(self, line):
        ok = 0
        for edge, field in (("video_reels", "description"), ("videos", "description"), ("posts", "message")):
            try:
                r = req("GET", f"{G_FB}/{self.id}/{edge}", {"fields": f"id,{field}", "limit": 100, "access_token": self.t}, tag=self.tag)
            except AuthError: raise
            except RuntimeError: continue
            ok += 1
            for v in r.get("data", []):
                if line in [l.strip() for l in (v.get(field) or "").splitlines()]:
                    vid = v["id"].split("_")[-1]
                    return v["id"], (f"https://www.facebook.com/reel/{vid}" if edge != "posts" else f"https://www.facebook.com/{v['id']}")
        if not ok: raise RuntimeError("couldn't read the Page to check for an existing copy, so nothing was posted")
        return None

    def _wait(self, vid, minutes):
        for _ in range(minutes * 2):
            time.sleep(30)
            try: st = req("GET", f"{G_FB}/{vid}", {"fields": "status", "access_token": self.t}, tag=self.tag).get("status", {})
            except AuthError: raise
            except RuntimeError: return
            phases = [st.get(k, {}).get("status") for k in ("uploading_phase", "processing_phase", "publishing_phase")]
            if st.get("video_status") == "ready" or phases[2] == "complete": return
            if st.get("video_status") == "error" or "error" in phases: raise RuntimeError(f"processing failed: {st}")

    def post(self, item):
        if item["kind"] == "long":
            return self.post_long(item)
        s = req("POST", f"{G_FB}/{self.id}/video_reels", data=form({"upload_phase": "start", "access_token": self.t}), tag=self.tag)
        vid = s["video_id"]
        push_file(s.get("upload_url") or f"https://rupload.facebook.com/video-upload/{API}/{vid}", self.t, item["file"])
        req("POST", f"{G_FB}/{self.id}/video_reels", data=form({"upload_phase": "finish", "video_id": vid, "video_state": "PUBLISHED",
            "description": item["captions"]["facebook"], "access_token": self.t}), tag=self.tag)
        self._wait(vid, 15)
        return vid, f"https://www.facebook.com/reel/{vid}"

    def post_long(self, item):
        gv, path = f"https://graph-video.facebook.com/{API}", item["file"]
        size = os.path.getsize(path)
        s = req("POST", f"{gv}/{self.id}/videos", data=form({"upload_phase": "start", "file_size": size, "access_token": self.t}), tag=self.tag)
        sess, vid = s["upload_session_id"], s["video_id"]
        start, end = int(s["start_offset"]), int(s["end_offset"])
        tmp = os.path.join(core.DIRS["logs"], "_chunk.bin")
        with open(path, "rb") as f:
            while start < end:
                f.seek(start)
                with open(tmp, "wb") as c: c.write(f.read(end - start))
                r = subprocess.run(["curl", "-sS", "--max-time", "1800", "-X", "POST", f"{gv}/{self.id}/videos",
                                    "--form-string", "upload_phase=transfer", "--form-string", f"upload_session_id={sess}",
                                    "--form-string", f"start_offset={start}", "--form-string", f"access_token={self.t}",
                                    "-F", f"video_file_chunk=@{tmp}"], capture_output=True, text=True)
                try: j = json.loads(r.stdout)
                except Exception: raise RuntimeError(f"upload rejected: {(r.stdout + r.stderr)[:300]}")
                if "start_offset" not in j: raise RuntimeError(f"upload rejected: {j}")
                start, end = int(j["start_offset"]), int(j["end_offset"])
                _state(self.tag, f"UPLOADING {start/size*100:.0f}%", kind="busy")
        try: os.remove(tmp)
        except OSError: pass
        req("POST", f"{gv}/{self.id}/videos", data=form({"upload_phase": "finish", "upload_session_id": sess, "title": item["title"],
            "description": item["captions"]["facebook"], "published": "true", "access_token": self.t}), tag=self.tag)
        self._wait(vid, 60)
        return vid, f"https://www.facebook.com/{self.id}/videos/{vid}"

    def metrics(self, mid):
        out = {}
        try:
            m = _req("GET", f"{G_FB}/{mid}", {"fields": "views,likes.summary(true),comments.summary(true)", "access_token": self.t}, timeout=20)
            if m.get("views") is not None: out["views"] = int(m["views"])
            out["likes"] = int(((m.get("likes") or {}).get("summary") or {}).get("total_count") or 0)
            out["comments"] = int(((m.get("comments") or {}).get("summary") or {}).get("total_count") or 0)
        except Exception:
            pass
        return out

class VideoLink:
    """Self-healing, single-file scoped Cloudflare tunnel (never exposes HOME_DIR over HTTP)."""
    def __init__(self):
        self.stage_dir = os.path.join(core.DIRS["logs"], "_tunnel_stage_" + uuid.uuid4().hex[:12])
        os.makedirs(self.stage_dir, exist_ok=True)
        self.http = None
        self.tun = None
        self.base = None
        self._open()

    def _open(self):
        self.close(keep_dir=True)
        cf = core.which("cloudflared")
        if not cf: raise RuntimeError("cloudflared isn't installed (`brew install cloudflared`)")
        sck = socket.socket(); sck.bind(("127.0.0.1", 0)); port = sck.getsockname()[1]; sck.close()
        self.http = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1", "--directory", self.stage_dir],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.tun = subprocess.Popen([cf, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.base, t0 = None, time.time()
        while time.time() - t0 < 60:
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", self.tun.stdout.readline() or "")
            if m: self.base = m.group(0); break
        if not self.base: raise RuntimeError("couldn't open the temporary video link (cloudflared)")
        threading.Thread(target=lambda: [None for _ in self.tun.stdout], daemon=True).start()
        probe_file = os.path.join(self.stage_dir, "_health.txt")
        open(probe_file, "w").write("ok")
        for _ in range(30):
            r = subprocess.run(["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "10",
                                "--doh-url", "https://1.1.1.1/dns-query", self.base + "/_health.txt"], capture_output=True, text=True)
            if r.stdout.strip() == "200": time.sleep(2); return
            time.sleep(3)

    def healthy(self):
        if not self.base or not self.tun or self.tun.poll() is not None or not self.http or self.http.poll() is not None:
            return False
        r = subprocess.run(["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "8",
                            "--doh-url", "https://1.1.1.1/dns-query", self.base + "/_health.txt"], capture_output=True, text=True)
        return r.stdout.strip() == "200"

    def stage_url(self, src_path):
        if not self.healthy():
            core.say("TH", "Cloudflare video tunnel reconnecting…", "info")
            self._open()
        ext = os.path.splitext(src_path)[1].lower() or ".mp4"
        tok = uuid.uuid4().hex + ext
        dst = os.path.join(self.stage_dir, tok)
        core.fast_copy(src_path, dst)
        return self.base + "/" + tok, dst

    def close(self, keep_dir=False):
        for pr in (self.tun, self.http):
            if pr:
                try: pr.terminate()
                except Exception: pass
        if not keep_dir and os.path.isdir(self.stage_dir):
            shutil.rmtree(self.stage_dir, ignore_errors=True)

class Threads:
    name, tag, kinds = "threads", "TH", ("reel",)
    def __init__(self):
        self.t = core.get_key("threads_token")
        if not self.t: raise RuntimeError("not connected")
        me = req("GET", f"{G_TH}/me", {"fields": "id,username", "access_token": self.t})
        self.id, self.username = me["id"], me["username"]
        self.account = f"Threads @{self.username}"
        self.link = None

    def room_today(self):
        try:
            r = req("GET", f"{G_TH}/{self.id}/threads_publishing_limit", {"fields": "quota_usage,config", "access_token": self.t}, tag=self.tag)
            d = (r.get("data") or [{}])[0]
            return d.get("quota_usage", 0) < (d.get("config", {}).get("quota_total") or 250)
        except AuthError: raise
        except RuntimeError: return True

    def find_live(self, line):
        try: r = req("GET", f"{G_TH}/{self.id}/threads", {"fields": "id,text,permalink", "limit": 30, "access_token": self.t}, tag=self.tag)
        except AuthError: raise
        except RuntimeError: return None
        for m in r.get("data", []):
            if line in [l.strip() for l in (m.get("text") or "").splitlines()]:
                return m["id"], m.get("permalink", "")
        return None

    def post(self, item):
        if self.link is None: self.link = VideoLink()
        pub_url, staged_path = self.link.stage_url(item["file"])
        try:
            c = req("POST", f"{G_TH}/{self.id}/threads", data=form({"media_type": "VIDEO", "video_url": pub_url,
                    "text": item["captions"]["threads"][:500], "access_token": self.t}), tag=self.tag)
            cid = c["id"]
            for _ in range(20):
                time.sleep(30)
                st = req("GET", f"{G_TH}/{cid}", {"fields": "status,error_message", "access_token": self.t}, tag=self.tag)
                if st.get("status") == "FINISHED": break
                if st.get("status") in ("ERROR", "EXPIRED"): raise RuntimeError(f"processing failed: {st}")
            else:
                raise RuntimeError("still processing after 10 min")
        finally:
            try: os.remove(staged_path)
            except OSError: pass
        mid = req("POST", f"{G_TH}/{self.id}/threads_publish", data=form({"creation_id": cid, "access_token": self.t}), tag=self.tag)["id"]
        try: link = req("GET", f"{G_TH}/{mid}", {"fields": "permalink", "access_token": self.t}, tag=self.tag).get("permalink", "")
        except RuntimeError: link = ""
        return mid, link

    def metrics(self, mid):
        out = {}
        try:
            ins = _req("GET", f"{G_TH}/{mid}/insights", {"metric": "views,likes,replies,reposts", "access_token": self.t}, timeout=20)
            for d in ins.get("data", []):
                val = d.get("total_value", {}).get("value")
                if val is None and d.get("values"):
                    val = d["values"][0].get("value")
                if val is not None:
                    k = "comments" if d.get("name") == "replies" else ("shares" if d.get("name") == "reposts" else d.get("name"))
                    out[k] = int(val)
        except Exception:
            pass
        return out

CLIENTS = {"instagram": Instagram, "facebook": Facebook, "threads": Threads}

def check(platform):
    try:
        c = CLIENTS[platform]()
        return True, f"Connected as {c.account}"
    except AuthError:
        return False, "That key has expired or was revoked. Make a new one (the guide shows how)."
    except Exception as e:
        return False, str(e)[:300]

# ---------------- priority scheduler + stagger + jitter + insights ----------------
RETRYABLE = ("upload rejected", "processing failed", "still processing")

def item_priority(it):
    p = int(it.get("priority") or round(float(it.get("score") or 5) * 10))
    hr = datetime.datetime.now().hour
    if it.get("tier") == "S" and (11 <= hr <= 14 or 18 <= hr <= 23):
        p += 50
    return p

def _last_post_time(q, platform):
    times = [v.get("time", 0) for it in q for p, v in it["posted"].items() if p == platform and v.get("status") == "posted"]
    return max(times) if times else 0

def _catchup_item(q, platform, kinds, active_platforms):
    """When sync_meta is True, if another active Meta platform already posted a video that `platform` hasn't posted yet, pick it immediately."""
    partial = []
    for it in q:
        if not it.get("approved") or it["kind"] not in kinds or platform in it["posted"] or not os.path.isfile(it.get("file", "")):
            continue
        other_times = [
            it["posted"][p].get("time", 0)
            for p in active_platforms
            if p != platform and it["posted"].get(p, {}).get("status") == "posted"
        ]
        if other_times:
            partial.append((min(other_times), -item_priority(it), it))
    if not partial:
        return None
    partial.sort(key=lambda x: (x[0], x[1]))
    return partial[0][2]

def _next_item(q, platform, kinds, alternate, sync_meta=False, active_platforms=()):
    if sync_meta and active_platforms:
        cu = _catchup_item(q, platform, kinds, active_platforms)
        if cu:
            return cu
    todo = [it for it in q if it["approved"] and it["kind"] in kinds and platform not in it["posted"] and os.path.isfile(it.get("file", ""))]
    if not todo: return None
    todo.sort(key=lambda it: (-item_priority(it), it.get("created", 0)))
    if alternate and not sync_meta and len(kinds) > 1:
        done = sorted((v.get("time", 0), it["kind"]) for it in q for p, v in it["posted"].items() if p == platform and v.get("status") == "posted")
        last_kind = done[-1][1] if done else "long"
        want = "reel" if last_kind == "long" else "long"
        pick = [it for it in todo if it["kind"] == want]
        if pick: return pick[0]
    return todo[0]

def _mark(item_id, platform, **v):
    with _qlock:
        q = pipeline.queue()
        for it in q:
            if it["id"] == item_id:
                prev_m = it["posted"].get(platform, {}).get("metrics")
                entry = dict(v, time=time.time())
                if prev_m and "metrics" not in entry:
                    entry["metrics"] = prev_m
                it["posted"][platform] = entry
        pipeline.save_queue(q)

def _post_with_retries(client, item):
    led = ledger_check(client.name, item.get("file"))
    if led:
        core.say(client.tag, f"already in SHA-256 ledger, skipping duplicate: {item['title'][:55]}"); return led
    line = first_line(item["captions"].get(client.name) or item["captions"].get("facebook", ""))
    live = client.find_live(line)
    if live:
        ledger_record(client.name, item.get("file"), live[0], live[1])
        core.say(client.tag, f"already live on {client.tag}, skipping: {item['title'][:55]}"); return live
    waits = [120, 300, 600]
    for attempt in range(len(waits) + 1):
        try:
            mid, url = client.post(item)
            ledger_record(client.name, item.get("file"), mid, url)
            return mid, url
        except AuthError: raise
        except RuntimeError as e:
            if attempt == len(waits) or not any(x in str(e) for x in RETRYABLE): raise
            core.say(client.tag, f"Meta refused the upload (try {attempt+1}); nothing was posted. Retrying in {waits[attempt]//60} min", "warn")
            _nap(client.tag, waits[attempt], "RETRYING")

def refresh_insights(limit_per_platform=12):
    """Fetch live views/likes/comments/shares from IG, FB & Threads for recent posts."""
    q = pipeline.queue()
    clients = {}
    for p in CLIENTS:
        if core.get_key({"instagram": "instagram_token", "facebook": "facebook_token", "threads": "threads_token"}[p]):
            try: clients[p] = CLIENTS[p]()
            except Exception: pass
    if not clients:
        return {"updated": 0}
    updated = 0
    for p, cl in clients.items():
        if not hasattr(cl, "metrics"): continue
        recent_items = sorted(
            [it for it in q if it["posted"].get(p, {}).get("status") == "posted" and it["posted"][p].get("id")],
            key=lambda it: it["posted"][p].get("time", 0), reverse=True,
        )[:limit_per_platform]
        for it in recent_items:
            m = cl.metrics(it["posted"][p]["id"])
            if m:
                with _qlock:
                    q_live = pipeline.queue()
                    for x in q_live:
                        if x["id"] == it["id"] and p in x["posted"]:
                            x["posted"][p]["metrics"] = m
                            updated += 1
                    pipeline.save_queue(q_live)
    return {"updated": updated}

def run_platform(platform):
    tag = CLIENTS[platform].tag
    try:
        client = CLIENTS[platform]()
    except AuthError:
        _state(tag, "KEY EXPIRED", kind="dead"); core.say(tag, "key expired; reconnect it in Accounts", "error"); return
    except Exception as e:
        _state(tag, "NOT CONNECTED", kind="dead", note=str(e)[:200]); return
    _state(tag, "STARTING", kind="busy", account=client.account)
    core.say(tag, f"posting as {client.account}")

    boot_stagger = int((core.load_settings().get("platform_stagger_min") or {}).get(platform, 0)) * 60
    boot_at = time.time()
    fails = 0
    while not _stop.is_set():
        s = core.load_settings()
        if not s["platforms_on"].get(platform, True):
            _nap(tag, 60, "PAUSED"); continue
        a, b = s["post_hours"]
        if not (a <= datetime.datetime.now().hour < b):
            _nap(tag, 600, "OUTSIDE POSTING HOURS"); continue
        q = pipeline.queue()
        sync_meta = bool(s.get("sync_meta", True))
        active_plats = [
            p for p, k in (("instagram", "instagram_token"), ("facebook", "facebook_token"), ("threads", "threads_token"))
            if s["platforms_on"].get(p, True) and core.get_key(k)
        ]
        catchup = _catchup_item(q, platform, client.kinds, active_plats) if sync_meta else None
        last_t = _last_post_time(q, platform)
        if catchup is not None:
            gap = 0
            item = catchup
        elif sync_meta and active_plats:
            leader = active_plats[0]
            iv = max(300, s["post_every_min"].get(leader, 30) * 60)
            plat_lasts = [_last_post_time(q, p) for p in active_plats]
            batch_last = max(plat_lasts) if any(plat_lasts) else 0
            any_pending_catchup = any(_catchup_item(q, p, CLIENTS[p].kinds, active_plats) is not None for p in active_plats)
            if platform != leader or any_pending_catchup:
                wait_for = max(10, (batch_last + iv) - time.time()) if batch_last else 10
                _nap(tag, min(wait_for, 10), "NEXT POST", total_until=wait_for); continue
            gap = (batch_last + iv) - time.time() if batch_last else 0
            if gap > 0:
                _nap(tag, min(gap, 15), "NEXT POST", total_until=gap); continue
            item = _next_item(q, platform, client.kinds, False, sync_meta=True, active_platforms=active_plats)
        else:
            if platform not in _next_jitter:
                jm = max(0, int(s.get("jitter_min", 0)))
                _next_jitter[platform] = random.randint(-jm * 60, jm * 60) if jm else 0
            if last_t == 0 and boot_stagger > 0:
                gap = (boot_at + boot_stagger) - time.time()
            else:
                base_iv = max(300, s["post_every_min"].get(platform, 60) * 60 + _next_jitter[platform])
                gap = (last_t + base_iv) - time.time() if last_t else 0
            if gap > 0:
                _nap(tag, min(gap, 15), "NEXT POST", total_until=gap); continue
            item = _next_item(q, platform, client.kinds, s.get("alternate_long", False))
        if not item:
            _nap(tag, 120, "WAITING FOR APPROVED VIDEOS"); continue
        try:
            if not client.room_today():
                _nap(tag, 1800, "DAILY LIMIT REACHED"); continue
            _state(tag, f"POSTING: {item['title'][:40]}", kind="busy")
            core.say(tag, f"posting [{item.get('tier','A')}]: {item['title'][:75]}")
            pid, url = _post_with_retries(client, item)
            _mark(item["id"], platform, status="posted", id=pid, url=url)
            _next_jitter.pop(platform, None)
            core.say(tag, f"posted: {url}", "good")
            fails = 0
        except AuthError:
            _state(tag, "KEY EXPIRED", kind="dead"); core.say(tag, "key expired; reconnect it in Accounts", "error"); return
        except Exception as e:
            fails += 1
            _mark(item["id"], platform, status="error", note=str(e)[:400])
            core.say(tag, f"couldn't post '{item['title'][:50]}': {str(e)[:250]}", "error")
            if fails >= 3:
                _state(tag, "STOPPED AFTER 3 FAILURES", kind="dead"); return
            _nap(tag, 300, "SKIPPED, COOLING DOWN")
    _state(tag, "STOPPED", kind="dead")

_threads = {}

def start_all():
    _stop.clear()
    if not getattr(start_all, "_maint", False):
        start_all._maint = True
        threading.Thread(target=_daily, daemon=True).start()
    for p in CLIENTS:
        if p in _threads and _threads[p].is_alive(): continue
        if not core.load_settings()["platforms_on"].get(p, True): continue
        t = threading.Thread(target=run_platform, args=(p,), daemon=True); t.start(); _threads[p] = t
    if shutil.which("caffeinate") and not getattr(start_all, "_awake", None):
        start_all._awake = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])

def stop_all():
    _stop.set()

def running():
    return {p: t.is_alive() for p, t in _threads.items()}

def _daily():
    while not _stop.is_set():
        threads_refresh()
        instagram_refresh()
        try: refresh_insights()
        except Exception: pass
        _stop.wait(7200)

def retry(item_id, platform):
    with _qlock:
        q = pipeline.queue()
        for it in q:
            if it["id"] == item_id and it["posted"].get(platform, {}).get("status") == "error":
                del it["posted"][platform]
        pipeline.save_queue(q)
