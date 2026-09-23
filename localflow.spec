# PyInstaller spec: one-folder build (portable: unzip and run).
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

from localflow import __version__

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"

datas = collect_data_files("faster_whisper") + [
    ("samples/jfk.wav", "samples"),
    ("build/models/base.en", "models/base.en"),
]
binaries = collect_dynamic_libs("ctranslate2") + collect_dynamic_libs("onnxruntime")
if IS_WIN:
    hidden = ["pynput.keyboard._win32", "pynput.mouse._win32", "pystray._win32"]
elif IS_MAC:
    hidden = ["pynput.keyboard._darwin", "pynput.mouse._darwin", "AppKit", "Quartz",
              "ApplicationServices"]
else:
    hidden = ["pynput.keyboard._xorg", "pynput.mouse._xorg"]

a = Analysis(
    ["run_localflow.py"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    excludes=["matplotlib", "pandas", "scipy", "IPython", "pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LocalFlow",
    console=False,
    target_arch=None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="LocalFlow")
if IS_MAC:
    app = BUNDLE(
        coll,
        name="LocalFlow.app",
        bundle_identifier="ca.pezant.localflow",
        version=__version__,
        info_plist={
            "CFBundleShortVersionString": __version__,
            "NSMicrophoneUsageDescription": "LocalFlow records your voice while you hold the "
                                            "dictation hotkey and transcribes it on this Mac.",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "13.0",
        },
    )
