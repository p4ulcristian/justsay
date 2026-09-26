# omarchy-dictation

Push-to-talk dictation for Hyprland. Hold Caps Lock, speak, let go, and the
text is typed into whatever window is focused. Everything runs locally: no
cloud, no account.

The program itself is called **iris-dictation**: `iris-dictation`, `iris-dictation.service`.

- **Fast.** Whisper large-v3-turbo on the GPU transcribes a 4–16 second clip
  in 120–180 ms, measured on an RTX 5060 Ti. The model stays loaded, so a
  key press never waits for it.
- **Any language, or just yours.** Whisper detects the language on every
  clip. Tell it which ones you speak (`languages = ["hu", "en"]`) and short
  clips stop coming out in a third language.
- **Launcher-friendly.** Results of up to three words lose their trailing
  full stop and start lowercase, so "Firefox." arrives as `firefox`.
- **Quiet on calls.** While you hold the key, Discord's microphone stream is
  muted, so your call doesn't hear what you dictate.
- **Waveform overlay** on Omarchy: a live voice meter while you talk, then
  the text it heard.
- **Scriptable.** `iris-dictation start`/`stop` from anything, plus
  `stop-return`, which hands the transcript back instead of typing it.
  [omarchy-controller](https://github.com/p4ulcristian/omarchy-controller)
  uses these for hold-R1-to-dictate on a DualSense.

## Requirements

- Hyprland (Omarchy for the waveform overlay), PipeWire.
- An NVIDIA GPU with about 4 GB of free VRAM for the default Whisper model.
  No GPU: install with `--cpu` to use Parakeet on the CPU instead (about
  0.5–1 s per clip, 25 European languages, but no language lock).
- `uv`, `wtype`, `wl-clipboard`, `libpulse` (for `parec`/`pactl`),
  `libnotify`:

```sh
sudo pacman -S uv wtype wl-clipboard libpulse libnotify
```

## Install

```sh
git clone https://github.com/p4ulcristian/omarchy-dictation ~/.local/share/omarchy-dictation
~/.local/share/omarchy-dictation/install.sh          # or: install.sh --cpu
```

The installer creates a Python environment inside the clone, downloads the
model (about 1.6 GB) to `~/.local/share/iris-dictation/models/`, links
`iris-dictation` and friends into `~/.local/bin`, adds the waveform overlay to
the Omarchy shell if there is one, and starts the `iris-dictation` systemd user
service.

Then free up Caps Lock (next section) and try it.

## Freeing Caps Lock

iris-dictation reads Caps Lock straight from the keyboard (evdev), so it works
whatever the key is mapped to. But if Caps Lock still toggles caps, or is
your Compose key (Omarchy's default), it will also do that every time you
dictate, and a half-typed Compose sequence swallows the first letters.

Map it to nothing in your Hyprland input config, and move Compose if you use
it:

```lua
-- ~/.config/hypr/input.lua (Omarchy)
hl.config({
  input = {
    kb_options = "caps:none,compose:menu,shift:both_capslock_cancel",
  },
})
```

Nothing but iris-dictation sees Caps Lock after that, games included. Prefer another
key? Set `key = "KEY_RIGHTALT"` (any evdev `KEY_*` name) in the config
instead and leave Caps Lock alone.

If the key does nothing at all, the daemon probably can't read
`/dev/input`: the log says so. Add yourself to the `input` group and log in
again.

## Use it

Hold the key, speak, release. From scripts or other tools:

```sh
iris-dictation start          # begin recording
iris-dictation stop           # stop, transcribe, type
iris-dictation stop-return    # stop, transcribe, print the text instead of typing
iris-dictation toggle
iris-dictation status         # idle | recording | transcribing
iris-dictation transcribe some.wav   # print the text, type nothing
```

```sh
systemctl --user restart iris-dictation
journalctl --user -u iris-dictation -f
iris-dictation-daemon --debug    # run in a terminal instead (stop the service first)
```

## Second pass: your words, no "um"s

Optional. A small local language model reads what Whisper wrote before it
is typed, and fixes three things only:

- words Whisper misheard that are in your vocabulary ("Zorbax" -> "Zorbex"),
- filler sounds ("so, um, the tests pass" -> "so, the tests pass"),
- self-corrections ("Monday, no wait, Tuesday" -> "Tuesday"); the small
  default model mostly leaves these alone.

Every answer is checked against Whisper's text, and anything beyond those
changes is thrown away, so the worst case is Whisper's own text. It takes
about 0.1 s on a GPU. Any OpenAI-compatible server works; with Ollama:

```sh
ollama pull qwen3.5:2b-q4_K_M     # ~1.6 GB of VRAM while loaded
```

```toml
# ~/.config/iris-dictation/config.toml
fix_model = "qwen3.5:2b-q4_K_M"
```

Put your names and domains in `~/.config/iris-dictation/vocabulary.toml`,
copied from [vocabulary.example.toml](vocabulary.example.toml); edits apply on
the next dictation. The file and the model both stay on your machine. The
journal shows Whisper's text and every fix, so a wrong fix is easy to spot.

The `[heard]` entries in that file are certain: a phrase Whisper keeps
getting wrong is always replaced, with or without the model. The model's own
changes are checked one by one, and only the allowed ones are kept.

Check what a sentence would become: `iris-dictation fix "my dual sense controller"`.

### Let an agent find your mishearings

```sh
iris-dictation-learn            # dictations since the last run (or the last 7 days)
iris-dictation-learn --since=-2h --dry-run
```

It hands your recent dictations to an AI agent, which looks for mishearings
(the same thing said again a few seconds later, spelled differently; names
written wrong) and proposes entries. You answer y/n to each. The accepted
ones go into your vocabulary and are checked against the dictations they came
from. The agent has no tools and changes nothing itself. By default it is
Claude Code (`claude -p`), which means the reviewed dictations are sent to
Anthropic; set `IRIS_DICTATION_AGENT` to any command that reads the prompt on
stdin and prints the JSON answer to use another one.

## Config

Optional: `~/.config/iris-dictation/config.toml`. Every setting and its default is
documented in [`iris_dictation/config.py`](iris_dictation/config.py). The common ones:

```toml
languages = ["hu", "en"]    # pick only among these; [] = any of 99
key = "KEY_CAPSLOCK"        # any evdev KEY_* name
output = "type"             # or "paste" (clipboard + paste shortcut), "clipboard"
mute_apps = ["discord", "vesktop", "webcord"]   # add "chromium" for Discord in a browser
device = "cuda"             # or "cpu"
trailing_space = true
preroll_ms = 0              # >0 keeps the mic open to catch the first syllable
audio_source = ""           # a PipeWire source name; "" = default mic
```

## How it works

A resident daemon owns the microphone, the key and the model:

1. **Key down:** `parec` starts recording from PipeWire, and apps in
   `mute_apps` get their capture stream muted. The mic itself stays on for
   iris-dictation.
2. **Key up:** the clip goes through Whisper (onnxruntime, CUDA, fp16). The
   language is picked from `languages` only, by a small patch over onnx-asr's
   Whisper language detection (`iris_dictation/languages.py`). This reaches into
   onnx-asr internals, so the version is pinned in `requirements.txt`.
3. The text is cleaned up: Whisper's hallucinated "Thank you." on silent
   clips is dropped, and short results are tidied. Then `wtype` types it into
   the focused window. If typing fails, it goes on the clipboard with a
   notification, so a transcription is never lost.

The waveform overlay listens on `$XDG_RUNTIME_DIR/iris-dictation/levels.sock`, one
line per event: `recording`, `level 0.42`, `transcribing`, `text …`, `idle`.
Both sockets are documented in [PROTOCOL.md](PROTOCOL.md), for building your
own tools on them.

### Models

| model | where | per 4–16 s clip | memory | language lock |
|---|---|---|---|---|
| Whisper large-v3-turbo fp16 (default) | GPU | 120–180 ms | ~3.7 GB VRAM | yes |
| Whisper large-v3-turbo fp32 | GPU | about the same | ~6.4 GB VRAM | yes |
| Parakeet TDT 0.6B v3 | GPU | 25–65 ms | | no |
| Parakeet TDT 0.6B v3 (`--cpu`) | CPU, 6 cores | 410–990 ms | ~2.3 GB RAM | no |

Parakeet is faster but guesses the language on its own, and on short clips
it sometimes guesses wrong. Whisper was also slightly more accurate on the
English test clips.

`iris-dictation-selftest` runs the clips in `testwav/` through the running daemon
and prints what it heard; nothing is typed.

## Development

```sh
uv pip install --python .venv/bin/python pytest
.venv/bin/python -m pytest
```

`tests/test_live.py` also runs the sample clips through the running daemon
(skipped when it isn't running). Nothing is typed, but the overlay shows each
result.

## Uninstall

```sh
systemctl --user disable --now iris-dictation
rm ~/.config/systemd/user/iris-dictation.service ~/.local/bin/iris-dictation-daemon \
   ~/.local/bin/iris-dictation ~/.local/bin/iris-dictation-selftest
rm ~/.config/omarchy/plugins/p4ulcristian.iris-dictation   # Omarchy only
rm -rf ~/.local/share/omarchy-dictation ~/.local/share/iris-dictation ~/.config/iris-dictation   # clone, models, config
```

On Omarchy, also remove `p4ulcristian.iris-dictation` from the `plugins` list
in `~/.config/omarchy/shell.json`.

## License

MIT
