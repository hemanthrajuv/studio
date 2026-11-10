"""The briefs — what each stage asks of the writer. The craft of the app lives here.

Every brief is a system prompt (house rules + the craft knowledge that stage needs) and a
task prompt (the approved material so far + what to do now). The model only ever sees
approved upstream work, plus the version it is revising and the notes pinned to it.
"""
import json, pathlib, re

STORY = pathlib.Path(__file__).resolve().parents[2]
SKILLS = pathlib.Path.home() / ".claude" / "skills"
SPLIT = "=== CHANGES ==="
FACTS = "=== FACTS ==="


def _read(p: pathlib.Path) -> str:
    try:
        return p.read_text()
    except OSError:
        return ""


def _skill(name: str) -> str:
    text = _read(SKILLS / name / "SKILL.md")
    return re.sub(r"\A---.*?---\s*", "", text, flags=re.S)


def harness() -> str:
    return _read(STORY / "FILM_HARNESS.md")


def story_checks() -> str:
    return _read(STORY / "ai_filmmaking" / "STORY_STAGE_CHECKS.md")


def system(stage: str, film: dict) -> str:
    parts = [f"""You are the writing department inside a small film studio's own software.
The studio is one filmmaker making AI short films. Everything you write becomes a file in the
film's folder and is read by that filmmaker, who approves it or sends notes.

How to work:
- Write the document itself and nothing else. No greeting, no "Here is", no sign-off.
- Be specific and particular. Restraint over melodrama. Ordinary wounds, not tragedies.
- When you are unsure of the filmmaker's intent, choose, and say what you chose in one line
  where the document has room for it — never stop to ask.
- Plain, readable English. A tired person should get every line in one pass.

The film: "{film.get('title')}".""",
    ]
    if film.get("brief"):
        parts.append("The filmmaker's standing brief for this film:\n" + film["brief"])
    canon = facts_block(film)
    if canon:
        parts.append(canon)
    h = harness()
    if h:
        parts.append("THE HOUSE RULES — FILM_HARNESS.md. Everything you write must respect "
                     "this. (It was written for stills; where this film uses motion, §6 "
                     "applies and the format notes adapt.)\n\n" + h)
    craft = {"premise": ["storycraft", "audience-psychology"],
             "script": ["storycraft"],
             "critique": ["storycraft", "audience-psychology"],
             "breakdown": ["storycraft", "cinematic-language"],
             "card": ["cinematic-language"]}.get(stage, [])
    for name in craft:
        k = _skill(name)
        if k:
            parts.append(f"CRAFT NOTES — {name}:\n\n{k}")
    if stage in ("premise", "script", "critique"):
        c = story_checks()
        if c:
            parts.append("STORY-STAGE CHECKS — hook, coherence, payoff. Write so a draft passes "
                         "these, and test every draft against them:\n\n" + c)
    return "\n\n---\n\n".join(parts)


def facts_block(film) -> str:
    """Kept facts are canon. Every stage writes inside them."""
    facts = [r for r in film.get("refs", []) if r.get("kind") == "fact" and r.get("status") == "kept"]
    if not facts:
        return ""
    label = {"true": "TRUE (researched — the world works this way)",
             "decided": "DECIDED (our choice — never contradict it)",
             "open": "OPEN ON PURPOSE (a question we keep unanswered — never answer it, never let it read as a mistake)"}
    out = ["THE FILM'S FACTS — kept by the filmmaker. Treat them as canon."]
    for kind in ("decided", "true", "open"):
        group = [f for f in facts if f.get("fact_kind") == kind]
        if group:
            out.append(f"\n{label[kind]}:")
            out += [f"- {f['text']}" + (f"  [{f['source']}]" if f.get("source") else "") for f in group]
    return "\n".join(out)


FACTS_RULE = f"""After everything else, write a line containing exactly
{FACTS}
and then a JSON array of the facts this work establishes or depends on that a later draft must
remember — names, ages, places, dates, how a thing works, what a character knows when, and every
question deliberately left open. Each item: {{"fact": "...", "kind": "decided" | "true" | "open",
"where": "scene or beat"}}. "true" only for real-world facts. Skip facts already in THE FILM'S
FACTS. Ten at most; only what would cause a contradiction if forgotten."""


def _block(title, text):
    return f"<{title}>\n{text.strip()}\n</{title}>"


def _notes(version) -> str:
    notes = [n["text"] for n in (version or {}).get("notes", []) if not n.get("done")]
    if not notes:
        return ""
    return _block("the_filmmakers_notes",
                  "\n".join(f"- {n}" for n in notes) +
                  "\n\nThese notes come first. Address every one of them.")


