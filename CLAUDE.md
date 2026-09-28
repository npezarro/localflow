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
- Public repo (MIT). Keep working notes (context.md / progress.md) untracked: they're gitignored. No personal/infra details in code, tests, docs or commit messages.
- Can't build Mac/Windows binaries from WSL: CI (`.github/workflows/build.yml`) builds + tests 3 portable zips, the Windows installer (`installer/localflow.iss`, Inno Setup) and 2 dmgs. Tag `v*` to publish a release; README download links use `releases/latest/download/<fixed name>`, so keep asset names stable.
- Portable vs installed is decided by `paths.data_dir()`: a `data` folder next to the app = portable (zips ship one); otherwise per-user folder. Don't create `data` from an installed copy.
- Icon: `python scripts/make_icon.py` regenerates `assets/icon.png` + `.ico`; CI builds the `.icns`.
- Transcription must keep `without_timestamps=False`: with True, Whisper skips the rest of a 30 s window when it stops early and silently drops speech from dictations over ~20 s (found 2026-09-28: 55 of 93 words).
- GPU (`localflow/gpu.py`): only cublas64_12, cublasLt64_12, cudnn64_9 are needed (measured); they're range-read out of NVIDIA's PyPI wheels. CTranslate2 finds them via PATH, not just add_dll_directory. Without cudnn64_9 it silently runs on CPU: check `Transcriber.device`.
- `speech_regions` must use a small `speech_pad_ms`: the 400 ms default erases the pauses the chunker (`localflow/chunked.py`) cuts at.
- `onnxruntime` is pinned to 1.23.2 on macOS: later versions dropped Intel mac wheels.
- `LOCALFLOW_FAKE_MIC=<wav>` feeds a WAV instead of the mic (E2E testing); `LOCALFLOW_DATA_DIR` overrides the data folder.
- Tests: `python -m pytest -q` (test_selftest downloads tiny.en on first run).
