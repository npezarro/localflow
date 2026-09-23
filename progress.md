# progress
## 2026-09-22 v0.1.0
- Built: hold-to-talk + hands-free lock + toggle mode, Esc cancel, paste-last hotkey, overlay pill, transcript history (search/copy/delete), settings UI with hotkey recorder, vocabulary + replacements, filler removal, portable data/ folder, bundled base.en.
- Verified: unit tests (14); packaged-app selftest green on Windows x64, macOS arm64 (14.8), macOS Intel (15.7); Windows E2E from source (hold → transcribe → paste into focused textbox; hands-free with Space suppressed; overlay kept focus off).
- Not verified: real-mic dictation and permission prompts on a physical Mac; packaged Windows exe E2E with the hotkey (this PC's MacKeys.ahk remaps Win→Ctrl, which swallows Ctrl+Win).
- Next ideas: optional local-LLM cleanup pass (local-llm-gateway), CUDA on Windows, code signing/notarization, Fn key on Mac.
