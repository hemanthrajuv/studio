"""Film store — one folder per film. film.json is the index; every version is its own file.

Nothing is ever overwritten. A new idea, a revision, a hand edit — each one is a new file,
and film.json records which version is approved and what upstream it was based on.

Items and their upstream:
    premise                     (none)
    script                      premise
    breakdown                   script
    card:<kind>:<slug>          breakdown
"""
import datetime, json, pathlib, re, threading

LOCK = threading.RLock()

STAGES = ["premise", "script", "breakdown", "preproduction"]
CARD_KINDS = ["character", "location", "prop", "scene", "music"]
CARD_DIR = {"character": "characters", "location": "locations", "prop": "props",
            "scene": "scenes", "music": "music"}
UPSTREAM = {"premise": None, "script": "premise", "breakdown": "script"}

# Industry revision order (WGA, via Final Draft). Version n takes colour n-1.
REVISION_COLOURS = ["white", "blue", "pink", "yellow", "green", "goldenrod",
                    "buff", "salmon", "cherry"]

# FILM_HARNESS.md §4, the gates this app enforces. Kept short enough to tick.
GATES = {
    "premise": ("A", [
        "One-Sentence Ache written",
        "Send Test passes with a specific person type",
        "Most people who aren't doing well would recognise themselves",
        "Irony engine named: what we know that the characters don't",
        "The clip identified: one scene that stands alone at 60–90s",
    ]),
    "script": ("B", [
        "Every harness invariant present, checked one by one",
        "Plant/payoff ledger written and balanced",
        "Timeline ledger written (ages/dates at every act break)",
        "Conveniences turned into shown choices, or cut",
        "Fresh-Mind Audit passed: one-pass parse, attribution check, callback ledger",
    ]),
    "breakdown": ("C", [
        "Every beat has Shown / Said / Music / Conveys / Moves forward",
        "Iconic frame named for every beat",
        "Budgets audited with counts (runtime, faces, VO share, thesis lines)",
        "Image-first audit: every VO line the image already says is cut",
        "Music licensing decided (soundalike originals planned)",
    ]),
}

# what gets researched before generation, and what a finished review looks like
RESEARCHED_KINDS = ("character", "location", "prop")
REF_STATUS = ("proposed", "kept", "skipped")
FACT_KINDS = ("true", "decided", "open")       # researched · our choice · a mystery we keep


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def slug(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:48] or "item"


def colour(n: int) -> str:
    return REVISION_COLOURS[(n - 1) % len(REVISION_COLOURS)]


class NotFound(Exception):
    pass


class Invalid(Exception):
    pass


