"""Format mode: turn a dictated phrase into the exact string it names, like a
URL, email address, path, command or code identifier.

Fixed rules do what they can do reliably (case styles, spoken symbols,
commands with flags). Whatever is left goes to a small language model behind
an OpenAI-compatible endpoint (Ollama, llama.cpp, LM Studio, ...), together
with the user's private vocabulary.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import tomllib
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("iris-dictation")

STYLES = {
    "camel case": lambda w: w[0].lower() + "".join(x.capitalize() for x in w[1:]),
    "pascal case": lambda w: "".join(x.capitalize() for x in w),
    "snake case": lambda w: "_".join(x.lower() for x in w),
    "kebab case": lambda w: "-".join(x.lower() for x in w),
    "constant": lambda w: "_".join(x.upper() for x in w),
}

# Spoken symbols. Order matters: longer phrases first.
SYMBOLS = [
    (r"\b(?:dash dash|double dash) ", "--"),
    (r"\b(?:dash|hyphen) ", "-"),
    (r"\s*\bdot\b\s*", "."),
    (r"\s*\bslash\b\s*", "/"),
    (r"\s*\bcolon\b\s*", ":"),
    (r"\s*\bunderscore\b\s*", "_"),
    (r"\s+\bat\b\s+(?=\S+\.)", "@"),       # only in front of something.with.dots
]

NUMBERS = {w: str(i) for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve".split())}

SYSTEM = "\n".join([
    "You turn dictated speech into the exact string the speaker means to type: a URL, "
    "domain, email address, file path, command, or code identifier.",
    "Rules:",
    "- Output only that string. No quotes, no explanation, no full stop.",
    '- Words that describe the kind of thing ("URL", "web page", "link", "website", '
    '"method", "function", "variable", "file", "path", "command") are hints, not part '
    "of the string.",
    "- A web page or URL gets https:// in front. A bare domain stays bare.",
    "- A method, function or variable is camel case.",
    '- Keep every word of an identifier, including small ones like "by", "is", "to", "of".',
    "- The symbols . / : @ - _ are already in place: keep every one of them.",
    "- Join words that the speech recogniser split apart when they form one name.",
    "- Prefer the spellings in the known words list.",
])

# Worked examples for the model, in spoken form. They go through the same
# rules as the input, so the model sees both in the same shape.
EXAMPLES = [
    ("example dot org web page URL", "https://example.org"),
    ("method fetch user profile", "fetchUserProfile"),
    ("home slash docs slash notes dot md", "~/docs/notes.md"),
    ("method convert to string", "convertToString"),
    ("variable has errors", "hasErrors"),
    ("my shop dot net slash page two", "myshop.net/page2"),
]


@dataclass
class Vocabulary:
    """The user's own words. Private: it lives in their config directory and
    never in this repository."""
    words: list[str] = field(default_factory=list)
    heard: dict[str, str] = field(default_factory=dict)   # what is said -> what to write


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


def _plain(text: str) -> str:
    return " ".join(re.findall(r"\w+", text.lower()))


def _numbers(text: str) -> str:
    # "three thousand" -> 3000; a number word right after a symbol -> digit.
    text = re.sub(r"\b(\w+) thousand\b",
                  lambda m: NUMBERS[m[1].lower()] + "000" if m[1].lower() in NUMBERS else m[0],
                  text, flags=re.I)
    return re.sub(r"([/:.])(\w+)\b", lambda m: m[1] + NUMBERS.get(m[2].lower(), m[2]), text)


def rules(text: str) -> tuple[str, bool]:
    """Apply the fixed rules. Returns (text, done): done means the rules
    settled it and the model is not needed."""
    t = text.strip().rstrip(".!?").strip()
    low = t.lower()
    for name, style in STYLES.items():
        if low.startswith(name + " "):
            words = re.findall(r"[A-Za-z0-9]+", t[len(name):])
            if words:
                return style(words), True
    for pattern, replacement in SYMBOLS:
        t = re.sub(pattern, replacement, t, flags=re.I)
    t = _numbers(t)
    # A command with flags is already right; the model would only glue it together.
    return t, bool(re.search(r"(^|\s)--?\w", t))


def clean(reply: str) -> str:
    """The model's answer as a bare string: first line, no quotes or fences."""
    reply = reply.strip().strip("`").strip()
    reply = reply.splitlines()[0] if reply else ""
    return reply.strip().rstrip(".").strip().strip("\"'`").rstrip(".").strip()


def messages(text: str, vocab: Vocabulary) -> list[dict]:
    system = SYSTEM
    if vocab.words or vocab.heard:
        known = dict.fromkeys(vocab.words + list(vocab.heard.values()))
        system += "\nKnown words: " + ", ".join(known)
    msgs = [{"role": "system", "content": system}]
    for heard, out in EXAMPLES + list(vocab.heard.items()):
        shaped, done = rules(heard)
        if not done:
            msgs += [{"role": "user", "content": shaped}, {"role": "assistant", "content": out}]
    msgs.append({"role": "user", "content": text})
    return msgs


class Formatter:
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
                log.debug("format model warm-up failed: %s", exc)
        threading.Thread(target=run, daemon=True).start()

    def format(self, text: str) -> str:
        vocab = self.vocabulary.get()
        said = _plain(text)
        for heard, write in vocab.heard.items():
            if _plain(heard) == said:
                return write
        shaped, done = rules(text)
        if done or not shaped:
            return shaped
        try:
            return clean(self._ask(messages(shaped, vocab), 80)) or shaped
        except Exception as exc:
            # No model (not installed, not running): the rules' version is
            # still better than the raw sentence.
            log.warning("format model failed (%s), using the rules only", exc)
            return shaped
