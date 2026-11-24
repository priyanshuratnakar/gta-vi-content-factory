"""Talks to whichever AI the user picked: Claude, OpenAI, or a free local model through Ollama.
Every provider gets the same job: look at a few frames (+ what was said) and write a hook."""
import json, base64, re, time, socket, urllib.request, urllib.error
from . import core

PROMPT_VER = "v3"

STYLE = """You write scroll-stopping hooks for a viral short-video page about {game}.
Every reel already shows "{header}" at the top; you write ONLY the continuation line below it.

Examples of 9/10 viral hooks (curiosity + specific detail, casual human voice):
- "if you wreck Brian's seaplane, he's waiting at the dock to ask what happened"
- "an NPC climbs back into his burning car just so he can sue"
- "there's a sunken private jet sitting wide open on the ocean floor"
- "cops bring out a Belgian Malinois K9 in a bandana to sniff your ride"
- "trying to lose 4 wanted stars in a neon street racer goes sideways fast"

Strict rules:
- Your hook MUST read as one smooth, complete grammatical sentence right after "{header}," (never write choppy headline fragments).
- Casual, punchy human English. Lowercase start, no period at the end. Max ~75 characters. No hashtags, no quotes around the whole hook, no ALL CAPS.
- NEVER write boring literal descriptions like "a red car drives past a billboard", "a car speeds down the road", or "someone says something". Name the specific twist, chaos, chase, NPC reaction, stunt, or discovery.
- Auto-transcripts come from noisy gameplay and can contain misheard gibberish: NEVER quote garbled or nonsensical words, and remember speech comes from human characters/players, never animals.
- Ground every claim in what is actually visible in the frames or clearly spoken. Never invent fake lore or features.

Scoring rubric (be picky so weak filler is filtered out):
- 1-4: Routine cruising/driving with no incident, menus, map, loading, black frames, or generic walking.
- 5-6: Mild dialogue or standard gameplay without a memorable hook.
- 7-8: Active police chase/wanted stars, crash, shootout, funny NPC interaction, underwater/aircraft moment, or cool Vice City detail.
- 9-10: Instant scroll-stopper — absurd physics, wild stunt/explosion, hilarious NPC/streamer reaction, or hidden world detail.
"""

ASK = """These are {n} frames from a {secs}-second {game} clip (relative clip seconds 0s..{secs}s in yellow), in chronological order.
{transcript}
Inspect HUD details (wanted stars top-right, red/blue police radar, subtitles) and action across the frames.
Reply with JSON only, no markdown or prose:
{{"score": <1-10 using the rubric>,
  "hook": "<scroll-stopping continuation after '{header}', max 78 chars>",
  "emoji": "<one fitting emoji, or empty>",
  "keywords": ["<3 to 5 search phrases like 'gta 6 police chase'>"],
  "best_start": <0 to {secs}: relative second inside THIS clip where the action starts, 0 if right away>,
  "why": "<one short reason>"}}"""

class AIError(RuntimeError): pass

def _post(url, headers, body, timeout=120):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=dict(headers, **{"Content-Type": "application/json"}))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise AIError(f"HTTP {e.code}: {e.read().decode(errors='replace')[:400]}")
    except urllib.error.URLError as e:
        raise AIError(f"couldn't reach the AI service ({e.reason})")
    except (TimeoutError, socket.timeout, json.JSONDecodeError, OSError) as e:
        raise AIError(f"AI connection/response error: {e}")

def _get(url, headers, timeout=20):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())

_ollama_cache = (0.0, None)

def ollama_ok(ttl=15.0):
    global _ollama_cache
    now = time.monotonic()
    if now - _ollama_cache[0] < ttl:
        return _ollama_cache[1]
    try:
        tags = _get("http://127.0.0.1:11434/api/tags", {}, timeout=2)
        res = [m["name"] for m in tags.get("models", [])]
    except Exception:
        res = None
    _ollama_cache = (now, res)
    return res

def pick_ollama_model(s):
    want = s.get("ollama_model") or "qwen2.5vl:7b"
    models = ollama_ok() or []
    if want in models:
        return want
    hit = next((m for m in models if m.startswith(want.split(":")[0])), None)
    if hit:
        return hit
    for pref in ("qwen2.5vl", "qwen3-vl", "gemma3", "llava", "minicpm-v"):
        hit = next((m for m in models if m.startswith(pref)), None)
        if hit:
            return hit
    return models[0] if models else want

def unload_ollama(model):
    """Frees Ollama VRAM before running another local model (e.g. MLX Whisper)."""
    if not model:
        return
    try:
        _post("http://127.0.0.1:11434/api/generate", {}, {"model": model, "keep_alive": 0}, timeout=5)
    except Exception:
        pass

def pick_provider(s):
    """auto = Claude key if present, else OpenAI key, else Ollama if running, else none."""
    p = s.get("ai_provider", "auto")
    if p != "auto": return p
    if core.get_key("claude_api_key"): return "claude"
    if core.get_key("openai_api_key"): return "openai"
    if ollama_ok(): return "ollama"
    return "none"

