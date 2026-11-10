#!/usr/bin/env python3
"""Studio voice server — Qwen3-TTS on the dev box, kept warm between requests.

Runs in the `qwen3tts` conda env (transformers 4.57.3 — the version Qwen3-TTS is built on;
ComfyUI's env has transformers 5.x, under which generation never stops). Both the studio app and
ComfyUI's bridge nodes call this server, so the models live in exactly one place.

    GET  /health
    GET  /speakers                                   preset voices of the CustomVoice model
    POST /voice        {instruct, language, sample_text}  -> {"voice_id"}   design once, clone after
    POST /speak        {voice_id, text, language}         -> audio/wav      (cloned designed voice)
    POST /speak_batch  {voice_id, texts, language}        -> {"wavs": [b64]}
    POST /custom       {text, speaker, instruct, language, seed}      -> audio/wav   preset voice
    POST /design_speak {text, instruct, language, seed}               -> audio/wav   voice from words
    POST /clone        {text, ref_audio_b64, ref_text, language, seed} -> audio/wav  any reference

One model is resident at a time (the card is shared with ComfyUI): asking for a different one
swaps it. Everything unloads after IDLE_S seconds without a request. Out of memory → 503.
"""
import base64, hashlib, io, json, os, threading, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

import numpy as np
import soundfile as sf
import torch
from qwen_tts import Qwen3TTSModel

HOST = os.environ.get("STUDIO_TTS_HOST", "100.107.204.122")
PORT = int(os.environ.get("STUDIO_TTS_PORT", "8191"))
MODELS = "/mnt/d1/tts/qwen3tts/models"
VOICES = "/mnt/d1/tts/voices"
IDLE_S = 180
BASE, DESIGN, CUSTOM = ("Qwen3-TTS-12Hz-1.7B-Base", "Qwen3-TTS-12Hz-1.7B-VoiceDesign",
                        "Qwen3-TTS-12Hz-1.7B-CustomVoice")
MAX_TOKENS = 2048

os.makedirs(VOICES, exist_ok=True)
lock = threading.Lock()
state = {"name": None, "model": None, "prompts": {}, "last": time.time()}


def model(name):
    """The requested model, evicting whichever other one is resident."""
    if state["name"] != name:
        _free()
        state["model"] = Qwen3TTSModel.from_pretrained(
            f"{MODELS}/{name}", device_map="cuda:0", dtype=torch.bfloat16, attn_implementation="sdpa")
        state["name"] = name
    return state["model"]


def _free():
    state["model"] = state["name"] = None
    state["prompts"] = {}
    torch.cuda.empty_cache()


def _seed(seed):
    if seed is not None:
        torch.manual_seed(int(seed))


def _wav(samples, sr):
    buf = io.BytesIO()
    sf.write(buf, samples, sr, format="WAV")
    return buf.getvalue()


def _done(wavs, sr):
    torch.cuda.empty_cache()                 # hand cached blocks back — ComfyUI shares this card
    return [_wav(w, sr) for w in wavs]


# ---- designed voices, cloned for every paragraph (the studio app's reader) ----

def voice_id(instruct, language):
    return hashlib.sha1(f"{instruct}\n{language}".encode()).hexdigest()[:12]


def design(instruct, language, sample_text):
    vid = voice_id(instruct, language)
    if not os.path.exists(f"{VOICES}/{vid}.wav"):
        wavs, sr = model(DESIGN).generate_voice_design(text=sample_text, instruct=instruct,
                                                       language=language, max_new_tokens=MAX_TOKENS)
        sf.write(f"{VOICES}/{vid}.wav", wavs[0], sr)
        with open(f"{VOICES}/{vid}.json", "w") as f:
            json.dump({"instruct": instruct, "language": language, "sample_text": sample_text}, f)
    return vid


def prompt_for(vid):
    m = model(BASE)
    if vid not in state["prompts"]:
        meta = json.load(open(f"{VOICES}/{vid}.json"))
        state["prompts"][vid] = m.create_voice_clone_prompt(
            ref_audio=f"{VOICES}/{vid}.wav", ref_text=meta["sample_text"])
    return state["prompts"][vid]


def speak_batch(vid, texts, language):
    prompt = prompt_for(vid)
    wavs, sr = model(BASE).generate_voice_clone(text=texts, language=[language] * len(texts),
                                                voice_clone_prompt=prompt * len(texts),
                                                max_new_tokens=MAX_TOKENS)
    return _done(wavs, sr)


