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

## 2026-09-24 v0.3.0
- AI clean-up setup assistant (first launch / unverified provider / after failure / Settings / tray): per-install diagnosis (ok, signed out, not responding, error), Sign in button opens a terminal running the CLI login, API key test before save. Clean-up pauses after 2 consecutive failures.
- Adjustable "Keep listening after release" (release_tail, 0-3 s) and "Keep before press" (preroll, 0-2 s).
- Verified on this PC via tests/setup_smoke.py (real app): first-launch open, Claude detection picked WSL Ubuntu over signed-out native + npm installs, bad Groq key rejected, 2 failures pause + assistant on next show, tail/preroll saved and applied to the live recorder, invalid number rejected, zero keychain writes.
- Confirmed no credentials in release zips (scan with positive control) and none in CI (no secrets configured).

## 2026-09-24 v0.4.0
- Live typing (toggle: Settings checkbox, tray item, Alt+Shift+L): LocalAgreement-2 streaming over faster-whisper word timestamps (localflow/live.py), Unicode SendInput / CGEvent typing with held-modifier lift on Windows (localflow/typer.py), LIVE badge on the pill.
- Fixes found while benchmarking: prompt must only contain text whose audio left the buffer (else Whisper skips words and timestamps shift); filter seam on word END + n-gram dedupe; temperature 0 + max_new_tokens cap for live passes (hallucination loops on cut-off words).
- Verified: 4 clips word-complete vs one-shot, 75-93% typed before stop; Windows E2E with Ctrl+Alt physically held (fake mic): progressive text from t=4s, final exact, no shortcut misfires, focus kept; toggle flips + saves; tests 31 pass (+1 Windows-only).
- Not verified: Mac live typing (CGEvent flags path) on real hardware; Chromium/Electron text fields.

## 2026-09-24 v0.5.0
- Learning (localflow/learn.py, data/learned.json): corrections in Transcripts -> word-diff replacements + vocabulary; uncommon words (multi-token in Whisper's tokenizer) promoted after 3 dictations; merged into each dictation via App._run_cfg (user entries win); Settings list with forget/forget-all/off switch.
- Verified: real model, one correction turns "Kabir nets" into "Kubernetes" on the next pass, and the vocabulary hint alone still fixes it; Windows real-app UI run (edit -> Save correction -> learned list -> forget); tests 35 pass (+1 Windows-only).

## 2026-09-25 v0.6.0
- Always-on live listening (live_pause_hotkey, default Ctrl+Shift+Space; tray item): continuous LiveSession with Silero VAD pause-flush (1.2 s), silence skipping, once-per-pause flush, loop/clause-repeat guard, low-confidence trailing-word trim, Recorder.trim_before + peek offsets, auto-pause after N min silence.
- Per-app typing control (localflow/apps.py): all / only / except + seen-apps picker; applies to paste, paste-last and live typing; blocked -> clipboard + history + pill notice.
- One-shot hotkeys now swallow their final key (Windows hook filter, macOS intercept); LocalFlow's own SendInput events are tagged (dwExtraInfo) and ignored by its listener.
- Benchmarks (base.en, 16 randomized pass timings over 2 recordings): no lost or duplicated words; remaining error is an occasional "ask"->"asked" substitution on partial audio. Windows E2E: start hotkey typed nothing, word-perfect session, pause/resume, auto-pause, allow-list blocks typing, settings save/validation.
- Not verified: Mac continuous mode / CGEvent intercept on hardware; very long (>10 min) real-mic sessions.
