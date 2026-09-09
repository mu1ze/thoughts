#!/usr/bin/env python3
"""thoughts editor — private admin page for editing blog posts.

Usage: python3 admin_editor.py  (port 8904, loopback only)
Tunnel it with cloudflared. Passphrase stored in .secrets/editor_pass.

WYSIWYG-ish: the editor shows the rendered text (no HTML tags). Structure
paragraphs with BLANK LINES. Markup conventions while editing:
  § Heading            -> <h2> section heading
  >>> quoted text      -> pull-quote
  *emphasis*           -> <em>
On save the server rebuilds the exact <article> block, validates tag
balance, preserves meta/footnote/back-link, writes, commits, deploys.
"""
import html.parser
import os
import re
import subprocess
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = "/opt/data/thoughts"
POSTS = os.path.join(ROOT, "posts")
SECRETS = os.path.join(ROOT, ".secrets")
PORT = 8904

PASS = open(os.path.join(SECRETS, "editor_pass")).read().strip()

POSTS_MAP = {}
for f in sorted(os.listdir(POSTS)):
    if f.endswith(".html"):
        path = os.path.join(POSTS, f)
        src = open(path).read()
        t = re.search(r'<h2 class="essay-title">(.*?)</h2>', src, re.S)
        title = re.sub(r"<[^>]+>", "", t.group(1)).strip() if t else f
        POSTS_MAP[f[:-5]] = (path, title)


class Balance(html.parser.HTMLParser):
    VOIDS = {"meta", "hr", "br", "img", "link", "input"}

    def __init__(self):
        super().__init__()
        self.stack, self.errs = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOIDS:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        else:
            self.errs.append(tag)


INLINE = re.compile(r"<(?!/?(?:h2|p|article)\b)[a-z][^>]*>|</(?:em|span|b|i)>")

# ---------- article -> plain text for editing ----------

def art_to_text(art):
    # capture inner html of <p> and <h2> and special blocks in order
    body = re.search(r"<article\b[^>]*>(.*)</article>", art, re.S).group(1)
    # drop the title/meta/kicker (protected, shown separately)
    body = re.sub(r'<p class="essay-kicker">.*?</p>', "", body, flags=re.S)
    body = re.sub(r'<h2 class="essay-title">.*?</h2>', "", body, flags=re.S)
    body = re.sub(r'<p class="essay-meta mono">.*?</p>', "", body, flags=re.S)
    body = body.replace('<a class="back-link" href="/">↑ back to the pile</a>', "")
    body = body.replace('<a class="back-link" href="#top">↑ back to the pile</a>', "")
    body = re.sub(r'<a class="back-link"[^>]*>.*?</a>', "", body, flags=re.S)

    out = []
    # walk block-level pieces
    for m in re.finditer(
        r'<p class="pull">(.*?)</p>|<p class="footnote">(.*?)</p>|<h2>(.*?)</h2>|<p>(.*?)</p>|<hr>\s*',
        body, re.S):
        if m.group(1) is not None:
            out.append(">>> " + inline_to_text(m.group(1)))
        elif m.group(2) is not None:
            out.append("[footnote] " + inline_to_text(m.group(2)))
        elif m.group(3) is not None:
            out.append("§ " + inline_to_text(m.group(3)))
        elif m.group(4) is not None:
            txt = m.group(4).strip()
            if "<em>" in txt and txt.startswith("<em>") and txt.endswith("</em>"):
                # standalone emphasized paragraph (e.g. the slot-machine line)
                out.append("*" + inline_to_text(txt[4:-5]) + "*")
            else:
                out.append(inline_to_text(txt))
        else:
            out.append("⁂")
    return "\n\n".join(out)


