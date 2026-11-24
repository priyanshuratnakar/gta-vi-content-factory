"""Finds the best moments in a long video.

1. Cheap signals over the whole video: loudness + 16kHz audio extraction in one pass, parallel with keyframe scene cuts (cached on disk).
2. Picks candidate windows BEFORE Whisper so local AI/ASR never wastes work on the rest of the timeline.
3. Transcribes ONLY the active candidate windows in one compact stitched buffer with MLX Whisper, caches per-window transcripts, and frees GPU RAM before loading Ollama.
4. Prefetches lightweight 3x2 contact sheets (960x360) on 4 threads and streams ready moments via on_moment so rendering can run in parallel."""
import os, re, gc, wave, uuid, importlib, threading, statistics, concurrent.futures
from . import core, ai

WHISPER_REPO = "mlx-community/whisper-small-mlx"


def _video_sig(video):
    st = os.stat(video)
    return f"{st.st_size}:{int(st.st_mtime)}"


def _ensure_wav(video, wav_out):
    if wav_out and os.path.exists(wav_out) and os.path.getsize(wav_out) > 1000:
        try:
            with wave.open(wav_out, "rb") as w:
                if w.getframerate() == 16000 and w.getsampwidth() == 2 and w.getnframes() > 0:
                    return wav_out
        except Exception:
            pass
    if not wav_out:
        return None
    ff = core.which("ffmpeg") or "ffmpeg"
    tmp = f"{wav_out}.{threading.get_ident()}.tmp.wav"
    core.run([ff, "-hide_banner", "-nostats", "-y", "-i", video, "-vn",
              "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", tmp], check=False)
    if os.path.exists(tmp):
        os.replace(tmp, wav_out)
    return wav_out if os.path.exists(wav_out) else None


def loudness_and_wav(video, wav_out=None):
    """dB RMS for every second of audio, optionally writing 16kHz mono WAV in the same pass for Whisper."""
    ff = core.which("ffmpeg") or "ffmpeg"
    rms_chain = "aresample=8000,asetnsamples=n=8000,astats=metadata=1:reset=1,ametadata=mode=print:key=lavfi.astats.Overall.RMS_level:file=-"
    need_wav = bool(wav_out) and not (os.path.exists(wav_out) and os.path.getsize(wav_out) > 1000)
    if need_wav:
        tmp = wav_out + ".tmp.wav"
        r = core.run([ff, "-hide_banner", "-nostats", "-y", "-i", video, "-vn",
                      "-filter_complex", f"[0:a]asplit=2[a1][a2];[a1]{rms_chain}[o1];[a2]aresample=16000[o2]",
                      "-map", "[o1]", "-f", "null", "-", "-map", "[o2]", "-ac", "1", "-c:a", "pcm_s16le", tmp], check=False)
        if os.path.exists(tmp):
            os.replace(tmp, wav_out)
    else:
        r = core.run([ff, "-hide_banner", "-nostats", "-i", video, "-vn", "-af", rms_chain, "-f", "null", "-"], check=False)
    vals = []
    for line in (r.stdout or "").splitlines():
        if "RMS_level=" in line:
            v = line.split("=", 1)[1].strip()
            vals.append(-90.0 if v in ("-inf", "inf", "nan") else max(-90.0, float(v)))
    return vals


def loudness(video, job=None):
    return loudness_and_wav(video)


def scene_cuts(video):
    ff = core.which("ffmpeg") or "ffmpeg"
    r = core.run([ff, "-hide_banner", "-nostats", "-skip_frame", "nokey", "-i", video, "-an", "-vf",
                  "scale=160:-2,select='gt(scene,0.22)',metadata=print:file=-", "-f", "null", "-"], check=False)
    return [float(m.group(1)) for m in re.finditer(r"pts_time:([\d.]+)", r.stdout or "")]


def _free_mlx_memory():
    try:
        mod = importlib.import_module("mlx_whisper.transcribe")
        if hasattr(mod, "ModelHolder"):
            mod.ModelHolder.model = None
            mod.ModelHolder.model_path = None
    except Exception:
        pass
    gc.collect()
    try:
        import mlx.core as mx  # type: ignore
        if hasattr(mx, "clear_cache"):
            mx.clear_cache()
        elif hasattr(mx, "metal") and hasattr(mx.metal, "clear_cache"):
            mx.metal.clear_cache()
    except Exception:
        pass


