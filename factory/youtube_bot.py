"""100% local YouTube Studio Auto-Uploader & Public Scheduler over direct Page-level Chrome DevTools Protocol (CDP).

Why direct Page-WebSocket CDP instead of Playwright launch / connect_over_cdp:
1. NEVER kills, closes, or restarts the user's open Chrome window.
2. NEVER passes `--use-mock-keychain` (which previously caused Playwright to wipe/ignore macOS Keychain Google cookies!).
3. Connects directly to `ws://127.0.0.1:9229/devtools/page/<TAB_ID>`, bypassing Chrome 158's root `Browser.setDownloadBehavior` error.
"""
import asyncio, os, re, time, json, datetime, subprocess, urllib.request
from . import core, pipeline

CDP_PORT = 9229
PROFILE_DIR = os.path.join(core.HOME_DIR, ".yt_chrome_profile")
SCHED_LEDGER = os.path.join(core.DIRS["logs"], "youtube_scheduled.json")

CHROME_PATHS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/usr/bin/google-chrome",
]

def _chrome_bin():
    for p in CHROME_PATHS:
        if os.access(p, os.X_OK):
            return p
    return core.which("google-chrome") or core.which("chromium")

def _cdp_tabs(port=CDP_PORT):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=1.5) as r:
            return [t for t in json.loads(r.read().decode()) if t.get("type") == "page"]
    except Exception:
        return None

def launch_studio_browser():
    """Open real Google Chrome on persistent profile (.yt_chrome_profile) ONLY if not already open. Never kills an open window."""
    os.makedirs(PROFILE_DIR, exist_ok=True)
    tabs = _cdp_tabs()
    if tabs is not None:
        return {"ok": True, "already_open": True, "profile": PROFILE_DIR}
    chrome = _chrome_bin()
    if not chrome:
        raise RuntimeError("Google Chrome wasn't found in /Applications.")
    subprocess.Popen(
        [
            chrome,
            f"--user-data-dir={PROFILE_DIR}",
            f"--remote-debugging-port={CDP_PORT}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-popup-blocking",
            "https://studio.youtube.com",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    for _ in range(25):
        if _cdp_tabs() is not None:
            break
        time.sleep(0.4)
    return {"ok": True, "already_open": False, "profile": PROFILE_DIR}

def studio_status():
    root = core.DIRS["youtube"]
    os.makedirs(root, exist_ok=True)
    days = sorted(
        [d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d)) and not d.startswith(".")],
        key=lambda d: (0 if d.startswith("S-Tier") else 1, d),
    )
    led = core.read_json(SCHED_LEDGER, {})
    tabs = _cdp_tabs()
    info = {
        "browser_open": tabs is not None,
        "signed_in": False,
        "channel_url": "",
        "scheduled_count": len(led),
        "day_folders": days,
    }
    if not tabs:
        return info
    for t in tabs:
        u = t.get("url") or ""
        if "studio.youtube.com/channel/" in u and "accounts.google.com" not in u:
            info["signed_in"] = True
            info["channel_url"] = u
            break
    return info

def parse_day_folder(day_dir):
    notes_file = os.path.join(day_dir, "_titles and descriptions.txt")
    desc_by_idx, desc_by_title = {}, {}
    if os.path.isfile(notes_file):
        raw = open(notes_file, encoding="utf-8").read()
        for block in re.split(r"-{20,}\n?", raw):
            lines = [l.rstrip() for l in block.strip().splitlines() if l.strip()]
            if not lines:
                continue
            head, body = lines[0], "\n".join(lines[1:]).strip()
            m = re.match(r"^\[(\d+)\]\s*(.*)$", head)
            if m:
                desc_by_idx[int(m.group(1))] = (m.group(2).strip(), body)
            else:
                desc_by_title[head.strip()] = body

    mp4s = sorted(f for f in os.listdir(day_dir) if f.lower().endswith(".mp4") and not f.startswith("."))
    items = []
    for i, fname in enumerate(mp4s, 1):
        mp4_path = os.path.join(day_dir, fname)
        stem = os.path.splitext(fname)[0]
        thumb_path = os.path.join(day_dir, stem + " - thumbnail.jpg")
        m_num = re.match(r"^(\d+)\s*-\s*(.*)$", stem)
        idx = int(m_num.group(1)) if m_num else i
        clean_stem = m_num.group(2).strip() if m_num else stem
        if idx in desc_by_idx:
            title, desc = desc_by_idx[idx]
        else:
            title = clean_stem
            desc = desc_by_title.get(stem) or desc_by_title.get(clean_stem) or f"{title}\n\n#gta6 #gtavi #Shorts"
        items.append({
            "mp4": mp4_path,
            "thumb": thumb_path if os.path.isfile(thumb_path) else "",
            "title": title[:100],
            "description": desc[:4900],
        })
    return items

