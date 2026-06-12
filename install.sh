#!/usr/bin/env bash
# Install the iris-ptt push-to-talk client from this repo into the user's
# system locations and (re)start the service. Idempotent.
set -euo pipefail
cd "$(dirname "$0")"

BIN="$HOME/.local/bin"
UNIT_DIR="$HOME/.config/systemd/user"
CFG="$HOME/.config/iris-ptt"

mkdir -p "$BIN" "$UNIT_DIR" "$CFG"

# 1. daemon
install -m 0755 iris-ptt-daemon.py "$BIN/iris-ptt-daemon.py"
echo "✓ daemon -> $BIN/iris-ptt-daemon.py"

# 2. systemd user service
install -m 0644 iris-ptt.service "$UNIT_DIR/iris-ptt.service"
echo "✓ service -> $UNIT_DIR/iris-ptt.service"

# 3. keyd remap (needs sudo; only if not already present)
if ! grep -q '^capslock' /etc/keyd/default.conf 2>/dev/null; then
  echo "… adding 'capslock = f13' to /etc/keyd/default.conf (sudo)"
  sudo sed -i '/^\[main\]/a capslock = f13' /etc/keyd/default.conf
  sudo keyd reload
fi
echo "✓ keyd: capslock -> f13"

# 4. API key reminder
if [ ! -s "$CFG/api_key" ]; then
  echo "!! Put the iris-comms API key at $CFG/api_key (chmod 600) before use."
fi

# 5. (re)start service
systemctl --user daemon-reload
systemctl --user enable --now iris-ptt.service
systemctl --user restart iris-ptt.service
echo "✓ service running:"
systemctl --user --no-pager status iris-ptt.service | sed -n '1,3p'