def transcribe(audio_or_video):
    """Optional speech-to-text. Uses mlx-whisper on Apple Silicon or faster-whisper if installed; else nothing."""
    try:
        import mlx_whisper  # type: ignore
        try:
            res = mlx_whisper.transcribe(
                audio_or_video,
                path_or_hf_repo=WHISPER_REPO,
                temperature=0.0,
                condition_on_previous_text=False,
                no_speech_threshold=0.55,
            )
            return [(float(s["start"]), float(s["end"]), s["text"]) for s in res.get("segments", [])]
        finally:
            _free_mlx_memory()
    except Exception:
        pass
    try:
        from faster_whisper import WhisperModel  # type: ignore
        model = WhisperModel("small", compute_type="int8")
        segs, _ = model.transcribe(audio_or_video, vad_filter=True)
        return [(float(s.start), float(s.end), s.text) for s in segs]
    except Exception:
        return []


def transcribe_candidates(video, wav_path, loud, pool, length, work, vsig=None, unload_model=None):
    """Slices only non-silent [start, start+length] windows from audio16k.wav into one stitched buffer,
    transcribes with fast greedy mlx-whisper, maps segments back to original video timestamps, and caches per window."""
    if not pool:
        return []
    vsig = vsig or _video_sig(video)
    tx_file = os.path.join(work, "transcripts.json")
    tx_data = core.read_json(tx_file, None)
    if not isinstance(tx_data, dict) or tx_data.get("vsig") != vsig or not isinstance(tx_data.get("windows"), dict):
        tx_data = {"vsig": vsig, "windows": {}}
    win_cache = tx_data["windows"]

    todo = []
    dirty = False
    ilen = int(length)
    for st in pool:
        wkey = f"{int(round(st))}:{ilen}"
        if wkey in win_cache:
            continue
        seg_loud = loud[int(st):int(st) + ilen] if loud else []
        if seg_loud and (sum(1 for v in seg_loud if v < -55) / len(seg_loud)) > 0.85:
            win_cache[wkey] = []
            dirty = True
        else:
            todo.append((float(st), wkey))

    if todo:
        src_wav = _ensure_wav(video, wav_path)
        if src_wav:
            try:
                import numpy as np  # type: ignore
                clips, spans, cursor = [], [], 0.0
                with wave.open(src_wav, "rb") as w:
                    sr = w.getframerate()
                    nch = w.getnchannels()
                    total = w.getnframes()
                    sep = np.zeros(int(0.5 * sr), dtype=np.float32)
                    for st, wkey in todo:
                        i0 = max(0, min(int(round(st * sr)), total))
                        i1 = max(i0, min(int(round((st + length) * sr)), total))
                        if i1 <= i0:
                            win_cache[wkey] = []
                            dirty = True
                            continue
                        w.setpos(i0)
                        arr = np.frombuffer(w.readframes(i1 - i0), dtype=np.int16)
                        if nch > 1:
                            arr = arr.reshape(-1, nch).mean(axis=1)
                        clip = arr.astype(np.float32) / 32768.0
                        if clips:
                            clips.append(sep)
                            cursor += 0.5
                        clip_dur = len(clip) / float(sr)
                        spans.append((cursor, cursor + clip_dur, st, wkey))
                        clips.append(clip)
                        cursor += clip_dur

                if spans:
                    stitched_np = np.concatenate(clips)
                    del clips
                    if unload_model:
                        ai.unload_ollama(unload_model)
                    raw_segs = None
                    try:
                        import mlx_whisper  # type: ignore
                        try:
                            res = mlx_whisper.transcribe(
                                stitched_np,
                                path_or_hf_repo=WHISPER_REPO,
                                temperature=0.0,
                                condition_on_previous_text=False,
                                no_speech_threshold=0.55,
                            )
                            raw_segs = [(float(s["start"]), float(s["end"]), (s.get("text") or "").strip())
                                        for s in res.get("segments", [])]
                            del res
                        finally:
                            _free_mlx_memory()
                    except Exception:
                        raw_segs = None
                    if raw_segs is None and stitched_np is not None:
                        try:
                            from faster_whisper import WhisperModel  # type: ignore
                            fw = WhisperModel("small", compute_type="int8")
                            fsegs, _ = fw.transcribe(stitched_np, vad_filter=True)
                            raw_segs = [(float(s.start), float(s.end), (s.text or "").strip()) for s in fsegs]
                        except Exception:
                            raw_segs = None
                    stitched_np = None

                    if raw_segs is not None:
                        per_win = {wkey: [] for _, _, _, wkey in spans}
                        for seg_s, seg_e, txt in raw_segs:
                            if not txt or seg_e <= seg_s:
                                continue
                            best, best_ov = None, 0.0
                            for sp_s, sp_e, orig_st, wkey in spans:
                                ov = min(seg_e, sp_e) - max(seg_s, sp_s)
                                if ov > best_ov:
                                    best_ov = ov
                                    best = (sp_s, sp_e, orig_st, wkey)
                            if best is not None and best_ov > 0.05:
                                sp_s, sp_e, orig_st, wkey = best
                                o_s = round(orig_st + max(0.0, seg_s - sp_s), 2)
                                o_e = round(orig_st + min(sp_e - sp_s, seg_e - sp_s), 2)
                                per_win[wkey].append([o_s, max(round(o_s + 0.05, 2), o_e), txt])
                        for wkey, seg_list in per_win.items():
                            win_cache[wkey] = seg_list
                        dirty = True
            except Exception as e:
                core.say("clip", f"Candidate transcription skipped: {e}", "warn")
                _free_mlx_memory()

    if dirty:
        core.write_json(tx_file, tx_data)

    out = []
    for st in pool:
        wkey = f"{int(round(st))}:{ilen}"
        for item in win_cache.get(wkey, ()):
            if len(item) >= 3:
                out.append((float(item[0]), float(item[1]), str(item[2])))
    out.sort(key=lambda x: x[0])
    return out


