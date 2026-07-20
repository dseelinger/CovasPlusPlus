# covas.spec — PyInstaller ONE-FOLDER (onedir) freeze of COVAS++ (I5, CPU-only).
#
#   Build:  .venv\Scripts\python.exe -m PyInstaller --noconfirm --clean covas.spec   (or build.ps1)
#   Output: dist\COVAS++\COVAS++.exe   (+ COVAS++.exe --selftest imports every native lib headless)
#
# Onedir (not onefile): faster startup and far fewer AV false-positives than a temp-unpacking
# onefile (INSTALLER_DESIGN "The stack"). Entry is run_covas_app.py — the native PyWebView window.
#
# STT is whisper.cpp via pywhispercpp (issue #206): MIT, CPU-only, reads float32 PCM directly. This
# REMOVED the faster-whisper + ctranslate2 + av/FFmpeg + onnxruntime(Silero-VAD) stack — and with av
# went the GPL-2.0 x264/x265 codec DLLs that used to be unavoidable dead weight (avcodec HARD-LINKED
# libx264/libx265/libvpx/libSvtAv1Enc/libdav1d via its import table, so they couldn't be trimmed even
# though COVAS++ never encoded video). The bundle is now 100% permissive BY CONSTRUCTION, and smaller
# (drops the ~63 MB av.libs FFmpeg blob + the ctranslate2 native DLL). pywhispercpp ships a top-level
# _pywhispercpp extension + loose ggml-*/whisper-* DLLs (delvewheel layout) that collect_all misses,
# so they're pinned/globbed in explicitly below; the frozen --selftest imports the backend and FAILS
# LOUD if any are missing.
import os
import sys

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
#   * edge_tts + aiohttp: the DEFAULT TTS provider (issue #15) is imported LAZILY (inside
#     EdgeTTS.__init__ / make_tts), so static analysis can miss it — and if it's missing from the
#     freeze the shipped app's default voice silently degrades to text. collect_all + the --selftest
#     import below make the freeze include it and FAIL LOUDLY if it doesn't. (The other cloud
#     providers — azure/openai/cartesia/gemini — ride `requests`, already bundled, so no entry here.)
for _pkg in ("pywhispercpp", "sounddevice", "soundfile",
             "webview", "edge_tts", "aiohttp"):
    _d, _b, _h = collect_all(_pkg)
    datas += _d
    binaries += _b
    hiddenimports += _h

# whisper.cpp STT (issue #206): pywhispercpp's native extension is a TOP-LEVEL `_pywhispercpp` module
# (not under the package), and its whisper.cpp/ggml DLLs are loose, hashed files in site-packages
# root (delvewheel repair) that collect_all("pywhispercpp") doesn't see. Pin the extension and glob
# the DLLs in so the frozen backend can load — the --selftest importing _pywhispercpp fails LOUD if
# any are missing. (PyInstaller's own analysis of these DLLs pulls the MSVC runtime they depend on.)
hiddenimports += ["_pywhispercpp"]
import glob
import sysconfig

_purelib = sysconfig.get_paths()["purelib"]
for _dll in glob.glob(os.path.join(_purelib, "ggml*.dll")) + glob.glob(os.path.join(_purelib, "whisper*.dll")):
    binaries.append((_dll, "."))

# Pillow renders the VR HUD's Segoe UI text (issue #48). It's imported lazily inside the renderer
# and falls back to a bitmap font if absent — so a freeze that missed it would SILENTLY ship the
# ugly 1980s font with no error, the same silent-degradation trap as openvr. It's required in the
# build env (requirements.txt); collect it explicitly and fail LOUD if it's missing.
try:
    _d, _b, _h = collect_all("PIL")
    datas += _d
    binaries += _b
    hiddenimports += _h
except Exception as _e:
    print("=" * 78, file=sys.stderr)
    print("WARNING: Pillow (PIL) NOT FOUND — the VR HUD will fall back to the bitmap font.", file=sys.stderr)
    print(f"         ({_e})", file=sys.stderr)
    print("         Fix:  .venv\\Scripts\\python.exe -m pip install -r requirements.txt", file=sys.stderr)
    print("=" * 78, file=sys.stderr)

# openvr (the VR HUD's SteamVR overlay, issue #48) bundles openvr_api.dll via collect_all. It is
# in requirements.txt, so a correct build env HAS it and the freeze ships the VR overlay.
#
# The build still succeeds without it (the runtime import is lazy + fail-soft), because failing
# the whole build over one optional surface is worse than shipping without it. But it must SHOUT:
# a silent `except: pass` here is exactly how v0.12.0 shipped the VR HUD as unreachable dead code
# — the setting existed, the docs told users to `pip install openvr` into a frozen app, and no
# build log ever mentioned that the feature had been dropped on the floor.
try:
    _d, _b, _h = collect_all("openvr")
    datas += _d
    binaries += _b
    hiddenimports += _h
except Exception as _e:
    print("=" * 78, file=sys.stderr)
    print("WARNING: openvr NOT FOUND — this build will ship WITHOUT the in-headset VR HUD.", file=sys.stderr)
    print(f"         ({_e})", file=sys.stderr)
    print("         [hud].vr_enabled will silently do nothing for every user of this build.", file=sys.stderr)
    print("         Fix:  .venv\\Scripts\\python.exe -m pip install -r requirements.txt", file=sys.stderr)
    print("=" * 78, file=sys.stderr)

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

# (No binary trim — the GPL av/x264/x265 codec DLLs are gone with the whisper.cpp move (issue #206);
# the whisper.cpp/ggml DLLs bundled above are MIT.)

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
