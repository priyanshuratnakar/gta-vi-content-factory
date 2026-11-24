"""YouTube: builds priority-ordered 'Day' folders (S-Tier Shorts + up to 2 long parts + thumbnails + descriptions)
ready to drag into YouTube Studio."""
import os, re, csv, time
from . import core, pipeline, posting

LOG = os.path.join(core.DIRS["logs"], "youtube.csv")

def placed():
    if not os.path.exists(LOG): return set()
    with open(LOG, newline="", encoding="utf-8") as f:
        return {r["item"] for r in csv.DictReader(f)}

def fit_title(t, tail=" #Shorts", limit=100):
    t = re.sub(r"\s+", " ", t.replace(":", " -").replace("/", "-").replace("\\", "-").replace('"', "'")).strip()
    if len(t) + len(tail) <= limit: return t + tail
    return t[:limit - len(tail) - 1].rsplit(" ", 1)[0].rstrip(" ,.-") + "…" + tail

def build_days(days=7, prefix="Day"):
    """Fill up to `days` Day folders ahead in strict tier/priority order (S-Tier first, alternating up to 2 long parts/day)."""
    s = core.load_settings()
    per_day = int(s.get("youtube_per_day", 12))
    done = placed()
    todo = [it for it in pipeline.queue() if it["approved"] and it["id"] not in done and os.path.isfile(it.get("file", ""))]
    todo.sort(key=lambda it: (-posting.item_priority(it), it.get("created", 0)))
    reels = [it for it in todo if it["kind"] == "reel"]
    longs = [it for it in todo if it["kind"] == "long"]
    root = core.DIRS["youtube"]
    os.makedirs(root, exist_ok=True)
    rx = re.compile(rf"^{re.escape(prefix)} (\d+)$")
    existing = sorted(d for d in os.listdir(root) if rx.match(d))
    full = [d for d in existing if os.listdir(os.path.join(root, d))]
    nxt = (max(int(rx.match(d).group(1)) for d in existing) + 1) if existing else 1
    made = []
    new_log = not os.path.exists(LOG)
    with open(LOG, "a", newline="", encoding="utf-8") as lf:
        w = csv.writer(lf)
        if new_log: w.writerow(["time", "day", "item", "kind", "title"])
        target_new = days if prefix != "Day" else max(0, days - len(full))
        for _ in range(target_new):
            batch = []
            day_longs = 0
            while len(batch) < per_day and (reels or longs):
                if reels: batch.append(reels.pop(0))
                if longs and len(batch) < per_day and day_longs < 2 and len(batch) % 5 == 4:
                    batch.append(longs.pop(0)); day_longs += 1
                elif not reels and longs and day_longs < 2:
                    batch.append(longs.pop(0)); day_longs += 1
            if not batch: break
            day_name = f"{prefix} {nxt:02d}"
            d = os.path.join(root, day_name); os.makedirs(d, exist_ok=True)
            notes = []
            for order_i, it in enumerate(batch, 1):
                raw_t = it["title"]
                title = fit_title(raw_t) if it["kind"] == "reel" else fit_title(raw_t, tail="")
                fname = f"{order_i:02d} - {core.safe_name(title, 110)}"
                core.fast_copy(it["file"], os.path.join(d, fname + ".mp4"))
                cov = it.get("cover") or ""
                if (not cov or not os.path.isfile(cov)) and it["kind"] == "reel":
                    auto_cov = it["file"][:-4] + " cover.jpg"
                    if not os.path.isfile(auto_cov):
                        try:
                            from . import covers
                            covers.make_reel_cover(it["file"], it.get("hook") or it["title"], auto_cov, s)
                        except Exception:
                            auto_cov = ""
                    cov = auto_cov
                if cov and os.path.isfile(cov):
                    core.fast_copy(cov, os.path.join(d, fname + " - thumbnail.jpg"))
                desc = it["captions"].get("youtube") or it["captions"].get("facebook", "")
                notes.append(f"[{order_i:02d}] {title}\n{desc}\n" + "-" * 48)
                w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), day_name, it["id"], it["kind"], title])
            with open(os.path.join(d, "_titles and descriptions.txt"), "w", encoding="utf-8") as f:
                f.write("\n".join(notes))
            made.append(day_name); nxt += 1
    core.say("YT", f"made {len(made)} YouTube pack(s): {', '.join(made) or 'nothing new to add'}", "good" if made else "info")
    return made
