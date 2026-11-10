"""Config and the generation model catalog.

config.json is the one place a person changes which model does what. The catalog lists
every model the app knows; tiers point into it. Nothing here generates anything — the
lanes that do are slice 2. `wired` says honestly whether a model can be run from the app yet.
"""
import json, os, pathlib, threading

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG = pathlib.Path(os.environ.get("STUDIO_CONFIG") or ROOT / "config.json")
_lock = threading.Lock()

WRITING_MODELS = [
    {"id": "opus", "label": "Opus (latest)"},
    {"id": "sonnet", "label": "Sonnet (latest)"},
    {"id": "haiku", "label": "Haiku (latest)"},
    {"id": "claude-fable-5-1", "label": "Fable 5.1"},
]

# the writing actions a person can route to different models
WRITING_ACTIONS = {
    "premise": "Premise",
    "draft": "Script draft & revision",
    "critique": "Critique",
    "breakdown": "Breakdown",
    "card": "Pre-production cards",
    "research": "Reference research (web)",
}

DEFAULT = {
    "films_root": str(ROOT.parent / "short_films"),
    "writing": {
        "model": "opus",
        "effort": "high",
        "per_action": {"premise": "opus", "draft": "opus", "critique": "opus",
                       "breakdown": "opus", "card": "sonnet", "research": "sonnet"},
    },
    "catalog": [
        # image — draft tier candidates
        {"id": "qwen-image-2.1", "label": "Qwen-Image 2.1", "kind": "image", "role": "generate",
         "lane": "local", "wired": False,
         "note": "dev box. Research licence — non-commercial, drafts only. 2 refs measured 25.6 GB on a 5090."},
        {"id": "qwen-image-2.1-edit", "label": "Qwen-Image 2.1 Edit", "kind": "image", "role": "edit",
         "lane": "local", "wired": False, "note": "dev box. Fix a draft instead of rerolling it."},
        {"id": "krea2", "label": "Krea 2 Turbo", "kind": "image", "role": "generate",
         "lane": "local", "wired": False, "note": "dev box, measured 20s at 1024x1824."},
        # image — final tier candidates
        {"id": "nano-banana-2.1", "label": "Nano Banana 2.1", "kind": "image", "role": "generate",
         "lane": "kie", "wired": False, "note": "multi-reference identity."},
        {"id": "gpt-image-2.5-sunburst", "label": "GPT Image 2.5 Sunburst", "kind": "image",
         "role": "generate", "lane": "kie", "wired": False, "note": ""},
        # video — draft tier
        {"id": "grok-imagine-2.1", "label": "Grok Imagine 2.1", "kind": "video", "role": "i2v",
         "lane": "kie", "wired": False, "note": "cheap motion check. Keep it if it holds up."},
        # video — final tier
        {"id": "minimax-h3", "label": "MiniMax H3", "kind": "video", "role": "i2v",
         "lane": "local", "wired": False, "note": "dev box, video + native audio, turbo LoRA 6–8 steps."},
        {"id": "wan-3.0", "label": "Wan 3.0", "kind": "video", "role": "i2v",
         "lane": "kie", "wired": False, "note": "API only — no open weights."},
        {"id": "seedance-2.0-fast", "label": "Seedance 2.0 Fast", "kind": "video", "role": "i2v",
         "lane": "kie", "wired": False, "note": ""},
    ],
    "tiers": {
        "image": {"draft": ["qwen-image-2.1", "qwen-image-2.1-edit"],
                  "final": ["nano-banana-2.1", "gpt-image-2.5-sunburst"]},
        "video": {"draft": ["grok-imagine-2.1"],
                  "final": ["grok-imagine-2.1", "minimax-h3", "wan-3.0", "seedance-2.0-fast"]},
    },
    "voice": {
        "host": "http://100.107.204.122:8191",
        "instruct": ("A calm, warm, clear narrator in his forties reading a screenplay aloud: unhurried "
                     "audiobook pace, close to the microphone, neutral accent, gentle and attentive, "
                     "never theatrical."),
        "language": "English",
    },
    "resolution": {
        "draft_video": "480p",
        "final_strategy": "720p-upscale",
        "strategies": {
            "480p-upscale": "Generate 480p, upscale to delivery",
            "720p-upscale": "Generate 720p, upscale to delivery",
            "1080p-native": "Generate 1080p directly",
            "4k-native": "Generate 4K directly",
        },
    },
}


def _merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def load() -> dict:
    with _lock:
        if not CONFIG.exists():
            CONFIG.write_text(json.dumps(DEFAULT, indent=2))
            return json.loads(json.dumps(DEFAULT))
        return _merge(DEFAULT, json.loads(CONFIG.read_text()))


def save(cfg: dict) -> dict:
    merged = _merge(DEFAULT, cfg)
    ids = {m["id"] for m in merged["catalog"]}
    for kind, tiers in merged["tiers"].items():
        for tier, chosen in tiers.items():
            missing = [m for m in chosen if m not in ids]
            if missing:
                raise ValueError(f"{kind} {tier} tier names unknown models: {missing}")
    with _lock:
        tmp = CONFIG.with_suffix(".tmp")
        tmp.write_text(json.dumps(merged, indent=2))
        tmp.replace(CONFIG)
    return merged


def writing_model(action: str) -> str:
    w = load()["writing"]
    return w.get("per_action", {}).get(action) or w["model"]
