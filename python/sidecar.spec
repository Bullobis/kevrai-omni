# -*- mode: python ; coding: utf-8 -*-
# python/sidecar.spec — build the Kevrai sidecar as a self-contained onedir.
#
# PyInstaller cannot cross-compile: build on each target OS (or in the
# release.yml matrix), from python/:
#   python -m pip install -r requirements.txt pyinstaller
#   pyinstaller sidecar.spec --noconfirm --clean
# Artifact: python/dist/sidecar/  -> sidecar(.exe) + _internal/

from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None

# uvicorn's protocol/loop/lifespan backends are imported dynamically, so they
# are invisible to static analysis and must be listed explicitly.
hiddenimports = [
    'uvicorn.logging',
    'uvicorn.loops', 'uvicorn.loops.auto',
    'uvicorn.protocols', 'uvicorn.protocols.auto',
    'uvicorn.protocols.http.auto', 'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespan', 'uvicorn.lifespan.on',
]
# Bundle every module of the app package (it is large and partly imported via
# importlib; collect_submodules is safer than listing each one by hand).
hiddenimports += collect_submodules('app')

datas = collect_data_files('app')

a = Analysis(
    ['run_sidecar.py'],
    pathex=['.'],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # Heavy ML libraries are lazy-imported inside functions and live in a
    # separate userData tools directory; never bundle them into the sidecar.
    excludes=[
        'torch', 'numpy', 'cv2', 'onnxruntime', 'matplotlib',
        'pandas', 'pytest', 'IPython', 'notebook',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='sidecar',          # sidecar on POSIX, sidecar.exe on Windows
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,               # UPX is a frequent source of AV false positives
    console=True,            # Electron spawns it with windowsHide
)

coll = COLLECT(
    exe, a.binaries, a.datas, a.zipfiles,
    strip=False,
    upx=False,
    name='sidecar',
)
