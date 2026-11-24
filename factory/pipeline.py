"""The whole 'long video in, posts out' flow, plus the posting queue it feeds."""
import os, re, time, uuid, hashlib, inspect, threading, concurrent.futures
from . import core, ingest, moments, render, covers, jobs

QUEUE_FILE = os.path.join(core.HOME_DIR, "queue.json")
_state_lock = threading.RLock()

_IG_VARIANTS = [
    "{hook}\n\nfollow @{handle} for daily {game} chaos\n\n{keywords}\n\n{hashtags}",
    "{hook}\n\nsave this for your first day in Vice City — @{handle}\n\n{hashtags}",
    "{hook}\n\nrate this 1-10 in the comments · more on @{handle}\n\n{keywords}\n\n{hashtags}",
    "wait til the end... {hook}\n\n@{handle} · {hashtags}",
    "{hook}\n\nRockstar went crazy on the details (@{handle})\n\n{hashtags}",
]
_FB_VARIANTS = [
    "{hook}\n\nFollow {page} for daily {game} clips!\n\n{hashtags}",
    "{hook}\n\nTag someone you're playing {game} with day one · {page}\n\n{hashtags}",
    "{hook}\n\nWhat's the first thing you're trying in Vice City? Follow {page}!\n\n{hashtags}",
    "{hook}\n\n{page} · daily {game} highlights\n\n{hashtags}",
    "{hook}\n\nOnly in {game}. Follow {page} for more!\n\n{hashtags}",
]
_TH_VARIANTS = [
    "{hook}",
    "{hook}\n\nhonestly GTA VI physics are unreal",
    "{hook}\n\nwhat's the first thing you're doing in Vice City?",
    "{hook}\n\nno way they actually put this in the game",
    "{hook} — rate this clip 1-10",
]

def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def queue():
    return core.read_json(QUEUE_FILE, [])

def save_queue(q):
    core.write_json(QUEUE_FILE, q)

def hook_line(s, hook, emoji=""):
    h = (hook or "").strip()
    if h.lower().startswith(("in gta vi", "in gta 6")):
        return h + (f" {emoji}" if emoji else "")
    return f"{s['header']}, {h}".strip() + (f" {emoji}" if emoji else "")

def captions(s, hook, emoji="", keywords=(), credit="", variant=None):
    hl = hook_line(s, hook, emoji)
    handle = (s.get("handle") or s.get("page_name") or "gta6clips").lstrip("@")
    page = s.get("page_name") or handle
    vals = {"hook": hl, "handle": handle, "page": page,
            "game": s.get("game", "GTA VI"), "hashtags": s.get("hashtags", ""),
            "keywords": ", ".join(list(keywords) + [f"{s['game'].lower()} gameplay", f"{s['game'].lower()} clips"])}
    idx = (sum(ord(c) for c in hl) % 5) if variant is None else (int(variant) % 5)
    rotate = bool(s.get("caption_rotation", True))
    templates = {
        "instagram": _IG_VARIANTS[idx] if rotate else s.get("caption_instagram", "{hook}"),
        "facebook":  _FB_VARIANTS[idx] if rotate else s.get("caption_facebook", "{hook}"),
        "threads":   _TH_VARIANTS[idx] if rotate else s.get("caption_threads", "{hook}"),
    }
    out = {}
    for p, t in templates.items():
        for k, v in vals.items(): t = t.replace("{" + k + "}", v)
        t = "\n".join(l for l in t.splitlines())
        if credit: t += f"\n\n{credit}"
        out[p] = t.strip()[: 2200 if p != "threads" else 500]
    out["youtube"] = f"{hl}\n\nSubscribe to @{handle} for daily {vals['game']} clips & walkthroughs!\n\n{vals['hashtags']} #Shorts".strip()
    return out

