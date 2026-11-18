"""A quiet music-box waltz for the example film."""
import wave
from pathlib import Path

import numpy as np


def write_waltz(path: Path, seconds: float) -> None:
    rate = 32000
    samples = np.zeros(round(seconds * rate), dtype=np.float32)

    def note(at, midi, length, volume, decay):
        start = round(at * rate)
        count = min(round(length * rate), len(samples) - start)
        if count <= 0:
            return
        t = np.arange(count) / rate
        phase = 2 * np.pi * 440 * 2 ** ((midi - 69) / 12) * t
        envelope = (1 - np.exp(-t * 90)) * np.exp(-decay * t)
        tone = np.sin(phase) + .23 * np.sin(phase * 2) + .09 * np.sin(phase * 3.01)
        samples[start:start + count] += volume * envelope * tone * np.minimum(1, (count / rate - t) * 20)

    chords = [(50, 57, 66, 69), (47, 54, 62, 66), (43, 55, 62, 71), (45, 57, 64, 69)]
    for bar, at in enumerate(np.arange(0, seconds - 1, 2.4)):
        bass, middle, high, top = chords[bar % len(chords)]
        note(at, bass, 2.3, .052, 1.35)
        for offset, key in [(0, high), (.8, middle), (1.6, top)]:
            note(at + offset, key, 1.8, .041, 2.25)
        if bar >= 16:
            note(at + 1.2, high + 12, 1.25, .024, 3)
    fade = min(3 * rate, len(samples))
    samples[-fade:] *= np.linspace(1, 0, fade)
    with wave.open(str(path), "wb") as stream:
        stream.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        stream.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
