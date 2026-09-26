# Third-party components

LocalFlow's own code is MIT licensed (see `LICENSE`). The downloadable builds bundle these
components, each under its own license:

| Component | Used for | License |
|---|---|---|
| Whisper `base.en` model (OpenAI), converted by Systran | speech recognition | MIT |
| faster-whisper, CTranslate2 | running the model | MIT |
| Silero VAD (via faster-whisper), ONNX Runtime | detecting speech / pauses | MIT |
| PyAV / FFmpeg libraries | audio decoding (faster-whisper dependency) | LGPL / BSD |
| NumPy | audio processing | BSD |
| sounddevice / PortAudio | microphone access | MIT |
| pynput | global hotkeys | LGPL-3.0 |
| pyperclip | clipboard (Windows) | BSD |
| keyring | storing API keys in the OS keychain | MIT |
| pystray, Pillow | tray icon (Windows) | LGPL-3.0 / MIT-CMU |
| PyObjC | macOS system APIs | MIT |
| Python, Tcl/Tk | runtime and UI | PSF / Tcl license |
| PyInstaller bootloader | packaging | GPL-2.0 with bootloader exception |

`samples/jfk.wav` (used by the self-test) is from President Kennedy's 1961 inaugural
address, a US government work in the public domain.

The LGPL components are included unmodified as separate Python packages inside the app
folder and can be replaced by the user.
