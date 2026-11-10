# Studio software — spec (v1, 2026-10-14)

Custom software for making AI content. **First category: film.** First film through it: *The Count*.

## What it is

A local web app that carries a film from a blank page to approved pre-production, one
approval at a time. Every piece of writing is done by headless Claude Code (`claude -p`).
Every piece is a file on disk, inside the film's own folder, so nothing is locked in the app.

## The workflow (approved in chat, 2026-10-14)

```
START ──► 1 PREMISE ──► 2 SCRIPT ──► 3 BREAKDOWN ──► 4 PRE-PRODUCTION ──► 5 GENERATION (slice 2)
idea or     Gate A        Gate B        Gate C          cards, one by one     beat by beat,
script                                                                         draft tier → final tier
```

- **Start** — two ways in: a loose idea, or a finished script (becomes script v1 and the
  premise is drawn out of it).
- **Premise (Gate A)** — one-sentence ache, send test, irony engine, the clip.
- **Script (Gate B)** — the house loop: draft → critique → revise. Numbered, append-only.
- **Breakdown (Gate C)** — acts → scenes → beats. Each beat: Shown / Said / Music / Conveys /
  Moves forward / Iconic frame / seconds. Budgets counted, not guessed.
- **Pre-production** — cards drafted and approved one at a time, in a suggested order
  (characters → locations → props → scene plans → music brief). Any card can be opened early.

### Approval rules (everywhere)

- Each item is **Draft → Review → Approved**. Approval is of a specific version.
- Notes produce a **new version**; old versions are never overwritten.
- Hand edits are saved as a new version "by you".
- Each version records what upstream it was **based on**. If an upstream approval changes,
  downstream items show **Needs a look** — nothing is rewritten silently.
- Each stage shows its harness gate checklist (FILM_HARNESS.md §4). Approving with open
  boxes asks for an explicit "approve anyway". Ticks are written to the film's
  `HARNESS_CHECK.md`.

## References — Gate D (added 2026-10-14)

Pictures and facts, per film, in `references/` + `film.json["refs"]`. Automated with a manual
override: approving a character/location/prop card starts a `claude -p` research job
(WebSearch + WebFetch, read-only) that downloads 6–8 pictures and up to 5 facts as *proposed*
findings. The filmmaker keeps or skips each one; paste/drop/link adds manual ones; "Research again"
takes a steer. Kept pictures are passed to card writing via the Read tool and will be the image
references for generation. Facts (decided / true / open on purpose) are proposed by drafts,
revisions and critiques and are canon in every brief once kept. Gate D = no findings left to
review + every character/location/prop has a kept picture. Generation waits for it.

Critiques now carry a **Gaps** table: every question a cold viewer is left with, open on purpose
(only if it serves the plot) or filled with the smallest on-screen fix; revisions must close every
unintended gap.

## Generation tiers (configured now, wired in slice 2)

Two levels, both configurable in Settings:

| | Draft tier — get the prompt right | Final tier — build it |
|---|---|---|
| Image | Qwen-Image 2.1 + Qwen-Image 2.1 Edit (dev box) | Nano Banana 2.1 or GPT Image 2.5 Sunburst |
| Video | Grok Imagine 2.1 (kie) | keep the draft, or MiniMax H3 / Wan 3.0 / Seedance 2.0 Fast / mini models |
| Resolution | — | strategy chosen per film on economics: 480p or 720p + upscale, or native 1080p / 4K |

Note: Qwen-Image 2.1 is research-licensed (non-commercial) — draft use only, never a shipped frame.

## Architecture

```
software/
  server/serve.py      stdlib HTTP server, JSON API, SSE job streams, static files
  server/films.py      film folders, film.json index, versions, approvals, staleness
  server/engine.py     claude -p runner: background threads, stream-json parsing, cost
  server/briefs.py     the stage briefs (system + task prompts) — the craft lives here
  server/registry.py   generation model catalog + config.json load/save
  web/                 Preact + htm, no build step
  config.json          films root, writing models, generation tiers
```

### claude -p contract

`claude -p --output-format stream-json --verbose --include-partial-messages
--setting-sources "" --strict-mcp-config --tools "" --no-session-persistence
--model <configured> --system-prompt <brief>`; structured stages (breakdown) add
`--json-schema`. Runs from a neutral cwd so no repo CLAUDE.md leaks in. The harness and the
relevant craft skill text are put into the system prompt explicitly.

### Files in a film folder

```
The Count/
  film.json               the index (items, versions, approvals, checks)
  idea.md                 the seed
  HARNESS_CHECK.md        ticked gate boxes
  premise/premise_v1.md …
  script/01_draft.md, 02_critique.md, 03_revised.md …   (house numbering, append-only)
  breakdown/breakdown_v1.json + breakdown_v1.md …
  preproduction/characters/<slug>_v1.md, locations/…, props/…, scenes/…, music/…
```

## Out of scope for v1

Image/video generation (slice 2), edit/release stages, other content categories, multi-user.
