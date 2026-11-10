#!/usr/bin/env python3
"""Studio — the server. Stdlib only, so it runs on any python3 with nothing to install.

    python3 software/server/serve.py            start and open the browser
    python3 software/server/serve.py --no-open  start without opening a browser
"""
import json, mimetypes, pathlib, sys, threading, time, webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import briefs, engine, fetch, films, registry, tts
from films import Store, NotFound, Invalid, CARD_KINDS, RESEARCHED_KINDS

WEB = pathlib.Path(__file__).resolve().parent.parent / "web"
PORT = int(__import__("os").environ.get("STUDIO_PORT", 8790))


def store():
    return Store(registry.load()["films_root"])


# ------------------------------------------------------------------ actions

def _text(s, fid, v):
    return s.read(fid, v["file"]) if v else ""


def _approved(film, iid):
    it = film["items"].get(iid)
    if not it or it["approved"] is None:
        return None
    return next(v for v in it["versions"] if v["n"] == it["approved"])


def _version(film, iid, n):
    it = film["items"][iid]
    if n is None:
        return it["versions"][-1] if it["versions"] else None
    return next((v for v in it["versions"] if v["n"] == n), None)


def _propose_facts(s, fid, facts, origin):
    for f in facts:
        kind = f.get("kind") if f.get("kind") in ("true", "decided", "open") else "decided"
        where = f" ({f['where']})" if f.get("where") else ""
        try:
            s.add_fact(fid, f["fact"], fact_kind=kind, status="proposed", by="claude",
                       origin=origin + where)
        except Invalid:
            pass


def _download(s, fid, iid, title, pictures):
    """Save research pictures as findings to review. Spaced out, so one site isn't hammered."""
    got, failed = 0, []
    pictures = [p for p in pictures if isinstance(p, dict) and p.get("image_url")]
    for i, pic in enumerate(pictures):
        if i:
            time.sleep(0.8)
        try:
            blob, ext = fetch.image(pic["image_url"])
        except fetch.FetchError as e:
            failed.append(dict(pic, error=str(e)))
            continue
        s.add_image(fid, blob, ext, status="proposed", by="claude", **{"for": [iid]},
                    note=pic.get("take", ""), title=pic.get("title", ""),
                    source=pic.get("page_url") or pic["image_url"], image_url=pic["image_url"],
                    origin=f"research for {title}")
        got += 1
    return got, failed


def start_research(fid, iid, steer=""):
    """Find reference pictures and real-world facts for one approved card. Findings arrive as
    'proposed' — downloaded into the film folder, waiting for the filmmaker to keep or skip."""
    s = store()
    film = s.load(fid)
    it = s.item(film, iid)
    if it["kind"] not in RESEARCHED_KINDS:
        raise Invalid("only characters, locations and props are researched")
    v = _approved(film, iid) or _version(film, iid, None)
    if not v:
        raise Invalid("draft the card first")
    prompt = briefs.research(film, it, s.read(fid, v["file"]), steer)
    model = registry.writing_model("research")

    def finish(job, text, data):
        got, failed = _download(s, fid, iid, it["title"], data.get("pictures", []))
        for f in data.get("facts", []):
            try:
                s.add_fact(fid, f["fact"], fact_kind="true", status="proposed", by="claude",
                           source=f.get("source_url", ""), **{"for": [iid]},
                           origin=f"research for {it['title']}")
            except Invalid:
                pass
        s.set_research(fid, iid, {"at": films.now(), "pictures": got,
                                  "failed": failed, "cost": job.cost, "steer": steer})
        return {"pictures": got, "facts": len(data.get("facts", [])), "failed": failed}
    return engine.start(fid, f"research:{iid}", "research", f"Researching {it['title']}", model,
                        briefs.system("research", film), prompt, finish,
                        schema=briefs.RESEARCH_SCHEMA, tools=("WebSearch", "WebFetch"))


