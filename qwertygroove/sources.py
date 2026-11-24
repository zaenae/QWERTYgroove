"""Where the music comes from: the demo song, or your music (anything the Mac plays: Spotify, Apple Music, YouTube...)."""
import os
import shutil
import subprocess
import tempfile
import time
import wave

import numpy as np

from . import coreaudio
from .analysis import SAMPLERATE, HOP

CACHE = os.path.join(os.path.expanduser("~"), ".qwertygroove", "cache")

# A free song by Kevin MacLeod (incompetech.com), CC BY 4.0, downloaded once.
DEMO_TITLE = "Funkorama"
DEMO_URL = "https://incompetech.com/music/royalty-free/mp3-royaltyfree/Funkorama.mp3"
DEMO_CREDIT = "“Funkorama” by Kevin MacLeod (incompetech.com), CC BY 4.0"


class SourceError(Exception):
    pass


def _sd():
    """sounddevice, with a fresh list of audio devices (so earphones or AirPods
    plugged in after the app opened are found)."""
    import sounddevice
    try:
        sounddevice._terminate()
        sounddevice._initialize()
    except Exception:
        pass
    return sounddevice


# --------------------------------------------------------------------------
# Spotify: the Mac's sound goes through BlackHole (so we can hear it) and on to
# the real speakers/headphones. The output is switched back when stopped.
# --------------------------------------------------------------------------

def _tool():
    """Older installs used the SwitchAudioSource tool; still used if macOS's
    audio system can't be reached directly for some reason."""
    for p in (shutil.which("SwitchAudioSource"), "/opt/homebrew/bin/SwitchAudioSource",
              "/usr/local/bin/SwitchAudioSource"):
        if p and os.path.exists(p):
            return p
    return None


def _run_tool(*args):
    tool = _tool()
    if not tool:
        return ""
    return subprocess.run([tool, *args], capture_output=True, text=True).stdout


def can_switch():
    return coreaudio.available() or _tool() is not None


def current_output():
    if coreaudio.available():
        return coreaudio.current_output()
    return _run_tool("-c", "-t", "output").strip() or None


def output_names():
    if coreaudio.available():
        return [name for _, name, _ in coreaudio.outputs()]
    return [o for o in _run_tool("-a", "-t", "output").splitlines() if o.strip()]


def switch_output(name):
    if coreaudio.available():
        return coreaudio.switch_output(name)
    _run_tool("-t", "output", "-s", name)
    return True


def _is_blackhole(name):
    return "blackhole" in (name or "").lower()


def real_output(sd):
    """(device index, name) of the real speakers/headphones. If the Mac's sound
    output was left on BlackHole (e.g. after a crash), switch it back first, so
    you can actually hear things."""
    current = current_output() if can_switch() else None
    if can_switch() and (not current or _is_blackhole(current)):
        real = [o for o in output_names() if not _is_blackhole(o) and "multi" not in o.lower()]
        if real:
            current = next((o for o in real if coreaudio.is_built_in(o) or "speaker" in o.lower()), real[0])
            switch_output(current)
        else:
            current = None
    devices = sd.query_devices()
    outputs = [(i, d["name"]) for i, d in enumerate(devices)
               if d["max_output_channels"] > 0 and not _is_blackhole(d["name"])]
    if current:
        for i, name in outputs:
            if name == current:
                return i, name
    try:
        default = sd.default.device[1]
        if default is not None and default >= 0 and not _is_blackhole(devices[default]["name"]):
            return default, devices[default]["name"]
    except Exception:
        pass
    if outputs:
        speakers = [o for o in outputs if "speaker" in o[1].lower()]
        return (speakers or outputs)[0]
    raise SourceError("Couldn't find any speakers or headphones.")


def _rates(sd, device):
    """Rates to try for a device: its own rate first (earphones/AirPods are often
    48 kHz only), then the common ones."""
    try:
        native = int(round(sd.query_devices(device)["default_samplerate"]))
    except Exception:
        native = 0
    out = []
    for r in (native, 48000, 44100):
        if r and r not in out:
            out.append(r)
    return out


# Bluetooth earphones: macOS sometimes reports far less delay than they really have
BLUETOOTH_HINTS = ("airpods", "beats", "bluetooth", "buds", "bose", "sony wh", "sony wf", "jbl", "powerbeats")
BLUETOOTH_TYPICAL = 0.17          # typical real delay of Bluetooth earbuds (seconds)


