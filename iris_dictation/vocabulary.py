"""The user's vocabulary: phrases Whisper keeps getting wrong, and what was
meant. Private: it lives in ~/.config/iris-dictation/vocabulary.toml, never
in this repository.

    [heard]
    "Zorbax" = "Zorbex"
"""

from __future__ import annotations

import logging
import re
import tomllib
from pathlib import Path

log = logging.getLogger("iris-dictation")


class VocabularyFile:
    """Reads the [heard] table again whenever the file changes, so an edit
    takes effect on the next dictation without a restart."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._mtime: float | None = None
        self._heard: dict[str, str] = {}

    def heard(self) -> dict[str, str]:
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            return {}
        if mtime != self._mtime:
            try:
                with self.path.open("rb") as fh:
                    data = tomllib.load(fh)
                self._heard = {str(k): str(v) for k, v in data.get("heard", {}).items()}
            except (OSError, tomllib.TOMLDecodeError) as exc:
                log.warning("%s: %s; keeping the previous vocabulary", self.path, exc)
            self._mtime = mtime
        return self._heard


def replace_heard(text: str, heard: dict[str, str]) -> str:
    """Replace each taught phrase with what was meant: whole words, any case,
    every time."""
    for said in sorted(heard, key=len, reverse=True):
        meant = heard[said]

        def swap(m: re.Match, meant: str = meant) -> str:
            # A phrase at the start of a sentence keeps its capital letter.
            start = m.start() == 0 or m.string[:m.start()].rstrip()[-1:] in ".!?"
            if start and " " in meant and m[0][:1].isupper():
                return meant[:1].upper() + meant[1:]
            return meant
        text = re.sub(rf"(?<!\w){re.escape(said)}(?!\w)", swap, text, flags=re.I)
    return text
