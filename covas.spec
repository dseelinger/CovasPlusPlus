# covas.spec — PyInstaller ONE-FOLDER (onedir) freeze of COVAS++ (I5, CPU-only).
#
#   Build:  .venv\Scripts\python.exe -m PyInstaller --noconfirm --clean covas.spec   (or build.ps1)
#   Output: dist\COVAS++\COVAS++.exe   (+ COVAS++.exe --selftest imports every native lib headless)
#
# Onedir (not onefile): faster startup and far fewer AV false-positives than a temp-unpacking
# onefile (INSTALLER_DESIGN "The stack"). Entry is run_covas_app.py — the native PyWebView window.
#
# Trim decisions were VERIFIED against the code + a frozen self-test, not cut blind (the prompt's
# explicit warning). Both of the design doc's "biggest wins" turned out UNSAFE with this env:
#   * onnxruntime STAYS — STT runs with vad_filter=True, and faster_whisper.vad imports it LAZILY
#     (inside a function), so PyInstaller's static analysis misses it. Pinned as a hiddenimport.
#   * PyAV (av) STAYS WHOLE — faster_whisper.__init__ imports it eagerly, so it can't be excluded
#     without breaking `import faster_whisper`. And its ffmpeg VIDEO-codec DLLs can't be individually
#     dropped either: avcodec-62.dll HARD-LINKS libx264/libx265/libvpx/libSvtAv1Enc/libdav1d via its
#     import table, so removing any (even ones we never use) makes `import av` fail with
#     "DLL load failed while importing _core". A frozen --selftest proved this, so no av trim.
# Net: ~260 MB onedir, no trims. Inno LZMA (I6) still gets that to a ~120-150 MB download.
import os

from PyInstaller.utils.hooks import collect_all, collect_data_files

# App/exe icon (I6). A placeholder today (covas/assets/icons/covas.ico, tools/gen_icon.py); the
# real branded art is issue #4. Optional so the build never breaks if the file is absent.
_ICON = os.path.join("covas", "assets", "icons", "covas.ico")
_ICON = _ICON if os.path.exists(_ICON) else None

datas = []
binaries = []
hiddenimports = []

# The app's package data: Flask templates + static + the shipped audio cues (covas/assets). These
# are package-relative, so a code-only collect misses them — collect_data_files pulls them in.
datas += collect_data_files("covas")

# Native/heavy deps PyInstaller under-collects (bundled DLLs, lazily-imported submodules, data).
# collect_all grabs binaries + datas + submodules for each.
for _pkg in ("ctranslate2", "sounddevice", "soundfile", "faster_whisper", "onnxruntime",
             "webview", "av"):
    _d, _b, _h = collect_all(_pkg)
    datas += _d
    binaries += _b
    hiddenimports += _h

# onnxruntime + the VAD module are imported lazily (see header) — pin them so the freeze includes
# them even though nothing imports them at module top level.
hiddenimports += ["onnxruntime", "faster_whisper.vad"]

# Shipped, read-only assets resolved via app_dir() at runtime (the writable copies are seeded into
# data_dir on first run): the default config.toml and the personality presets.
datas += [
    ("config.toml", "."),
    ("personalities/presets.md", "personalities"),
]

a = Analysis(
    ["run_covas_app.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

# (No binary trim — see the header: av's codec DLLs are hard-linked by avcodec and can't be dropped.)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="COVAS++",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # Windowed build: no console window spawns beside the native app window (a shipped double-click
    # app shouldn't show a terminal). A windowed PyInstaller build leaves sys.stdout/stderr as None,
    # so run_covas_app.py redirects those to os.devnull at startup — every print() in the app stays
    # safe, and the app logs to %APPDATA%\COVAS++\logs regardless. (--selftest still signals via its
    # exit code, which is all build.ps1 checks.)
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_ICON,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="COVAS++",
)