def import_external_folder(folder_path, tier="S", priority_base=1000):
    """Import pre-rendered 9:16 clips (e.g. 00-GLOBAL-S-TIER or Drop ready reels here) into the priority shipping queue."""
    if not os.path.isdir(folder_path):
        return {"added": 0, "updated": 0, "total": 0}
    files = sorted(f for f in os.listdir(folder_path) if f.lower().endswith((".mp4", ".mov")) and not f.startswith("."))
    if not files:
        return {"added": 0, "updated": 0, "total": 0}
    with _state_lock:
        s = core.load_settings()
        q = queue()
        by_sha = {it.get("sha256"): it for it in q if it.get("sha256")}
        added, updated = 0, 0
        os.makedirs(core.DIRS["queue"], exist_ok=True)
        for idx, fname in enumerate(files, 1):
            src = os.path.join(folder_path, fname)
            if not os.path.isfile(src) or os.path.getsize(src) < 10_000:
                continue
            sha = file_sha256(src)
            stem = os.path.splitext(fname)[0]
            m_rank = re.match(r"^(\d+)\s*-\s*(?:\[([^\]]+)\]\s*)?(.*)$", stem)
            if m_rank:
                rank_n = int(m_rank.group(1))
                tag_label = m_rank.group(2) or tier
                clean_title = m_rank.group(3).strip()
            else:
                rank_n = idx
                tag_label = tier
                clean_title = re.sub(r"^\d{4,6}s?\s*[-_]?\s*", "", stem)
                clean_title = re.sub(r"\s*\[score[^\]]*\]\s*$", "", clean_title, flags=re.I).strip()
            prio = max(100, priority_base - rank_n)
            staged = os.path.join(core.DIRS["queue"], f"stier_{rank_n:02d}_{core.safe_name(clean_title, 65)}.mp4")
            if not os.path.exists(staged) or os.path.getsize(staged) != os.path.getsize(src):
                core.fast_copy(src, staged)
            cover_jpg = staged[:-4] + " cover.jpg"
            src_sidecar = os.path.splitext(src)[0] + " - thumbnail.jpg"
            if os.path.isfile(src_sidecar):
                core.fast_copy(src_sidecar, cover_jpg)
            elif not os.path.isfile(cover_jpg):
                try: covers.make_reel_cover(staged, clean_title, cover_jpg, s)
                except Exception: cover_jpg = ""
            caps = captions(s, clean_title, keywords=["gta 6 highlights", "vice city"], variant=rank_n)
            existing = by_sha.get(sha)
            if existing:
                existing.update(file=staged, cover=cover_jpg if os.path.isfile(cover_jpg) else "",
                                priority=prio, tier=tier, source_tag=tag_label, approved=True)
                if not any(v.get("status") == "posted" for v in existing.get("posted", {}).values()):
                    existing.update(title=f"In {s['game']}, {clean_title}" if not clean_title.lower().startswith("in gta") else clean_title,
                                    hook=clean_title, captions=caps)
                updated += 1
            else:
                title_line = clean_title if clean_title.lower().startswith("in gta") else f"{s['header']} — {clean_title}"
                item = {
                    "id": "stier_" + sha[:8],
                    "project": "global_s_tier",
                    "ref": f"rank_{rank_n:02d}",
                    "kind": "reel",
                    "tier": tier,
                    "priority": prio,
                    "source_tag": tag_label,
                    "sha256": sha,
                    "file": staged,
                    "hook": clean_title,
                    "title": title_line,
                    "captions": caps,
                    "cover": cover_jpg if os.path.isfile(cover_jpg) else "",
                    "approved": True,
                    "created": time.time(),
                    "posted": {},
                }
                q.append(item)
                by_sha[sha] = item
                added += 1
        q.sort(key=lambda it: (-int(it.get("priority") or 0), it.get("created", 0)))
        save_queue(q)
    core.say("queue", f"Global {tier}-Tier sync: +{added} added, {updated} refreshed (top of shipping queue)", "good")
    return {"added": added, "updated": updated, "total": len(files)}

