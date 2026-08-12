# -*- mode: python ; coding: utf-8 -*-
# H3Studio 精简打包配置 — 不含 torch/diffsynth 等重型推理引擎
# 包体积约 80MB，首次运行通过应用内市场下载推理引擎

block_cipher = None

a = Analysis(
    ['..\\h3studio\\main.py'],
    pathex=['..'],
    binaries=[],
    datas=[('..\\resources', 'resources')],
    hiddenimports=[
        'h3studio', 'h3studio.config', 'h3studio.hardware',
        'h3studio.sources', 'h3studio.downloader', 'h3studio.facts',
        'h3studio.i18n', 'h3studio.customizer', 'h3studio.image_gen',
        'h3studio.planner',
        'h3studio.ui', 'h3studio.ui.styles', 'h3studio.ui.widgets',
        'h3studio.ui.main_window', 'h3studio.ui.page_generate',
        'h3studio.ui.page_market', 'h3studio.ui.page_library',
        'h3studio.ui.page_gallery', 'h3studio.ui.page_settings',
        'h3studio.ui.page_custom', 'h3studio.ui.page_help',
        'h3studio.ui.page_image',
        'PIL', 'requests',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['torch', 'torchaudio', 'torchvision', 'diffsynth', 'modelscope',
              'huggingface_hub', 'bitsandbytes', 'av', 'transformers',
              'safetensors', 'einops', 'imageio', 'imageio_ffmpeg',
              'tkinter', 'matplotlib', 'IPython', 'jupyter', 'pytest'],
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
    name='H3Studio',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name='H3Studio',
)