def run_action(fid, body):
    """Start a writing job. Returns the job. Validation errors raise Invalid."""
    s = store()
    film = s.load(fid)
    if not film.get("entry"):
        raise Invalid("start the film first")
    iid, action = body.get("item"), body.get("action")
    n = body.get("n")
    cfg = registry.load()["writing"]
    effort = cfg.get("effort")

    def idea_text():
        f = film.get("idea_file")
        return s.read(fid, f) if f else ""

    if iid == "premise":
        model = registry.writing_model("premise")
        prev = _version(film, "premise", None)
        if film["entry"] == "script":
            src_kind, src = "script", _text(s, fid, _version(film, "script", 1))
        else:
            src_kind, src = "idea", idea_text()
        prompt = briefs.premise(film, src_kind, src, prev, _text(s, fid, prev) if prev else None)

        def finish(job, text, _):
            v = s.add_version(fid, "premise", text, cost=job.cost, model=job.model,
                              note="Revised from notes" if prev else "First premise")
            if prev:
                s.mark_notes_done(fid, "premise", prev["n"])
            return v
        label = "Revising the premise" if prev else "Writing the premise"
        return engine.start(fid, iid, "premise", label, model,
                            briefs.system("premise", film), prompt, finish, effort=effort)

    if iid == "script":
        premise_v = _approved(film, "premise")
        premise_text = _text(s, fid, premise_v)
        if action == "draft":
            if not premise_v:
                raise Invalid("approve the premise first")
            model = registry.writing_model("draft")
            prompt = briefs.script_draft(film, premise_text, idea_text())

            def finish(job, text, _):
                body_, changes, facts = briefs.split_changes(text)
                v = s.add_version(fid, "script", body_, cost=job.cost, model=job.model,
                                  role="draft", note=changes or "First draft")
                _propose_facts(s, fid, facts, f"draft {v['n']}")
                return v
            return engine.start(fid, iid, "draft", "Writing the first draft", model,
                                briefs.system("script", film), prompt, finish, effort=effort)

        v = _version(film, "script", n)
        if not v:
            raise Invalid("there is no draft yet")
        script_text = _text(s, fid, v)
        if action == "critique":
            model = registry.writing_model("critique")
            prompt = briefs.critique(film, premise_text, script_text, v)

            def finish(job, text, _):
                body_, facts = briefs.split_facts(text)
                out = s.add_critique(fid, v["n"], body_, cost=job.cost, model=job.model)
                _propose_facts(s, fid, facts, f"critique of draft {v['n']}")
                return out
            return engine.start(fid, iid, "critique", f"Critiquing draft {v['n']}", model,
                                briefs.system("critique", film), prompt, finish, effort=effort)
        if action == "revise":
            model = registry.writing_model("draft")
            crit = s.read(fid, v["critique"]["file"]) if v.get("critique") else ""
            if not crit and not any(not x.get("done") for x in v.get("notes", [])):
                raise Invalid("add a note or run a critique first — a revision needs something to answer")
            prompt = briefs.script_revise(film, premise_text, script_text, v, crit)

            def finish(job, text, _):
                body_, changes, facts = briefs.split_changes(text)
                nv = s.add_version(fid, "script", body_, cost=job.cost, model=job.model,
                                   role="revised", note=changes or f"Revision of draft {v['n']}")
                s.mark_notes_done(fid, "script", v["n"])
                _propose_facts(s, fid, facts, f"draft {nv['n']}")
                return nv
            return engine.start(fid, iid, "revise", f"Revising draft {v['n']}", model,
                                briefs.system("script", film), prompt, finish, effort=effort)
        raise Invalid(f"unknown script action {action!r}")

    if iid == "breakdown":
        script_v = _approved(film, "script")
        if not script_v:
            raise Invalid("approve a script draft first")
        model = registry.writing_model("breakdown")
        prev = _version(film, "breakdown", None)
        prev_data = json.loads(s.read(fid, prev["data"])) if prev else None
        prompt = briefs.breakdown(film, _text(s, fid, script_v), prev_data, prev)

        def finish(job, text, data):
            md = briefs.breakdown_md(data, film["title"])
            v = s.add_version(fid, "breakdown", md, data=data, cost=job.cost, model=job.model,
                              note="Revised from notes" if prev else "First breakdown")
            if prev:
                s.mark_notes_done(fid, "breakdown", prev["n"])
            return v
        return engine.start(fid, iid, "breakdown",
                            "Revising the breakdown" if prev else "Breaking the script down",
                            model, briefs.system("breakdown", film), prompt, finish,
                            schema=briefs.BREAKDOWN_SCHEMA, effort=effort)

    if iid and iid.startswith("card:"):
        it = s.item(film, iid)
        bd = _approved(film, "breakdown")
        if not bd:
            raise Invalid("approve the breakdown first")
        script_v = _approved(film, "script")
        related = []
        # scene and music cards lean on every approved character/location/prop card
        if it["kind"] in ("scene", "music"):
            for oid in film["order"]:
                o = film["items"][oid]
                if o["kind"] in ("character", "location", "prop") and o["approved"]:
                    related.append((o["title"], _text(s, fid, _approved(film, oid))))
        prev = _version(film, iid, None)
        model = registry.writing_model("card")
        d = s.dir(fid)
        pictures = [(str(d / r["file"]), r.get("note", "")) for r in film.get("refs", [])
                    if r["kind"] == "image" and r["status"] == "kept" and iid in r["for"]][:8]
        prompt = briefs.card(film, it, _text(s, fid, bd), _text(s, fid, script_v), related,
                             prev, _text(s, fid, prev) if prev else None, pictures)

        def finish(job, text, _):
            v = s.add_version(fid, iid, text, cost=job.cost, model=job.model,
                              note="Revised from notes" if prev else "First pass")
            if prev:
                s.mark_notes_done(fid, iid, prev["n"])
            return v
        verb = "Revising" if prev else "Drafting"
        return engine.start(fid, iid, "card", f"{verb} {it['title']}", model,
                            briefs.system("card", film), prompt, finish, effort=effort,
                            tools=("Read",) if pictures else (),
                            add_dirs=(d / "references",) if pictures else ())

    raise Invalid(f"nothing to run for {iid!r}")


