import { h, render } from "preact";
import { useState, useEffect, useRef, useCallback } from "preact/hooks";
import htm from "htm";
import { markdown, fountain } from "./text.js";

const html = htm.bind(h);
const enc = encodeURIComponent;

// ------------------------------------------------------------------ plumbing

async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  };
  const r = await fetch("/api" + path, opts);
  const d = await r.json().catch(() => ({ error: r.statusText }));
  if (!r.ok) throw new Error(d.error || r.statusText);
  return d;
}

// version files are append-only, so a path's text never changes — cache forever
const files = new Map();
async function readFile(fid, path) {
  const key = fid + "\u0000" + path;
  if (!files.has(key)) files.set(key, api(`/films/${enc(fid)}/file?path=${enc(path)}`).then((d) => d.text));
  try { return await files.get(key); } catch (e) { files.delete(key); throw e; }
}
function useFile(fid, path) {
  const [text, setText] = useState(null);
  useEffect(() => {
    let live = true;
    setText(null);
    if (path) readFile(fid, path).then((t) => live && setText(t)).catch((e) => live && setText(`Could not read ${path}: ${e.message}`));
    return () => { live = false; };
  }, [fid, path]);
  return text;
}

let sayHook = () => {};
const say = (msg, bad = false) => sayHook({ msg, bad, at: Date.now() });

function StatusLine() {
  const [m, setM] = useState(null);
  useEffect(() => { sayHook = setM; }, []);
  useEffect(() => {
    if (!m) return;
    const t = setTimeout(() => setM(null), m.bad ? 7000 : 3200);
    return () => clearTimeout(t);
  }, [m]);
  return m ? html`<div class=${"statusline" + (m.bad ? " bad" : "")} role="status">${m.msg}</div>` : null;
}

function useRoute() {
  const parse = () => location.hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  const [r, setR] = useState(parse());
  useEffect(() => {
    const on = () => { setR(parse()); window.scrollTo(0, 0); };
    addEventListener("hashchange", on);
    return () => removeEventListener("hashchange", on);
  }, []);
  return r;
}
const go = (...parts) => { location.hash = "#/" + parts.map(enc).join("/"); };
const href = (...parts) => "#/" + parts.map(enc).join("/");

const COLOUR_NAME = { white: "White", blue: "Blue", pink: "Pink", yellow: "Yellow", green: "Green", goldenrod: "Goldenrod", buff: "Buff", salmon: "Salmon", cherry: "Cherry" };
const tint = (c) => `var(--rev-${c || "white"})`;
const day = (iso) => iso ? new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" }) : "";
const money = (c) => (c || c === 0) ? `$${c < 0.1 ? c.toFixed(3) : c.toFixed(2)}` : "";
const STAGES = [
  { key: "premise", label: "Premise", gate: "A" },
  { key: "script", label: "Script", gate: "B" },
  { key: "breakdown", label: "Breakdown", gate: "C" },
  { key: "prep", label: "Pre-production" },
  { key: "refs", label: "References", gate: "D" },
];
const RESEARCHED = ["character", "location", "prop"];
const refUrl = (fid, r) => `/api/films/${enc(fid)}/ref/${r.id}`;
const KIND_LABEL = { character: "Characters", location: "Locations", prop: "Props", scene: "Scene plans", music: "Music" };

// ------------------------------------------------------------------ listening

