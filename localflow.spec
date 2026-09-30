# PyInstaller spec: one-folder build (portable: unzip and run).
import os
import sys

sys.path.insert(0, SPECPATH)  # noqa: F821 (injected by PyInstaller)

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

from localflow import __version__

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"

datas = collect_data_files("faster_whisper") + [
    ("samples/jfk.wav", "samples"),
    ("assets/icon.png", "assets"),
    ("build/models/base.en", "models/base.en"),
]
binaries = collect_dynamic_libs("ctranslate2") + collect_dynamic_libs("onnxruntime")
if IS_WIN:
    hidden = ["pynput.keyboard._win32", "pynput.mouse._win32", "pystray._win32"]
    # UI Automation wrappers (learning from edits): generate them now so the packaged app
    # never has to write generated code at run time.
    import comtypes.client
    comtypes.client.GetModule("UIAutomationCore.dll")
    from PyInstaller.utils.hooks import collect_submodules
    hidden += ["comtypes.client", "comtypes.stream"] + collect_submodules("comtypes.gen")
elif IS_MAC:
    hidden = ["pynput.keyboard._darwin", "pynput.mouse._darwin", "AppKit", "Quartz",
              "ApplicationServices"]
else:
    hidden = ["pynput.keyboard._xorg", "pynput.mouse._xorg"]

if os.path.exists(os.path.join(SPECPATH, "localflow", "_oauth_client.py")):  # noqa: F821
    hidden.append("localflow._oauth_client")  # Google sign-in client, generated in CI

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
    icon="assets/icon.ico" if IS_WIN else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="LocalFlow")
if IS_MAC:
    app = BUNDLE(
        coll,
        name="LocalFlow.app",
        bundle_identifier="ca.pezant.localflow",
        icon="build/icon.icns" if os.path.exists("build/icon.icns") else None,
        version=__version__,
        info_plist={
            "CFBundleShortVersionString": __version__,
            "NSMicrophoneUsageDescription": "LocalFlow records your voice while you hold the "
                                            "dictation hotkey and transcribes it on this Mac.",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "13.0",
        },
    )