# ---------------------------------------------------------------- premise

PREMISE_SHAPE = """Write the premise page in Markdown with exactly these sections:

# Premise — {title}

## The one-sentence ache
One sentence a viewer carries for the rest of their life. Not a logline.

## Send test
The specific person a viewer would send this film to.

## Logline
"X must do Y or lose Z" — specific.

## The irony engine
What the audience knows that the characters don't, and for how long.

## The bookend
The opening and closing image/words/place, transformed by everything between.

## The clip
The one scene that stands alone as a 60–90s short with zero context.

## The people
For each tracked character (keep it to as few as the story needs): who they are, want vs
need, and the wound layer — ghost → wound → lie — specific, never on screen.

## Shape
Six to ten sentences: the Pixar spine. Know the ending.

## Format & feel
Runtime target, look, sound, the music's job. One short paragraph.

## Choices I made
Bullets: anything the material left open that you decided."""


def premise(film, source_kind, source_text, prev=None, prev_text=None):
    task = [PREMISE_SHAPE.format(title=film["title"])]
    if source_kind == "idea":
        task.insert(0, _block("the_idea", source_text))
    else:
        task.insert(0, _block("the_script", source_text) +
                    "\nThe filmmaker started from this script. Draw the premise OUT of it — "
                    "describe the film that is there, and name in 'Choices I made' anything "
                    "the script doesn't yet support.")
    if prev_text:
        task.insert(1, _block("previous_premise", prev_text))
        task.insert(2, _notes(prev) or "Improve the previous premise where it is weakest.")
        task.append("Rewrite the whole premise page, keeping what works.")
    return "\n\n".join(t for t in task if t)


# ---------------------------------------------------------------- script

SCRIPT_FORMAT = f"""Write the screenplay in Fountain (plain-text screenplay format):
- Scene headings on their own line: INT. CAFÉ - DAY / EXT. HILL ROAD - DUSK. Number nothing.
- Action in present tense, short paragraphs, images not explanations.
- Character cue in CAPS on its own line, dialogue under it. Parentheticals sparingly, in ( ).
- Voiceover as NAME (V.O.). Music cues as action lines starting with "MUSIC:".
- Transitions (CUT TO:, SMASH CUT TO:) only when they carry meaning, right after a blank line.
- No title page, no markdown headings, no bold.

After the screenplay, write a line containing exactly
{SPLIT}
and then 3–8 bullets on what this version does and what you chose."""


def script_draft(film, premise_text, idea_text):
    return "\n\n".join([
        _block("approved_premise", premise_text),
        _block("the_original_idea", idea_text) if idea_text else "",
        "Write the first full draft of the film.", SCRIPT_FORMAT, FACTS_RULE])


def script_revise(film, premise_text, script_text, version, critique_text):
    return "\n\n".join(t for t in [
        _block("approved_premise", premise_text) if premise_text else "",
        _block(f"script_version_{version['n']}", script_text),
        _block("critique_of_this_version", critique_text) if critique_text else "",
        _notes(version),
        "Write the next full draft. Rewrite for turns, not polish: strengthen the turning "
        "points, convert conveniences into shown choices, cut what the image already says. "
        "Close every gap the critique marks as unintended, with the smallest fix it allows — "
        "one image, one line, one insert. Leave every question marked open on purpose open. "
        "Keep what works — do not re-inflate the drama.",
        SCRIPT_FORMAT, FACTS_RULE] if t)


CRITIQUE_SHAPE = """Write a critique of this draft in Markdown. Your job is to find what is
emotionally false — not to praise. Sections, in order:

# Critique of draft {n}

## What's working
Short. Only what must be protected in the next draft.

## What's emotionally false
The heart of it. Quote the line or moment, say why it's false, offer the better alternative
("yes, and" — never a bare rejection).

## Gate B
- **Invariants** — each harness invariant: present / weak / missing, one line each.
- **Plant/payoff ledger** — a two-column table; flag every orphan on either side.
- **Timeline ledger** — ages/dates at each act break; flag contradictions.
- **Conveniences** — where the story bends because the writer needed it to.
- **Fresh-Mind Audit** — every line that fails one-pass parse, attribution, or pronoun
  check, quoted, with a rewrite.

## Hook · Coherence · Payoff
Run the STORY-STAGE CHECKS on this draft. A table with three rows: Hook (the first 10 seconds),
Coherence (the causal chain, plants and payoffs, one map), Payoff (the final image, the visible
choice, the cue). Columns: Verdict (strong / weak / missing) | The exact line or image, quoted |
Why | The smallest fix, written out. Add two more lines under the table: the result of the muted
read, and the one-sentence summary of any montage sequence. Keep the fixes quiet and specific —
a small concrete action or question, never spectacle.

## Gaps a viewer is left with
Watch it as a cold viewer who knows only what the film has shown. List EVERY question they would
be left asking — who is this, why did she do that, how does he know, what happened to the car.
A table: Question | Where it arises | Open on purpose? | The fill.
- **Open on purpose** only when the question IS the plot's intent (a mystery the ending
  depends on, an ache the film wants carried out of the room). Say what the openness does.
  Anything already listed as OPEN ON PURPOSE in the film's facts stays open.
- Otherwise it is a gap, and the fill is the smallest thing that answers it on screen — one
  image, one line, one insert, a prop in frame — written out exactly, with where it goes.
  Never fill a gap with a voiceover explanation if an image can do it.

## Flat-script diagnostics
Gap test, but/therefore, so-what, remove-a-scene, on-the-nose — only the failures.

## Summary of what needs fixing
A numbered list, most important first, including every weak or missing hook, coherence or payoff
verdict and every unintended gap. This is what the next draft answers."""


