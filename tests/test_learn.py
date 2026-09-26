"""iris-dictation-learn without an agent: reading the journal and writing
the vocabulary file. Made-up words only."""

import tomllib

import pytest

from iris_dictation import learn

JOURNAL = """\
2026-01-02T10:32:45+01:00 host iris-dictation-daemon[1]: 10:32:45 INFO 1.7s audio (peak rms 0.0398) -> 0.10s infer (rtf 0.058): 'Zorbax is down'
2026-01-02T10:32:45+01:00 host iris-dictation-daemon[1]: 10:32:45 INFO fixed -> 'Zorbex is down' (0.12s)
2026-01-02T10:32:49+01:00 host iris-dictation-daemon[1]: 10:32:49 INFO watching Some Keyboard (/dev/input/event4)
2026-01-02T10:32:52+01:00 host iris-dictation-daemon[1]: 10:32:52 INFO 1.1s audio (peak rms 0.0334) -> 0.10s infer (rtf 0.091): "It's Quilo, isn't it?"
2026-01-02T10:32:53+01:00 host iris-dictation-daemon[1]: 10:32:53 INFO 0.5s audio (peak rms 0.0010) -> 0.10s infer (rtf 0.091): ''
"""


def test_read_log(monkeypatch):
    class Done:
        stdout = JOURNAL
    monkeypatch.setattr(learn.subprocess, "run", lambda *a, **k: Done())
    assert learn.read_log("-1h") == [
        {"time": "2026-01-02T10:32:45+01:00", "text": "Zorbax is down", "typed": "Zorbex is down"},
        {"time": "2026-01-02T10:32:52+01:00", "text": "It's Quilo, isn't it?", "typed": None},
    ]


EXISTING = """\
# my words

[heard]
"Zorbax" = "Zorbex"
"""


@pytest.mark.parametrize("start", [EXISTING, "", "[heard]\n", "[heard]\n\n[other]\nx = 1\n"])
def test_add_to_vocabulary(tmp_path, start):
    path = tmp_path / "vocabulary.toml"
    path.write_text(start)
    learn.add_to_vocabulary(path, {"quilo": "Quillo", 'say "hi"': "Say hi"})
    data = tomllib.loads(path.read_text())
    assert data["heard"]["quilo"] == "Quillo"
    assert data["heard"]['say "hi"'] == "Say hi"
    if start == EXISTING:
        assert path.read_text().startswith("# my words\n")      # comments kept
        assert data["heard"]["Zorbax"] == "Zorbex"


def test_add_to_vocabulary_skips_what_is_there(tmp_path):
    path = tmp_path / "vocabulary.toml"
    path.write_text(EXISTING)
    learn.add_to_vocabulary(path, {"zorbax": "Other", "quilo": "Quillo"})
    assert tomllib.loads(path.read_text())["heard"] == {"Zorbax": "Zorbex", "quilo": "Quillo"}
