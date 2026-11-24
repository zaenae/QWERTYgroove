"""Music analysis: turns audio into a light level and per-instrument activity."""
from collections import deque

import numpy as np

SAMPLERATE = 44100
HOP = 256            # a new analysis every 5.8 ms
WINDOW = 1024        # each analysis looks at the last 23 ms of sound

# name, low Hz, high Hz, shortest gap between hits (s), label,
# weight (how strongly a hit in this range flashes the keyboard),
# target hits per second (each range tunes itself towards this, per song)
BANDS = [
    ("kick",   40,    150,   0.20, "Kick",  1.00, 2.2),
    ("bass",   150,   300,   0.12, "Bass",  0.70, 1.6),
    ("low",    300,   600,   0.10, "Snare", 1.00, 2.0),
    ("mid",    600,   1200,  0.08, "Vocal", 0.70, 4.0),
    ("upper",  1200,  2500,  0.08, "Lead",  0.60, 2.4),
    ("pres",   2500,  5000,  0.07, "Snap",  0.55, 2.4),
    ("bright", 5000,  10000, 0.06, "Hats",  0.32, 3.0),
    ("air",    10000, 16000, 0.06, "Cymb",  0.28, 3.0),
]
MODES = ("all", "hits", "bass", "groove")


class BandOnset:
    """Fires when one frequency range gets a new note or hit. Looks at every
    pitch in the range separately, so a new note is caught even when the
    overall volume barely changes. Held notes don't count. Its sensitivity
    drifts so it fires about `target` times a second on any song: busy songs
    don't turn into constant flicker, quiet songs still get flashes."""

    def __init__(self, low, high, min_gap, weight, target, freqs, fps):
        self.mask = (freqs >= low) & (freqs < high)
        self.lookback = deque(maxlen=3)                       # compare with ~17 ms ago
        self.flux_hist = deque(maxlen=int(1.5 * fps))         # last 1.5 s
        self.level_hist = deque(maxlen=int(4 * fps))          # last 4 s
        self.min_gap = int(min_gap * fps)
        self.weight, self.target, self.fps = weight, target, fps
        self.adapt = 1.0                                      # self-tuning sensitivity factor
        self.rate = target                                    # recent hits per second
        self.rate_k = 1 / (4 * fps)                           # ~4 s memory
        self.since = 10 ** 6
        self.db = -100.0
        self.env = 0.0       # flash envelope 0..1 (jumps on hits, fades)
        self.level = 0.0     # smooth loudness 0..1 relative to the last 4 s

    def update(self, power, spec_db, sensitivity, decay):
        self.db = 10.0 * np.log10(float(np.sum(power[self.mask])) + 1e-10)
        bins = spec_db[self.mask]
        flux = float(np.mean(np.maximum(0.0, bins - self.lookback[0]))) if len(self.lookback) == 3 else 0.0
        self.lookback.append(bins)
        self.level_hist.append(self.db)
        top = max(self.level_hist)
        strength = 0.0
        self.since += 1
        sens = sensitivity * self.adapt
        hit = False
        if len(self.flux_hist) > 20 and self.since >= self.min_gap and self.db > -70:
            h = np.fromiter(self.flux_hist, float)
            mean, std = h.mean(), h.std() + 1e-6
            if flux > max(mean + sens * std, 2.0):
                hit = True
                self.since = 0
                sharp = min(1.0, 0.45 + 0.12 * ((flux - mean) / std - sensitivity))   # brightness ignores self-tuning
                loud = min(1.0, max(0.0, (self.db - (top - 30.0)) / 30.0))
                strength = sharp * (0.4 + 0.6 * loud)          # louder + sharper = brighter
        self.flux_hist.append(flux)
        # self-tuning: too many hits -> stricter, too few -> more relaxed
        self.rate += ((self.fps if hit else 0.0) - self.rate) * self.rate_k
        if self.rate > self.target * 1.25:
            self.adapt = min(2.2, self.adapt * 1.0015)
        elif self.rate < self.target * 0.6:
            self.adapt = max(1.0, self.adapt * 0.9985)
        self.env = max(strength, self.env * decay)
        x = min(1.0, max(0.0, (self.db - (top - 40.0)) / 40.0)) if self.db > -70 else 0.0
        self.level += (x - self.level) * (0.5 if x > self.level else 0.08)
        return strength * self.weight


class Groove:
    """Loudness scaled to the song's own recent quiet..loud range (0..1)."""

    def __init__(self, fps):
        self.hist = deque(maxlen=int(4 * fps))
        self.level = 0.0
        self.attack = 1 - 0.5 ** (1 / (0.02 * fps))   # 20 ms rise
        self.release = 1 - 0.5 ** (1 / (0.15 * fps))  # 150 ms fall

    def update(self, db):
        self.hist.append(db)
        h = np.fromiter(self.hist, float)
        lo, hi = np.percentile(h, 10), np.percentile(h, 98)
        x = 0.0 if hi - lo < 3 else min(1.0, max(0.0, (db - lo) / (hi - lo)))
        x = x ** 1.5
        self.level += (x - self.level) * (self.attack if x > self.level else self.release)
        return self.level