def critique(film, premise_text, script_text, version):
    return "\n\n".join(t for t in [
        _block("approved_premise", premise_text) if premise_text else "",
        _block(f"script_draft_{version['n']}", script_text),
        CRITIQUE_SHAPE.format(n=version["n"]), FACTS_RULE] if t)


# ---------------------------------------------------------------- breakdown

BEAT = {"type": "object", "additionalProperties": False,
        "required": ["id", "shown", "said", "music", "conveys", "moves_forward",
                     "iconic_frame", "seconds"],
        "properties": {
            "id": {"type": "string", "description": "scene number + letter, e.g. 3B"},
            "shown": {"type": "string", "description": "what is on screen — images, not explanations"},
            "said": {"type": "string", "description": "exact dialogue/VO in this beat, or empty"},
            "music": {"type": "string", "description": "cue and prominence (foreground/under/silent)"},
            "conveys": {"type": "string", "description": "what the viewer feels/learns"},
            "moves_forward": {"type": "string", "description": "how the story advances"},
            "iconic_frame": {"type": "string", "description": "the single frame that holds this beat"},
            "seconds": {"type": "number"}}}

BREAKDOWN_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["logline", "acts", "cast", "locations", "props", "budgets", "audit"],
    "properties": {
        "logline": {"type": "string"},
        "acts": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["id", "title", "purpose", "scenes"],
            "properties": {
                "id": {"type": "string"}, "title": {"type": "string"},
                "purpose": {"type": "string"},
                "scenes": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["id", "heading", "int_ext", "time", "location", "title",
                                 "summary", "characters", "beats"],
                    "properties": {
                        "id": {"type": "string", "description": "scene number, '1', '2'…"},
                        "heading": {"type": "string"},
                        "int_ext": {"type": "string", "enum": ["INT", "EXT", "INT/EXT"]},
                        "time": {"type": "string"},
                        "location": {"type": "string"},
                        "title": {"type": "string", "description": "2–5 word name"},
                        "summary": {"type": "string"},
                        "characters": {"type": "array", "items": {"type": "string"}},
                        "beats": {"type": "array", "items": BEAT}}}}}}},
        "cast": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["name", "role", "tracked", "description"],
            "properties": {"name": {"type": "string"}, "role": {"type": "string"},
                           "tracked": {"type": "boolean",
                                       "description": "face must be recognised across scenes"},
                           "description": {"type": "string"}}}},
        "locations": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["name", "description"],
            "properties": {"name": {"type": "string"}, "description": {"type": "string"}}}},
        "props": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["name", "why"],
            "properties": {"name": {"type": "string"},
                           "why": {"type": "string", "description": "what it carries in the story"}}}},
        "budgets": {"type": "object", "additionalProperties": False,
                    "required": ["runtime_seconds", "tracked_faces", "vo_share_percent",
                                 "thesis_lines", "longest_despair_chain", "notes"],
                    "properties": {"runtime_seconds": {"type": "number"},
                                   "tracked_faces": {"type": "integer"},
                                   "vo_share_percent": {"type": "number"},
                                   "thesis_lines": {"type": "integer"},
                                   "longest_despair_chain": {"type": "integer"},
                                   "notes": {"type": "string"}}},
        "audit": {"type": "array", "items": {"type": "string"},
                  "description": "problems found while breaking down: redundant beats, VO "
                                 "the image already says, budget overruns"}}}


