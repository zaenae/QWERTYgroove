"""QWERTYgroove for Mac: a small local server for the interface, shown in its own
window (pywebview) or, if that isn't available, in your web browser."""
import argparse
import json
import os
import signal
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__, sources
from .engine import Engine
from .lights import LightError, PreviewLight, make_light

UI = os.path.join(os.path.dirname(__file__), "ui", "index.html")
PAGE_GONE_AFTER = 4.0      # seconds without the page before the app quits (a refresh comes back sooner)


class PageWatch:
    """Keeps track of whether the app's page is still open."""

    def __init__(self):
        self.lock = threading.Lock()
        self.open_pages = 0
        self.ever_opened = False
        self.last_seen = time.monotonic()

    def opened(self):
        with self.lock:
            self.open_pages += 1
            self.ever_opened = True

    def closed(self):
        with self.lock:
            self.open_pages -= 1
            self.last_seen = time.monotonic()

    def gone(self):
        with self.lock:
            return (self.ever_opened and self.open_pages <= 0
                    and time.monotonic() - self.last_seen > PAGE_GONE_AFTER)


def make_handler(engine, info, watch):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(data)

        def _ours(self):
            """Only answer requests made to this app on this Mac. Blocks other
            websites, including ones that try to pose as 127.0.0.1 (DNS rebinding)."""
            port = self.server.server_port
            if self.headers.get("Host") not in (f"127.0.0.1:{port}", f"localhost:{port}"):
                return False
            origin = self.headers.get("Origin")
            return origin in (None, f"http://127.0.0.1:{port}", f"http://localhost:{port}")

        def do_GET(self):
            if not self._ours():
                return self._send(403, {"error": "forbidden"})
            if self.path in ("/", "/index.html"):
                with open(UI, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            elif self.path == "/api/info":
                ready, msg = sources.spotify_status()
                self._send(200, dict(info, spotify_ready=ready, spotify_message=msg))
            elif self.path == "/api/stream":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                watch.opened()
                try:
                    while True:
                        self.wfile.write(b"data: " + json.dumps(engine.snapshot()).encode() + b"\n\n")
                        self.wfile.flush()
                        time.sleep(1 / 40)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    watch.closed()
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._ours():
                return self._send(403, {"error": "forbidden"})
            length = min(int(self.headers.get("Content-Length") or 0), 10_000)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                body = {}
            if not isinstance(body, dict):
                body = {}
            if self.path == "/api/start" and body.get("source") in ("demo", "spotify"):
                engine.start(body["source"])
                self._send(200, {"ok": True})
            elif self.path in ("/api/stop", "/api/bye"):      # "bye": the page is closing
                engine.stop()
                self._send(200, {"ok": True})
            else:
                self._send(404, {"error": "not found"})

    return Handler


def main():
    p = argparse.ArgumentParser(prog="qwertygroove", description="Make your MacBook keyboard light dance to music.")
    p.add_argument("--browser", action="store_true", help="open in the web browser instead of a window")
    p.add_argument("--no-open", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--port", type=int, default=0, help=argparse.SUPPRESS)
    p.add_argument("--preview", action="store_true", help="don't touch the keyboard light")
    p.add_argument("--version", action="version", version=f"QWERTYgroove {__version__}")
    args = p.parse_args()

    light_error = None
    if args.preview:
        light = PreviewLight()
    else:
        try:
            light = make_light()
        except LightError as e:
            light_error = str(e)
            light = PreviewLight()
    engine = Engine(light)
    info = {"version": __version__, "light_error": light_error}
    watch = PageWatch()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(engine, info, watch))
    server.daemon_threads = True
    url = f"http://127.0.0.1:{server.server_port}/"
    threading.Thread(target=server.serve_forever, daemon=True).start()

    done = threading.Event()

    def quit_app(*_):
        if not done.is_set():
            done.set()
            engine.shutdown()                       # restores the light and the sound output
    signal.signal(signal.SIGTERM, lambda *a: (quit_app(), sys.exit(0)))

    tty = sys.stdout.isatty()
    teal, dim, off = ("\033[38;5;43m", "\033[2m", "\033[0m") if tty else ("", "", "")
    print(f"\n  {teal}♪ QWERTYgroove is open.{off}")
    print(f"  {dim}Close its window when you're done (or press Ctrl+C here).{off}\n")
    if light_error:
        print(f"  {dim}Note: {light_error}{off}")
    try:
        window = None
        if not (args.browser or args.no_open):
            try:
                import webview
                window = webview.create_window("QWERTYgroove", url, width=900, height=760,
                                               min_size=(420, 600), background_color="#AEBAB8")
            except Exception:
                window = None
        if window is not None:
            webview.start()                         # returns when the window is closed
        else:
            if not args.no_open:
                webbrowser.open(url)
            # in the browser: quit once its tab has been closed
            while not watch.gone():
                time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        quit_app()
        server.shutdown()
        print(f"\n  {teal}Bye! Your keyboard light and sound are back to normal.{off}\n")


if __name__ == "__main__":
    main()
