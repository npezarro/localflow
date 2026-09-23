# LocalFlow

Portable, local-only push-to-talk dictation (Wispr Flow replacement) for Windows + macOS.
Python 3.12, faster-whisper (CTranslate2, CPU int8), pynput hotkeys, sounddevice mic, Tk UI, PyInstaller one-folder builds.

## Layout
- `localflow/app.py` Tk UI, overlay pill, control + transcription worker threads (Tk only touched on main thread via `ui_q`)
- `localflow/hotkey.py` pure `HotkeyMachine` (unit tested) + pynput `HotkeyListener` (lock-key suppression per OS)
- `localflow/platform_fix.py` macOS keyboard-layout pin (pynput TSM off-main-thread crash), AX trust prompt, Windows no-activate overlay, Win-key mask
- `localflow/paths.py` portable `data/` next to the exe/.app, user-dir fallback when read-only or App-Translocated
- `localflow/selftest.py` headless check CI runs against each packaged app

## Rules
- Can't build Mac/Windows binaries from WSL: CI (`.github/workflows/build.yml`) builds + selftests all three zips. Tag `v*` to publish a release.
- `onnxruntime` is pinned to 1.23.2 on macOS: later versions dropped Intel mac wheels.
- `LOCALFLOW_FAKE_MIC=<wav>` feeds a WAV instead of the mic (E2E testing); `LOCALFLOW_DATA_DIR` overrides the data folder.
- Tests: `python -m pytest -q` (test_selftest downloads tiny.en on first run).