def breakdown(film, script_text, prev_data=None, prev_version=None):
    parts = [_block("approved_script", script_text)]
    if prev_data:
        parts.append(_block("previous_breakdown", json.dumps(prev_data, indent=1)))
        parts.append(_notes(prev_version) or "Improve the previous breakdown where it is weakest.")
    parts.append("""Break the approved script down for production (Gate C). Acts → scenes → beats.
Every beat: Shown / Said / Music (with prominence) / Conveys / Moves forward / Iconic frame /
seconds. If two beats convey and move forward the same thing, merge them. Count the budgets —
runtime from a timed read of the words plus the time images need, tracked faces, VO share,
thesis lines, longest run of beats with no warmth/humour/beauty. List every problem you find
in `audit`. Cast: only real characters (groups are texture, not cast). Props: only objects
that carry meaning. Locations: distinct places, named plainly.""")
    return "\n\n".join(t for t in parts if t)


def breakdown_md(data: dict, title: str) -> str:
    out = [f"# Breakdown — {title}", "", f"_{data.get('logline', '')}_", ""]
    b = data.get("budgets", {})
    out += ["## Budgets", "",
            f"- Runtime: {b.get('runtime_seconds', 0) / 60:.1f} min",
            f"- Tracked faces: {b.get('tracked_faces')}",
            f"- VO share: {b.get('vo_share_percent')}%",
            f"- Thesis lines: {b.get('thesis_lines')}",
            f"- Longest despair chain: {b.get('longest_despair_chain')}",
            f"- {b.get('notes', '')}", ""]
    for act in data.get("acts", []):
        out += [f"## Act {act['id']} — {act['title']}", "", act.get("purpose", ""), ""]
        for sc in act.get("scenes", []):
            out += [f"### {sc['id']}. {sc['heading']} — {sc['title']}", "", sc["summary"], "",
                    "| Beat | Shown | Said | Music | Conveys | Moves forward | Iconic frame | s |",
                    "|---|---|---|---|---|---|---|---|"]
            for bt in sc.get("beats", []):
                cells = [bt.get(k, "") for k in ("id", "shown", "said", "music", "conveys",
                                                  "moves_forward", "iconic_frame")]
                cells = [str(c).replace("|", "/").replace("\n", " ") for c in cells]
                out.append("| " + " | ".join(cells) + f" | {bt.get('seconds', '')} |")
            out.append("")
    out += ["## Cast", ""] + [f"- **{c['name']}** — {c['role']}. {c['description']}"
                              for c in data.get("cast", [])]
    out += ["", "## Locations", ""] + [f"- **{l['name']}** — {l['description']}"
                                       for l in data.get("locations", [])]
    out += ["", "## Props", ""] + [f"- **{p['name']}** — {p['why']}" for p in data.get("props", [])]
    out += ["", "## Audit", ""] + [f"- {a}" for a in data.get("audit", [])]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- pre-production cards

PROMPT_RULE = """Reference prompts go in fenced blocks marked ```prompt so the generation stage
can pick them up. Write each prompt as plain visual description for an image model: subject,
framing, lens, light, palette, texture. No story words, no names, no feelings — only what a
camera would see."""

CARD_SHAPE = {
    "character": """# {title}

## Who they are
## The wound layer
Ghost → wound → lie. Specific. Never on screen; every scene knows it.
## Want vs need
## Look
Age, build, face, hair, skin, hands, distinguishing marks — specific enough to lock a face.
## Wardrobe
Per scene or life-stage. Fabric, colour, wear.
## How they move and speak
## Never
Things this person would never do, wear or say.
## Reference sheet prompt
One ```prompt block: a 3-panel character sheet (front, three-quarter, profile), full figure,
flat 18% neutral grey background, shadowless even light.
## Continuity locks
Bullets that must hold in every frame.""",
    "location": """# {title}

## What this place is to the story
## Look
Architecture, materials, scale, what's worn and what's new.
## Light
By time of day as the script uses it.
## Sound
## The frame that sells it
## Reference prompt
One ```prompt block, empty of people.
## Continuity locks""",
    "prop": """# {title}

## What it carries
The meaning it holds, where it's planted and where it pays off.
## Look
Material, size, age, wear, colour.
## Where it appears
Scene and beat ids.
## Reference prompt
One ```prompt block, the object alone on a neutral surface.
## Continuity locks""",
    "scene": """# {title}

## The scene's job
One line: the gap between expectation and result.
## Blocking
Where everyone is, how they move.
## Shot plan
For EACH beat in this scene, a subsection `### Beat <id>` with:
- **Shot** — size, angle, lens (mm), camera move with start and end framing
- **Light & palette**
- **Keyframe prompt** — a ```prompt block for the still that starts the shot
- **Motion prompt** — a ```prompt block: one action, bounded camera, constraints written positively
- **Seconds** · **Transition in/out** · **Keyframe origin** (new image, or last frame of previous shot)
## Sound
## Continuity
What must match the scenes either side.""",
    "music": """# {title}

## The music's job in this film
## Cue sheet
A table: Cue | Scenes/beats | Emotional spec | Tempo/feel | Instrumentation | Prominence |
Diegetic? | Soundalike reference | Licensing plan
## Silences
Where music must stop, and why the silence is earned.
## Recurring motif
## Generation notes
Style-prompt drafts for each original cue, in ```prompt blocks.""",
}


