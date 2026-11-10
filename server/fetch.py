"""Fetching references from the internet.

A link is either an image (saved as it is) or a page (its images are listed so a person can
pick). Every saved file is sniffed — a URL that claims to be an image but isn't is refused.
"""
import base64, html.parser, re, time, urllib.error, urllib.request
from urllib.parse import urljoin, urlparse

MAX_BYTES = 25 * 1024 * 1024
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")


class FetchError(Exception):
    pass


def sniff(data: bytes):
    """The real type of an image, from its first bytes. None if it isn't one we keep."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[4:12] in (b"ftypavif", b"ftypavis"):
        return ".avif"
    return None


# Wikimedia asks automated clients to identify themselves; a browser-looking agent gets 429s.
WIKI_UA = "StudioReferences/1.0 (local film pre-production reference tool)"


def wikimedia_thumb(url, width=1600):
    """upload.wikimedia.org originals → a cached 1600px rendition (lighter, rarely rate-limited)."""
    m = re.match(r"(https://upload\.wikimedia\.org/wikipedia/[^/]+)/(\w)/(\w\w)/([^/?#]+)$", url)
    if not m or "/thumb/" in url or m.group(4).lower().endswith((".svg", ".tif", ".tiff")):
        return url
    base, a, ab, name = m.groups()
    return f"{base}/thumb/{a}/{ab}/{name}/{width}px-{name}"


def _get(url, timeout=30, tries=4):
    if urlparse(url).scheme not in ("http", "https"):
        raise FetchError("only http and https links can be fetched")
    host = urlparse(url).hostname or ""
    ua = WIKI_UA if host.endswith("wikimedia.org") or host.endswith("wikipedia.org") else UA
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "image/*,text/html;q=0.9,*/*;q=0.5"})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read(MAX_BYTES + 1)
                ctype = r.headers.get("Content-Type", "")
                final = r.geturl()
            break
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < tries - 1:
                # the site asked us to slow down: wait as told (capped), then try again
                wait = e.headers.get("Retry-After", "")
                time.sleep(min(int(wait), 20) if wait.isdigit() else 2 * (attempt + 1) ** 2)
                continue
            raise FetchError(f"the site answered {e.code}")
        except (urllib.error.URLError, OSError) as e:
            raise FetchError(f"couldn't reach the link ({getattr(e, 'reason', e)})")
    if len(data) > MAX_BYTES:
        raise FetchError("that file is over 25 MB")
    return data, ctype, final


def image(url):
    """Download one image. Returns (bytes, ext)."""
    try:
        data, ctype, _ = _get(wikimedia_thumb(url))
    except FetchError:
        if wikimedia_thumb(url) == url:
            raise
        data, ctype, _ = _get(url)              # some files have no thumbnail; take the original
    ext = sniff(data)
    if not ext:
        raise FetchError("that link isn't an image" + (f" ({ctype.split(';')[0]})" if ctype else ""))
    return data, ext


def data_url(s: str):
    """A pasted/dropped image arrives as a data: URL."""
    m = re.match(r"data:[^;,]*;base64,(.*)", s or "", re.S)
    if not m:
        raise FetchError("not an image")
    data = base64.b64decode(m.group(1))
    ext = sniff(data)
    if not ext:
        raise FetchError("that file isn't a png, jpg, gif, webp or avif image")
    if len(data) > MAX_BYTES:
        raise FetchError("that image is over 25 MB")
    return data, ext


class _Imgs(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.found, self.title = [], ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta" and (a.get("property") or a.get("name") or "").lower() in (
                "og:image", "og:image:url", "twitter:image", "twitter:image:src"):
            self.found.insert(0, a.get("content"))
        elif tag == "img":
            for k in ("data-src", "data-original", "src"):
                if a.get(k):
                    self.found.append(a[k])
                    break
            if a.get("srcset"):
                # the last candidate in a srcset is usually the largest
                self.found.append(a["srcset"].split(",")[-1].strip().split(" ")[0])
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, d):
        if self._in_title:
            self.title += d


def link(url):
    """Either {"image": (bytes, ext)} or {"candidates": [...], "title": str}."""
    data, ctype, final = _get(url)
    ext = sniff(data)
    if ext:
        return {"image": (data, ext)}
    if "html" not in ctype and not data.lstrip()[:15].lower().startswith((b"<!doctype", b"<html")):
        raise FetchError("that link is neither an image nor a web page")
    p = _Imgs()
    try:
        p.feed(data.decode("utf-8", "replace"))
    except Exception:
        pass
    seen, out = set(), []
    for src in p.found:
        if not src or src.startswith("data:"):
            continue
        u = urljoin(final, src.strip())
        low = u.lower().split("?")[0]
        if low.endswith((".svg", ".ico")) or any(w in low for w in ("sprite", "logo", "icon", "pixel", "1x1", "tracking")):
            continue
        if u not in seen:
            seen.add(u)
            out.append(u)
    if not out:
        raise FetchError("no images found on that page — try copying the image itself")
    return {"candidates": out[:40], "title": p.title.strip()[:200], "page_url": final}
