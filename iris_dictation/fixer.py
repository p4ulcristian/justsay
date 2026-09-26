"""A second pass over what Whisper wrote, by a small local language model:
fix words it misheard from the user's vocabulary, drop filler sounds, and
keep only the corrected version when the speaker corrects themselves
("Monday, no wait, Tuesday").

A guardrail checks every answer against what Whisper wrote and throws it away
if the model changed anything else, so the worst case is Whisper's own text.
The model runs behind any OpenAI-compatible endpoint (Ollama, llama.cpp,
LM Studio, ...).
"""

from __future__ import annotations

import difflib
import json
import logging
import re
import threading
import tomllib
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("iris-dictation")

FILLERS = {"um", "umm", "uh", "uhh", "er", "erm", "hmm", "mm", "ah", "eh",
           "öh", "öö", "ööö", "őő"}
# Words a speaker uses to correct themselves. Only with one of these in the
# text may the model drop real words.
CUES = ("no wait", "wait no", "i mean", "sorry", "actually", "or rather", "scratch that",
        "vagyis", "azaz", "bocs", "nem is")

SYSTEM = "\n".join([
    "You clean up text from a speech recogniser. Return the same text with only "
    "these changes:",
    "1. A word the recogniser misheard that is clearly one of the known words: "
    "write the known word instead, spelled exactly as listed.",
    "2. Remove filler sounds: um, uh, er, hmm, öö.",
    '3. When the speaker corrects themselves ("no wait", "I mean", "sorry", '
    '"actually"), keep only the corrected version.',
    "Change nothing else. Keep the language, the words, their order and the "
    "punctuation. Never translate, never answer, never add words. If nothing needs "
    "fixing, return the text unchanged. Output only the text.",
])

# Worked examples, with made-up known words. Real vocabulary is private.
EXAMPLES = [
    ("Zorbex", "I pushed it to the zor bex repo.", "I pushed it to the Zorbex repo."),
    ("", "Let's meet on Monday, uh, no wait, Tuesday.", "Let's meet on Tuesday."),
    ("", "Bring the red, I mean the blue cable.", "Bring the blue cable."),
    ("", "Can you, um, check the logs?", "Can you check the logs?"),
    ("Quillo, Marvina", "Tell Marvinna the Kilo build is done.",
     "Tell Marvina the Quillo build is done."),
    ("Zorbex", "Öö, holnap megnézem a zorbeksz hibát.", "Holnap megnézem a Zorbex hibát."),
    ("Zorbex", "What time is it?", "What time is it?"),
]


@dataclass
class Vocabulary:
    """The user's own words. Private: it lives in their config directory and
    never in this repository."""
    words: list[str] = field(default_factory=list)
    heard: dict[str, str] = field(default_factory=dict)   # what Whisper writes -> meant

    def known(self) -> list[str]:
        return list(dict.fromkeys(self.words + list(self.heard.values())))


