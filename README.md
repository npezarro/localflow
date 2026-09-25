# LocalFlow

Push-to-talk dictation for Windows and macOS that runs entirely on your machine. Hold a hotkey, talk, let go: the words are transcribed locally with Whisper (via faster-whisper) and pasted into whatever app has focus. Every transcript is also kept on the clipboard and in a searchable history.

No account, no cloud, no installer. Unzip the folder and run it.

## Download

Grab the zip for your machine from the latest build (GitHub Actions artifacts, or the Releases page for tagged versions):

| Platform | File |
|---|---|
| Windows 10/11 (x64) | `LocalFlow-windows-x64.zip` |
| Mac, Apple Silicon (M1 and later) | `LocalFlow-macos-arm64.zip` |
| Mac, Intel | `LocalFlow-macos-intel.zip` |

Each zip includes the `base.en` model, so it works offline on first launch.

## Run it

**Windows:** unzip anywhere (Desktop, a USB stick, `C:\Tools`), open the `LocalFlow` folder, double-click `LocalFlow.exe`. If SmartScreen warns about an unrecognized app, choose *More info → Run anyway* (the build is unsigned). Closing the window sends LocalFlow to the system tray; quit from the tray icon.

**macOS:** unzip, then in Terminal clear the download quarantine once (the app is not notarized):

```bash
xattr -dr com.apple.quarantine ~/Downloads/LocalFlow
```

Open `LocalFlow.app`. macOS will ask for three permissions; grant each, then quit and reopen LocalFlow:

1. **Microphone** (prompted on your first dictation)
2. **Accessibility** (System Settings → Privacy & Security → Accessibility), needed to paste
3. **Input Monitoring** (same pane), needed to see the hotkey

## Use it

| Action | Windows default | macOS default |
|---|---|---|
| Dictate (hold, speak, release) | `Ctrl` + `Win` | `Control` + `Option` |
| Hands-free | while holding, tap `Space`; press the hotkey again to finish | same |
| Cancel | `Esc` | `Esc` |
| Paste last transcript again | `Alt` + `Shift` + `Z` | `Option` + `Shift` + `Z` |
| Turn live typing on/off | `Alt` + `Shift` + `L` | `Option` + `Shift` + `L` |
| Start / pause always-on live listening | `Ctrl` + `Shift` + `Space` | `Control` + `Shift` + `Space` |

### Live typing

With live typing on, words appear in whatever text field you're using while you're still talking, instead of all at once when you let go. Turn it on with the checkbox in *Settings → Dictation*, the tray menu, or `Alt`+`Shift`+`L`; the pill shows a **LIVE** badge while it's on.

How it works: about once a second LocalFlow re-transcribes the part of the recording that isn't final yet and types only the words two passes in a row agree on, so what's typed never has to be deleted and rewritten. Text runs roughly one to two seconds behind your voice, and the last few words are typed when you stop. Words go in as keystrokes, so your clipboard isn't touched; if you're holding the hotkey, LocalFlow briefly releases it for each burst so held Ctrl/Alt/Win don't turn letters into shortcuts.

**Always-on live listening.** Press `Ctrl`+`Shift`+`Space` and LocalFlow keeps listening with nothing held down: words flow into whatever text field is focused (switch apps and they follow you) and each sentence lands as soon as you pause. Press it again to pause, and again to resume. To have it running whenever LocalFlow is open, tick *Settings → Dictation → Start always-on listening when LocalFlow opens*; the hotkey then just pauses and resumes it. the pill shows a red dot and **LIVE** while it's listening. Silence is skipped rather than transcribed, audio that's already been typed is discarded as you go so long sessions stay light, and after 5 minutes of silence it pauses itself (*Settings → Dictation → Auto-pause after silence*; 0 = never). Each session is saved to Transcripts when you pause. The hotkey's own keystroke is swallowed, so it never types a space into your document.

Trade-offs: live typing uses the local model (so it works offline, but not with the online engine) and AI clean-up doesn't apply, because text that's already typed isn't rewritten. `Esc` stops a live dictation but leaves the words already typed.

A floating pill appears at the bottom centre of the monitor you're working on (the one with the focused window) while you dictate: a live waveform that follows your voice, a red dot in hands-free mode, then a travelling wave while it transcribes (blue while AI clean-up runs). It never takes focus, so the text still lands in your app. Taps shorter than 0.3 s are ignored, and if you press a different key while holding the hotkey (for example `Ctrl`+`Win`+`D`), LocalFlow backs off so your normal shortcuts still work.

