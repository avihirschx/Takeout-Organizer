"""Local browser-based review for near-duplicate groups.

Starts a tiny localhost-only web server (standard library only) that shows each
near-dup group's photos side by side with thumbnails, sizes and dimensions, and
a keep/delete toggle per photo. Clicking "Apply" moves the rejected copies to a
``near-dup-removed/`` folder (recoverable — not a permanent delete) and stops the
server. Nothing is touched until you click Apply.

Thumbnails need Pillow; the caller registers pillow-heif for HEIC if available.
"""

import html
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def _human(n):
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024


def build_registry(groups):
    """Turn groups (lists of Paths, largest first) into a flat registry plus
    in-memory thumbnails. Returns (registry, thumbs).

    registry[id] = {path, name, size, w, h, group, suggest_keep}
    thumbs[id]   = JPEG bytes
    """
    from PIL import Image  # lazy

    registry = {}
    thumbs = {}
    next_id = 0
    for gi, paths in enumerate(groups):
        for rank, p in enumerate(paths):
            try:
                with Image.open(p) as im:
                    w, h = im.size
                    im = im.convert("RGB")
                    im.thumbnail((260, 260))
                    import io
                    buf = io.BytesIO()
                    im.save(buf, "JPEG", quality=80)
                    thumbs[next_id] = buf.getvalue()
            except Exception:
                w = h = 0
                thumbs[next_id] = b""
            registry[next_id] = {
                "path": p, "name": p.name, "size": p.stat().st_size,
                "w": w, "h": h, "group": gi, "suggest_keep": rank == 0,
            }
            next_id += 1
    return registry, thumbs


def apply_deletions(ids, registry, removed_dir):
    """Move the registry entries in ``ids`` into ``removed_dir`` (recoverable).
    Only ids present in the registry are touched. Returns the number moved."""
    import shutil

    moved = 0
    removed_dir.mkdir(parents=True, exist_ok=True)
    for i in ids:
        entry = registry.get(i)
        if not entry:
            continue
        src = entry["path"]
        if not src.exists():
            continue
        dest = removed_dir / src.name
        n = 1
        while dest.exists():
            dest = removed_dir / f"{src.stem}_{n}{src.suffix}"
            n += 1
        try:
            shutil.move(str(src), str(dest))
            moved += 1
        except OSError:
            pass
    return moved


def _page(registry):
    groups = {}
    for i, e in registry.items():
        groups.setdefault(e["group"], []).append(i)

    rows = []
    for gi in sorted(groups):
        cards = []
        for i in groups[gi]:
            e = registry[i]
            checked = "" if e["suggest_keep"] else "checked"
            dims = f'{e["w"]}×{e["h"]}' if e["w"] else "?"
            cards.append(f'''
              <div class="card">
                <a href="/full/{i}" target="_blank"><img src="/thumb/{i}" loading="lazy"></a>
                <div class="meta">{html.escape(e["name"])}</div>
                <div class="meta dim">{dims} · {_human(e["size"])}</div>
                <label class="del"><input type="checkbox" class="d" data-id="{i}" {checked}> delete</label>
              </div>''')
        rows.append(f'<section><h3>Group {gi+1} — {len(groups[gi])} similar</h3>'
                    f'<div class="row">{"".join(cards)}</div></section>')

    return f'''<!doctype html><html><head><meta charset="utf-8">
<title>Near-duplicate review</title>
<style>
 body{{font-family:system-ui,Arial,sans-serif;margin:0;background:#1b1b1f;color:#eee}}
 header{{padding:14px 18px;background:#26262c;position:sticky;top:0;z-index:5;
   display:flex;align-items:center;gap:16px;border-bottom:1px solid #333}}
 h3{{margin:18px 18px 6px}}
 .row{{display:flex;flex-wrap:wrap;gap:12px;padding:0 18px 10px}}
 .card{{background:#26262c;border:1px solid #333;border-radius:8px;padding:8px;width:170px}}
 .card img{{width:154px;height:154px;object-fit:contain;background:#111;border-radius:4px}}
 .meta{{font-size:12px;margin-top:4px;word-break:break-all}}
 .dim{{color:#999}}
 .del{{display:block;margin-top:6px;font-size:13px;color:#ff9b9b}}
 .card:has(.d:checked){{outline:2px solid #b33;opacity:.65}}
 button{{font-size:15px;padding:8px 16px;border:0;border-radius:6px;cursor:pointer}}
 #apply{{background:#3b7d3b;color:#fff}} #apply:disabled{{opacity:.5;cursor:default}}
 #status{{color:#9bd29b}}
</style></head><body>
<header>
  <strong>Near-duplicate review</strong>
  <span id="count"></span>
  <button id="apply">Apply — move deletes to near-dup-removed/</button>
  <span id="status"></span>
</header>
{''.join(rows)}
<script>
 const boxes=()=>[...document.querySelectorAll('.d')];
 function upd(){{const d=boxes().filter(b=>b.checked).length;
   document.getElementById('count').textContent=d+' to delete, '+(boxes().length-d)+' to keep';}}
 boxes().forEach(b=>b.addEventListener('change',upd)); upd();
 document.getElementById('apply').onclick=async()=>{{
   const ids=boxes().filter(b=>b.checked).map(b=>+b.dataset.id);
   if(!confirm('Move '+ids.length+' photo(s) to near-dup-removed/? The rest stay in place.'))return;
   const btn=document.getElementById('apply'); btn.disabled=true;
   const r=await fetch('/apply',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{delete:ids}})}});
   const j=await r.json();
   document.getElementById('status').textContent='Moved '+j.moved+'. You can close this tab.';
 }};
</script></body></html>'''


def serve_review(groups, removed_dir, host="127.0.0.1", port=0, open_browser=True):
    """Run the review server until the user applies or cancels. Returns the
    number of files moved (0 if cancelled)."""
    registry, thumbs = build_registry(groups)
    page = _page(registry).encode("utf-8")
    done = threading.Event()
    result = {"moved": 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="text/html; charset=utf-8"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/":
                self._send(200, page)
            elif self.path.startswith("/thumb/"):
                self._send(200, thumbs.get(int(self.path[7:]), b""), "image/jpeg")
            elif self.path.startswith("/full/"):
                e = registry.get(int(self.path[6:]))
                data = e["path"].read_bytes() if e and e["path"].exists() else b""
                self._send(200, data, "application/octet-stream")
            else:
                self._send(404, b"not found")

        def do_POST(self):
            if self.path != "/apply":
                self._send(404, b"not found")
                return
            length = int(self.headers.get("Content-Length", 0))
            try:
                ids = json.loads(self.rfile.read(length) or b"{}").get("delete", [])
                ids = [int(i) for i in ids]
            except Exception:
                ids = []
            result["moved"] = apply_deletions(ids, registry, removed_dir)
            self._send(200, json.dumps(result).encode(), "application/json")
            done.set()

    server = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{server.server_address[1]}/"
    print(f"\n  Review at: {url}")
    print("  Pick keep/delete in your browser and click Apply, or press Ctrl-C to cancel.")
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        done.wait()
    except KeyboardInterrupt:
        print("\n  Cancelled — nothing changed.")
    server.shutdown()
    return result["moved"]