class VocabularyFile:
    """Reads the vocabulary file again whenever it changes, so an edit takes
    effect on the next dictation without a restart."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._mtime: float | None = None
        self._vocab = Vocabulary()

    def get(self) -> Vocabulary:
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            return Vocabulary()
        if mtime != self._mtime:
            try:
                with self.path.open("rb") as fh:
                    data = tomllib.load(fh)
                self._vocab = Vocabulary(
                    words=[str(w) for w in data.get("words", [])],
                    heard={str(k): str(v) for k, v in data.get("heard", {}).items()},
                )
            except (OSError, tomllib.TOMLDecodeError) as exc:
                log.warning("%s: %s; keeping the previous vocabulary", self.path, exc)
            self._mtime = mtime
        return self._vocab


def _words(text: str) -> list[str]:
    """Lowercase words without the punctuation around them; case and
    punctuation changes are not what the guardrail judges."""
    return [w for w in (t.strip(".,!?;:\"'()…").lower() for t in text.split()) if w]


def _squash(text: str) -> str:
    return re.sub(r"[\s\-_]", "", text.lower())


def allowed(before: str, after: str, known: list[str]) -> bool:
    """Whether `after` differs from `before` only in the ways the model may
    change it: known words for similar-sounding ones, fillers dropped, and,
    after a correction cue, words dropped."""
    a, b = _words(before), _words(after)
    if not b:
        return not a or all(w in FILLERS for w in a)
    cue = any(c in " ".join(a) for c in CUES)
    known_squashed = {_squash(k) for k in known}
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        old, new = " ".join(a[i1:i2]), " ".join(b[j1:j2])
        if tag == "delete":
            if cue or all(w in FILLERS for w in a[i1:i2]):
                continue
            return False
        if tag == "insert":
            return False
        # replace: a known word for one that sounds like it, or, after a cue,
        # dropping the words being corrected (what is left was said anyway).
        kept = [w for w in a[i1:i2] if w not in FILLERS]
        if _squash(new) in known_squashed and \
                difflib.SequenceMatcher(None, _squash(" ".join(kept)), _squash(new)).ratio() >= 0.5:
            continue
        if cue and set(b[j1:j2]) <= set(a[i1:i2]):
            continue
        log.debug("fix changed %r -> %r: not allowed", old, new)
        return False
    return True


def respell(before: str, after: str, known: list[str]) -> str:
    """Give each word the model brought in the exact spelling from the
    vocabulary ("github" -> "GitHub"). Words that were already in `before`
    keep theirs."""
    spelled = {_squash(k): k for k in known if " " not in k}
    said = set(_words(before))
    out = []
    for token in after.split():
        core = token.strip(".,!?;:\"'()…")
        if core and core.lower() not in said and _squash(core) in spelled:
            token = token.replace(core, spelled[_squash(core)])
        out.append(token)
    return " ".join(out)


def worth_asking(text: str, vocab: Vocabulary) -> bool:
    """Skip the model when it could not change anything anyway."""
    words = _words(text)
    return bool(vocab.known()) or any(w in FILLERS for w in words) or \
        any(c in " ".join(words) for c in CUES)


def messages(text: str, vocab: Vocabulary) -> list[dict]:
    def ask(known: str, said: str) -> dict:
        return {"role": "user", "content": f"Known words: {known or '(none)'}\nText: {said}"}

    msgs = [{"role": "system", "content": SYSTEM}]
    for known, said, fixed in EXAMPLES:
        msgs += [ask(known, said), {"role": "assistant", "content": fixed}]
    for heard, meant in vocab.heard.items():
        msgs += [ask(meant, heard), {"role": "assistant", "content": meant}]
    msgs.append(ask(", ".join(vocab.known()), text))
    return msgs


class Fixer:
    def __init__(self, url: str, model: str, timeout: float, vocabulary: VocabularyFile) -> None:
        self.url = url.rstrip("/") + "/chat/completions"
        self.model = model
        self.timeout = timeout
        self.vocabulary = vocabulary

    def _ask(self, msgs: list[dict], max_tokens: int) -> str:
        body = json.dumps({
            "model": self.model,
            "messages": msgs,
            "temperature": 0,
            "max_tokens": max_tokens,
            "reasoning_effort": "none",   # small Qwen models think otherwise, slowly
        }).encode()
        req = urllib.request.Request(self.url, body, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.load(resp)["choices"][0]["message"]["content"] or ""

    def warm(self) -> None:
        """Load the model in the background while the user is still talking,
        so a model that was unloaded costs nothing extra on release."""
        def run() -> None:
            try:
                self._ask([{"role": "user", "content": "ok"}], 1)
            except Exception as exc:
                log.debug("fix model warm-up failed: %s", exc)
        threading.Thread(target=run, daemon=True).start()

    def fix(self, text: str) -> str:
        """The fixed text, or `text` itself when there was nothing to fix,
        the model was unavailable, or its answer broke the rules."""
        vocab = self.vocabulary.get()
        if not text or not worth_asking(text, vocab):
            return text
        try:
            # Room for the text plus a little; a runaway answer is cut short
            # and then fails the guardrail.
            fixed = self._ask(messages(text, vocab), 2 * len(text.split()) + 20).strip()
        except Exception as exc:
            log.warning("fix model failed (%s), typing Whisper's text", exc)
            return text
        if fixed == text:
            return text
        if not allowed(text, fixed, vocab.known()):
            log.info("fix rejected: %r -> %r", text, fixed)
            return text
        return respell(text, fixed, vocab.known())