# ------------------------------------------------------------------ http

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body: bytes, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json")

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def _route(self, method):
        u = urlparse(self.path)
        parts = [unquote(p) for p in u.path.strip("/").split("/") if p]
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if parts[:1] != ["api"]:
                return self._static(u.path)
            return self._api(method, parts[1:], q)
        except NotFound as e:
            self.json({"error": str(e)}, 404)
        except (Invalid, ValueError, RuntimeError, fetch.FetchError, tts.VoiceError, ConnectionError) as e:
            self.json({"error": str(e)}, 400)
        except BrokenPipeError:
            pass
        except Exception as e:
            self.json({"error": f"{type(e).__name__}: {e}"}, 500)

    def _api(self, method, p, q):
        s = store()
        if p == ["films"] and method == "GET":
            return self.json({"films": s.list(), "root": str(s.root)})
        if p == ["films"] and method == "POST":
            return self.json({"id": s.create(self.body().get("title", ""))})
        if p == ["config"]:
            if method == "POST":
                return self.json(registry.save(self.body()))
            return self.json(dict(registry.load(), writing_models=registry.WRITING_MODELS,
                                  writing_actions=registry.WRITING_ACTIONS))
        if p == ["voice", "try"] and method == "POST":
            return self._send(200, tts.sample_wav(), "audio/wav")
        if len(p) >= 2 and p[0] == "read":
            r = tts.READINGS.get(p[1])
            if not r:
                raise NotFound("that reading has ended — press Listen again")
            if len(p) == 2:
                return self.json(r.public())
            if p[2] == "seek" and method == "POST":
                r.cursor = max(0, min(int(self.body().get("i", 0)), len(r.chunks) - 1))
                return self.json(r.public())
            if p[2] == "stop" and method == "POST":
                r.stop.set()
                return self.json(r.public())
            i = int(p[2])
            if not (0 <= i < len(r.paths)) or r.paths[i] is None:
                raise NotFound("not voiced yet")
            body = r.paths[i].read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            self.wfile.write(body)
            return
        if len(p) == 2 and p[0] == "jobs" and method == "GET":
            j = engine.JOBS.get(p[1])
            if not j:
                raise NotFound("no such job")
            return self.json(j.public())
        if len(p) == 3 and p[0] == "jobs" and p[2] == "events":
            return self._events(p[1])
        if len(p) == 3 and p[0] == "jobs" and p[2] == "cancel" and method == "POST":
            j = engine.cancel(p[1])
            return self.json(j.public(False) if j else {})

        if len(p) >= 2 and p[0] == "films":
            fid = p[1]
            rest = p[2:]
            if not rest and method == "GET":
                view = s.view(fid, engine.running_items(fid))
                view["jobs"] = [j.public(False) for j in engine.jobs_for(fid)[:20]]
                return self.json(view)
            if rest == ["file"]:
                return self.json({"path": q["path"], "text": s.read(fid, q["path"])})
            if len(rest) == 2 and rest[0] == "ref":
                f = s.ref_path(fid, rest[1])
                if not f.is_file():
                    raise NotFound(rest[1])
                ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
                body = f.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "max-age=31536000, immutable")
                self.end_headers()
                self.wfile.write(body)
                return
            if method != "POST":
                raise NotFound("no such route")
            b = self.body()
            if rest == ["start"]:
                s.start(fid, b.get("entry"), b.get("text", ""), b.get("brief", ""))
                return self.json(s.view(fid))
            if rest == ["run"]:
                return self.json(run_action(fid, b).public())
            if rest == ["save"]:
                it = s.item(s.load(fid), b["item"])
                if it["id"] == "breakdown":
                    raise Invalid("edit the breakdown through notes — it is structured data")
                v = s.add_version(fid, b["item"], b["text"], by="you",
                                  role="edited" if b["item"] == "script" else "draft",
                                  note=b.get("note") or "Edited by hand")
                return self.json(v)
            if rest == ["note"]:
                return self.json(s.add_note(fid, b["item"], int(b["n"]), b.get("text", "")))
            if rest == ["approve"]:
                it = s.approve(fid, b["item"], int(b["n"]))
                film = s.load(fid)
                out = dict(it)
                # approving a character, location or prop starts its reference research
                if (it["kind"] in RESEARCHED_KINDS and not film.get("research", {}).get(it["id"])
                        and f"research:{it['id']}" not in engine.running_items(fid)):
                    out["research_job"] = start_research(fid, it["id"]).id
                return self.json(out)
            if rest == ["unapprove"]:
                return self.json(s.unapprove(fid, b["item"]))
            if rest == ["check"]:
                s.set_check(fid, b["stage"], int(b["idx"]), b.get("value"))
                return self.json({"ok": True})
            if rest == ["read"]:
                r = tts.start(fid, s.dir(fid) / "audio" / "readings", b.get("chunks") or [])
                if b.get("from") is not None:
                    r.cursor = max(0, min(int(b["from"]), len(r.chunks) - 1))
                return self.json(r.public())
            if rest == ["refs", "research"]:
                return self.json(start_research(fid, b["item"], b.get("steer", "")).public(False))
            if rest == ["refs", "retry"]:
                film = s.load(fid)
                state = film.get("research", {}).get(b["item"]) or {}
                it = s.item(film, b["item"])
                got, failed = _download(s, fid, it["id"], it["title"], state.get("failed", []))
                s.set_research(fid, it["id"], {"pictures": state.get("pictures", 0) + got, "failed": failed})
                return self.json({"pictures": got, "failed": len(failed)})
            if rest == ["refs", "research-all"]:
                film = s.load(fid)
                running = engine.running_items(fid)
                started = []
                for iid in film.get("order", []):
                    it = film["items"][iid]
                    if (it["kind"] in RESEARCHED_KINDS and it["versions"]
                            and not film.get("research", {}).get(iid)
                            and f"research:{iid}" not in running):
                        started.append(start_research(fid, iid).id)
                return self.json({"started": len(started)})
            if rest == ["refs", "paste"]:
                blob, ext = fetch.data_url(b.get("data", ""))
                return self.json(s.add_image(fid, blob, ext, **{"for": b.get("for") or []},
                                             note=b.get("note", ""), title=b.get("name", ""),
                                             source=b.get("source", "pasted")))
            if rest == ["refs", "link"]:
                got = fetch.link(b.get("url", "").strip())
                if "image" in got:
                    blob, ext = got["image"]
                    return self.json({"ref": s.add_image(fid, blob, ext, **{"for": b.get("for") or []},
                                                         note=b.get("note", ""), source=b["url"].strip())})
                return self.json(got)
            if rest == ["refs", "pick"]:
                blob, ext = fetch.image(b["image_url"])
                return self.json(s.add_image(fid, blob, ext, **{"for": b.get("for") or []},
                                             note=b.get("note", ""), image_url=b["image_url"],
                                             source=b.get("page_url") or b["image_url"]))
            if rest == ["refs", "fact"]:
                return self.json(s.add_fact(fid, b.get("text", ""), fact_kind=b.get("fact_kind", "decided"),
                                            source=b.get("source", ""), **{"for": b.get("for") or []}))
            if rest == ["refs", "update"]:
                return self.json(s.update_ref(fid, b["id"], b.get("fields", {})))
            if rest == ["setup-cards"]:
                return self.json({"added": s.setup_cards(fid)})
            if rest == ["brief"]:
                film = s.load(fid)
                film["brief"] = b.get("brief", "")
                s.save(fid, film)
                return self.json({"ok": True})
        raise NotFound("no such route")

    def _events(self, job_id):
        """Server-sent events: the job's text as it grows, then its final state."""
        j = engine.JOBS.get(job_id)
        if not j:
            raise NotFound("no such job")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        sent = 0
        last_status = None
        while True:
            with j.changed:
                if j.status in ("queued", "running") and len(j.text) == sent:
                    j.changed.wait(timeout=15)
            chunk = j.text[sent:]
            sent += len(chunk)
            msg = {"t": chunk, "status": j.status}
            if j.status != last_status or chunk:
                if j.status not in ("queued", "running"):
                    msg["job"] = j.public(False)
                self.wfile.write(f"data: {json.dumps(msg)}\n\n".encode())
                self.wfile.flush()
                last_status = j.status
            else:
                self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
            if j.status not in ("queued", "running") and sent >= len(j.text):
                return

    def _static(self, path):
        if path in ("/", "") or not (WEB / path.lstrip("/")).suffix:
            path = "/index.html"                    # client-side routes
        f = (WEB / path.lstrip("/")).resolve()
        if WEB not in f.parents or not f.is_file():
            return self._send(404, b"not found", "text/plain")
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        if f.suffix == ".js":
            ctype = "text/javascript"
        self._send(200, f.read_bytes(), ctype + ("; charset=utf-8" if ctype.startswith("text") else ""))


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    srv.daemon_threads = True
    url = f"http://127.0.0.1:{PORT}/"
    print(f"Studio running at {url}  (films: {registry.load()['films_root']})")
    if "--no-open" not in sys.argv:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
