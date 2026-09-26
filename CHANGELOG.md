# Changelog

## 1.1.0

To update: `git pull`, run `./install.sh` again (with `--cpu` if you
installed that way), and on Omarchy restart the shell (`omarchy-restart-shell`)
so it loads the new overlay.

Changed:
- The overlay keeps one look from start to finish: the dot stays on screen
  while transcribing and next to the result, and while waiting the waves
  ease from following your voice into a slow breath instead of switching to
  a different full-width animation.
- `iris-dictation transcribe <file>` replies with the text instead of typing
  it into the focused window. `iris-dictation-selftest` prints its results
  the same way.
- `--cpu` installs the CPU build of onnxruntime: about 150 MB instead of
  2.4 GB of CUDA libraries. The GPU build is now pinned.
- Removed `$XDG_RUNTIME_DIR/iris-dictation/state`, which nothing in
  iris-dictation read. Use `iris-dictation status`, or follow the state live
  on `levels.sock` (see PROTOCOL.md).

Fixed:
- The overlay stopped appearing for good after iris-dictation restarted: it
  retried the connection once, before the model had loaded. It now retries
  every 2 seconds until the daemon answers.
- `transcribe` with a missing or broken file crashed the daemon.
- `transcribe` with a relative path looked in the daemon's directory.
- `notify = false` silenced only one of the three notifications.
- The daemon refused to start when no keyboard was plugged in; it now waits
  for one.
- Unknown settings in `config.toml` (typos) are logged instead of silently
  ignored.

Added:
- PROTOCOL.md, documenting both sockets.
- Tests (`.venv/bin/python -m pytest`).

## 1.0.0

First release.
