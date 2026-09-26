"""Configuration for the iris-dictation daemon.

Everything is overridable from ~/.config/iris-dictation/config.toml.
"""

from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

log = logging.getLogger("iris-dictation")

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "iris-dictation"
CONFIG_PATH = CONFIG_DIR / "config.toml"
VOCABULARY_PATH = CONFIG_DIR / "vocabulary.toml"


def runtime_dir() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    d = Path(base) / "iris-dictation"
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class Config:
    # The model is Whisper large-v3 in fp16 on an NVIDIA GPU: about 0.2-0.4 s
    # per clip and ~5 GB of VRAM while the daemon runs. This is the directory
    # holding its ONNX files, filled by bin/iris-dictation-fetch-model. (A
    # plain directory, because onnxruntime will not follow the Hugging Face
    # cache's symlinks to the weights.)
    model_path: str = "~/.local/share/iris-dictation/models/whisper-large-v3"
    # The languages Whisper may pick from, as Whisper codes, e.g. ["hu", "en"].
    # It detects which one you are speaking, but only among these, which
    # stops short clips being read as a third language. One entry forces that
    # language. Empty means any of Whisper's 99.
    languages: list[str] = field(default_factory=list)

    # Key to hold, as an evdev KEY_* name.
    key: str = "KEY_CAPSLOCK"
    # Restrict to these evdev device paths; empty means every keyboard that
    # reports the key.
    devices: list[str] = field(default_factory=list)

    # Audio capture. Empty source means the PipeWire default input.
    # List the alternatives with: pactl list sources short
    audio_source: str = ""
    sample_rate: int = 16000
    # Milliseconds of audio to keep from before the key went down. Any value
    # above 0 keeps the microphone open for as long as the daemon runs, which
    # removes the ~155 ms it otherwise takes parec to start streaming. 0
    # means the microphone is only opened while the key is held.
    preroll_ms: int = 0
    max_seconds: int = 120
    min_seconds: float = 0.35
    # Whisper turns silence into "Thank you." and similar. Such a phrase is
    # dropped when the clip's loudest 30 ms also stays under this RMS; every
    # other clip is transcribed, however quiet. Headset mic: a silent hold
    # peaks around 0.002, a quiet short word around 0.003. 0 = off.
    silence_rms: float = 0.005
    # Results of at most this many words (a launcher search, one word into a
    # field) lose their trailing punctuation and start lowercase: "Firefox."
    # becomes "firefox". Acronyms like "USB" keep their case. 0 = off.
    short_words: int = 3

    # "type"      -> wtype the characters directly. Works everywhere, about
    #                220 chars/sec. This is the default.
    # "paste"     -> wl-copy then send the paste shortcut for the focused
    #                window (ctrl+shift+v in terminals, ctrl+v elsewhere).
    #                Instant for long text, but borrows the clipboard.
    # "clipboard" -> only put it on the clipboard, paste it yourself.
    output: str = "type"
    # Fall back to the other injection method if the first one fails.
    output_fallback: bool = True
    # Trailing space after the pasted text.
    trailing_space: bool = True

    notify: bool = True

    # Mute these apps' microphone streams while the key is held, so a voice
    # call does not hear the dictation. Matched case-insensitively against
    # the stream's application name and binary. Discord as a browser web app
    # records as the browser ("chromium"), so add that to catch it; it then
    # also mutes other tabs using the mic while you dictate. Empty list
    # turns this off.
    mute_apps: list[str] = field(
        default_factory=lambda: ["discord", "vesktop", "webcord"]
    )


def load() -> Config:
    cfg = Config()
    if not CONFIG_PATH.exists():
        return cfg
    with CONFIG_PATH.open("rb") as fh:
        data = tomllib.load(fh)
    known = {f.name for f in fields(Config)}
    for key, value in data.items():
        if key in known:
            setattr(cfg, key, value)
        else:
            log.warning("%s: unknown setting %r, ignored", CONFIG_PATH, key)
    return cfg
