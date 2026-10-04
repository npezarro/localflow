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
- Every local transcription goes through `chunked.transcribe_in_chunks` (<= 20 s per Whisper call; cuts at pauses, forced cut at the quietest point). This bounds the whole 30 s window-resume failure class; keep it even if the cause below is fixed upstream.
- Transcription must keep `without_timestamps=False`: with True, Whisper skips the rest of a 30 s window when it stops early and silently drops speech from dictations over ~20 s (found 2026-09-28: 55 of 93 words).
- GPU (`localflow/gpu.py`): only cublas64_12, cublasLt64_12, cudnn64_9 are needed (measured); they're range-read out of NVIDIA's PyPI wheels. CTranslate2 finds them via PATH, not just add_dll_directory. Without cudnn64_9 it silently runs on CPU: check `Transcriber.device`.
- `speech_regions` must use a small `speech_pad_ms`: the 400 ms default erases the pauses the chunker (`localflow/chunked.py`) cuts at.
- `onnxruntime` is pinned to 1.23.2 on macOS: later versions dropped Intel mac wheels.
- `LOCALFLOW_FAKE_MIC=<wav>` feeds a WAV instead of the mic (E2E testing); `LOCALFLOW_DATA_DIR` overrides the data folder.
- `work_q` carries two job shapes: `(samples, seconds, chunker)` and `("feedback", samples, info)`. Dispatch with `isinstance(job[0], str)`: `ndarray == "feedback"` raises outside the try and silently kills the transcription thread (the app keeps running, nothing transcribes).
- Spoken corrections (`localflow/feedback.py`, `App._apply_feedback`): the in-place swap is undo + paste, so it only runs right after LocalFlow's own paste, same window, no user keystrokes since (`HotkeyListener.user_keys`), never in terminals (`apps.is_terminal`: by window class as well as program name, since a console window can belong to any program; Ctrl+Z suspends there). Otherwise clipboard only.
- Windows E2E harness gotcha: an Alt-based dictation hotkey puts a Tk test window into menu mode and eats the next Ctrl+V; use a single key (F9) for the harness hotkey.
- Hotkeys going dead: `HotkeyMachine` needs an exact key match, so ONE key whose release we never saw (lock screen, Ctrl+Alt+Del, UAC) blocks it forever. `HotkeyListener.clear_stale()` asks the OS (GetAsyncKeyState / CGEventSourceKeyState, codes recorded per key name in `_on_press`) on every press and every 2 s (`App._hotkey_watchdog`); the watchdog also sends a tagged unassigned-key probe (`PROBE_VK`) to catch a hook the OS removed. Reproduced by releasing a key with the typer TAG (hook ignores it, OS state updates).
- Learning from edits (`localflow/edits.py`): reads only the focused field right after our paste, until focus leaves it or it is sent/cleared (20 s cap; last reading while focused wins). App edits need the same fix twice (`confirm_after=2`) before becoming a replacement: an edit can be a change of mind. comtypes wrappers are generated at build time in `localflow.spec`.
- Windows desktop E2E harnesses must find their target window by process and verify it's in front before sending any key (`GetForegroundWindow()` after launching grabs whatever the user had open).
- CUDA: `WhisperModel(device="cuda")` loads fine without cuBLAS/cuDNN and only fails on the first transcribe, so `Transcriber.load` runs a warm-up transcription before reporting `device == "cuda"`. Never trust the load alone.
- Settings dropdowns/number boxes: Tk changes their value on the mouse wheel; `SettingsPanel._guard_wheel` makes the wheel scroll the page instead (new ones are covered automatically).
- macOS HTTPS: the frozen app's OpenSSL looks for a CA file at a build-machine path that users' Macs don't have, so urllib failed with CERTIFICATE_VERIFY_FAILED on real Macs while CI (which has the file) passed. `__main__._setup_tls` points SSL_CERT_FILE at the bundled certifi; the selftest fails on macOS unless HTTPS works through that bundled file.
- macOS permissions: ad-hoc signatures change every build, so TCC grants silently stop applying after an update (the old entry stays "on"). CI signs with a stable self-signed identity (secrets MACOS_SELF_SIGN_*); don't fall back to ad-hoc for releases.
- MLX models: a model folder counts as downloaded only with the `.localflow-complete` marker written after the weights validate (`apple_gpu._weights_ok`); an interrupted download used to leave a truncated weights.npz ("[load_npz] Input must be a zip file").
- Tests: `python -m pytest -q` (test_selftest downloads tiny.en on first run).
