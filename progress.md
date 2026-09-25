# progress
## 2026-09-22 v0.1.0
- Built: hold-to-talk + hands-free lock + toggle mode, Esc cancel, paste-last hotkey, overlay pill, transcript history (search/copy/delete), settings UI with hotkey recorder, vocabulary + replacements, filler removal, portable data/ folder, bundled base.en.
- Verified: unit tests (14); packaged-app selftest green on Windows x64, macOS arm64 (14.8), macOS Intel (15.7); Windows E2E from source (hold → transcribe → paste into focused textbox; hands-free with Space suppressed; overlay kept focus off).
- Not verified: real-mic dictation and permission prompts on a physical Mac; packaged Windows exe E2E with the hotkey (this PC's MacKeys.ahk remaps Win→Ctrl, which swallows Ctrl+Win).
- Next ideas: optional local-LLM cleanup pass (local-llm-gateway), CUDA on Windows, code signing/notarization, Fn key on Mac.

## 2026-09-24 v0.2.0
- Settings: scrollable, pinned Save/Revert footer, dirty tracking, save prompt on tab leave/window close (root cause of "mode not saving": Save button was below the fold of the old form).
- Capture: warm mic with 0.5 s pre-roll + 0.3 s tail, native-rate fallback with anti-alias resample, level normalisation before VAD, permissive VAD + no-VAD retry, last-recording.wav + per-dictation log line, Test microphone button.
- Engines: online OpenAI-compatible STT (Groq default, OpenAI gpt-transcribe, custom) with local fallback; keys in OS keychain.
- AI clean-up: Claude Code CLI (subscription), Codex CLI (subscription), or API (Groq qwen default). Auto-detects working install incl. WSL distros on Windows.
- Indicator: Wispr-style pill on the active monitor (foreground window's monitor on Windows, pointer's screen on Mac).
- Verified on this PC: UI smoke (save/no/cancel paths), pill render + focus retention, control-path flow with fake mic, Groq STT 0.5-0.7 s, Groq/Claude/Codex clean-up, Windows WSL detection.
- Not verified: OpenAI API defaults (no key), multi-monitor placement on real hardware, Mac pill transparency (CI smoke only).
