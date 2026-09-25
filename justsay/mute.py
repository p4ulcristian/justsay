"""Mute other apps' microphone streams while dictating.

So the people in a Discord call do not hear what you dictate. This mutes the
app's capture stream in PipeWire, not the microphone itself, so justsay still
records. The app's own mute icon does not change; it just receives silence.

Only streams this module muted are unmuted again, so a stream you had muted
yourself stays muted.
"""

from __future__ import annotations

import json
import logging
import subprocess

log = logging.getLogger("justsay")


def _pactl(*args: str) -> str:
    return subprocess.run(
        ["pactl", *args], capture_output=True, text=True, timeout=2, check=True
    ).stdout


class StreamMuter:
    def __init__(self, apps: list[str]) -> None:
        self.apps = [a.lower() for a in apps]
        self.muted: list[str] = []

    def _matches(self, props: dict) -> bool:
        names = (
            props.get("application.name", ""),
            props.get("application.process.binary", ""),
        )
        return any(app in name.lower() for app in self.apps for name in names)

    def mute(self) -> None:
        if not self.apps:
            return
        try:
            streams = json.loads(_pactl("-f", "json", "list", "source-outputs"))
        except Exception as exc:
            log.warning("could not list capture streams: %s", exc)
            return
        for s in streams:
            if s.get("mute") or not self._matches(s.get("properties", {})):
                continue
            idx = str(s["index"])
            try:
                _pactl("set-source-output-mute", idx, "1")
            except Exception as exc:
                log.warning("could not mute stream %s: %s", idx, exc)
                continue
            self.muted.append(idx)
            log.info("muted %s (stream %s)",
                     s["properties"].get("application.name"), idx)

    def restore(self) -> None:
        for idx in self.muted:
            try:
                _pactl("set-source-output-mute", idx, "0")
            except Exception:
                # The stream ended while muted (left the call). Nothing to undo.
                pass
        self.muted = []
