// Two small renderers: Markdown (premise, critiques, cards) and Fountain (the script page).
// Both escape first and only ever produce markup they built themselves.

const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

function inline(s) {
  return esc(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[\s(])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>")
    .replace(/(^|[\s(])_([^_\s][^_]*)_/g, "$1<em>$2</em>");
}

export function markdown(src) {
  const lines = (src || "").replace(/\r/g, "").split("\n");
  const out = [];
  let i = 0;
  const para = [];
  const flush = () => {
    if (para.length) out.push(`<p>${inline(para.join(" "))}</p>`);
    para.length = 0;
  };
  while (i < lines.length) {
    const line = lines[i];
    const fence = line.match(/^```\s*(\w*)/);
    if (fence) {
      flush();
      const lang = fence[1];
      const body = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) body.push(lines[i++]);
      i++;
      const code = esc(body.join("\n"));
      out.push(lang === "prompt"
        ? `<figure class="prompt"><figcaption>Prompt</figcaption><pre>${code}</pre><button class="copy" data-copy>Copy</button></figure>`
        : `<pre class="code">${code}</pre>`);
      continue;
    }
    const h = line.match(/^(#{1,4})\s+(.*)/);
    if (h) {
      flush();
      const lvl = Math.min(h[1].length + 1, 5);
      out.push(`<h${lvl}>${inline(h[2])}</h${lvl}>`);
      i++;
      continue;
    }
    if (/^\s*\|.*\|\s*$/.test(line) && /^\s*\|[\s:-|]+\|\s*$/.test(lines[i + 1] || "")) {
      flush();
      const row = (l) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      const head = row(line);
      i += 2;
      const rows = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) rows.push(row(lines[i++]));
      out.push(`<div class="table"><table><thead><tr>${head.map((c) => `<th>${inline(c)}</th>`).join("")}</tr></thead><tbody>${rows
        .map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);
      continue;
    }
    const li = line.match(/^(\s*)([-*]|\d+[.)])\s+(.*)/);
    if (li) {
      flush();
      const ordered = /\d/.test(li[2]);
      const items = [];
      while (i < lines.length) {
        const m = lines[i].match(/^(\s*)([-*]|\d+[.)])\s+(.*)/);
        if (m) { items.push(m[3]); i++; continue; }
        if (lines[i].trim() && /^\s{2,}/.test(lines[i]) && items.length) { items[items.length - 1] += " " + lines[i].trim(); i++; continue; }
        break;
      }
      const tag = ordered ? "ol" : "ul";
      out.push(`<${tag}>${items.map((t) => `<li>${inline(t.replace(/^\[( |x)\]\s*/, (m0, x) => (x === "x" ? "☑ " : "☐ ")))}</li>`).join("")}</${tag}>`);
      continue;
    }
    if (/^>\s?/.test(line)) {
      flush();
      const q = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) q.push(lines[i++].replace(/^>\s?/, ""));
      out.push(`<blockquote>${inline(q.join(" "))}</blockquote>`);
      continue;
    }
    if (/^(-{3,}|\*{3,})\s*$/.test(line)) { flush(); out.push("<hr>"); i++; continue; }
    if (!line.trim()) { flush(); i++; continue; }
    para.push(line.trim());
    i++;
  }
  flush();
  return out.join("\n");
}

// ---------------------------------------------------------------- Fountain

const HEADING = /^(INT|EXT|EST|INT\.?\/EXT|I\/E)[\s.]/i;
const TRANSITION = /^[A-Z0-9 .'\-]+(TO:|OUT\.|IN:)$/;

export function fountain(src) {
  const lines = (src || "").replace(/\r/g, "").split("\n");
  const out = [];
  let scene = 0;
  for (let i = 0; i < lines.length; i++) {
    const raw = lines[i];
    const line = raw.trim();
    const prevBlank = i === 0 || !lines[i - 1].trim();
    const next = (lines[i + 1] || "").trim();
    if (!line) continue;
    if (prevBlank && (HEADING.test(line) || /^\.[A-Z]/.test(line))) {
      scene++;
      const t = line.replace(/^\./, "");
      out.push(`<div class="fx-scene" data-scene="${scene}"><span class="fx-num">${scene}</span>${esc(t.toUpperCase())}<span class="fx-num">${scene}</span></div>`);
      continue;
    }
    if (prevBlank && (TRANSITION.test(line) || line.startsWith(">")) && !line.endsWith("<")) {
      out.push(`<div class="fx-transition">${esc(line.replace(/^>\s*/, ""))}</div>`);
      continue;
    }
    const isCue = prevBlank && next && (/^@/.test(line) ||
      (line === line.toUpperCase() && /[A-Z]/.test(line) && !/[.!?]$/.test(line.replace(/\(.*\)$/, "").trim())
        && line.length < 40 && !line.startsWith("MUSIC:")));
    if (isCue) {
      out.push(`<div class="fx-cue">${esc(line.replace(/^@/, ""))}</div>`);
      i++;
      while (i < lines.length && lines[i].trim()) {
        const d = lines[i].trim();
        out.push(d.startsWith("(")
          ? `<div class="fx-paren">${esc(d)}</div>`
          : `<div class="fx-dialogue">${inline(d)}</div>`);
        i++;
      }
      continue;
    }
    if (/^MUSIC:/i.test(line)) {
      out.push(`<div class="fx-action fx-music">${inline(line)}</div>`);
      continue;
    }
    if (line.startsWith("#") || line.startsWith("=")) continue; // sections & synopses
    // action: keep consecutive lines together
    const block = [line];
    while (i + 1 < lines.length && lines[i + 1].trim()) block.push(lines[++i].trim());
    out.push(`<div class="fx-action">${block.map(inline).join("<br>")}</div>`);
  }
  return { html: out.join("\n"), scenes: scene };
}

export function sceneHeadings(src) {
  const lines = (src || "").replace(/\r/g, "").split("\n");
  const out = [];
  lines.forEach((l, i) => {
    const t = l.trim();
    if ((i === 0 || !lines[i - 1].trim()) && (HEADING.test(t) || /^\.[A-Z]/.test(t))) out.push(t.replace(/^\./, "").toUpperCase());
  });
  return out;
}