# ---- one-off generation (ComfyUI's bridge nodes) ----

def custom(text, speaker, instruct, language, seed):
    _seed(seed)
    wavs, sr = model(CUSTOM).generate_custom_voice(text=text, speaker=speaker, language=language,
                                                   instruct=instruct or None, max_new_tokens=MAX_TOKENS)
    return _done(wavs, sr)[0]


def design_speak(text, instruct, language, seed):
    _seed(seed)
    wavs, sr = model(DESIGN).generate_voice_design(text=text, instruct=instruct, language=language,
                                                   max_new_tokens=MAX_TOKENS)
    return _done(wavs, sr)[0]


def clone(text, ref_b64, ref_text, language, seed):
    _seed(seed)
    ref, sr_ref = sf.read(io.BytesIO(base64.b64decode(ref_b64)), dtype="float32")
    if ref.ndim > 1:
        ref = ref.mean(axis=1)
    kw = {"ref_text": ref_text} if ref_text else {"x_vector_only_mode": True}
    wavs, sr = model(BASE).generate_voice_clone(text=text, language=language,
                                                ref_audio=(np.asarray(ref), sr_ref),
                                                max_new_tokens=MAX_TOKENS, **kw)
    return _done(wavs, sr)[0]


def idle_reaper():
    while True:
        time.sleep(20)
        if state["model"] is not None and time.time() - state["last"] > IDLE_S and lock.acquire(blocking=False):
            try:
                _free()
            finally:
                lock.release()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj).encode())

    def do_GET(self):
        if self.path == "/health":
            free, total = torch.cuda.mem_get_info()
            return self._json(200, {"ok": True, "loaded": state["name"], "vram_free_mib": free // 2**20,
                                    "vram_total_mib": total // 2**20})
        if self.path == "/speakers":
            cfg = json.load(open(f"{MODELS}/{CUSTOM}/config.json"))
            spk = (cfg.get("talker_config") or {}).get("spk_id") or {}
            return self._json(200, {"speakers": sorted(spk)})
        self._json(404, {"error": "no such route"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            b = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._json(400, {"error": "bad json"})
        state["last"] = time.time()
        lang = b.get("language", "English")
        try:
            with lock:
                if self.path == "/voice":
                    return self._json(200, {"voice_id": design(b["instruct"], lang, b["sample_text"])})
                if self.path in ("/speak", "/speak_batch"):
                    if not os.path.exists(f"{VOICES}/{b['voice_id']}.json"):
                        return self._json(404, {"error": "unknown voice — design it first"})
                    texts = [b["text"]] if self.path == "/speak" else b.get("texts", [])
                    texts = [t.strip()[:2000] for t in texts if t and t.strip()][:8]
                    if not texts:
                        return self._json(400, {"error": "no text"})
                    wavs = speak_batch(b["voice_id"], texts, lang)
                    if self.path == "/speak":
                        return self._send(200, wavs[0], "audio/wav")
                    return self._json(200, {"wavs": [base64.b64encode(w).decode() for w in wavs]})
                text = (b.get("text") or "").strip()[:4000]
                if not text:
                    return self._json(400, {"error": "empty text"})
                if self.path == "/custom":
                    audio = custom(text, b.get("speaker", "Ryan"), b.get("instruct", ""), lang, b.get("seed"))
                elif self.path == "/design_speak":
                    audio = design_speak(text, b.get("instruct", ""), lang, b.get("seed"))
                elif self.path == "/clone":
                    audio = clone(text, b["ref_audio_b64"], b.get("ref_text", ""), lang, b.get("seed"))
                else:
                    return self._json(404, {"error": "no such route"})
                state["last"] = time.time()
                return self._send(200, audio, "audio/wav")
        except torch.cuda.OutOfMemoryError:
            # keep the model; drop the cache — the caller retries smaller, or after the render
            torch.cuda.empty_cache()
            self._json(503, {"error": "the dev box GPU is full — a render is probably running", "oom": True})
        except KeyError as e:
            self._json(400, {"error": f"missing {e}"})
        except Exception as e:
            self._json(500, {"error": f"{type(e).__name__}: {e}"})


if __name__ == "__main__":
    threading.Thread(target=idle_reaper, daemon=True).start()
    srv = ThreadingHTTPServer((HOST, PORT), H)
    print(f"studio tts on http://{HOST}:{PORT}", flush=True)
    srv.serve_forever()