def card(film, item, breakdown_text, script_text, related: list[tuple[str, str]], prev=None,
         prev_text=None, pictures: list[tuple[str, str]] = ()):
    kind = item["kind"]
    parts = [_block("approved_breakdown", breakdown_text)]
    if pictures:
        parts.append(_block("reference_pictures",
            "The filmmaker kept these reference pictures for this card. Open each one with the "
            "Read tool and look at it before writing. Describe what to take from them in the card's "
            "own words — the prompts must stand on their own, never say 'as in the reference'.\n"
            + "\n".join(f"- {path}" + (f" — {note}" if note else "") for path, note in pictures)))
    if kind in ("scene", "music", "character"):
        parts.append(_block("approved_script", script_text))
    for title, text in related:
        parts.append(_block(f"approved_card: {title}", text))
    seed = item.get("seed") or {}
    focus = f"Scene {seed['scene_id']}" if kind == "scene" and seed.get("scene_id") else item["title"]
    if prev_text:
        parts.append(_block("previous_version", prev_text))
        parts.append(_notes(prev) or "Improve the previous version where it is weakest.")
    parts.append(f"Write the pre-production {kind} card for: {focus}.\n"
                 "Stay consistent with every approved card above.\n\n"
                 + CARD_SHAPE[kind].format(title=item["title"]) + "\n\n" + PROMPT_RULE)
    return "\n\n".join(t for t in parts if t)


def split_facts(text: str):
    """(text without the facts tail, [fact dicts])."""
    if FACTS not in text:
        return text.strip(), []
    body, tail = text.split(FACTS, 1)
    m = re.search(r"\[.*\]", tail, re.S)
    facts = []
    if m:
        try:
            facts = [f for f in json.loads(m.group(0)) if isinstance(f, dict) and f.get("fact")]
        except ValueError:
            facts = []
    return body.strip(), facts


def split_changes(text: str):
    """(body, changes, facts) from a draft or revision."""
    text, facts = split_facts(text)
    if SPLIT in text:
        body, changes = text.split(SPLIT, 1)
        return body.strip(), changes.strip(), facts
    return text.strip(), "", facts


# ---------------------------------------------------------------- research (before generation)

RESEARCH_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["pictures", "facts"],
    "properties": {
        "pictures": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["image_url", "page_url", "title", "take"],
            "properties": {
                "image_url": {"type": "string", "description": "direct link to the image file"},
                "page_url": {"type": "string", "description": "the page it was found on"},
                "title": {"type": "string"},
                "take": {"type": "string", "description": "what to take from this picture, in one line"}}}},
        "facts": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["fact", "source_url"],
            "properties": {"fact": {"type": "string"}, "source_url": {"type": "string"}}}}}}


def research(film, item, card_text, steer=""):
    kind = item["kind"]
    what = {"character": "this person's look and wardrobe — real photographs of people, clothes, "
                         "fabrics, hair and hands that match the card. Wardrobe pieces matter most.",
            "location": "this place — real photographs of the architecture, materials, light at the "
                        "times of day the card uses, and the details that sell it.",
            "prop": "this object — real photographs of the exact kind, era, material and wear."}[kind]
    return "\n\n".join(t for t in [
        _block("approved_card", card_text),
        _block("steer", steer) if steer else "",
        f"""Research {item['title']} for the art department before anything is generated.

Pictures: find 6 to 8 reference pictures of {what}
Use WebSearch, then WebFetch the pages to get DIRECT image file URLs — product photos, museum and
archive images, photographers' pages, Wikimedia Commons. Prefer large, clear, well-lit photos of
the real thing over moodboards, collages, renders or AI images. Each one should show something
different (angle, detail, variant). For each, one line on what to take from it.

Facts: up to 5 real-world facts that keep this {kind} true on screen — how it is used, what it
is made of, what era details give it away, what would be wrong. Each with its source page.
Skip anything the card already says."""] if t)