def _merge_user_edits(pid, target_moments):
    disk = ingest.load(pid)
    if not disk:
        return
    by_id = {m["id"]: m for m in disk.get("moments", [])}
    for m in target_moments:
        dm = by_id.get(m["id"])
        if not dm:
            continue
        if dm.get("status") in ("approved", "rejected"):
            m["status"] = dm["status"]
        if dm.get("posted"):
            m["posted"] = dm["posted"]
        if dm.get("hook") and dm["hook"] != m.get("hook") and dm.get("reel") and os.path.exists(dm["reel"]):
            m["hook"], m["emoji"], m["reel"] = dm["hook"], dm.get("emoji", m.get("emoji", "")), dm["reel"]

def process(job, pid):
    """Analyse -> live-stream reels -> cut long parts + covers -> queue. Safe to re-run."""
    p = ingest.load(pid)
    if not p.get("video") or not os.path.isfile(p["video"]):
        src = (p.get("source") or "").strip()
        with jobs.dl_sem:
            if src.startswith(("http://", "https://")):
                ingest.from_link(job, src, pid=pid)
            elif src and os.path.isfile(src):
                ingest.from_file(job, src, move=False, pid=pid)
            else:
                raise RuntimeError("This project's source video is missing. Add the video link or file again.")
    if jobs.clip_lock.locked():
        job.update(0.95, "Downloaded · waiting for GPU clipper")
    with jobs.clip_lock:
        return _process_loaded(job, pid)

def _process_loaded(job, pid):
    s = core.load_settings()
    p = ingest.load(pid)

    prev_moments = list(p.get("moments", []))
    reels_dir = os.path.join(ingest.project_dir(pid), "reels")
    os.makedirs(reels_dir, exist_ok=True)
    save_lock = _state_lock
    streamed = []
    stream_futs = []
    streamed_done = 0

    with save_lock:
        p["moments"] = []
        p["status"] = "analysing"
        ingest.save(p)

    def _stream_render(m):
        nonlocal streamed_done
        render_moment(p, m, s, reels_dir)
        with save_lock:
            streamed_done += 1
            _merge_user_edits(pid, p["moments"])
            ingest.save(p)
            sync_queue(p)
        if m.get("reel"):
            core.say("reel", f"ready ({streamed_done}): {m['hook'][:55]}", "good")

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as render_pool:
        def _on_moment(m):
            with save_lock:
                streamed.append(m)
                if all(x["id"] != m["id"] for x in p["moments"]):
                    p["moments"].append(m)
                    p["moments"].sort(key=lambda x: x["start"])
                _merge_user_edits(pid, p["moments"])
                ingest.save(p)
            stream_futs.append(render_pool.submit(_stream_render, m))

        params = inspect.signature(moments.find).parameters
        if "on_moment" in params or any(v.kind == inspect.Parameter.VAR_KEYWORD for v in params.values()):
            found = moments.find(job, p, s, on_moment=_on_moment)
        else:
            found = moments.find(job, p, s)

        for fut in stream_futs:
            fut.result()

        with save_lock:
            _merge_user_edits(pid, found)
            p["moments"] = found
            p["status"] = "rendering"
            ingest.save(p)

        remaining = [m for m in found if not m.get("reel") or not os.path.exists(m["reel"]) or os.path.getsize(m["reel"]) < 10000]
        done_n = len(found) - len(remaining)

        def _render_rest(m):
            nonlocal done_n
            render_moment(p, m, s, reels_dir)
            with save_lock:
                done_n += 1
                job.update(0.75 + 0.1 * done_n / max(1, len(found)), f"Rendering reel {done_n} of {len(found)}")
                _merge_user_edits(pid, p["moments"])
                ingest.save(p)
                sync_queue(p)

        if remaining:
            list(render_pool.map(_render_rest, remaining))

    kept_reels = {m.get("reel") for m in found if m.get("reel")}
    posted_files = {it.get("file") for it in queue()
                    if it.get("project") == pid and any(v.get("status") == "posted" for v in it.get("posted", {}).values())}
    for old_m in prev_moments + streamed:
        r = old_m.get("reel")
        if r and r not in kept_reels and r not in posted_files and os.path.exists(r):
            core.move_to_trash(r)

    if s.get("make_long") and p["duration"] > s["long_minutes"] * 60 * 0.8:
        job.update(0.86, "Cutting long-form parts")
        prev_long = {lp["n"]: lp for lp in p.get("long_parts", [])}
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as lpool:
            f_bg = lpool.submit(covers.background_for, p, s, render.still)
            parts = render.long_parts(job, p["video"], s["long_minutes"], os.path.join(ingest.project_dir(pid), "long"),
                                      f"{s['game']} Full Gameplay Walkthrough - Part {{n}}")
            bg = f_bg.result()
            job.update(0.92, f"Making covers for {len(parts)} parts")
            def _make_cover(part):
                old = prev_long.get(part["n"])
                if old and old.get("file") == part["file"]:
                    part["approved"] = old.get("approved", False)
                    part["posted"] = old.get("posted", {})
                part["cover"] = covers.make(bg, part["n"], part["file"][:-4] + " cover.jpg", s)
            list(lpool.map(_make_cover, parts))
        p["long_parts"] = parts
    else:
        p["long_parts"] = []

    with save_lock:
        _merge_user_edits(pid, found)
        p["moments"] = found
        p["status"] = "review"
        ingest.save(p)
        sync_queue(p)
    core.say("factory", f"'{p['title']}': {sum(1 for m in found if m.get('reel'))} reels ({sum(1 for m in found if not m.get('reel'))} need a hook) and {len(p['long_parts'])} long parts ready to review")
    return pid

