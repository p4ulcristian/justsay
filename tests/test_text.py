import numpy as np
import pytest

from iris_dictation.audio import level, loudest_rms, split_at_pauses, to_float
from iris_dictation.text import is_silence_phrase, tidy_short


@pytest.mark.parametrize("text", [
    "Thank you.", "thanks", " Bye! ", "Köszönöm szépen.", "Feliratok: valaki",
])
def test_silence_phrases(text):
    assert is_silence_phrase(text)


@pytest.mark.parametrize("text", ["", "Thank you for the help.", "Hello there."])
def test_not_silence_phrases(text):
    assert not is_silence_phrase(text)


@pytest.mark.parametrize("text, expected", [
    ("Firefox.", "firefox"),
    ("YouTube Music!", "youtube Music"),
    ("USB drive.", "USB drive"),
    ("I.", "i"),
    ("", ""),
    ("One two three four.", "One two three four."),
])
def test_tidy_short(text, expected):
    assert tidy_short(text, 3) == expected


def test_tidy_short_off():
    assert tidy_short("Firefox.", 0) == "Firefox."


def test_loudest_rms_finds_the_loud_window():
    samples = np.zeros(16000, dtype=np.float32)
    samples[9600:10080] = 0.5   # one whole 30 ms window
    assert loudest_rms(samples) == pytest.approx(0.5)


def test_loudest_rms_short_clip():
    assert loudest_rms(np.zeros(100, dtype=np.float32)) == 0.0


def test_to_float_drops_an_odd_byte():
    assert list(to_float(b"\x00\x40\x00\xc0\x01")) == [0.5, -0.5]


def test_level_scale():
    def chunk(amplitude):
        return (np.full(320, amplitude * 32767, dtype=np.int16)).tobytes()
    assert level(b"") == 0.0
    assert level(chunk(0.001)) == 0.0     # about -60 dB: silence
    assert level(chunk(0.5)) == 1.0       # loud
    assert 0 < level(chunk(0.005)) < 1    # about -46 dB: in between


def test_short_recording_is_one_piece():
    samples = np.ones(16000 * 20, dtype=np.float32)
    assert len(split_at_pauses(samples, 16000)) == 1


def test_long_recording_is_cut_at_the_pause():
    rate = 16000
    samples = np.full(rate * 70, 0.3, dtype=np.float32)
    samples[rate * 25: rate * 25 + rate // 2] = 0.0          # a pause at 25 s
    pieces = split_at_pauses(samples, rate)
    assert all(len(p) <= 28 * rate for p in pieces)
    assert abs(len(pieces[0]) / rate - 25.25) < 0.3            # cut inside the pause
    assert sum(len(p) for p in pieces) == len(samples)          # nothing lost