// Speech-friendly text: screenplay shorthand spelled out, ALL-CAPS names softened so the
// voice says "Mara", not "M-A-R-A".
const soften = (t) => t.replace(/\b([A-Z])([A-Z'’]{1,})\b/g, (m, a, b) => a + b.toLowerCase());
function sayHeading(t) {
  return soften(t.replace(/^\s*(INT\.?\/EXT\.?|I\/E\.?)\s*/i, "Interior, exterior. ")
    .replace(/^\s*INT\.?\s+/i, "Interior. ").replace(/^\s*EXT\.?\s+/i, "Exterior. ")
    .replace(/^\s*EST\.?\s+/i, "Establishing. ").replace(/\s+[-–—]\s+/g, ". ")) + ".";
}
function sayCue(t) {
  return soften(t.replace(/\(CONT['’]?D\)/i, "").replace(/\(V\.?O\.?\)/i, ", in voiceover").replace(/\(O\.?S\.?\)/i, ", off screen").trim()) + ":";
}

function readingBlocks(root) {
  const out = [];
  const sp = root.querySelector(".screenplay");
  if (sp) {
    const kids = [...sp.children];
    for (let i = 0; i < kids.length; i++) {
      const el = kids[i];
      const c = el.className;
      if (c.includes("fx-scene")) {
        const t = [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join(" ");
        out.push({ els: [el], text: sayHeading(t) });
      } else if (c.includes("fx-cue")) {
        const els = [el];
        let text = sayCue(el.textContent) + " ";
        while (kids[i + 1] && /fx-(dialogue|paren)/.test(kids[i + 1].className)) {
          const d = kids[++i];
          els.push(d);
          text += (d.className.includes("fx-paren") ? d.textContent.replace(/[()]/g, "") + ", " : soften(d.textContent) + " ");
        }
        out.push({ els, text: text.trim() });
      } else if (c.includes("fx-transition")) {
        out.push({ els: [el], text: soften(el.textContent.replace(/:$/, "")) + "." });
      } else if (el.textContent.trim()) {
        out.push({ els: [el], text: soften(el.textContent.trim()) });
      }
    }
  } else {
    const prose = root.querySelector(".prose");
    if (prose) prose.querySelectorAll("h2, h3, h4, h5, p, li, blockquote, tr").forEach((el) => {
      if (el.closest("figure.prompt") || el.closest("li li") || (el.tagName === "P" && el.closest("li, blockquote"))) return;
      let t = el.tagName === "TR" ? [...el.children].map((c) => c.textContent.trim()).filter(Boolean).join(". ") : el.textContent.trim();
      if (t) out.push({ els: [el], text: t });
    });
  }
  // fold tiny pieces into the next one so the voice doesn't stutter through headings
  const merged = [];
  for (const b of out) {
    const last = merged[merged.length - 1];
    if (last && last.text.length < 60 && last.text.length + b.text.length < 420) { last.els.push(...b.els); last.text += " " + b.text; }
    else merged.push({ els: [...b.els], text: b.text });
  }
  return merged;
}

let listenHook = () => {};
const listen = (fid, root, from = 0) => listenHook({ fid, root, from });

function ListenBar() {
  const [r, setR] = useState(null);            // { fid, blocks, key, total, ready, status, note, error }
  const [i, setI] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [rate, setRate] = useState(() => Number(localStorage.getItem("listenRate") || 1));
  const audio = useRef(null);
  const want = useRef({ i: 0, play: false });
  if (!audio.current) audio.current = new Audio();

  const stop = useCallback(() => {
    audio.current.pause();
    setR((cur) => {
      if (cur) {
        cur.blocks.forEach((b) => b.els.forEach((e) => e.classList.remove("reading", "listenable")));
        cur.cleanup?.();
        if (cur.key) api(`/read/${cur.key}/stop`, {}).catch(() => {});
      }
      return null;
    });
    setPlaying(false);
  }, []);

  const begin = async ({ fid, root, from }) => {
    if (!root) return;
    stop();
    const blocks = readingBlocks(root);
    if (!blocks.length) return say("Nothing to read on this page.", true);
    // clicking a paragraph while listening jumps there
    const handlers = blocks.map((b, k) => { const h = (e) => { if (!e.target.closest("a, button")) jump(k); }; b.els.forEach((el) => { el.classList.add("listenable"); el.addEventListener("click", h); }); return h; });
    const cleanup = () => blocks.forEach((b, k) => b.els.forEach((el) => el.removeEventListener("click", handlers[k])));
    setR({ fid, blocks, cleanup, status: "starting", note: "Connecting to the dev box…", ready: [], total: blocks.length });
    want.current = { i: from, play: true };
    setI(from);
    try {
      const d = await api(`/films/${enc(fid)}/read`, { chunks: blocks.map((b) => b.text), from });
      setR((cur) => cur && cur.blocks === blocks ? { ...cur, ...d } : cur);
    } catch (e) { setR((cur) => cur && { ...cur, status: "error", error: e.message }); }
  };
  useEffect(() => { listenHook = begin; }, []);
  useEffect(() => { const off = () => stop(); addEventListener("hashchange", off); return () => removeEventListener("hashchange", off); }, [stop]);

  // keep asking how far ahead the voice has got
  useEffect(() => {
    if (!r?.key || !["starting", "reading"].includes(r.status)) return;
    const t = setInterval(async () => {
      try { const d = await api(`/read/${r.key}`); setR((cur) => cur && cur.key === d.key ? { ...cur, ...d } : cur); }
      catch (e) { setR((cur) => cur && { ...cur, status: "error", error: e.message }); }
    }, 1500);
    return () => clearInterval(t);
  }, [r?.key, r?.status]);

  // play paragraph i once it's ready
  useEffect(() => {
    if (!r?.key) return;
    r.blocks.forEach((b, k) => b.els.forEach((e) => e.classList.toggle("reading", k === i)));
    const el = r.blocks[i]?.els[0];
    if (el) { const box = el.getBoundingClientRect(); if (box.top < 70 || box.bottom > innerHeight - 90) el.scrollIntoView({ block: "center", behavior: "smooth" }); }
    if (!want.current.play) return;
    if (r.ready[i]) {
      const src = `/api/read/${r.key}/${i}`;
      if (!audio.current.src.endsWith(src)) { audio.current.src = src; audio.current.playbackRate = rate; }
      audio.current.play().then(() => setPlaying(true)).catch(() => setPlaying(false));
    }
  }, [i, r?.key, r?.ready?.[i]]);

  useEffect(() => {
    const a = audio.current;
    const ended = () => {
      setR((cur) => {
        if (cur && i + 1 < cur.total) { want.current.i = i + 1; setI(i + 1); }
        else { setPlaying(false); want.current.play = false; }
        return cur;
      });
    };
    a.addEventListener("ended", ended);
    return () => a.removeEventListener("ended", ended);
  }, [i]);

  const jump = (k) => {
    want.current = { i: k, play: true };
    audio.current.pause();
    audio.current.removeAttribute("src");
    setI(k);
    setR((cur) => { if (cur?.key) api(`/read/${cur.key}/seek`, { i: k }).catch(() => {}); return cur; });
  };
  const toggle = () => {
    if (playing) { audio.current.pause(); want.current.play = false; setPlaying(false); }
    else { want.current.play = true; if (r?.ready[i]) audio.current.play().then(() => setPlaying(true)); else setR((cur) => cur && { ...cur }); }
  };
  const setSpeed = (x) => { setRate(x); audio.current.playbackRate = x; try { localStorage.setItem("listenRate", String(x)); } catch {} };
  useEffect(() => {
    const onKey = (e) => {
      if (!r || e.target.closest?.("input, textarea, select")) return;
      if (e.code === "Space") { e.preventDefault(); toggle(); }
    };
    addEventListener("keydown", onKey);
    return () => removeEventListener("keydown", onKey);
  });

  if (!r) return null;
  const readyCount = (r.ready || []).filter(Boolean).length;
  const waiting = want.current.play && !r.ready?.[i] && r.status !== "error";
  const pad = (n) => String(n).padStart(3, "0");
  return html`<div class="listenbar" role="region" aria-label="Listening">
    <button class="lb-btn" onClick=${() => jump(Math.max(0, i - 1))} aria-label="Previous paragraph" disabled=${i === 0}>⏮</button>
    <button class="lb-btn main" onClick=${toggle} aria-label=${playing ? "Pause" : "Play"}>${playing ? "❚❚" : "▶"}</button>
    <button class="lb-btn" onClick=${() => jump(Math.min(r.total - 1, i + 1))} aria-label="Next paragraph" disabled=${i >= r.total - 1}>⏭</button>
    <span class="lb-count">${pad(i + 1)} / ${pad(r.total)}</span>
    <span class="lb-status">${r.status === "error" ? html`<span class="lb-err">${r.error}</span>`
      : r.note ? r.note : waiting ? "Voicing this paragraph…" : readyCount < r.total ? `${readyCount} of ${r.total} voiced` : "All voiced"}</span>
    <select class="lb-rate" value=${rate} onChange=${(e) => setSpeed(Number(e.target.value))} aria-label="Speed">
      ${[0.9, 1, 1.15, 1.3, 1.5].map((x) => html`<option value=${x}>${x}×</option>`)}</select>
    <button class="lb-btn" onClick=${stop} aria-label="Stop listening">✕</button>
  </div>`;
}

// ------------------------------------------------------------------ app

function App() {
  const route = useRoute();
  let screen;
  if (route[0] === "film" && route[1]) screen = html`<${FilmScreen} key=${route[1]} fid=${route[1]} stage=${route[2]} sub=${route[3]} />`;
  else if (route[0] === "settings") screen = html`<${Settings} />`;
  else screen = html`<${Home} />`;
  return html`${screen}<${ListenBar} /><${StatusLine} />`;
}

function Slate({ film, stage, children }) {
  const running = (film?.jobs || []).filter((j) => j.status === "running" || j.status === "queued");
  return html`<header class="slate">
    <a class="home" href="#/" title="All films">
      <svg viewBox="0 0 20 20" aria-hidden="true"><rect x="3.5" y="2" width="13" height="16" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M6.5 7h7M6.5 10h5M6.5 13h7" stroke="currentColor" stroke-width="1.6"/></svg>
      Studio
    </a>
    ${film && html`<div class="film-title">${film.title}</div>`}
    ${film && film.entry && html`<${Rail} film=${film} stage=${stage} />`}
    ${children}
    <div class="spacer"></div>
    <div class="tools">
      ${running.length > 0 && html`<span class="writing-pill" title=${running.map((j) => j.label).join("\n")}><span class="dot"></span>${running.length === 1 ? running[0].label + "…" : `${running.length} pieces being written…`}</span>`}
      <a class="btn quiet" href="#/settings">Settings</a>
    </div>
  </header>`;
}

function Rail({ film, stage }) {
  const it = film.items;
  const done = (k) => k === "prep" ? false : k === "refs" ? film.references?.ready : it[k]?.approved != null;
  const stale = (k) => k === "prep"
    ? Object.values(it).some((x) => x.id.startsWith("card:") && x.status === "stale")
    : k === "refs" ? film.references?.to_review > 0 : it[k]?.status === "stale";
  const locked = (k) => ({ premise: false, script: film.entry !== "script" && !done("premise"), breakdown: !done("script"), prep: !done("breakdown"), refs: !(film.order || []).length })[k];
  return html`<nav class="rail" aria-label="Stages">
    ${STAGES.map((s) => html`<a href=${href("film", film.id, s.key)} aria-current=${stage === s.key ? "page" : undefined} class=${locked(s.key) ? "locked" : ""}>
      ${s.label}
      ${done(s.key) ? html`<span class="tick" title="Approved">✓</span>` : stale(s.key) ? html`<span class="flagdot" title="Needs a look"></span>` : s.gate ? html`<span class="gate">${s.gate}</span>` : null}
    </a>`)}
  </nav>`;
}

// ------------------------------------------------------------------ home

function Home() {
  const [data, setData] = useState(null);
  const [title, setTitle] = useState("");
  const [err, setErr] = useState("");
  useEffect(() => { api("/films").then(setData).catch((e) => setErr(e.message)); }, []);
  const create = async (e) => {
    e.preventDefault();
    try { const d = await api("/films", { title }); go("film", d.id); } catch (e2) { setErr(e2.message); }
  };
  const where = (f) => !f.started ? "Not started" : { start: "Not started", premise: "Working on the premise", script: "Working on the script", breakdown: "Breaking it down", preproduction: "In pre-production" }[f.stage] || "";
  return html`<${Slate} />
  <main><div class="shelf">
    <h1>Films</h1>
    <p class="sub">Each film is a folder in <code>${data?.root || "…"}</code>. Everything you approve is written there as files.</p>
    ${data && (data.films.length
      ? html`<ul class="films">${data.films.map((f) => html`<li><a href=${href("film", f.id)}>
          <span class="t">${f.title}</span><span class="s">${where(f)}${f.updated ? ` · ${day(f.updated)}` : ""}</span></a></li>`)}</ul>`
      : html`<div class="blank"><p>No films yet. Name your first one below.</p></div>`)}
    <form class="new-film" onSubmit=${create}>
      <input placeholder="Title of a new film" value=${title} onInput=${(e) => setTitle(e.target.value)} aria-label="Title of a new film" />
      <button class="btn primary" disabled=${!title.trim()}>Start a film</button>
    </form>
    ${err && html`<p class="err">${err}</p>`}
  </div></main>`;
}

// ------------------------------------------------------------------ film

function FilmScreen({ fid, stage, sub }) {
  const [film, setFilm] = useState(null);
  const [err, setErr] = useState("");
  const reload = useCallback(async () => {
    try { setFilm(await api(`/films/${enc(fid)}`)); } catch (e) { setErr(e.message); }
  }, [fid]);
  useEffect(() => { reload(); }, [reload]);

  // a quiet heartbeat while something is being written elsewhere in the film
  const busy = film?.jobs?.some((j) => j.status === "running" || j.status === "queued");
  useEffect(() => {
    if (!busy) return;
    const t = setInterval(reload, 4000);
    return () => clearInterval(t);
  }, [busy, reload]);

  if (err) return html`<${Slate} /><main><div class="shelf"><p class="err">${err}</p><a href="#/">Back to films</a></div></main>`;
  if (!film) return html`<${Slate} />`;
  if (!film.entry) return html`<${Slate} film=${film} /><main><${Start} film=${film} onStarted=${(f) => { setFilm(f); reload(); go("film", fid, f.entry === "script" ? "script" : "premise"); }} /></main>`;

  const current = stage || { premise: "premise", script: "script", breakdown: "breakdown", preproduction: "prep" }[film.stage] || "premise";
  let body;
  if (current === "premise") body = html`<${ItemDesk} key="premise" film=${film} iid="premise" reload=${reload} />`;
  else if (current === "script") body = html`<${ItemDesk} key="script" film=${film} iid="script" reload=${reload} />`;
  else if (current === "breakdown") body = html`<${Breakdown} film=${film} reload=${reload} />`;
  else if (current === "refs") body = html`<${References} film=${film} sub=${sub} reload=${reload} />`;
  else body = html`<${Prep} film=${film} sub=${sub} reload=${reload} />`;
  return html`<${Slate} film=${film} stage=${current} /><main>${body}</main>`;
}

// ------------------------------------------------------------------ start

function Start({ film, onStarted }) {
  const [entry, setEntry] = useState("idea");
  const [text, setText] = useState("");
  const [brief, setBrief] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setBusy(true); setErr("");
    try {
      const f = await api(`/films/${enc(film.id)}/start`, { entry, text, brief });
      if (entry === "idea") await api(`/films/${enc(film.id)}/run`, { item: "premise", action: "draft" }).catch((e) => say(e.message, true));
      onStarted(f);
    }
    catch (e) { setErr(e.message); setBusy(false); }
  };
  return html`<div class="start"><div class="page">
    <h2>How does ${film.title} begin?</h2>
    <p class="lede">Start from a loose idea and build the premise first, or bring a script you already have.</p>
    <div class="ways" role="group" aria-label="Way in">
      <button aria-pressed=${entry === "idea"} onClick=${() => setEntry("idea")}><b>I have an idea</b><span>A few lines are enough. We'll find the premise together.</span></button>
      <button aria-pressed=${entry === "script"} onClick=${() => setEntry("script")}><b>I have a script</b><span>It becomes draft 1. The premise is drawn out of it.</span></button>
    </div>
    <textarea class=${"big " + entry} value=${text} onInput=${(e) => setText(e.target.value)}
      placeholder=${entry === "idea" ? "What's the film about? Who's in it? What should it leave someone feeling?" : "Paste the screenplay. Fountain or plain screenplay format both work."}
      aria-label=${entry === "idea" ? "The idea" : "The script"}></textarea>
    <label class="field"><span>Standing brief <small>(optional)</small></span>
      <small>Anything every stage should always know: runtime, format (stills or motion), aspect, tone, what to avoid.</small>
      <textarea class="note" value=${brief} onInput=${(e) => setBrief(e.target.value)} placeholder="e.g. Under 5 minutes. Motion, 16:9. Quiet, no villains."></textarea>
    </label>
    <div class="row"><button class="btn primary" disabled=${!text.trim() || busy} onClick=${submit}>${entry === "idea" ? "Save and write the premise" : "Save as draft 1"}</button></div>
    ${err && html`<p class="err">${err}</p>`}
  </div></div>`;
}

// ------------------------------------------------------------------ the item desk (premise, script, cards)

function runningJob(film, iid) {
  return (film.jobs || []).find((j) => j.item === iid && (j.status === "running" || j.status === "queued"));
}

function ItemDesk({ film, iid, reload, left }) {
  const it = film.items[iid];
  const versions = it.versions;
  const latest = versions[versions.length - 1];
  const [sel, setSel] = useState(latest?.n);
  const [view, setView] = useState("page");            // page | critique | edit
  const [landed, setLanded] = useState(null);
  const count = versions.length;
  useEffect(() => { setSel(latest?.n); setView("page"); }, [count, iid]);
  const v = versions.find((x) => x.n === sel) || latest;
  const job = runningJob(film, iid);
  const isScript = iid === "script";
  const gateKey = iid === "premise" || iid === "script" ? iid : null;

  const run = async (action, extra = {}) => {
    try { await api(`/films/${enc(film.id)}/run`, { item: iid, action, n: v?.n, ...extra }); await reload(); }
    catch (e) { say(e.message, true); }
  };
  const onDone = async (j) => {
    if (j.error) say(j.error, true);
    else if (j.action === "critique") { say("Critique written."); setView("critique"); }
    else say(`Version ${j.result?.n ?? ""} is on the desk.`);
    await reload();
  };

  const stackEl = left || (count > 0 && html`<${VersionStack} it=${it} sel=${v?.n} onSel=${(n, mode) => { setSel(n); setView(mode || "page"); }} view=${view} />`);

  let page;
  if (job) page = html`<${LivePage} job=${job} format=${isScript && job.action !== "critique" ? "fountain" : "markdown"} onDone=${onDone} />`;
  else if (!v) page = html`<${EmptyItem} film=${film} it=${it} run=${run} />`;
  else if (view === "edit") page = html`<${Editor} film=${film} it=${it} v=${v} reload=${reload} onClose=${() => setView("page")} />`;
  else page = html`<${VersionPage} film=${film} it=${it} v=${v} view=${view} landed=${landed === v.n} />`;

  return html`<div class=${"desk" + (stackEl ? "" : " no-left")}>
    ${stackEl && html`<aside class="stack">${stackEl}</aside>`}
    <div>${page}</div>
    <aside class="margin">
      ${!job && v && view !== "edit" && html`<${Actions} film=${film} it=${it} v=${v} latest=${latest} run=${run} view=${view} setView=${setView}
          reload=${reload} gateKey=${gateKey} onApproved=${(n) => setLanded(n)} />`}
      ${!job && v && view !== "edit" && html`<${Notes} film=${film} it=${it} v=${v} reload=${reload} />`}
      ${isScript && v && !job && html`<${FactsToKeep} film=${film} n=${v.n} reload=${reload} />`}
      ${RESEARCHED.includes(it.kind) && html`<${CardRefs} film=${film} it=${it} reload=${reload} />`}
      ${gateKey && html`<${Gate} film=${film} stage=${gateKey} reload=${reload} />`}
      ${v && html`<${About} v=${v} />`}
    </aside>
  </div>`;
}

// ------------------------------------------------------------------ facts proposed by drafts and critiques

function FactsToKeep({ film, n, reload }) {
  const mine = (film.refs || []).filter((r) => r.kind === "fact" && r.status === "proposed"
    && (r.origin.startsWith(`critique of draft ${n}`) || r.origin.startsWith(`draft ${n}`)));
  const kept = (film.refs || []).filter((r) => r.kind === "fact" && r.status === "kept").length;
  const keepAll = async () => {
    try { for (const f of mine) await api(`/films/${enc(film.id)}/refs/update`, { id: f.id, fields: { status: "kept" } }); say(`${mine.length} facts kept.`); await reload(); }
    catch (e) { say(e.message, true); }
  };
  if (!mine.length) return kept ? html`<section><h4>The film's facts</h4>
    <p class="hint">${kept} kept fact${kept > 1 ? "s" : ""} go into every draft and critique. <a href=${href("film", film.id, "refs", "facts")}>See them</a></p></section>` : null;
  return html`<section>
    <h4>Facts to remember</h4>
    <p class="hint">Proposed by draft ${n} and its critique. Kept facts go into every later draft, so nothing contradicts them.</p>
    <ul class="factlist">${mine.map((f) => html`<${FactRow} film=${film} f=${f} reload=${reload} compact />`)}</ul>
    ${mine.length > 1 && html`<div class="row" style="margin-top:8px"><button class="btn small" onClick=${keepAll}>Keep all ${mine.length}</button></div>`}
  </section>`;
}

const FACT_KIND = { true: "True", decided: "Decided", open: "Open on purpose" };

function FactRow({ film, f, reload, compact }) {
  const upd = async (fields) => {
    try { await api(`/films/${enc(film.id)}/refs/update`, { id: f.id, fields }); await reload(); }
    catch (e) { say(e.message, true); }
  };
  return html`<li class=${"fact " + f.status}>
    <p>${f.text}</p>
    <div class="row">
      <select class="kind" value=${f.fact_kind} onChange=${(e) => upd({ fact_kind: e.target.value })} aria-label="Kind of fact">
        ${Object.entries(FACT_KIND).map(([k, l]) => html`<option value=${k}>${l}</option>`)}</select>
      ${f.status !== "kept" && html`<button class="btn small" onClick=${() => upd({ status: "kept" })}>Keep</button>`}
      ${f.status !== "skipped" && html`<button class="btn quiet small" onClick=${() => upd({ status: "skipped" })}>Skip</button>`}
      ${f.status === "skipped" && html`<span class="cost">Skipped</span>`}
    </div>
    ${!compact && html`<small>${[f.origin || (f.by === "you" ? "added by you" : ""), f.source && html`<a href=${f.source} target="_blank" rel="noreferrer">source</a>`].filter(Boolean).map((x, i) => html`${i ? " · " : ""}${x}`)}</small>`}
  </li>`;
}

// ------------------------------------------------------------------ a card's references, in its margin

function researchJob(film, iid) {
  return (film.jobs || []).find((j) => j.item === `research:${iid}` && (j.status === "running" || j.status === "queued"));
}

function CardRefs({ film, it, reload }) {
  const refs = (film.refs || []).filter((r) => r.kind === "image" && r.for.includes(it.id));
  const kept = refs.filter((r) => r.status === "kept");
  const review = refs.filter((r) => r.status === "proposed").length;
  const job = researchJob(film, it.id);
  const done = film.research?.[it.id];
  return html`<section>
    <h4>References</h4>
    ${job ? html`<p class="hint"><span class="state working"></span> Researching pictures and facts…</p>`
      : !done && !refs.length ? html`<p class="hint">Research starts by itself when this card is approved.</p>` : null}
    ${kept.length > 0 && html`<div class="mini-sheet">${kept.slice(0, 6).map((r) => html`<img src=${refUrl(film.id, r)} alt=${r.note || r.title || "reference"} loading="lazy" />`)}</div>`}
    <p class="hint">${kept.length} kept${review ? ` · ${review} to review` : ""}${kept.length ? ". Claude looks at the kept ones when it writes this card." : ""}</p>
    <a class="btn small" href=${href("film", film.id, "refs", it.id)}>${review ? "Review findings" : "Open references"}</a>
  </section>`;
}

// ------------------------------------------------------------------ references: pictures and facts, before any asset

function References({ film, sub, reload }) {
  const refs = film.refs || [];
  const cards = (film.order || []).map((id) => film.items[id]).filter((c) => c && RESEARCHED.includes(c.kind));
  const filter = sub || (film.references?.to_review ? "review" : cards[0]?.id || "mood");
  const card = film.items[filter];
  const [sel, setSel] = useState(null);
  const [showSkipped, setShowSkipped] = useState(false);
  const selected = refs.find((r) => r.id === sel);
  useEffect(() => { setSel(null); setShowSkipped(false); }, [filter]);

  const forList = card ? [card.id] : [];
  const addPasted = async (dataUrl, name) => {
    try { await api(`/films/${enc(film.id)}/refs/paste`, { data: dataUrl, name, for: forList }); say("Reference kept."); await reload(); }
    catch (e) { say(e.message, true); }
  };
  const readFiles = (fileList) => [...fileList].filter((f) => f.type.startsWith("image/")).forEach((f) => {
    const rd = new FileReader();
    rd.onload = () => addPasted(rd.result, f.name);
    rd.readAsDataURL(f);
  });
  useEffect(() => {
    const onPaste = (e) => {
      if (e.target.closest && e.target.closest("input, textarea")) return;
      const files = [...(e.clipboardData?.files || [])];
      if (files.length) { e.preventDefault(); readFiles(files); }
    };
    addEventListener("paste", onPaste);
    return () => removeEventListener("paste", onPaste);
  }, [filter]);
  const [dragging, setDragging] = useState(false);

  const researching = cards.filter((c) => researchJob(film, c.id));
  const unresearched = cards.filter((c) => c.versions.length && !film.research?.[c.id] && !researchJob(film, c.id));
  const researchAll = async () => {
    try { const d = await api(`/films/${enc(film.id)}/refs/research-all`, {}); say(d.started ? `Researching ${d.started} card${d.started > 1 ? "s" : ""}.` : "Every drafted card has been researched."); await reload(); }
    catch (e) { say(e.message, true); }
  };

  // what the sheet shows
  let pics = [], facts = [], title = "", lede = "";
  if (filter === "review") {
    pics = refs.filter((r) => r.kind === "image" && r.status === "proposed");
    facts = refs.filter((r) => r.kind === "fact" && r.status === "proposed");
    title = "To review";
    lede = "Everything research and the critiques turned up. Keep what's right, skip the rest. Nothing is generated until this is empty.";
  } else if (filter === "facts") {
    facts = refs.filter((r) => r.kind === "fact" && (showSkipped || r.status !== "skipped"));
    title = "The film's facts";
    lede = "Kept facts are canon. Every draft, critique and card is written inside them.";
  } else if (filter === "mood") {
    pics = refs.filter((r) => r.kind === "image" && !r.for.length && (showSkipped || r.status !== "skipped"));
    title = "Mood & world";
    lede = "Anything about the film's look as a whole: era, palette, light, texture.";
  } else if (card) {
    pics = refs.filter((r) => r.kind === "image" && r.for.includes(card.id) && (showSkipped || r.status !== "skipped"));
    facts = refs.filter((r) => r.kind === "fact" && r.for.includes(card.id) && (showSkipped || r.status !== "skipped"));
    title = card.title;
    lede = { character: "Look, wardrobe, hair, hands.", location: "Architecture, materials, light at the times the script uses.", prop: "The exact kind, era, material and wear." }[card.kind];
  }
  const order = { proposed: 0, kept: 1, skipped: 2 };
  pics = [...pics].sort((a, b) => order[a.status] - order[b.status] || a.n - b.n);
  const skippedCount = refs.filter((r) => r.status === "skipped" && (filter === "facts" ? r.kind === "fact" : filter === "mood" ? r.kind === "image" && !r.for.length : card && r.for.includes(card.id))).length;

  const count = (c) => {
    const mine = refs.filter((r) => r.kind === "image" && r.for.includes(c.id));
    return { kept: mine.filter((r) => r.status === "kept").length, review: mine.filter((r) => r.status === "proposed").length };
  };
  const rs = film.references || {};
  const nav = html`<nav class="binder" aria-label="References">
    <p class="cost" style="margin:0 0 14px">${rs.ready ? "Gate D passed — ready for generation" : rs.to_review ? `${rs.to_review} finding${rs.to_review > 1 ? "s" : ""} to review` : rs.missing?.length ? `${rs.missing.length} card${rs.missing.length > 1 ? "s" : ""} without a kept picture` : ""}</p>
    <ul>
      <li><a href=${href("film", film.id, "refs", "review")} aria-current=${filter === "review" ? "page" : undefined}><span class=${"state " + (rs.to_review ? "stale" : "approved")}>${rs.to_review ? "" : "✓"}</span>To review${rs.to_review ? ` (${rs.to_review})` : ""}</a></li>
      <li><a href=${href("film", film.id, "refs", "facts")} aria-current=${filter === "facts" ? "page" : undefined}><span class="state"></span>Facts</a></li>
      <li><a href=${href("film", film.id, "refs", "mood")} aria-current=${filter === "mood" ? "page" : undefined}><span class="state"></span>Mood & world</a></li>
    </ul>
    ${RESEARCHED.map((k) => {
      const list = cards.filter((c) => c.kind === k);
      return list.length ? html`<h4>${KIND_LABEL[k]}</h4><ul>${list.map((c) => {
        const n = count(c); const busy = researchJob(film, c.id);
        return html`<li><a href=${href("film", film.id, "refs", c.id)} aria-current=${filter === c.id ? "page" : undefined}>
          <span class=${"state " + (busy ? "working" : n.review ? "stale" : n.kept ? "approved" : "empty")}>${!busy && !n.review && n.kept ? "✓" : ""}</span>${c.title}
          <span class="cost" style="margin-left:auto">${n.kept || ""}</span></a></li>`;
      })}</ul>` : null;
    })}
  </nav>`;

  const onDrop = (e) => { e.preventDefault(); setDragging(false); readFiles(e.dataTransfer.files); };
  return html`<div class="desk">
    <aside class="stack">${nav}</aside>
    <div>
      <article class=${"page" + (dragging ? " dropping" : "")} onDragOver=${(e) => { e.preventDefault(); setDragging(true); }} onDragLeave=${() => setDragging(false)} onDrop=${onDrop}>
        <div class="page-head"><span><b>${title}</b></span><span>${filter !== "review" && filter !== "facts" ? "Paste or drop pictures anywhere on this page" : ""}</span></div>
        <p class="lede-line">${lede}</p>
        ${card && researchJob(film, card.id) && html`<p class="hint"><span class="state working"></span> Researching ${card.title}. Findings land here for review.</p>`}
        ${pics.length > 0 && html`<div class="sheet">${pics.map((r) => html`<${Frame} film=${film} r=${r} selected=${sel === r.id} onSel=${() => setSel(sel === r.id ? null : r.id)} reload=${reload} showFor=${filter === "review"} />`)}</div>`}
        ${facts.length > 0 && html`<h3 class="sheet-h">Facts</h3><ul class="factlist wide">${facts.map((f) => html`<${FactRow} film=${film} f=${f} reload=${reload} />`)}</ul>`}
        ${!pics.length && !facts.length && html`<div class="blank"><p>${filter === "review" ? "Nothing to review. Every finding has been kept or skipped."
          : filter === "facts" ? "No facts yet. Critiques propose them, research finds them, and you can add your own on the right."
          : card && !film.research?.[card.id] ? (card.versions.length ? "Not researched yet. Research starts by itself when the card is approved, or start it now on the right." : "Draft this card in pre-production first; research reads it.")
          : "No pictures yet. Paste or drop one here, or add a link on the right."}</p></div>`}
        ${skippedCount > 0 && filter !== "review" && html`<button class="btn quiet small" onClick=${() => setShowSkipped(!showSkipped)}>${showSkipped ? "Hide" : "Show"} ${skippedCount} skipped</button>`}
      </article>
    </div>
    <aside class="margin">
      ${selected ? html`<${FrameDetail} film=${film} r=${selected} cards=${cards} reload=${reload} onClose=${() => setSel(null)} />` : html`
        ${(unresearched.length > 0 || researching.length > 0) && html`<section><h4>Research</h4>
          <p class="hint">${researching.length ? `Researching ${researching.map((c) => c.title).join(", ")}…` : ""} ${unresearched.length ? `${unresearched.length} drafted card${unresearched.length > 1 ? "s haven't" : " hasn't"} been researched.` : ""}</p>
          ${unresearched.length > 0 && html`<button class="btn primary" onClick=${researchAll}>Research ${unresearched.length > 1 ? `all ${unresearched.length}` : unresearched[0].title}</button>`}</section>`}
        ${card && html`<${ResearchAgain} film=${film} card=${card} reload=${reload} />`}
        ${filter !== "review" && filter !== "facts" && html`<${AddLink} film=${film} forList=${forList} reload=${reload} />`}
        ${(filter === "facts" || card) && html`<${AddFact} film=${film} forList=${forList} reload=${reload} />`}
        <section><h4>Gate D</h4><ul class="checks">
          <li><label><input type="checkbox" disabled checked=${!rs.to_review} /><span>Every finding reviewed${rs.to_review ? ` (${rs.to_review} left)` : ""}</span></label></li>
          <li><label><input type="checkbox" disabled checked=${cards.length > 0 && !rs.missing?.length} /><span>Every character, location and prop has a kept picture${rs.missing?.length ? ` — missing ${rs.missing.join(", ")}` : ""}</span></label></li>
        </ul><p class="hint">Generation waits for both.</p></section>`}
    </aside>
  </div>`;
}

function Frame({ film, r, selected, onSel, reload, showFor }) {
  const upd = async (fields, e) => {
    e?.stopPropagation();
    try { await api(`/films/${enc(film.id)}/refs/update`, { id: r.id, fields }); await reload(); }
    catch (err) { say(err.message, true); }
  };
  const forTitle = r.for.map((id) => film.items[id]?.title).filter(Boolean).join(", ");
  return html`<figure class=${"frame " + r.status + (selected ? " selected" : "")}>
    <button class="shot" onClick=${onSel} aria-label=${`Reference ${r.n}${r.note ? `: ${r.note}` : ""}`}>
      <img src=${refUrl(film.id, r)} alt="" loading="lazy" />
      ${r.status === "kept" && html`<svg class="ring" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true"><path d="M8 52 C 6 22, 40 6, 62 9 C 88 13, 97 36, 93 58 C 89 84, 58 95, 36 92 C 14 89, 4 72, 9 46" /></svg>`}
      <span class="edge-num">${String(r.n).padStart(2, "0")}${r.by === "claude" ? "" : "  ·  yours"}</span>
    </button>
    <figcaption>
      ${showFor && forTitle && html`<b>${forTitle}</b>`}
      <span>${r.note || r.title || ""}</span>
      ${r.status === "proposed" && html`<div class="row"><button class="btn small" onClick=${(e) => upd({ status: "kept" }, e)}>Keep</button><button class="btn quiet small" onClick=${(e) => upd({ status: "skipped" }, e)}>Skip</button></div>`}
    </figcaption>
  </figure>`;
}

function FrameDetail({ film, r, cards, reload, onClose }) {
  const [note, setNote] = useState(r.note || "");
  useEffect(() => setNote(r.note || ""), [r.id]);
  const upd = async (fields) => {
    try { await api(`/films/${enc(film.id)}/refs/update`, { id: r.id, fields }); await reload(); }
    catch (e) { say(e.message, true); }
  };
  const toggleFor = (id) => upd({ for: r.for.includes(id) ? r.for.filter((x) => x !== id) : [...r.for, id] });
  return html`<section class="detail">
    <div class="row" style="justify-content:space-between"><h4 style="margin:0">Frame ${String(r.n).padStart(2, "0")}</h4><button class="btn quiet small" onClick=${onClose}>Close</button></div>
    <a href=${refUrl(film.id, r)} target="_blank" rel="noreferrer"><img class="big" src=${refUrl(film.id, r)} alt=${note || "reference"} /></a>
    ${r.title && html`<p class="hint" style="margin:8px 0 0">${r.title}</p>`}
    <label class="field" style="margin:12px 0"><span>What to take from it</span>
      <textarea class="note" value=${note} onInput=${(e) => setNote(e.target.value)} onBlur=${() => note !== (r.note || "") && upd({ note })}></textarea></label>
    <div class="field" style="margin:0 0 12px"><span>For</span>
      <ul class="checks">${cards.map((c) => html`<li><label><input type="checkbox" checked=${r.for.includes(c.id)} onChange=${() => toggleFor(c.id)} /><span>${c.title}</span></label></li>`)}</ul>
      <small>None ticked means Mood & world.</small></div>
    <div class="row">
      ${r.status !== "kept" && html`<button class="btn stamp small" onClick=${() => upd({ status: "kept" })}>Keep</button>`}
      ${r.status !== "skipped" && html`<button class="btn quiet small" onClick=${() => upd({ status: "skipped" })}>Skip</button>`}
      ${r.status === "kept" && html`<span class="cost">Kept — Claude looks at it when writing</span>`}
    </div>
    <p class="cost" style="margin-top:12px">${r.source && r.source.startsWith("http") ? html`From <a href=${r.source} target="_blank" rel="noreferrer">${new URL(r.source).hostname}</a>` : r.source || ""}${r.origin ? ` · ${r.origin}` : ""}</p>
    <p class="cost">Reference only — never used as a frame that ships.</p>
  </section>`;
}

function ResearchAgain({ film, card, reload }) {
  const [steer, setSteer] = useState("");
  const busy = researchJob(film, card.id);
  const done = film.research?.[card.id];
  const [retrying, setRetrying] = useState(false);
  if (!card.versions.length) return null;
  const retry = async () => {
    setRetrying(true);
    try { const d = await api(`/films/${enc(film.id)}/refs/retry`, { item: card.id }); say(d.pictures ? `${d.pictures} more picture${d.pictures > 1 ? "s" : ""} to review.` : "Those links still won't download."); await reload(); }
    catch (e) { say(e.message, true); }
    setRetrying(false);
  };
  const go_ = async () => {
    try { await api(`/films/${enc(film.id)}/refs/research`, { item: card.id, steer }); setSteer(""); say(`Researching ${card.title}.`); await reload(); }
    catch (e) { say(e.message, true); }
  };
  return html`<section><h4>${done ? "Research again" : "Research"}</h4>
    ${done && html`<p class="hint">Last run found ${done.pictures} picture${done.pictures === 1 ? "" : "s"}${done.failed?.length ? `; ${done.failed.length} link${done.failed.length > 1 ? "s" : ""} wouldn't download` : ""}. ${money(done.cost)}</p>`}
    ${done?.failed?.length > 0 && html`<p class="row" style="margin:-4px 0 10px"><button class="btn small" disabled=${retrying} onClick=${retry}>${retrying ? "Trying again…" : "Try those links again"}</button></p>`}
    <textarea class="note" value=${steer} onInput=${(e) => setSteer(e.target.value)} placeholder="Steer it, e.g. more worn, less showroom; 1970s, not 1960s" aria-label="Steer the research"></textarea>
    <div class="row" style="margin-top:6px"><button class="btn small" disabled=${!!busy} onClick=${go_}>${busy ? "Researching…" : done ? "Find more" : "Research now"}</button></div>
  </section>`;
}

function AddLink({ film, forList, reload }) {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [cands, setCands] = useState(null);
  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setCands(null);
    try {
      const d = await api(`/films/${enc(film.id)}/refs/link`, { url, for: forList });
      if (d.ref) { say("Reference kept."); setUrl(""); await reload(); }
      else setCands(d);
    } catch (err) { say(err.message, true); }
    setBusy(false);
  };
  const pick = async (u) => {
    try { await api(`/films/${enc(film.id)}/refs/pick`, { image_url: u, page_url: cands.page_url, for: forList }); setCands({ ...cands, picked: [...(cands.picked || []), u] }); await reload(); }
    catch (err) { say(err.message, true); }
  };
  return html`<section><h4>Add from a link</h4>
    <p class="hint">An image link is kept as is. A page link shows its pictures to pick from. To add from your computer, paste (⌘V) or drop onto the page.</p>
    <form class="row" onSubmit=${submit}><input class="linkin" value=${url} onInput=${(e) => setUrl(e.target.value)} placeholder="https://…" aria-label="Link" />
      <button class="btn small" disabled=${!url.trim() || busy}>${busy ? "Fetching…" : "Add"}</button></form>
    ${cands && html`<div class="cands"><p class="hint" style="margin:10px 0 6px">${cands.title || "Pictures on that page"} — click to keep</p>
      <div class="mini-sheet">${cands.candidates.map((u) => html`<button class=${"cand" + ((cands.picked || []).includes(u) ? " picked" : "")} onClick=${() => pick(u)} disabled=${(cands.picked || []).includes(u)}>
        <img src=${u} referrerpolicy="no-referrer" loading="lazy" alt="" onError=${(e) => { e.target.parentElement.style.display = "none"; }} /></button>`)}</div></div>`}
  </section>`;
}

function AddFact({ film, forList, reload }) {
  const [text, setText] = useState("");
  const [kind, setKind] = useState("decided");
  const [source, setSource] = useState("");
  const add = async () => {
    try { await api(`/films/${enc(film.id)}/refs/fact`, { text, fact_kind: kind, source, for: forList }); setText(""); setSource(""); await reload(); }
    catch (e) { say(e.message, true); }
  };
  return html`<section><h4>Add a fact</h4>
    <textarea class="note" value=${text} onInput=${(e) => setText(e.target.value)} placeholder="e.g. Mara is 57. She has worked nights for 11 years." aria-label="Fact"></textarea>
    <div class="row" style="margin-top:6px">
      <select value=${kind} onChange=${(e) => setKind(e.target.value)} aria-label="Kind of fact">${Object.entries(FACT_KIND).map(([k, l]) => html`<option value=${k}>${l}</option>`)}</select>
      <input class="linkin" value=${source} onInput=${(e) => setSource(e.target.value)} placeholder="Source link (optional)" aria-label="Source" />
    </div>
    <div class="row" style="margin-top:6px"><button class="btn small" disabled=${!text.trim()} onClick=${add}>Keep fact</button></div>
  </section>`;
}

function VersionStack({ it, sel, onSel, view }) {
  return html`<h4>Versions</h4><ol>
    ${it.versions.map((v) => html`<li>
      <button aria-current=${v.n === sel && view !== "critique" ? "true" : "false"} onClick=${() => onSel(v.n)} style=${{ "--tint": tint(v.colour) }}>
        <span class="edge"></span>
        <span class="v">${it.id === "script" ? `Draft ${v.n}` : `Version ${v.n}`}${it.approved === v.n ? html`<span class="ok">approved</span>` : null}</span>
        <span class="meta">${COLOUR_NAME[v.colour]} · ${v.by === "you" ? "by you" : v.role === "revised" ? "revision" : "Claude"} · ${day(v.at)}</span>
      </button>
      ${v.critique && html`<button class="crit" aria-current=${v.n === sel && view === "critique" ? "true" : "false"} onClick=${() => onSel(v.n, "critique")}>↳ Critique of draft ${v.n}</button>`}
    </li>`)}
  </ol>`;
}

function VersionPage({ film, it, v, view, landed }) {
  const showCritique = view === "critique" && v.critique;
  const text = useFile(film.id, showCritique ? v.critique.file : v.file);
  const isScript = it.id === "script" && !showCritique;
  const approved = it.approved === v.n && !showCritique;
  const isLatest = v.n === it.versions[it.versions.length - 1].n;
  const stale = it.status === "stale" && isLatest;
  const upstreamName = it.id === "script" ? "premise" : it.id === "breakdown" ? "script" : "breakdown";
  const body = text == null ? "" : isScript ? fountain(text).html : markdown(text);
  const ref = useRef();
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const onClick = (e) => {
      const b = e.target.closest("[data-copy]");
      if (!b) return;
      navigator.clipboard.writeText(b.parentElement.querySelector("pre").innerText).then(() => say("Prompt copied."));
    };
    el.addEventListener("click", onClick);
    return () => el.removeEventListener("click", onClick);
  }, []);
  return html`<article class=${"page" + (showCritique ? "" : " tint")} style=${{ "--tint": tint(v.colour) }} ref=${ref}>
    <div class="page-head">
      <span>${showCritique ? html`<b>Critique of draft ${v.n}</b>` : html`<b>${it.id === "script" ? `Draft ${v.n}` : `Version ${v.n}`}</b> · ${COLOUR_NAME[v.colour]} pages`}</span>
      <span class="row" style="gap:12px">${text != null && html`<button class="btn quiet small listen-btn" onClick=${() => listen(film.id, ref.current)} title="Read this page aloud (Qwen3-TTS on the dev box)">
        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2 6h3l4-3v10l-4-3H2z" fill="currentColor"/><path d="M11 5.5c1 .8 1 4.2 0 5M12.8 3.8c2 1.8 2 6.6 0 8.4" fill="none" stroke="currentColor" stroke-width="1.3"/></svg>Listen</button>`}
        ${day(showCritique ? v.critique.at : v.at)}</span>
    </div>
    ${approved && html`<div class=${"stamp-mark" + (landed ? " landing" : "")}>APPROVED<small>v${v.n} · ${day(v.approved_at || v.at)}</small></div>`}
    ${stale && html`<div class="flag-tab">Needs a look — the ${upstreamName} it was written from has changed.</div>`}
    ${!isLatest && !showCritique && html`<p class="cost" style="margin-top:-20px">You're reading an older version. Notes and revisions start from the one you're reading.</p>`}
    ${text == null ? html`<p class="cost">Opening…</p>` : html`<div class=${isScript ? "screenplay" : "prose"} dangerouslySetInnerHTML=${{ __html: body }}></div>`}
  </article>`;
}

function LivePage({ job, format, onDone }) {
  const [text, setText] = useState("");
  const [secs, setSecs] = useState(Math.max(0, Math.round(Date.now() / 1000 - job.started)));
  useEffect(() => {
    let acc = "", closed = false;
    const es = new EventSource(`/api/jobs/${job.id}/events`);
    es.onmessage = (e) => {
      const m = JSON.parse(e.data);
      if (m.t) { acc += m.t; setText(acc); }
      if (m.job) { closed = true; es.close(); onDone(m.job); }
    };
    es.onerror = () => {
      es.close();
      if (closed) return;
      // the stream dropped — fall back to asking for the job's state
      const poll = setInterval(async () => {
        try {
          const j = await api(`/jobs/${job.id}`);
          setText(j.text || "");
          if (j.status !== "running" && j.status !== "queued") { clearInterval(poll); onDone(j); }
        } catch { clearInterval(poll); }
      }, 1500);
    };
    const t = setInterval(() => setSecs(Math.max(0, Math.round(Date.now() / 1000 - job.started))), 1000);
    return () => { es.close(); clearInterval(t); };
  }, [job.id]);
  const cancel = async () => { await api(`/jobs/${job.id}/cancel`, {}); };
  const json = job.action === "breakdown";
  const scenes = json ? (text.match(/"heading"/g) || []).length : 0;
  const beats = json ? (text.match(/"iconic_frame"/g) || []).length : 0;
  const rendered = format === "fountain" ? fountain(text).html : markdown(text);
  return html`<article class="page live" aria-live="polite">
    <div class="live-head"><span><b>${job.label}</b> · ${job.model}</span><span>${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, "0")} <button class="btn quiet small" onClick=${cancel}>Stop</button></span></div>
    ${json
      ? html`<div class="blank"><p>${scenes ? `${scenes} scene${scenes > 1 ? "s" : ""} and ${beats} beat${beats === 1 ? "" : "s"} so far.` : "Reading the script…"}</p><p class="cost">The board appears when the breakdown is complete.</p></div>`
      : text ? html`<div class=${format === "fountain" ? "screenplay" : "prose"} dangerouslySetInnerHTML=${{ __html: rendered }}></div><span class="caret"></span>`
        : html`<div class="blank"><p>Reading everything approved so far…</p></div><span class="caret"></span>`}
  </article>`;
}

function EmptyItem({ film, it, run }) {
  const premiseOk = film.items.premise?.approved != null;
  if (it.id === "premise") return html`<article class="page"><div class="blank">
    <p>${film.entry === "script" ? "The premise will be drawn out of your script: the ache, the irony engine, the clip." : "The premise turns your idea into the film's argument: the one-sentence ache, who it's for, what the audience knows that the characters don't."}</p>
    <button class="btn primary" onClick=${() => run("draft")}>Write the premise</button></div></article>`;
  if (it.id === "script") return html`<article class="page"><div class="blank">
    ${premiseOk ? html`<p>The premise is approved. The first draft is written from it, in screenplay format.</p>
      <button class="btn primary" onClick=${() => run("draft")}>Write the first draft</button>`
      : html`<p>The first draft is written from the approved premise. Approve the premise first.</p>
      <a class="btn" href=${href("film", film.id, "premise")}>Go to the premise</a>`}</div></article>`;
  const what = {
    character: "the wound layer, want and need, a face specific enough to lock, wardrobe per scene, how they move and speak, and a character-sheet prompt",
    location: "what the place is to the story, its look and light at each time of day, its sound, and a reference prompt",
    prop: "what it carries, where it's planted and paid off, its look and wear, and a reference prompt",
    scene: "the scene's job, blocking, and a shot plan for every beat — framing, lens, light, keyframe and motion prompts",
    music: "a cue sheet with the emotional spec for every cue, the silences, the recurring motif, and licensing plans",
  }[it.kind];
  return html`<article class="page"><div class="blank">
    <h2 style="font-family:var(--prose);font-weight:500;font-size:34px;color:var(--ink);margin:0 0 12px">${it.title}</h2>
    <p>This card will hold ${what}.</p>
    <button class="btn primary" onClick=${() => run("draft")}>Draft this card</button></div></article>`;
}

function Actions({ film, it, v, latest, run, view, setView, reload, gateKey, onApproved }) {
  const [confirming, setConfirming] = useState(false);
  const openNotes = (v.notes || []).filter((n) => !n.done).length;
  const isLatest = v.n === latest.n;
  const gate = gateKey && film.gates[gateKey];
  const ticks = gateKey ? (film.checks?.[gateKey] || []) : [];
  const open = gate ? gate.checks.length - gate.checks.filter((_, i) => ticks[i]).length : 0;
  const approved = it.approved === v.n;

  const approve = async () => {
    try {
      const res = await api(`/films/${enc(film.id)}/approve`, { item: it.id, n: v.n }).catch((e) => { throw e; });
      setConfirming(false); onApproved(v.n); await reload();
      say(res.research_job ? `Approved. Researching reference pictures for ${it.title}…` : `Approved ${it.id === "script" ? "draft" : "version"} ${v.n}.`);
    } catch (e) { say(e.message, true); }
  };
  const unapprove = async () => { await api(`/films/${enc(film.id)}/unapprove`, { item: it.id }); await reload(); };

  const primary = [];
  if (it.id === "script") {
    if (!v.critique) primary.push(html`<button class="btn" onClick=${() => run("critique")}>Critique draft ${v.n}</button>`);
    else if (view !== "critique") primary.push(html`<button class="btn" onClick=${() => setView("critique")}>Read the critique</button>`);
    else primary.push(html`<button class="btn" onClick=${() => setView("page")}>Back to the draft</button>`);
    if (v.critique) primary.push(html`<button class="btn quiet small" onClick=${() => run("critique")} title="Writes a new critique of this draft; the current one is kept on record">Critique again</button>`);
    primary.push(html`<button class="btn primary" disabled=${!v.critique && !openNotes}
      title=${!v.critique && !openNotes ? "Add a note or run a critique first" : ""} onClick=${() => run("revise")}>
      Revise into draft ${latest.n + 1}</button>`);
  } else if (isLatest) {
    primary.push(html`<button class="btn primary" onClick=${() => run("revise")}>${openNotes ? `Revise from ${openNotes === 1 ? "the note" : `${openNotes} notes`}` : "Write another version"}</button>`);
  }

  return html`<section>
    <h4>${approved ? "Approved" : "Next"}</h4>
    ${it.id === "script" && html`<p class="hint">${v.critique ? "A revision answers the critique and your notes." : "Critique first, or pin your own notes, then revise."}</p>`}
    <div class="row">${primary}</div>
    <div class="row" style="margin-top:8px">
      ${approved
        ? html`<button class="btn quiet small" onClick=${unapprove}>Take back approval</button>`
        : html`<button class="btn stamp" onClick=${() => (open > 0 ? setConfirming(true) : approve())}>Approve ${it.id === "script" ? "draft" : "v"}${it.id === "script" ? " " : ""}${v.n}</button>`}
      ${it.id !== "breakdown" && html`<button class="btn quiet small" onClick=${() => setView("edit")}>Edit by hand</button>`}
    </div>
    ${confirming && html`<div class="confirm">${open} of ${gate.checks.length} Gate ${gate.gate} checks aren't ticked. Approve anyway?
      <div class="row" style="margin-top:8px"><button class="btn stamp small" onClick=${approve}>Approve anyway</button><button class="btn quiet small" onClick=${() => setConfirming(false)}>Not yet</button></div></div>`}
  </section>`;
}

function Notes({ film, it, v, reload }) {
  const [text, setText] = useState("");
  const add = async () => {
    if (!text.trim()) return;
    try { await api(`/films/${enc(film.id)}/note`, { item: it.id, n: v.n, text }); setText(""); await reload(); }
    catch (e) { say(e.message, true); }
  };
  const notes = v.notes || [];
  return html`<section>
    <h4>Notes on ${it.id === "script" ? "draft" : "version"} ${v.n}</h4>
    ${notes.length ? html`<ul class="notes">${notes.map((n) => html`<li class=${n.done ? "done" : ""}>${n.text}<small>${n.done ? "answered in a later version" : day(n.at)}</small></li>`)}</ul>`
      : html`<p class="hint">Say what feels false, missing or too much. The next version answers every note.</p>`}
    <textarea class="note" value=${text} onInput=${(e) => setText(e.target.value)} placeholder="A note…"
      onKeyDown=${(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) add(); }} aria-label="New note"></textarea>
    <div class="row" style="margin-top:6px"><button class="btn small" disabled=${!text.trim()} onClick=${add}>Pin note</button><span class="cost">⌘↩</span></div>
  </section>`;
}

function Gate({ film, stage, reload }) {
  const g = film.gates[stage];
  const ticks = film.checks?.[stage] || [];
  const set = async (i, value) => {
    try { await api(`/films/${enc(film.id)}/check`, { stage, idx: i, value }); await reload(); }
    catch (e) { say(e.message, true); }
  };
  return html`<section>
    <h4>Gate ${g.gate}</h4>
    <ul class="checks">${g.checks.map((c, i) => html`<li><label><input type="checkbox" checked=${!!ticks[i]} onChange=${(e) => set(i, e.target.checked)} /><span>${c}</span></label></li>`)}</ul>
  </section>`;
}

function About({ v }) {
  const note = v.note && v.note.trim();
  return html`<section>
    <h4>About this version</h4>
    ${note && html`<div class="prose" style="font-size:15px;line-height:1.5" dangerouslySetInnerHTML=${{ __html: markdown(note) }}></div>`}
    <p class="cost">${[v.by === "you" ? "Written by you" : v.model, money(v.cost), v.critique?.cost ? `critique ${money(v.critique.cost)}` : ""].filter(Boolean).join(" · ")}</p>
    <p class="cost"><code>${v.file}</code></p>
  </section>`;
}

function Editor({ film, it, v, reload, onClose }) {
  const original = useFile(film.id, v.file);
  const [text, setText] = useState(null);
  useEffect(() => { if (original != null && text == null) setText(original); }, [original]);
  const save = async () => {
    try { const nv = await api(`/films/${enc(film.id)}/save`, { item: it.id, text }); say(`Saved as version ${nv.n}.`); await reload(); onClose(); }
    catch (e) { say(e.message, true); }
  };
  return html`<article class="page">
    <div class="page-head"><span><b>Editing</b> — saving makes version ${it.versions.length + 1}; version ${v.n} stays as it was.</span>
      <span class="row"><button class="btn quiet small" onClick=${onClose}>Cancel</button><button class="btn primary small" disabled=${text == null || text === original} onClick=${save}>Save as version ${it.versions.length + 1}</button></span></div>
    ${text == null ? html`<p class="cost">Opening…</p>` : html`<textarea class=${"editor" + (it.id === "script" ? "" : " proseedit")} value=${text} onInput=${(e) => setText(e.target.value)} aria-label="Edit text"></textarea>`}
  </article>`;
}

// ------------------------------------------------------------------ breakdown

function Breakdown({ film, reload }) {
  const it = film.items.breakdown;
  const versions = it.versions;
  const latest = versions[versions.length - 1];
  const [sel, setSel] = useState(latest?.n);
  const [landed, setLanded] = useState(null);
  useEffect(() => { setSel(latest?.n); }, [versions.length]);
  const v = versions.find((x) => x.n === sel) || latest;
  const job = runningJob(film, "breakdown");
  const scriptOk = film.items.script?.approved != null;
  const run = async () => {
    try { await api(`/films/${enc(film.id)}/run`, { item: "breakdown", action: "draft" }); await reload(); }
    catch (e) { say(e.message, true); }
  };
  const onDone = async (j) => { if (j.error) say(j.error, true); else say(`Breakdown version ${j.result?.n} is on the desk.`); await reload(); };
  const setup = async () => {
    try { const d = await api(`/films/${enc(film.id)}/setup-cards`, {}); say(d.added ? `${d.added} cards set up.` : "Every card already exists."); await reload(); go("film", film.id, "prep"); }
    catch (e) { say(e.message, true); }
  };
  let page;
  if (job) page = html`<${LivePage} job=${job} format="markdown" onDone=${onDone} />`;
  else if (!v) page = html`<article class="page"><div class="blank">
    ${scriptOk ? html`<p>The approved script gets broken into acts, scenes and beats. Each beat says what's shown, what's said, the music, and the one frame that holds it.</p>
      <button class="btn primary" onClick=${run}>Break the script down</button>`
      : html`<p>The breakdown is made from an approved script. Approve a draft first.</p><a class="btn" href=${href("film", film.id, "script")}>Go to the script</a>`}
  </div></article>`;
  else page = html`<${Board} film=${film} it=${it} v=${v} landed=${landed === v.n} />`;
  const cardsExist = film.order?.length > 0;
  return html`<div class=${"desk" + (versions.length ? "" : " no-left")}>
    ${versions.length > 0 && html`<aside class="stack"><${VersionStack} it=${it} sel=${v?.n} onSel=${setSel} view="page" /></aside>`}
    <div>${page}</div>
    <aside class="margin">
      ${!job && v && html`<${Actions} film=${film} it=${it} v=${v} latest=${latest} run=${run} view="page" setView=${() => {}} reload=${reload} gateKey="breakdown" onApproved=${setLanded} />`}
      ${!job && v && it.approved === v.n && html`<section><h4>Pre-production</h4>
        <p class="hint">${cardsExist ? "Cards are set up. New characters, places or scenes from this version get their own cards." : "Make a card for every character, location, key prop and scene, plus a music brief."}</p>
        <button class="btn primary" onClick=${setup}>${cardsExist ? "Add missing cards" : "Set up pre-production"}</button></section>`}
      ${!job && v && html`<${Notes} film=${film} it=${it} v=${v} reload=${reload} />`}
      <${Gate} film=${film} stage="breakdown" reload=${reload} />
      ${v && html`<${About} v=${v} />`}
    </aside>
  </div>`;
}

function stripColour(sc) {
  const night = /NIGHT|DUSK|EVENING|DARK/i.test(sc.time || "");
  const ext = sc.int_ext === "EXT";
  return `var(--strip-${ext ? "ext" : "int"}-${night ? "night" : "day"})`;
}

function Board({ film, it, v, landed }) {
  const [data, setData] = useState(null);
  const [open, setOpen] = useState({});
  useEffect(() => { setData(null); readFile(film.id, v.data).then((t) => setData(JSON.parse(t))).catch((e) => say(e.message, true)); }, [v.data]);
  const approved = it.approved === v.n;
  if (!data) return html`<article class="page"><p class="cost">Opening…</p></article>`;
  const b = data.budgets || {};
  const allOpen = Object.values(open).filter(Boolean).length > 0;
  const toggleAll = () => {
    if (allOpen) return setOpen({});
    const o = {}; data.acts.forEach((a) => a.scenes.forEach((s) => { o[s.id] = true; })); setOpen(o);
  };
  const secs = (sc) => Math.round(sc.beats.reduce((t, x) => t + (x.seconds || 0), 0));
  return html`<article class="page tint" style=${{ "--tint": tint(v.colour) }}>
    <div class="page-head"><span><b>Breakdown, version ${v.n}</b> · ${COLOUR_NAME[v.colour]} pages</span><span>${day(v.at)}</span></div>
    ${approved && html`<div class=${"stamp-mark" + (landed ? " landing" : "")}>APPROVED<small>v${v.n} · ${day(v.approved_at || v.at)}</small></div>`}
    ${it.status === "stale" && v.n === it.versions[it.versions.length - 1].n && html`<div class="flag-tab">Needs a look — the script it was made from has changed.</div>`}
    <p class="prose" style="margin:0 0 22px;font-style:italic">${data.logline}</p>
    <div class="budgets">
      <div><b>${(b.runtime_seconds / 60).toFixed(1)}</b><span>minutes</span></div>
      <div><b>${b.tracked_faces}</b><span>tracked faces</span></div>
      <div><b>${b.vo_share_percent}%</b><span>voiceover</span></div>
      <div><b>${b.thesis_lines}</b><span>thesis lines</span></div>
      <div><b>${b.longest_despair_chain}</b><span>longest despair run</span></div>
    </div>
    <div class="row" style="justify-content:space-between;margin-bottom:12px">
      <div class="legend"><span><i style="background:var(--strip-int-day)"></i>Interior day</span><span><i style="background:var(--strip-ext-day)"></i>Exterior day</span><span><i style="background:var(--strip-int-night)"></i>Interior night</span><span><i style="background:var(--strip-ext-night)"></i>Exterior night</span></div>
      <button class="btn quiet small" onClick=${toggleAll}>${allOpen ? "Fold all scenes" : "Open all scenes"}</button>
    </div>
    <div class="board">
      ${data.acts.map((act) => html`
        <div class="act-head"><h3>${act.title}</h3><p>${act.purpose}</p></div>
        ${act.scenes.map((sc) => html`
          <button class="strip" style=${{ "--strip": stripColour(sc) }} aria-expanded=${!!open[sc.id]} onClick=${() => setOpen({ ...open, [sc.id]: !open[sc.id] })}>
            <span class="num">${sc.id}</span>
            <span><span class="slug">${sc.heading}</span><br /><span class="sum">${sc.title} — ${sc.summary}</span></span>
            <span class="len">${sc.beats.length} beat${sc.beats.length === 1 ? "" : "s"} · ${secs(sc)}s</span>
          </button>
          ${open[sc.id] && html`<div class="beats">${sc.beats.map((bt) => html`<div class="beat">
            <span class="id">${bt.id}</span>
            <dl>
              <dt>Shown</dt><dd>${bt.shown}</dd>
              ${bt.said && html`<dt>Said</dt><dd class="said">${bt.said}</dd>`}
              <dt>Music</dt><dd>${bt.music}</dd>
              <dt>Conveys</dt><dd>${bt.conveys}</dd>
              <dt>Moves forward</dt><dd>${bt.moves_forward}</dd>
              <dt>Iconic frame</dt><dd class="frame">${bt.iconic_frame}</dd>
              <dt>Seconds</dt><dd>${bt.seconds}</dd>
            </dl></div>`)}</div>`}
        `)}
      `)}
    </div>
    ${b.notes && html`<p class="cost" style="margin-top:18px">${b.notes}</p>`}
    ${data.audit?.length > 0 && html`<div class="audit"><h4>Problems found while breaking down</h4><ul>${data.audit.map((a) => html`<li>${a}</li>`)}</ul></div>`}
  </article>`;
}

// ------------------------------------------------------------------ pre-production

function Prep({ film, sub, reload }) {
  const cards = (film.order || []).map((id) => film.items[id]).filter(Boolean);
  const bdOk = film.items.breakdown?.approved != null;
  const next = cards.find((c) => c.status === "empty" || c.status === "review" || c.status === "stale");
  const current = film.items[sub] || next || cards[0];
  // pin the open card in the address so approving it doesn't whisk it away
  useEffect(() => { if (current && !film.items[sub]) history.replaceState(null, "", href("film", film.id, "prep", current.id)); }, [sub, current?.id]);
  if (!cards.length) return html`<div class="desk no-left"><article class="page"><div class="blank">
      ${bdOk ? html`<p>The breakdown is approved. Set up a card for every character, location, key prop and scene, plus a music brief, and approve them one by one.</p>
        <button class="btn primary" onClick=${async () => { try { await api(`/films/${enc(film.id)}/setup-cards`, {}); await reload(); } catch (e) { say(e.message, true); } }}>Set up pre-production</button>`
        : html`<p>Pre-production starts from an approved breakdown.</p><a class="btn" href=${href("film", film.id, "breakdown")}>Go to the breakdown</a>`}
    </div></article><aside class="margin"></aside></div>`;
  const after = cards.find((c, i) => i > cards.indexOf(current) && c.status !== "approved") || (next !== current ? next : null);
  const groups = Object.keys(KIND_LABEL).map((k) => [k, cards.filter((c) => c.kind === k)]).filter(([, l]) => l.length);
  const doneCount = cards.filter((c) => c.status === "approved").length;
  const binder = html`<nav class="binder" aria-label="Cards">
    <p class="cost" style="margin:0 0 14px">${doneCount} of ${cards.length} approved</p>
    ${groups.map(([k, list]) => html`<h4>${KIND_LABEL[k]}</h4><ul>${list.map((c) => html`<li>
      <a href=${href("film", film.id, "prep", c.id)} aria-current=${c.id === current.id ? "page" : undefined}><span class=${"state " + c.status}>${c.status === "approved" ? "✓" : ""}</span>${c.title}</a></li>`)}</ul>`)}
  </nav>`;
  const waiting = current.kind === "scene" || current.kind === "music"
    ? cards.filter((c) => ["character", "location", "prop"].includes(c.kind) && c.status !== "approved").length : 0;
  return html`<div>
    ${current.status === "approved" && after && html`<p class="cost" style="text-align:center;margin:-10px 0 16px">Approved. Next up: <a href=${href("film", film.id, "prep", after.id)}>${after.title}</a></p>`}
    ${waiting > 0 && current.status === "empty" && html`<p class="cost" style="text-align:center;margin:-10px 0 16px">${waiting} character, location or prop card${waiting > 1 ? "s aren't" : " isn't"} approved yet. ${KIND_LABEL[current.kind]} are written against the approved ones.</p>`}
    <${ItemDesk} key=${current.id} film=${film} iid=${current.id} reload=${reload} left=${binder} />
  </div>`;
}

// ------------------------------------------------------------------ settings

function Settings() {
  const [cfg, setCfg] = useState(null);
  const [dirty, setDirty] = useState(false);
  const [err, setErr] = useState("");
  const [nm, setNm] = useState({ id: "", label: "", kind: "image", lane: "kie", note: "" });
  useEffect(() => { api("/config").then(setCfg).catch((e) => setErr(e.message)); }, []);
  if (!cfg) return html`<${Slate} /><main>${err && html`<p class="err">${err}</p>`}</main>`;
  const upd = (fn) => { const c = structuredClone(cfg); fn(c); setCfg(c); setDirty(true); };
  const save = async () => {
    try {
      const { writing_models, writing_actions, ...rest } = cfg;
      const saved = await api("/config", rest);
      setCfg({ ...saved, writing_models, writing_actions }); setDirty(false); say("Settings saved.");
    } catch (e) { say(e.message, true); }
  };
  const toggleTier = (kind, tier, id) => upd((c) => {
    const l = c.tiers[kind][tier];
    c.tiers[kind][tier] = l.includes(id) ? l.filter((x) => x !== id) : [...l, id];
  });
  const addModel = () => {
    if (!nm.id.trim() || !nm.label.trim()) return;
    if (cfg.catalog.some((m) => m.id === nm.id.trim())) return say("That model id already exists.", true);
    upd((c) => c.catalog.push({ ...nm, id: nm.id.trim(), role: nm.kind === "video" ? "i2v" : "generate", wired: false }));
    setNm({ id: "", label: "", kind: nm.kind, lane: nm.lane, note: "" });
  };
  const tierBox = (kind, tier, title, help) => html`<fieldset><legend>${title}</legend>
    <p class="hint" style="margin:0 0 6px;font-size:13px;color:var(--graphite)">${help}</p>
    ${cfg.catalog.filter((m) => m.kind === kind).map((m) => html`<label>
      <input type="checkbox" checked=${cfg.tiers[kind][tier].includes(m.id)} onChange=${() => toggleTier(kind, tier, m.id)} />
      <span>${m.label} <span class="unwired">${m.lane === "local" ? "dev box" : m.lane}${m.wired ? "" : " · not connected yet"}</span><small>${m.note}</small></span></label>`)}
  </fieldset>`;
  return html`<${Slate} /><main><div class="settings"><div class="page">
    <h2>Settings</h2>
    <p class="lede">Which model does what. Changes apply to the next piece written or generated.</p>

    <h3>Writing</h3>
    <p class="lede">Every piece of writing runs through Claude Code on this Mac (<code>claude -p</code>), on your existing login.</p>
    <div class="kv">
      ${Object.entries(cfg.writing_actions).map(([k, label]) => html`<span>${label}</span>
        <select value=${cfg.writing.per_action[k] || cfg.writing.model} onChange=${(e) => upd((c) => { c.writing.per_action[k] = e.target.value; })}>
          ${cfg.writing_models.map((m) => html`<option value=${m.id}>${m.label}</option>`)}</select>`)}
      <span>Effort</span>
      <select value=${cfg.writing.effort} onChange=${(e) => upd((c) => { c.writing.effort = e.target.value; })}>
        ${["low", "medium", "high", "xhigh", "max"].map((x) => html`<option value=${x}>${x}</option>`)}</select>
    </div>

    <h3>Images</h3>
    <p class="lede">Get the prompt right on the cheap tier, then build the keeper on the final tier.</p>
    <div class="tier-grid">
      ${tierBox("image", "draft", "Draft tier", "Fast and cheap. For finding the prompt.")}
      ${tierBox("image", "final", "Final tier", "For the frame that ships.")}
    </div>

    <h3>Video</h3>
    <p class="lede">Test motion on the draft model. If it holds up, keep it; otherwise rebuild on a final model.</p>
    <div class="tier-grid">
      ${tierBox("video", "draft", "Draft tier", "Cheap motion check.")}
      ${tierBox("video", "final", "Final tier", "Choices for the shot that ships.")}
    </div>

    <h3>Resolution</h3>
    <div class="kv">
      <span>Draft video</span>
      <select value=${cfg.resolution.draft_video} onChange=${(e) => upd((c) => { c.resolution.draft_video = e.target.value; })}>
        ${["480p", "720p"].map((x) => html`<option value=${x}>${x}</option>`)}</select>
      <span>Final video</span>
      <select value=${cfg.resolution.final_strategy} onChange=${(e) => upd((c) => { c.resolution.final_strategy = e.target.value; })}>
        ${Object.entries(cfg.resolution.strategies).map(([k, l]) => html`<option value=${k}>${l}</option>`)}</select>
    </div>

    <h3>Add a model</h3>
    <p class="lede">For anything new on kie or the dev box. It appears in the tier lists above.</p>
    <div class="kv">
      <span>Id</span><input value=${nm.id} onInput=${(e) => setNm({ ...nm, id: e.target.value })} placeholder="e.g. seedance-2.0-mini" />
      <span>Name</span><input value=${nm.label} onInput=${(e) => setNm({ ...nm, label: e.target.value })} placeholder="Seedance 2.0 Mini" />
      <span>Kind</span><select value=${nm.kind} onChange=${(e) => setNm({ ...nm, kind: e.target.value })}><option value="image">Image</option><option value="video">Video</option></select>
      <span>Runs on</span><select value=${nm.lane} onChange=${(e) => setNm({ ...nm, lane: e.target.value })}><option value="kie">kie</option><option value="local">Dev box</option></select>
      <span>Note</span><input value=${nm.note} onInput=${(e) => setNm({ ...nm, note: e.target.value })} placeholder="Price, strengths, catches" />
      <span></span><div><button class="btn small" disabled=${!nm.id.trim() || !nm.label.trim()} onClick=${addModel}>Add model</button></div>
    </div>

    <h3>Listening</h3>
    <p class="lede">Documents are read aloud by Qwen3-TTS on the dev box. Describe the voice; it is designed once and then kept for every page.</p>
    <div class="kv">
      <span>Voice</span><textarea class="note" value=${cfg.voice.instruct} onInput=${(e) => upd((c) => { c.voice.instruct = e.target.value; })}></textarea>
      <span>Language</span><select value=${cfg.voice.language} onChange=${(e) => upd((c) => { c.voice.language = e.target.value; })}>
        ${["English", "Chinese", "Japanese", "Korean", "German", "French", "Russian", "Portuguese", "Spanish", "Italian"].map((x) => html`<option value=${x}>${x}</option>`)}</select>
      <span>Voice server</span><input value=${cfg.voice.host} onInput=${(e) => upd((c) => { c.voice.host = e.target.value; })} />
      <span></span><div><button class="btn small" disabled=${dirty} title=${dirty ? "Save settings first" : ""} onClick=${async (e) => {
        const b = e.currentTarget; b.textContent = "Voicing…"; b.disabled = true;
        try { const r = await fetch("/api/voice/try", { method: "POST" }); if (!r.ok) throw new Error((await r.json()).error); new Audio(URL.createObjectURL(await r.blob())).play(); }
        catch (err) { say(err.message, true); }
        b.textContent = "Try this voice"; b.disabled = false;
      }}>Try this voice</button></div>
    </div>

    <h3>Films folder</h3>
    <div class="kv"><span>Folder</span><input value=${cfg.films_root} onInput=${(e) => upd((c) => { c.films_root = e.target.value; })} /></div>

    <div class="row" style="margin-top:34px"><button class="btn primary" disabled=${!dirty} onClick=${save}>Save settings</button>${dirty && html`<span class="cost">Unsaved changes</span>`}</div>
  </div></div></main>`;
}

render(html`<${App} />`, document.getElementById("app"));
