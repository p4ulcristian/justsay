import json

from iris_dictation import mute

STREAMS = [
    {"index": 1, "mute": False, "properties": {"application.name": "Chromium"}},
    {"index": 2, "mute": True, "properties": {"application.name": "Discord"}},
    {"index": 3, "mute": False, "properties": {"application.process.binary": "discord"}},
    {"index": 4, "mute": False, "properties": {"application.name": "OBS"}},
]


def fake_pactl(calls):
    def run(*args):
        calls.append(args)
        if args[:3] == ("-f", "json", "list"):
            return json.dumps(STREAMS)
        return ""
    return run


def test_mutes_matching_streams_and_restores_only_those(monkeypatch):
    calls = []
    monkeypatch.setattr(mute, "_pactl", fake_pactl(calls))
    m = mute.StreamMuter(["discord", "chromium"])
    m.mute()
    # Stream 2 was already muted by the user, so it is left alone.
    assert m.muted == ["1", "3"]
    calls.clear()
    m.restore()
    assert calls == [("set-source-output-mute", "1", "0"),
                     ("set-source-output-mute", "3", "0")]
    assert m.muted == []


def test_empty_list_does_nothing(monkeypatch):
    calls = []
    monkeypatch.setattr(mute, "_pactl", fake_pactl(calls))
    mute.StreamMuter([]).mute()
    assert calls == []
