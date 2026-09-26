# PyInstaller spec — freeze the Kevrai Omni sidecar into a platform-specific
# onedir bundle. PyInstaller is NOT a cross-compiler, so build it on each OS
# (the release workflow does this):
#     python -m PyInstaller sidecar.spec --noconfirm --clean
# The resulting bundle is emitted to dist/sidecar/.
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# The whole ``app`` package is imported dynamically by uvicorn ("app.main:app")
# and pulls in several modules at runtime, which static analysis of the string
# form cannot follow — collect every submodule explicitly.
hiddenimports = collect_submodules("app")

# uvicorn chooses its loop / http / websocket implementations dynamically at
# runtime, so the selected backends must be present. numpy / imageio are
# imported lazily inside functions (LTX/MNN export) and are small, so include
# them explicitly too.
hiddenimports += [
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan.on",
    "numpy",
    "imageio",
    "imageio_ffmpeg",
]

# Heavy / optional stacks are intentionally NOT bundled: they are multi-GB and
# only needed for on-device inference (which downloads its own engines). This
# keeps the base sidecar small; users install these only when they run the
# corresponding inference feature.
excludes = [
    "torch",
    "diffusers",
    "transformers",
    "accelerate",
    "cv2",
    "onnxruntime",
    "matplotlib",
    "pandas",
    "pytest",
    "pytest_asyncio",
    "tkinter",
]

a = Analysis(
    ["run_sidecar.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="sidecar",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="sidecar",
)