def out_latency(stream, device_name):
    """Seconds between handing sound to the speakers/earphones and hearing it."""
    try:
        lat = stream.latency                    # what macOS reports (buffers + device)
        lat = float(lat[1] if isinstance(lat, (tuple, list)) else lat)
    except Exception:
        lat = 0.02
    bluetooth = coreaudio.is_bluetooth(device_name)
    if bluetooth is None:                       # couldn't ask macOS: go by the name
        bluetooth = any(h in (device_name or "").lower() for h in BLUETOOTH_HINTS)
    if bluetooth and lat < 0.12:                # reported delay is too small to be real
        lat = max(lat, 0.03) + BLUETOOTH_TYPICAL - 0.03
    return lat


def _open(make, rates, where):
    """Open a stream at the first rate the device accepts."""
    last = None
    for rate in rates:
        try:
            return make(rate), rate
        except Exception as e:                 # e.g. "Invalid sample rate"
            last = e
    raise SourceError(f"Couldn't play through {where}. ({last})")


# Where the audio helper is installed. If it's there but macOS hasn't loaded it
# yet, restarting the Mac's sound service loads it (no need to restart the Mac).
HELPER_DRIVER = "/Library/Audio/Plug-Ins/HAL/BlackHole2ch.driver"
NOT_SET_UP = "Music listening isn't set up yet. Run the install command again and you're set."


def _helper_loaded(sd):
    try:
        return any(_is_blackhole(d["name"]) for d in sd.query_devices())
    except Exception:
        return False


def spotify_status():
    """(ready, message) for the app to show."""
    if not can_switch():
        return False, NOT_SET_UP
    if not _helper_loaded(_sd()) and not os.path.exists(HELPER_DRIVER):
        return False, NOT_SET_UP
    return True, "Your keyboard follows anything you play."


def _switch_on_helper():
    """The helper is installed but not loaded yet: restart the Mac's sound
    service (macOS shows its normal password box once), then wait for it."""
    r = subprocess.run(["osascript", "-e",
                        'do shell script "killall coreaudiod" with administrator privileges'],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SourceError("Sound setup was cancelled. Click Try again whenever you're ready.")
    for _ in range(24):                          # the sound service comes back within a few seconds
        time.sleep(0.5)
        sd = _sd()
        if _helper_loaded(sd):
            return sd
    raise SourceError("Couldn't switch on sound for your music. Try again in a moment.")


class SpotifySource:
    kind = "spotify"
    title = "Your groove"

    def __init__(self, push):
        sd = _sd()
        if not can_switch():
            raise SourceError(NOT_SET_UP)
        if not _helper_loaded(sd):
            if not os.path.exists(HELPER_DRIVER):
                raise SourceError(NOT_SET_UP)
            sd = _switch_on_helper()
        out, original = real_output(sd)
        devices = sd.query_devices()
        bh = next((i for i, d in enumerate(devices)
                   if _is_blackhole(d["name"]) and d["max_input_channels"] > 0), None)
        if bh is None:
            raise SourceError("Couldn't hear your music. Try restarting your Mac.")
        self.helper = devices[bh]["name"]                 # e.g. "BlackHole 2ch"
        self.original = original
        self.output = original
        self.subtitle = f"Sound goes to {original}"

        def callback(indata, outdata, frames, time_info, status):
            outdata[:] = indata
            push(indata.mean(axis=1).astype(np.float32))
        self.stream, self.samplerate = _open(
            lambda rate: sd.Stream(device=(bh, out), channels=2, samplerate=rate, blocksize=HOP,
                                   dtype="float32", latency="low", callback=callback),
            _rates(sd, out), original)
        self.out_latency = out_latency(self.stream, original)

    active = property(lambda self: self.stream.active)
    position = duration = None

    def start(self):
        self.stream.start()
        switch_output(self.helper)

    def stop(self):
        switch_output(self.original)
        self.stream.stop()
        self.stream.close()


# --------------------------------------------------------------------------
# Demo song
# --------------------------------------------------------------------------

def load_audio(path):
    """Song file -> float32 array [samples, channels] at 44.1 kHz (via macOS afconvert)."""
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
    try:
        if shutil.which("afconvert"):
            if subprocess.run(["afconvert", "-f", "WAVE", "-d", f"LEI16@{SAMPLERATE}", path, tmp],
                              capture_output=True).returncode != 0:
                raise SourceError("Something went wrong with the demo song.")
            src = tmp
        elif shutil.which("ffmpeg"):                    # only used when testing off a Mac
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", path, "-ar", str(SAMPLERATE),
                            "-acodec", "pcm_s16le", tmp], check=True)
            src = tmp
        else:
            src = path
        with wave.open(src) as w:
            ch = w.getnchannels()
            raw = w.readframes(w.getnframes())
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return (np.frombuffer(raw, dtype="<i2").reshape(-1, ch)[:, :2].astype(np.float32) / 32768.0)


