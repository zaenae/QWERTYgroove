"""Connects a music source to the analysis and the keyboard lights, and keeps
a live snapshot for the app to display."""
import collections
import queue
import threading
import time

from . import sources
from .analysis import Analyzer, SAMPLERATE, HOP

# The keyboard backlight itself takes a moment to react, so flashes are sent
# this much earlier than the sound comes out.
KEYBOARD_LAG = 0.015
MAX_DELAY = 0.6
# How often to check whether the Mac's sound output changed (earphones plugged in...)
FOLLOW_EVERY = 0.4


DEFAULTS = {"mode": "all", "sensitivity": 1.8, "punch": 1.6, "fade": None, "floor": 0.0}


class Engine:
    def __init__(self, light):
        self.light = light
        self.settings = dict(DEFAULTS)
        self.lock = threading.Lock()
        self.chunks = queue.Queue()
        self.source = None
        self.analyzer = Analyzer(self.settings)
        self.worker = None
        self.running = False
        self.busy = False
        self.status = "Ready"
        self.error = None
        self.lag_ms = 0.0
        # The speakers/earphones play each bit of sound a little after we read it
        # (much more on Bluetooth). Each light change waits for that same delay,
        # so the flash lands with the beat you hear, not before it.
        self.delay = 0.0
        self.pending = collections.deque()
        self.view = None                         # what's showing right now: (light, envs, levels)
        threading.Thread(target=self._show_loop, daemon=True).start()
        threading.Thread(target=self._follow_loop, daemon=True).start()

    # ---------------------------------------------------------------- control
    def start(self, kind):
        """Start listening. kind: demo | spotify. Runs setup in the background
        (the demo may need to download) so the app stays responsive."""
        self.busy, self.error, self.status = True, None, "One sec…"   # set first: no "Ready" flicker
        self.stop()
        threading.Thread(target=self._start, args=(kind,), daemon=True).start()

    def _start(self, kind):
        try:
            push = lambda chunk: self.chunks.put((time.perf_counter(), chunk))
            if kind == "demo":
                src = sources.DemoSource(push, progress=self._set_status)
            elif kind == "spotify":
                src = sources.SpotifySource(push)
            else:
                raise sources.SourceError(f"Unknown source: {kind}")
            with self.lock:
                while not self.chunks.empty():
                    self.chunks.get_nowait()          # drop leftovers from the last source
                self.analyzer = Analyzer(self.settings, getattr(src, "samplerate", SAMPLERATE))
                self.delay = self._delay_for(src)
                self.pending.clear()
                self.source = src
                self.running = True
                self.worker = threading.Thread(target=self._run, daemon=True)
                self.worker.start()
                src.start()
            self.status = "Listening"
        except Exception as e:                       # show the problem in the app
            self.error = str(e) or e.__class__.__name__
            self.status = "Stopped"
        finally:
            self.busy = False

    def stop(self):
        with self.lock:
            src, self.source = self.source, None
            self.running = False
            self.pending.clear()
            self.view = None
            self.chunks.put(None)                    # wake the audio loop right away
        if src:
            try:
                src.stop()
            except Exception:
                pass
        if self.worker:
            self.worker.join(timeout=1)
            self.worker = None
        self.light.update(self.settings.get("floor", 0.0), None)
        if not self.busy:
            self.status = "Ready"

    def shutdown(self):
        self.stop()
        self.light.restore()

    def _set_status(self, text):
        self.status = text

    @staticmethod
    def _delay_for(src):
        """Wait until the sound actually comes out of the speakers/earphones,
        minus the moment the keyboard light itself takes to react."""
        return min(MAX_DELAY, max(0.0, getattr(src, "out_latency", 0.0) - KEYBOARD_LAG))

    def _follow_loop(self):
        """Keeps the sound and the light on whatever the Mac plays through:
        earphones plugged in or out, AirPods connecting, another output picked."""
        while True:
            time.sleep(FOLLOW_EVERY)
            src = self.source
            if src is None or not self.running or self.busy or not hasattr(src, "follow"):
                continue
            try:
                moved = src.follow()
            except Exception as e:
                if self.source is src:
                    self.stop()
                    self.error = str(e) or e.__class__.__name__
                    self.status = "Stopped"
                continue
            if moved:
                with self.lock:
                    if self.source is not src:
                        continue
                    if src.samplerate != self.analyzer.samplerate:
                        self.analyzer = Analyzer(self.settings, src.samplerate)
                    self.delay = self._delay_for(src)     # e.g. Bluetooth earbuds lag more than speakers
                    self.pending.clear()

    # ---------------------------------------------------------------- audio loop
    def _run(self):
        while self.running:
            try:
                item = self.chunks.get(timeout=0.5)
                if item is None:
                    continue
                heard_at, chunk = item
            except queue.Empty:
                src = self.source
                if src is not None and not src.active and src.kind == "demo":
                    self.status = "Finished"
                    self.running = False
                    self.source = None
                    self.light.update(self.settings.get("floor", 0.0), None)
                    try:
                        src.stop()
                    except Exception:
                        pass
                continue
            level = self.analyzer.feed(chunk)
            bands = self.analyzer.bands
            self.pending.append((heard_at + self.delay, level,
                                 [d.env for _, _, d in bands], [d.level for _, _, d in bands]))
            self.lag_ms = self.chunks.qsize() * HOP / self.analyzer.samplerate * 1000

    def _show_loop(self):
        """Applies each light change at the moment its sound comes out."""
        while True:
            now = time.perf_counter()
            while self.pending and self.pending[0][0] <= now:
                _, level, envs, levels = self.pending.popleft()
                if self.running:
                    self.light.update(level, None)
                    self.view = (level, envs, levels)
            time.sleep(0.003)

    # ---------------------------------------------------------------- for the app
    def snapshot(self):
        src = self.source
        a = self.analyzer
        view = self.view if self.running else None
        return {
            "running": self.running,
            "busy": self.busy,
            "status": self.status,
            "error": self.error,
            "kind": src.kind if src else None,
            "title": src.title if src else None,
            "subtitle": getattr(src, "subtitle", None) if src else None,
            "output": getattr(src, "output", None) if src else None,
            "position": src.position if src else None,
            "duration": src.duration if src else None,
            "light": round(view[0], 3) if view else 0.0,
            "bands": [{"name": n, "label": l, "env": round(view[1][i], 3), "level": round(view[2][i], 3)}
                      for i, (n, l, _) in enumerate(a.bands)] if view else
                     [{"name": n, "label": l, "env": 0, "level": 0} for n, l, d in a.bands],
            "delay_ms": round(self.delay * 1000),
            "lag": round(self.lag_ms),
        }
