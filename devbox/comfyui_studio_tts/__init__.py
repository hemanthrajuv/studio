"""Qwen3-TTS for ComfyUI, through the studio voice server on this box.

Why a bridge instead of running the model in ComfyUI: Qwen3-TTS is built on transformers 4.57;
this ComfyUI runs transformers 5.x, under which the model's generation never stops (measured:
a 5-second line came back as 163 seconds). The voice server runs in its own `qwen3tts` env, so
these nodes need nothing beyond what ComfyUI already has, and cannot break other nodes.

Server: /mnt/d1/tts/bin/studio_tts_server.py — started automatically if it isn't running.
"""
import base64, io, json, os, subprocess, time, urllib.error, urllib.request

import numpy as np
import soundfile as sf
import torch

SERVER = os.environ.get("STUDIO_TTS_URL", "http://100.107.204.122:8191")
START = "/mnt/d1/tts/bin/studio_tts_start.sh"
LANGUAGES = ["English", "Chinese", "Japanese", "Korean", "German", "French", "Russian",
             "Portuguese", "Spanish", "Italian"]
SPEAKERS = ["Aiden", "Dylan", "Eric", "Ono_anna", "Ryan", "Serena", "Sohee", "Uncle_fu", "Vivian"]
CATEGORY = "audio/Qwen3-TTS"


def _up():
    try:
        urllib.request.urlopen(SERVER + "/health", timeout=3).read()
        return True
    except OSError:
        return False


def _ensure():
    if _up():
        return
    subprocess.run(["bash", START], capture_output=True, timeout=30)
    for _ in range(45):
        time.sleep(2)
        if _up():
            return
    raise RuntimeError("Qwen3-TTS voice server didn't start — see /mnt/d1/tts/studio_tts.log")


def _post(path, body):
    _ensure()
    req = urllib.request.Request(SERVER + path, json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=900) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read()).get("error", "")
            except ValueError:
                msg = ""
            if e.code == 503 and attempt < 2:
                time.sleep(10)          # GPU momentarily full — let ComfyUI's own models settle
                continue
            raise RuntimeError(f"Qwen3-TTS: {msg or e.code}")


def _audio(wav_bytes):
    data, sr = sf.read(io.BytesIO(wav_bytes), dtype="float32", always_2d=True)   # (samples, channels)
    waveform = torch.from_numpy(np.ascontiguousarray(data.T)).unsqueeze(0)       # (1, channels, samples)
    return ({"waveform": waveform, "sample_rate": sr},)


def _wav_from_audio(audio):
    w = audio["waveform"][0].float().cpu().numpy()          # (channels, samples)
    buf = io.BytesIO()
    sf.write(buf, w.T, audio["sample_rate"], format="WAV")
    return buf.getvalue()


SEED = ("INT", {"default": 0, "min": 0, "max": 2**31 - 1, "control_after_generate": True})


class StudioQwen3TTSPreset:
    """A built-in voice (CustomVoice model), with an optional delivery instruction."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
                    "text": ("STRING", {"multiline": True, "default": "Long night? They all are."}),
                    "speaker": (SPEAKERS, {"default": "Ryan"}),
                    "language": (LANGUAGES, {"default": "English"}),
                    "seed": SEED},
                "optional": {
                    "instruct": ("STRING", {"multiline": True, "default": "",
                                            "placeholder": "Delivery, e.g. tired and quiet, holding back"})}}

    RETURN_TYPES = ("AUDIO",)
    FUNCTION = "run"
    CATEGORY = CATEGORY

    def run(self, text, speaker, language, seed, instruct=""):
        return _audio(_post("/custom", {"text": text, "speaker": speaker, "language": language,
                                        "instruct": instruct, "seed": seed}))


class StudioQwen3TTSDesign:
    """A voice invented from a description (VoiceDesign model)."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "text": ("STRING", {"multiline": True, "default": "Long night? They all are."}),
            "voice": ("STRING", {"multiline": True,
                                 "default": "A woman in her fifties, low and level, tired but dry, close to the mic."}),
            "language": (LANGUAGES, {"default": "English"}),
            "seed": SEED}}

    RETURN_TYPES = ("AUDIO",)
    FUNCTION = "run"
    CATEGORY = CATEGORY

    def run(self, text, voice, language, seed):
        return _audio(_post("/design_speak", {"text": text, "instruct": voice, "language": language,
                                              "seed": seed}))


class StudioQwen3TTSClone:
    """Any voice from a 5–15 s reference clip (Base model). A transcript of the clip improves it."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
                    "text": ("STRING", {"multiline": True, "default": "Long night? They all are."}),
                    "reference_audio": ("AUDIO",),
                    "language": (LANGUAGES, {"default": "English"}),
                    "seed": SEED},
                "optional": {
                    "reference_transcript": ("STRING", {"multiline": True, "default": "",
                                                        "placeholder": "What the reference clip says (recommended)"})}}

    RETURN_TYPES = ("AUDIO",)
    FUNCTION = "run"
    CATEGORY = CATEGORY

    def run(self, text, reference_audio, language, seed, reference_transcript=""):
        ref = base64.b64encode(_wav_from_audio(reference_audio)).decode()
        return _audio(_post("/clone", {"text": text, "ref_audio_b64": ref, "language": language,
                                       "ref_text": reference_transcript.strip(), "seed": seed}))


NODE_CLASS_MAPPINGS = {
    "StudioQwen3TTSPreset": StudioQwen3TTSPreset,
    "StudioQwen3TTSDesign": StudioQwen3TTSDesign,
    "StudioQwen3TTSClone": StudioQwen3TTSClone,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "StudioQwen3TTSPreset": "Qwen3-TTS · Preset voice",
    "StudioQwen3TTSDesign": "Qwen3-TTS · Voice from description",
    "StudioQwen3TTSClone": "Qwen3-TTS · Clone a voice",
}
