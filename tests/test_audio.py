import subprocess

from iris_dictation import audio

SOURCES = "1\tphone_mic\tPipeWire\ts16le 1ch 16000Hz\tSUSPENDED\n2\theadset_mic\tPipeWire\ts16le 1ch 48000Hz\tIDLE\n"


def fake_pactl(monkeypatch, out):
    monkeypatch.setattr(audio.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=out, stderr=""))


def test_a_single_source_is_used_as_is():
    assert audio.pick_source("headset_mic") == "headset_mic"
    assert audio.pick_source("") == ""
    assert audio.pick_source(["headset_mic"]) == "headset_mic"


def test_a_list_takes_the_first_source_that_exists(monkeypatch):
    fake_pactl(monkeypatch, SOURCES)
    assert audio.pick_source(["phone_mic", "headset_mic"]) == "phone_mic"
    fake_pactl(monkeypatch, SOURCES.split("\n", 1)[1])
    assert audio.pick_source(["phone_mic", "headset_mic"]) == "headset_mic"


def test_a_list_with_none_there_means_the_default_input(monkeypatch):
    fake_pactl(monkeypatch, SOURCES)
    assert audio.pick_source(["gone", "also_gone"]) == ""
