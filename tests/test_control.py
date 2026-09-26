"""The control socket protocol. Other programs (omarchy-controller, scripts)
depend on these replies, so they must not change."""

import queue
import threading

from iris_dictation.sockets import ControlServer


def server(state="idle"):
    events = queue.Queue()
    return ControlServer("/unused", events, lambda: state), events


def test_start_stop():
    ctl, events = server()
    assert ctl.handle("start") == "ok"
    assert ctl.handle("stop") == "ok"
    assert [events.get_nowait(), events.get_nowait()] == [("down",), ("up",)]


def test_toggle():
    ctl, events = server("idle")
    ctl.handle("toggle")
    assert events.get_nowait() == ("down",)
    ctl, events = server("recording")
    ctl.handle("toggle")
    assert events.get_nowait() == ("up",)


def test_status_ping_quit_unknown():
    ctl, events = server("transcribing")
    assert ctl.handle("status") == "transcribing"
    assert ctl.handle("ping") == "pong"
    assert ctl.handle("quit") == "ok"
    assert events.get_nowait() == ("quit",)
    assert ctl.handle("dance") == "unknown command: dance"


def answer(events, text):
    """Play the daemon: take the next event and reply into its box."""
    def run():
        event = events.get(timeout=5)
        event[-1].put(text)
    t = threading.Thread(target=run)
    t.start()
    return t


def test_stop_return_replies_with_text():
    ctl, events = server()
    t = answer(events, "hello there")
    assert ctl.handle("stop-return") == "hello there"
    t.join()


def test_transcribe_replies_with_text():
    ctl, events = server()
    t = answer(events, "a satellite")
    assert ctl.handle("transcribe /tmp/x.wav") == "a satellite"
    t.join()
