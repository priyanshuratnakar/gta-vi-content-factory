"""Turns moments into finished vertical reels and the long video into parts."""
import os, re, sys, platform, threading, textwrap
from . import core

_USE_VT = sys.platform == "darwin" and platform.machine() == "arm64"
_VT_SEM = threading.Semaphore(2)

def _esc(t):
    return (t.replace("\\", "\\\\").replace("'", "’").replace('"', "”").replace(":", "\\:")
             .replace("%", "\\%").replace(",", "\\,"))

def reel_filter(hook, header, credit=""):
    """1080x1920: video in the middle, '<header>' + hook above it, inside the safe zone."""
    font = core.FONT_BOLD.replace(":", r"\:")
    hook = re.sub(r"\s+", " ", (hook or "").strip())
    for wrap_w, fs, lh, max_l in ((27, 54, 68, 3), (31, 48, 60, 4), (36, 42, 52, 5)):
        lines = textwrap.wrap(hook, wrap_w)
        if len(lines) <= max_l:
            break
    else:
        lines = textwrap.wrap(hook[:155].rsplit(" ", 1)[0] + "…", 36)[:5]
        fs, lh = 42, 52
    head, gap = 72, 26
    top = max(130, 626 - (head + gap + len(lines) * lh))
    parts = ["fps=30", "scale=1080:-2:flags=fast_bilinear", "pad=1080:1920:0:(oh-ih)/2:black"]
    if header:
        parts.append(f"drawtext=fontfile='{font}':text='{_esc(header)}':fontcolor=white:fontsize=58:x=(w-text_w)/2:y={top}")
    for k, line in enumerate(lines):
        parts.append(f"drawtext=fontfile='{font}':text='{_esc(line)}':fontcolor=white:fontsize={fs}:x=(w-text_w)/2:y={top+head+gap+k*lh}")
    if credit:
        parts.append(f"drawtext=fontfile='{font}':text='{_esc(credit)}':fontcolor=white@0.8:fontsize=30:x=(w-text_w)/2:y=1330")
    parts.append("format=yuv420p")
    return ",".join(parts)

def reel(video, start, length, hook, out, s):
    ff = core.which("ffmpeg") or "ffmpeg"
    tmp = f"{out}.{os.getpid()}.{threading.get_ident()}.part.mp4"
    vf = reel_filter(hook, s["header"], s.get("credit_line", ""))
    base = [ff, "-hide_banner", "-loglevel", "error", "-y", "-ss", str(start), "-t", str(length), "-i", video, "-vf", vf, "-r", "30"]
    acodec = "aac_at" if _USE_VT else "aac"
    tail = ["-c:a", acodec, "-b:a", "128k", "-ar", "44100", "-ac", "2", "-movflags", "+faststart", tmp]
    vt_ran = False
    try:
        if _USE_VT and _VT_SEM.acquire(blocking=False):
            vt_ran = True
            try:
                core.run(base + ["-c:v", "h264_videotoolbox", "-q:v", "56", "-profile:v", "high",
                                 "-prio_speed", "1", "-allow_sw", "1"] + tail, timeout=600)
                os.replace(tmp, out)
                return out
            except Exception:
                tail = ["-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2", "-movflags", "+faststart", tmp]
            finally:
                _VT_SEM.release()
        x264_extra = ["-threads", "4"] if (_USE_VT and not vt_ran) else []
        core.run(base + ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-profile:v", "high",
                         "-pix_fmt", "yuv420p"] + x264_extra + tail, timeout=900)
        os.replace(tmp, out)
        return out
    finally:
        if os.path.exists(tmp):
            try: os.remove(tmp)
            except OSError: pass

def long_parts(job, video, minutes, outdir, title_fmt):
    """Cuts the full video into ~N-minute parts (fast: no re-encode, cuts on keyframes)."""
    ff = core.which("ffmpeg") or "ffmpeg"
    os.makedirs(outdir, exist_ok=True)
    seg_sec = minutes * 60
    cached, idx = [], 1
    while True:
        final = os.path.join(outdir, core.safe_name(title_fmt.format(n=idx)) + ".mp4")
        if not os.path.isfile(final) or os.path.getsize(final) < 10000:
            break
        cover = final[:-4] + " cover.jpg"
        cached.append({"n": idx, "file": final, "duration": core.duration(final),
                       "title": title_fmt.format(n=idx), "posted": {}, "cover": cover if os.path.isfile(cover) else ""})
        idx += 1
    if cached:
        vdur = core.duration(video)
        total_d = sum(c["duration"] for c in cached)
        seg_ok = (abs(cached[0]["duration"] - seg_sec) < 60) if (len(cached) > 1 or vdur > seg_sec * 1.25) else True
        if seg_ok and -15 <= (vdur - total_d) <= seg_sec * 0.25 + 15:
            return cached
        for f in os.listdir(outdir):
            if f.endswith(".mp4"):
                core.move_to_trash(os.path.join(outdir, f))
    pattern = os.path.join(outdir, "part_%03d.mp4")
    job.update(None, f"Cutting the full video into {minutes}-minute parts")
    core.run([ff, "-hide_banner", "-loglevel", "error", "-y", "-i", video, "-c", "copy", "-map", "0:v:0", "-map", "0:a:0?",
              "-f", "segment", "-segment_time", str(seg_sec), "-reset_timestamps", "1", pattern], timeout=3600)
    parts = []
    files = sorted(f for f in os.listdir(outdir) if re.match(r"part_\d{3}\.mp4$", f))
    for i, f in enumerate(files, 1):
        p = os.path.join(outdir, f)
        d = core.duration(p)
        if d < seg_sec * 0.25 and parts:          # tiny tail: not worth its own video
            core.move_to_trash(p); continue
        final = os.path.join(outdir, core.safe_name(title_fmt.format(n=i)) + ".mp4")
        os.replace(p, final)
        parts.append({"n": i, "file": final, "duration": d, "title": title_fmt.format(n=i), "posted": {}, "cover": ""})
    return parts

def still(video, at, out):
    """A frame for cover backgrounds (scaled to 1280 wide)."""
    ff = core.which("ffmpeg") or "ffmpeg"
    core.run([ff, "-hide_banner", "-loglevel", "error", "-y", "-ss", str(at), "-i", video, "-frames:v", "1",
              "-vf", "scale=1280:-2", "-q:v", "2", out])
    return out
