#!/usr/bin/env bash
# Install justsay for the current user. Safe to run again.
#
#   ./install.sh          Whisper large-v3-turbo on an NVIDIA GPU (default)
#   ./install.sh --cpu    Parakeet on the CPU: no GPU needed, no language lock
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
SRC=$PWD
PLUGIN=p4ulcristian.justsay-wave

missing=()
for tool in uv wtype wl-copy parec pactl notify-send; do
  command -v "$tool" >/dev/null || missing+=("$tool")
done
if [ ${#missing[@]} -gt 0 ]; then
  echo "Missing: ${missing[*]}" >&2
  echo "On Arch: sudo pacman -S uv wtype wl-clipboard libpulse libnotify" >&2
  exit 1
fi

echo "Setting up the Python environment ..."
[ -d .venv ] || uv venv -q --python 3.12 .venv
uv pip install -q --python .venv/bin/python -r requirements.txt

if [ "${1:-}" = "--cpu" ]; then
  ./bin/justsay-fetch-model parakeet
  mkdir -p ~/.config/justsay
  if [ ! -f ~/.config/justsay/config.toml ]; then
    cat > ~/.config/justsay/config.toml <<'TOML'
model = "nemo-parakeet-tdt-0.6b-v3"
model_path = "~/.local/share/justsay/models/parakeet-tdt-0.6b-v3"
quantization = ""
device = "cpu"
TOML
    echo "Wrote ~/.config/justsay/config.toml for Parakeet on the CPU"
  fi
else
  ./bin/justsay-fetch-model whisper
fi

mkdir -p ~/.local/bin ~/.config/systemd/user
for b in justsay-daemon justsayctl justsay-selftest; do
  ln -sfn "$SRC/bin/$b" ~/.local/bin/$b
done

# The waveform overlay, if this is Omarchy.
SHELL_JSON=~/.config/omarchy/shell.json
if [ -f "$SHELL_JSON" ]; then
  mkdir -p ~/.config/omarchy/plugins
  ln -sfn "$SRC/overlay" ~/.config/omarchy/plugins/$PLUGIN
  python3 - "$SHELL_JSON" "$PLUGIN" <<'PY'
import json, sys
path, plugin = sys.argv[1:]
with open(path) as f:
    cfg = json.load(f)
plugins = cfg.setdefault("plugins", [])
if not any(p.get("id") == plugin for p in plugins):
    plugins.append({"id": plugin})
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
    print("enabled the waveform overlay in", path)
PY
fi

cp systemd/justsay.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now justsay
systemctl --user restart justsay
echo
echo "Installed. Logs: journalctl --user -u justsay -f"
echo "Caps Lock still does its normal job until you free it; see README, 'Freeing Caps Lock'."