def inline_to_text(s):
    s = re.sub(r"<em>(.*?)</em>", r"*\1*", s, flags=re.S)
    s = re.sub(r"<br\s*/?>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    return html.unescape(s).strip()

# ---------- plain text -> article ----------

def text_to_art(text, meta_line):
    blocks = [b.strip() for b in text.replace("\r\n", "\n").split("\n\n") if b.strip()]
    parts = []
    for b in blocks:
        if b in ("⁂", "***"):
            parts.append("<hr>")
        elif b.startswith(">>> "):
            parts.append('<p class="pull">%s</p>' % text_to_inline(b[4:]))
        elif b.startswith("[footnote] "):
            parts.append('<p class="footnote">%s</p>' % text_to_inline(b[len("[footnote] "):]))
        elif b.startswith("§ "):
            parts.append("<h2>%s</h2>" % text_to_inline(b[2:]))
        else:
            parts.append("<p>%s</p>" % text_to_inline(b))
    inner = "\n\n    ".join(parts)
    return (
        '<article style="margin-top:5rem;">\n'
        '    <p class="essay-kicker">Essay — Satire</p>\n'
        "    <h2 class=\"essay-title\">TITLE_PLACEHOLDER</h2>\n"
        '    <p class="essay-meta mono">%s</p>\n\n    %s\n\n    '
        '<a class="back-link" href="/">↑ back to the pile</a>\n  </article>' % (meta_line, inner)
    )


def text_to_inline(s):
    s = html.escape(s, quote=False)
    s = re.sub(r"\*(.+?)\*", r"<em>\1</em>", s, flags=re.S)
    return s


# ---------- preserve title/meta on save ----------

def art_meta(art):
    t = re.search(r'<h2 class="essay-title">(.*?)</h2>', art, re.S)
    m = re.search(r'<p class="essay-meta mono">(.*?)</p>', art, re.S)
    k = re.search(r'<p class="essay-kicker">(.*?)</p>', art, re.S)
    return (t.group(1) if t else "Untitled",
            m.group(1) if m else "",
            k.group(1) if k else "Essay — Satire")


def rebuild_with_text(old_art, text):
    title, meta, kicker = art_meta(old_art)
    art = text_to_art(text, meta)
    art = art.replace("TITLE_PLACEHOLDER", title)
    if kicker != "Essay — Satire":
        art = art.replace('<p class="essay-kicker">Essay — Satire</p>',
                          '<p class="essay-kicker">%s</p>' % kicker)
    # match original indentation style of file
    art = art.replace('    <a class="back-link"', '    <a class="back-link"')
    return art


def article_of(path):
    src = open(path).read()
    m = re.search(r"(<article\b.*</article>)", src, re.S)
    return m.group(1)


def replace_article(path, new_art):
    src = open(path).read()
    out = re.sub(r"<article\b.*</article>", lambda m: new_art, src, count=1, flags=re.S)
    open(path, "w").write(out)


def git_deploy(msg):
    env = dict(os.environ, HOME="/opt/data")
    cmds = [
        ["git", "-c", "user.name=mu1ze", "-c", "user.email=mu1ze@users.noreply.github.com",
         "add", "-A"],
        ["git", "-c", "user.name=mu1ze", "-c", "user.email=mu1ze@users.noreply.github.com",
         "commit", "-m", msg],
        ["git", "push"],
        ["bash", "deploy.sh"],
    ]
    for c in cmds:
        r = subprocess.run(c, cwd=ROOT, env=env, capture_output=True, text=True)
        if c[0] == "git" and "commit" in c and r.returncode != 0:
            if "nothing to commit" in r.stdout:
                continue
            return False, r.stdout + r.stderr
        if r.returncode != 0:
            return False, (r.stdout + r.stderr)[-800:]
    return True, "deployed"


PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>thoughts — editor</title><style>
:root{{--paper:#faf9f6;--ink:#1a1a1a;--muted:#8a877f;--faint:#d8d5cd;--accent:#c2410c}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:var(--paper);color:var(--ink);font-family:Georgia,serif;line-height:1.7;padding:2rem 1.25rem}}
main{{max-width:44rem;margin:0 auto}}
h1{{font-weight:400;font-size:1.9rem}}h1 span{{color:var(--accent)}}
a{{color:inherit}}.list a{{display:block;padding:.9rem 0;border-bottom:1px solid var(--faint);text-decoration:none}}
.list a:hover h2{{color:var(--accent)}}.list h2{{font-weight:400;font-size:1.15rem;transition:color .2s}}
.mono{{font-family:ui-monospace,Menlo,monospace;font-size:.75rem;color:var(--muted)}}
textarea{{width:100%;min-height:65vh;font-family:Georgia,serif;font-size:1.05rem;line-height:1.8;
padding:1.25rem;border:1px solid var(--faint);border-radius:8px;background:#fff;resize:vertical}}
.hint{{font-size:.8rem;color:var(--muted);background:#fff;border:1px solid var(--faint);
border-radius:8px;padding:.8rem 1rem;margin:.75rem 0}}
button{{margin-top:1rem;background:var(--accent);color:#fff;border:none;padding:.8rem 1.6rem;
font-size:1rem;border-radius:6px;cursor:pointer;font-family:Georgia,serif}}
button:hover{{opacity:.9}}#msg{{margin-top:1rem;font-size:.9rem}}
.back{{display:inline-block;margin:1rem 0;color:var(--muted);text-decoration:none;font-size:.85rem}}
input{{padding:.7rem;font-size:1rem;border:1px solid var(--faint);border-radius:6px;width:100%;max-width:22rem}}
</style></head><body><main>{body}</main></body></html>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body):
        b = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def _authed(self):
        return "t=%s" % PASS in self.headers.get("Cookie", "")

    def do_GET(self):
        if not self._authed():
            self._send(200, PAGE.format(body="""
<h1>thoughts<span>·</span>editor</h1>
<form method="POST" action="/login">
<p><input type="password" name="p" placeholder="passphrase" autofocus></p>
<button>Enter</button></form>"""))
            return
        if self.path == "/":
            rows = "".join(
                f'<a href="/edit/{slug}"><h2>{title}</h2>'
                f'<span class="mono">/posts/{slug}.html</span></a>'
                for slug, (_, title) in POSTS_MAP.items())
            self._send(200, PAGE.format(body=f"""
<h1>thoughts<span>·</span>editor</h1>
<p class="mono" style="margin:.5rem 0 1.5rem">plain-text editing — formatting rebuilt on save</p>
<div class="list">{rows}</div>"""))
            return
        m = re.match(r"^/edit/([a-z0-9-]+)$", self.path)
        if m and m.group(1) in POSTS_MAP:
            slug = m.group(1)
            path, title = POSTS_MAP[slug]
            txt = art_to_text(article_of(path))
            self._send(200, PAGE.format(body=f"""
<a class="back" href="/">← all posts</a>
<h1>{html.escape(title)}</h1>
<div class="hint">
<b>how to format</b><br>
blank line = new paragraph<br>
<code>§ Heading</code> = section heading<br>
<code>&gt;&gt;&gt; quoted line</code> = pull-quote<br>
<code>*word*</code> = <em>italic</em><br>
<code>⁂</code> on its own line = section divider<br>
<code>[footnote] …</code> = footnote<br>
Title and byline are locked.
</div>
<form method="POST" action="/save/{slug}">
<textarea name="a" spellcheck="false">{html.escape(txt)}</textarea>
<button>Publish — save, commit &amp; deploy</button>
</form>"""))
            return
        self._send(404, PAGE.format(body="<h1>404</h1>"))

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        form = self.rfile.read(n).decode()
        if self.path == "/login":
            if ("p=" + PASS) in form:
                self.send_response(303)
                self.send_header("Set-Cookie", f"t={PASS}; Path=/; HttpOnly")
                self.send_header("Location", "/")
                self.end_headers()
            else:
                self._send(200, PAGE.format(body="<h1>nope</h1><a class='back' href='/'>try again</a>"))
            return
        m = re.match(r"^/save/([a-z0-9-]+)$", self.path)
        if m and m.group(1) in POSTS_MAP and self._authed():
            slug = m.group(1)
            path, _ = POSTS_MAP[slug]
            raw = urllib.parse.unquote_plus(form.split("a=", 1)[1])
            old = article_of(path)
            new_art = rebuild_with_text(old, raw)
            b = Balance()
            b.feed(new_art)
            if b.stack or b.errs:
                self._send(400, PAGE.format(body="<p>refused: generated markup invalid — nothing saved</p>"
                                             "<a class='back' href='/'>back</a>"))
                return
            replace_article(path, new_art)
            ok, out = git_deploy(f"edit: {slug} via editor")
            msg = "published &amp; deployed ✓" if ok else "saved, but deploy failed: " + html.escape(out)
            self._send(200, PAGE.format(body=f"""
<a class="back" href="/">← all posts</a><h1>{html.escape(POSTS_MAP[slug][1])}</h1>
<p id="msg">{msg}</p><a class="back" href="/edit/{slug}">keep editing</a>"""))
            return
        self._send(404, PAGE.format(body="<h1>404</h1>"))


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", PORT), H).serve_forever()
