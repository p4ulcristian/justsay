"""iris-dictation-learn: have an AI agent read your recent dictations, spot
what Whisper got wrong, and propose vocabulary entries. You say yes or no to
each; the accepted ones are written to your vocabulary and then checked
against the dictations they came from.

The agent only answers: it gets no tools and cannot change anything. By
default it is Claude Code (`claude -p`), so the reviewed dictations are sent
to Anthropic. Set IRIS_DICTATION_AGENT to any command that reads the prompt
on stdin and prints the JSON answer to use something else, e.g. a local model.
"""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

from . import config as cfgmod
from . import ctl

STATE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) \
    / "iris-dictation" / "learned-until"

SCHEMA = {
    "type": "object",
    "properties": {
        "heard": {"type": "array", "items": {"type": "object", "properties": {
            "said": {"type": "string"}, "meant": {"type": "string"},
            "example": {"type": "string"}, "why": {"type": "string"}},
            "required": ["said", "meant", "example", "why"]}},
    },
    "required": ["heard"],
}

PROMPT = """You review a speech-to-text log to improve its vocabulary. The speaker dictates with
Whisper in English and Hungarian. Each line is a time, then what Whisper wrote, and after
"=>" what was typed when the vocabulary changed it.

Find mistakes Whisper makes repeatedly or clearly:
- The same thing said again within about 20 seconds with a different spelling: the speaker
  was correcting a mishearing. The later, correct version shows what was meant.
- Names, projects, products or domains written wrongly, especially ones in the vocabulary.

Propose "heard" entries: a phrase exactly as Whisper wrote it (copy it from the log, it is
matched as whole words in any case) -> what was meant. It replaces that phrase in every future
dictation, so only propose phrases that would never be meant literally. Give each proposal the
log text it came from as "example" and a short "why".

Do not propose ordinary words, rewordings, grammar or punctuation. Do not repeat what is
already in the vocabulary. Proposing nothing is a fine answer.

Current vocabulary:
{vocabulary}

Log:
{log}
"""

LINE = re.compile(r"^(\S+) \S+ \S+\[\d+\]: \S+ INFO "
                  r"(?:[\d.]+s audio .*?: (.+)|fixed -> (.+) \([\d.]+s\))$")


def read_log(since: str) -> list[dict]:
    """Dictations from the journal: [{time, text, typed}]."""
    out = subprocess.run(
        ["journalctl", "--user", "-u", "iris-dictation", "--since", since,
         "-o", "short-iso", "--no-pager"],
        capture_output=True, text=True, check=False).stdout
    entries: list[dict] = []
    for line in out.splitlines():
        m = LINE.match(line)
        if not m:
            continue
        try:
            if m[2] is not None:
                text = ast.literal_eval(m[2])
                if text:
                    entries.append({"time": m[1], "text": text, "typed": None})
            elif entries:
                entries[-1]["typed"] = ast.literal_eval(m[3])
        except (ValueError, SyntaxError):
            continue
    return entries


def ask_agent(prompt: str) -> dict:
    custom = os.environ.get("IRIS_DICTATION_AGENT")
    if custom:
        out = subprocess.run(shlex.split(custom), input=prompt, capture_output=True,
                             text=True, check=True).stdout
        return json.loads(out)
    if not shutil.which("claude"):
        sys.exit("claude (Claude Code) not found; set IRIS_DICTATION_AGENT to another agent")
    out = subprocess.run(
        ["claude", "-p", "--tools", "", "--strict-mcp-config", "--no-session-persistence",
         "--output-format", "json", "--json-schema", json.dumps(SCHEMA)],
        input=prompt, capture_output=True, text=True, check=True).stdout
    return json.loads(out)["structured_output"]


def _toml(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def add_to_vocabulary(path: Path, heard: dict[str, str]) -> None:
    """Add [heard] entries to the vocabulary file, keeping its comments and
    layout. Entries already there are skipped; a broken file is never written."""
    text = path.read_text() if path.exists() else ""
    have = {k.lower() for k in tomllib.loads(text).get("heard", {})}
    heard = {k: v for k, v in heard.items() if k.lower() not in have}
    if not heard:
        return
    lines = f"# learned {dt.date.today().isoformat()}\n" + \
        "".join(f"{_toml(k)} = {_toml(v)}\n" for k, v in heard.items())
    m = re.search(r"^\[heard\]\s*$", text, re.M)
    if m is None:
        text = text.rstrip("\n") + ("\n\n" if text.strip() else "") + "[heard]\n" + lines
    else:
        nxt = re.search(r"^\[", text[m.end():], re.M)
        at = m.end() + nxt.start() if nxt else len(text)
        text = text[:at].rstrip("\n") + "\n" + lines + ("\n" + text[at:] if nxt else "")
    tomllib.loads(text)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def yes(question: str) -> bool | None:
    try:
        answer = input(question + " [y/N/q] ").strip().lower()
    except EOFError:
        return None
    return None if answer == "q" else answer == "y"


def main() -> int:
    parser = argparse.ArgumentParser(prog="iris-dictation-learn",
                                     description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="show what the agent proposes, change nothing")
    parser.add_argument("--since", help='journalctl time, e.g. "-2h" or "yesterday" '
                        "(default: since the last run, or the last 7 days)")
    args = parser.parse_args()

    since = args.since or (STATE.read_text().strip() if STATE.exists() else "-7d")
    until = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entries = read_log(since)
    if not entries:
        print(f"No dictations since {since}.")
        return 0

    path = cfgmod.VOCABULARY_PATH
    vocab_text = path.read_text() if path.exists() else "(empty)"
    log = "\n".join(f"{e['time'][11:19]}  {e['text']}"
                    + (f"  => {e['typed']}" if e["typed"] else "") for e in entries)
    print(f"Reviewing {len(entries)} dictations since {since} ...")
    answer = ask_agent(PROMPT.format(vocabulary=vocab_text, log=log))

    if args.dry_run:
        print(json.dumps(answer, indent=2, ensure_ascii=False))
        return 0
    known = tomllib.loads(vocab_text) if path.exists() else {}
    have = {k.lower() for k in known.get("heard", {})}
    heard, examples = {}, []
    quit_ = False
    for p in answer.get("heard", []):
        if quit_ or not p["said"].strip() or p["said"].lower() in have:
            continue
        print(f'\n  "{p["said"]}"  ->  "{p["meant"]}"\n  from: {p["example"]}\n  why:  {p["why"]}')
        ok = yes("  Always replace it?")
        if ok is None:
            quit_ = True
        elif ok:
            heard[p["said"]] = p["meant"]
            examples.append(p["example"])

    if not answer.get("heard"):
        print("Nothing to learn.")
    if heard:
        add_to_vocabulary(path, heard)
        print(f"\nAdded {len(heard)} replacement(s) to {path}.")
        print("Checked against the dictations they came from:")
        for text in dict.fromkeys(examples):
            try:
                print(f"  {text}\n  -> {ctl.send('fix ' + text, timeout=30)}")
            except OSError:
                print("  (daemon not running, not checked)")
                break
    if not quit_:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(until + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
