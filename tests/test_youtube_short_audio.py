import math
import struct
import sys
import wave
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from classes.YouTube import YouTube


def _write_wav(path, frames, *, channels=1, sample_width=2, rate=8000):
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(sample_width)
        wav.setframerate(rate)
        wav.writeframes(frames)


@pytest.mark.parametrize("channels, audible_channel", [(1, 0), (2, 0), (2, 1)])
def test_speech_end_seconds_finds_tone_end_before_trailing_silence(
    tmp_path, channels, audible_channel
):
    rate = 8000
    tone_frames = rate // 2
    tone = [
        round(8192 * math.sin(2 * math.pi * 440 * frame / rate))
        for frame in range(tone_frames)
    ]
    samples = [
        sample if channel == audible_channel else 0
        for sample in tone + [0] * (rate // 2)
        for channel in range(channels)
    ]
    wav_path = tmp_path / "tone_and_silence.wav"
    _write_wav(wav_path, struct.pack(f"<{len(samples)}h", *samples), channels=channels)

    assert YouTube._speech_end_seconds(str(wav_path)) == pytest.approx(
        tone_frames / rate, abs=2 / rate
    )


@pytest.mark.parametrize("channels", [1, 2])
def test_speech_end_seconds_returns_none_for_all_silence(tmp_path, channels):
    wav_path = tmp_path / "silence.wav"
    _write_wav(wav_path, b"\x00\x00" * 8000 * channels, channels=channels)

    assert YouTube._speech_end_seconds(str(wav_path)) is None


def test_speech_end_seconds_returns_none_for_non_wav_file(tmp_path):
    wav_path = tmp_path / "mp3_named_as_wav.wav"
    wav_path.write_bytes(b"ID3\x04\x00\x00" + b"not a PCM WAV" * 10)

    assert YouTube._speech_end_seconds(str(wav_path)) is None


def test_speech_end_seconds_returns_none_for_missing_file(tmp_path):
    assert YouTube._speech_end_seconds(str(tmp_path / "missing.wav")) is None


@pytest.mark.parametrize("sample_width", [1, 3, 4])
def test_speech_end_seconds_returns_none_for_non_16_bit_pcm(tmp_path, sample_width):
    wav_path = tmp_path / "unsupported_pcm.wav"
    _write_wav(wav_path, b"\x01" * sample_width * 800, sample_width=sample_width)

    assert YouTube._speech_end_seconds(str(wav_path)) is None


def test_speech_end_seconds_returns_none_for_empty_wav(tmp_path):
    wav_path = tmp_path / "empty.wav"
    _write_wav(wav_path, b"")

    assert YouTube._speech_end_seconds(str(wav_path)) is None


def test_speech_end_seconds_handles_negative_full_scale_sample(tmp_path):
    wav_path = tmp_path / "negative_full_scale.wav"
    _write_wav(wav_path, struct.pack("<4h", 0, -32768, 0, 0))

    assert YouTube._speech_end_seconds(str(wav_path)) == pytest.approx(2 / 8000)


@pytest.mark.parametrize(
    "channels, missing_bytes",
    [(1, 1), (2, 1), (2, 2)],
    ids=["mono-partial-sample", "stereo-partial-sample", "stereo-partial-frame"],
)
def test_speech_end_seconds_returns_none_for_truncated_pcm_sample(
    tmp_path, channels, missing_bytes
):
    wav_path = tmp_path / "truncated.wav"
    _write_wav(wav_path, struct.pack("<4h", 0, 1000, 0, 0), channels=channels)
    # Keep the valid header but leave an incomplete sample or stereo frame.
    wav_path.write_bytes(wav_path.read_bytes()[:-missing_bytes])

    assert YouTube._speech_end_seconds(str(wav_path)) is None