class Store:
    def __init__(self, root):
        self.root = pathlib.Path(root).resolve()

    # ---------- folders ----------

    def dir(self, fid: str) -> pathlib.Path:
        d = (self.root / fid).resolve()
        if d.parent != self.root or not d.is_dir():
            raise NotFound(f"no film {fid!r}")
        return d

    def list(self):
        out = []
        if not self.root.exists():
            return out
        for d in sorted(self.root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not d.is_dir() or d.name.startswith("."):
                continue
            idx = d / "film.json"
            if idx.exists():
                f = json.loads(idx.read_text())
                out.append({"id": d.name, "title": f.get("title", d.name), "started": True,
                            "category": f.get("category", "film"),
                            "stage": self._current_stage(f), "updated": f.get("updated")})
            else:
                out.append({"id": d.name, "title": d.name, "started": False,
                            "category": "film", "stage": None, "updated": None})
        return out

    def create(self, title: str) -> str:
        title = title.strip()
        if not title or "/" in title or title.startswith("."):
            raise Invalid("give the film a plain title")
        d = self.root / title
        if d.exists():
            raise Invalid(f"a folder called {title!r} already exists")
        d.mkdir(parents=True)
        return d.name

    # ---------- index ----------

    def load(self, fid: str) -> dict:
        d = self.dir(fid)
        idx = d / "film.json"
        if idx.exists():
            return json.loads(idx.read_text())
        return {"schema": 1, "title": fid, "category": "film", "entry": None,
                "brief": "", "created": None, "updated": None,
                "items": {}, "order": [], "checks": {}}

    def save(self, fid: str, film: dict):
        d = self.dir(fid)
        film["updated"] = now()
        tmp = d / "film.json.tmp"
        tmp.write_text(json.dumps(film, indent=2, ensure_ascii=False))
        tmp.replace(d / "film.json")
        self._write_harness_check(d, film)

    def read(self, fid: str, rel: str) -> str:
        d = self.dir(fid)
        p = (d / rel).resolve()
        if d not in p.parents or not p.is_file():
            raise NotFound(rel)
        return p.read_text()

    # ---------- start ----------

    def start(self, fid: str, entry: str, text: str, brief: str = ""):
        if entry not in ("idea", "script"):
            raise Invalid("entry must be idea or script")
        if not text.strip():
            raise Invalid("paste or write something first")
        with LOCK:
            film = self.load(fid)
            if film.get("entry"):
                raise Invalid("this film has already been started")
            d = self.dir(fid)
            film.update(entry=entry, brief=brief.strip(), created=now())
            idea = d / "idea.md"
            if entry == "idea":
                if idea.exists() and idea.read_text().strip():
                    # never overwrite a real idea file — keep both
                    idea = d / "idea_from_studio.md"
                idea.write_text(text.strip() + "\n")
                film["idea_file"] = idea.name
            for key, title in (("premise", "Premise"), ("script", "Script"),
                               ("breakdown", "Breakdown")):
                film["items"].setdefault(key, self._new_item(key, key, title))
            if entry == "script":
                self._add_version(d, film, "script", text.strip(), by="you",
                                  role="draft", note="Pasted script")
            self.save(fid, film)
            return film

    # ---------- items & versions ----------

    @staticmethod
    def _new_item(iid, kind, title, **extra):
        return {"id": iid, "kind": kind, "title": title, "versions": [],
                "approved": None, **extra}

    def item(self, film, iid):
        it = film["items"].get(iid)
        if not it:
            raise NotFound(f"no item {iid!r}")
        return it

    def upstream_of(self, iid):
        if iid.startswith("card:"):
            return "breakdown"
        return UPSTREAM.get(iid)

    def _version_path(self, d, film, iid, role, ext="md"):
        it = film["items"][iid]
        n = len(it["versions"]) + 1
        if iid == "script":
            # house numbering: one counter across drafts and critiques, append-only
            folder = d / "script"
            folder.mkdir(exist_ok=True)
            seq = film.setdefault("script_seq", 0) + 1
            film["script_seq"] = seq
            return folder / f"{seq:02d}_{role}.{ext}"
        if iid.startswith("card:"):
            _, kind, s = iid.split(":", 2)
            folder = d / "preproduction" / CARD_DIR[kind]
            folder.mkdir(parents=True, exist_ok=True)
            return folder / f"{s}_v{n}.{ext}"
        folder = d / iid
        folder.mkdir(exist_ok=True)
        return folder / f"{iid}_v{n}.{ext}"

    def _based_on(self, film, iid):
        up = self.upstream_of(iid)
        if not up or up not in film["items"]:
            return {}
        return {up: film["items"][up]["approved"]}

    def _add_version(self, d, film, iid, text, by, role="draft", note="", cost=None,
                     data=None, model=None):
        it = film["items"][iid]
        if data is not None:
            jp = self._version_path(d, film, iid, role, "json")
            jp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
            mp = jp.with_suffix(".md")
            mp.write_text(text)
            files = {"file": str(mp.relative_to(d)), "data": str(jp.relative_to(d))}
        else:
            p = self._version_path(d, film, iid, role)
            p.write_text(text if text.endswith("\n") else text + "\n")
            files = {"file": str(p.relative_to(d))}
        v = {"n": len(it["versions"]) + 1, "role": role, "by": by, "at": now(),
             "note": note, "based_on": self._based_on(film, iid), "cost": cost,
             "model": model, "notes": [], **files}
        v["colour"] = colour(v["n"])
        it["versions"].append(v)
        return v

    def add_version(self, fid, iid, text, by="claude", role="draft", note="", cost=None,
                    data=None, model=None):
        with LOCK:
            film = self.load(fid)
            self.item(film, iid)
            v = self._add_version(self.dir(fid), film, iid, text, by, role, note, cost,
                                  data, model)
            self.save(fid, film)
            return v

    def add_critique(self, fid, n, text, cost=None, model=None):
        """A critique belongs to a script version; it is a numbered file, not a version."""
        with LOCK:
            film = self.load(fid)
            it = self.item(film, "script")
            v = self._get_version(it, n)
            p = self._version_path(self.dir(fid), film, "script", "critique")
            p.write_text(text if text.endswith("\n") else text + "\n")
            if v.get("critique"):
                # a second critique of the same draft keeps the first one on record
                v.setdefault("earlier_critiques", []).append(v["critique"])
            v["critique"] = {"file": str(p.relative_to(self.dir(fid))), "at": now(),
                             "cost": cost, "model": model}
            self.save(fid, film)
            return v

    def add_note(self, fid, iid, n, text):
        """Notes are pinned to a version. The next revision is the reply to them."""
        if not text.strip():
            raise Invalid("empty note")
        with LOCK:
            film = self.load(fid)
            v = self._get_version(self.item(film, iid), n)
            v.setdefault("notes", []).append({"text": text.strip(), "at": now(), "done": False})
            self.save(fid, film)
            return v

    def mark_notes_done(self, fid, iid, n):
        with LOCK:
            film = self.load(fid)
            v = self._get_version(self.item(film, iid), n)
            for note in v.get("notes", []):
                note["done"] = True
            self.save(fid, film)

    @staticmethod
    def _get_version(it, n):
        for v in it["versions"]:
            if v["n"] == n:
                return v
        raise NotFound(f"{it['id']} has no version {n}")

    def approve(self, fid, iid, n):
        with LOCK:
            film = self.load(fid)
            it = self.item(film, iid)
            v = self._get_version(it, n)
            up = self.upstream_of(iid)
            if up and film["items"].get(up, {}).get("approved") is None and iid != "script":
                raise Invalid(f"approve the {up} first")
            it["approved"] = n
            v["approved_at"] = now()
            self.save(fid, film)
            return it

    def unapprove(self, fid, iid):
        with LOCK:
            film = self.load(fid)
            it = self.item(film, iid)
            it["approved"] = None
            self.save(fid, film)
            return it

    def set_check(self, fid, stage, idx, value):
        if stage not in GATES or not (0 <= idx < len(GATES[stage][1])):
            raise Invalid("no such check")
        with LOCK:
            film = self.load(fid)
            checks = film.setdefault("checks", {}).setdefault(stage, [False] * len(GATES[stage][1]))
            checks[idx] = bool(value)
            self.save(fid, film)

    # ---------- pre-production cards ----------

    def setup_cards(self, fid):
        """Create one card per character, location, key prop and scene from the approved
        breakdown, plus one music brief. Existing cards are kept, never duplicated."""
        with LOCK:
            film = self.load(fid)
            bd = film["items"].get("breakdown")
            if not bd or bd["approved"] is None:
                raise Invalid("approve the breakdown first")
            v = self._get_version(bd, bd["approved"])
            data = json.loads(self.read(fid, v["data"]))
            wanted = []
            for c in data.get("cast", []):
                wanted.append(("character", c.get("name") or c.get("id"), c))
            for loc in data.get("locations", []):
                wanted.append(("location", loc.get("name") or loc.get("id"), loc))
            for p in data.get("props", []):
                wanted.append(("prop", p.get("name") or p.get("id"), p))
            for act in data.get("acts", []):
                for sc in act.get("scenes", []):
                    wanted.append(("scene", f"{sc.get('id', '')} {sc.get('title', '')}".strip(),
                                   {"scene_id": sc.get("id")}))
            wanted.append(("music", "Music & sound brief", {}))
            added = 0
            for kind, title, seed in wanted:
                iid = f"card:{kind}:{slug(title)}"
                if iid in film["items"]:
                    continue
                film["items"][iid] = self._new_item(iid, kind, title, seed=seed)
                film["order"].append(iid)
                added += 1
            self.save(fid, film)
            return added

    # ---------- references: pictures and facts ----------

    def _new_ref(self, film, **fields):
        film["ref_seq"] = film.get("ref_seq", 0) + 1
        ref = {"id": f"r{film['ref_seq']:03d}", "n": film["ref_seq"], "at": now(),
               "for": [], "note": "", "status": "kept", "by": "you", "origin": "", **fields}
        film.setdefault("refs", []).append(ref)
        return ref

    def add_image(self, fid, data: bytes, ext: str, **fields):
        with LOCK:
            film = self.load(fid)
            ref = self._new_ref(film, kind="image", **fields)
            folder = self.dir(fid) / "references" / "images"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{ref['id']}{ext}"
            path.write_bytes(data)
            ref["file"] = str(path.relative_to(self.dir(fid)))
            self.save(fid, film)
            return ref

    def add_fact(self, fid, text, **fields):
        if not text.strip():
            raise Invalid("empty fact")
        fields.setdefault("fact_kind", "decided")
        if fields["fact_kind"] not in FACT_KINDS:
            raise Invalid("a fact is true, decided, or open on purpose")
        with LOCK:
            film = self.load(fid)
            # the same fact proposed twice (e.g. by two critiques) is kept once
            for r in film.get("refs", []):
                if r["kind"] == "fact" and r["text"].strip().lower() == text.strip().lower():
                    return r
            ref = self._new_ref(film, kind="fact", text=text.strip(), **fields)
            self.save(fid, film)
            return ref

    def update_ref(self, fid, rid, fields: dict):
        allowed = {"note", "for", "status", "text", "fact_kind", "source"}
        with LOCK:
            film = self.load(fid)
            ref = next((r for r in film.get("refs", []) if r["id"] == rid), None)
            if not ref:
                raise NotFound(f"no reference {rid}")
            for k, v in fields.items():
                if k not in allowed:
                    continue
                if k == "status" and v not in REF_STATUS:
                    raise Invalid("status must be proposed, kept or skipped")
                if k == "fact_kind" and v not in FACT_KINDS:
                    raise Invalid("a fact is true, decided, or open on purpose")
                ref[k] = v
            self.save(fid, film)
            return ref

    def set_research(self, fid, iid, state: dict):
        with LOCK:
            film = self.load(fid)
            film.setdefault("research", {})[iid] = dict(film.get("research", {}).get(iid, {}), **state)
            self.save(fid, film)

    def ref_path(self, fid, rid) -> pathlib.Path:
        film = self.load(fid)
        ref = next((r for r in film.get("refs", []) if r["id"] == rid and r.get("file")), None)
        if not ref:
            raise NotFound(rid)
        return self.dir(fid) / ref["file"]

    @staticmethod
    def references_status(film):
        """Gate D: nothing left to review, and every researched card has a kept picture."""
        refs = [r for r in film.get("refs", [])]
        cards = [film["items"][i] for i in film.get("order", []) if film["items"].get(i)
                 and film["items"][i]["kind"] in RESEARCHED_KINDS]
        to_review = sum(1 for r in refs if r["status"] == "proposed")
        missing = [c["title"] for c in cards
                   if not any(r["kind"] == "image" and r["status"] == "kept" and c["id"] in r["for"]
                              for r in refs)]
        return {"to_review": to_review, "missing": missing,
                "ready": bool(cards) and not to_review and not missing}

    # ---------- derived state for the UI ----------

    def status(self, film, iid, running: set):
        it = film["items"][iid]
        if iid in running:
            return "working"
        if not it["versions"]:
            return "empty"
        latest = it["versions"][-1]
        up = self.upstream_of(iid)
        if up and up in film["items"]:
            up_ok = film["items"][up]["approved"]
            seen = latest.get("based_on", {}).get(up)
            if up_ok is not None and seen is not None and seen != up_ok:
                return "stale"
        if it["approved"] == latest["n"]:
            return "approved"
        return "review"

    def _current_stage(self, film):
        items = film.get("items", {})
        if not film.get("entry"):
            return "start"
        for s in ("premise", "script", "breakdown"):
            if items.get(s, {}).get("approved") is None:
                return s
        return "preproduction"

    def view(self, fid, running_items=frozenset()):
        film = self.load(fid)
        out = dict(film)
        out["id"] = fid
        out["stage"] = self._current_stage(film)
        out["gates"] = {k: {"gate": g, "checks": c} for k, (g, c) in GATES.items()}
        out["references"] = self.references_status(film)
        out["items"] = {}
        for iid, it in film["items"].items():
            out["items"][iid] = dict(it, status=self.status(film, iid, running_items))
        return out

    # ---------- HARNESS_CHECK.md ----------

    def _write_harness_check(self, d, film):
        lines = [f"# HARNESS CHECK — {film.get('title', d.name)}",
                 "", "_Written by the studio app from the ticks in its gate panels._", ""]
        for stage, (gate, checks) in GATES.items():
            ticks = film.get("checks", {}).get(stage, [False] * len(checks))
            it = film.get("items", {}).get(stage, {})
            ok = it.get("approved")
            lines.append(f"## Gate {gate} — {stage.title()}"
                         + (f" · approved v{ok}" if ok else ""))
            for i, c in enumerate(checks):
                lines.append(f"- [{'x' if i < len(ticks) and ticks[i] else ' '}] {c}")
            lines.append("")
        rs = self.references_status(film)
        lines.append("## Gate D — References (before any asset is generated)")
        lines.append(f"- [{'x' if not rs['to_review'] else ' '}] Every research finding reviewed"
                     + (f" ({rs['to_review']} left)" if rs["to_review"] else ""))
        lines.append(f"- [{'x' if not rs['missing'] and rs['ready'] else ' '}] Every character, "
                     "location and prop has a kept reference picture"
                     + (f" (missing: {', '.join(rs['missing'])})" if rs["missing"] else ""))
        lines.append("")
        (d / "HARNESS_CHECK.md").write_text("\n".join(lines))