def built_in_beat(seconds=32, bpm=120):
    """A simple beat, used if the demo song can't be downloaded (e.g. offline)."""
    sr = SAMPLERATE
    t = np.arange(int(seconds * sr)) / sr
    song = np.zeros_like(t)
    beat = 60.0 / bpm
    rng = np.random.default_rng(0)
    for i, start in enumerate(np.arange(0, seconds, beat)):
        a = int(start * sr)
        n = min(int(beat * sr), len(t) - a)
        tt = t[:n]
        song[a:a + n] += 0.18 * np.sin(2 * np.pi * [55.0, 55.0, 65.4, 49.0][(i // 4) % 4] * t[a:a + n])
        k = min(int(0.25 * sr), n)
        sweep = 50 + 90 * np.exp(-tt[:k] * 30)
        song[a:a + k] += 0.9 * np.sin(2 * np.pi * np.cumsum(sweep) / sr) * np.exp(-tt[:k] * 12) \
            * np.minimum(1, (k - np.arange(k)) / 400)
        if i % 2 == 1:
            s = min(int(0.15 * sr), n)
            song[a:a + s] += 0.25 * rng.standard_normal(s) * np.exp(-tt[:s] * 25)
        h0 = int((start + beat / 2) * sr)
        h = min(int(0.04 * sr), len(t) - h0)
        if h > 0:
            song[h0:h0 + h] += 0.08 * np.diff(rng.standard_normal(h + 1)) * np.exp(-tt[:h] * 120)
    return (0.6 * song / np.max(np.abs(song))).astype(np.float32)[:, None]


_demo = None


def get_demo(progress=None):
    """Returns (audio, title, credit). Downloads the demo song the first time,
    and keeps it in memory so switching back to it is instant."""
    global _demo
    if _demo is not None:
        return _demo
    _demo = _load_demo(progress)
    return _demo


def _load_demo(progress=None):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, DEMO_TITLE + ".mp3")
    if not (os.path.exists(path) and os.path.getsize(path) > 100_000):
        if progress:
            progress("Grabbing the demo song…")
        ok = subprocess.run(["curl", "-sfL", "--connect-timeout", "8", "--max-time", "60",
                             "-o", path, DEMO_URL]).returncode == 0
        if not (ok and os.path.exists(path) and os.path.getsize(path) > 100_000):
            if os.path.exists(path):
                os.remove(path)
            return built_in_beat(), "Demo beat", "Built-in beat (the demo song couldn't be downloaded)"
    return load_audio(path), DEMO_TITLE, DEMO_CREDIT


_resampled = {}


def resampled(song, rate):
    """The demo song at the device's rate (made once per rate, then reused)."""
    if rate == SAMPLERATE:
        return song
    key = (id(song), rate)
    if key not in _resampled:
        n = int(len(song) * rate / SAMPLERATE)
        x = np.linspace(0, len(song) - 1, n)
        _resampled[key] = np.stack([np.interp(x, np.arange(len(song)), song[:, c])
                                    for c in range(song.shape[1])], 1).astype(np.float32)
    return _resampled[key]


class DemoSource:
    kind = "demo"

    def __init__(self, push, progress=None):
        sd = _sd()
        device, self.output = real_output(sd)
        song44, self.title, self.subtitle = get_demo(progress)
        self.pos = 0
        holder = {}

        def callback(outdata, frames, time_info, status):
            song = holder["song"]
            chunk = song[self.pos:self.pos + frames]
            outdata[:len(chunk)] = chunk
            outdata[len(chunk):] = 0
            self.pos += frames
            if len(chunk):
                push(chunk.mean(axis=1).astype(np.float32))
            if len(chunk) < frames:
                raise sd.CallbackStop
        def make(rate):
            holder["song"] = resampled(song44, rate)
            return sd.OutputStream(device=device, channels=song44.shape[1], samplerate=rate,
                                   blocksize=HOP, dtype="float32", latency="low", callback=callback)
        self.stream, self.samplerate = _open(make, _rates(sd, device), self.output)
        self.song = holder["song"]
        self.out_latency = out_latency(self.stream, self.output)

    active = property(lambda self: self.stream.active)
    position = property(lambda self: min(self.pos, len(self.song)) / self.samplerate)
    duration = property(lambda self: len(self.song) / self.samplerate)

    def start(self):
        self.stream.start()

    def stop(self):
        self.stream.stop()
        self.stream.close()
