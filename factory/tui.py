"""Terminal dashboard. Same engine as the web app, drawn with curses.

Keys:  s start/stop posting   a approve everything waiting for review   o open the web app
       f open the factory folder   w switch to the web app next time   g star on GitHub   x follow on X   q quit
"""
import curses, json, subprocess, sys, time, urllib.request, webbrowser

from . import core, posting, pipeline, server

PLAT = [("instagram", "IG", "Instagram"), ("facebook", "FB", "Facebook"), ("threads", "TH", "Threads")]
REPO = "https://github.com/priyanshuratnakar/gta-vi-content-factory"
X = "https://x.com/0xratnakar"
SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def _http_api(url, path, body=None):
    req = urllib.request.Request(
        url.rstrip("/") + path,
        method="POST" if body is not None else "GET",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", "x-gvcf": "1"},
    )
    with urllib.request.urlopen(req, timeout=4) as r:
        return json.loads(r.read().decode())


def _cd(until):
    if not until: return ""
    s = max(0, int(until - time.time()))
    return f"{s // 3600}h{s % 3600 // 60:02d}m" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def _bar(frac, width):
    n = int(round(max(0, min(1, frac)) * width))
    return "█" * n + "░" * (width - n)


class UI:
    def __init__(self, scr, url, remote=False):
        self.scr, self.url, self.remote, self.msg, self.msg_t, self.frame = scr, url, remote, "", 0, 0
        curses.curs_set(0); scr.nodelay(True); scr.timeout(500)
        curses.start_color(); curses.use_default_colors()
        for i, c in enumerate([curses.COLOR_MAGENTA, curses.COLOR_CYAN, curses.COLOR_GREEN,
                               curses.COLOR_YELLOW, curses.COLOR_RED, curses.COLOR_WHITE], 1):
            curses.init_pair(i, c, -1)
        self.PINK, self.CYAN, self.GREEN, self.YELLOW, self.RED, self.WHITE = (curses.color_pair(i) for i in range(1, 7))
        self.DIM = curses.A_DIM

    def put(self, y, x, text, attr=0):
        h, w = self.scr.getmaxyx()
        if 0 <= y < h - 1 and x < w:
            try: self.scr.addnstr(y, x, text, max(0, w - x - 1), attr)
            except curses.error: pass

    def note(self, m): self.msg, self.msg_t = m, time.time()

    def draw(self):
        S = _http_api(self.url, "/api/state") if self.remote else server.state()
        self.scr.erase(); h, w = self.scr.getmaxyx(); self.frame += 1
        running = any(S["posting"]["running"].values())
        # header: a little Vice City sunset
        self.put(0, 2, "▄▀▄▀ ", self.PINK | curses.A_BOLD)
        self.put(0, 7, "CONTENT FACTORY", curses.A_BOLD)
        self.put(0, 23, f"· {S['settings'].get('page_name') or 'your page'} · SYNC 1/HR", self.DIM)
        state = "● LIVE POSTING" if running else "○ PAUSED"
        self.put(0, w - len(state) - 3, state, (self.GREEN if running else self.YELLOW) | curses.A_BOLD)
        self.put(1, 2, "─" * (w - 4), self.PINK | self.DIM)

        y = 3
        self.put(y, 2, "CHANNELS", self.DIM | curses.A_BOLD); y += 1
        for p, tag, label in PLAT:
            st, c = S["posting"]["status"].get(tag, {}), S["counts"].get(p, {"posted": 0, "waiting": 0, "error": 0})
            on = S["accounts"].get(p)
            kind = st.get("kind")
            col = self.RED if kind == "dead" else self.CYAN if kind == "busy" else self.GREEN if on else self.DIM
            self.put(y, 2, f"{label:<10}", curses.A_BOLD)
            self.put(y, 13, "●", col)
            self.put(y, 15, f"{(st.get('state') or ('ready' if on else 'not connected'))[:28]:<28}", col)
            self.put(y, 45, f"{c['posted']:>4} posted", self.WHITE)
            self.put(y, 58, f"{c['waiting']:>4} waiting", self.DIM)
            if c["error"]: self.put(y, 72, f"{c['error']} failed", self.RED)
            if st.get("until"): self.put(y, 83, f"next {_cd(st['until'])}", self.PINK)
            y += 1
        yb = S.get("youtube_bot") or {}
        yt_st = "signed in" if yb.get("signed_in") else ("chrome open" if yb.get("browser_open") else "idle")
        self.put(y, 2, "YouTube   ", curses.A_BOLD)
        self.put(y, 13, "●", self.GREEN if yb.get("signed_in") else self.YELLOW)
        self.put(y, 15, f"{yt_st:<28}", self.GREEN if yb.get("signed_in") else self.YELLOW)
        self.put(y, 45, f"{yb.get('scheduled_count', 0):>4} scheduled", self.WHITE)
        y += 1

        y += 1
        self.put(y, 2, "WORKING ON", self.DIM | curses.A_BOLD); y += 1
        jobs = [j for j in S["jobs"] if j["status"] != "done"][:4]
        if not jobs:
            self.put(y, 2, "nothing running · drop a video into the inbox folder, or: ./start.sh add <link>", self.DIM); y += 1
        for j in jobs:
            spin = SPIN[self.frame % len(SPIN)] if j["status"] == "running" else "✕"
            self.put(y, 2, spin, self.CYAN if j["status"] == "running" else self.RED)
            self.put(y, 4, f"{j['title'][:34]:<34}", curses.A_BOLD)
            self.put(y, 40, _bar(j["progress"], 24), self.PINK)
            self.put(y, 66, f"{int(j['progress'] * 100):>3}%  {(j.get('error') or j['step'])[:max(0, w - 80)]}", self.DIM)
            y += 1

        review = sum(max(0, p["reels"] - p["approved"]) for p in S["projects"])
        if review:
            y += 1; self.put(y, 2, f"▲ {review} reels waiting for your OK · press a to approve all, or o to review them in the browser", self.YELLOW); y += 1

        y += 1
        self.put(y, 2, "UP NEXT", self.DIM | curses.A_BOLD); y += 1
        for it in S["up_next"][:4]:
            self.put(y, 2, "▸", self.PINK)
            self.put(y, 4, it["title"][:max(10, w - 34)])
            self.put(y, w - 28, " ".join({"instagram": "IG", "facebook": "FB", "threads": "TH"}[t] for t in it["todo"]), self.CYAN); y += 1
        if not S["up_next"]: self.put(y, 2, "queue is empty", self.DIM); y += 1

        y += 1
        self.put(y, 2, "LIVE", self.DIM | curses.A_BOLD); y += 1
        room = h - y - 4
        feed_items = S.get("feed", []) if self.remote else list(core.FEED)
        for l in feed_items[-max(0, room):]:
            col = self.RED if l["level"] in ("error", "bad") else self.GREEN if l["level"] in ("good", "ok") else 0
            self.put(y, 2, l["t"][11:19], self.DIM)
            self.put(y, 11, f"{l['tag'][:9]:<9}", self.PINK)
            self.put(y, 21, l["msg"], col); y += 1

        self.put(h - 3, 2, "★ free & open source · star it: github.com/priyanshuratnakar/gta-vi-content-factory · follow x.com/0xratnakar", self.DIM)
        keys = " s start/stop  a approve all  o web app  f folder  w web next time  g star on GitHub  x follow on X  q quit "
        self.put(h - 2, 0, keys.ljust(w - 1), curses.A_REVERSE)
        if self.msg and time.time() - self.msg_t < 5: self.put(h - 3, 2, self.msg.ljust(w - 4), self.YELLOW)
        self.scr.refresh()
        return S

    def key(self, k, S):
        if k in (ord("q"), 27): return False
        if k == ord("s"):
            if any(S["posting"]["running"].values()):
                if self.remote: _http_api(self.url, "/api/posting/stop", {})
                else: posting.stop_all(); core.save_settings({"posting_on": False})
                self.note("Posting stops after the current upload.")
            else:
                if self.remote: _http_api(self.url, "/api/posting/start", {})
                else: core.save_settings({"posting_on": True}); posting.start_all()
                self.note("Posting started.")
        if k == ord("a"):
            n = 0
            for p in S["projects"]:
                if p["reels"] > p["approved"]: pipeline.approve_all(p["id"]); n += 1
            self.note(f"Approved everything in {n} video(s)." if n else "Nothing waiting for review.")
        if k == ord("o"): webbrowser.open(self.url); self.note(f"Opened {self.url}")
        if k == ord("f"):
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", core.HOME_DIR]); self.note("Opened the factory folder.")
        if k == ord("g"): webbrowser.open(REPO); self.note("Opened GitHub. A star helps a lot, thank you!")
        if k == ord("x"): webbrowser.open(X); self.note("Opened @0xratnakar on X.")
        if k == ord("w"):
            core.save_settings({"launch_mode": "web"}); self.note("Next start opens the web app. (./start.sh --terminal brings this back)")
        return True


def run(url, remote=False):
    def loop(scr):
        ui = UI(scr, url, remote=remote)
        while True:
            S = ui.draw()
            k = scr.getch()
            if k != -1 and not ui.key(k, S): break
    try:
        curses.wrapper(loop)
    except KeyboardInterrupt:
        pass
    print("Content Factory stopped. Start again with ./start.sh")
    print(f"★ Like it? Star {REPO}  ·  follow {X}")
