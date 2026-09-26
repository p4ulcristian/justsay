import numpy as np
import pytest

from iris_dictation.daemon import is_silence_phrase, loudest_rms, tidy_short


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
