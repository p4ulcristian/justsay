# justsay

Hold **CapsLock**, talk, release — your speech is transcribed and dropped into
the focused window. Push-to-talk dictation for Linux/Wayland (Hyprland).

Speech-to-text runs on the **iris-comms** server
([nvidia/parakeet-tdt-0.6b-v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3),
multilingual with automatic language detection — Hungarian and English just
work, no language to set). The client here records the mic and delivers the
result.

## How it works

```
hold CapsLock ──(keyd: capslock→F13)──> daemon grabs F13 from the keyd
   release    ──> pw-record → /tmp/iris-ptt.wav
              ──> POST to iris-comms /stt/transcribe (X-API-Key, WireGuard-direct)
              ──> paste the text into the focused window (Ctrl+V, or
                  Ctrl+Shift+V in terminals — auto-detected via hyprctl)
```

The daemon listens on the **keyd virtual keyboard** without grabbing it, so the
keyboard is unaffected if the daemon stops.

## Install

```bash
./install.sh
```

This copies the daemon to `~/.local/bin`, installs the systemd **user** service
(starts with the graphical session), adds `capslock = f13` to keyd, and starts
the service. You must also place the iris-comms API key at
`~/.config/iris-ptt/api_key` (mode 600).

Dependencies (Arch): `pacman -S wtype python-evdev wl-clipboard` (plus
`pipewire`/`pw-record`, `libnotify`, `curl`, `keyd`).

## Configuration (env vars on the service)

| Variable | Default | Description |
|---|---|---|
| `IRIS_PTT_ENDPOINT` | `http://10.99.0.2:4260/stt/transcribe` | STT endpoint (WireGuard-direct) |
| `IRIS_PTT_LANG` | _(empty)_ | force a language code; empty = server auto-detects |
| `IRIS_PTT_OUTPUT` | `paste` | `paste` (instant, clipboard) or `type` (keystrokes) |

Override with `systemctl --user edit iris-ptt` (add `Environment=…` lines),
then `systemctl --user restart iris-ptt`.

## Files

- `iris-ptt-daemon.py` — the push-to-talk daemon (python-evdev)
- `iris-ptt.service` — systemd user unit
- `keyd-capslock.conf` — the keyd remap snippet
- `install.sh` — deploy + (re)start

## Logs

```bash
journalctl --user -u iris-ptt -f
```

The server side (iris-comms STT/TTS) lives in its own repo:
github.com/p4ulcristian/iris-stt.