def text_between(segments, a, b):
    return " ".join(t.strip() for s, e, t in segments if e > a and s < b)


def candidates(dur, loud, cuts, length, count, segments=()):
    """Greedy pick of non-overlapping windows weighted by audio spikes, dynamic contrast, action cuts and speech."""
    if dur < length + 2:
        return [0.0]
    med = statistics.median(loud) if loud else -40.0
    spread = (statistics.pstdev(loud) or 1.0) if loud else 1.0
    step, scored = 4, []
    for start in range(5, max(6, int(dur - length - 2)), step):
        seg = loud[start:start + length] or [med]
        quiet = sum(1 for v in seg if v < -58) / len(seg)
        mean_db = sum(seg) / len(seg)
        peak_db = max(seg)
        dyn = statistics.pstdev(seg) if len(seg) > 2 else 0.0
        local_ctx = loud[max(0, start - 30):min(len(loud), start + length + 30)] or [med]
        local_med = statistics.median(local_ctx)
        spike = max(0.0, (peak_db - local_med) / spread)
        z = (mean_db - med) / spread
        peak = (peak_db - med) / spread
        flat_penalty = 0.6 if (dyn < 1.8 and z > 0.5) else 0.0
        n_cuts = sum(1 for c in cuts if start <= c < start + length)
        speech_boost = 0.0
        if segments:
            txt = text_between(segments, start, start + length)
            words = len(txt.split())
            excl = txt.count("!") + txt.count("?")
            if 6 <= words <= 90:
                speech_boost = min(0.8, words / 65.0) + min(0.5, excl * 0.15)
        score = 0.75 * z + 0.65 * peak + 0.55 * spike + 0.18 * min(n_cuts, 8) + speech_boost - 2.2 * quiet - flat_penalty
        scored.append((score, float(start)))
    scored.sort(reverse=True)
    picked = []
    min_gap = max(12.0, length * 0.85)
    for score, start in scored:
        if all(abs(start - p) >= min_gap for p in picked):
            picked.append(start)
        if len(picked) >= count:
            break
    return sorted(picked)


