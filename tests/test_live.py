"""End to end through the running daemon: the real model on the sample clips.
Skipped when the daemon is not running. Nothing is typed."""

import re
from pathlib import Path

import pytest

from iris_dictation import ctl

CLIPS = Path(__file__).parent.parent / "testwav"
# Words that must come through (see testwav/SOURCES.md).
EXPECTED = {
    "16k_fleurs_en_0.wav": ["thousands", "miles", "satellite"],
    "16k_fleurs_en_2.wav": ["fund", "global warming"],
    "16k_piper_hu.wav": ["teszt", "magyarul"],
}


def daemon_running() -> bool:
    try:
        return ctl.send("ping", timeout=2) == "pong"
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not daemon_running(), reason="daemon not running")


@pytest.mark.parametrize("clip", sorted(EXPECTED))
def test_clip(clip):
    text = ctl.send(f"transcribe {CLIPS / clip}").lower()
    for word in EXPECTED[clip]:
        assert re.search(word, text), f"{word!r} not in {text!r}"


def test_missing_file_does_not_kill_the_daemon():
    assert ctl.send("transcribe /nonexistent.wav") == ""
    assert ctl.send("ping") == "pong"
