"""The MacBook keyboard backlight.

It's driven from its own background thread, so the music analysis never waits
on it, and every beat reaches full brightness even if the light was busy."""
import platform
import threading
import time

import numpy as np


class LightError(Exception):
    pass


def make_light():
    if platform.system() != "Darwin":
        raise LightError("QWERTYgroove only works on a Mac.")
    return MacLight()


class MacLight:
    FRAMEWORK = "/System/Library/PrivateFrameworks/CoreBrightness.framework"
    RATE = 120               # updates per second (lowered automatically if needed)

    def __init__(self):
        try:
            import objc
        except ImportError:
            raise LightError("Missing PyObjC. Run: python3 -m pip install --user pyobjc-core")
        try:
            objc.loadBundle("CoreBrightness", {}, bundle_path=self.FRAMEWORK)
            self.c = objc.lookUpClass("KeyboardBrightnessClient").alloc().init()
        except Exception as e:
            raise LightError(f"Couldn't reach the keyboard backlight ({e}).")
        has = lambda sel: bool(self.c.respondsToSelector_(sel))
        self.kid = 1
        if has("copyKeyboardBacklightIDs"):
            ids = self.c.copyKeyboardBacklightIDs()
            if ids:
                self.kid = int(ids[0])
        self.original = float(self.c.brightnessForKeyboard_(self.kid))
        if has("setBrightness:fadeSpeed:commit:forKeyboard:"):
            self._set = lambda v: self.c.setBrightness_fadeSpeed_commit_forKeyboard_(v, 0, False, self.kid)
        else:
            self._set = lambda v: self.c.setBrightness_forKeyboard_(v, self.kid)
        self.auto_was_on = None
        if has("isAutoBrightnessEnabledForKeyboard:") and has("enableAutoBrightness:forKeyboard:"):
            self.auto_was_on = bool(self.c.isAutoBrightnessEnabledForKeyboard_(self.kid))
            self.c.enableAutoBrightness_forKeyboard_(False, self.kid)
        self.idle = has("suspendIdleDimming:forKeyboard:")
        if self.idle:
            self.c.suspendIdleDimming_forKeyboard_(True, self.kid)
        self.target = 0.0
        self.peak = 0.0
        self.call_ms = 0.0
        self.running = True
        self.paused = False
        threading.Thread(target=self._loop, daemon=True).start()

    @property
    def description(self):
        return "MacBook keyboard backlight"

    def _loop(self):
        shown, sent = 0.0, -1.0
        period = 1.0 / self.RATE
        next_t = time.perf_counter()
        while self.running:
            if not self.paused:
                target = max(self.target, self.peak)
                self.peak = 0.0
                k = 1.0 if target > shown else 1 - 0.55 ** (60 * period)   # rise instantly, fall softer
                shown += (target - shown) * k
                if abs(shown - sent) > 0.004:
                    t0 = time.perf_counter()
                    try:
                        self._set(float(shown))
                    except Exception:
                        pass
                    self.call_ms = 0.8 * self.call_ms + 0.2 * (time.perf_counter() - t0) * 1000
                    sent = shown
                if self.call_ms / 1000 > 0.8 * period and period < 1 / 30:
                    period = min(1 / 30, period * 1.25)                     # Mac can't keep up
            next_t += period
            time.sleep(max(0.0, next_t - time.perf_counter()))

    def update(self, light, bands):
        self.target = light
        self.peak = max(self.peak, light)

    def test(self):
        for i in range(121):
            self.update(0.5 - 0.5 * np.cos(i / 120 * 2 * np.pi), None)
            time.sleep(1 / 60)
        for _ in range(4):
            self.update(1.0, None)
            time.sleep(0.12)
            self.update(0.0, None)
            time.sleep(0.35)

    def restore(self):
        self.running = False
        time.sleep(0.05)
        try:
            self.c.setBrightness_forKeyboard_(self.original, self.kid)
            if self.auto_was_on:
                self.c.enableAutoBrightness_forKeyboard_(True, self.kid)
            if self.idle:
                self.c.suspendIdleDimming_forKeyboard_(False, self.kid)
        except Exception:
            pass


# --------------------------------------------------------------------------

class PreviewLight:
    """No keyboard light available: the app still runs, the light just doesn't change."""

    def __init__(self, reason=""):
        self.reason = reason
        self.log = []

    description = property(lambda self: "Preview only (no keyboard light)")

    def update(self, light, bands):
        self.log.append((time.perf_counter(), light))
        if len(self.log) > 100000:
            del self.log[:50000]

    def test(self):
        time.sleep(1)

    def restore(self):
        pass