def frame_strip(video, start, length, out):
    """Fast 6-seek 3x2 contact sheet (224:-2 -> ~672x252, -q:v 6) — optimal ViT patch count for fast Qwen2.5-VL / Gemma3."""
    if os.path.exists(out) and os.path.getsize(out) > 1000:
        with open(out, "rb") as f:
            return f.read()
    ff = core.which("ffmpeg") or "ffmpeg"
    font = core.FONT_BOLD.replace(":", r"\:")
    n = 6
    offsets = [length * (i + 0.5) / n for i in range(n)]
    inputs, filters = [], []
    for i, off in enumerate(offsets):
        inputs += ["-ss", f"{start + off:.2f}", "-i", video]
        filters.append(f"[{i}:v]scale=224:-2,drawtext=fontfile='{font}':text='{int(round(off))}s':"
                       f"fontcolor=yellow:fontsize=16:x=5:y=4:box=1:boxcolor=black@0.65[v{i}]")
    fc = ";".join(filters) + ";" + "".join(f"[v{i}]" for i in range(n)) + "xstack=inputs=6:layout=0_0|w0_0|w0+w1_0|0_h0|w0_h0|w0+w1_h0[out]"
    tmp = f"{out}.{threading.get_ident()}.jpg"
    try:
        core.run([ff, "-hide_banner", "-loglevel", "error", "-y", *inputs,
                  "-filter_complex", fc, "-map", "[out]", "-frames:v", "1", "-q:v", "6", tmp], timeout=90)
        os.replace(tmp, out)
    except Exception:
        if os.path.exists(tmp):
            try: os.remove(tmp)
            except OSError: pass
        vf = (f"fps=6/{length},scale=224:-2,"
              f"drawtext=fontfile='{font}':text='%{{eif\\:t\\:d}}s':fontcolor=yellow:fontsize=16:x=5:y=4:box=1:boxcolor=black@0.65,"
              "tile=3x2")
        core.run([ff, "-hide_banner", "-loglevel", "error", "-y", "-ss", str(start), "-t", str(length), "-i", video,
                  "-vf", vf, "-frames:v", "1", "-q:v", "6", out], timeout=120)
    with open(out, "rb") as f:
        return f.read()


def _load_signals(video, work, need_wav, job):
    sig_file = os.path.join(work, "signals_v2.json")
    vsig = _video_sig(video)
    wav_path = os.path.join(work, "audio16k.wav") if need_wav else None
    cached = core.read_json(sig_file, None)
    if cached and cached.get("vsig") == vsig and "loud" in cached:
        return cached.get("loud", []), cached.get("cuts", []), vsig, wav_path

    if cached and cached.get("vsig") != vsig and wav_path and os.path.exists(wav_path):
        try: os.remove(wav_path)
        except OSError: pass

    job.update(0.04, "Analysing audio & scene cuts in parallel")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        f_loud = ex.submit(loudness_and_wav, video, wav_path)
        f_cuts = ex.submit(scene_cuts, video)
        loud = f_loud.result()
        cuts = f_cuts.result()

    core.write_json(sig_file, {"vsig": vsig, "loud": loud, "cuts": cuts})
    return loud, cuts, vsig, wav_path


