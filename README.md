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

A small pill at the bottom of the screen shows when LocalFlow is listening and transcribing. Taps shorter than 0.3 s are ignored, and if you press a different key while holding the hotkey (for example `Ctrl`+`Win`+`D`), LocalFlow backs off so your normal shortcuts still work.

**Transcripts tab:** every dictation, newest first. Search, double-click to copy, delete, or clear.

**Settings tab:**

- **Dictation hotkey / Paste last:** type a combo (`ctrl+cmd`, `alt_r`, `ctrl+shift+space`) or click *Record…* and press it. `cmd` is the Windows key on Windows. A single right-hand modifier such as `alt_r` (right Option) makes a nice one-key hotkey.
- **Mode:** `hold` (push-to-talk) or `toggle` (press once to start, again to stop).
- **Model:** bigger is more accurate and slower. Anything other than the bundled `base.en` downloads once into `data/models` and is reused offline after that.

  | Model | Download | Notes |
  |---|---|---|
  | `tiny.en` | 75 MB | fastest, rough |
  | `base.en` | 145 MB | bundled default |
  | `small.en` | 480 MB | good balance on a modern laptop |
  | `distil-large-v3` | 1.5 GB | near large-model accuracy, English |
  | `large-v3-turbo` | 1.6 GB | best accuracy, multilingual, needs a fast CPU |
  | `medium.en` | 1.5 GB | |

- **Language:** `en` or `auto` (auto-detect needs a model without `.en`).
- **Microphone:** pick an input device or use the system default.
- **Paste into the focused app:** off = only copy to the clipboard.
- **Restore previous clipboard:** off (default) = the transcript stays on the clipboard.
- **Remove filler words**, **trailing space**, **sounds**.
- **Vocabulary:** names and jargon Whisper should spell your way (one per line).
- **Replacements:** `spoken => written`, one per line. Examples: `new line => \n`, `my email => me@example.com`.

## Portable data

Everything LocalFlow writes lives in a `data` folder next to the app:

```
LocalFlow/
  LocalFlow.exe          (Windows)  or  LocalFlow.app (macOS)
  data/
    config.json          settings (editable by hand while LocalFlow is closed)
    history.jsonl        transcripts
    models/              downloaded models
    localflow.log        log for troubleshooting
```

Move or copy the folder and everything comes with it; delete it to uninstall. If the folder is read-only (or macOS runs the app from a quarantine sandbox), LocalFlow falls back to `%LOCALAPPDATA%\LocalFlow` or `~/Library/Application Support/LocalFlow` and shows the path at the bottom of Settings.

## Compared with Wispr Flow

Same core loop: hold-to-talk, hands-free lock, paste into any app, history, dictionary/snippets. Differences: transcription is plain local Whisper, so there is no cloud LLM rewriting pass (tone adjustment, "command mode", automatic list formatting), and on Mac the default hotkey is Control+Option because the Fn key is not reliably visible to apps without a kernel-level helper.

## Troubleshooting

- **Nothing happens on the hotkey (Mac):** Accessibility and Input Monitoring must both be on for LocalFlow, then restart it. After updating to a new build you may need to remove and re-add it in both lists.
- **Text is copied but not pasted:** some elevated (admin) windows on Windows block synthetic keystrokes from normal apps; press `Ctrl`+`V` yourself or run LocalFlow as admin.
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
