# -*- mode: python ; coding: utf-8 -*-
# Build a standalone macOS .app:
#   .venv/bin/pyinstaller --noconfirm QRAirDrop-mac.spec

from pathlib import Path

block_cipher = None
root = Path(SPECPATH)

a = Analysis(
    ['app.py'],
    pathex=[str(root)],
    binaries=[],
    datas=[
        (str(root / 'templates'), 'templates'),
    ],
    hiddenimports=[
        'native_clipboard',
        'windows_clipboard',
        'qrcode.image.pil',
        'PIL._tkinter_finder',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='QR AirDrop',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=True,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='QR AirDrop',
)

app = BUNDLE(
    coll,
    name='QR AirDrop.app',
    icon=None,
    bundle_identifier='com.stephenlkp.qrairdrop',
    info_plist={
        'CFBundleName': 'QR AirDrop',
        'CFBundleDisplayName': 'QR AirDrop',
        'CFBundleShortVersionString': '1.0.0',
        'CFBundleVersion': '1.0.0',
        'NSHighResolutionCapable': True,
        'LSBackgroundOnly': False,
        'NSAppleEventsUsageDescription': (
            'QR AirDrop uses automation to place received files on the Mac clipboard.'
        ),
    },
)
