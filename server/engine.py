"""The writing engine — headless Claude Code (`claude -p`), one process per job.

Each job streams text as it is written, so the page fills in live. When the process ends
the job's `finish` callback turns the result into a new version on disk.

Isolation: no settings sources, no MCP servers, no tools unless a job asks for read-only ones,
no session saved, and a neutral
working directory so no repo CLAUDE.md is picked up. Everything the model should know is
in the system prompt the brief builds.
"""
import json, os, pathlib, shutil, subprocess, tempfile, threading, time, uuid

CWD = pathlib.Path(tempfile.gettempdir()) / "studio-writing-engine"
MAX_PARALLEL = 3
_slots = threading.Semaphore(MAX_PARALLEL)
JOBS: dict[str, "Job"] = {}
_lock = threading.Lock()


class Job:
    def __init__(self, film, item, action, label, model):
        self.id = uuid.uuid4().hex[:10]
        self.film, self.item, self.action, self.label, self.model = film, item, action, label, model
        self.status = "queued"          # queued | running | done | error | cancelled
        self.text = ""
        self.cost = None
        self.error = None
        self.result = None              # what finish() returned (e.g. the new version)
        self.started = time.time()
        self.ended = None
        self.proc = None
        self.changed = threading.Condition()

    def public(self, with_text=True):
        d = {"id": self.id, "film": self.film, "item": self.item, "action": self.action,
             "label": self.label, "model": self.model, "status": self.status,
             "cost": self.cost, "error": self.error, "result": self.result,
             "started": self.started, "ended": self.ended, "chars": len(self.text)}
        if with_text:
            d["text"] = self.text
        return d

    def _bump(self):
        with self.changed:
            self.changed.notify_all()


def claude_bin():
    return shutil.which("claude") or str(pathlib.Path.home() / ".local/bin/claude")


def running_items(film):
    with _lock:
        return {j.item for j in JOBS.values()
                if j.film == film and j.status in ("queued", "running")}


def jobs_for(film):
    with _lock:
        js = [j for j in JOBS.values() if j.film == film]
    return sorted(js, key=lambda j: j.started, reverse=True)


def start(film, item, action, label, model, system, prompt, finish, schema=None, effort=None,
          tools=(), add_dirs=()):
    """Start a job in the background. `finish(job, text, structured)` runs on success
    and returns whatever should be reported back (usually the new version)."""
    if item in running_items(film):
        raise RuntimeError("that item is already being written")
    job = Job(film, item, action, label, model)
    with _lock:
        JOBS[job.id] = job
    threading.Thread(target=_run, args=(job, system, prompt, finish, schema, effort, tools, add_dirs),
                     daemon=True).start()
    return job


def cancel(job_id):
    job = JOBS.get(job_id)
    if job and job.proc and job.status == "running":
        job.status = "cancelled"
        job.proc.terminate()
        job._bump()
    return job


def _run(job, system, prompt, finish, schema, effort, tools=(), add_dirs=()):
    CWD.mkdir(exist_ok=True)
    args = [claude_bin(), "-p",
            "--output-format", "stream-json", "--verbose", "--include-partial-messages",
            "--setting-sources", "", "--strict-mcp-config", "--tools", ",".join(tools),
            "--no-session-persistence", "--model", job.model,
            "--system-prompt", system]
    if tools:
        # only read-only tools are ever granted: Read (reference images), WebSearch, WebFetch
        args += ["--allowedTools", ",".join(tools)]
    for d in add_dirs:
        args += ["--add-dir", str(d)]
    if effort:
        args += ["--effort", effort]
    if schema:
        args += ["--json-schema", json.dumps(schema)]
    with _slots:
        if job.status == "cancelled":
            return
        job.status = "running"
        job._bump()
        structured, final_text, stderr_tail = None, None, ""
        try:
            job.proc = subprocess.Popen(args, cwd=CWD, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True, bufsize=1, env=os.environ.copy())
            job.proc.stdin.write(prompt)
            job.proc.stdin.close()
            for line in job.proc.stdout:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                t = ev.get("type")
                if t == "stream_event":
                    e = ev.get("event", {})
                    d = e.get("delta") or {}
                    if e.get("type") == "content_block_delta":
                        if d.get("type") == "text_delta":
                            job.text += d.get("text", "")
                            job._bump()
                        elif d.get("type") == "input_json_delta":
                            # structured output arrives as tool input; show it growing
                            job.text += d.get("partial_json", "")
                            job._bump()
                elif t == "result":
                    job.cost = ev.get("total_cost_usd")
                    if ev.get("is_error"):
                        job.error = str(ev.get("result") or ev.get("subtype"))[:600]
                    structured = ev.get("structured_output")
                    final_text = ev.get("result")
            job.proc.wait()
            stderr_tail = (job.proc.stderr.read() or "")[-600:]
        except Exception as e:                      # process failed to start, etc.
            job.error = f"{type(e).__name__}: {e}"
        if job.status == "cancelled":
            job.ended = time.time()
            job._bump()
            return
        if not job.error and job.proc and job.proc.returncode not in (0, None):
            job.error = f"claude exited {job.proc.returncode}: {stderr_tail.strip()}"
        if not job.error and schema and structured is None:
            job.error = "the model did not return the structured breakdown"
        if not job.error:
            text = clean(final_text if final_text and not schema else job.text)
            try:
                job.result = finish(job, text, structured)
            except Exception as e:
                job.error = f"saving failed — {type(e).__name__}: {e}"
        job.status = "error" if job.error else "done"
        job.ended = time.time()
        job._bump()


def clean(text: str) -> str:
    """Drop a wrapping code fence if the model fenced the whole document."""
    t = (text or "").strip()
    if t.startswith("```") and t.endswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        t = t.rsplit("```", 1)[0]
    return t.strip()