**Transcripts tab:** every dictation, newest first. Search, double-click to copy, delete, or clear. Select one to see it below the list; fix any mistakes there and press **Save correction** (the fixed text is also copied to the clipboard).

### Learning your words

LocalFlow learns how you talk, on your computer only (`data/learned.json`), from two sources:

- **Your corrections.** When you fix a transcript and press *Save correction*, it compares the two and learns what it misheard ("Kabir nets" → "Kubernetes"): the pair becomes a replacement and the corrected word a spelling hint, from the very next dictation. The hint alone is usually enough for Whisper to spell the word right in new sentences too.
- **Words you repeat.** Uncommon words (names, jargon, product terms: anything Whisper's tokenizer splits into several pieces) are counted per dictation; once one has come up in 3 dictations it's fed to Whisper as a spelling hint automatically.

*Settings → Words → Learned* lists everything it has picked up, with *Forget selected* / *Forget all*, and a checkbox to turn learning off. Your own Vocabulary and Replacements always win over learned ones. It learns from transcripts, not from edits you make afterwards in other apps.

**Settings tab:** changes take effect when you press **Save** (bottom of the tab, always visible). Leaving the tab or closing the window with unsaved changes asks whether to save them; **Revert** discards them.

- **Dictation:** hotkey (type a combo like `ctrl+cmd`, `alt_r`, `ctrl+shift+space`, or click *Record…* and press it; `cmd` is the Windows key on Windows), mode (`hold` push-to-talk or `toggle`), the paste-last hotkey, and **Keep listening after release** (0-3 s, default 0.3): how long the mic stays open after you let go of the hotkey, so a trailing word isn't cut off. The pill keeps showing the live waveform until it closes.
- **Microphone:** input device; **Keep the microphone ready** (on by default) keeps the input open and buffers the last half second, so your first word isn't cut off while the device wakes up (Bluetooth headsets can take a second). The OS microphone indicator stays on while LocalFlow runs; turn this off if you'd rather the mic only opens while you dictate. **Keep before press** (0-2 s, default 0.5) sets how much of that buffer is kept. **Test microphone** records 4 seconds and shows the level and what was heard.
- **Transcription engine:**
  - *On this computer*: Whisper runs locally. `base.en` is bundled; bigger models download once into `data/models`.

    | Model | Download | Notes |
    |---|---|---|
    | `base.en` | 145 MB | bundled default, fastest |
    | `small.en` | 480 MB | more accurate, about 2x slower |
    | `distil-large-v3` | 1.5 GB | English, near large-model accuracy |
    | `large-v3-turbo` | 1.6 GB | best local accuracy, multilingual, needs Apple Silicon or a fast CPU |

  - *Online API*: sends the recording to an OpenAI-compatible speech API. **Groq** (`whisper-large-v3-turbo`) is the recommended choice: about half a second per dictation and a free tier. **OpenAI** (`gpt-transcribe`) and any **custom** OpenAI-compatible server also work. If the service is unreachable, LocalFlow falls back to the local model.
- **AI clean-up (optional):** a second pass that fixes punctuation, removes false starts and applies self-corrections ("Tuesday, actually Wednesday" becomes "Wednesday"), like Wispr's formatting. Choose:
  - **Claude:** uses your Claude subscription through Claude Code (`claude`), which must be installed and signed in on the computer. About 2-4 s.
  - **ChatGPT:** uses your ChatGPT subscription through the Codex CLI (`codex login`). About 3-5 s.
  - **API key:** Groq (fastest, about 0.4 s), OpenAI, or a custom/local server such as Ollama.

  A **setup assistant** opens the first time you launch LocalFlow, whenever you pick a clean-up option that hasn't been checked on this computer, and after clean-up fails (from *Settings → Set up / check…* or the tray menu any time). It tries every install it can find (on Windows including the copies inside WSL), says in plain words what's wrong with each (not installed, not signed in, not responding), and offers the fix: an install link, a **Sign in** button that opens a terminal running the sign-in, or an API key box it tests before saving. Nothing is turned on until a check passes.

  If clean-up fails during dictation, the plain transcript is pasted, the pill says so, and after two failures in a row clean-up pauses until you fix it, so a broken setup never adds a delay to every dictation.
- **API keys:** stored in the Windows Credential Manager / macOS Keychain, not in the data folder.
- **Apps:** *Type into* **All apps** (default), **Only the apps listed**, or **All apps except those listed**, for both pasting and live typing. List programs one per line (`chrome`, `slack`, `code`, `notepad`; on a Mac the app name like `Google Chrome` or its bundle id). *Apps you've dictated into* offers the ones you've actually used so you don't have to guess names. When the focused app isn't allowed, the text is copied to the clipboard and saved in Transcripts instead, and the pill says so.
- **Output:** paste into the focused app, restore the previous clipboard, remove filler words, trailing space, sounds, keep the last recording for troubleshooting.
- **Words:** vocabulary (names and jargon to spell your way; this is the single biggest accuracy win for names) and replacements (`spoken => written`, e.g. `new line => \n`).

### Why subscriptions only cover clean-up, not transcription

Neither Anthropic nor OpenAI offers speech-to-text on a subscription login. Claude's dictation only exists inside Claude Code, and ChatGPT's transcription endpoint is private and requires impersonating OpenAI's own apps, which LocalFlow deliberately doesn't do. Online transcription therefore uses an API key (Groq's free tier covers normal dictation use), while the clean-up pass can use your Claude or ChatGPT subscription through their official command-line tools.

## Portable data

Everything LocalFlow writes lives in a `data` folder next to the app:

```
LocalFlow/
  LocalFlow.exe          (Windows)  or  LocalFlow.app (macOS)
  data/
    config.json          settings (editable by hand while LocalFlow is closed)
    history.jsonl        transcripts (with the pre-clean-up text when AI clean-up ran)
    last-recording.wav   your most recent dictation, for troubleshooting (optional)
    models/              downloaded models
    localflow.log        log for troubleshooting
```

Move or copy the folder and everything comes with it; delete it to uninstall. If the folder is read-only (or macOS runs the app from a quarantine sandbox), LocalFlow falls back to `%LOCALAPPDATA%\LocalFlow` or `~/Library/Application Support/LocalFlow` and shows the path at the bottom of Settings.

## Compared with Wispr Flow

Same core loop: hold-to-talk, hands-free lock, floating waveform pill, paste into any app, history, dictionary/snippets, optional AI formatting. Differences: no "command mode" (editing selected text by voice) or per-app tone styles yet, and on Mac the default hotkey is Control+Option because the Fn key is not reliably visible to apps without a kernel-level helper.

## Troubleshooting

- **Words missing or wrong:** run *Settings → Test microphone*. A low peak means the wrong input or a muted mic. Keep *Keep the microphone ready* on so the first word isn't clipped. Add names to *Vocabulary*. For the best accuracy switch the engine to Groq, or the local model to `small.en` / `large-v3-turbo`. `data/localflow.log` records each dictation's length, level, engine and timing, and `data/last-recording.wav` is exactly what was transcribed.
- **Claude/ChatGPT clean-up fails:** press *Test clean-up*; it lists every install it tried and why each failed (for example "Not logged in"). Sign in with `claude` or `codex login`, or put a working command in *Claude command* / *Codex command*.

- **Nothing happens on the hotkey (Mac):** Accessibility and Input Monitoring must both be on for LocalFlow, then restart it. After updating to a new build you may need to remove and re-add it in both lists.
- **Text is copied but not pasted:** some elevated (admin) windows on Windows block synthetic keystrokes from normal apps; press `Ctrl`+`V` yourself or run LocalFlow as admin.
- **Ctrl+Win does nothing (Windows):** a key remapper (AutoHotkey, PowerToys Keyboard Manager) that rewrites the Win key will swallow the chord. Pick another hotkey such as `ctrl+alt` or `alt_r`.
- **Start menu opens after `Ctrl`+`Win`:** shouldn't happen (LocalFlow masks the Win key), but if it does, pick a different hotkey.
- **Check the model works:** run `LocalFlow.exe --selftest --out result.json` (or `LocalFlow.app/Contents/MacOS/LocalFlow --selftest`) to transcribe the bundled sample and report timing.

## Build from source

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt pyinstaller pytest
python -m pytest -q
python run_localflow.py                            # run from source
python scripts/fetch_model.py base.en && pyinstaller --noconfirm localflow.spec
```

CI (`.github/workflows/build.yml`) builds all three zips, runs the unit tests, and runs `--selftest` against each packaged app before uploading it.
