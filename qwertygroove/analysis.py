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


def _widen(spec, k):
    """Each pitch takes the loudest value of its neighbours (k on each side)."""
    out = spec.copy()
    for i in range(1, k + 1):
        np.maximum(out[i:], spec[:-i], out=out[i:])
        np.maximum(out[:-i], spec[i:], out=out[:-i])
    return out


def _spread(spec, k):
    """For each pitch, the middle value of its neighbourhood (k on each side).
    High for a wall of sound (drums, noise), low between the separate pitches
    of a voice or an instrument playing a note."""
    padded = np.pad(spec, k, mode="edge")
    return np.median(np.lib.stride_tricks.sliding_window_view(padded, 2 * k + 1), axis=1)


class _Recent:
    """Average and spread of the last `n` numbers, kept as running totals so
    it costs the same however long the memory is."""

    def __init__(self, n):
        self.hist = deque(maxlen=n)
        self.total = self.squares = 0.0
        self.count = 0

    def __len__(self):
        return len(self.hist)

    def add(self, x):
        if len(self.hist) == self.hist.maxlen:
            old = self.hist[0]
            self.total -= old
            self.squares -= old * old
        self.hist.append(x)
        self.total += x
        self.squares += x * x
        self.count += 1
        if self.count % 4096 == 0:                # tidy up rounding now and then
            self.total = float(sum(self.hist))
            self.squares = float(sum(v * v for v in self.hist))

    def mean_std(self):
        n = len(self.hist)
        mean = self.total / n
        return mean, max(0.0, self.squares / n - mean * mean) ** 0.5 + 1e-6


class _RecentMax:
    """The largest of the last `n` numbers, without re-reading them all."""

    def __init__(self, n):
        self.n, self.i, self.q = n, 0, deque()

    def add(self, x):
        while self.q and self.q[-1][1] <= x:
            self.q.pop()
        self.q.append((self.i, x))
        if self.q[0][0] <= self.i - self.n:
            self.q.popleft()
        self.i += 1
        return self.q[0][1]


class _Usual:
    """How unusual a number is compared with the last 1.5 s."""

    def __init__(self, fps):
        self.hist = _Recent(int(1.5 * fps))

    def z(self, x):
        z = 0.0
        if len(self.hist) > 20:
            mean, std = self.hist.mean_std()
            z = (x - mean) / std
        self.hist.add(x)
        return z