def _round_15m(dt: datetime.datetime) -> datetime.datetime:
    dt = dt.replace(second=0, microsecond=0)
    rem = dt.minute % 15
    if rem:
        dt += datetime.timedelta(minutes=(15 - rem))
    return dt

def _next_schedule_slots(n, spacing_min=30, start_in_min=30):
    led = core.read_json(SCHED_LEDGER, {})
    latest_ts = max((v.get("scheduled_ts", 0) for v in led.values()), default=0)
    base_ts = max(time.time() + start_in_min * 60, latest_ts + spacing_min * 60)
    cur = _round_15m(datetime.datetime.fromtimestamp(base_ts))
    step = max(15, round(spacing_min / 15) * 15)
    slots = []
    for _ in range(n):
        slots.append(cur)
        cur = _round_15m(cur + datetime.timedelta(minutes=step))
    return slots


class _CDPPage:
    """Minimal, rock-solid direct Page-target CDP client (never restarts Chrome!)."""
    def __init__(self, ws):
        self.ws = ws
        self._id = 0

    async def call(self, method, params=None):
        self._id += 1
        cid = self._id
        await self.ws.send(json.dumps({"id": cid, "method": method, "params": params or {}}))
        while True:
            raw = await asyncio.wait_for(self.ws.recv(), timeout=60)
            msg = json.loads(raw)
            if msg.get("id") == cid:
                if "error" in msg:
                    raise RuntimeError(f"CDP {method}: {msg['error']}")
                return msg.get("result") or {}

    async def eval(self, js):
        r = await self.call("Runtime.evaluate", {"expression": js, "returnByValue": True, "awaitPromise": True})
        if "exceptionDetails" in r:
            raise RuntimeError(f"JS error: {r['exceptionDetails'].get('text')}")
        return (r.get("result") or {}).get("value")

    async def wait_for(self, js_cond, timeout=25.0):
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            val = await self.eval(f"Boolean({js_cond})")
            if val:
                return True
            await asyncio.sleep(0.5)
        raise RuntimeError(f"Timed out waiting on YouTube Studio UI: {js_cond[:80]}")

    async def set_file_input(self, selector, file_path):
        await self.call("DOM.enable")
        # Query via JS -> requestNode -> setFileInputFiles works even across shadow/custom elements!
        res = await self.call("Runtime.evaluate", {"expression": f"document.querySelector({json.dumps(selector)})"})
        obj_id = (res.get("result") or {}).get("objectId")
        if not obj_id:
            raise RuntimeError(f"File input not found: {selector}")
        await self.call("DOM.setFileInputFiles", {"files": [file_path], "objectId": obj_id})

    async def set_contenteditable(self, selector, text):
        await self.wait_for(f"document.querySelector({json.dumps(selector)})")
        await self.eval(
            f"""(() => {{
                const el = document.querySelector({json.dumps(selector)});
                el.scrollIntoView({{block: 'center'}});
                el.focus();
                el.click();
            }})()"""
        )
        await asyncio.sleep(0.25)
        await self.eval(
            f"""(() => {{
                const el = document.querySelector({json.dumps(selector)});
                document.execCommand('selectAll', false, null);
                document.execCommand('insertText', false, {json.dumps(text)});
                el.dispatchEvent(new Event('input', {{bubbles: true}}));
            }})()"""
        )
        await asyncio.sleep(0.3)

    async def type_into_input(self, selector, text):
        await self.eval(
            f"""(() => {{
                const el = document.querySelector({json.dumps(selector)});
                if (!el) return false;
                el.focus();
                el.click();
                el.select && el.select();
                return true;
            }})()"""
        )
        await asyncio.sleep(0.2)
        await self.call("Input.insertText", {"text": text})
        await self.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13})
        await self.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13})
        await asyncio.sleep(0.5)


