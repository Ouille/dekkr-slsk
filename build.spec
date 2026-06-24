# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        'pystray._win32',
        'PIL._tkinter_finder',
        'aioslsk',
        'aioslsk.client',
        'aioslsk.settings',
        'aioslsk.search',
        'aioslsk.transfer',
        'aioslsk.network',
        'aioslsk.network.network',
        'aioslsk.network.connection',
        'aioslsk.exceptions',
        'cryptography',
        'aiohttp',
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.asyncio',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'mutagen',
        'mutagen.mp3',
        'mutagen.flac',
        'mutagen.wave',
        'mutagen.id3',
        'mutagen.oggvorbis',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='dekkr-slsk',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,      # pas de fenêtre console
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