class BandOnset:
    """Fires when one frequency range gets a new note or hit. Looks at every
    pitch in the range separately, so a new note is caught even when the
    overall volume barely changes. Held notes don't count, and neither does a
    voice wobbling or sliding in pitch: each pitch is compared with the loudest
    of its neighbours a moment ago. Its sensitivity drifts so it fires about
    `target` times a second on any song: busy songs don't turn into constant
    flicker, quiet songs still get flashes."""

    def __init__(self, low, high, min_gap, weight, target, freqs, fps):
        self.mask = (freqs >= low) & (freqs < high)
        self.blur = 0 if low < 100 else (1 if low < 300 else 2)   # how far a pitch may wobble (steps of ~43 Hz)
        self.flux_hist = _Recent(int(1.5 * fps))              # last 1.5 s
        self.level_top = _RecentMax(int(4 * fps))             # loudest moment in the last 4 s
        self.min_gap = int(min_gap * fps)
        self.weight, self.target, self.fps = weight, target, fps
        self.adapt = 1.0                                      # self-tuning sensitivity factor
        self.rate = target                                    # recent hits per second
        self.rate_k = 1 / (4 * fps)                           # ~4 s memory
        self.since = 10 ** 6
        self.z = 0.0         # how unusual this moment's change is for this range
        self.db = -100.0
        self.env = 0.0       # flash envelope 0..1 (jumps on hits, fades)
        self.level = 0.0     # smooth loudness 0..1 relative to the last 4 s

    def update(self, power, spec_db, past, sensitivity, decay):
        """past: the blurred spectra from ~17 ms ago (None at the very start)."""
        self.db = 10.0 * np.log10(float(np.sum(power[self.mask])) + 1e-10)
        bins = spec_db[self.mask]
        flux = float(np.mean(np.maximum(0.0, bins - past[self.blur][self.mask]))) if past is not None else 0.0
        top = self.level_top.add(self.db)
        strength = 0.0
        self.since += 1
        sens = sensitivity * self.adapt
        hit = False
        self.z = 0.0
        if len(self.flux_hist) > 20:
            mean, std = self.flux_hist.mean_std()
            self.z = (flux - mean) / std
        if len(self.flux_hist) > 20 and self.since >= self.min_gap and self.db > -70:
            if flux > max(mean + sens * std, 2.0):
                hit = True
                self.since = 0
                sharp = min(1.0, 0.45 + 0.12 * ((flux - mean) / std - sensitivity))   # brightness ignores self-tuning
                loud = min(1.0, max(0.0, (self.db - (top - 30.0)) / 30.0))
                strength = sharp * (0.4 + 0.6 * loud)          # louder + sharper = brighter
        self.flux_hist.add(flux)
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

    def __init__(self, fps, rise=0.02, fall=0.15):
        self.hist = deque(maxlen=int(4 * fps))
        self.n = 0
        self.lo = self.hi = 0.0
        self.level = 0.0
        self.attack = 1 - 0.5 ** (1 / (rise * fps))
        self.release = 1 - 0.5 ** (1 / (fall * fps))

    def update(self, db):
        self.hist.append(db)
        if self.n % 8 == 0:                       # the song's range barely moves in 50 ms: refresh it now and then
            h = np.fromiter(self.hist, float)
            self.lo, self.hi = np.percentile(h, [10, 98])
        self.n += 1
        lo, hi = self.lo, self.hi
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
        self.tune_mask = (self.freqs >= 150) & (self.freqs < 5000)
        self.tune_glow = Groove(self.fps, rise=0.12, fall=0.45)  # soft and slow: it can't flicker
        self.glow_on = 0.0                         # 0..1, how much of the glow is showing
        self.flash = 0.0
        self.light = 0.0
        self.frame = 0
        self.hold = 0                             # frames left at full peak
        self.past = deque(maxlen=3)               # blurred spectra of the last few moments
        self.mid_mask = (self.freqs >= 200) & (self.freqs < 2500)
        idx = np.flatnonzero(self.mid_mask)
        self.mid_span = (idx[0], idx[-1] + 1)     # where the "wall of sound" is measured
        self.wall = _Usual(self.fps)              # how much a "wall of sound" just started (drums)
        self.last_noise = -10 ** 6                # last moment with a burst of high noise (snare, clap)
        self.last_beat = -10 ** 6                 # last moment a drum hit
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
            half_life = min(0.30, max(0.045, 0.13 * gap))
        else:
            half_life = 0.09
        decay = 0.5 ** (1 / (half_life * self.fps))
        band_decay = 0.5 ** (1 / (0.09 * self.fps))      # the on-screen card keeps its own pace

        past = self.past[0] if len(self.past) == 3 else None
        a, b = self.mid_span
        spread = _spread(spec_db[max(0, a - 12):b + 12], 12)[a - max(0, a - 12):][:b - a]
        self.past.append((spec_db, _widen(spec_db, 1), _widen(spec_db, 2), spread))
        fired = {}
        for name, _, det in self.bands:
            strength = det.update(power, spec_db, past, s.get("sensitivity", 1.8), band_decay)
            if strength > 0 and (mode != "bass" or name == "kick"):
                fired[name] = strength
        z = {name: det.z for name, _, det in self.bands}

        # Is it a drum, or just the tune (a voice, a piano note, a guitar chord)?
        # Drums start a wall of sound across many pitches at once, and are loud
        # in the mix. A voice only lights up its own few pitches, even when it
        # slides or wobbles. Only drums flash the keyboard.
        wall = float(np.mean(np.maximum(0.0, spread - past[3]))) if past else 0.0
        wall_z = self.wall.z(wall)
        raw = float(np.mean(np.maximum(0.0, spec_db[self.mid_mask] - past[0][self.mid_mask]))) if past else 0.0
        dense = wall / (raw + 1e-6)               # near 1: every pitch jumped (drums). low: only a few did (a voice)
        if max(z["pres"], z["bright"]) > 2.5:
            self.last_noise = self.frame
        noisy = self.frame - self.last_noise <= 2
        drummy = wall_z > 2.0
        tune = max(z["mid"], z["upper"])
        total = 10.0 * np.log10(float(np.sum(power[self.all_mask])) + 1e-10)
        # a real part of the mix, not a faint echo of something else (a kick gets
        # more room: it's only just starting at the moment it's spotted)
        loud = {name: det.db > total - (24.0 if name == "kick" else 18.0) for name, _, det in self.bands}

        drum = set()
        if "kick" in fired and loud["kick"] and (z["kick"] > tune or drummy or noisy):
            drum.add("kick")
        # a bass note also moves the very lowest pitches, where voices don't reach
        if "bass" in fired and loud["bass"] and (drummy or (z["kick"] > 1.0 and z["bass"] > tune)):
            drum.add("bass")
        # a snare, clap or rimshot: a burst of noise up high, and every pitch in
        # between jumps at once (a voice only jumps on its own few pitches)
        if noisy and wall_z > 4.5 and dense > 0.85:
            drum.update(k for k in ("low", "mid", "upper") if k in fired)
        if drum:
            for k in ("mid", "upper"):            # the rest of the same drum hit
                if k in fired and drummy:
                    drum.add(k)
        gap = float(np.median(np.diff(self.beat_times))) / self.fps if len(self.beat_times) >= 3 else 0.5
        drums_lately = self.frame - self.last_beat < max(2.0, 2.5 * gap) * self.fps
        body = bool(drum)
        if drum:
            hits = [fired[k] for k in drum] + [fired[k] for k in ("pres", "bright", "air") if k in fired]
        elif drums_lately:
            hits = [fired[k] for k in ("pres", "bright", "air") if k in fired]   # hi-hats between beats
        else:
            hits = []                             # the tune on its own (or "s"/"t" sounds): no flashes
        if drum:
            self.last_beat = self.frame
            drums_lately = True
        # With no drums (an intro, a breakdown, a ballad) the light gently follows
        # the music's loudness instead, so it's alive but never flickery.
        glow = self.tune_glow.update(10.0 * np.log10(float(np.sum(power[self.tune_mask])) + 1e-10))
        self.glow_on += ((0.0 if drums_lately else 1.0) - self.glow_on) * (0.25 if drums_lately else 0.004)
        if hits:
            hits.sort(reverse=True)
            hit = min(1.0, hits[0] + 0.12 * sum(hits[1:3]))       # several at once = bigger
            # brightness levels, relative to this song: its biggest hits go all the way,
            # softer ones less, so the beat looks layered instead of all-or-nothing
            if body:
                self.recent_hits.append(hit)
            # (only real drums count here now, so most hits are proper beats;
            # the small level is for soft, ghost-like hits)
            ref = (np.percentile(self.recent_hits, 60) if len(self.recent_hits) >= 6 else 0.6) / s.get("punch", 1.6) * 1.6
            rel = min(1.0, hit / max(ref, 1e-3))
            if body:
                if rel >= 0.45:
                    peak = 0.8 + 0.2 * (rel - 0.45) / (1 - 0.45)  # a real beat: strong and consistent
                else:
                    peak = 0.45 * (rel / 0.45) ** 1.5             # a softer hit: clearly smaller
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
        elif mode == "all":
            level = max(self.flash, 0.32 * glow * self.glow_on)   # flashes on drums, a soft glow without them
        else:
            level = self.flash                                    # "hits": flashes only, dark in between
        floor = s.get("floor", 0.0)
        self.light = floor + (1.0 - floor) * level
        return self.light
