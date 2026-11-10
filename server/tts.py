"""Listening — documents read aloud by Qwen3-TTS on the dev box.

The page sends its paragraphs in reading order. A background reader voices them in batches of
four, starting wherever the listener is, and caches each paragraph as a wav in the film's
`audio/readings/` — keyed by voice and text, so an unchanged paragraph is never voiced twice.

One reading at a time: the GPU is one card, and you only listen to one document at once.
Starting a new reading stops the old one (its finished paragraphs stay cached).
"""
import base64, hashlib, json, pathlib, subprocess, threading, time, urllib.error, urllib.request

import registry

BATCH = 4
SSH_HOST = "gpu"
START_SCRIPT = "/mnt/d1/tts/bin/studio_tts_start.sh"
SAMPLE_TEXT = ("The light comes in low across the table. She counts the cars, one by one, and "
               "writes each one down. Nobody asks her why, and she has stopped expecting anyone to.")

_lock = threading.Lock()
READINGS: dict[str, "Reading"] = {}
_current = {"key": None}
_voice_ids: dict[str, str] = {}


class VoiceError(Exception):
    pass


def cfg():
    v = registry.load()["voice"]
    return v["host"].rstrip("/"), v["instruct"], v["language"]


def _call(path, body=None, timeout=600):
    host, _, _ = cfg()
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(host + path, data=data,
                                 headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error")
        except ValueError:
            msg = None
        raise VoiceError(msg or f"voice server answered {e.code}") from None
    except (urllib.error.URLError, OSError) as e:
        raise ConnectionError(str(getattr(e, "reason", e))) from None


def ensure_server(on_status=lambda s: None):
    """The voice server, started over ssh if it isn't up."""
    try:
        _call("/health", timeout=4)
        return
    except (ConnectionError, VoiceError):
        pass
    on_status("Starting the voice on the dev box…")
    try:
        subprocess.run(["ssh", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes", SSH_HOST,
                        f"bash {START_SCRIPT}"], capture_output=True, timeout=40, check=True)
    except (subprocess.SubprocessError, OSError):
        raise VoiceError("can't reach the dev box — is Tailscale up?")
    for _ in range(45):
        time.sleep(2)
        try:
            _call("/health", timeout=4)
            return
        except (ConnectionError, VoiceError):
            continue
    raise VoiceError("the voice server on the dev box didn't start — see /mnt/d1/tts/studio_tts.log")


def voice_id(on_status=lambda s: None):
    _, instruct, language = cfg()
    key = f"{instruct}\n{language}"
    if key not in _voice_ids:
        on_status("Designing the reading voice (once)…")
        raw, _ = _call("/voice", {"instruct": instruct, "language": language, "sample_text": SAMPLE_TEXT})
        _voice_ids[key] = json.loads(raw)["voice_id"]
    return _voice_ids[key]


def sample_wav():
    """A short line in the configured voice, for Settings' 'Try this voice'."""
    ensure_server()
    vid = voice_id()
    _, _, language = cfg()
    raw, _ = _call("/speak", {"voice_id": vid, "text": "This is how your documents will sound when you listen to them.",
                              "language": language})
    return raw


class Reading:
    def __init__(self, fid, folder: pathlib.Path, chunks):
        self.fid, self.folder, self.chunks = fid, folder, chunks
        self.key = hashlib.sha1(("\n\x00".join(chunks) + fid).encode()).hexdigest()[:16]
        self.paths = [None] * len(chunks)
        self.cursor = 0
        self.status = "starting"          # starting | reading | done | error | stopped
        self.note = ""
        self.error = None
        self.stop = threading.Event()
        self.started = time.time()

    def public(self):
        return {"key": self.key, "total": len(self.chunks), "status": self.status,
                "ready": [p is not None for p in self.paths], "note": self.note, "error": self.error}

    def _path(self, vid, text):
        h = hashlib.sha1(f"{vid}\n{text}".encode()).hexdigest()[:20]
        return self.folder / f"{h}.wav"

    def run(self):
        try:
            ensure_server(self._say)
            vid = voice_id(self._say)
            _, _, language = cfg()
            self.folder.mkdir(parents=True, exist_ok=True)
            for i, t in enumerate(self.chunks):
                p = self._path(vid, t)
                if p.exists():
                    self.paths[i] = p
            self.status, self.note = "reading", ""
            batch = BATCH
            while not self.stop.is_set():
                todo = self._next_batch(batch)
                if not todo:
                    break
                try:
                    raw, _ = _call("/speak_batch", {"voice_id": vid, "language": language,
                                                    "texts": [self.chunks[i] for i in todo]})
                except VoiceError as e:
                    if "GPU is full" in str(e):
                        if batch > 1:
                            batch //= 2             # the card is shared with ComfyUI: ask for less
                            continue
                        self._say("The dev box GPU is busy with a render — waiting…")
                        self.stop.wait(15)
                        continue
                    raise
                except ConnectionError:
                    ensure_server(self._say)
                    continue
                for i, b in zip(todo, json.loads(raw)["wavs"]):
                    p = self._path(vid, self.chunks[i])
                    p.write_bytes(base64.b64decode(b))
                    self.paths[i] = p
                self.note = ""
            self.status = "stopped" if self.stop.is_set() and None in self.paths else "done"
        except (VoiceError, ConnectionError) as e:
            self.status, self.error = "error", str(e)
        except Exception as e:
            self.status, self.error = "error", f"{type(e).__name__}: {e}"

    def _say(self, s):
        self.note = s

    def _next_batch(self, size=BATCH):
        n = len(self.chunks)
        order = list(range(self.cursor, n)) + list(range(0, self.cursor))
        return [i for i in order if self.paths[i] is None][:size]


def start(fid, folder, chunks):
    chunks = [c.strip()[:1800] for c in chunks if c and c.strip()]
    if not chunks:
        raise ValueError("nothing to read on this page")
    r = Reading(fid, folder, chunks)
    with _lock:
        old = READINGS.get(_current["key"])
        if old and old.key != r.key:
            old.stop.set()
        existing = READINGS.get(r.key)
        if existing and existing.status in ("starting", "reading", "done"):
            _current["key"] = r.key
            return existing
        READINGS[r.key] = r
        _current["key"] = r.key
    threading.Thread(target=r.run, daemon=True).start()
    return r
