# Studio: Technical Guide

## Run it

```bash
python3 software/server/serve.py          # opens http://127.0.0.1:8790
```

No install step. It needs `python3` and the `claude` CLI logged in, and nothing else.

## The workflow

**Start** (an idea or a script) → **Premise** (Gate A) → **Script** (Gate B: draft → critique → revise)
→ **Breakdown** (Gate C: acts, scenes, beats, budgets) → **Pre-production** (one card at a time:
characters, locations, props, scene plans, music) → **References** (Gate D, before any asset).

- **References** hold pictures and facts. Approving a character, location or prop card starts web
  research automatically: Claude finds reference photos and real-world facts, and they are
  downloaded as *findings to review*. You keep or skip each one, and add your own by paste, drop or
  link. Kept pictures are circled, Claude looks at them when it writes that card, and generation
  will use them as image references. Internet pictures are reference only and never ship.
- **Facts** are canon. Drafts, revisions and critiques propose them (decided / true / open on
  purpose), and kept facts go into every later brief, so nothing contradicts them.
- **Critiques list every gap** a cold viewer is left with. Each unintended gap gets the smallest
  on-screen fill, and the next revision must close it. Intended mysteries stay open.
- Every piece is Draft → Review → Approved. Approval is of a specific version.
- A note is pinned to a version, and the next version answers it. Old versions are never overwritten.
- If an upstream approval changes, downstream work gets a yellow **Needs a look** flag.
  Nothing is rewritten silently.
- Gate checklists come from `FILM_HARNESS.md`. Ticks are written to the film's `HARNESS_CHECK.md`.

## Listening

Every document page has **Listen**. The page is read aloud by Qwen3-TTS on the dev box. The line being
read is highlighted, clicking any paragraph jumps there, space bar pauses, and the speed setting
is remembered. The voice is designed once from the description in Settings → Listening and then
cloned for every paragraph, so a long read stays one voice. Paragraphs are voiced four at a time,
a little ahead of you (about 2.8× real time), and cached in the film's `audio/readings/`.

The voice server (`devbox/studio_tts_server.py`, deployed to `/mnt/d1/tts/bin/`) is started over
ssh on first use. It unloads after 3 idle minutes and backs off when ComfyUI needs the GPU.

The same server gives ComfyUI Qwen3-TTS: `devbox/comfyui_studio_tts/` is installed as
`custom_nodes/ComfyUI-StudioQwenTTS` (Preset voice / Voice from description / Clone a voice).
It is a bridge on purpose: Qwen3-TTS breaks under ComfyUI's transformers 5.x.

## Where things live

| | |
|---|---|
| `server/serve.py` | HTTP server, API, live job streams |
| `server/films.py` | film folders, versions, approvals, staleness |
| `server/engine.py` | runs `claude -p` in the background and streams its text |
| `server/fetch.py` | downloading references (sniffed, rate-limit aware) |
| `server/tts.py` | reading documents aloud: batching, caching, starting the dev-box voice |
| `devbox/` | the voice server that runs on the dev box |
| `server/briefs.py` | **the craft**: what each stage asks of the writer. Edit this to change how it writes. |
| `server/registry.py` | model catalog, generation tiers, writing models |
| `config.json` | your settings (edit in the app: Settings) |
| `web/` | the UI (Preact + htm, no build step) |
| `docs/spec.md` | the design |

Each film is a folder under the films root (`short_films/` by default): `film.json` is the index,
and `premise/`, `script/`, `breakdown/` and `preproduction/` hold the numbered files.

## Not built yet

Slice 2 is **generation**: beat by beat, the draft tier (Qwen-Image 2.1 / Edit on the dev box,
Grok Imagine 2.1 on kie) is used to get the prompt right, then the final tier builds it. The tiers
are already configurable in Settings and marked "not connected yet".

## Testing

`STUDIO_CONFIG=/path/test.json STUDIO_PORT=8791 python3 server/serve.py --no-open`
runs an isolated instance against another films folder.