def render_moment(p, m, s, reels_dir=None, force=False):
    reels_dir = reels_dir or os.path.join(ingest.project_dir(p["id"]), "reels")
    os.makedirs(reels_dir, exist_ok=True)
    if not m["hook"]:
        m["status"] = "needs_hook"; return
    out = os.path.join(reels_dir, f"{int(m['start']):06d} {core.safe_name(m['hook'], 60)}.mp4")
    if m.get("reel") and os.path.exists(m["reel"]) and m["reel"] != out:
        core.move_to_trash(m["reel"])
    if force or not os.path.exists(out) or os.path.getsize(out) < 10000:
        render.reel(p["video"], m["start"], s["reel_seconds"], m["hook"], out, s)
    m["reel"] = out
    cov_out = out[:-4] + " cover.jpg"
    if force or not os.path.exists(cov_out) or os.path.getsize(cov_out) < 2000:
        try:
            covers.make_reel_cover(out, m["hook"], cov_out, s, source_video=p.get("video"),
                                   start_sec=m["start"], length_sec=s["reel_seconds"])
        except Exception:
            cov_out = ""
    m["cover"] = cov_out if cov_out and os.path.exists(cov_out) else ""
    if m["status"] == "needs_hook": m["status"] = "review"

def sync_queue(p):
    """Mirror a project's reels/parts into the posting queue, keeping anything already posted."""
    with _state_lock:
        s = core.load_settings()
        q = queue()
        valid_refs = ({m["id"] for m in p.get("moments", []) if m.get("reel")}
                      | {f"long{part['n']}" for part in p.get("long_parts", [])})
        kept = []
        for it in q:
            if it["project"] != p["id"] or it["ref"] in valid_refs:
                kept.append(it)
            elif any(v.get("status") == "posted" for v in it.get("posted", {}).values()):
                it["approved"] = False
                kept.append(it)
        q = kept
        by_key = {(it["project"], it["ref"]): it for it in q}
        for m in p.get("moments", []):
            if not m.get("reel"): continue
            it = by_key.get((p["id"], m["id"]))
            caps = captions(s, m["hook"], m.get("emoji", ""), m.get("keywords", []), s.get("credit_line", ""))
            rcover = m.get("cover") or (m["reel"][:-4] + " cover.jpg" if os.path.isfile(m["reel"][:-4] + " cover.jpg") else "")
            if it:
                it["score"] = m.get("score", 0)
                it["priority"] = int(it.get("priority") or round(float(m.get("score") or 5) * 10))
                if not any(v.get("status") == "posted" for v in it["posted"].values()):
                    it.update(file=m["reel"], cover=rcover, hook=m["hook"], title=hook_line(s, m["hook"], m.get("emoji", "")), captions=caps)
                it["approved"] = m["status"] == "approved"
            else:
                q.append({"id": uuid.uuid4().hex[:10], "project": p["id"], "ref": m["id"], "kind": "reel",
                          "score": m.get("score", 0), "priority": int(round(float(m.get("score") or 5) * 10)),
                          "file": m["reel"], "hook": m["hook"], "title": hook_line(s, m["hook"], m.get("emoji", "")),
                          "captions": caps, "cover": rcover, "approved": m["status"] == "approved",
                          "created": time.time(), "posted": {}})
        for part in p.get("long_parts", []):
            ref = f"long{part['n']}"
            it = by_key.get((p["id"], ref))
            desc = (f"{part['title']}\n\npart {part['n']} of the full {s['game']} playthrough. no commentary, just the game.\n\n"
                    f"{('Follow ' + s['page_name'] + ' for the next part!') if s.get('page_name') else ''}\n\n{s.get('hashtags','')}").strip()
            if not it:
                q.append({"id": uuid.uuid4().hex[:10], "project": p["id"], "ref": ref, "kind": "long",
                          "file": part["file"], "hook": part["title"], "title": part["title"], "cover": part.get("cover", ""),
                          "captions": {"facebook": desc, "youtube": desc}, "approved": part.get("approved", False),
                          "created": time.time(), "posted": {}})
            else:
                if not any(v.get("status") == "posted" for v in it["posted"].values()):
                    it.update(file=part["file"], hook=part["title"], title=part["title"],
                              cover=part.get("cover", ""), captions={"facebook": desc, "youtube": desc})
                it["approved"] = part.get("approved", False)
        save_queue(q)

