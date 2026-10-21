"""Preview server for the app shell.

Serves app/src exactly as it sits on disk, and injects three script tags
into index.html on the way out: the Tauri global API bundle, the state
fixtures, and the mock bridge. Nothing is written into app/src, so the
shipped bundle (frontendDist embeds that directory whole) is unchanged.

The Tauri bundle is not vendored here. It is read from the pinned tauri
crate in the cargo registry, at the exact version Cargo.lock names, so
the preview always speaks the API of the crate the app builds against.

Run:  python tools/preview/serve.py [port]
Then open  http://127.0.0.1:4747/?state=gate  (any state in states.js).
"""

import http.server
import functools
import os
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = ROOT / "app" / "src"
LOCK = ROOT / "app" / "src-tauri" / "Cargo.lock"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 4747

ANCHOR = b'<script src="bootstrap.js" defer></script>'
INJECT = (
    b'<script src="/__preview/tauri-global.js"></script>\n'
    b'  <script src="/__preview/states.js"></script>\n'
    b'  <script src="/__preview/mock.js"></script>\n'
    b'  ' + ANCHOR
)


def tauri_bundle() -> pathlib.Path:
    """The pinned crate's bundle.global.js, found via Cargo.lock."""
    lock = LOCK.read_text(encoding="utf-8")
    m = re.search(r'name = "tauri"\nversion = "([^"]+)"', lock)
    if not m:
        raise SystemExit("no tauri package in Cargo.lock")
    version = m.group(1)
    cargo_home = pathlib.Path(
        os.environ.get("CARGO_HOME", pathlib.Path.home() / ".cargo")
    )
    hits = list(cargo_home.glob(
        f"registry/src/*/tauri-{version}/scripts/bundle.global.js"
    ))
    if not hits:
        raise SystemExit(
            f"tauri-{version} not in the cargo registry; "
            "run `cargo fetch` in app/src-tauri first"
        )
    return hits[0]


BUNDLE = tauri_bundle()


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?", 1)[0]

        if path == "/__preview/tauri-global.js":
            return self._send_bytes(BUNDLE.read_bytes(), "text/javascript")
        if path.startswith("/__preview/"):
            return self._send_file(HERE / path[len("/__preview/"):], "text/javascript")

        if path in ("/", "/index.html"):
            html = (SRC / "index.html").read_bytes()
            if ANCHOR not in html:
                raise SystemExit("anchor not found in index.html; update serve.py")
            return self._send_bytes(html.replace(ANCHOR, INJECT), "text/html")

        return super().do_GET()

    def _send_file(self, target, ctype):
        if not target.is_file():
            self.send_error(404)
            return
        self._send_bytes(target.read_bytes(), ctype)

    def _send_bytes(self, body, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    handler = functools.partial(Handler, directory=str(SRC))
    print(f"preview on http://127.0.0.1:{PORT}/  serving {SRC}")
    print(f"tauri bundle: {BUNDLE}")
    http.server.ThreadingHTTPServer(("127.0.0.1", PORT), handler).serve_forever()
