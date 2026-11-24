"""python -m factory               start in the mode you picked during setup (browser or terminal)
python -m factory --web         browser dashboard
python -m factory --terminal    terminal dashboard (the web app keeps running too, for reviewing reels)
python -m factory add <link|file>   hand a video to a running factory
python -m factory status            one-line summary from a running factory
"""
import json, os, shutil, socket, sys, threading, time, urllib.request, webbrowser

def _port_free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0

def _is_gvcf(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=1.5) as r:
            d = json.load(r)
            return isinstance(d, dict) and "queue_size" in d and "posting" in d
    except Exception:
        return False

def _pick_port(for_cli=False):
    if os.environ.get("GVCF_PORT"):
        return int(os.environ["GVCF_PORT"])
    from . import core
    pf = os.path.join(core.HOME_DIR, ".port")
    if for_cli:
        try:
            saved = int(open(pf).read().strip())
            if _is_gvcf(saved):
                return saved
        except Exception:
            pass
        for p in range(8765, 8776):
            if _is_gvcf(p):
                return p
        return 8765
    for p in range(8765, 8776):
        if _port_free(p):
            try:
                core.ensure_dirs()
                open(pf, "w").write(str(p))
            except OSError:
                pass
            return p
    return 8765

PORT = int(os.environ.get("GVCF_PORT", "8765"))
URL = f"http://localhost:{PORT}"


def _call(path, body=None):
    req = urllib.request.Request(URL + path, method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "x-gvcf": "1"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def cli(args):
    cmd = args[0]
    try:
        if cmd == "monitor":
            from . import tui
            return tui.run(URL, remote=True)
        if cmd == "status":
            s = _call("/api/state")
            on = any(s["posting"]["running"].values())
            print(f"{s['settings'].get('page_name') or 'Your page'} · posting {'ON' if on else 'off'} · queue {s['queue_size']}")
            for p, c in s["counts"].items():
                print(f"  {p:<10} {c['posted']:>4} posted  {c['waiting']:>4} waiting  {c['error']:>3} failed")
            return
        if cmd == "add" and len(args) > 1:
            what = args[1]
            if what.startswith("http"):
                _call("/api/videos/link", {"url": what}); print("Added. Clipping has started.")
            else:
                from . import core
                shutil.copy2(what, core.DIRS["inbox"]); print("Copied into the inbox. Clipping starts within 20 seconds.")
            return
    except OSError:
        print("The factory isn't running. Start it with ./start.sh first."); sys.exit(1)
    print(__doc__)


def main():
    global PORT, URL
    args = sys.argv[1:]
    if args and not args[0].startswith("-"):
        PORT = _pick_port(for_cli=True)
        URL = f"http://localhost:{PORT}"
        return cli(args)
    if "--terminal" in args:
        existing_p = _pick_port(for_cli=True)
        if _is_gvcf(existing_p):
            from . import tui
            return tui.run(f"http://localhost:{existing_p}", remote=True)
    PORT = _pick_port(for_cli=False)
    URL = f"http://localhost:{PORT}"
    from . import server, core
    s = core.load_settings()
    mode = "terminal" if "--terminal" in args else "web" if "--web" in args else (s.get("launch_mode") or "web")
    if not s.get("setup_done"):
        mode = "web"   # setup happens in the browser, once
    if mode == "terminal":
        core.QUIET = True
    server.boot()
    import uvicorn
    if mode == "terminal":
        cfg = uvicorn.Config(server.app, host="127.0.0.1", port=PORT, log_level="critical", access_log=False)
        threading.Thread(target=uvicorn.Server(cfg).run, daemon=True).start()
        from . import tui
        core.say("factory", f"terminal dashboard on · web app still at {URL}")
        tui.run(URL)
        return
    core.say("factory", f"Content Factory is running at {URL}  (keep this window open; Ctrl+C to stop)")
    if "--no-browser" not in args:
        threading.Thread(target=lambda: (time.sleep(1.2), webbrowser.open(URL)), daemon=True).start()
    uvicorn.run(server.app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