class Analyzer:
    """Feed it HOP-sized chunks of mono audio; read .light and .bands."""

    def __init__(self, settings, samplerate=SAMPLERATE):
        self.settings = settings                 # shared dict, can change while running
        self.samplerate = samplerate             # whatever rate the speakers/earphones run at
        self.fps = samplerate / HOP
        self.freqs = np.fft.rfftfreq(WINDOW, 1.0 / samplerate)
        self.window = np.hanning(WINDOW)
        self.buf = np.zeros(WINDOW, dtype=np.float32)
        self.bands = [(name, label, BandOnset(lo, hi, gap, w, tgt, self.freqs, self.fps))
                      for name, lo, hi, gap, label, w, tgt in BANDS]
        self.all_mask = (self.freqs >= 40) & (self.freqs < 16000)
        self.bass_mask = (self.freqs >= 40) & (self.freqs < 150)
        self.groove = Groove(self.fps)
        self.flash = 0.0
        self.light = 0.0
        self.frame = 0
        self.hold = 0                             # frames left at full peak
        self.beat_times = deque(maxlen=8)         # recent strong hits, to follow the tempo
        self.recent_hits = deque(maxlen=24)       # how hard this song usually hits

    def feed(self, chunk):
        s = self.settings
        mode = s.get("mode", "all")
        self.frame += 1
        self.buf = np.concatenate((self.buf[len(chunk):], chunk))
        power = np.abs(np.fft.rfft(self.buf * self.window)) ** 2
        spec_db = 10.0 * np.log10(power + 1e-10)
        spec_db = np.maximum(spec_db, spec_db.max() - 80.0)

        # how long a flash takes to fade: follows the song's tempo, so flashes fade
        # slowly to dark when beats are far apart and stay crisp when they're fast
        if s.get("fade"):
            half_life = s["fade"]
        elif mode == "bass":
            half_life = 0.15
        elif len(self.beat_times) >= 3:
            gap = float(np.median(np.diff(self.beat_times))) / self.fps
            half_life = min(0.30, max(0.045, 0.18 * gap))
        else:
            half_life = 0.09
        decay = 0.5 ** (1 / (half_life * self.fps))
        band_decay = 0.5 ** (1 / (0.09 * self.fps))      # the on-screen card keeps its own pace

        hits, body = [], False
        for name, _, det in self.bands:
            strength = det.update(power, spec_db, s.get("sensitivity", 1.8), band_decay)
            if strength > 0 and (mode != "bass" or name == "kick"):
                hits.append(strength)
                body = body or name in ("kick", "bass", "low", "mid", "upper")
        if hits:
            hits.sort(reverse=True)
            hit = min(1.0, hits[0] + 0.12 * sum(hits[1:3]))       # several at once = bigger
            # brightness levels, relative to this song: its biggest hits go all the way,
            # softer ones less, so the beat looks layered instead of all-or-nothing
            if body:
                self.recent_hits.append(hit)
            ref = (np.percentile(self.recent_hits, 75) if len(self.recent_hits) >= 6 else 0.6) / s.get("punch", 1.6) * 1.6
            rel = min(1.0, hit / max(ref, 1e-3))
            if body:
                if rel >= 0.7:
                    peak = 0.8 + 0.2 * (rel - 0.7) / (1 - 0.7)  # a real beat: strong and consistent
                else:
                    peak = 0.45 * (rel / 0.7) ** 1.5              # a smaller hit: clearly smaller
                if not self.beat_times or self.frame - self.beat_times[-1] > 0.12 * self.fps:
                    self.beat_times.append(self.frame)
            else:
                peak = min(0.3, rel ** 1.6)                       # only hi-hats/cymbals: a faint flicker
                if peak < 0.12:
                    peak = 0.0                                    # too faint to matter: skip it (less flicker)
            if peak > self.flash:
                self.flash = peak
                self.hold = int(0.035 * self.fps)                # stay at the top for a moment
        if self.hold > 0:
            self.hold -= 1
        else:
            self.flash *= decay
        if self.flash < 0.03:
            self.flash = 0.0                                      # all the way dark between beats

        if mode == "groove":
            level = self.groove.update(10.0 * np.log10(float(np.sum(power[self.all_mask])) + 1e-10))
        elif mode == "bass":
            g = self.groove.update(10.0 * np.log10(float(np.sum(power[self.bass_mask])) + 1e-10))
            level = max(self.flash, 0.5 * g)
        else:
            level = self.flash                                    # "all" and "hits": flashes only, dark in between
        floor = s.get("floor", 0.0)
        self.light = floor + (1.0 - floor) * level
        return self.light