def _parse(text):
    raw = text or ""
    d = None
    for m in re.finditer(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", raw, re.S):
        chunk = re.sub(r",\s*([}\]])", r"\1", m.group(0))
        try:
            cand = json.loads(chunk)
            if isinstance(cand, dict) and ("hook" in cand or "score" in cand):
                d = cand
                break
        except json.JSONDecodeError:
            continue
    if d is None:
        m = re.search(r"\{.*\}", raw, re.S)
        if not m: raise AIError("the AI didn't return JSON")
        try:
            d = json.loads(re.sub(r",\s*([}\]])", r"\1", m.group(0)))
        except json.JSONDecodeError as e:
            raise AIError(f"invalid JSON from AI: {e}")
    try:
        d["score"] = float(d.get("score") or 0)
    except (TypeError, ValueError):
        d["score"] = 0.0
    hook = re.sub(r"^\s*(in gta vi|in gta 6)[, :\-]*", "", str(d.get("hook") or "").strip(), flags=re.I).strip(' "').rstrip(".")
    if len(hook) > 105:
        hook = hook[:102].rsplit(" ", 1)[0].rstrip(" ,.-")
    d["hook"] = hook
    kw = d.get("keywords")
    d["keywords"] = [str(k) for k in (kw if isinstance(kw, list) else [])][:5]
    try:
        d["best_start"] = float(d.get("best_start") or 0)
    except (TypeError, ValueError):
        d["best_start"] = 0.0
    return d

def describe(image_jpg, transcript, secs, s, provider=None):
    """image_jpg: bytes of a frame strip. Returns dict(score, hook, emoji, keywords, best_start, why)."""
    provider = provider or pick_provider(s)
    system = STYLE.format(game=s["game"], header=s["header"])
    t = f'What is said in the clip: "{transcript.strip()[:900]}"' if transcript and transcript.strip() else "There is no transcript."
    user = ASK.format(n=6, secs=int(secs), transcript=t, header=s["header"], game=s["game"])
    b64 = base64.b64encode(image_jpg).decode()

    if provider == "claude":
        key = core.get_key("claude_api_key")
        if not key: raise AIError("No Claude API key saved.")
        r = _post("https://api.anthropic.com/v1/messages",
                  {"x-api-key": key, "anthropic-version": "2023-06-01"},
                  {"model": s.get("claude_model"), "max_tokens": 400, "system": system,
                   "messages": [{"role": "user", "content": [
                       {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                       {"type": "text", "text": user}]}]})
        return _parse("".join(c.get("text", "") for c in r.get("content", [])))

    if provider == "openai":
        key = core.get_key("openai_api_key")
        if not key: raise AIError("No OpenAI API key saved.")
        r = _post("https://api.openai.com/v1/chat/completions", {"Authorization": f"Bearer {key}"},
                  {"model": s.get("openai_model"), "max_tokens": 400, "response_format": {"type": "json_object"},
                   "messages": [{"role": "system", "content": system},
                                {"role": "user", "content": [
                                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                                    {"type": "text", "text": user}]}]})
        return _parse(r["choices"][0]["message"]["content"])

    if provider == "ollama":
        model = pick_ollama_model(s)
        r = _post("http://127.0.0.1:11434/api/chat", {},
                  {"model": model, "stream": False, "format": "json", "keep_alive": "15m",
                   "options": {"temperature": 0.3, "num_ctx": 3072, "num_predict": 260},
                   "messages": [{"role": "system", "content": system},
                                {"role": "user", "content": user, "images": [b64]}]}, timeout=300)
        return _parse(r.get("message", {}).get("content", ""))

    raise AIError("no AI set up")

def test(provider, s):
    """Small call to check a key works. Returns (ok, message)."""
    try:
        if provider == "claude":
            _get("https://api.anthropic.com/v1/models", {"x-api-key": core.get_key("claude_api_key"), "anthropic-version": "2023-06-01"})
            return True, "Claude key works."
        if provider == "openai":
            _get("https://api.openai.com/v1/models", {"Authorization": f"Bearer {core.get_key('openai_api_key')}"})
            return True, "OpenAI key works."
        if provider == "ollama":
            models = ollama_ok(ttl=0)
            if models is None: return False, "Ollama isn't running. Open the Ollama app, then try again."
            want = s.get("ollama_model")
            if not any(m.startswith(want.split(":")[0]) for m in models):
                return False, f"Ollama is running but the model isn't downloaded yet. In Terminal run:  ollama pull {want}"
            return True, f"Ollama is ready with {want}."
        return False, "Pick an AI first."
    except urllib.error.HTTPError as e:
        return False, "That key was rejected." if e.code in (401, 403) else f"HTTP {e.code}"
    except Exception as e:
        return False, str(e)[:200]