def find(job, project, s, on_moment=None):
    """Fills project['moments'] with the best clips. Streams ready clips via on_moment(m) if provided."""
    video, dur, length = project["video"], project["duration"], int(s["reel_seconds"])
    target = max(1, round(dur / 3600 * s["reels_per_hour"]))
    work = os.path.join(os.path.dirname(video), "_analysis")
    os.makedirs(work, exist_ok=True)

    provider = ai.pick_provider(s)
    loud, cuts, vsig, wav_path = _load_signals(video, work, provider != "none", job)

    max_fit = max(1, int(dur // max(12, length * 0.9)))
    if provider == "none":
        pool_size = min(max_fit, target)
    elif provider == "ollama":
        pool_size = min(max_fit, max(target + 2, round(target * 1.3)))
    else:
        pool_size = min(max_fit, max(target + 2, round(target * 2)))

    pool = candidates(dur, loud, cuts, length, pool_size)
    model_tag = f"{provider}:{ai.pick_ollama_model(s)}" if provider == "ollama" else provider
    core.say("clip", f"{len(pool)} candidate moments (target {target}), AI: {model_tag}")

    if provider == "none":
        return [{"id": uuid.uuid4().hex[:8], "start": st, "end": st + length, "score": len(pool) - i,
                 "hook": "", "emoji": "", "keywords": [], "why": "loud / busy moment",
                 "status": "needs_hook", "reel": "", "posted": {}} for i, st in enumerate(pool)]

    job.update(0.16, "Transcribing candidate clips (Apple Silicon MLX)")
    unload_model = ai.pick_ollama_model(s) if (provider == "ollama" or ai.ollama_ok()) else None
    segments = transcribe_candidates(video, wav_path, loud, pool, length, work, vsig=vsig, unload_model=unload_model)

    cache_file = os.path.join(work, "ai_cache.json")
    ai_cache = core.read_json(cache_file, {})
    cache_lock = threading.Lock()

    strip_paths = {st: os.path.join(work, f"strip224_{int(st)}_{length}.jpg") for st in pool}
    strip_pool = concurrent.futures.ThreadPoolExecutor(max_workers=4)
    strip_futs = {st: strip_pool.submit(frame_strip, video, st, length, strip_paths[st]) for st in pool}

    done_count = 0
    streamed_ids = set()

    def eval_one(start):
        nonlocal done_count
        ck = f"{getattr(ai, 'PROMPT_VER', 'v1')}|{model_tag}|{int(start)}|{length}|{s.get('header','')}"
        m = {"id": uuid.uuid4().hex[:8], "start": start, "end": start + length, "score": 0, "hook": "",
             "emoji": "", "keywords": [], "why": "", "status": "review", "reel": "", "posted": {}}
        try:
            with cache_lock:
                d = ai_cache.get(ck)
            if not d:
                img = strip_futs[start].result()
                d = ai.describe(img, text_between(segments, start, start + length), length, s, provider)
                with cache_lock:
                    ai_cache[ck] = d
                    core.write_json(cache_file, ai_cache)
            raw_bs = float(d.get("best_start") or 0.0)
            if raw_bs >= start and start >= length:
                raw_bs -= start
            shift = max(0.0, min(raw_bs, length * 0.55)) if 0 <= raw_bs < length else 0.0
            new_start = max(0.0, min(start + shift, dur - length))
            m.update(score=d["score"], hook=d["hook"], emoji=d.get("emoji", ""), keywords=d["keywords"],
                     why=d.get("why", ""), start=new_start, end=new_start + length)
        except ai.AIError as e:
            core.say("clip", f"AI skipped moment @{int(start)}s: {e}", "warn")
            if "key" in str(e).lower() or "401" in str(e):
                raise
        except Exception as e:
            core.say("clip", f"Skipped moment @{int(start)}s: {e}", "warn")
        do_stream = False
        with cache_lock:
            done_count += 1
            job.update(0.3 + 0.65 * done_count / max(1, len(pool)), f"Watching moment {done_count} of {len(pool)}")
            if on_moment is not None and m["hook"] and m["score"] >= 6 and len(streamed_ids) < target:
                streamed_ids.add(m["id"])
                do_stream = True
        if do_stream:
            on_moment(m)
        return m

    ai_workers = 1 if provider == "ollama" else 4
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=ai_workers) as ex:
            moments = list(ex.map(eval_one, pool))
    finally:
        strip_pool.shutdown(wait=False)

    good = [m for m in moments if m["hook"] and m["score"] >= 6]
    if len(good) < min(target, max(1, len(moments) // 3)):
        good = [m for m in moments if m["hook"] and m["score"] >= 5] or moments
    good.sort(key=lambda m: (m["score"], m["id"] in streamed_ids), reverse=True)

    stop_w = {"the", "a", "an", "in", "on", "at", "to", "for", "of", "and", "is", "are", "you", "your", "with", "into", "out"}
    deduped, seen_sets = [], []
    for m in good:
        toks = {w for w in re.findall(r"[a-z0-9]+", m["hook"].lower()) if w not in stop_w and len(w) > 2}
        if toks and any(len(toks & prev) / max(1, min(len(toks), len(prev))) >= 0.7 for prev in seen_sets):
            continue
        if toks:
            seen_sets.append(toks)
        deduped.append(m)
    if len(deduped) < min(target, len(good)):
        for m in good:
            if m not in deduped:
                deduped.append(m)
            if len(deduped) >= target:
                break

    chosen = sorted(deduped[:target], key=lambda m: m["start"])
    for m in chosen:
        if not m["hook"]:
            m["status"] = "needs_hook"
    return chosen