def set_moment(pid, mid, approved=None, hook=None, emoji=None):
    with _state_lock:
        p = ingest.load(pid)
        m = next(x for x in p["moments"] if x["id"] == mid)
        need_rerender = hook is not None and hook.strip() != m["hook"]
        if need_rerender:
            m["hook"] = hook.strip().removeprefix("In GTA VI,").strip()
            if emoji is not None: m["emoji"] = emoji
        elif emoji is not None and emoji != m.get("emoji"):
            m["emoji"] = emoji
        if approved is not None:
            m["status"] = "approved" if approved else "rejected"
        ingest.save(p); sync_queue(p)
    if need_rerender:
        def rerender(job):
            render_moment(p, m, core.load_settings(), force=True)
            with _state_lock:
                cur = ingest.load(pid)
                cm = next((x for x in cur["moments"] if x["id"] == mid), None)
                if cm:
                    cm["hook"], cm["emoji"], cm["reel"] = m["hook"], m.get("emoji", ""), m["reel"]
                    if cm["status"] == "needs_hook": cm["status"] = "review"
                    ingest.save(cur); sync_queue(cur)
        jobs.start("render", f"Re-render: {m['hook'][:40]}", rerender)
    return m

def approve_all(pid, include_long=True):
    with _state_lock:
        p = ingest.load(pid)
        for m in p["moments"]:
            if m["status"] in ("review",) and m.get("reel"): m["status"] = "approved"
        if include_long:
            for part in p.get("long_parts", []): part["approved"] = True
        p["status"] = "approved"
        ingest.save(p); sync_queue(p)