async def _run_cdp_autopost(job, day_name, todo, slots, led_empty_initially):
    import websockets  # type: ignore

    tabs = _cdp_tabs()
    if not tabs:
        launch_studio_browser()
        await asyncio.sleep(2.0)
        tabs = _cdp_tabs() or []
    target = next((t for t in tabs if "studio.youtube.com" in (t.get("url") or "")), None) or (tabs[0] if tabs else None)
    if not target or not target.get("webSocketDebuggerUrl"):
        raise RuntimeError("No open YouTube Studio tab found in Chrome.")

    scheduled_n = 0
    async with websockets.connect(target["webSocketDebuggerUrl"], max_size=16 * 1024 * 1024) as ws:
        cdp = _CDPPage(ws)
        await cdp.call("Page.enable")
        await cdp.call("Runtime.enable")

        cur_url = await cdp.eval("location.href") or ""
        if "studio.youtube.com/channel/" not in cur_url:
            if "accounts.google.com" not in cur_url:
                await cdp.call("Page.navigate", {"url": "https://studio.youtube.com"})
                await asyncio.sleep(4.0)
                cur_url = await cdp.eval("location.href") or ""
            if "studio.youtube.com/channel/" not in cur_url:
                await cdp.call("Page.bringToFront")
                raise RuntimeError(
                    "Finish signing into YouTube Studio in the open Chrome window (it will NOT close or reset anymore!), "
                    "then click 'Start Auto-Schedule'."
                )

        for idx, (it, slot_dt) in enumerate(zip(todo, slots), 1):
            publish_now = (idx == 1 and led_empty_initially)
            slot_str = "PUBLIC NOW" if publish_now else slot_dt.strftime("%b %d · %I:%M %p")
            job.update((idx - 0.8) / len(todo), f"[{idx}/{len(todo)}] Uploading: {it['title'][:40]} ({slot_str})")
            core.say("YT", f"[{idx}/{len(todo)}] uploading '{it['title'][:55]}' -> {slot_str}")

            cur_url = await cdp.eval("location.href") or ""
            if "studio.youtube.com/channel/" not in cur_url:
                await cdp.call("Page.navigate", {"url": "https://studio.youtube.com"})
                await asyncio.sleep(3.5)

            # Dismiss any leftover dialog
            await cdp.eval(
                """(() => {
                    const b = document.querySelector('ytcp-uploads-still-processing-dialog #close-button, ytcp-video-share-dialog #close-button, ytcp-uploads-dialog #close-button');
                    if (b) b.click();
                })()"""
            )
            await asyncio.sleep(0.8)

            # Click Create -> Upload videos
            await cdp.wait_for("document.querySelector('#create-icon, ytcp-button#create-icon, button[aria-label=\"Create\"]')")
            await cdp.eval("document.querySelector('#create-icon, ytcp-button#create-icon, button[aria-label=\"Create\"]').click()")
            await asyncio.sleep(0.8)
            await cdp.wait_for("document.querySelector('#text-item-0') || [...document.querySelectorAll('tp-yt-paper-item')].find(e => e.textContent.includes('Upload'))")
            await cdp.eval("(document.querySelector('#text-item-0') || [...document.querySelectorAll('tp-yt-paper-item')].find(e => e.textContent.includes('Upload'))).click()")
            await asyncio.sleep(1.0)

            await cdp.wait_for("document.querySelector('ytcp-uploads-file-picker input[type=\"file\"], input[type=\"file\"][name=\"Filedata\"]')")
            await cdp.set_file_input("ytcp-uploads-file-picker input[type='file'], input[type='file'][name='Filedata']", it["mp4"])
            await asyncio.sleep(3.5)

            daily_cap = await cdp.eval("document.body.innerText.includes('Daily upload limit reached')")
            if daily_cap:
                core.say("YT", f"YouTube daily upload limit reached after {scheduled_n} video(s) — verify Advanced features in Studio Settings -> Channel to unlock 50+/day!", "warn")
                break

            # Title + Description
            await cdp.set_contenteditable("#title-textarea #textbox", it["title"])
            await cdp.set_contenteditable("#description-textarea #textbox", it["description"])

            # Optional thumbnail (attach if file input exists on this video type)
            if it["thumb"] and os.path.isfile(it["thumb"]):
                has_th = await cdp.eval("Boolean(document.querySelector('ytcp-thumbnails-compact-editor input[type=\"file\"], input#file-loader'))")
                if has_th:
                    try:
                        await cdp.set_file_input("ytcp-thumbnails-compact-editor input[type='file'], input#file-loader", it["thumb"])
                        await asyncio.sleep(1.0)
                    except Exception:
                        pass

            # Not made for kids
            await cdp.wait_for('document.querySelector(\'tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]\')')
            await cdp.eval(
                """(() => {
                    const r = document.querySelector('tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]');
                    r.scrollIntoView({block: 'center'});
                    r.click();
                })()"""
            )
            await asyncio.sleep(0.6)

            # Next x 3 -> Visibility
            for _ in range(3):
                await cdp.wait_for("document.querySelector('#next-button')")
                await cdp.eval("document.querySelector('#next-button').click()")
                await asyncio.sleep(1.0)

            if publish_now:
                await cdp.wait_for('document.querySelector(\'tp-yt-paper-radio-button[name="PUBLIC"]\')')
                await cdp.eval("document.querySelector('tp-yt-paper-radio-button[name=\"PUBLIC\"]').click()")
                await asyncio.sleep(0.6)
            else:
                await cdp.eval("(document.querySelector('tp-yt-paper-radio-button[name=\"SCHEDULE\"]') || document.querySelector('#second-container-expand-button')).click()")
                await asyncio.sleep(0.8)
                await cdp.eval("document.querySelector('#datepicker-trigger').click()")
                await asyncio.sleep(0.6)
                for fmt in ("%b %d, %Y", "%d %b %Y", "%m/%d/%Y"):
                    cand_d = slot_dt.strftime(fmt).replace(" 0", " ")
                    await cdp.eval("(() => { const i = document.querySelector('ytcp-date-picker input'); if (i) i.value = ''; })()")
                    await cdp.type_into_input("ytcp-date-picker input", cand_d)
                    invalid = await cdp.eval("Boolean(document.querySelector('ytcp-date-picker tp-yt-paper-input[invalid]'))")
                    if not invalid:
                        break
                t12 = slot_dt.strftime("%I:%M %p").lstrip("0")
                t24 = slot_dt.strftime("%H:%M")
                await cdp.eval("(() => { const t = document.querySelector('#time-of-day-trigger, #time-of-day-container input'); if (t) t.click(); })()")
                await asyncio.sleep(0.5)
                picked_t = await cdp.eval(
                    f"""(() => {{
                        const norm = s => (s || '').replace(/[\\u202f\\u00a0\\s]+/g, ' ').trim().toUpperCase();
                        const hit = [...document.querySelectorAll('tp-yt-paper-item')].find(el => {{
                            const x = norm(el.textContent);
                            return x === norm({json.dumps(t12)}) || x === norm({json.dumps(t24)});
                        }});
                        if (hit) {{ hit.click(); return true; }}
                        return false;
                    }})()"""
                )
                if not picked_t:
                    await cdp.eval("(() => { const i = document.querySelector('#time-of-day-container input'); if (i) i.value = ''; })()")
                    await cdp.type_into_input("#time-of-day-container input", t12)

            # Wait until file upload completes & #done-button is enabled
            for _ in range(180):
                ready = await cdp.eval(
                    """(() => {
                        const p = document.querySelector('ytcp-video-upload-progress');
                        const txt = p ? p.innerText : '';
                        const d = document.querySelector('#done-button');
                        const dis = !d || d.hasAttribute('disabled') || d.getAttribute('aria-disabled') === 'true';
                        return !txt.includes('Uploading') && !dis;
                    })()"""
                )
                if ready:
                    break
                await asyncio.sleep(2.0)

            await cdp.eval("document.querySelector('#done-button').click()")
            await asyncio.sleep(3.5)

            for _ in range(15):
                closed = await cdp.eval(
                    """(() => {
                        const b = document.querySelector('ytcp-uploads-still-processing-dialog #close-button, ytcp-video-share-dialog #close-button, ytcp-dialog #close-button');
                        if (b && b.offsetParent !== null) { b.click(); return true; }
                        return false;
                    })()"""
                )
                if closed:
                    break
                await asyncio.sleep(1.0)
            await asyncio.sleep(1.5)

            led = core.read_json(SCHED_LEDGER, {})
            led[it["sha256"]] = {
                "file": os.path.basename(it["mp4"]),
                "day": day_name,
                "title": it["title"],
                "scheduled_for": "NOW" if publish_now else slot_dt.isoformat(),
                "scheduled_ts": time.time() if publish_now else slot_dt.timestamp(),
                "uploaded_at": time.time(),
            }
            core.write_json(SCHED_LEDGER, led)
            scheduled_n += 1
            core.say("YT", f"{'published LIVE' if publish_now else 'scheduled'} #{idx}: '{it['title'][:50]}' ({slot_str})", "good")
            job.update(idx / len(todo), f"Done {idx}/{len(todo)}: {slot_str}")

    return {"scheduled": scheduled_n, "day": day_name}


def autopost_day_job(job, day_name="S-Tier Day 01", spacing_min=30, max_videos=15):
    day_dir = os.path.join(core.DIRS["youtube"], day_name)
    if not os.path.isdir(day_dir):
        raise RuntimeError(f"Day folder not found: {day_dir}. Build a Day pack first.")
    items = parse_day_folder(day_dir)
    if not items:
        raise RuntimeError(f"No .mp4 files in {day_name}.")
    led = core.read_json(SCHED_LEDGER, {})
    led_empty_initially = len(led) == 0
    todo = []
    for it in items:
        sha = pipeline.file_sha256(it["mp4"])
        if sha in led:
            continue
        it["sha256"] = sha
        todo.append(it)
        if len(todo) >= max_videos:
            break
    if not todo:
        core.say("YT", f"All videos in '{day_name}' are already scheduled on YouTube!", "good")
        return {"scheduled": 0, "skipped": len(items)}
    slots = _next_schedule_slots(len(todo), spacing_min=spacing_min, start_in_min=spacing_min)
    return asyncio.run(_run_cdp_autopost(job, day_name, todo, slots, led_empty_initially))
