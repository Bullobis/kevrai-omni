# -*- mode: python ; coding: utf-8 -*-
# H3Studio PyInstaller 打包配置
# 说明：torch 的 CUDA 运行库体积大属正常现象（约 2.5GB），
#       安装包经 Inno Setup 压缩后约 1.5~2GB。

block_cipher = None

a = Analysis(
    ['..\\h3studio\\main.py'],
    pathex=['..'],
    binaries=[],
    datas=[('..\\resources', 'resources')],
    hiddenimports=[
        'h3studio', 'h3studio.facts', 'h3studio.config', 'h3studio.hardware',
        'h3studio.sources', 'h3studio.downloader', 'h3studio.engine',
        'h3studio.ui', 'h3studio.ui.styles', 'h3studio.ui.widgets',
        'h3studio.ui.main_window', 'h3studio.ui.page_generate',
        'h3studio.ui.page_market', 'h3studio.ui.page_library',
        'h3studio.ui.page_gallery', 'h3studio.ui.page_settings',
        'h3studio.planner', 'imageio', 'imageio.v2', 'imageio_ffmpeg',
        'diffsynth', 'diffsynth.pipelines', 'diffsynth.pipelines.minimax_h3_audio_video',
        'diffsynth.utils.data.audio_video', 'diffsynth.utils.data.audio',
        'modelscope', 'huggingface_hub', 'bitsandbytes', 'av', 'torchaudio',
        'PIL', 'psutil', 'requests', 'safetensors', 'einops', 'transformers',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'IPython', 'jupyter'],
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
    console=False,          # 图形程序，无控制台窗口
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
